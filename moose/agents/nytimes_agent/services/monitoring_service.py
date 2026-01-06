"""Monitoring service for polling NYTimes sections and sending articles to finance_office."""

import asyncio
import threading
import time
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False

from moose.framework.logging.http_client import traced_requests_post

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False


class NYTimesMonitoringService:
    """
    Monitors NYTimes sections via Newswire API and sends articles to finance_office.
    
    Similar pattern to news_scraper monitoring service.
    """
    
    def __init__(
        self,
        nytimes_client: Any,
        article_storage: Any,
        llm_client_factory: Any,  # Function to get LLM client for a section
        article_processor: Any,
        content_extractor: Any,
        finance_office_endpoint: str,
        config: Dict[str, Any],
        logger=None,
    ):
        """
        Initialize monitoring service.
        
        Args:
            nytimes_client: NYTimesClient instance
            article_storage: ArticleStorage instance
            llm_client_factory: Function(section_config) -> LLM client for that section
            article_processor: ArticleProcessor instance
            content_extractor: ContentExtractor instance
            finance_office_endpoint: Finance office endpoint URL
            config: Monitoring configuration dict
            logger: Logger instance
        """
        self.nytimes_client = nytimes_client
        self.article_storage = article_storage
        self.llm_client_factory = llm_client_factory
        self.article_processor = article_processor
        self.content_extractor = content_extractor
        self.finance_office_endpoint = finance_office_endpoint
        self.config = config
        self.logger = logger
        
        # Monitoring configuration
        self.enabled = bool(config.get("enabled", False))
        self.poll_interval_minutes = int(config.get("newswire_poll_interval_minutes", 15) or 15)
        self.poll_interval_seconds = self.poll_interval_minutes * 60
        self.sections = config.get("sections", [])  # List of section configs
        
        # Content extraction config
        content_cfg = config.get("content_extraction", {}) if isinstance(config.get("content_extraction"), dict) else {}
        self.fetch_full_content = bool(content_cfg.get("fetch_full_content", True))
        self.skip_paywalled = bool(content_cfg.get("skip_paywalled", True))
        
        # Quality control config
        quality_cfg = config.get("quality_control", {}) if isinstance(config.get("quality_control"), dict) else {}
        self.min_quality_score = int(quality_cfg.get("min_quality_score", 3) or 3)
        self.skip_below_score = bool(quality_cfg.get("skip_below_score", True))
        
        # Background monitoring
        self._monitor_stop = threading.Event()
        self._monitor_thread: Optional[threading.Thread] = None
        
        # Finance office posting queue (similar to news_scraper)
        self._analyzer_queue: Optional[asyncio.Queue] = None
        self._analyzer_worker_tasks: List[asyncio.Task] = []
        self._analyzer_workers_started = False
        self._analyzer_loop: Optional[asyncio.AbstractEventLoop] = None
        self.analyzer_enqueued = 0
        self.analyzer_sent_ok = 0
        self.analyzer_sent_failed = 0
        
        # Analyzer queue config
        analyzer_queue_cfg = config.get("analyzer_queue", {}) if isinstance(config.get("analyzer_queue"), dict) else {}
        self.analyzer_queue_maxsize = int(analyzer_queue_cfg.get("maxsize", 200) or 200)
        self.analyzer_worker_count = int(analyzer_queue_cfg.get("worker_count", 2) or 2)
        
        if not REQUESTS_AVAILABLE:
            if self.logger:
                self.logger.warning("requests not available, cannot send to finance_office")
    
    def start_monitoring(self):
        """Start background monitoring loop if enabled."""
        if not self.enabled:
            return
        if self._monitor_thread is not None and self._monitor_thread.is_alive():
            return
        
        self._monitor_stop.clear()
        
        t = threading.Thread(target=self._monitor_loop, name="nytimes_monitor", daemon=True)
        self._monitor_thread = t
        t.start()
        if self.logger:
            next_run = datetime.now() + timedelta(seconds=self.poll_interval_seconds)
            self.logger.info(
                f"NYTimes monitoring enabled: interval={self.poll_interval_minutes} minutes "
                f"(next_run={next_run.isoformat()})"
            )
    
    def stop_monitoring(self):
        """Stop monitoring."""
        self._monitor_stop.set()
        if self._monitor_thread and self._monitor_thread.is_alive():
            self._monitor_thread.join(timeout=5.0)
    
    def _monitor_loop(self):
        """Background loop: poll sections at regular intervals."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        cycle = 0
        try:
            while not self._monitor_stop.is_set():
                # Sleep first to avoid immediate polling on start
                if self.logger:
                    next_run = datetime.now() + timedelta(seconds=self.poll_interval_seconds)
                    self.logger.info(
                        f"Monitoring sleeping: next_run={next_run.isoformat()} "
                        f"in={self.poll_interval_seconds}s"
                    )
                if self._monitor_stop.wait(self.poll_interval_seconds):
                    break
                
                cycle += 1
                if self.logger:
                    self.logger.info(f"Monitoring woke up: starting cycle={cycle}")
                
                try:
                    loop.run_until_complete(self._poll_all_sections())
                except Exception as e:
                    if self.logger:
                        self.logger.warning(f"Monitoring cycle {cycle} failed: {e}")
        finally:
            try:
                # Clean up analyzer workers
                if self._analyzer_loop is loop and self._analyzer_worker_tasks:
                    for t in list(self._analyzer_worker_tasks):
                        try:
                            t.cancel()
                        except Exception:
                            pass
                    try:
                        loop.run_until_complete(asyncio.gather(*self._analyzer_worker_tasks, return_exceptions=True))
                    except Exception:
                        pass
                    if self._analyzer_loop is loop:
                        self._analyzer_queue = None
                        self._analyzer_worker_tasks = []
                        self._analyzer_workers_started = False
                        self._analyzer_loop = None
            finally:
                try:
                    loop.close()
                except Exception:
                    pass
    
    async def _poll_all_sections(self):
        """Poll all enabled sections."""
        enabled_sections = [
            sec for sec in self.sections
            if isinstance(sec, dict) and bool(sec.get("enabled", False))
        ]
        
        if not enabled_sections:
            if self.logger:
                self.logger.info("No enabled sections to monitor")
            return
        
        if self.logger:
            self.logger.info(f"Polling {len(enabled_sections)} enabled sections")
        
        await self._ensure_analyzer_workers_started()
        
        for section_config in enabled_sections:
            try:
                await self._poll_section(section_config)
            except Exception as e:
                if self.logger:
                    self.logger.error(f"Error polling section {section_config.get('section_name', 'unknown')}: {e}")
                continue
    
    async def _poll_section(self, section_config: Dict[str, Any]):
        """
        Poll a single section for new articles.
        
        Args:
            section_config: Section configuration dict with section_name, model, etc.
        """
        section_name = str(section_config.get("section_name", "")).strip()
        if not section_name:
            return
        
        if self.logger:
            self.logger.info(f"Polling section: {section_name}")
        
        # Convert section name to lowercase for Newswire API
        section_lower = section_name.lower()
        
        # Call Newswire API
        try:
            response = self.nytimes_client.newswire.get_content(
                source="all",
                section=section_lower,
                limit=20,
                offset=0,
            )
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error calling Newswire API for section {section_name}: {e}")
            # Try Article Search API as fallback
            if self.logger:
                self.logger.warning(f"Trying Article Search API as fallback for section {section_name}")
            await self._poll_section_fallback_article_search(section_config)
            return
        
        # Check for API errors
        if isinstance(response, dict) and "error" in response:
            if self.logger:
                self.logger.warning(f"Newswire API error for section {section_name}: {response.get('error')}")
            # Try Article Search API as fallback
            if self.logger:
                self.logger.warning(f"Trying Article Search API as fallback for section {section_name}")
            await self._poll_section_fallback_article_search(section_config)
            return
        
        # Check API response status
        if response.get("status") != "OK":
            error_msg = response.get("fault", {}).get("faultstring", "Unknown error")
            if self.logger:
                self.logger.warning(f"Newswire API returned non-OK status for {section_name}: {error_msg}")
            return
        
        # Process articles
        articles = response.get("results", [])
        if not articles:
            if self.logger:
                self.logger.debug(f"No articles found for section {section_name}")
            return
        
        if self.logger:
            self.logger.info(f"Found {len(articles)} articles for section {section_name}")
        
        # Get LLM client for this section
        llm_client = self.llm_client_factory(section_config)
        
        processed_count = 0
        skipped_count = 0
        failed_count = 0
        
        for article_data in articles:
            try:
                # Process article from Newswire API
                article = self.article_processor.process_newswire_result(article_data)
                url = article.get("url", "")
                
                if not url:
                    continue
                
                # Check deduplication
                if self.article_storage.is_url_scraped(url):
                    skipped_count += 1
                    continue
                
                # Extract full content if needed
                content = None
                if self.fetch_full_content:
                    content = await self._fetch_article_content(url)
                    if not content:
                        # Fallback to abstract if content extraction fails
                        content = article.get("abstract", "")
                
                # Quality score using section-specific model
                title = article.get("title", "")
                abstract = article.get("abstract", "")
                
                quality_result = await llm_client.score_article_quality(
                    url=url,
                    title=title,
                    abstract=abstract,
                    content=content,
                    section=section_name,
                )
                
                quality_score = quality_result.get("quality_score", 0)
                rationale = quality_result.get("rationale", "")
                
                # Skip if quality score is too low
                if self.skip_below_score and quality_score < self.min_quality_score:
                    if self.logger:
                        self.logger.debug(f"Skipping low quality article (score={quality_score}): {url}")
                    skipped_count += 1
                    continue
                
                # Prepare article for saving (format compatible with finance_office)
                article_to_save = {
                    "title": title,
                    "summary": abstract,
                    "raw_article": content or abstract,
                    "quality_score": quality_score,
                    "rationale": rationale,
                }
                
                # Parse published date
                pub_date_str = article.get("published_date", "")
                pub_date = None
                if pub_date_str:
                    try:
                        # Try parsing ISO format
                        pub_date = datetime.fromisoformat(pub_date_str.replace('Z', '+00:00'))
                    except Exception:
                        pass
                
                # Save article
                saved_path = self.article_storage.save_article_json(url, article_to_save, article_date=pub_date)
                
                if saved_path:
                    processed_count += 1
                    
                    # Enqueue for finance_office
                    metadata = {
                        "source": "nytimes",
                        "api_source": "newswire",
                        "section": section_name,
                        "pub_date": pub_date_str,
                        "quality_score": quality_score,
                    }
                    
                    await self._enqueue_analyzer_send(
                        file_path=str(saved_path),
                        url=url,
                        metadata=metadata,
                    )
                else:
                    failed_count += 1
                    
            except Exception as e:
                failed_count += 1
                if self.logger:
                    self.logger.error(f"Error processing article in section {section_name}: {e}")
                continue
        
        if self.logger:
            self.logger.info(
                f"Section {section_name}: processed={processed_count}, "
                f"skipped={skipped_count}, failed={failed_count}"
            )
    
    async def _poll_section_fallback_article_search(self, section_config: Dict[str, Any]):
        """
        Fallback to Article Search API when Newswire API fails.
        
        Args:
            section_config: Section configuration
        """
        section_name = str(section_config.get("section_name", "")).strip()
        if not section_name:
            return
        
        if self.logger:
            self.logger.info(f"Using Article Search API fallback for section {section_name}")
        
        try:
            # Use filter query to search by section
            filter_query = f'section.name:"{section_name}"'
            
            response = self.nytimes_client.article_search.search_articles(
                filter_query=filter_query,
                page=0,
                sort="newest",
            )
            
            if isinstance(response, dict) and "error" in response:
                if self.logger:
                    self.logger.warning(f"Article Search API also failed for {section_name}: {response.get('error')}")
                return
            
            if response.get("status") != "OK":
                return
            
            response_data = response.get("response", {})
            docs = response_data.get("docs", [])
            
            if not docs:
                return
            
            # Get LLM client
            llm_client = self.llm_client_factory(section_config)
            
            # Process articles (similar to _poll_section but using Article Search format)
            # Get LLM client
            llm_client = self.llm_client_factory(section_config)
            
            processed_count = 0
            skipped_count = 0
            failed_count = 0
            
            for doc in docs:
                try:
                    # Process article from Article Search API
                    article = self.article_processor.process_article_search_result(doc)
                    url = article.get("url", "")
                    
                    if not url:
                        continue
                    
                    # Check deduplication
                    if self.article_storage.is_url_scraped(url):
                        skipped_count += 1
                        continue
                    
                    # Extract full content if needed
                    content = None
                    if self.fetch_full_content:
                        content = await self._fetch_article_content(url)
                        if not content:
                            content = article.get("abstract", "")
                    
                    # Quality score
                    title = article.get("title", "")
                    abstract = article.get("abstract", "")
                    
                    quality_result = await llm_client.score_article_quality(
                        url=url,
                        title=title,
                        abstract=abstract,
                        content=content,
                        section=section_name,
                    )
                    
                    quality_score = quality_result.get("quality_score", 0)
                    
                    # Skip if quality score is too low
                    if self.skip_below_score and quality_score < self.min_quality_score:
                        skipped_count += 1
                        continue
                    
                    # Prepare article for saving
                    article_to_save = {
                        "title": title,
                        "summary": abstract,
                        "raw_article": content or abstract,
                        "quality_score": quality_score,
                        "rationale": quality_result.get("rationale", ""),
                    }
                    
                    # Parse published date
                    pub_date_str = article.get("published_date", "")
                    pub_date = None
                    if pub_date_str:
                        try:
                            pub_date = datetime.fromisoformat(pub_date_str.replace('Z', '+00:00'))
                        except Exception:
                            pass
                    
                    # Save article
                    saved_path = self.article_storage.save_article_json(url, article_to_save, article_date=pub_date)
                    
                    if saved_path:
                        processed_count += 1
                        
                        # Enqueue for finance_office
                        metadata = {
                            "source": "nytimes",
                            "api_source": "article_search",
                            "section": section_name,
                            "pub_date": pub_date_str,
                            "quality_score": quality_score,
                        }
                        
                        await self._enqueue_analyzer_send(
                            file_path=str(saved_path),
                            url=url,
                            metadata=metadata,
                        )
                    else:
                        failed_count += 1
                        
                except Exception as e:
                    failed_count += 1
                    if self.logger:
                        self.logger.error(f"Error processing article in Article Search fallback for {section_name}: {e}")
                    continue
            
            if self.logger:
                self.logger.info(
                    f"Article Search fallback for {section_name}: processed={processed_count}, "
                    f"skipped={skipped_count}, failed={failed_count}"
                )
                
        except Exception as e:
            if self.logger:
                self.logger.error(f"Article Search fallback failed for {section_name}: {e}")
    
    async def _ensure_analyzer_workers_started(self):
        """Ensure analyzer worker tasks are started."""
        if self._analyzer_workers_started:
            return
        
        loop = asyncio.get_event_loop()
        if self._analyzer_queue is None:
            self._analyzer_queue = asyncio.Queue(maxsize=self.analyzer_queue_maxsize)
        
        self._analyzer_loop = loop
        
        for i in range(self.analyzer_worker_count):
            task = asyncio.create_task(self._analyzer_worker(i))
            self._analyzer_worker_tasks.append(task)
        
        self._analyzer_workers_started = True
        if self.logger:
            self.logger.info(f"Started {self.analyzer_worker_count} analyzer workers")
    
    async def _analyzer_worker(self, worker_id: int):
        """Background worker that sends articles to finance_office."""
        while True:
            try:
                item = await self._analyzer_queue.get()
                if item is None:  # Shutdown signal
                    break
                
                file_path = item.get("file_path", "")
                url = item.get("url", "")
                metadata = item.get("metadata", {})
                
                if not file_path or not url:
                    continue
                
                ok = await self._send_to_analyzer(file_path, url, metadata)
                
                if ok:
                    self.analyzer_sent_ok += 1
                else:
                    self.analyzer_sent_failed += 1
                    
            except Exception as e:
                self.analyzer_sent_failed += 1
                if self.logger:
                    self.logger.warning(f"Analyzer worker {worker_id} failed: {e}")
            finally:
                try:
                    self._analyzer_queue.task_done()
                except Exception:
                    pass
    
    async def _enqueue_analyzer_send(self, file_path: str, url: str, metadata: Dict[str, Any]):
        """Enqueue article for sending to finance_office."""
        if not self._analyzer_queue:
            await self._ensure_analyzer_workers_started()
        
        try:
            await self._analyzer_queue.put({
                "file_path": file_path,
                "url": url,
                "metadata": metadata,
            })
            self.analyzer_enqueued += 1
        except asyncio.QueueFull:
            if self.logger:
                self.logger.warning("Analyzer queue is full, dropping article")
    
    async def _fetch_article_content(self, url: str) -> Optional[str]:
        """
        Fetch and extract article content from URL using Playwright.
        NYTimes blocks direct HTTP requests, so we use Playwright to bypass blocking.
        
        Args:
            url: Article URL
            
        Returns:
            Extracted article text, or None if failed
        """
        # Use Playwright to fetch (bypasses NYTimes blocking)
        if self.logger:
            self.logger.debug(f"Fetching content from {url} using Playwright")
        html_content = await self.content_extractor.fetch_with_playwright(url)
        
        if not html_content:
            if self.logger:
                self.logger.warning(f"Failed to fetch content from {url} using Playwright")
            return None
        
        # Extract content using ContentExtractor
        content = self.content_extractor.extract_content(url, html_content)
        return content
    
    async def _send_to_analyzer(self, file_path: str, url: str, metadata: Dict[str, Any]) -> bool:
        """
        Send article to finance_office.
        
        Args:
            file_path: Path to article file
            url: Article URL
            metadata: Additional metadata
            
        Returns:
            True if successfully sent
        """
        if not REQUESTS_AVAILABLE:
            return False
        
        try:
            payload = {
                "file_path": file_path,
                "url": url,
                "metadata": metadata,
            }
            
            response = traced_requests_post(
                self.finance_office_endpoint,
                json=payload,
                timeout=10
            )
            
            if response.status_code == 200:
                if self.logger:
                    self.logger.debug(f"Sent article to finance_office: {file_path}")
                return True
            else:
                if self.logger:
                    self.logger.warning(
                        f"Failed to send to finance_office (status {response.status_code}): {response.text[:200]}"
                    )
                return False
                
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error sending to finance_office: {e}")
            return False

