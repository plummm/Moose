"""News Scraper Agent.

This agent scrapes news from a configured URL, extracts text content from article URLs,
and saves them to organized folders with SHA256-hashed filenames.
The scraped articles are sent to the finance_office agent for analysis.
"""

import os, sys
from pathlib import Path
from typing import Dict, Any

from moose.framework import BaseAgent

# Import local modules
try:
    from .scraper import NewsScraperCore, NewsScraperService
except ImportError:
    # Fallback for direct execution
    from moose.agents.news_scraper.scraper import NewsScraperCore, NewsScraperService


class NewsScraper(BaseAgent):
    """
    Generic news scraper agent.
    
    Scrapes news from a configured URL, extracts text content from article URLs,
    saves them to organized folders with SHA256-hashed filenames.
    Sends scraped articles to finance_office agent for analysis.
    """
    
    name = "news_scraper"
    description = "Generic news scraper that extracts text content from web pages"
    
    def __init__(self, config_path=None, debug=False):
        """Initialize the news scraper."""
        super().__init__(config_path, debug=debug)
        
        # Load scraper configuration
        scraper_config = NewsScraperService.load_scraper_config(self.config)
        
        # Initialize data directory
        data_dir = os.getenv("SCRAPER_DATA_DIR", "/data/scraper/finviz.com")
        data_dir_path = Path(data_dir)
        data_dir_path.mkdir(parents=True, exist_ok=True)
        self.logger.info(f"Data directory: {data_dir_path}")
        
        # Initialize scraper core
        scraper_core = NewsScraperCore(
            data_dir=data_dir_path,
            scraper_config=scraper_config,
            logger=self.logger
        )
        
        # Get finance_office endpoint URL from config
        custom_config = self.config.get("custom", {})
        analyzer_config = custom_config.get("finance_office", {})
        analyzer_endpoint = analyzer_config.get("endpoint", "http://localhost:3501/get_financial_news")
        
        # Initialize scraping service (no summarizer or workflow)
        self.scraper_service = NewsScraperService(
            scraper_core=scraper_core,
            analyzer_endpoint=analyzer_endpoint,
            logger=self.logger
        )
        
        self.logger.info(f"Initialized scraper with start_url: {scraper_config.get('start_url')}")
        self.logger.info(f"Analyzer endpoint: {analyzer_endpoint}")
    
    async def scrape(self, input_data=None) -> Any:
        """
        Main scraping method (delegates to scraper service).
        
        Args:
            input_data: Can be:
                - Dict with optional keys: start_url, max_depth
                - String: treated as start_url override
                - Empty/None: uses config defaults
                
        Returns:
            Dict with scraping results
        """
        return await self.scraper_service.scrape(input_data)
    
    def process(self, input_data=None) -> Any:
        """
        Main processing method (backward compatibility).
        
        This method calls scrape() to maintain backward compatibility with existing code.
        
        Args:
            input_data: Can be:
                - Dict with optional keys: start_url, max_depth, date
                - String: treated as start_url override
                - Empty/None: uses config defaults
                
        Returns:
            Dict with scraping results
        """
        import asyncio
        return asyncio.run(self.scrape(input_data))
