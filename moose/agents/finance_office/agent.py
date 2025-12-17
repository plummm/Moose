"""Financial Report Analyzer Agent.

This agent receives file paths from news_scraper agent and analyzes financial news
articles using LLM with LangGraph workflow.
"""

import os
import asyncio
import hashlib
import json
from pathlib import Path
from typing import Dict, Any

from moose.framework import BaseAgent
from moose.framework.llm_core import LLMClient

# Import local modules
try:
    from .assistant import FinanceOfficeAssistant
    from .investment_research_team.research_lead import ResearchLead
    from .investment_research_team.edgar_mcp_tools import EdgarAllMCPTools
    from .investment_research_team.fmp_mcp_tools import FMPAllMCPTools
    from .investment_research_team.mcp_tools import CombinedFinanceMCPTools
except ImportError:
    # Fallback for direct execution
    from moose.agents.finance_office.assistant import FinanceOfficeAssistant
    from moose.agents.finance_office.investment_research_team.research_lead import ResearchLead
    from moose.agents.finance_office.investment_research_team.edgar_mcp_tools import EdgarAllMCPTools
    from moose.agents.finance_office.investment_research_team.fmp_mcp_tools import FMPAllMCPTools
    from moose.agents.finance_office.investment_research_team.mcp_tools import CombinedFinanceMCPTools


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
        self.news_data_dir = Path(news_dir)
        self.news_data_dir.mkdir(parents=True, exist_ok=True)
        self.logger.info(f"News data directory: {self.news_data_dir}")
        
        # Initialize analyzer (if LLM config is available)
        custom_config = self.config.get("custom", {})
        analyzer = None
        
        # Initialize EdgarAllMCPTools if edgar_config is enabled
        sec_data_tools = None
        edgar_tools = None
        fmp_tools = None
        edgar_config = custom_config.get("edgar_config", {})
        if edgar_config.get("enabled", False):
            try:
                edgar_tools = EdgarAllMCPTools(
                    identity=edgar_config.get("identity", ""),
                    logger=self.logger,
                )
                self.logger.info("Initialized EdgarAllMCPTools")
            except Exception as e:
                self.logger.warning(f"Failed to initialize EdgarAllMCPTools: {e}")

        # Initialize FMPAllMCPTools if fmp_config is enabled
        fmp_config = custom_config.get("fmp_config", {})
        if fmp_config.get("enabled", False):
            try:
                fmp_tools = FMPAllMCPTools(
                    api_key=fmp_config.get("api_key"),
                    logger=self.logger,
                )
                self.logger.info("Initialized FMPAllMCPTools")
            except Exception as e:
                self.logger.warning(f"Failed to initialize FMPAllMCPTools: {e}")

        # Combine enabled providers (if any) so the LLM gets the full tool list
        if edgar_tools is not None or fmp_tools is not None:
            sec_data_tools = CombinedFinanceMCPTools(edgar=edgar_tools, fmp=fmp_tools, logger=self.logger)
            try:
                # Warm tool binding cache so we log accurate availability early
                all_tools = sec_data_tools.get_langchain_tools()
                self.logger.info(f"Initialized combined MCP tools provider with {len(all_tools)} tools")
            except Exception as e:
                self.logger.warning(f"Failed to build combined MCP tools provider tools list: {e}")
        
        llm_config = custom_config.get("llm_config", {})
        if llm_config:
            try:
                model = llm_config.get("model", "gpt-5")
                temperature = llm_config.get("temperature", 0.7)
                enable_multi_stage_reasoning = llm_config.get("enable_multi_stage_reasoning", True)
                max_tool_iterations = llm_config.get("max_tool_iterations", 20)
                analyzer = ResearchLead(
                    model=model,
                    temperature=temperature,
                    logger=self.logger,
                    sec_data_tools=sec_data_tools,
                    enable_multi_stage_reasoning=enable_multi_stage_reasoning,
                    max_tool_iterations=max_tool_iterations,
                    agent_name=self.name,
                    **llm_config.get("kwargs", {})
                )
                self.logger.info(f"Initialized analyzer with model: {model}")
                if enable_multi_stage_reasoning:
                    self.logger.info(f"Multi-stage reasoning enabled with max {max_tool_iterations} iterations")
            except Exception as e:
                self.logger.warning(f"Failed to initialize analyzer: {e}")
        
        # Store SEC tools provider for cleanup
        self.sec_data_tools = sec_data_tools
        
        # Store analyzer
        self.analyzer = analyzer
        # Department-level helper for ad-hoc tasks like news analysis
        self.assistant = FinanceOfficeAssistant(team_manager=analyzer, logger=self.logger) if analyzer else None
    
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
            "result": { ... analysis ... }
        }
        """
        try:
            file_path = data.get("file_path")
            if not file_path:
                return {
                    "status": "error",
                    "error": "file_path is required",
                }
            
            # Validate file exists
            path = Path(file_path)
            if not path.exists():
                return {
                    "status": "error",
                    "error": f"File not found: {file_path}",
                }

            if not self.analyzer:
                return {"status": "error", "error": "Analyzer is not initialized"}
            if not self.assistant:
                return {"status": "error", "error": "Assistant is not initialized"}

            url = str(data.get("url", "") or "").strip()
            metadata = data.get("metadata", {}) if isinstance(data.get("metadata"), dict) else {}

            # Cache by url sha256 (recursive search under NEWS_RESULT_DIR)
            if url:
                try:
                    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
                    existing = list(self.news_data_dir.rglob(f"{digest}.json"))
                    if existing:
                        existing.sort(key=lambda p: p.stat().st_mtime, reverse=True)
                        with open(existing[0], "r", encoding="utf-8") as f:
                            analysis = json.load(f)
                        if isinstance(analysis, dict) and "error" not in analysis:
                            return {"status": "success", "result": analysis, "cached": True}
                except Exception as e:
                    self.logger.debug(f"Cache lookup failed; continuing: {e}")

            # Synchronous analysis (news is an ad-hoc HTTP call).
            # News analysis is owned by the department-level assistant, not the Investment Research team.
            result = await self.assistant.analyze_news(url=url, file_path=path, metadata=metadata)
            if isinstance(result, dict) and "error" not in result:
                try:
                    self.assistant.save_news_analysis_result(result, self.news_data_dir)
                except Exception as save_error:
                    self.logger.warning(f"Failed to save analysis result: {save_error}")

            return {"status": "success", "result": result, "cached": False, "metadata": metadata}
                
        except Exception as e:
            self.logger.error(f"Error in get_financial_news endpoint: {e}")
            return {
                "status": "error",
                "error": str(e),
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
        # Queue-based background workflow is disabled; keep a stable endpoint.
        return {"status": "success", "stats": {"queue_enabled": False, "queue_size": 0}}

    async def run_task(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        HTTP endpoint handler for running a natural-language finance research task.

        Expected input:
        {
          "instruction": "Give me a report of Microsoft latest earnings report",
          "context": { ... optional ... }
        }
        """
        try:
            instruction = str(data.get("instruction") or "").strip()
            context = data.get("context") if isinstance(data.get("context"), dict) else {}
            if not instruction:
                return {"status": "error", "error": "instruction is required"}

            if not self.analyzer:
                return {"status": "error", "error": "Analyzer is not initialized"}

            # Department-head router (tool-less) selects team + goal; currently routes into investment_research_team.
            from .department_router import load_department_playbooks, route_department_task

            playbooks_path = Path(__file__).resolve().parent / "department_playbooks.yaml"
            dept_playbooks = load_department_playbooks(playbooks_path)

            dept_client = LLMClient(
                model=self.config.get("custom", {}).get("llm_config", {}).get("model", "gpt-5.2"),
                temperature=float(self.config.get("custom", {}).get("llm_config", {}).get("temperature", 0.7)),
                tools=[],
                enable_multi_stage_reasoning=False,
                agent_name=self.name,
                **(self.config.get("custom", {}).get("llm_config", {}).get("kwargs", {}) or {}),
            )

            decision = await route_department_task(
                llm_client=dept_client,
                dept_playbooks=dept_playbooks,
                instruction=instruction,
                context=context,
            )

            # For now we only implement investment_research_team.
            team_task = decision.team_tasks.get("investment_research_team", {}) if isinstance(decision.team_tasks, dict) else {}
            team_goal = str(team_task.get("goal") or instruction)
            team_inputs = team_task.get("inputs") if isinstance(team_task.get("inputs"), dict) else {"instruction": instruction, "context": context}

            # Official invocation: team manager routes into investment_research_team via run_task()
            context_text = str((team_inputs or {}).get("context_text") or "")
            team_resp = await self.analyzer.run_task(
                team_goal,
                context_text=context_text,
                metadata=team_inputs,
                task_goal=str((team_inputs or {}).get("task_goal") or ""),
            )
            if not isinstance(team_resp, dict):
                return {"status": "error", "error": "Invalid team response", "result": {}}

            if team_resp.get("status") != "success":
                return {
                    "status": "error",
                    "error": str(team_resp.get("error") or "Team error"),
                    "result": team_resp.get("result"),
                }

            return {"status": "success", "error": None, "result": team_resp.get("result")}
        except Exception as e:
            self.logger.error(f"Error in run_task endpoint: {e}")
            return {"status": "error", "error": str(e), "result": None}
    
    def process(self, input_data=None) -> Any:
        """
        Main processing method (for backward compatibility).
        
        This agent primarily works via HTTP endpoints, but this method
        can be used to get queue statistics.
        """
        return {
            "status": "success",
            "message": "Financial Report Analyzer is running",
            "stats": {"queue_enabled": False, "queue_size": 0}
        }

