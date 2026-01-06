"""Content extraction from HTML for NYTimes articles."""

import re
import random
import asyncio
from typing import Optional, Dict, Any

try:
    from bs4 import BeautifulSoup
    import html2text
    EXTRACTION_AVAILABLE = True
except ImportError:
    EXTRACTION_AVAILABLE = False

try:
    from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError  # type: ignore
    PLAYWRIGHT_AVAILABLE = True
except Exception:
    PLAYWRIGHT_AVAILABLE = False
    async_playwright = None  # type: ignore
    PlaywrightTimeoutError = Exception  # type: ignore


class PlaywrightFetcher:
    """
    Minimal Playwright fetcher to render JS-heavy pages.
    
    Designed as a fallback only (requests remains the primary fetch path).
    Copied from news_scraper.
    """

    def __init__(self, *, logger=None, max_concurrent_pages: int = 1, headless: bool = True):
        if not PLAYWRIGHT_AVAILABLE:
            raise ImportError("Playwright is not available. Install with: pip install playwright (and install browsers).")
        self.logger = logger
        self._headless = bool(headless)
        self._sem = asyncio.Semaphore(max(1, int(max_concurrent_pages or 1)))
        self._pw = None
        self._browser = None
        self._lock = asyncio.Lock()

    async def _ensure_started(self, *, timeout_seconds: int = 30):
        # Lazy init because Playwright is heavy.
        if self._browser is not None:
            return
        async with self._lock:
            if self._browser is not None:
                return
            # Guard startup to avoid indefinite hangs during browser launch.
            to_s = max(5, int(timeout_seconds or 30))
            self._pw = await asyncio.wait_for(async_playwright().start(), timeout=to_s)  # type: ignore[misc]
            self._browser = await asyncio.wait_for(self._pw.chromium.launch(headless=self._headless), timeout=to_s)  # type: ignore[union-attr]
            if self.logger:
                self.logger.info("Playwright Chromium launched (fallback fetcher ready)")

    async def fetch_rendered_html(
        self,
        *,
        url: str,
        referer: Optional[str],
        user_agent: Optional[str],
        timeout_seconds: int,
        wait_selector: Optional[str],
    ) -> Optional[str]:
        await self._ensure_started(timeout_seconds=timeout_seconds)

        try:
            await asyncio.wait_for(self._sem.acquire(), timeout=max(1, int(timeout_seconds or 30)))
        except Exception:
            return None
        try:
            context_kwargs: Dict[str, Any] = {}
            if user_agent:
                context_kwargs["user_agent"] = str(user_agent)
            context = await self._browser.new_context(**context_kwargs)  # type: ignore[union-attr]
            page = await context.new_page()

            headers: Dict[str, str] = {}
            if referer:
                headers["Referer"] = str(referer)
            if headers:
                await page.set_extra_http_headers(headers)

            # Load and optionally wait for article selector.
            await page.goto(url, wait_until="domcontentloaded", timeout=int(timeout_seconds * 1000))
            if wait_selector:
                try:
                    await page.wait_for_selector(str(wait_selector), timeout=int(timeout_seconds * 1000))
                except PlaywrightTimeoutError:
                    # Not fatal; still try to capture content after hydration time.
                    pass

            # Small jitter for hydration/lazy text.
            await page.wait_for_timeout(int(random.uniform(500, 1500)))
            html_content = await page.content()

            try:
                await page.close()
            except Exception:
                pass
            try:
                await context.close()
            except Exception:
                pass

            return html_content
        except Exception as e:
            if self.logger:
                self.logger.warning(f"Playwright fetch failed for {url}: {e}")
            return None
        finally:
            try:
                self._sem.release()
            except Exception:
                pass


