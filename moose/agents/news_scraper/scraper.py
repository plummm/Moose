"""News Scraper Core - Scraping functionality separated from agent logic."""

import os
import hashlib
import json
import time
import random
import threading
import asyncio
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone, timedelta
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

try:
    # Playwright is optional and only used when enabled by config.
    from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError  # type: ignore
    PLAYWRIGHT_AVAILABLE = True
except Exception:
    PLAYWRIGHT_AVAILABLE = False
    async_playwright = None  # type: ignore
    PlaywrightTimeoutError = Exception  # type: ignore

try:
    from moose.framework.llm_core import LLMClient
except Exception:
    LLMClient = None  # type: ignore[assignment]


class PlaywrightFetcher:
    """
    Minimal Playwright fetcher to render JS-heavy pages.

    Designed as a fallback only (requests remains the primary fetch path).
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


def _extract_json(text: str) -> Optional[dict]:
    s = (text or "").strip()
    if not s:
        return None

    start = s.find("{")
    end = s.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(s[start : end + 1])
    except Exception:
        return None


def _json_decode_error(text: str) -> str:
    """
    Best-effort JSON error message for LLM outputs, aligned with `_extract_json` behavior.
    """
    s = (text or "").strip()
    if not s:
        return "Empty output."
    # Strip common code fences (same as _extract_json)
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", s).strip()
        s = re.sub(r"\s*```$", "", s).strip()
    start = s.find("{")
    end = s.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return "No JSON object boundaries found (missing '{' or '}')."
    try:
        json.loads(s[start : end + 1])
        return "Unknown JSON error (parsed successfully in diagnostic)."
    except Exception as e:
        return f"{type(e).__name__}: {e}"


async def _repair_json_once(llm_client: Any, *, bad_output: str, error_hint: str) -> Any:
    """
    One-shot JSON repair retry (minimal context).
    Returns the new LLM response object.
    """
    repair_system_message = (
        "You are a JSON repair tool.\n"
        "CRITICAL OUTPUT REQUIREMENT:\n"
        "- Return ONLY a single valid JSON object (no markdown fences, no leading/trailing quotes, no commentary).\n"
        "- JSON strings MUST be valid: do not include raw double quotes (\") inside string values.\n"
        "  If you need to quote text, use \\\" ... \\\" or use Chinese quotes 「...」.\n"
        "- Use \\n for newlines inside string values.\n"
    )
    repair_user_message = (
        "Your previous output was invalid JSON and could not be parsed.\n"
        f"Parser error: {error_hint}\n\n"
        "Fix the INVALID OUTPUT below so it becomes strict valid JSON.\n"
        "Keep the same keys/structure and preserve content as much as possible.\n\n"
        "INVALID OUTPUT:\n"
        + str(bad_output or "")
        + "\n\nNow output the corrected JSON object ONLY."
    )
    return await llm_client.send_message(message=repair_user_message, system_message=repair_system_message)


def _basic_whitespace_cleanup(text: str) -> str:
    """
    Lightweight cleanup used as a fallback when the LLM fails.
    """
    s = (text or "").strip()
    if not s:
        return ""
    # Normalize line endings
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    # Collapse runs of spaces/tabs
    s = re.sub(r"[ \t]+", " ", s)
    # Collapse excessive blank lines
    s = re.sub(r"\n\s*\n\s*\n+", "\n\n", s)
    return s.strip()


class NewsScraperLLMClient:
    """
    LLM-powered normalizer for noisy extracted web page text.

    Contract: return a dict with exactly:
      - summary: short summary of the article
      - raw_article: cleaned main article body (human-readable)
    """

    def __init__(self, *, llm_config: Dict[str, Any], logger=None):
        self.logger = logger
        self.llm_config = llm_config or {}

        model = str(self.llm_config.get("model") or "").strip()
        if not model:
            raise ValueError("Missing required config: custom.llm_config.model")
        temperature = float(self.llm_config.get("temperature", 0.3))
        kwargs = self.llm_config.get("kwargs") if isinstance(self.llm_config.get("kwargs"), dict) else {}

        if LLMClient is None:
            raise ImportError("LLMClient is unavailable (moose.framework.llm_core). Ensure LLM dependencies are installed.")

        self.client = LLMClient(
            model=model,
            temperature=temperature,
            enable_multi_stage_reasoning=False,
            tools=[],
            **(kwargs or {}),
        )

    async def normalize_article(self, *, url: str, extracted_text: str) -> Dict[str, str]:
        extracted_text = str(extracted_text or "")
        cleaned_fallback = _basic_whitespace_cleanup(extracted_text)

        if not cleaned_fallback:
            return {"summary": "", "raw_article": ""}

        system_message = """You are a text normalization engine for scraped web pages.

