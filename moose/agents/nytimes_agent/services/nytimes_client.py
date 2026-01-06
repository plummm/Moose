"""NYTimes API Client with rate limiting and error handling."""

import os
import time
import json
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False


class NYTimesAPIClient:
    """Base class for NYTimes API clients with rate limiting."""
    
    def __init__(self, api_key: str, logger=None, rate_limit: int = 50):
        """
        Initialize NYTimes API client.
        
        Args:
            api_key: NYTimes API key
            logger: Logger instance
            rate_limit: Requests per minute (default: 50, conservative limit)
        """
        if not REQUESTS_AVAILABLE:
            raise ImportError("requests library is required. Install with: pip install requests")
        
        self.api_key = api_key
        self.logger = logger
        self.base_url = "https://api.nytimes.com"
        self.rate_limit = rate_limit
        self.request_timestamps: List[float] = []
        
        if not self.api_key:
            raise ValueError("NYTimes API key is required")
    
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
            oldest_timestamp = min(self.request_timestamps)
            wait_time = 60.0 - (current_time - oldest_timestamp) + 0.1
            if wait_time > 0:
                if self.logger:
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
    
    def _request_json(
        self,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        timeout: int = 30,
        max_retries: int = 3,
    ) -> Dict[str, Any]:
        """
        Make HTTP request to NYTimes API with rate limiting and error handling.
        
        Args:
            path: API path (e.g., "svc/search/v2/articlesearch.json")
            params: Query parameters (API key will be added automatically)
            timeout: Request timeout in seconds
            max_retries: Maximum number of retries on 429 errors
            
        Returns:
            Parsed JSON response or error dict
        """
        self._rate_limit_check()
        
        # Add API key to params
        qp = dict(params or {})
        qp["api-key"] = self.api_key
        
        url = f"{self.base_url}/{path.lstrip('/')}"
        
        for attempt in range(max_retries):
            try:
                response = requests.get(url, params=qp, timeout=timeout)
                
                # Handle rate limiting (429)
                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", 60))
                    if attempt < max_retries - 1:
                        if self.logger:
                            self.logger.warning(f"Rate limit exceeded (429), waiting {retry_after} seconds (attempt {attempt + 1}/{max_retries})")
                        time.sleep(retry_after)
                        continue
                    else:
                        return {
                            "error": "Rate limit exceeded",
                            "status_code": 429,
                            "retry_after": retry_after
                        }
                
                response.raise_for_status()
                return response.json()
                
            except requests.exceptions.HTTPError as e:
                if e.response.status_code == 401:
                    error_msg = "Unauthorized - Invalid API key"
                elif e.response.status_code == 400:
                    error_msg = f"Bad request: {e.response.text[:200]}"
                else:
                    error_msg = f"HTTP {e.response.status_code}: {e.response.text[:200]}"
                
                if self.logger:
                    self.logger.error(f"NYTimes API error: {error_msg}")
                
                return {
                    "error": error_msg,
                    "status_code": e.response.status_code if hasattr(e, 'response') else None
                }
                
            except requests.exceptions.Timeout:
                if self.logger:
                    self.logger.warning(f"NYTimes API timeout for {url}")
                return {"error": "Request timeout", "url": url}
                
            except requests.exceptions.RequestException as e:
                if self.logger:
                    self.logger.error(f"NYTimes API request failed: {e}")
                return {"error": str(e), "url": url}
        
        return {"error": "Max retries exceeded"}


class ArticleSearchClient(NYTimesAPIClient):
    """Client for Article Search API."""
    
    def search_articles(
        self,
        query: Optional[str] = None,
        begin_date: Optional[str] = None,
        end_date: Optional[str] = None,
        page: int = 0,
        sort: str = "best",
        filter_query: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Search articles using Article Search API.
        
        Args:
            query: Search query (searches headline, byline, body)
            begin_date: Start date in YYYYMMDD format
            end_date: End date in YYYYMMDD format
            page: Page number (0-100)
            sort: Sort order ("best", "newest", "oldest", "relevance")
            filter_query: Lucene filter query
            
        Returns:
            API response dict
        """
        params: Dict[str, Any] = {
            "page": page,
            "sort": sort,
        }
        
        if query:
            params["q"] = query
        if begin_date:
            params["begin_date"] = begin_date
        if end_date:
            params["end_date"] = end_date
        if filter_query:
            params["fq"] = filter_query
        
        return self._request_json("svc/search/v2/articlesearch.json", params=params)


class NewswireClient(NYTimesAPIClient):
    """Client for Times Newswire API."""
    
    def get_content(
        self,
        source: str = "all",
        section: str = "all",
        limit: int = 20,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """
        Get content from Times Newswire API.
        
        Args:
            source: "all", "nyt", or "inyt"
            section: Section name (converted to lowercase automatically)
            limit: Number of results (20-500, increments of 20)
            offset: Starting offset (0-500, increments of 20)
            
        Returns:
            API response dict
        """
        # Convert section to lowercase for Newswire API
        section_lower = section.lower() if section else "all"
        
        path = f"svc/news/v3/content/{source}/{section_lower}.json"
        params = {
            "limit": limit,
            "offset": offset,
        }
        
        return self._request_json(path, params=params)
    
    def get_section_list(self) -> Dict[str, Any]:
        """
        Get list of available sections.
        
        Returns:
            API response dict with section list
        """
        return self._request_json("svc/news/v3/content/section-list.json")


class MostPopularClient(NYTimesAPIClient):
    """Client for Most Popular API."""
    
    def get_most_viewed(self, period: int = 1) -> Dict[str, Any]:
        """Get most viewed articles."""
        path = f"svc/mostpopular/v2/viewed/{period}.json"
        return self._request_json(path)
    
    def get_most_emailed(self, period: int = 1) -> Dict[str, Any]:
        """Get most emailed articles."""
        path = f"svc/mostpopular/v2/emailed/{period}.json"
        return self._request_json(path)
    
    def get_most_shared(self, period: int = 1, share_type: str = "facebook") -> Dict[str, Any]:
        """Get most shared articles."""
        path = f"svc/mostpopular/v2/shared/{period}/{share_type}.json"
        return self._request_json(path)


class ArchiveClient(NYTimesAPIClient):
    """Client for Archive API."""
    
    def get_archive(self, year: int, month: int) -> Dict[str, Any]:
        """
        Get archive articles for a specific month.
        
        Args:
            year: Year (1851-2019)
            month: Month (1-12)
            
        Returns:
            API response dict (can be very large)
        """
        path = f"svc/archive/v1/{year}/{month}.json"
        return self._request_json(path, timeout=60)  # Longer timeout for large responses


class NYTimesClient:
    """Unified NYTimes API client."""
    
    def __init__(self, api_key: str, logger=None, rate_limit: int = 50):
        """
        Initialize unified NYTimes client.
        
        Args:
            api_key: NYTimes API key
            logger: Logger instance
            rate_limit: Requests per minute
        """
        self.article_search = ArticleSearchClient(api_key, logger, rate_limit)
        self.newswire = NewswireClient(api_key, logger, rate_limit)
        self.most_popular = MostPopularClient(api_key, logger, rate_limit)
        self.archive = ArchiveClient(api_key, logger, rate_limit)
        self.logger = logger

