"""News Scraper Agent.

This agent scrapes news from a configured URL, extracts text content from article URLs,
and saves them to organized folders with SHA256-hashed filenames.
"""

import os
import hashlib
import time
from pathlib import Path
from typing import Dict, Any, List, Optional, Set
from datetime import datetime
from urllib.parse import urljoin, urlparse
import re

from moose.framework import BaseAgent

try:
    import requests
    from bs4 import BeautifulSoup
    import html2text
    from lxml import etree
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False


class NewsScraper(BaseAgent):
    """
    Generic news scraper agent.
    
    Scrapes news from a configured URL, extracts text content from article URLs,
    and saves them to organized folders with SHA256-hashed filenames.
    """
    
    name = "news_scraper"
    description = "Generic news scraper that extracts text content from web pages"
    
    def __init__(self, config_path=None, debug=False):
        """Initialize the news scraper."""
        super().__init__(config_path, debug=debug)
        
        if not REQUESTS_AVAILABLE:
            raise ImportError(
                "Required packages not installed. Install with: "
                "pip install requests beautifulsoup4 html2text lxml"
            )
        
        # Load scraper configuration
        self.scraper_config = self._load_scraper_config()
        
        # Initialize data directory
        data_dir = os.getenv("SCRAPER_DATA_DIR", "/data/scraper/finviz.com")
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.logger.info(f"Data directory: {self.data_dir}")
        
        # Rate limiting
        self.rate_limit = self.scraper_config.get("rate_limit", 60)  # requests per minute
        self.request_timestamps: List[float] = []
        self.min_request_interval = 60.0 / self.rate_limit if self.rate_limit > 0 else 0
        
        # HTTP session
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": self.scraper_config.get(
                "user_agent",
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            )
        })
        
        # HTML to text converter
        self.html_converter = html2text.HTML2Text()
        self.html_converter.ignore_links = False
        self.html_converter.ignore_images = True
        self.html_converter.body_width = 0  # Don't wrap lines
        
        self.logger.info(f"Initialized scraper with start_url: {self.scraper_config.get('start_url')}")
    
    def _load_scraper_config(self) -> Dict[str, Any]:
        """Load scraper configuration from agent config."""
        scraper_config = self.config.get("scraper_config", {})
        
        # Set defaults
        defaults = {
            "start_url": "https://finviz.com/news.ashx",
            "rate_limit": 60,
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "max_retrieval_depth": 1,
            "xpath": None
        }
        
        # Merge with defaults
        config = {**defaults, **scraper_config}
        
        self.logger.debug(f"Loaded scraper config: {config}")
        return config
    
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
    
    def _apply_xpath(self, html_content: str, xpath: str) -> Optional[str]:
        """
        Apply XPath selector to HTML content.
        
        Args:
            html_content: HTML content as string
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
                    html_content = extracted_html
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
            return article_urls
            
        except Exception as e:
            self.logger.error(f"Error scraping feed {url}: {e}", exc_info=True)
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
            self.logger.error(f"Error extracting text from {url}: {e}", exc_info=True)
            return None
    
    def save_article(self, file_path: Path, text_content: str, article_date: Optional[datetime] = None) -> Optional[Path]:
        """
        Save article text to organized folder structure.
        
        Args:
            url: Article URL
            text_content: Extracted text content
            article_date: Article date (defaults to current date)
            
        Returns:
            Path to saved file or None if failed
        """
        if not text_content:
            return None
        
        date_folder = file_path.parent
        date_folder.mkdir(parents=True, exist_ok=True)
        
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(text_content)
            
            self.logger.info(f"Saved article to: {file_path}")
            return file_path
            
        except Exception as e:
            self.logger.error(f"Error saving article to {file_path}: {e}")
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
    
    def process(self, input_data=None) -> Any:
        """
        Main processing method.
        
        Args:
            input_data: Can be:
                - Dict with optional keys: start_url, max_depth, date
                - String: treated as start_url override
                - Empty/None: uses config defaults
                
        Returns:
            Dict with scraping results
        """
        try:
            # Parse input
            if isinstance(input_data, str):
                start_url = input_data
                max_depth = self.scraper_config.get("max_retrieval_depth", 1)
            elif isinstance(input_data, dict):
                start_url = input_data.get("start_url") or self.scraper_config.get("start_url")
                max_depth = input_data.get("max_depth") or self.scraper_config.get("max_retrieval_depth", 1)
            else:
                start_url = self.scraper_config.get("start_url")
                max_depth = self.scraper_config.get("max_retrieval_depth", 1)
            
            if not start_url:
                return {
                    "status": "error",
                    "error": "No start_url provided or configured"
                }
            
            self.logger.info(f"Starting scrape with start_url: {start_url}, max_depth: {max_depth}")
            
            # Scrape feed to get article URLs
            article_urls = self.scrape_feed(start_url)
            
            if not article_urls:
                return {
                    "status": "success",
                    "message": "No articles found",
                    "articles_scraped": 0,
                    "articles_skipped": 0,
                    "articles_failed": 0
                }
            
            # Process articles
            articles_scraped = 0
            articles_skipped = 0
            articles_failed = 0
            saved_files = []
            
            for url in article_urls:
                # Check if already scraped (deduplication)
                file_path = self._get_file_path_for_url(url)
                if file_path.exists():
                    self.logger.debug(f"Skipping already scraped: {url}")
                    articles_skipped += 1
                    continue
                
                # Extract text
                text_content = self.extract_text_from_url(url)
                
                if text_content:
                    # Save article
                    saved_path = self.save_article(file_path, text_content)
                    if saved_path:
                        articles_scraped += 1
                        saved_files.append(str(saved_path))
                    else:
                        articles_failed += 1
                else:
                    articles_failed += 1
            
            result = {
                "status": "success",
                "start_url": start_url,
                "articles_found": len(article_urls),
                "articles_scraped": articles_scraped,
                "articles_skipped": articles_skipped,
                "articles_failed": articles_failed,
                "saved_files": saved_files[:10]  # Limit to first 10 for response size
            }
            
            self.logger.info(
                f"Scraping complete: {articles_scraped} scraped, "
                f"{articles_skipped} skipped, {articles_failed} failed"
            )
            
            return result
            
        except Exception as e:
            self.logger.error(f"Error in process: {e}", exc_info=True)
            return {
                "status": "error",
                "error": str(e)
            }


if __name__ == "__main__":
    """Entry point for the agent."""
    import sys
    
    # Determine communication mode from environment or default to HTTP
    mode = os.getenv("MOOSE_AGENT_MODE", "http")
    
    # Get port from environment or config
    port = int(os.getenv("MOOSE_AGENT_PORT", "8000"))
    
    # Get debug flag from environment
    debug = os.getenv("MOOSE_AGENT_DEBUG", "false").lower() in ("true", "1", "yes", "on")
    
    # Initialize and run agent
    agent = NewsScraper(debug=debug)
    
    agent.process()
    # if mode == "http":
    #     agent.run(mode="http", port=port)
    # elif mode == "stdin":
    #     agent.run(mode="stdin")
    # elif mode == "file":
    #     watch_dir = os.getenv("MOOSE_AGENT_WATCH_DIR", "/project/agent_io")
    #     agent.run(mode="file", watch_dir=watch_dir)
    # else:
    #     print(f"Unknown mode: {mode}", file=sys.stderr)
    #    sys.exit(1)

