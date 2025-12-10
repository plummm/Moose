"""News Scraper Core - Scraping functionality separated from agent logic."""

import os
import hashlib
import json
import time
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime
from urllib.parse import urljoin, urlparse
import re

try:
    import requests
    from bs4 import BeautifulSoup
    import html2text
    from lxml import etree
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False


class NewsScraperCore:
    """
    Core scraping functionality for news articles.
    
    Handles:
    - Feed scraping and article URL extraction
    - Text extraction from article URLs
    - Article saving with organized folder structure
    - Rate limiting
    - Deduplication using index file
    """
    
    def __init__(
        self,
        data_dir: Path,
        scraper_config: Dict[str, Any],
        logger
    ):
        """
        Initialize the scraper core.
        
        Args:
            data_dir: Directory to save scraped articles
            scraper_config: Scraper configuration dictionary
            logger: Logger instance
        """
        if not REQUESTS_AVAILABLE:
            raise ImportError(
                "Required packages not installed. Install with: "
                "pip install requests beautifulsoup4 html2text lxml"
            )
        
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.scraper_config = scraper_config
        self.logger = logger
        
        # Rate limiting
        self.rate_limit = scraper_config.get("rate_limit", 60)  # requests per minute
        self.request_timestamps: List[float] = []
        self.min_request_interval = 60.0 / self.rate_limit if self.rate_limit > 0 else 0
        
        # HTTP session
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": scraper_config.get(
                "user_agent",
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            )
        })
        
        # HTML to text converter
        self.html_converter = html2text.HTML2Text()
        self.html_converter.ignore_links = False
        self.html_converter.ignore_images = True
        self.html_converter.body_width = 0  # Don't wrap lines
        
        # Index file for deduplication
        self.index_file = self.data_dir / ".scraper_index.json"
        self.index: Dict[str, Dict[str, Any]] = {}
        self._load_index()
        
        self.logger.info(f"Initialized scraper core with data_dir: {self.data_dir}")
    
    def _load_index(self):
        """Load the deduplication index from file."""
        if self.index_file.exists():
            try:
                with open(self.index_file, 'r', encoding='utf-8') as f:
                    self.index = json.load(f)
                self.logger.info(f"Loaded index with {len(self.index)} entries")
            except Exception as e:
                self.logger.warning(f"Failed to load index: {e}, starting with empty index")
                self.index = {}
        else:
            self.index = {}
    
    def _save_index(self):
        """Save the deduplication index to file."""
        try:
            with open(self.index_file, 'w', encoding='utf-8') as f:
                json.dump(self.index, f, indent=2)
            self.logger.debug(f"Saved index with {len(self.index)} entries")
        except Exception as e:
            self.logger.error(f"Failed to save index: {e}")
    
    def _rate_limit_check(self):
        """Enforce rate limiting by waiting if necessary."""
        if self.rate_limit <= 0:
            return
        
        current_time = time.time()
        
        # Remove timestamps older than 1 minute
        self.request_timestamps = [
            ts for ts in self.request_timestamps
            if current_time - ts < 60.0
        ]
        
        # If we've hit the rate limit, wait
        if len(self.request_timestamps) >= self.rate_limit:
            # Wait until the oldest request is more than 1 minute old
            oldest_timestamp = min(self.request_timestamps)
            wait_time = 60.0 - (current_time - oldest_timestamp) + 0.1
            if wait_time > 0:
                self.logger.debug(f"Rate limit reached, waiting {wait_time:.2f} seconds")
                time.sleep(wait_time)
                # Clean up again after waiting
                current_time = time.time()
                self.request_timestamps = [
                    ts for ts in self.request_timestamps
                    if current_time - ts < 60.0
                ]
        
        # Record this request
        self.request_timestamps.append(time.time())
    
    def _apply_xpath(self, html_content: bytes, xpath: str) -> Optional[str]:
        """
        Apply XPath selector to HTML content.
        
        Args:
            html_content: HTML content as bytes
            xpath: XPath selector expression
            
        Returns:
            Extracted HTML section or None if not found
        """
        if not xpath:
            return None
        
        try:
            # Parse HTML with lxml
            parser = etree.HTMLParser()
            tree = etree.fromstring(html_content.decode('utf-8'), parser)
            
            # Apply XPath
            elements = tree.xpath(xpath)
            
            if not elements:
                self.logger.warning(f"XPath '{xpath}' matched no elements")
                return None
            
            # Get HTML of matched elements
            if len(elements) == 1:
                result = etree.tostring(elements[0], encoding='unicode', method='html')
            else:
                # Multiple elements - concatenate
                result = ''.join(
                    etree.tostring(elem, encoding='unicode', method='html')
                    for elem in elements
                )
            
            self.logger.debug(f"XPath extracted {len(elements)} element(s)")
            return result
            
        except Exception as e:
            self.logger.error(f"Error applying XPath '{xpath}': {e}")
            return None
    
    def scrape_feed(self, url: Optional[str] = None) -> List[str]:
        """
        Scrape the feed page and extract article URLs.
        
        Args:
            url: URL to scrape (defaults to start_url from config)
            
        Returns:
            List of article URLs
        """
        url = url or self.scraper_config.get("start_url")
        if not url:
            self.logger.error("No URL provided and start_url not configured")
            return []
        
        self.logger.info(f"Scraping feed: {url}")
        
        try:
            self._rate_limit_check()
            response = self.session.get(url, timeout=30)
            response.raise_for_status()
            
            html_content = response.content
            # Apply XPath if configured
            xpath = self.scraper_config.get("xpath")
            if xpath:
                extracted_html = self._apply_xpath(html_content, xpath)
                if extracted_html:
                    html_content = extracted_html.encode('utf-8')
                else:
                    self.logger.warning(f"XPath extraction failed for {url}, using full page")
            
            soup = BeautifulSoup(html_content, 'lxml')
            
            # Extract all links
            article_urls = set()
            base_url = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
            
            for link in soup.find_all('a', href=True):
                href = link.get('href')
                if not href:
                    continue
                
                # Convert relative URLs to absolute
                absolute_url = urljoin(base_url, href)
                
                # Filter out non-article links (common patterns)
                # Skip: javascript:, mailto:, anchors, same-page links
                if any(href.startswith(prefix) for prefix in ['javascript:', 'mailto:', '#']):
                    continue
                
                # Skip if it's the same as the feed URL
                if urlparse(absolute_url).netloc == urlparse(url).netloc:
                    continue
                
                # Basic validation: should be http/https
                if not absolute_url.startswith(('http://', 'https://')):
                    continue
                
                article_urls.add(absolute_url)
            
            self.logger.info(f"Found {len(article_urls)} unique article URLs")
            return list(article_urls)
            
        except Exception as e:
            self.logger.error(f"Error scraping feed {url}: {e}")
            return []
    
    def extract_text_from_url(self, url: str) -> Optional[str]:
        """
        Fetch URL and extract plain text content.
        
        Args:
            url: URL to fetch and extract text from
            
        Returns:
            Extracted text content or None if failed
        """
        self.logger.debug(f"Extracting text from: {url}")
        
        try:
            self._rate_limit_check()
            response = self.session.get(url, timeout=30)
            response.raise_for_status()
            
            # Get HTML content
            html_content = response.text
            
            # Convert HTML to text
            text_content = self.html_converter.handle(html_content)
            
            # Clean up whitespace
            text_content = re.sub(r'\n\s*\n\s*\n+', '\n\n', text_content)  # Multiple newlines
            text_content = text_content.strip()
            
            if not text_content:
                self.logger.warning(f"No text content extracted from {url}")
                return None
            
            self.logger.debug(f"Extracted {len(text_content)} characters from {url}")
            return text_content
            
        except requests.exceptions.RequestException as e:
            self.logger.error(f"HTTP error fetching {url}: {e}")
            return None
        except Exception as e:
            self.logger.error(f"Error extracting text from {url}: {e}")
            return None
    
    def _get_file_path_for_url(self, url: str, article_date: Optional[datetime] = None) -> Path:
        """
        Get the expected file path for a URL (for deduplication check).
        
        Args:
            url: Article URL
            article_date: Article date (defaults to current date)
            
        Returns:
            Expected file path
        """
        url_hash = hashlib.sha256(url.encode('utf-8')).hexdigest()
        
        if article_date is None:
            article_date = datetime.now()
        
        date_folder = self.data_dir / str(article_date.year) / f"{article_date.month:02d}" / f"{article_date.day:02d}"
        return date_folder / f"{url_hash}.txt"
    
    def is_url_scraped(self, url: str) -> bool:
        """
        Check if a URL has already been scraped using index and file system.
        
        Args:
            url: Article URL to check
            
        Returns:
            True if URL has been scraped, False otherwise
        """
        url_hash = hashlib.sha256(url.encode('utf-8')).hexdigest()
        
        # Check index first (faster)
        if url_hash in self.index:
            index_entry = self.index[url_hash]
            file_path = Path(index_entry.get("file_path", ""))
            # Verify file still exists
            if file_path.exists():
                return True
            else:
                # File was deleted, remove from index
                del self.index[url_hash]
                self._save_index()
        
        # Fallback to file system check
        file_path = self._get_file_path_for_url(url)
        if file_path.exists():
            # Add to index for future lookups
            self.index[url_hash] = {
                "file_path": str(file_path),
                "scraped_at": datetime.now().isoformat()
            }
            self._save_index()
            return True
        
        return False
    
    def save_article(
        self,
        url: str,
        text_content: str,
        article_date: Optional[datetime] = None
    ) -> Optional[Path]:
        """
        Save article text to organized folder structure and update index.
        
        Args:
            url: Article URL
            text_content: Extracted text content
            article_date: Article date (defaults to current date)
            
        Returns:
            Path to saved file or None if failed
        """
        if not text_content:
            return None
        
        file_path = self._get_file_path_for_url(url, article_date)
        date_folder = file_path.parent
        date_folder.mkdir(parents=True, exist_ok=True)
        
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(text_content)
            
            # Update index
            url_hash = hashlib.sha256(url.encode('utf-8')).hexdigest()
            self.index[url_hash] = {
                "file_path": str(file_path),
                "scraped_at": datetime.now().isoformat()
            }
            self._save_index()
            
            self.logger.info(f"Saved article to: {file_path}")
            return file_path
            
        except Exception as e:
            self.logger.error(f"Error saving article to {file_path}: {e}")
            return None
    
    def save_index(self):
        """Manually save the index (called after scraping session)."""
        self._save_index()


