"""Article processing from different NYTimes API sources."""

from typing import Dict, Any, Optional
from datetime import datetime
import re


class ArticleProcessor:
    """Processes articles from different NYTimes API sources into standardized format."""
    
    def __init__(self, logger=None):
        """
        Initialize article processor.
        
        Args:
            logger: Logger instance
        """
        self.logger = logger
    
    def _parse_date(self, date_str: str) -> Optional[datetime]:
        """
        Parse date string to datetime.
        
        Args:
            date_str: Date string in various formats
            
        Returns:
            Parsed datetime or None
        """
        if not date_str:
            return None
        
        # Try ISO 8601 format first (common in APIs)
        try:
            # Remove timezone info for parsing, then add back if needed
            if 'T' in date_str:
                # ISO format: 2025-12-30T10:02:10Z
                date_str_clean = date_str.replace('Z', '+00:00')
                return datetime.fromisoformat(date_str_clean.replace('Z', '+00:00'))
        except Exception:
            pass
        
        # Try other formats if needed
        for fmt in ['%Y-%m-%d', '%Y%m%d', '%Y-%m-%d %H:%M:%S']:
            try:
                return datetime.strptime(date_str, fmt)
            except Exception:
                continue
        
        return None
    
    def process_article_search_result(self, doc: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process Article Search API result.
        
        Args:
            doc: Article document from Article Search API response.docs
            
        Returns:
            Standardized article dict
        """
        headline = doc.get("headline", {})
        if isinstance(headline, dict):
            title = headline.get("main", "") or headline.get("print_headline", "")
        else:
            title = str(headline or "")
        
        byline = doc.get("byline", {})
        if isinstance(byline, dict):
            byline_text = byline.get("original", "")
        else:
            byline_text = str(byline or "")
        
        # Extract keywords
        keywords = []
        keywords_list = doc.get("keywords", [])
        if isinstance(keywords_list, list):
            for kw in keywords_list:
                if isinstance(kw, dict):
                    keywords.append({
                        "name": kw.get("name", ""),
                        "value": kw.get("value", ""),
                        "rank": kw.get("rank", 0),
                    })
        
        pub_date_str = doc.get("pub_date", "")
        pub_date = self._parse_date(pub_date_str)
        
        return {
            "url": doc.get("web_url", ""),
            "uri": doc.get("uri", ""),
            "title": title,
            "abstract": doc.get("snippet", ""),
            "published_date": pub_date_str,
            "published_datetime": pub_date.isoformat() if pub_date else None,
            "section": doc.get("section_name", ""),
            "subsection": doc.get("subsection_name", ""),
            "byline": byline_text,
            "keywords": keywords,
            "source": doc.get("source", "The New York Times"),
            "type_of_material": doc.get("type_of_material", ""),
            "word_count": doc.get("word_count", 0),
            "document_type": doc.get("document_type", ""),
            "desk": doc.get("news_desk", ""),
            "metadata": {
                "print_page": doc.get("print_page"),
                "print_section": doc.get("print_section"),
                "multimedia": doc.get("multimedia", []),
            }
        }
    
    def process_newswire_result(self, article: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process Newswire API result.
        
        Args:
            article: Article from Newswire API response.results
            
        Returns:
            Standardized article dict
        """
        # Newswire API uses 'title' directly (not nested in headline)
        title = article.get("title", "")
        
        pub_date_str = article.get("published_date", "")
        pub_date = self._parse_date(pub_date_str)
        
        # Extract facets as keywords
        keywords = []
        for facet_type in ["des_facet", "org_facet", "per_facet", "geo_facet"]:
            facets = article.get(facet_type, [])
            if isinstance(facets, list):
                for facet in facets:
                    if isinstance(facet, str):
                        keywords.append({"name": facet_type.replace("_facet", ""), "value": facet})
        
        return {
            "url": article.get("url", ""),
            "uri": article.get("uri", ""),
            "title": title,
            "abstract": article.get("abstract", ""),
            "published_date": pub_date_str,
            "published_datetime": pub_date.isoformat() if pub_date else None,
            "section": article.get("section", ""),
            "subsection": article.get("subsection", ""),
            "byline": article.get("byline", ""),
            "keywords": keywords,
            "source": article.get("source", "The New York Times"),
            "type_of_material": article.get("material_type_facet", ""),
            "word_count": 0,  # Newswire doesn't provide word_count
            "document_type": article.get("item_type", ""),
            "desk": "",  # Newswire doesn't provide desk
            "metadata": {
                "kicker": article.get("kicker", ""),
                "subheadline": article.get("subheadline", ""),
                "multimedia": article.get("multimedia", []),
                "related_urls": article.get("related_urls", []),
            }
        }
    
    def process_most_popular_result(self, article: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process Most Popular API result.
        
        Args:
            article: Article from Most Popular API response.results
            
        Returns:
            Standardized article dict
        """
        pub_date_str = article.get("published_date", "")
        pub_date = self._parse_date(pub_date_str)
        
        # Extract keywords from facets
        keywords = []
        for facet_type in ["des_facet", "org_facet", "per_facet", "geo_facet"]:
            facets = article.get(facet_type, [])
            if isinstance(facets, list):
                for facet in facets:
                    if isinstance(facet, str):
                        keywords.append({"name": facet_type.replace("_facet", ""), "value": facet})
        
        return {
            "url": article.get("url", ""),
            "uri": article.get("uri", ""),
            "title": article.get("title", ""),
            "abstract": article.get("abstract", ""),
            "published_date": pub_date_str,
            "published_datetime": pub_date.isoformat() if pub_date else None,
            "section": article.get("section", ""),
            "subsection": article.get("subsection", ""),
            "byline": article.get("byline", ""),
            "keywords": keywords,
            "source": article.get("source", "The New York Times"),
            "type_of_material": article.get("type", ""),
            "word_count": 0,  # Most Popular doesn't provide word_count
            "document_type": article.get("type", ""),
            "desk": "",  # Most Popular doesn't provide desk
            "metadata": {
                "media": article.get("media", []),
                "adx_keywords": article.get("adx_keywords", ""),
                "nytdsection": article.get("nytdsection", ""),
            }
        }
    
    def process_archive_result(self, doc: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process Archive API result (same format as Article Search).
        
        Args:
            doc: Article document from Archive API response.docs
            
        Returns:
            Standardized article dict
        """
        # Archive API uses same format as Article Search API
        return self.process_article_search_result(doc)
    
    def add_content_to_article(
        self,
        article: Dict[str, Any],
        content: str,
    ) -> Dict[str, Any]:
        """
        Add scraped content to article dict.
        
        Args:
            article: Article dict
            content: Scraped article content text
            
        Returns:
            Updated article dict with content field
        """
        article["content"] = content
        return article

