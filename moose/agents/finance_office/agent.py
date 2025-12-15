"""Financial Report Analyzer Agent.

This agent receives file paths from news_scraper agent and analyzes financial news
articles using LLM with LangGraph workflow.
"""

import os
import asyncio
from pathlib import Path
from typing import Dict, Any

from moose.framework import BaseAgent

# Import local modules
try:
    from .finance_analysis_team.finance_researcher import FinanceResearcher
    from .finance_analysis_team.edgar_mcp_tools import EdgarAllMCPTools
    from .queue_manager import FilePathQueue
    from .workflow import create_workflow, LANGGRAPH_AVAILABLE
except ImportError:
    # Fallback for direct execution
    from moose.agents.finance_office.finance_analysis_team.finance_researcher import FinanceResearcher
    from moose.agents.finance_office.finance_analysis_team.edgar_mcp_tools import EdgarAllMCPTools
    from moose.agents.finance_office.queue_manager import FilePathQueue
    from moose.agents.finance_office.workflow import create_workflow, LANGGRAPH_AVAILABLE


class FinancialReportAnalyzer(BaseAgent):
    """
    Financial report analyzer agent.
    
    Receives file paths from news_scraper agent via HTTP endpoint,
    maintains a queue, and analyzes articles using LLM with LangGraph workflow.
    """
    
    name = "finance_office"
    description = "Analyzes financial news articles scraped by news_scraper agent"
    
    def __init__(self, config_path=None, debug=False):
        """Initialize the financial report analyzer."""
        super().__init__(config_path, debug=debug)
        
        # Initialize data directory (same as news_scraper)
        data_dir = os.getenv("SCRAPER_DATA_DIR", "/data/scraper/finviz.com")
        data_dir_path = Path(data_dir)
        data_dir_path.mkdir(parents=True, exist_ok=True)
        self.logger.info(f"Data directory: {data_dir_path}")
        
        # Initialize news data directory for saving analysis results
        news_dir = os.getenv("NEWS_RESULT_DIR", "/data/news")
        news_data_dir = Path(news_dir)
        news_data_dir.mkdir(parents=True, exist_ok=True)
        self.logger.info(f"News data directory: {news_data_dir}")
        
        # Initialize queue manager
        self.queue_manager = FilePathQueue(logger=self.logger)
        self.logger.info("Initialized file path queue")
        
        # Initialize analyzer (if LLM config is available)
        custom_config = self.config.get("custom", {})
        analyzer = None
        use_langgraph = custom_config.get("use_langgraph", True)
        
        # Initialize EdgarAllMCPTools if edgar_config is enabled
        sec_data_tools = None
        edgar_config = custom_config.get("edgar_config", {})
        if edgar_config.get("enabled", False):
            try:
                sec_data_tools = EdgarAllMCPTools(
                    identity=edgar_config.get("identity", ""),
                    logger=self.logger,
                )
                self.logger.info("Initialized EdgarAllMCPTools")
            except Exception as e:
                self.logger.warning(f"Failed to initialize EdgarAllMCPTools: {e}")
        
        llm_config = custom_config.get("llm_config", {})
        if llm_config:
            try:
                model = llm_config.get("model", "gpt-5")
                temperature = llm_config.get("temperature", 0.7)
                analyzer = FinanceResearcher(
                    model=model,
                    temperature=temperature,
                    logger=self.logger,
                    sec_data_tools=sec_data_tools,
                    **llm_config.get("kwargs", {})
                )
                self.logger.info(f"Initialized analyzer with model: {model}")
            except Exception as e:
                self.logger.warning(f"Failed to initialize analyzer: {e}")
        
        # Store SEC tools provider for cleanup
        self.sec_data_tools = sec_data_tools
        
        # Store analyzer for workflow
        self.analyzer = analyzer
        
        # Initialize LangGraph workflow if enabled
        self.workflow_app = None
        self.workflow_task = None
        if use_langgraph and LANGGRAPH_AVAILABLE and analyzer:
            try:
                max_concurrent = custom_config.get("max_concurrent_analyses", 20)
                self.workflow_app = create_workflow(
                    queue_manager=self.queue_manager,
                    analyzer=analyzer,
                    logger=self.logger,
                    max_concurrent_analyses=max_concurrent,
                    news_data_dir=news_data_dir
                )
                self.logger.info("Initialized LangGraph workflow")
                
                self._workflow_started = False
            except Exception as e:
                self.logger.warning(f"Failed to initialize LangGraph workflow: {e}")
                use_langgraph = False
        elif use_langgraph and not LANGGRAPH_AVAILABLE:
            self.logger.warning("LangGraph not available, workflow disabled")
            use_langgraph = False
        
        self.logger.info(f"LangGraph workflow: {'enabled' if use_langgraph else 'disabled'}")
    
    def run_http_server(self, port=None, host="0.0.0.0"):
        """Override to start workflow task when HTTP server starts."""
        # Start workflow task in background
        if self.workflow_app and not self._workflow_started:
            async def run_workflow():
                """Run workflow continuously."""
                self.logger.debug("Running workflow")
                try:
                    initial_state = {
                        "current_item": None,
                        "analyses": [],
                        "analyses_completed": 0,
                        "analyses_failed": 0,
                        "status": "processing",
                        "error": None,
                        "queue_manager": self.queue_manager,
                        "analyzer": self.analyzer,
                        "logger": self.logger
                    }
                    
                    # Run workflow continuously
                    while self.running:
                        try:
                            self.logger.debug("Invoking workflow")
                            # Check if workflow app has async invoke method
                            if hasattr(self.workflow_app, 'ainvoke'):
                                # Invoke workflow - it will process one item and route back
                                result = await self.workflow_app.ainvoke(initial_state)
                                # Update initial_state with result for next iteration
                                self.logger.debug("Workflow finished: {}".format(result))
                                initial_state = result
                            else:
                                # Fallback to sync invoke in async context
                                loop = asyncio.get_event_loop()
                                result = await loop.run_in_executor(
                                    None,
                                    lambda: self.workflow_app.invoke(initial_state)
                                )
                                initial_state = result
                            
                            # Small delay to prevent tight loop
                            await asyncio.sleep(0.1)
                        except Exception as e:
                            self.logger.error(f"Error in workflow execution: {e}")
                            await asyncio.sleep(1)
                except Exception as e:
                    self.logger.error(f"Workflow task error: {e}")
            
            # Start workflow in background thread with event loop
            import threading
            def start_workflow_loop():
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                self._workflow_loop = loop
                loop.run_until_complete(run_workflow())
            
            workflow_thread = threading.Thread(target=start_workflow_loop, daemon=True)
            workflow_thread.start()
            self._workflow_started = True
            self.logger.info("Started workflow background task")
        
        # Call parent's run_http_server
        super().run_http_server(port=port, host=host)
    
    async def get_financial_news(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        HTTP endpoint handler for receiving file paths from news_scraper.
        
        Expected input:
        {
            "file_path": str,  # Path to scraped article file
            "url": str,        # Original URL (optional)
            "metadata": dict   # Additional metadata (optional)
        }
        
        Returns:
        {
            "status": "success" | "error",
            "message": str,
            "queue_size": int
        }
        """
        try:
            file_path = data.get("file_path")
            if not file_path:
                return {
                    "status": "error",
                    "error": "file_path is required",
                    "queue_size": self.queue_manager.size()
                }
            
            # Validate file exists
            path = Path(file_path)
            if not path.exists():
                return {
                    "status": "error",
                    "error": f"File not found: {file_path}",
                    "queue_size": self.queue_manager.size()
                }
            
            # Enqueue the file path
            url = data.get("url", "")
            metadata = data.get("metadata", {})
            
            success = self.queue_manager.enqueue(
                file_path=str(path),
                url=url,
                metadata=metadata
            )
            
            if success:
                self.logger.info(f"Received file path for analysis: {file_path}")
                return {
                    "status": "success",
                    "message": f"File path enqueued: {file_path}",
                    "queue_size": self.queue_manager.size()
                }
            else:
                return {
                    "status": "error",
                    "error": "Failed to enqueue file path",
                    "queue_size": self.queue_manager.size()
                }
                
        except Exception as e:
            self.logger.error(f"Error in get_financial_news endpoint: {e}")
            return {
                "status": "error",
                "error": str(e),
                "queue_size": self.queue_manager.size()
            }
    
    async def get_queue_stats(self, data: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        HTTP endpoint handler for getting queue statistics.
        
        Returns:
        {
            "status": "success",
            "stats": {
                "total_received": int,
                "total_processed": int,
                "total_failed": int,
                "queue_size": int,
                "pending": int
            }
        }
        """
        return {
            "status": "success",
            "stats": self.queue_manager.get_stats()
        }
    
    def process(self, input_data=None) -> Any:
        """
        Main processing method (for backward compatibility).
        
        This agent primarily works via HTTP endpoints, but this method
        can be used to get queue statistics.
        """
        return {
            "status": "success",
            "message": "Financial Report Analyzer is running",
            "stats": self.queue_manager.get_stats()
        }