class NewsScraperService:
    """
    High-level scraping service that orchestrates scraping operations.
    
    Scrapes articles and sends file paths to finance_office agent.
    """
    
    @staticmethod
    def load_scraper_config(config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Load scraper configuration from agent config.
        
        Args:
            config: Agent configuration dictionary
            
        Returns:
            Scraper configuration dictionary with defaults applied
        """
        custom_config = config.get("custom", {})
        scraper_config = custom_config.get("scraper_config", {})
        
        # Set defaults
        defaults = {
            "start_url": "https://finviz.com/news.ashx",
            "rate_limit": 60,
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "max_retrieval_depth": 1,
            "xpath": None
        }
        
        # Merge with defaults
        merged_config = {**defaults, **scraper_config}
        
        return merged_config
    
    def __init__(
        self,
        scraper_core: NewsScraperCore,
        analyzer_endpoint: str = "http://localhost:3501/get_financial_news",
        logger=None
    ):
        """
        Initialize the scraping service.
        
        Args:
            scraper_core: NewsScraperCore instance
            analyzer_endpoint: HTTP endpoint URL for finance_office
            logger: Logger instance
        """
        self.scraper_core = scraper_core
        self.analyzer_endpoint = analyzer_endpoint
        self.logger = logger
        
        # Import requests for HTTP calls
        try:
            import requests
            self.requests_available = True
        except ImportError:
            self.requests_available = False
            if logger:
                logger.warning("requests not available, cannot send to analyzer")
    
    async def scrape(self, input_data=None) -> Dict[str, Any]:
        """
        Main scraping method.
        
        Args:
            input_data: Can be:
                - Dict with optional keys: start_url, max_depth
                - String: treated as start_url override
                - Empty/None: uses config defaults
                
        Returns:
            Dict with scraping results
        """
        try:
            # Parse input
            scraper_config = self.scraper_core.scraper_config
            if isinstance(input_data, str):
                start_url = input_data
                max_depth = scraper_config.get("max_retrieval_depth", 1)
            elif isinstance(input_data, dict):
                start_url = input_data.get("start_url") or scraper_config.get("start_url")
                max_depth = input_data.get("max_depth") or scraper_config.get("max_retrieval_depth", 1)
            else:
                start_url = scraper_config.get("start_url")
                max_depth = scraper_config.get("max_retrieval_depth", 1)
            
            if not start_url:
                return {
                    "status": "error",
                    "error": "No start_url provided or configured"
                }
            
            return await self._scrape_direct(start_url, max_depth)
            
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error in scrape: {e}")
            return {
                "status": "error",
                "error": str(e)
            }
    
    def _send_to_analyzer(self, file_path: str, url: str) -> bool:
        """
        Send file path to finance_office agent.
        
        Args:
            file_path: Path to scraped article file
            url: Original URL of the article
            
        Returns:
            True if successfully sent
        """
        if not self.requests_available:
            if self.logger:
                self.logger.warning("requests not available, cannot send to analyzer")
            return False
        
        try:
            import requests
            
            payload = {
                "file_path": str(file_path),
                "url": url,
                "metadata": {}
            }
            
            response = requests.post(
                self.analyzer_endpoint,
                json=payload,
                timeout=10
            )
            
            if response.status_code == 200:
                if self.logger:
                    self.logger.debug(f"Sent file path to analyzer: {file_path}")
                return True
            else:
                if self.logger:
                    self.logger.warning(
                        f"Failed to send to analyzer (status {response.status_code}): {response.text}"
                    )
                return False
                
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error sending to analyzer: {e}")
            return False
    
    async def _scrape_direct(self, start_url: str, max_depth: int) -> Dict[str, Any]:
        """Scrape articles and send file paths to analyzer."""
        if self.logger:
            self.logger.info(f"Starting scrape: {start_url}, max_depth: {max_depth}")
        
        # Scrape feed to get article URLs
        article_urls = self.scraper_core.scrape_feed(start_url)
        
        if not article_urls:
            return {
                "status": "success",
                "message": "No articles found",
                "articles_scraped": 0,
                "articles_skipped": 0,
                "articles_failed": 0,
                "sent_to_analyzer": 0
            }
        
        # Process articles
        articles_scraped = 0
        articles_skipped = 0
        articles_failed = 0
        sent_to_analyzer = 0
        saved_files = []
        
        for url in article_urls:
            # Check if already scraped (deduplication)
            if self.scraper_core.is_url_scraped(url):
                if self.logger:
                    self.logger.debug(f"Skipping already scraped: {url}")
                articles_skipped += 1
                continue
            
            # Extract text
            text_content = self.scraper_core.extract_text_from_url(url)
            
            if text_content:
                # Save article
                saved_path = self.scraper_core.save_article(url, text_content)
                if saved_path:
                    articles_scraped += 1
                    saved_files.append(str(saved_path))
                    
                    # Send to analyzer
                    if self._send_to_analyzer(saved_path, url):
                        sent_to_analyzer += 1
                else:
                    articles_failed += 1
            else:
                articles_failed += 1
        
        # Save index after scraping session
        self.scraper_core.save_index()
        
        result = {
            "status": "success",
            "articles_found": len(article_urls),
            "articles_scraped": articles_scraped,
            "articles_skipped": articles_skipped,
            "articles_failed": articles_failed,
            "sent_to_analyzer": sent_to_analyzer,
        }
        
        if self.logger:
            self.logger.info(
                f"Scraping complete: {articles_scraped} scraped, "
                f"{articles_skipped} skipped, {articles_failed} failed, "
                f"{sent_to_analyzer} sent to analyzer"
            )
        
        return result

