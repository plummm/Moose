"""News Scraper Agent.

This agent scrapes news from a configured URL, extracts text content from article URLs,
saves them to organized folders with SHA256-hashed filenames, and summarizes them using LLM.
"""

import os, sys
from pathlib import Path
from typing import Dict, Any

from moose.framework import BaseAgent

# Import local modules
try:
    from .scraper import NewsScraperCore, NewsScraperService
    from .summarizer import NewsSummarizer
    from .workflow import create_workflow, LANGGRAPH_AVAILABLE
except ImportError:
    # Fallback for direct execution
    from moose.agents.news_scraper.scraper import NewsScraperCore, NewsScraperService
    from moose.agents.news_scraper.summarizer import NewsSummarizer
    from moose.agents.news_scraper.workflow import create_workflow, LANGGRAPH_AVAILABLE


class NewsScraper(BaseAgent):
    """
    Generic news scraper agent.
    
    Scrapes news from a configured URL, extracts text content from article URLs,
    saves them to organized folders with SHA256-hashed filenames, and optionally
    summarizes them using LLM via LangGraph workflow.
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
        
        # Initialize summarizer (if LLM config is available)
        summarizer = None
        use_langgraph = self.config.get("use_langgraph", False)
        
        llm_config = self.config.get("llm_config", {})
        if llm_config:
            try:
                model = llm_config.get("model", "gpt-4")
                temperature = llm_config.get("temperature", 0.7)
                summarizer = NewsSummarizer(
                    model=model,
                    temperature=temperature,
                    logger=self.logger,
                    **llm_config.get("kwargs", {})
                )
                self.logger.info(f"Initialized summarizer with model: {model}")
            except Exception as e:
                self.logger.warning(f"Failed to initialize summarizer: {e}")
        
        # Initialize LangGraph workflow if enabled
        workflow_app = None
        if use_langgraph and LANGGRAPH_AVAILABLE and summarizer:
            try:
                workflow_app = create_workflow(
                    scraper_core=scraper_core,
                    summarizer=summarizer,
                    logger=self.logger
                )
                self.logger.info("Initialized LangGraph workflow")
            except Exception as e:
                self.logger.warning(f"Failed to initialize LangGraph workflow: {e}")
                use_langgraph = False
        elif use_langgraph and not LANGGRAPH_AVAILABLE:
            self.logger.warning("LangGraph not available, workflow disabled")
            use_langgraph = False
        
        # Initialize scraping service (coordinates all components)
        self.scraper_service = NewsScraperService(
            scraper_core=scraper_core,
            summarizer=summarizer,
            workflow_app=workflow_app,
            use_langgraph=use_langgraph,
            logger=self.logger
        )
        
        self.logger.info(f"Initialized scraper with start_url: {scraper_config.get('start_url')}")
        self.logger.info(f"LangGraph workflow: {'enabled' if use_langgraph else 'disabled'}")
    
    def scrape(self, input_data=None) -> Any:
        """
        Main scraping method (delegates to scraper service).
        
        Args:
            input_data: Can be:
                - Dict with optional keys: start_url, max_depth
                - String: treated as start_url override
                - Empty/None: uses config defaults
                
        Returns:
            Dict with scraping results (and summaries if LangGraph is enabled)
        """
        return self.scraper_service.scrape(input_data)
    
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
        return self.scrape(input_data)
