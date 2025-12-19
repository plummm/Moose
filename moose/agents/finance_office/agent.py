"""Financial Report Analyzer Agent.

This agent receives file paths from news_scraper agent and analyzes financial news
articles using LLM with LangGraph workflow.
"""

import os
import asyncio
import hashlib
import json
import threading
import queue
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, List

from moose.framework import BaseAgent
from moose.framework.llm_core import LLMClient

# Import local modules
try:
    from assistant import FinanceOfficeAssistant
    from investment_research_team.research_lead import ResearchLead
    from investment_research_team.edgar_mcp_tools import EdgarAllMCPTools
    from investment_research_team.fmp_mcp_tools import FMPAllMCPTools
    from investment_research_team.mcp_tools import CombinedFinanceMCPTools
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
        
        llm_config = custom_config.get("llm_config")
        if isinstance(llm_config, dict):
            try:
                model = str(llm_config.get("model") or "").strip()
                if not model:
                    raise ValueError("Missing required config: custom.llm_config.model")
                temperature = llm_config.get("temperature", 0.7)
                enable_multi_stage_reasoning = llm_config.get("enable_multi_stage_reasoning", True)
                max_tool_iterations = llm_config.get("max_tool_iterations", 20)

                analyzer = ResearchLead(
                    model=model,
                    temperature=float(temperature),
                    logger=self.logger,
                    sec_data_tools=sec_data_tools,
                    enable_multi_stage_reasoning=enable_multi_stage_reasoning,
                    max_tool_iterations=max_tool_iterations,
                    agent_name=self.name,
                    custom_config=custom_config,
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
        self.assistant = (
            FinanceOfficeAssistant(
                team_manager=analyzer,
                logger=self.logger,
                custom_config=(self.config.get("custom") if isinstance(self.config.get("custom"), dict) else {}),
            )
            if analyzer
            else None
        )

        # Bounded queue + fixed worker pool for news analysis (avoid unbounded asyncio.create_task on Flask async views)
        custom_config = self.config.get("custom", {}) if isinstance(self.config.get("custom"), dict) else {}
        news_cfg = custom_config.get("news_analysis") if isinstance(custom_config.get("news_analysis"), dict) else {}

        max_workers = news_cfg.get("max_concurrent", 4)
        try:
            self._news_max_workers = max(1, int(max_workers or 0))
        except Exception:
            self._news_max_workers = 4

        max_queue = news_cfg.get("max_queue_size", 200)
        try:
            self._news_max_queue_size = max(1, int(max_queue or 0))
        except Exception:
            self._news_max_queue_size = 200

        self._news_queue: "queue.Queue[Dict[str, Any]]" = queue.Queue(maxsize=self._news_max_queue_size)
        self._news_worker_stop = threading.Event()
        self._news_workers: list[threading.Thread] = []
        self._news_total_received = 0
        self._news_total_queued = 0
        self._news_total_dropped = 0
        self._news_total_processed = 0
        self._news_total_failed = 0

        if self.assistant:
            self._start_news_workers()
            self.logger.info(
                f"News analysis worker pool: workers={self._news_max_workers}, queue_max={self._news_max_queue_size}"
            )

    def _start_news_workers(self) -> None:
        if self._news_workers:
            return

        def worker_main(worker_idx: int) -> None:
            import asyncio as _asyncio
            loop = _asyncio.new_event_loop()
            _asyncio.set_event_loop(loop)
            try:
                while not self._news_worker_stop.is_set() and not self.shutdown_requested:
                    try:
                        job = self._news_queue.get(timeout=0.5)
                    except queue.Empty:
                        continue
                    try:
                        if not self.assistant:
                            continue
                        loop.run_until_complete(
                            self.assistant.process_news(
                                url=str(job.get("url") or ""),
                                file_path=Path(job["file_path"]),
                                metadata=job.get("metadata") if isinstance(job.get("metadata"), dict) else {},
                                news_data_dir=self.news_data_dir,
                            )
                        )
                        self._news_total_processed += 1
                    except Exception as e:
                        self._news_total_failed += 1
                        self.logger.error(f"News worker {worker_idx} failed: {e}", exc_info=True)
                    finally:
                        try:
                            self._news_queue.task_done()
                        except Exception:
                            pass
            finally:
                try:
                    loop.close()
                except Exception:
                    pass

        for i in range(self._news_max_workers):
            t = threading.Thread(target=worker_main, args=(i,), name=f"news_worker_{i}", daemon=True)
            t.start()
            self._news_workers.append(t)

    # -------------------------------------------------------------------------
    # Cost tracking UI (reads existing llm.log* JSONL; finance_office-only view)
    # -------------------------------------------------------------------------
    def _get_project_logs_dir(self) -> Path:
        """
        Resolve the project log directory in a container-friendly way.

        Prefer env vars (so Docker mounts like /app/projects work) and fall back to repo-local ./projects.
        """
        project_id = os.getenv("MOOSE_PROJECT_ID") or "default"
        base_dir = os.getenv("MOOSE_PROJECTS_DIR")
        projects_base = Path(base_dir) if base_dir else (Path.cwd() / "projects")
        return projects_base / str(project_id) / "logs"

    def _normalize_date(self, date_str: Optional[str]) -> str:
        if not date_str:
            return datetime.now().strftime("%Y-%m-%d")
        try:
            datetime.strptime(str(date_str), "%Y-%m-%d")
            return str(date_str)
        except Exception:
            return datetime.now().strftime("%Y-%m-%d")

    def _iter_llm_log_files(self, logs_dir: Path) -> List[Path]:
        """
        Return llm log files to scan (llm.log, llm.log.1, llm.log.<n> ...).
        """
        try:
            files = sorted(logs_dir.glob("llm.log*"), key=lambda p: p.stat().st_mtime, reverse=True)
            return [p for p in files if p.is_file()]
        except Exception:
            return []

    def _aggregate_costs_from_llm_logs(self, *, date: str) -> Dict[str, Any]:
        """
        Aggregate costs/tokens from llm.log* JSON lines for this agent.

        We use llm.log because cost logs (llm_costs_YYYY-MM-DD.log) currently don't carry agent_name,
        and the user asked for finance_office-only stats.
        """
        logs_dir = self._get_project_logs_dir()
        files = self._iter_llm_log_files(logs_dir)

        totals = {
            "calls": 0,
            "cost_usd": 0.0,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
        }
        by_model: Dict[str, Dict[str, Any]] = {}

        agent_name = str(getattr(self, "name", "finance_office") or "finance_office")
        date_prefix = f"{date}"

        for fp in files:
            try:
                with open(fp, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            entry = json.loads(line)
                        except Exception:
                            continue
                        if not isinstance(entry, dict):
                            continue

                        ts = str(entry.get("timestamp") or "")
                        if not ts.startswith(date_prefix):
                            continue

                        if str(entry.get("direction") or "") != "response":
                            continue

                        meta = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
                        if str(meta.get("agent_name") or "") != agent_name:
                            continue

                        model = str(entry.get("model") or "").strip() or "unknown"
                        try:
                            cost = float(entry.get("cost") or 0.0)
                        except Exception:
                            cost = 0.0
                        usage = entry.get("usage") if isinstance(entry.get("usage"), dict) else {}
                        it = int(usage.get("input_tokens", 0) or 0)
                        ot = int(usage.get("output_tokens", 0) or 0)
                        tt = int(usage.get("total_tokens", it + ot) or (it + ot))

                        totals["calls"] += 1
                        totals["cost_usd"] += float(cost or 0.0)
                        totals["input_tokens"] += it
                        totals["output_tokens"] += ot
                        totals["total_tokens"] += tt

                        slot = by_model.get(model)
                        if slot is None:
                            slot = {
                                "model": model,
                                "calls": 0,
                                "cost_usd": 0.0,
                                "input_tokens": 0,
                                "output_tokens": 0,
                                "total_tokens": 0,
                            }
                            by_model[model] = slot
                        slot["calls"] += 1
                        slot["cost_usd"] += float(cost or 0.0)
                        slot["input_tokens"] += it
                        slot["output_tokens"] += ot
                        slot["total_tokens"] += tt
            except Exception as e:
                # Don't fail the endpoint due to logging issues
                try:
                    self.logger.debug(f"Failed to read llm log file {fp}: {e}")
                except Exception:
                    pass

        by_model_list = sorted(by_model.values(), key=lambda r: float(r.get("cost_usd", 0.0) or 0.0), reverse=True)

        return {
            "date": date,
            "agent_name": agent_name,
            "totals": {
                **totals,
                "cost_usd": round(float(totals["cost_usd"]), 6),
            },
            "by_model": [
                {**r, "cost_usd": round(float(r.get("cost_usd", 0.0) or 0.0), 6)} for r in by_model_list
            ],
            "log_dir": str(logs_dir),
            "log_files": [p.name for p in files],
        }

    def costs_json(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        GET /costs.json?date=YYYY-MM-DD
        """
        date = self._normalize_date(str(data.get("date") or "").strip() or None)
        return {"status": "success", "data": self._aggregate_costs_from_llm_logs(date=date)}

    def costs_page(self, data: Dict[str, Any]) -> Any:
        """
        GET /costs
        Simple HTML page that fetches /costs.json and renders totals + per-model breakdown.
        """
        from flask import Response  # type: ignore

        today = datetime.now().strftime("%Y-%m-%d")
        html = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8"/>
    <meta name="viewport" content="width=device-width, initial-scale=1"/>
    <title>finance_office — Cost Tracking</title>
    <style>
      body {{ font-family: system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif; margin: 16px; }}
      .row {{ display: flex; gap: 16px; flex-wrap: wrap; align-items: center; }}
      .card {{ border: 1px solid #ddd; border-radius: 10px; padding: 12px 14px; }}
      table {{ border-collapse: collapse; width: 100%; }}
      th, td {{ border-bottom: 1px solid #eee; padding: 8px 6px; text-align: left; }}
      th {{ font-weight: 600; }}
      code {{ background: #f6f6f6; padding: 2px 6px; border-radius: 6px; }}
      .muted {{ color: #666; }}
    </style>
  </head>
  <body>
    <h2>finance_office — Cost Tracking</h2>
    <div class="row card">
      <div>
        <label for="date">Date:</label>
        <input id="date" type="date" value="{today}"/>
      </div>
      <div class="muted">Data source: <code>llm.log*</code> filtered by <code>metadata.agent_name=finance_office</code></div>
    </div>

    <div id="status" class="muted" style="margin-top: 10px;"></div>

    <div class="card" style="margin-top: 12px;">
      <h3 style="margin: 0 0 10px 0;">Today’s totals</h3>
      <div id="totals"></div>
    </div>

    <div class="card" style="margin-top: 12px;">
      <h3 style="margin: 0 0 10px 0;">Breakdown by model</h3>
      <div id="byModel"></div>
    </div>

    <script>
      function fmtUSD(x) {{
        try {{ return '$' + Number(x || 0).toFixed(6); }} catch (e) {{ return '$0.000000'; }}
      }}
      function fmtInt(x) {{
        try {{ return String(parseInt(x || 0, 10)); }} catch (e) {{ return '0'; }}
      }}
      async function load() {{
        const date = document.getElementById('date').value || '{today}';
        const status = document.getElementById('status');
        status.textContent = 'Loading…';
        try {{
          const resp = await fetch('/costs.json?date=' + encodeURIComponent(date));
          const payload = await resp.json();
          if (!payload || payload.status !== 'success') {{
            status.textContent = 'Failed to load: ' + JSON.stringify(payload);
            return;
          }}
          const data = payload.data || {{}};
          const t = data.totals || {{}};
          status.textContent = `Log dir: ${data.log_dir || ''} | Files: ${(data.log_files || []).join(', ')}`;

          document.getElementById('totals').innerHTML = `
            <div class="row">
              <div><b>Calls</b>: ${fmtInt(t.calls)}</div>
              <div><b>Cost</b>: ${fmtUSD(t.cost_usd)}</div>
              <div><b>Input tokens</b>: ${fmtInt(t.input_tokens)}</div>
              <div><b>Output tokens</b>: ${fmtInt(t.output_tokens)}</div>
              <div><b>Total tokens</b>: ${fmtInt(t.total_tokens)}</div>
            </div>
          `;

          const rows = (data.by_model || []).map(r => `
            <tr>
              <td><code>${(r.model || 'unknown')}</code></td>
              <td>${fmtInt(r.calls)}</td>
              <td>${fmtUSD(r.cost_usd)}</td>
              <td>${fmtInt(r.input_tokens)}</td>
              <td>${fmtInt(r.output_tokens)}</td>
              <td>${fmtInt(r.total_tokens)}</td>
            </tr>
          `).join('');
          document.getElementById('byModel').innerHTML = `
            <table>
              <thead>
                <tr>
                  <th>Model</th>
                  <th>Calls</th>
                  <th>Cost</th>
                  <th>Input tokens</th>
                  <th>Output tokens</th>
                  <th>Total tokens</th>
                </tr>
              </thead>
              <tbody>
                ${rows || '<tr><td colspan="6" class="muted">No entries for this date.</td></tr>'}
              </tbody>
            </table>
          `;
        }} catch (e) {{
          status.textContent = 'Error: ' + String(e);
        }}
      }}
      document.getElementById('date').addEventListener('change', load);
      load();
    </script>
  </body>
</html>
"""
        # This HTML was originally written as an f-string; we keep the JS template-literals intact by
        # doing a simple placeholder replacement for the date and un-escaping doubled braces.
        html = html.replace("{today}", today).replace("{{", "{").replace("}}", "}")
        return Response(html, mimetype="text/html")
    
    def get_financial_news(self, data: Dict[str, Any]) -> Dict[str, Any]:
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
                            return {"status": "success"}
                except Exception as e:
                    self.logger.debug(f"Cache lookup failed; continuing: {e}")

            self._news_total_received += 1

            # Enqueue bounded job for worker pool
            try:
                self._news_queue.put_nowait(
                    {"url": url, "file_path": str(path), "metadata": metadata}
                )
                self._news_total_queued += 1
            except queue.Full:
                self._news_total_dropped += 1
                return {"status": "error", "error": "busy (news analysis queue full)"}, 429

            return {"status": "success", "queued": True}
                
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
        return {
            "status": "success",
            "stats": {
                "queue_enabled": True,
                "queue_size": int(self._news_queue.qsize()),
                "queue_max": int(self._news_max_queue_size),
                "workers": int(self._news_max_workers),
                "total_received": int(self._news_total_received),
                "total_queued": int(self._news_total_queued),
                "total_dropped": int(self._news_total_dropped),
                "total_processed": int(self._news_total_processed),
                "total_failed": int(self._news_total_failed),
            },
        }

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

            custom = self.config.get("custom", {}) if isinstance(self.config.get("custom"), dict) else {}
            base = custom.get("llm_config") if isinstance(custom.get("llm_config"), dict) else None
            if not isinstance(base, dict) or not str(base.get("model") or "").strip():
                return {"status": "error", "error": "Missing required config: custom.llm_config.model", "result": None}
            override = custom.get("department_router_llm_config") if isinstance(custom.get("department_router_llm_config"), dict) else {}
            eff = dict(base)
            eff.update({k: v for k, v in (override or {}).items() if k != "kwargs"})
            kw = dict((base.get("kwargs") or {}) if isinstance(base.get("kwargs"), dict) else {})
            kw.update(dict((override.get("kwargs") or {}) if isinstance(override.get("kwargs"), dict) else {}))

            dept_client = LLMClient(
                model=str(eff.get("model") or "").strip(),
                temperature=float(eff.get("temperature", 0.7)),
                tools=[],
                enable_multi_stage_reasoning=False,
                agent_name=self.name,
                **(kw or {}),
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

