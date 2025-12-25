"""Financial Report Analyzer Agent.

This agent receives file paths from news_scraper agent and analyzes financial news
articles using LLM with LangGraph workflow.
"""

import os
import asyncio
import html
import hashlib
import json
import threading
import queue
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, List
from urllib.parse import quote

from moose.framework import BaseAgent
from moose.framework.llm_core import LLMClient

# LangGraph (assumed available in this environment)
from langgraph.graph import END, StateGraph

# Import local agent modules (agent code is mounted into /app; do not import from the installed `moose` package)
from assistant import FinanceOfficeAssistant
from investment_research_team.research_lead import ResearchLead
from investment_research_team.edgar_mcp_tools import EdgarAllMCPTools
from investment_research_team.fmp_mcp_tools import FMPAllMCPTools
from investment_research_team.mcp_tools import CombinedFinanceMCPTools


class FinanceOffice(BaseAgent):
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
        
        # Store analyzers (future-proof: multiple analyzers/teams)
        self.analyzers: List[Any] = []
        self.analyzers_by_team: Dict[str, Any] = {}
        if analyzer is not None:
            self.analyzers.append(analyzer)
            # Currently only investment_research_team is wired.
            self.analyzers_by_team["investment_research_team"] = analyzer

        # Cached department-level workflow (LangGraph) for routing tasks to sub-agent teams.
        # This is compiled once per FinanceOffice instance for performance.
        self._department_workflow_app: Any = None
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

    # -------------------------------------------------------------------------
    # Market Impression UI (reads /data/news/<ticker>/<YYYY>/<MM>/memory.json)
    # -------------------------------------------------------------------------
    def _market_impression_base_dir(self) -> Path:
        base_news_dir = os.getenv("NEWS_RESULT_DIR", "/data/news") or "/data/news"
        return Path(str(base_news_dir))

    def _market_impression_current_bucket_utc(self) -> Tuple[str, str]:
        now = datetime.now(timezone.utc)
        return now.strftime("%Y"), now.strftime("%m")

    def _market_impression_read_json(self, fp: Path) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        try:
            if not fp.exists() or not fp.is_file():
                return None, "not_found"
            raw = fp.read_text(encoding="utf-8")
            obj = json.loads(raw)
            if not isinstance(obj, dict):
                return None, "not_a_dict"
            return obj, None
        except Exception as e:
            return None, f"error:{e}"

    def _market_impression_safe_resolve_news_path(self, file_path: str) -> Tuple[Optional[Path], Optional[str]]:
        """
        Resolve a user-provided file_path to an on-disk Path under NEWS_RESULT_DIR.
        Security: only allow paths that resolve within NEWS_RESULT_DIR and end with .json.
        """
        base = self._market_impression_base_dir()
        base_resolved = base.resolve()
        try:
            raw = str(file_path or "").strip()
            if not raw:
                return None, "missing_file_path"
            p = Path(raw)
            if not p.is_absolute():
                p = base / p
            resolved = p.resolve()
            # Python 3.11+ has Path.is_relative_to
            try:
                if not resolved.is_relative_to(base_resolved):  # type: ignore[attr-defined]
                    return None, "forbidden_path"
            except Exception:
                # Fallback in case is_relative_to isn't available for some reason
                if str(base_resolved) not in str(resolved):
                    return None, "forbidden_path"
            if resolved.suffix.lower() != ".json":
                return None, "only_json_allowed"
            if not resolved.exists() or not resolved.is_file():
                return None, "not_found"
            return resolved, None
        except Exception as e:
            return None, f"error:{e}"

    def market_impression_page(self, data: Dict[str, Any]) -> Any:
        """
        GET /market-impression
        Lists tickers under NEWS_RESULT_DIR and shows current UTC month memory.json summary fields.
        """
        from flask import Response  # type: ignore

        base = self._market_impression_base_dir()
        year, month = self._market_impression_current_bucket_utc()

        tickers: List[str] = []
        try:
            if base.exists() and base.is_dir():
                for child in base.iterdir():
                    if child.is_dir():
                        tickers.append(child.name)
        except Exception:
            tickers = []
        tickers = sorted(set(tickers), key=lambda s: str(s).upper())

        rows_html: List[str] = []
        for t in tickers:
            mem_fp = base / t / year / month / "memory.json"
            mem, _err = self._market_impression_read_json(mem_fp)
            sentiment = str((mem or {}).get("sentiment") or "N/A")
            params = (mem or {}).get("parameters") if isinstance((mem or {}).get("parameters"), dict) else {}
            try:
                sentiment_number = float((params or {}).get("sentiment_number"))
                sentiment_number_str = f"{sentiment_number:.4f}"
            except Exception:
                sentiment_number_str = "N/A"
            try:
                memory_weight = float((params or {}).get("memory_weight"))
                memory_weight_str = f"{memory_weight:.4f}"
            except Exception:
                memory_weight_str = "N/A"

            t_disp = html.escape(str(t))
            sentiment_disp = html.escape(sentiment)
            link = f"/market-impression/ticker?ticker={quote(str(t))}"
            rows_html.append(
                f"<tr>"
                f"<td><a href=\"{link}\"><code>{t_disp}</code></a></td>"
                f"<td>{sentiment_disp}</td>"
                f"<td><code>{html.escape(sentiment_number_str)}</code></td>"
                f"<td><code>{html.escape(memory_weight_str)}</code></td>"
                f"</tr>"
            )

        html_doc = f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8"/>
    <meta name="viewport" content="width=device-width, initial-scale=1"/>
    <title>finance_office — Market Impression</title>
    <style>
      body {{ font-family: system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif; margin: 16px; }}
      .row {{ display: flex; gap: 16px; flex-wrap: wrap; align-items: center; }}
      .card {{ border: 1px solid #ddd; border-radius: 10px; padding: 12px 14px; }}
      table {{ border-collapse: collapse; width: 100%; }}
      th, td {{ border-bottom: 1px solid #eee; padding: 8px 6px; text-align: left; vertical-align: top; }}
      th {{ font-weight: 600; }}
      code {{ background: #f6f6f6; padding: 2px 6px; border-radius: 6px; }}
      .muted {{ color: #666; }}
      a {{ color: inherit; }}
    </style>
  </head>
  <body>
    <h2>finance_office — Market Impression</h2>
    <div class="row card">
      <div><b>Base dir</b>: <code>{html.escape(str(base))}</code></div>
      <div><b>UTC bucket</b>: <code>{html.escape(year)}/{html.escape(month)}</code></div>
      <div class="muted">Showing values from each ticker’s <code>memory.json</code> for the current UTC month.</div>
    </div>

    <div class="card" style="margin-top: 12px;">
      <h3 style="margin: 0 0 10px 0;">Tickers</h3>
      <table>
        <thead>
          <tr>
            <th>Ticker</th>
            <th>Sentiment</th>
            <th>Sentiment #</th>
            <th>Memory weight</th>
          </tr>
        </thead>
        <tbody>
          {''.join(rows_html) if rows_html else '<tr><td colspan="4" class="muted">No tickers found under NEWS_RESULT_DIR.</td></tr>'}
        </tbody>
      </table>
    </div>
  </body>
</html>
"""
        return Response(html_doc, mimetype="text/html")

    def market_impression_ticker_page(self, data: Dict[str, Any]) -> Any:
        """
        GET /market-impression/ticker?ticker=...
        Shows current UTC month memory.json details for a ticker.
        """
        from flask import Response  # type: ignore

        ticker = str((data or {}).get("ticker") or "").strip().upper()
        if not ticker:
            return Response(
                "<h3>400 — Missing required query param: ticker</h3><p>Try <code>/market-impression</code>.</p>",
                mimetype="text/html",
                status=400,
            )

        base = self._market_impression_base_dir()
        year, month = self._market_impression_current_bucket_utc()
        mem_fp = base / ticker / year / month / "memory.json"
        mem, err = self._market_impression_read_json(mem_fp)

        if not mem:
            msg = html.escape(str(err or "not_found"))
            return Response(
                f"""<!doctype html><html><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>finance_office — {html.escape(ticker)} — Market Impression</title>
<style>
body {{ font-family: system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif; margin: 16px; }}
.card {{ border: 1px solid #ddd; border-radius: 10px; padding: 12px 14px; }}
code {{ background: #f6f6f6; padding: 2px 6px; border-radius: 6px; }}
.muted {{ color: #666; }}
</style></head><body>
<p><a href="/market-impression">← Back</a></p>
<h2>finance_office — <code>{html.escape(ticker)}</code></h2>
<div class="card">
  <div class="muted">No <code>memory.json</code> for current UTC month.</div>
  <div style="margin-top: 6px;"><b>Expected path</b>: <code>{html.escape(str(mem_fp))}</code></div>
  <div style="margin-top: 6px;"><b>Reason</b>: <code>{msg}</code></div>
</div>
</body></html>""",
                mimetype="text/html",
                status=404,
            )

        trading_insights = str(mem.get("trading_insights") or "").strip()
        sentiment = str(mem.get("sentiment") or "").strip() or "N/A"
        params = mem.get("parameters") if isinstance(mem.get("parameters"), dict) else {}
        try:
            sentiment_number = float((params or {}).get("sentiment_number"))
            sentiment_number_str = f"{sentiment_number:.4f}"
        except Exception:
            sentiment_number_str = "N/A"

        memory_list = mem.get("memory_list") if isinstance(mem.get("memory_list"), list) else []
        blocks: List[str] = []
        for i, item in enumerate(memory_list):
            if not isinstance(item, dict):
                continue
            title = str(item.get("memory_title") or "").strip() or "(untitled)"
            fp = str(item.get("file_path") or "").strip()
            try:
                conf = float(item.get("confidence") or 0.0)
                conf_str = f"{conf:.2f}"
            except Exception:
                conf_str = "N/A"
            if not fp:
                link = ""
            else:
                link = f"/market-impression/memory?ticker={quote(ticker)}&file_path={quote(fp)}"
            mem_link_html = (
                f'<a href="{link}">Open raw JSON →</a>'
                if link
                else '<span class="muted">Missing file_path</span>'
            )
            blocks.append(
                f"""<div class="mem">
  <div class="memTitle">{html.escape(title)}</div>
  <div class="memMeta"><span class="pill">confidence: <code>{html.escape(conf_str)}</code></span></div>
  <div class="memLink">{mem_link_html}</div>
</div>"""
            )

        html_doc = f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8"/>
    <meta name="viewport" content="width=device-width, initial-scale=1"/>
    <title>finance_office — {html.escape(ticker)} — Market Impression</title>
    <style>
      body {{ font-family: system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif; margin: 16px; }}
      .card {{ border: 1px solid #ddd; border-radius: 10px; padding: 12px 14px; }}
      code {{ background: #f6f6f6; padding: 2px 6px; border-radius: 6px; }}
      .muted {{ color: #666; }}
      .grid {{ display: grid; grid-template-columns: 1fr; gap: 10px; }}
      .mem {{ border: 1px solid #eee; border-radius: 10px; padding: 10px 12px; }}
      .memTitle {{ font-weight: 600; margin-bottom: 6px; }}
      .memMeta {{ margin-bottom: 6px; }}
      .pill {{ display: inline-block; border: 1px solid #ddd; border-radius: 999px; padding: 4px 10px; }}
      a {{ color: inherit; }}
      pre {{ white-space: pre-wrap; word-break: break-word; }}
    </style>
  </head>
  <body>
    <p><a href="/market-impression">← Back</a></p>
    <h2>finance_office — <code>{html.escape(ticker)}</code></h2>

    <div class="card">
      <div class="muted" style="margin-bottom: 8px;">Current UTC month memory</div>
      <div style="margin-bottom: 10px;"><b>trading_insights</b></div>
      <pre>{html.escape(trading_insights) if trading_insights else html.escape('(empty)')}</pre>
      <hr style="border: none; border-top: 1px solid #eee; margin: 12px 0;"/>
      <div><b>sentiment</b>: <code>{html.escape(sentiment)}</code></div>
      <div style="margin-top: 6px;"><b>sentiment_number</b>: <code>{html.escape(sentiment_number_str)}</code></div>
    </div>

    <div class="card" style="margin-top: 12px;">
      <div style="display:flex; justify-content: space-between; align-items: baseline; gap: 12px; flex-wrap: wrap;">
        <h3 style="margin: 0;">Memories</h3>
        <div class="muted"><code>{html.escape(str(len(blocks)))}</code> entries</div>
      </div>
      <div class="grid" style="margin-top: 10px;">
        {''.join(blocks) if blocks else '<div class="muted">No memory_list entries.</div>'}
      </div>
    </div>
  </body>
</html>
"""
        return Response(html_doc, mimetype="text/html")

    def market_impression_memory_page(self, data: Dict[str, Any]) -> Any:
        """
        GET /market-impression/memory?file_path=...
        Shows raw JSON content for a memory entry file_path, restricted to NEWS_RESULT_DIR.
        """
        from flask import Response  # type: ignore

        raw_fp = str((data or {}).get("file_path") or "").strip()
        ticker = str((data or {}).get("ticker") or "").strip().upper()
        resolved, err = self._market_impression_safe_resolve_news_path(raw_fp)
        back_link = "/market-impression"
        if ticker:
            back_link = f"/market-impression/ticker?ticker={quote(ticker)}"

        if not resolved:
            return Response(
                f"""<!doctype html><html><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>finance_office — Memory JSON</title>
<style>
body {{ font-family: system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif; margin: 16px; }}
.card {{ border: 1px solid #ddd; border-radius: 10px; padding: 12px 14px; }}
code {{ background: #f6f6f6; padding: 2px 6px; border-radius: 6px; }}
.muted {{ color: #666; }}
a {{ color: inherit; }}
</style></head><body>
<p><a href="{back_link}">← Back</a></p>
<h2>finance_office — Memory JSON</h2>
<div class="card">
  <div><b>Status</b>: <code>forbidden</code></div>
  <div style="margin-top: 8px;"><b>Reason</b>: <code>{html.escape(str(err or 'forbidden'))}</code></div>
  <div style="margin-top: 8px;"><b>Requested</b>: <code>{html.escape(raw_fp or '(empty)')}</code></div>
  <div style="margin-top: 8px;" class="muted">Allowed root: <code>{html.escape(str(self._market_impression_base_dir()))}</code></div>
</div>
</body></html>""",
                mimetype="text/html",
                status=403,
            )

        text = ""
        pretty = ""
        parse_error: Optional[str] = None
        try:
            text = resolved.read_text(encoding="utf-8")
            try:
                obj = json.loads(text)
                pretty = json.dumps(obj, ensure_ascii=False, indent=2)
            except Exception as e:
                parse_error = str(e)
                pretty = text
        except Exception as e:
            parse_error = f"read_error:{e}"
            pretty = ""

        title = f"finance_office — Memory JSON — {resolved.name}"
        pe = html.escape(pretty)
        parse_banner = ""
        if parse_error:
            parse_banner = f'<div class="muted" style="margin-top: 6px;">Parse note: <code>{html.escape(parse_error)}</code></div>'

        return Response(
            f"""<!doctype html><html><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{html.escape(title)}</title>
<style>
body {{ font-family: system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif; margin: 16px; }}
.card {{ border: 1px solid #ddd; border-radius: 10px; padding: 12px 14px; }}
code {{ background: #f6f6f6; padding: 2px 6px; border-radius: 6px; }}
.muted {{ color: #666; }}
pre {{ white-space: pre-wrap; word-break: break-word; }}
a {{ color: inherit; }}
</style></head><body>
<p><a href="{back_link}">← Back</a></p>
<h2>finance_office — Memory JSON</h2>
<div class="card">
  <div><b>File</b>: <code>{html.escape(str(resolved))}</code></div>
  {parse_banner}
  <pre style="margin-top: 10px;">{pe if pe else html.escape('(empty)')}</pre>
</div>
</body></html>""",
            mimetype="text/html",
        )
    
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

            if not self.analyzers_by_team.get("investment_research_team"):
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

    # -------------------------------------------------------------------------
    # Department task workflow (LangGraph): routes tasks to sub-agent teams
    # -------------------------------------------------------------------------
    def _get_department_workflow_app(self) -> Any:
        """
        Department-level LangGraph (cached per instance):
        start -> investment_research_team -> END

        Designed to be extended later with additional team nodes.
        """
        if self._department_workflow_app is not None:
            return self._department_workflow_app

        workflow = StateGraph(dict)  # state is a plain dict
        workflow.add_node("start", self._node_start)
        workflow.add_node("prompt_engineer", self._node_prompt_engineer)
        workflow.add_node("investment_research_team", self._node_investment_research_team)

        def _route_after_start(state: Dict[str, Any]) -> str:
            # If a node already produced a final response, end early.
            if isinstance(state.get("final_response"), dict):
                return "end"
            team = str(state.get("selected_team") or "").strip()
            return team if team else "end"

        workflow.set_entry_point("start")
        workflow.add_conditional_edges(
            "start",
            _route_after_start,
            {"investment_research_team": "prompt_engineer", "end": END},
        )

        def _route_after_prompt_engineer(state: Dict[str, Any]) -> str:
            if isinstance(state.get("final_response"), dict):
                return "end"
            team = str(state.get("selected_team") or "").strip()
            return team if team else "end"

        workflow.add_conditional_edges(
            "prompt_engineer",
            _route_after_prompt_engineer,
            {"investment_research_team": "investment_research_team", "end": END},
        )
        workflow.add_edge("investment_research_team", END)

        self._department_workflow_app = workflow.compile()
        return self._department_workflow_app

    async def _node_start(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """
        Start node: validate input, run department router, select team, and prepare state.
        """
        raw_request = state.get("raw_request") if isinstance(state.get("raw_request"), dict) else {}
        instruction = str(state.get("instruction") or "").strip()
        context_text = str(state.get("context") or "").strip()
        analyzer_data = state.get("analyzer_data") if isinstance(state.get("analyzer_data"), dict) else {}

        if not instruction:
            return {**state, "final_response": {"status": "error", "error": "instruction is required", "result": None}}

        if not self.analyzers:
            return {**state, "final_response": {"status": "error", "error": "Analyzer is not initialized", "result": None}}

        # Department-head router (tool-less) selects team + goal; currently routes into investment_research_team.
        # NOTE: `agent.py` is imported as a top-level module in /app (see generated entry.py), so avoid
        # relative imports like `from .department_router ...`.
        from department_router import load_department_playbooks, route_department_task

        playbooks_path = Path(__file__).resolve().parent / "department_playbooks.yaml"
        dept_playbooks = load_department_playbooks(playbooks_path)

        custom = self.config.get("custom", {}) if isinstance(self.config.get("custom"), dict) else {}
        base = custom.get("llm_config") if isinstance(custom.get("llm_config"), dict) else None
        if not isinstance(base, dict) or not str(base.get("model") or "").strip():
            return {
                **state,
                "final_response": {"status": "error", "error": "Missing required config: custom.llm_config.model", "result": None},
            }
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
            task_instruction=instruction,
            context=context_text,
        )

        # For now we only implement a single team node, but state is structured per-team for future expansion.
        selected_team = str((decision.selected_teams or [""])[0] or "").strip()
        if not selected_team:
            return {**state, "final_response": {"status": "error", "error": "Router returned no selected team", "result": None}}
        if selected_team != "investment_research_team":
            return {
                **state,
                "final_response": {
                    "status": "error",
                    "error": f"Unsupported team selected by router: {selected_team}",
                    "result": None,
                },
            }

        # Compute a seed per-team task_instruction from the selected playbook's team base goal + the user instruction.
        # This will be rewritten by the prompt_engineer node into a higher-quality team-specific instruction.
        pb_defs = dept_playbooks.get("playbooks") if isinstance(dept_playbooks.get("playbooks"), dict) else {}
        team_defs = dept_playbooks.get("teams") if isinstance(dept_playbooks.get("teams"), dict) else {}
        pb_spec = pb_defs.get(decision.playbook) if isinstance(pb_defs.get(decision.playbook), dict) else {}
        pb_teams = pb_spec.get("teams") if isinstance(pb_spec.get("teams"), list) else []
        base_goal = ""
        for t in pb_teams:
            if not isinstance(t, dict):
                continue
            if str(t.get("team") or "").strip() == selected_team:
                base_goal = str(t.get("goal") or "").strip()
                break
        derived_team_instruction = (base_goal + "\n\nUser request:\n" + instruction).strip() if base_goal else instruction
        team_desc = ""
        td = team_defs.get(selected_team) if isinstance(team_defs.get(selected_team), dict) else {}
        if isinstance(td, dict):
            team_desc = str(td.get("description") or "").strip()

        # Router-provided team task envelope (goal + notes). Keep this as part of per-team decision.
        user_message = str(analyzer_data.get("user_message") or "").strip()
        system_message = str(analyzer_data.get("system_message") or "").strip()

        return {
            **state,
            "raw_request": raw_request,
            "instruction": instruction,
            "context": context_text,
            "analyzer_data": analyzer_data,
            # routing
            "decision_obj": decision,
            "selected_team": selected_team,
            # per-team state (required contract)
            "task_instruction": {selected_team: derived_team_instruction},
            "decision": {
                selected_team: {
                    "playbook": str(decision.playbook or ""),
                    "rationale": str(decision.rationale or ""),
                    "base_goal": base_goal,
                }
            },
            "team_description": {selected_team: team_desc},
            "playbook_team_goal": {selected_team: base_goal},
            "user_message": {selected_team: user_message},
            "system_message": {selected_team: system_message},
            "previous_team_result": {},
        }

    async def _node_prompt_engineer(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """
        Prompt engineer node (not a team analyzer):
        Rewrites per-team task instructions using:
        - department-level instruction + context
        - team description
        - playbook base goal for that team
        - optional prior outputs from other team nodes (future multi-team workflows)
        """
        if isinstance(state.get("final_response"), dict):
            return state

        instruction = str(state.get("instruction") or "").strip()
        context_text = str(state.get("context") or "").strip()
        selected_team = str(state.get("selected_team") or "").strip()

        per_team_task_instruction = state.get("task_instruction") if isinstance(state.get("task_instruction"), dict) else {}
        per_team_desc = state.get("team_description") if isinstance(state.get("team_description"), dict) else {}
        per_team_goal = state.get("playbook_team_goal") if isinstance(state.get("playbook_team_goal"), dict) else {}
        per_team_prev = state.get("previous_team_result") if isinstance(state.get("previous_team_result"), dict) else {}

        if not selected_team:
            return {**state, "final_response": {"status": "error", "error": "Missing selected_team for prompt_engineer", "result": None}}

        seed = str(per_team_task_instruction.get(selected_team) or "").strip()
        team_desc = str(per_team_desc.get(selected_team) or "").strip()
        base_goal = str(per_team_goal.get(selected_team) or "").strip()
        prev_out = per_team_prev.get(selected_team)

        custom = self.config.get("custom", {}) if isinstance(self.config.get("custom"), dict) else {}
        base_cfg = custom.get("llm_config") if isinstance(custom.get("llm_config"), dict) else None
        if not isinstance(base_cfg, dict) or not str(base_cfg.get("model") or "").strip():
            return {
                **state,
                "final_response": {"status": "error", "error": "Missing required config: custom.llm_config.model", "result": None},
            }
        override = (
            custom.get("department_prompt_engineer_llm_config")
            if isinstance(custom.get("department_prompt_engineer_llm_config"), dict)
            else {}
        )
        eff = dict(base_cfg)
        eff.update({k: v for k, v in (override or {}).items() if k != "kwargs"})
        kw = dict((base_cfg.get("kwargs") or {}) if isinstance(base_cfg.get("kwargs"), dict) else {})
        kw.update(dict((override.get("kwargs") or {}) if isinstance(override.get("kwargs"), dict) else {}))

        pe_client = LLMClient(
            model=str(eff.get("model") or "").strip(),
            temperature=float(eff.get("temperature", 0.3)),
            tools=[],
            enable_multi_stage_reasoning=False,
            agent_name=self.name,
            **(kw or {}),
        )

        system_message = f"""You are a prompt engineer specialized in finance / investment research orchestration.

Goal:
- Given department-level context and a target team, produce ONE high-quality instruction that best guides the target team to execute the work.
- The instruction must be aligned with the team's mandate (playbook base goal) and tailored to the user's request.

You will be given the following inputs in the USER message:
- Team (string): The target team name. Use it only to tailor tone/assumptions; do not restate it unnecessarily.
- Team description (string): Human-readable capability summary. Use it to decide what to emphasize/avoid.
- Playbook base goal (string): The team's high-level mandate for the selected playbook. Treat this as the primary objective.
- User request (string): The user's natural-language request. This provides specificity and constraints.
- Context (string): Optional context text. It may be empty. It can include pasted notes, article text, tickers, constraints, or background.
- Seed instruction (string): The current draft instruction. You should improve/clarify it; do not blindly repeat it.
- Previous output (JSON or empty): Optional output from a prior step for this same team (future multi-step workflows). It may be null/empty.
  - If present, treat it as partial progress or intermediate findings and refine the instruction accordingly.

Mandatory rules:
- Output MUST be PLAIN TEXT only (no JSON, no markdown, no code fences).
- Be specific and actionable: include concrete tasks,expectations, deliverables, scope, and evaluation criteria when possible.
- If key inputs are missing (e.g., company/ticker, timeframe, region), ask concise clarifying questions inside the instruction.
- Do NOT mention these rules or your role.

Return format:
- Plain text instruction only."""

        pe_input = {
            "team": selected_team,
            "team_description": team_desc,
            "playbook_base_goal": base_goal,
            "user_request": instruction,
            "context": context_text,
            "seed_instruction": seed,
            "previous_output": prev_out,
        }
        user_message = f"""Rewrite the instruction for the target team.

Inputs (JSON):
{json.dumps(pe_input, ensure_ascii=False, indent=2)}

Return plain text instruction only."""

        resp = await pe_client.send_message(message=user_message, system_message=system_message)
        rewritten = str(getattr(resp, "content", "") or "").strip()
        if not rewritten:
            rewritten = seed or instruction

        return {**state, "task_instruction": {**per_team_task_instruction, selected_team: rewritten}}

    async def _node_investment_research_team(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """
        Team node: invoke investment_research_team analyzer and normalize output to API response schema.
        """
        team_name = "investment_research_team"
        context_text = str(state.get("context") or "").strip()
        per_team_instruction = state.get("task_instruction") if isinstance(state.get("task_instruction"), dict) else {}
        task_instruction = str(per_team_instruction.get(team_name) or "").strip()
        per_team_user_message = state.get("user_message") if isinstance(state.get("user_message"), dict) else {}
        per_team_system_message = state.get("system_message") if isinstance(state.get("system_message"), dict) else {}
        merge_user_message = str(per_team_user_message.get(team_name) or "").strip()
        merge_system_message = str(per_team_system_message.get(team_name) or "").strip()
        per_team_decision = state.get("decision") if isinstance(state.get("decision"), dict) else {}
        team_decision = per_team_decision.get(team_name) if isinstance(per_team_decision.get(team_name), dict) else {}

        analyzer = self.analyzers_by_team.get("investment_research_team")
        if analyzer is None:
            return {
                **state,
                "final_response": {"status": "error", "error": "investment_research_team analyzer is not initialized", "result": None},
            }
        if not task_instruction:
            return {
                **state,
                "final_response": {"status": "error", "error": "Missing per-team task_instruction for investment_research_team", "result": None},
            }

        team_resp = await analyzer.run_task(
            task_instruction=task_instruction,
            context_text=context_text,
            metadata={
                "decision": team_decision,
            },
            merge_system_message=merge_system_message,
            merge_user_message=merge_user_message,
        )
        if not isinstance(team_resp, dict):
            return {**state, "final_response": {"status": "error", "error": "Invalid team response", "result": {}}}

        if team_resp.get("status") != "success":
            return {
                **state,
                "final_response": {
                    "status": "error",
                    "error": str(team_resp.get("error") or "Team error"),
                    "result": team_resp.get("result"),
                },
            }

        # The investment_research_team returns an envelope that may include a `raw` snapshot of the full
        # LangGraph state. That snapshot can contain non-JSON-serializable objects (e.g., LLMClient instances
        # under `specialist_clients`). Since finance_office is an HTTP JSON API, strip `raw` before returning.
        team_result = team_resp.get("result")
        if isinstance(team_result, dict) and "raw" in team_result:
            try:
                team_result = dict(team_result)
                team_result.pop("raw", None)
            except Exception:
                pass

        # Plumb InvestmentResearchWorkflow's token usage/cost totals into the department workflow state so
        # FinanceOffice.run_task can attach them to the outgoing response.
        # ResearchLead.run_task provides them both top-level and inside last_state (keep this defensive).
        last_state = team_resp.get("last_state") if isinstance(team_resp.get("last_state"), dict) else {}
        usage_total = team_resp.get("llm_usage_total")
        cost_total = team_resp.get("llm_cost_total")
        if usage_total is None:
            usage_total = last_state.get("llm_usage_total")
        if cost_total is None:
            cost_total = last_state.get("llm_cost_total")

        per_team_prev = state.get("previous_team_result") if isinstance(state.get("previous_team_result"), dict) else {}
        return {
            **state,
            "previous_team_result": {**per_team_prev, team_name: team_result},
            "final_response": {"status": "success", "error": None, "result": team_result},
            "llm_usage_total": usage_total,
            "llm_cost_total": cost_total,
        }

    async def run_task(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        HTTP endpoint handler for running a natural-language finance research task.

        Expected input (no backward compatibility):
        {"instruction": "", "context": "", "analyzer_data": {"user_message":"", "system_message":""}}
        """
        try:
            instruction = str(data.get("instruction") or "").strip()
            context_text = str(data.get("context") or "").strip()
            analyzer_data = data.get("analyzer_data") if isinstance(data.get("analyzer_data"), dict) else {}

            app = self._get_department_workflow_app()
            state_in = {
                "raw_request": data,
                "instruction": instruction,
                "context": context_text,
                "analyzer_data": analyzer_data,
            }
            state_out = await app.ainvoke(state_in)
            final_response = state_out.get("final_response") if isinstance(state_out, dict) else None
            if isinstance(final_response, dict):
                # Include token usage/cost totals for downstream callers (e.g., telegram_stock_bot).
                try:
                    usage_total = state_out.get("llm_usage_total") if isinstance(state_out, dict) else None
                    cost_total = state_out.get("llm_cost_total") if isinstance(state_out, dict) else None
                    if isinstance(usage_total, dict) and "llm_usage_total" not in final_response:
                        final_response["llm_usage_total"] = usage_total
                    if cost_total is not None and "llm_cost_total" not in final_response:
                        try:
                            final_response["llm_cost_total"] = float(cost_total)
                        except Exception:
                            final_response["llm_cost_total"] = cost_total
                except Exception:
                    pass
                return final_response
            return {"status": "error", "error": "Invalid department workflow response", "result": None}
        except Exception as e:
            self.logger.error(f"Error in run_task endpoint: {e}", exc_info=True)
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