class ContentExtractor:
    """Extracts article content from HTML (for full content scraping)."""
    
    def __init__(self, logger=None, playwright_config: Optional[Dict[str, Any]] = None):
        """
        Initialize content extractor.
        
        Args:
            logger: Logger instance
            playwright_config: Optional playwright configuration dict
        """
        if not EXTRACTION_AVAILABLE:
            raise ImportError(
                "Required packages not installed. Install with: "
                "pip install beautifulsoup4 html2text lxml"
            )
        self.logger = logger
        self.html_converter = html2text.HTML2Text()
        self.html_converter.ignore_links = False
        self.html_converter.ignore_images = False
        self.html_converter.body_width = 0  # Don't wrap lines
        
        # Playwright configuration
        self.playwright_config = playwright_config or {}
        self._pw_fetcher: Optional[PlaywrightFetcher] = None
    
    def _is_paywalled(self, html_content: str) -> bool:
        """
        Detect if article is behind paywall.
        
        Args:
            html_content: HTML content to check
            
        Returns:
            True if paywall detected
        """
        if not html_content:
            return False
        
        html_lower = html_content.lower()
        
        # Common paywall indicators
        paywall_patterns = [
            "subscribe to continue reading",
            "this article is reserved for subscribers",
            "already a subscriber",
            "create a free account",
            "subscribe now",
            "limited time offer",
        ]
        
        for pattern in paywall_patterns:
            if pattern in html_lower:
                return True
        
        return False
    
    def _get_playwright_cfg(self) -> Dict[str, Any]:
        """Get playwright configuration."""
        return self.playwright_config if isinstance(self.playwright_config, dict) else {}
    
    async def fetch_with_playwright(
        self,
        url: str,
        referer: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> Optional[str]:
        """
        Fetch URL content using Playwright (for bypassing blocking).
        
        Args:
            url: URL to fetch
            referer: Optional referer URL
            user_agent: Optional user agent string
            
        Returns:
            HTML content or None if failed
        """
        pw_cfg = self._get_playwright_cfg()
        if not bool(pw_cfg.get("playwright_enabled", True)):
            return None
        
        if not PLAYWRIGHT_AVAILABLE:
            if self.logger:
                self.logger.warning("Playwright not available, cannot use Playwright fallback")
            return None
        
        try:
            if self._pw_fetcher is None:
                self._pw_fetcher = PlaywrightFetcher(
                    logger=self.logger,
                    max_concurrent_pages=int(pw_cfg.get("playwright_max_concurrent_pages", 1) or 1),
                    headless=bool(pw_cfg.get("playwright_headless", True)),
                )
            
            pw_timeout_s = int(pw_cfg.get("playwright_timeout_seconds", 35) or 35)
            wait_selector = str(pw_cfg.get("playwright_wait_selector") or "").strip() or None
            
            html_pw = await asyncio.wait_for(
                self._pw_fetcher.fetch_rendered_html(
                    url=url,
                    referer=referer,
                    user_agent=user_agent,
                    timeout_seconds=pw_timeout_s,
                    wait_selector=wait_selector,
                ),
                timeout=max(5, pw_timeout_s + 5),
            )
            return html_pw
        except Exception as e:
            if self.logger:
                self.logger.warning(f"Playwright fetch error for {url}: {e}")
            return None
    
    def extract_content(self, url: str, html_content: str) -> Optional[str]:
        """
        Extract main article content from HTML.
        
        Uses heavy normalization similar to news_scraper:
        - Finds article body using BeautifulSoup
        - Removes navigation, ads, footers
        - Converts to clean text using html2text
        - Normalizes whitespace
        
        Args:
            url: Article URL (for logging)
            html_content: Raw HTML content
            
        Returns:
            Extracted article text, or None if extraction failed or paywalled
        """
        if not html_content:
            return None
        
        # Check for paywall
        if self._is_paywalled(html_content):
            if self.logger:
                self.logger.warning(f"Paywall detected for {url}, skipping")
            return None
        
        try:
            soup = BeautifulSoup(html_content, 'lxml')
            
            # Remove script, style, and other non-content elements
            for element in soup(['script', 'style', 'nav', 'header', 'footer', 'aside', 'iframe', 'noscript']):
                element.decompose()
            
            # Try to find main article content
            # NYTimes articles typically have article tags or specific classes
            article_body = None
            
            # Try common article selectors
            selectors = [
                'article',
                '[role="article"]',
                '.StoryBodyCompanionColumn',  # NYTimes specific
                '.css-53u6y8',  # NYTimes article content class
                'section[name="articleBody"]',
                '.article-body',
            ]
            
            for selector in selectors:
                article_body = soup.select_one(selector)
                if article_body:
                    break
            
            # Fallback to body if no article found
            if not article_body:
                article_body = soup.find('body')
            
            if not article_body:
                if self.logger:
                    self.logger.warning(f"Could not find article content in {url}")
                return None
            
            # Convert to text using html2text
            html_text = str(article_body)
            text_content = self.html_converter.handle(html_text)
            
            # Normalize whitespace (similar to news_scraper)
            text_content = re.sub(r'\n\s*\n\s*\n+', '\n\n', text_content)  # Multiple newlines to double
            text_content = re.sub(r'[ \t]+', ' ', text_content)  # Multiple spaces to single
            text_content = text_content.strip()
            
            if not text_content:
                if self.logger:
                    self.logger.warning(f"Extracted empty text from {url}")
                return None
            
            if self.logger:
                self.logger.debug(f"Extracted {len(text_content)} characters from {url}")
            
            return text_content
            
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error extracting content from {url}: {e}")
            return None


def _basic_whitespace_cleanup(text: str) -> str:
    """
    Lightweight cleanup used as a fallback.
    
    Args:
        text: Text to clean
        
    Returns:
        Cleaned text
    """
    if not text:
        return ""
    # Normalize line endings
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # Collapse runs of spaces/tabs
    text = re.sub(r"[ \t]+", " ", text)
    # Collapse excessive blank lines
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()

