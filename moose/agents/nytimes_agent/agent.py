"""NYTimes Agent - Monitors NYTimes APIs and provides search endpoints."""

import os
import json
import html
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime
from urllib.parse import quote

from moose.framework.agent_core.base_agent import BaseAgent

from services.nytimes_client import NYTimesClient
from services.article_storage import ArticleStorage
from services.article_processor import ArticleProcessor
from services.content_extractor import ContentExtractor
from services.llm_client import NYTimesQualityLLMClient
from services.monitoring_service import NYTimesMonitoringService

class NYTimesAgent(BaseAgent):
    """
    NYTimes agent that monitors NYTimes APIs and provides search endpoints.
    
    Features:
    - Monitors business-impact sections via Times Newswire API
    - Provides search endpoints for stock_research workflow
    - Quality scoring using LLM before sending to finance_office
    - Content extraction from article URLs
    - Deduplication using article URLs
    """
    
    name = "nytimes_agent"
    description = "NYTimes API agent for monitoring and searching news articles"
    
    def __init__(self, config_path=None, debug=False):
        """Initialize the NYTimes agent."""
        super().__init__(config_path, debug=debug)
        
        # Load custom configuration
        custom_config = self.config.get("custom", {}) if isinstance(self.config.get("custom"), dict) else {}
        
        # Get API key
        api_key = os.getenv("NYTIMES_API_KEY", "")
        if not api_key:
            raise ValueError("NYTIMES_API_KEY environment variable is required")
        
        # Initialize data directory
        data_dir = os.getenv("NYTIMES_DATA_DIR", "/data/scraper/nytimes")
        data_dir_path = Path(data_dir)
        data_dir_path.mkdir(parents=True, exist_ok=True)
        self.logger.info(f"Data directory: {data_dir_path}")
        
        # Rate limiting config
        rate_limit_cfg = custom_config.get("rate_limiting", {}) if isinstance(custom_config.get("rate_limiting"), dict) else {}
        rate_limit = int(rate_limit_cfg.get("requests_per_minute", 50) or 50)
        
        # Initialize NYTimes API client
        self.nytimes_client = NYTimesClient(
            api_key=api_key,
            logger=self.logger,
            rate_limit=rate_limit
        )
        
        # Initialize article storage
        self.article_storage = ArticleStorage(
            data_dir=data_dir_path,
            logger=self.logger
        )
        
        # Initialize article processor
        self.article_processor = ArticleProcessor(logger=self.logger)
        
        # Initialize content extractor with playwright config
        content_extraction_cfg = custom_config.get("content_extraction", {}) if isinstance(custom_config.get("content_extraction"), dict) else {}
        playwright_cfg = content_extraction_cfg.get("playwright", {}) if isinstance(content_extraction_cfg.get("playwright"), dict) else {}
        self.content_extractor = ContentExtractor(logger=self.logger, playwright_config=playwright_cfg)
        
        # LLM client factory (for section-specific models)
        def create_llm_client(section_config: Dict[str, Any]) -> NYTimesQualityLLMClient:
            """Factory function to create LLM client for a section."""
            # Use section-specific config if available, otherwise use default
            default_llm_config = custom_config.get("llm_config", {}) if isinstance(custom_config.get("llm_config"), dict) else {}
            
            # Merge section config with default
            section_llm_config = dict(default_llm_config)
            if "model" in section_config:
                section_llm_config["model"] = section_config["model"]
            if "temperature" in section_config:
                section_llm_config["temperature"] = section_config.get("temperature", 0.3)
            if "kwargs" in section_config:
                section_llm_config["kwargs"] = section_config.get("kwargs", {})
            
            return NYTimesQualityLLMClient(llm_config=section_llm_config, logger=self.logger)
        
        self.llm_client_factory = create_llm_client
        
        # Get finance_office endpoint
        finance_office_cfg = custom_config.get("finance_office", {}) if isinstance(custom_config.get("finance_office"), dict) else {}
        finance_office_endpoint = finance_office_cfg.get("endpoint", "http://localhost:3501/get_financial_news")
        
        # Monitoring configuration
        monitoring_cfg = custom_config.get("monitoring", {}) if isinstance(custom_config.get("monitoring"), dict) else {}
        
        # Initialize monitoring service
        self.monitoring_service = NYTimesMonitoringService(
            nytimes_client=self.nytimes_client,
            article_storage=self.article_storage,
            llm_client_factory=create_llm_client,
            article_processor=self.article_processor,
            content_extractor=self.content_extractor,
            finance_office_endpoint=finance_office_endpoint,
            config=monitoring_cfg,
            logger=self.logger
        )
        
        # Start monitoring if enabled
        try:
            self.monitoring_service.start_monitoring()
        except Exception as e:
            self.logger.warning(f"Failed to start monitoring: {e}")
        
        self.logger.info(f"Initialized NYTimes agent")
        self.logger.info(f"Finance office endpoint: {finance_office_endpoint}")
    
    def _get_data_dir(self) -> Path:
        """Get data directory path."""
        data_dir = os.getenv("NYTIMES_DATA_DIR", "/data/scraper/nytimes")
        return Path(data_dir)
    
    def _today_folder(self) -> Path:
        """Get today's article folder path."""
        now = datetime.now()
        return self._get_data_dir() / str(now.year) / f"{now.month:02d}" / f"{now.day:02d}"
    
    def status_page(self, data: Dict[str, Any]) -> Any:
        """HTML status page showing recent articles and monitoring stats."""
        from flask import Response
        
        limit = 100
        try:
            if isinstance(data, dict) and data.get("limit") is not None:
                limit = int(data.get("limit"))
        except Exception:
            limit = 100
        limit = max(1, min(500, limit))
        
        folder = self._today_folder()
        files = []
        if folder.exists():
            files = sorted(folder.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        
        total = len(files)
        items = []
        for p in files[:limit]:
            try:
                with open(p, "r", encoding="utf-8") as f:
                    payload = json.load(f)
            except Exception:
                payload = {}
            title = str((payload or {}).get("title") or p.name).strip() or p.name
            rel = p.relative_to(self._get_data_dir())
            href = "/article?path=" + quote(str(rel))
            items.append(f'<li><a href="{href}">{html.escape(title)}</a></li>')
        
        # Monitoring stats
        monitoring_enabled = getattr(self.monitoring_service, "enabled", False)
        analyzer_enqueued = getattr(self.monitoring_service, "analyzer_enqueued", 0)
        analyzer_ok = getattr(self.monitoring_service, "analyzer_sent_ok", 0)
        analyzer_failed = getattr(self.monitoring_service, "analyzer_sent_failed", 0)
        analyzer_queue_size = 0
        try:
            if self.monitoring_service._analyzer_queue:
                analyzer_queue_size = self.monitoring_service._analyzer_queue.qsize()
        except Exception:
            pass
        
        page = f"""<!doctype html>
<html>
  <head>
    <meta charset="utf-8"/>
    <meta name="viewport" content="width=device-width, initial-scale=1"/>
    <title>NYTimes Agent Status</title>
    <style>
      body {{ font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial; margin: 24px; }}
      .muted {{ color: #555; }}
      .card {{ border: 1px solid #e5e5e5; border-radius: 10px; padding: 16px; margin-top: 16px; }}
      a {{ text-decoration: none; }}
      a:hover {{ text-decoration: underline; }}
      ul {{ line-height: 1.7; }}
    </style>
  </head>
  <body>
    <h2>NYTimes Agent — Today</h2>
    <div class="muted">Folder: {html.escape(str(folder))}</div>
    <div class="card">
      <div><b>Total articles today:</b> {total}</div>
      <div class="muted">Showing latest {min(limit, total)} (use <code>?limit=...</code> up to 500)</div>
    </div>
    <div class="card">
      <h3>Monitoring & Finance Office Queue</h3>
      <div><b>Monitoring enabled:</b> {monitoring_enabled}</div>
      <div><b>Queue depth:</b> {analyzer_queue_size}</div>
      <div><b>Enqueued:</b> {analyzer_enqueued}</div>
      <div><b>Sent OK:</b> {analyzer_ok}</div>
      <div><b>Sent failed:</b> {analyzer_failed}</div>
    </div>
    <div class="card">
      <h3>Today's articles</h3>
      <ul>
        {''.join(items) if items else "<li class='muted'>No articles found for today.</li>"}
      </ul>
    </div>
  </body>
</html>"""
        
        return Response(page, mimetype="text/html")
    
    # Search endpoint handlers
    
    async def search_articles(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Search articles using Article Search API.
        
        Parameters:
            query: Search query
            begin_date: Start date (YYYYMMDD)
            end_date: End date (YYYYMMDD)
            page: Page number (0-100)
            sort: Sort order ("best", "newest", "oldest", "relevance")
            sections: Filter by sections (list)
            desks: Filter by news desks (list)
            max_results: Maximum results (default: 10, max: 100)
            include_content: Whether to fetch full content (default: false)
        """
        query = data.get("query")
        begin_date = data.get("begin_date")
        end_date = data.get("end_date")
        page = int(data.get("page", 0))
        sort = data.get("sort", "best")
        max_results = int(data.get("max_results", 10))
        max_results = min(max_results, 100)  # Hard limit
        include_content = bool(data.get("include_content", False))
        
        # Build filter query for sections/desks
        # According to Article Search API spec: section.name and desk are the correct field names
        filter_parts = []
        sections = data.get("sections", [])
        desks = data.get("desks", [])
        if sections:
            # Use section.name field (not section_name)
            # Format: section.name:("value1", "value2") for multiple values
            section_values = [sec for sec in sections if sec]
            if section_values:
                if len(section_values) == 1:
                    filter_parts.append(f'section.name:"{section_values[0]}"')
                else:
                    values_str = ", ".join([f'"{v}"' for v in section_values])
                    filter_parts.append(f'section.name:({values_str})')
        if desks:
            # Use desk field (not news_desk)
            # Format: desk:("value1", "value2") for multiple values
            desk_values = [desk for desk in desks if desk]
            if desk_values:
                if len(desk_values) == 1:
                    filter_parts.append(f'desk:"{desk_values[0]}"')
                else:
                    values_str = ", ".join([f'"{v}"' for v in desk_values])
                    filter_parts.append(f'desk:({values_str})')
        
        filter_query = " AND ".join(filter_parts) if filter_parts else None
        
        # Search
        response = self.nytimes_client.article_search.search_articles(
            query=query,
            begin_date=begin_date,
            end_date=end_date,
            page=page,
            sort=sort,
            filter_query=filter_query,
        )
        
        if isinstance(response, dict) and "error" in response:
            return {
                "status": "error",
                "error": response.get("error"),
            }
        
        if response.get("status") != "OK":
            return {
                "status": "error",
                "error": f"API returned status: {response.get('status')}",
            }
        
        response_data = response.get("response", {})
        docs = response_data.get("docs", [])
        metadata = response_data.get("metadata", {})
        total_hits = metadata.get("hits", 0)
        
        # Process articles
        articles = []
        for doc in docs[:max_results]:
            article = self.article_processor.process_article_search_result(doc)
            
            # Fetch content if requested
            if include_content:
                url = article.get("url", "")
                if url:
                    content = await self._fetch_article_content(url)
                    if content:
                        article = self.article_processor.add_content_to_article(article, content)
            
            articles.append(article)
        
        return {
            "status": "success",
            "total_hits": total_hits,
            "articles": articles,
        }
    
    async def search_by_ticker(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Search articles by ticker symbol.
        
        Parameters:
            tickers: List of ticker symbols
            begin_date: Start date (YYYYMMDD)
            end_date: End date (YYYYMMDD)
            max_results: Maximum results (default: 10, max: 100)
            include_content: Whether to fetch full content (default: false)
        """
        tickers = data.get("tickers", [])
        if not tickers or not isinstance(tickers, list):
            return {
                "status": "error",
                "error": "tickers parameter is required and must be a list",
            }
        
        begin_date = data.get("begin_date")
        end_date = data.get("end_date")
        max_results = int(data.get("max_results", 10))
        max_results = min(max_results, 100)
        include_content = bool(data.get("include_content", False))
        
        # Build search query from tickers
        # Convert tickers to search query (simple approach: search for ticker symbols)
        query_parts = []
        for ticker in tickers:
            if isinstance(ticker, str) and ticker.strip():
                query_parts.append(ticker.strip().upper())
        
        if not query_parts:
            return {
                "status": "error",
                "error": "No valid tickers provided",
            }
        
        query = " OR ".join(query_parts)
        
        # Search using Article Search API
        response = self.nytimes_client.article_search.search_articles(
            query=query,
            begin_date=begin_date,
            end_date=end_date,
            page=0,
            sort="newest",
        )
        
        if isinstance(response, dict) and "error" in response:
            return {
                "status": "error",
                "error": response.get("error"),
            }
        
        if response.get("status") != "OK":
            return {
                "status": "error",
                "error": f"API returned status: {response.get('status')}",
            }
        
        response_data = response.get("response", {})
        docs = response_data.get("docs", [])
        metadata = response_data.get("metadata", {})
        total_hits = metadata.get("hits", 0)
        
        # Filter using LLM to remove false positives
        # For now, return all results (LLM filtering can be added later)
        articles = []
        for doc in docs[:max_results * 2]:  # Get more to filter
            article = self.article_processor.process_article_search_result(doc)
            
            # TODO: Add LLM-based filtering to ensure article actually mentions ticker
            # For now, include all results
            
            articles.append(article)
            if len(articles) >= max_results:
                break
        
        return {
            "status": "success",
            "total_hits": total_hits,
            "articles": articles,
        }
    
    async def search_realtime(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Search real-time articles using Newswire API.
        
        Parameters:
            sections: List of section names
            source: "all", "nyt", or "inyt" (default: "all")
            limit: Number of results (default: 20, max: 500)
            offset: Starting offset (default: 0)
            hours_back: Filter articles from last N hours (optional)
        """
        sections = data.get("sections", ["all"])
        source = data.get("source", "all")
        limit = int(data.get("limit", 20))
        limit = min(limit, 500)
        offset = int(data.get("offset", 0))
        hours_back = data.get("hours_back")
        
        all_articles = []
        
        # Poll each section
        for section in sections:
            try:
                response = self.nytimes_client.newswire.get_content(
                    source=source,
                    section=section.lower() if section else "all",
                    limit=limit,
                    offset=offset,
                )
                
                if isinstance(response, dict) and "error" in response:
                    continue
                
                if response.get("status") != "OK":
                    continue
                
                articles_data = response.get("results", [])
                for article_data in articles_data:
                    article = self.article_processor.process_newswire_result(article_data)
                    
                    # Filter by hours_back if specified
                    if hours_back:
                        pub_date_str = article.get("published_date", "")
                        if pub_date_str:
                            try:
                                from datetime import timedelta
                                pub_date = datetime.fromisoformat(pub_date_str.replace('Z', '+00:00'))
                                cutoff = datetime.now(pub_date.tzinfo) - timedelta(hours=int(hours_back))
                                if pub_date < cutoff:
                                    continue
                            except Exception:
                                pass
                    
                    all_articles.append(article)
            except Exception as e:
                self.logger.warning(f"Error polling section {section}: {e}")
                continue
        
        return {
            "status": "success",
            "total_hits": len(all_articles),
            "articles": all_articles,
        }
    
    async def search_archive(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Search archive articles for a specific month.
        
        Parameters:
            year: Year (1851-2019)
            month: Month (1-12)
            sections: Filter by sections (list, optional)
            max_results: Maximum results (default: 10, max: 100)
        """
        year = int(data.get("year"))
        month = int(data.get("month"))
        sections = data.get("sections", [])
        max_results = int(data.get("max_results", 10))
        max_results = min(max_results, 100)
        
        if not year or not month:
            return {
                "status": "error",
                "error": "year and month parameters are required",
            }
        
        # Get archive
        response = self.nytimes_client.archive.get_archive(year, month)
        
        if isinstance(response, dict) and "error" in response:
            return {
                "status": "error",
                "error": response.get("error"),
            }
        
        if response.get("status") != "OK":
            return {
                "status": "error",
                "error": f"API returned status: {response.get('status')}",
            }
        
        response_data = response.get("response", {})
        docs = response_data.get("docs", [])
        metadata = response_data.get("metadata", {})
        total_hits = metadata.get("hits", 0)
        
        # Filter by sections if specified
        articles = []
        for doc in docs:
            article = self.article_processor.process_archive_result(doc)
            
            if sections:
                article_section = article.get("section", "")
                if article_section not in sections:
                    continue
            
            articles.append(article)
            if len(articles) >= max_results:
                break
        
        return {
            "status": "success",
            "total_hits": total_hits,
            "articles": articles,
        }
    
    async def search_trending(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Get trending articles using Most Popular API.
        
        Parameters:
            type: "viewed", "emailed", or "shared" (default: "viewed")
            period: 1, 7, or 30 days (default: 1)
            sections: Filter by sections (list, optional)
            max_results: Maximum results (default: 10, max: 100)
        """
        type_ = data.get("type", "viewed")
        period = int(data.get("period", 1))
        sections = data.get("sections", [])
        max_results = int(data.get("max_results", 10))
        max_results = min(max_results, 100)
        
        # Get most popular articles
        if type_ == "viewed":
            response = self.nytimes_client.most_popular.get_most_viewed(period)
        elif type_ == "emailed":
            response = self.nytimes_client.most_popular.get_most_emailed(period)
        elif type_ == "shared":
            share_type = data.get("share_type", "facebook")
            response = self.nytimes_client.most_popular.get_most_shared(period, share_type)
        else:
            return {
                "status": "error",
                "error": f"Invalid type: {type_}. Must be 'viewed', 'emailed', or 'shared'",
            }
        
        if isinstance(response, dict) and "error" in response:
            return {
                "status": "error",
                "error": response.get("error"),
            }
        
        if response.get("status") != "OK":
            return {
                "status": "error",
                "error": f"API returned status: {response.get('status')}",
            }
        
        articles_data = response.get("results", [])
        num_results = response.get("num_results", 0)
        
        # Filter by sections if specified
        articles = []
        for article_data in articles_data:
            article = self.article_processor.process_most_popular_result(article_data)
            
            if sections:
                article_section = article.get("section", "")
                if article_section not in sections:
                    continue
            
            articles.append(article)
            if len(articles) >= max_results:
                break
        
        return {
            "status": "success",
            "total_hits": num_results,
            "articles": articles,
        }
    
    async def get_article(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Get a single article by URL.
        
        Parameters:
            url: Article URL
            include_content: Whether to fetch full content (default: true)
        """
        url = data.get("url", "")
        if not url:
            return {
                "status": "error",
                "error": "url parameter is required",
            }
        
        include_content = bool(data.get("include_content", True))
        
        # Search for article by URL using Article Search API
        # According to Article Search API spec: use 'url' field (not 'web_url')
        filter_query = f'url:"{url}"'
        
        response = self.nytimes_client.article_search.search_articles(
            filter_query=filter_query,
            page=0,
            sort="newest",
        )
        
        if isinstance(response, dict) and "error" in response:
            return {
                "status": "error",
                "error": response.get("error"),
            }
        
        if response.get("status") != "OK":
            return {
                "status": "error",
                "error": f"API returned status: {response.get('status')}",
            }
        
        response_data = response.get("response", {})
        docs = response_data.get("docs", [])
        
        if not docs:
            return {
                "status": "error",
                "error": "Article not found",
            }
        
        article = self.article_processor.process_article_search_result(docs[0])
        
        # Fetch content if requested
        if include_content:
            content = await self._fetch_article_content(url)
            if content:
                article = self.article_processor.add_content_to_article(article, content)
        
        return {
            "status": "success",
            "article": article,
        }
    
    async def start_monitoring(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Start monitoring manually."""
        try:
            self.monitoring_service.start_monitoring()
            return {
                "status": "success",
                "message": "Monitoring started",
            }
        except Exception as e:
            return {
                "status": "error",
                "error": str(e),
            }
    
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
    
    async def stop_monitoring(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Stop monitoring."""
        try:
            self.monitoring_service.stop_monitoring()
            return {
                "status": "success",
                "message": "Monitoring stopped",
            }
        except Exception as e:
            return {
                "status": "error",
                "error": str(e),
            }