Goal:
- Identify and extract ONLY the main news article content from the provided extracted page text.
- Discard unrelated text: navigation, menus, cookie banners, subscription prompts, ads, related links, author bio blocks, stock tickers lists, timestamps-only blocks, footers, disclaimers, and repeated boilerplate.
- Remove stray symbols/ASCII art/markdown noise when it is not part of the actual article.
- Normalize whitespace into a human-readable article with proper paragraphs.
- Normalize the article text to markdown format.
- Give a quality score for the article between 1 and 10, and a rationale of 2-3 sentences for the quality score.

Strict output format:
- Return STRICT JSON ONLY. No markdown, no code fences, no commentary.
- Return exactly this object shape (no extra keys):
  {"title", "...", "summary": "...", "raw_article": "...", "quality_score": <int 1-10>, "rationale": "..."}

JSON rules:
- Your output MUST be valid JSON parseable by json.loads().
- Do not include literal newlines inside JSON strings. Use \\n to represent line breaks.
- Use \\n\\n between paragraphs in raw_article.

Quality score (1-10):
- <1-3> (either one of the following reasons): 
    - The source of the article is not trustworthy; 
    - The article is not complete, or missing important information; (e.g., news article was not fully loaded, the current content contain limited interesting insights)
    - The article is bad written, confusing, or self-contradictory;
    - The article has obvious flaws, errors, or inconsistencies;
    - A pure stock prompting article with no interesting insights;
- <4-6> (either one of the following reasons): 
    - A standard news article with no obvious flaws, and no interesting insights either;
    - A stock prompting article that somewhat interesting insights;
    - A standard technical analysis article;
- <7-10> (either one of the following reasons): 
    - An insightful article with clear knowledge;
    - An insightful technical analysis article with no obvious flaws;
    - An article that discloses insider information;
    - An article about a major event or news;

Mandatory rules:
- Do NOT change any wording of the article, keep the text as is for the article content. Do NOT summarize, add, or remove any article main text.
- Keep the image href as is in the article main text
- Title should be a short in one sentence.
- Keep summary concise (3-6 sentences).
- Article length cannot be a solo factor to determine the quality of the article; A breaking news can be short and concise, in fact, these short breaking news offen come with extrememly important updates."""

        user_message = f"""URL: {url}

EXTRACTED_TEXT:
{extracted_text}

Return STRICT JSON only."""

        try:
            resp = await self.client.send_message(message=user_message, system_message=system_message)
            content = getattr(resp, "content", "") or ""
            if not isinstance(content, str):
                content = str(content)
        except Exception as e:
            if self.logger:
                self.logger.warning(f"LLM normalization failed for {url}: {e}")
            return {"title": "", "summary": "", "raw_article": cleaned_fallback, "quality_score": 0}

        data = _extract_json(content)
        if not isinstance(data, dict):
            # One-shot JSON repair retry
            try:
                repaired = await _repair_json_once(self.client, bad_output=str(content), error_hint=_json_decode_error(content))
                repaired_content = getattr(repaired, "content", "") or ""
                if not isinstance(repaired_content, str):
                    repaired_content = str(repaired_content)
                data = _extract_json(repaired_content)
            except Exception:
                data = None
        if not isinstance(data, dict):
            if self.logger:
                self.logger.warning(f"LLM returned non-JSON for {url}; falling back to cleaned extraction")
            return {"title": "", "summary": "", "raw_article": cleaned_fallback, "quality_score": 0}

        title = str(data.get("title") or "").strip()
        summary = str(data.get("summary") or "").strip()
        quality_score = int(data.get("quality_score", 0))
        raw_article = str(data.get("raw_article") or "").strip()
        rationale = str(data.get("rationale") or "").strip()
        if not raw_article:
            raw_article = cleaned_fallback

        return {"title": title, "summary": summary, "raw_article": raw_article, "quality_score": quality_score, "rationale": rationale}


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

        # Stealth / anti-bot settings
        self.stealth_mode = bool(scraper_config.get("stealth_mode", True))
        self.header_profile = str(scraper_config.get("header_profile", "auto") or "auto").strip() or "auto"
        try:
            self.min_article_chars = int(scraper_config.get("min_article_chars", 1200))
        except Exception:
            self.min_article_chars = 1200
        try:
            self.block_max_retries = int(scraper_config.get("block_max_retries", 2))
        except Exception:
            self.block_max_retries = 2
        try:
            self.backoff_base_seconds = float(scraper_config.get("backoff_base_seconds", 2.0))
        except Exception:
            self.backoff_base_seconds = 2.0
        try:
            self.backoff_max_seconds = float(scraper_config.get("backoff_max_seconds", 120.0))
        except Exception:
            self.backoff_max_seconds = 120.0

        self._last_feed_url: Optional[str] = None
        self._domain_failures: Dict[str, int] = {}
        self._domain_cooldown_until: Dict[str, float] = {}
        # Playwright fallback (lazy init)
        self._pw_fetcher: Optional[PlaywrightFetcher] = None
        self._pw_fallback_attempted: int = 0
        self._pw_fallback_succeeded: int = 0
        
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

        # Pick a stable header profile for this run (avoid per-request randomization)
        self._run_header_profile_name = self._choose_header_profile_name()
        if self.logger:
            self.logger.info(f"Stealth: enabled={self.stealth_mode}, header_profile={self._run_header_profile_name}")
        
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

    def _choose_header_profile_name(self) -> str:
        pref = (self.header_profile or "auto").strip().lower()
        if pref and pref != "auto":
            return pref
        return random.choice(["chrome_windows", "chrome_macos", "safari_macos"])

    def _header_profiles(self) -> Dict[str, Dict[str, str]]:
        # Coherent, conservative header sets. Keep Accept-Encoding compatible with requests defaults.
        return {
            "chrome_windows": {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "gzip, deflate",
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1",
            },
            "chrome_macos": {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "gzip, deflate",
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1",
            },
            "safari_macos": {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "gzip, deflate",
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1",
            },
        }

    def _build_request_headers(self, *, url: str, referer: Optional[str]) -> Dict[str, str]:
        if not self.stealth_mode:
            return {}
        profiles = self._header_profiles()
        base = dict(profiles.get(getattr(self, "_run_header_profile_name", "") or "", {}))

        # Respect explicitly configured UA if provided
        configured_ua = str(self.scraper_config.get("user_agent") or "").strip()
        if configured_ua:
            base["User-Agent"] = configured_ua

        if referer:
            base["Referer"] = referer
        return base

    def _is_likely_blocked(self, *, status_code: int, body_text: str) -> bool:
        if status_code in (403, 429, 503):
            return True
        # Be conservative here: false positives are worse than misses.
        # Example: many normal pages include "Roboto" (font), which would match "robot" substring.
        s = body_text or ""
        if not s:
            return False

        # High-confidence bot/interstitial signatures (status 200 pages can still be challenge pages).
        patterns = [
            # Challenge platforms (prefer URL/path signatures over generic words)
            r"/cdn-cgi/challenge-platform",
            r"\bcf-chl-\w+",
            r"captcha-delivery\.com",
            r"checking your browser before accessing",
            r"just a moment\W*\.*",  # Cloudflare-style interstitial title/text
            r"attention required!\s*\|\s*cloudflare",
            # Common bot wall phrasing
            r"\bverify you are human\b",
            r"\bunusual traffic\b",
            r"\bpardon the interruption\b",
            r"\baccess denied\b",
            r"\btemporarily unavailable\b",
            r"\bplease enable javascript\b",
            r"\benable javascript to continue\b",
        ]

        low = s.lower()
        for pat in patterns:
            try:
                if re.search(pat, low, flags=re.IGNORECASE):
                    return True
            except Exception:
                # If regex fails for any reason, ignore and continue
                continue
        return False

    def _cooldown_before_request(self, domain: str):
        until = float(self._domain_cooldown_until.get(domain, 0.0) or 0.0)
        now = time.time()
        if until > now:
            sleep_s = min(until - now, float(self.backoff_max_seconds or 120.0))
            if self.logger:
                self.logger.warning(f"Cooldown active for {domain}: sleeping {sleep_s:.1f}s")
            time.sleep(max(0.0, sleep_s))

    def _register_block_and_backoff(self, domain: str) -> float:
        fails = int(self._domain_failures.get(domain, 0) or 0) + 1
        self._domain_failures[domain] = fails

        base = max(0.5, float(self.backoff_base_seconds or 2.0))
        cap = max(base, float(self.backoff_max_seconds or 120.0))
        delay = min(cap, base * (2 ** min(fails, 10)))
        jitter = random.uniform(0.0, min(1.0, delay * 0.1))
        delay = min(cap, delay + jitter)

        self._domain_cooldown_until[domain] = time.time() + delay
        return delay

    def _register_success(self, domain: str):
        self._domain_failures[domain] = 0
        self._domain_cooldown_until[domain] = 0.0
    
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
            self._last_feed_url = url
            domain = urlparse(url).netloc

            # Retry feed fetch if it looks blocked
            max_retries = max(0, int(self.block_max_retries or 0))
            response = None
            for attempt in range(max_retries + 1):
                self._cooldown_before_request(domain)
                self._rate_limit_check()
                headers = self._build_request_headers(url=url, referer=None)
                response = self.session.get(url, timeout=30, headers=headers)

                body_text = ""
                try:
                    body_text = response.text or ""
                except Exception:
                    body_text = ""

                if self._is_likely_blocked(status_code=int(getattr(response, "status_code", 0) or 0), body_text=body_text):
                    delay = self._register_block_and_backoff(domain)
                    if self.logger:
                        self.logger.warning(
                            f"Blocked-like feed response for {url} (status={getattr(response,'status_code',None)}), "
                            f"attempt={attempt+1}/{max_retries+1}, backoff={delay:.1f}s"
                        )
                    if attempt >= max_retries:
                        return []
                    continue

                response.raise_for_status()
                self._register_success(domain)
                break
            
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
    
    async def extract_text_from_url(self, url: str) -> Optional[str]:
        """
        Fetch URL and extract plain text content.
        
        Args:
            url: URL to fetch and extract text from
            
        Returns:
            Extracted text content or None if failed
        """
        self.logger.debug(f"Extracting text from: {url}")
        
        try:
            domain = urlparse(url).netloc
            referer = self._last_feed_url

            max_retries = max(0, int(self.block_max_retries or 0))
            html_content = ""

            pw_used = False
            for attempt in range(max_retries + 1):
                # Do blocking requests work in a worker thread to avoid blocking the event loop.
                fetch_timeout_s = 45.0
                try:
                    fetch_timeout_s = float(self.scraper_config.get("fetch_timeout_seconds", 45) or 45)
                except Exception:
                    fetch_timeout_s = 45.0
                fetch_timeout_s = max(5.0, fetch_timeout_s)
                try:
                    status_code, body_text, req_err = await asyncio.wait_for(
                        asyncio.to_thread(self._fetch_url_text_sync, url, referer),
                        timeout=fetch_timeout_s,
                    )
                except asyncio.TimeoutError:
                    # requests' timeout does not reliably cover DNS resolution; protect the scrape loop.
                    if self.logger:
                        self.logger.warning(
                            f"Timed out fetching URL after {fetch_timeout_s:.0f}s (possible DNS hang): {url}"
                        )
                    status_code, body_text, req_err = 0, "", f"timeout_after_{int(fetch_timeout_s)}s"

                blocked_like = self._is_likely_blocked(
                    status_code=int(status_code or 0),
                    body_text=body_text,
                )
                if blocked_like:
                    # Optional Playwright fallback on block-like response
                    pw_cfg = self._get_playwright_cfg()
                    if bool(pw_cfg.get("playwright_enabled")) and bool(pw_cfg.get("playwright_fallback_on_block", True)):
                        if self.logger:
                            self.logger.debug(f"Playwright fallback starting (blocked-like): {url}")
                        html_pw = await self._try_playwright_fetch(url=url, referer=referer)
                        if html_pw:
                            pw_used = True
                            self._register_success(domain)
                            html_content = html_pw
                            break

                    delay = self._register_block_and_backoff(domain)
                    if self.logger:
                        self.logger.warning(
                            f"Blocked-like article response for {url} (status={status_code}), "
                            f"attempt={attempt+1}/{max_retries+1}, backoff={delay:.1f}s"
                        )
                    if attempt >= max_retries:
                        return None
                    continue

                if req_err:
                    # Not a block but request failed
                    raise requests.exceptions.RequestException(req_err)
                self._register_success(domain)
                html_content = body_text
                break
            
            # Convert HTML to text
            text_content = self.html_converter.handle(html_content)
            text_content = re.sub(r'\n\s*\n\s*\n+', '\n\n', text_content)  # Multiple newlines
            text_content = text_content.strip()

            # Content sanity: very short text often indicates an interstitial / JS wall
            if self.stealth_mode and self.min_article_chars and len(text_content) < int(self.min_article_chars):
                if pw_used:
                    self.logger.warning(f"Playwright fallback used but content is still too short for {url}")
                    return None
                pw_cfg = self._get_playwright_cfg()
                if bool(pw_cfg.get("playwright_enabled")) and bool(pw_cfg.get("playwright_fallback_on_short_content", True)):
                    if self.logger:
                        self.logger.debug(f"Playwright fallback starting (short content): {url}")
                    html_pw = await self._try_playwright_fetch(url=url, referer=referer)
                    if html_pw:
                        text2 = self.html_converter.handle(html_pw)
                        text2 = re.sub(r'\n\s*\n\s*\n+', '\n\n', text2).strip()
                        if text2 and len(text2) >= int(self.min_article_chars):
                            return text2

                delay = self._register_block_and_backoff(domain)
                if self.logger:
                    self.logger.warning(
                        f"Suspiciously short article text for {url} (len={len(text_content)} < {int(self.min_article_chars)}); "
                        f"cooldown={delay:.1f}s"
                    )
                return None
            
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

    def _get_playwright_cfg(self) -> Dict[str, Any]:
        scfg = self.scraper_config or {}
        return scfg.get("playwright") if isinstance(scfg.get("playwright"), dict) else {}

    async def _try_playwright_fetch(self, *, url: str, referer: Optional[str]) -> Optional[str]:
        """
        Best-effort Playwright fallback fetch. Returns rendered HTML or None.
        """
        pw_cfg = self._get_playwright_cfg()
        if not bool(pw_cfg.get("playwright_enabled")):
            return None

        self._pw_fallback_attempted += 1
        try:
            if self._pw_fetcher is None:
                self._pw_fetcher = PlaywrightFetcher(
                    logger=self.logger,
                    max_concurrent_pages=int(pw_cfg.get("playwright_max_concurrent_pages", 1) or 1),
                    headless=bool(pw_cfg.get("playwright_headless", True)),
                )

            ua = str(pw_cfg.get("playwright_user_agent") or "").strip()
            if not ua:
                ua = str(self.scraper_config.get("user_agent") or "").strip()
            ua = ua or None

            # Guard Playwright fetch with an outer timeout too.
            pw_timeout_s = int(pw_cfg.get("playwright_timeout_seconds", 35) or 35)
            html_pw = await asyncio.wait_for(
                self._pw_fetcher.fetch_rendered_html(
                url=url,
                referer=referer,
                user_agent=ua,
                    timeout_seconds=pw_timeout_s,
                wait_selector=str(pw_cfg.get("playwright_wait_selector") or "").strip() or None,
                ),
                timeout=max(5, pw_timeout_s + 5),
            )
            if html_pw:
                self._pw_fallback_succeeded += 1
            return html_pw
        except Exception as e:
            if self.logger:
                self.logger.warning(f"Playwright fallback error for {url}: {e}")
            return None

    def _fetch_url_text_sync(self, url: str, referer: Optional[str]) -> tuple[int, str, Optional[str]]:
        """
        Blocking requests fetch used by async extract_text_from_url via asyncio.to_thread.
        Returns: (status_code, body_text, error_message_if_any)
        """
        domain = urlparse(url).netloc
        self._cooldown_before_request(domain)
        self._rate_limit_check()
        headers = self._build_request_headers(url=url, referer=referer)
        try:
            resp = self.session.get(url, timeout=30, headers=headers)
            body_text = ""
            try:
                body_text = resp.text or ""
            except Exception:
                body_text = ""
            try:
                resp.raise_for_status()
            except Exception as e:
                return int(getattr(resp, "status_code", 0) or 0), body_text, str(e)
            return int(getattr(resp, "status_code", 0) or 0), body_text, None
        except Exception as e:
            return 0, "", str(e)
    
    def _get_file_path_for_url(
        self,
        url: str,
        article_date: Optional[datetime] = None,
        *,
        extension: str = ".json",
    ) -> Path:
        """
        Get the expected file path for a URL (for deduplication check).
        
        Args:
            url: Article URL
            article_date: Article date (defaults to current date)
            extension: File extension to use (default: .json)
            
        Returns:
            Expected file path
        """
        url_hash = hashlib.sha256(url.encode('utf-8')).hexdigest()
        
        if article_date is None:
            article_date = datetime.now()
        
        ext = str(extension or "").strip() or ".json"
        if not ext.startswith("."):
            ext = "." + ext

        date_folder = self.data_dir / str(article_date.year) / f"{article_date.month:02d}" / f"{article_date.day:02d}"
        return date_folder / f"{url_hash}{ext}"
    
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
        file_path = self._get_file_path_for_url(url, extension=".json")
        if file_path.exists():
            # Add to index for future lookups
            self.index[url_hash] = {
                "file_path": str(file_path),
                "scraped_at": datetime.now().isoformat()
            }
            self._save_index()
            return True
        
        return False

    def save_article_json(
        self,
        url: str,
        article_json: Dict[str, Any],
        article_date: Optional[datetime] = None,
    ) -> Optional[Path]:
        """
        Save normalized article as JSON-only and update index.

        The persisted JSON will contain exactly:
          - title
          - summary
          - raw_article
        """
        if not isinstance(article_json, dict):
            return None

        if not article_json["title"] and not article_json["summary"] and not article_json["raw_article"]:
            return None
        
        article_json["url"] = url

        file_path = self._get_file_path_for_url(url, article_date, extension=".json")
        date_folder = file_path.parent
        date_folder.mkdir(parents=True, exist_ok=True)

        try:
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(article_json, f, ensure_ascii=False, indent=2)

            url_hash = hashlib.sha256(url.encode("utf-8")).hexdigest()
            self.index[url_hash] = {
                "file_path": str(file_path),
                "scraped_at": datetime.now().isoformat(),
            }
            self._save_index()

            self.logger.info(f"Saved article JSON to: {file_path}")
            return file_path
        except Exception as e:
            self.logger.error(f"Error saving article JSON to {file_path}: {e}")
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
            # Background monitor: periodically re-check start_url and scrape any new URLs
            "auto_monitor": False,
            "check_interval_hours": 2,
            # Maximum number of *new* article URLs to process per scrape cycle (0 = unlimited)
            "max_articles_per_cycle": 0,
            "max_retrieval_depth": 1,
            # Playwright fallback (disabled by default)
            "playwright": {
                "playwright_enabled": False,
                "playwright_fallback_on_block": True,
                "playwright_fallback_on_short_content": True,
                "playwright_timeout_seconds": 35,
                "playwright_wait_selector": "article",
                "playwright_user_agent": "",
                "playwright_headless": True,
                "playwright_max_concurrent_pages": 1,
                "playwright_persist_storage": False,
            },
            "xpath": None
        }
        
        # Merge with defaults
        merged_config = {**defaults, **scraper_config}
        # Deep-merge playwright block
        pw_default = defaults.get("playwright") if isinstance(defaults.get("playwright"), dict) else {}
        pw_override = scraper_config.get("playwright") if isinstance(scraper_config.get("playwright"), dict) else {}
        merged_config["playwright"] = {**(pw_default or {}), **(pw_override or {})}
        
        return merged_config
    
    def __init__(
        self,
        scraper_core: NewsScraperCore,
        analyzer_endpoint: str = "http://localhost:3501/get_financial_news",
        llm_config: Optional[Dict[str, Any]] = None,
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

        if not isinstance(llm_config, dict) or not str(llm_config.get("model") or "").strip():
            raise ValueError("Missing required config: custom.llm_config.model must be set for news_scraper normalization")
        self.normalizer = NewsScraperLLMClient(llm_config=llm_config, logger=logger)

        # Prevent concurrent runs between manual /start and background monitor.
        self._scrape_lock = threading.Lock()

        # Analyzer posting queue (async workers; created when an event loop is available)
        self._analyzer_queue: Optional[asyncio.Queue] = None
        self._analyzer_worker_tasks: List[asyncio.Task] = []
        self._analyzer_workers_started = False
        # The event loop that owns the analyzer queue/tasks (important when monitor uses threads/loops).
        self._analyzer_loop: Optional[asyncio.AbstractEventLoop] = None
        self.analyzer_enqueued = 0
        self.analyzer_sent_ok = 0
        self.analyzer_sent_failed = 0

        # Background monitor configuration
        scfg = self.scraper_core.scraper_config or {}
        self._monitor_enabled = bool(scfg.get("auto_monitor", False))
        try:
            self._check_interval_hours = float(scfg.get("check_interval_hours", 2) or 2)
        except Exception:
            self._check_interval_hours = 2.0
        self._check_interval_seconds = max(60.0, self._check_interval_hours * 3600.0)
        self._monitor_stop = threading.Event()
        self._monitor_thread: Optional[threading.Thread] = None
        
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

            await self._ensure_analyzer_workers_started()
            with self._scrape_lock:
                return await self._scrape_direct(start_url, max_depth)
            
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error in scrape: {e}")
            return {
                "status": "error",
                "error": str(e)
            }

    async def _ensure_analyzer_workers_started(self):
        """
        Create analyzer queue and start background worker tasks on the current event loop.
        """
        loop = asyncio.get_running_loop()

        # If we already started workers on *this* loop and they are still alive, do nothing.
        if self._analyzer_workers_started and self._analyzer_loop is loop:
            if self._analyzer_worker_tasks and not any(t.done() for t in self._analyzer_worker_tasks):
                return

        # If workers were started on a different (possibly closed) loop, tear down best-effort and recreate.
        old_loop = self._analyzer_loop
        old_tasks = list(self._analyzer_worker_tasks or [])
        if old_tasks:
            # Try to cancel tasks on their owning loop (if it is still running).
            if old_loop is not None and old_loop is not loop and not old_loop.is_closed():
                for t in old_tasks:
                    try:
                        old_loop.call_soon_threadsafe(t.cancel)
                    except Exception:
                        pass
            else:
                for t in old_tasks:
                    try:
                        t.cancel()
                    except Exception:
                        pass

            # Only await tasks that belong to the current loop (cross-loop awaiting will error).
            tasks_to_await: List[asyncio.Task] = []
            for t in old_tasks:
                try:
                    if getattr(t, "get_loop", None) and t.get_loop() is loop:
                        tasks_to_await.append(t)
                except Exception:
                    pass
            if tasks_to_await:
                try:
                    await asyncio.gather(*tasks_to_await, return_exceptions=True)
                except Exception:
                    pass

        # Reset state before recreating.
        self._analyzer_queue = None
        self._analyzer_worker_tasks = []
        self._analyzer_workers_started = False
        self._analyzer_loop = None

        scfg = self.scraper_core.scraper_config or {}
        try:
            maxsize = int(scfg.get("analyzer_queue_maxsize", 500) or 500)
        except Exception:
            maxsize = 500
        maxsize = max(1, maxsize)

        try:
            worker_count = int(scfg.get("analyzer_worker_count", 2) or 2)
        except Exception:
            worker_count = 2
        worker_count = max(1, min(10, worker_count))

        self._analyzer_queue = asyncio.Queue(maxsize=maxsize)
        self._analyzer_worker_tasks = [loop.create_task(self._analyzer_worker_loop(i)) for i in range(worker_count)]
        self._analyzer_workers_started = True
        self._analyzer_loop = loop

        if self.logger:
            self.logger.info(f"Analyzer queue started: workers={worker_count}, maxsize={maxsize}")

    async def _enqueue_analyzer_send(self, *, file_path: str, url: str, quality_score: int):
        """
        Enqueue a {file_path, url} item for background analyzer posting.
        """
        await self._ensure_analyzer_workers_started()
        assert self._analyzer_queue is not None
        await self._analyzer_queue.put({"file_path": str(file_path), "url": str(url), "quality_score": quality_score})
        self.analyzer_enqueued += 1

    async def _analyzer_worker_loop(self, worker_id: int):
        """
        Background worker: POST saved file path to analyzer without blocking scrape loop.
        """
        scfg = self.scraper_core.scraper_config or {}
        try:
            retry_count = int(scfg.get("analyzer_send_retry_count", 2) or 2)
        except Exception:
            retry_count = 2
        retry_count = max(0, min(10, retry_count))

        try:
            backoff = float(scfg.get("analyzer_send_retry_backoff_seconds", 2.0) or 2.0)
        except Exception:
            backoff = 2.0
        backoff = max(0.0, min(60.0, backoff))

        assert self._analyzer_queue is not None
        q = self._analyzer_queue

        while True:
            item = await q.get()
            try:
                file_path = str((item or {}).get("file_path") or "")
                url = str((item or {}).get("url") or "")
                quality_score = int((item or {}).get("quality_score") or 0)
                self.logger.debug(f"Dequeued item to analyzer: {file_path}, {url}, {quality_score}")
                ok = False
                for attempt in range(retry_count + 1):
                    ok = await asyncio.to_thread(self._send_to_analyzer, file_path, url, quality_score)
                    if ok:
                        break
                    if backoff > 0 and attempt < retry_count:
                        await asyncio.sleep(backoff * (2 ** attempt))

                if ok:
                    self.analyzer_sent_ok += 1
                else:
                    self.analyzer_sent_failed += 1
            except Exception as e:
                self.analyzer_sent_failed += 1
                if self.logger:
                    self.logger.warning(f"Analyzer worker {worker_id} failed to send: {e}")
            finally:
                try:
                    q.task_done()
                except Exception:
                    pass

    def start_monitor(self):
        """
        Start background monitoring loop if enabled.

        When enabled, the agent will re-check start_url every `check_interval_hours` and
        scrape any new URLs, then hibernate (sleep) again.
        """
        if not self._monitor_enabled:
            return
        if self._monitor_thread is not None and self._monitor_thread.is_alive():
            return

        self._monitor_stop.clear()

        t = threading.Thread(target=self._monitor_loop, name="news_scraper_monitor", daemon=True)
        self._monitor_thread = t
        t.start()
        if self.logger:
            next_run = datetime.now(timezone.utc) + timedelta(seconds=self._check_interval_seconds)
            self.logger.info(
                f"Auto-monitor enabled: interval_hours={self._check_interval_hours} "
                f"(next_run_utc={next_run.isoformat(timespec='seconds')})"
            )

    def stop_monitor(self):
        self._monitor_stop.set()

    def _monitor_loop(self):
        """
        Background loop: every interval, run a scrape cycle to pick up any new URLs.
        """
        # IMPORTANT: do not call asyncio.run() per cycle. That creates and closes a new event loop each time,
        # which breaks long-lived async components (Playwright + analyzer worker tasks) that outlive a cycle.
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        cycle = 0
        try:
            while not self._monitor_stop.is_set():
                # Sleep first to avoid immediate duplicate scrape on container start unless user triggers /start.
                if self.logger:
                    next_run = datetime.now(timezone.utc) + timedelta(seconds=self._check_interval_seconds)
                    self.logger.info(
                        f"Auto-monitor sleeping: next_run_utc={next_run.isoformat(timespec='seconds')} "
                        f"in={self._check_interval_seconds:.0f}s"
                    )
                if self._monitor_stop.wait(self._check_interval_seconds):
                    break
                cycle += 1
                if self.logger:
                    self.logger.info(f"Auto-monitor woke up: starting cycle={cycle}")

                scfg = self.scraper_core.scraper_config or {}
                start_url = str(scfg.get("start_url") or "").strip()
                max_depth = int(scfg.get("max_retrieval_depth", 1) or 1)
                if not start_url:
                    continue

                # Avoid overlapping with manual scrape; skip this cycle if busy.
                if not self._scrape_lock.acquire(blocking=False):
                    if self.logger:
                        self.logger.info(f"Auto-monitor cycle={cycle} skipped (scrape already running)")
                    continue
                try:
                    try:
                        # Run one async scrape cycle on the persistent loop for this monitor thread
                        loop.run_until_complete(self._scrape_direct(start_url, max_depth))
                    except Exception as e:
                        if self.logger:
                            self.logger.warning(f"Auto-monitor scrape failed: {e}")
                finally:
                    try:
                        self._scrape_lock.release()
                    except Exception:
                        pass
        finally:
            # Best-effort: cancel analyzer workers if they were started on this loop.
            try:
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
    
    def _send_to_analyzer(self, file_path: str, url: str, quality_score: int) -> bool:
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
                "metadata": {"quality_score": quality_score}
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

        # Snapshot Playwright counters at cycle start (per-cycle metrics)
        pw_attempted_0 = int(getattr(self.scraper_core, "_pw_fallback_attempted", 0) or 0)
        pw_succeeded_0 = int(getattr(self.scraper_core, "_pw_fallback_succeeded", 0) or 0)
        
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
        analyzer_enqueued_this_cycle = 0

        # Cap per cycle: number of *new* article URLs processed (0 = unlimited)
        max_per_cycle = 0
        try:
            max_per_cycle = int(self.scraper_core.scraper_config.get("max_articles_per_cycle", 0) or 0)
        except Exception:
            max_per_cycle = 0
        max_per_cycle = max(0, max_per_cycle)
        processed_new = 0
        
        for url in article_urls:
            if max_per_cycle > 0 and processed_new >= max_per_cycle:
                if self.logger:
                    self.logger.info(f"Reached max_articles_per_cycle={max_per_cycle}; stopping this cycle early")
                break

            # Check if already scraped (deduplication)
            if self.scraper_core.is_url_scraped(url):
                if self.logger:
                    self.logger.debug(f"Skipping already scraped: {url}")
                articles_skipped += 1
                continue

            processed_new += 1
            
            # Extract text
            raw_text = await self.scraper_core.extract_text_from_url(url)

            if raw_text:
                # Normalize using LLM and save JSON-only
                normalized = await self.normalizer.normalize_article(url=url, extracted_text=raw_text)
                saved_path = self.scraper_core.save_article_json(url, normalized)
                quality_score = normalized.get("quality_score", 0)
                if saved_path:
                    articles_scraped += 1
                    saved_files.append(str(saved_path))
                    
                    # Enqueue analyzer send (handled by background workers)
                    if quality_score > 0 and quality_score <= 2:
                        self.logger.info(f"Low quality article [{str(saved_path)}] will be skipped from analyzer")
                        continue
                    await self._enqueue_analyzer_send(file_path=str(saved_path), url=url, quality_score=quality_score)
                    analyzer_enqueued_this_cycle += 1
                else:
                    articles_failed += 1
            else:
                articles_failed += 1
        
        # Save index after scraping session
        self.scraper_core.save_index()
        
        result = {
            "status": "success",
            "articles_found": len(article_urls),
            "max_articles_per_cycle": max_per_cycle,
            "articles_processed_this_cycle": processed_new,
            "articles_scraped": articles_scraped,
            "articles_skipped": articles_skipped,
            "articles_failed": articles_failed,
            "sent_to_analyzer": sent_to_analyzer,
            "analyzer_enqueued_this_cycle": analyzer_enqueued_this_cycle,
            "analyzer_queue_depth": int(self._analyzer_queue.qsize()) if self._analyzer_queue is not None else 0,
            "playwright_fallback_attempted": int(getattr(self.scraper_core, "_pw_fallback_attempted", 0) or 0) - pw_attempted_0,
            "playwright_fallback_succeeded": int(getattr(self.scraper_core, "_pw_fallback_succeeded", 0) or 0) - pw_succeeded_0,
        }
        
        if self.logger:
            self.logger.info(
                f"Scraping complete: {articles_scraped} scraped, "
                f"{articles_skipped} skipped, {articles_failed} failed, "
                f"{analyzer_enqueued_this_cycle} enqueued to analyzer"
            )
        
        return result

