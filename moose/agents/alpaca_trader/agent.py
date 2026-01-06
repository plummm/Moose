"""
alpaca_trader agent (paper).

This module intentionally starts small:
- Provides an HTTP `/events` endpoint (text-only) backed by a bounded queue.
- Runs a fixed worker pool to process queued events asynchronously.

This agent:
- Provides an HTTP `/events` endpoint (text-only) backed by a bounded queue.
- Runs a fixed worker pool to process queued events asynchronously.
- Builds trade plans via an LLM controller orchestrating specialist tools.
- Applies deterministic risk gates and (optionally) executes via Alpaca MCP sidecars.
"""

from __future__ import annotations

import asyncio
import queue
import threading
import time
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional, List

from moose.framework import BaseAgent

from moose.framework.llm_core import LLMClient

# Support both:
# - Moose import mode: moose.agents.alpaca_trader.*
# - Standalone agent-dir mode (Docker): services.*, storage.*, mcp.* available on PYTHONPATH
try:  # Moose mode
    from moose.agents.alpaca_trader.alpaca_mcp.alpaca_mcp import AlpacaMcpToolRegistry
    from moose.agents.alpaca_trader.services.fmp_client import FmpClient, FmpConfig
    from moose.agents.alpaca_trader.services.market_hours import MarketHoursConfig, MarketHoursService
    from moose.agents.alpaca_trader.services.scoring import (
        extract_symbols,
        event_dedupe_key,
        regime_delta_from_text,
        watch_bump_from_text,
        clamp,
    )
    from moose.agents.alpaca_trader.storage.db import TraderDb, default_db_path
    from moose.agents.alpaca_trader.services.risk_gate import RiskCaps, GlobalCaps, RiskConfig, RiskGate
    from moose.agents.alpaca_trader.services.execution import AlpacaExecutionService, ExecutionConfig
    from moose.agents.alpaca_trader.services.notifications import DbTradingActivityNotifier, StubNotifierConfig
    from moose.agents.alpaca_trader.services.specialist_tools import create_specialist_tools
    from moose.agents.alpaca_trader.services.plan_controller import PlanController, PlanControllerConfig
except Exception:  # Standalone mode
    from alpaca_mcp.alpaca_mcp import AlpacaMcpToolRegistry  # local file import (agent folder on PYTHONPATH in container)
    from services.fmp_client import FmpClient, FmpConfig
    from services.market_hours import MarketHoursConfig, MarketHoursService
    from services.scoring import extract_symbols, event_dedupe_key, regime_delta_from_text, watch_bump_from_text, clamp
    from storage.db import TraderDb, default_db_path
    from services.risk_gate import RiskCaps, GlobalCaps, RiskConfig, RiskGate
    from services.execution import AlpacaExecutionService, ExecutionConfig
    from services.notifications import DbTradingActivityNotifier, StubNotifierConfig
    from services.specialist_tools import create_specialist_tools
    from services.plan_controller import PlanController, PlanControllerConfig


@dataclass(frozen=True)
class IngestedEvent:
    text: str
    source: str
    ts: float
    raw: Dict[str, Any]


@dataclass(frozen=True)
class ResearchTask:
    task_id: int
    symbol: str
    priority: float
    reason: str
    strategy_hint: Optional[str]
    event_text: str
    created_from_event: Optional[str]


class AlpacaTrader(BaseAgent):
    name = "alpaca_trader"
    description = "Auto trading agent that researches and trades stocks/crypto via Alpaca MCP sidecars"

    def __init__(self, config_path=None, debug: bool = False):
        super().__init__(config_path, debug=debug)

        # Auth password override:
        # - In Docker, prefer env var so we don't commit secrets into agent_config.json.
        # - BaseAgent uses `self.http_auth_password` for auth enforcement.
        env_pw = str(os.getenv("ALPACA_TRADER_AUTH_PASSWORD") or "").strip()
        if env_pw:
            self.http_auth_password = env_pw

        custom = self.config.get("custom") if isinstance(self.config.get("custom"), dict) else {}
        # Alpaca MCP sidecars (one per account). Loaded lazily; tool schemas cached.
        try:
            self.alpaca_mcp = AlpacaMcpToolRegistry.from_custom_config(custom, logger=self.logger)
            self.logger.info(f"Alpaca MCP accounts configured: {self.alpaca_mcp.account_ids()}")
        except Exception as e:
            # Keep agent alive even if MCP config is missing; it can still ingest/queue events.
            self.alpaca_mcp = None
            self.logger.warning(f"Failed to initialize Alpaca MCP tool registry: {e}")
        qcfg = custom.get("event_queue") if isinstance(custom.get("event_queue"), dict) else {}

        # SQLite persistence (events, dedupe, scores, tasks, ...)
        self.db: Optional[TraderDb] = None
        try:
            scfg = custom.get("storage") if isinstance(custom.get("storage"), dict) else {}
            db_path = str(scfg.get("db_path") or "").strip()
            if db_path:
                from pathlib import Path

                self.db = TraderDb(Path(db_path))
            else:
                self.db = TraderDb(default_db_path(agent_name=self.name))
            self.logger.info(f"SQLite DB path: {self.db.path}")
        except Exception as e:
            self.db = None
            self.logger.warning(f"Failed to initialize SQLite DB: {e}")

        # Market hours gating (stocks only).
        self.market_hours: Optional[MarketHoursService] = None
        try:
            mh_cfg = custom.get("market_hours") if isinstance(custom.get("market_hours"), dict) else {}
            exchange = str(mh_cfg.get("exchange") or "NASDAQ").strip() or "NASDAQ"
            holiday_cache_days = mh_cfg.get("holiday_cache_days", 14)
            try:
                holiday_cache_days_i = max(7, int(holiday_cache_days or 14))
            except Exception:
                holiday_cache_days_i = 14

            fmp_key = str((mh_cfg.get("api_key") or "")).strip() or str(
                (custom.get("fmp_api_key") or "")  # optional agent-local override
            ).strip() or ""
            if not fmp_key:
                fmp_key = str(os.getenv("FMP_API_KEY") or "").strip()

            if fmp_key:
                fmp = FmpClient(FmpConfig(api_key=fmp_key))
                self.market_hours = MarketHoursService(
                    fmp=fmp,
                    cfg=MarketHoursConfig(exchange=exchange, holiday_cache_days=holiday_cache_days_i),
                    logger=self.logger,
                )
                self.logger.info(f"MarketHoursService enabled (exchange={exchange})")
            else:
                self.logger.warning("MarketHoursService disabled: missing FMP_API_KEY")
        except Exception as e:
            self.market_hours = None
            self.logger.warning(f"Failed to initialize MarketHoursService: {e}")

        max_workers = qcfg.get("max_concurrent", 4)
        try:
            self._event_max_workers = max(1, int(max_workers or 0))
        except Exception:
            self._event_max_workers = 4

        max_queue = qcfg.get("max_queue_size", 500)
        try:
            self._event_max_queue_size = max(1, int(max_queue or 0))
        except Exception:
            self._event_max_queue_size = 500

        self._drop_policy = str(qcfg.get("drop_policy") or "drop_new").strip().lower()
        if self._drop_policy not in {"drop_new", "drop_oldest"}:
            self._drop_policy = "drop_new"

        self._event_queue: "queue.Queue[IngestedEvent]" = queue.Queue(maxsize=self._event_max_queue_size)
        self._event_worker_stop = threading.Event()
        self._event_workers: list[threading.Thread] = []

        # Research task queue (separate from event ingest).
        self._task_queue: "queue.Queue[ResearchTask]" = queue.Queue(maxsize=self._event_max_queue_size)
        self._task_worker_stop = threading.Event()
        self._task_workers: list[threading.Thread] = []

        self._events_total_received = 0
        self._events_total_queued = 0
        self._events_total_dropped = 0
        self._events_total_processed = 0
        self._events_total_failed = 0

        self._tasks_total_created = 0
        self._tasks_total_queued = 0
        self._tasks_total_dropped = 0
        self._tasks_total_processed = 0
        self._tasks_total_failed = 0

        self._start_event_workers()
        self._start_task_workers()
        self.logger.info(
            f"Event worker pool: workers={self._event_max_workers}, queue_max={self._event_max_queue_size}, "
            f"drop_policy={self._drop_policy}"
        )

        # Planning controller (LLM orchestrator + specialist tools)
        self.plan_controller: Optional[PlanController] = None
        try:
            if self.alpaca_mcp is not None and self.db is not None:
                planning_cfg = custom.get("planning") if isinstance(custom.get("planning"), dict) else {}
                llm_cfg = custom.get("llm_config") if isinstance(custom.get("llm_config"), dict) else {}
                controller_model = str(
                    planning_cfg.get("controller_model") or planning_cfg.get("model") or llm_cfg.get("model") or ""
                ).strip()
                if controller_model:
                    focfg = custom.get("finance_office") if isinstance(custom.get("finance_office"), dict) else {}
                    finance_ep = str(focfg.get("endpoint") or "").strip()
                    tools = create_specialist_tools(
                        agent_name=self.name,
                        logger=self.logger,
                        db=self.db,
                        alpaca_mcp=self.alpaca_mcp,
                        finance_office_endpoint=finance_ep,
                        planning_cfg=planning_cfg,
                    )
                    self.plan_controller = PlanController(
                        cfg=PlanControllerConfig(
                            model=controller_model,
                            temperature=float(planning_cfg.get("temperature", llm_cfg.get("temperature", 0.2)) or 0.2),
                            max_tool_iterations=int(planning_cfg.get("max_tool_iterations", llm_cfg.get("max_tool_iterations", 8)) or 8),
                        ),
                        tools=tools,
                        logger=self.logger,
                        agent_name=self.name,
                    )
                    self.logger.info("PlanController enabled")
                else:
                    self.logger.warning("PlanController disabled: missing custom.planning.controller_model (or custom.llm_config.model)")
        except Exception as e:
            self.plan_controller = None
            self.logger.warning(f"Failed to initialize PlanController: {e}")

        # Risk gate (hard guardrails).
        self.risk_gate: Optional[RiskGate] = None
        try:
            rcfg = custom.get("risk_caps") if isinstance(custom.get("risk_caps"), dict) else {}
            stocks = rcfg.get("stocks") if isinstance(rcfg.get("stocks"), dict) else {}
            crypto = rcfg.get("crypto") if isinstance(rcfg.get("crypto"), dict) else {}
            g = RiskConfig(
                mode=str(custom.get("mode") or "paper"),
                trading_enabled=bool(custom.get("trading_enabled", True)),
                stocks=RiskCaps(
                    max_notional_per_trade_usd=float(stocks.get("max_notional_per_trade_usd", 15000)),
                    max_notional_per_day_usd=float(stocks.get("max_notional_per_day_usd", 75000)),
                    max_symbol_exposure_pct=float(stocks.get("max_symbol_exposure_pct", 0.20)),
                ),
                crypto=RiskCaps(
                    max_notional_per_trade_usd=float(crypto.get("max_notional_per_trade_usd", 5000)),
                    max_notional_per_day_usd=float(crypto.get("max_notional_per_day_usd", 25000)),
                    max_symbol_exposure_pct=float(crypto.get("max_symbol_exposure_pct", 0.10)),
                ),
                global_caps=GlobalCaps(
                    max_open_positions=int(rcfg.get("max_open_positions", 40)),
                    max_open_orders=int(rcfg.get("max_open_orders", 150)),
                ),
            )
            allow_all, allow_stocks, allow_crypto = self._get_allowlists()
            self.risk_gate = RiskGate(
                cfg=g,
                allow_all=allow_all,
                allow_stocks=allow_stocks,
                allow_crypto=allow_crypto,
                market_hours=self.market_hours,
                logger=self.logger,
            )
            self.logger.info("RiskGate enabled")
        except Exception as e:
            self.risk_gate = None
            self.logger.warning(f"Failed to initialize RiskGate: {e}")

        # Execution + reconciliation
        self.execution: Optional[AlpacaExecutionService] = None
        self._reconcile_thread: Optional[threading.Thread] = None
        self._daily_summary_thread: Optional[threading.Thread] = None

        # Notifications (stub)
        self.notifier = None
        try:
            ncfg = custom.get("notifications") if isinstance(custom.get("notifications"), dict) else {}
            if self.db is not None:
                self.notifier = DbTradingActivityNotifier(
                    cfg=StubNotifierConfig(enabled=bool(ncfg.get("enabled", True))),
                    db=self.db,
                    logger=self.logger,
                )
        except Exception:
            self.notifier = None
        try:
            ecfg = custom.get("execution") if isinstance(custom.get("execution"), dict) else {}
            llm_cfg = custom.get("llm_config") if isinstance(custom.get("llm_config"), dict) else {}
            # IMPORTANT:
            # Gemini function calling is stricter about JSON schemas than OpenAI/Anthropic. Some MCP tool schemas
            # (including those produced by some MCP servers) can be rejected by Gemini, causing a 400 error.
            # We therefore allow a dedicated execution model override.
            model = str(ecfg.get("llm_model") or llm_cfg.get("model") or "").strip()
            if self.alpaca_mcp is not None and self.db is not None and model:
                self.execution = AlpacaExecutionService(
                    cfg=ExecutionConfig(
                        model=model,
                        temperature=float(ecfg.get("temperature", llm_cfg.get("temperature", 0.2)) or 0.2),
                        max_tool_iterations=int(ecfg.get("max_tool_iterations", llm_cfg.get("max_tool_iterations", 6)) or 6),
                        auto_execute_approved=bool(ecfg.get("auto_execute_approved", True)),
                        reconcile_interval_s=float(ecfg.get("reconcile_interval_s", 120) or 120),
                    ),
                    mcp_registry=self.alpaca_mcp,
                    db=self.db,
                    logger=self.logger,
                    agent_name=self.name,
                )
                self._start_reconcile_loop()
                self._start_daily_summary_loop()
                self.logger.info("Execution service enabled")
            else:
                self.logger.info("Execution service disabled (missing MCP/DB/model)")
        except Exception as e:
            self.execution = None
            self.logger.warning(f"Failed to initialize execution service: {e}")

    # ---------------------------------------------------------------------
    # BaseAgent required method (stdin/file modes); HTTP mode uses endpoints.
    # ---------------------------------------------------------------------
    def process(self, input_data: Any) -> Any:
        # Minimal fallback for stdin/file mode: treat input as a single event.
        if isinstance(input_data, dict):
            payload = input_data
        else:
            payload = {"text": str(input_data)}
        # Best-effort: enqueue synchronously
        loop = None
        try:
            loop = asyncio.get_running_loop()
        except Exception:
            loop = None
        if loop is not None and loop.is_running():
            # Can't block in an existing loop from sync method; just return an error.
            return {"ok": False, "error": "process() called inside running event loop; use HTTP /events instead"}
        return asyncio.run(self.ingest_event(payload))

    # ---------------------------------------------------------------------
    # HTTP endpoint handlers (wired via agent_config.json)
    # ---------------------------------------------------------------------
    async def ingest_event(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        POST /events
        Body: { "text": "...", "source": "...optional...", "timestamp": "...optional..." }
        """
        self._events_total_received += 1
        text = (data.get("text") or data.get("content") or "").strip()
        if not text:
            return {"ok": False, "error": "missing_text"}

        source = str(data.get("source") or "unknown").strip() or "unknown"
        ts = time.time()
        # Allow caller-supplied timestamp as best-effort; keep unix float seconds internally.
        if data.get("timestamp"):
            try:
                ts = float(data.get("timestamp"))
            except Exception:
                ts = time.time()

        evt = IngestedEvent(text=text, source=source, ts=ts, raw=dict(data))

        queued = self._enqueue_event(evt)
        return {
            "ok": True,
            "queued": bool(queued),
            "dropped": (not bool(queued)),
            "queue_depth": int(self._event_queue.qsize()),
            "received_at": datetime.now(tz=timezone.utc).isoformat(),
        }

    def status(self, _data: Dict[str, Any]) -> Dict[str, Any]:
        """GET /status"""
        return {
            "ok": True,
            "agent": self.name,
            "mode": str((self.config.get("custom") or {}).get("mode") or "paper"),
            "queue": self.queue_stats({}).get("result"),
            "market_hours": {"enabled": bool(self.market_hours)},
        }

    def queue_stats(self, _data: Dict[str, Any]) -> Dict[str, Any]:
        """GET /queue_stats"""
        return {
            "ok": True,
            "result": {
                "max_workers": self._event_max_workers,
                "max_queue_size": self._event_max_queue_size,
                "drop_policy": self._drop_policy,
                "queue_depth": int(self._event_queue.qsize()),
                "total_received": int(self._events_total_received),
                "total_queued": int(self._events_total_queued),
                "total_dropped": int(self._events_total_dropped),
                "total_processed": int(self._events_total_processed),
                "total_failed": int(self._events_total_failed),
                "task_queue_depth": int(self._task_queue.qsize()),
                "tasks_total_created": int(self._tasks_total_created),
                "tasks_total_queued": int(self._tasks_total_queued),
                "tasks_total_dropped": int(self._tasks_total_dropped),
                "tasks_total_processed": int(self._tasks_total_processed),
                "tasks_total_failed": int(self._tasks_total_failed),
            },
        }

    async def execute_command(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        POST /execute (auth required)

        Body:
          {
            "text": "place a paper order to buy ...",
            "account_id": "paper_moose1"   // optional
          }
        """
        # Enforce that auth is actually configured; otherwise BaseAgent auth would be a no-op.
        if not str(getattr(self, "http_auth_password", "") or "").strip():
            return {"ok": False, "error": "auth_password_not_configured", "hint": "Set env ALPACA_TRADER_AUTH_PASSWORD"}

        if self.alpaca_mcp is None:
            return {"ok": False, "error": "alpaca_mcp_not_configured"}
        if self.execution is None:
            return {"ok": False, "error": "execution_not_configured"}

        text = str((data.get("text") or data.get("command") or "")).strip()
        if not text:
            return {"ok": False, "error": "missing_text"}

        account_id = str(data.get("account_id") or "").strip()
        if not account_id:
            # Default to first configured account (stable order).
            ids = self.alpaca_mcp.account_ids()
            if not ids:
                return {"ok": False, "error": "no_accounts_configured"}
            account_id = ids[0]

        # Load MCP tools for this account and invoke the execution model with tool calling enabled.
        tools = await self.alpaca_mcp.get_tools(account_id=account_id)

        llm = LLMClient(
            model=str(self.execution.cfg.model),
            temperature=float(getattr(self.execution.cfg, "temperature", 0.2) or 0.2),
            tools=tools,
            enable_multi_stage_reasoning=True,
            max_tool_iterations=int(getattr(self.execution.cfg, "max_tool_iterations", 6) or 6),
            agent_name=self.name,
        )

        prompt = (
            "You are an authenticated execution operator for Alpaca paper trading.\n"
            "You can call Alpaca MCP tools.\n"
            "Rules:\n"
            "- PAPER trading only.\n"
            "- If the user request is ambiguous, ask one clarification question.\n"
            "- If the user asks to place an order, place it and then fetch the order status/details.\n"
            "- Return JSON only with keys: ok, account_id, action, result.\n\n"
            f"account_id: {account_id}\n"
            f"user_command: {text}\n"
        )

        resp = await llm.send_message(prompt)
        return {"ok": True, "account_id": account_id, "raw": resp.content, "usage": resp.usage, "cost": resp.cost}

    # ---------------------------------------------------------------------
    # Worker pool
    # ---------------------------------------------------------------------
    def _enqueue_event(self, evt: IngestedEvent) -> bool:
        try:
            self._event_queue.put_nowait(evt)
            self._events_total_queued += 1
            return True
        except queue.Full:
            if self._drop_policy == "drop_oldest":
                try:
                    _ = self._event_queue.get_nowait()
                    try:
                        self._event_queue.task_done()
                    except Exception:
                        pass
                except Exception:
                    pass
                try:
                    self._event_queue.put_nowait(evt)
                    self._events_total_queued += 1
                    self._events_total_dropped += 1
                    return True
                except Exception:
                    self._events_total_dropped += 1
                    return False
            self._events_total_dropped += 1
            return False

    def _start_event_workers(self) -> None:
        if self._event_workers:
            return

        def worker_main(worker_idx: int) -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                while not self._event_worker_stop.is_set() and not self.shutdown_requested:
                    try:
                        evt = self._event_queue.get(timeout=0.5)
                    except queue.Empty:
                        continue
                    try:
                        loop.run_until_complete(self._process_event(evt, worker_idx=worker_idx))
                        self._events_total_processed += 1
                    except Exception as e:
                        self._events_total_failed += 1
                        self.logger.error(f"Event worker {worker_idx} failed: {e}", exc_info=True)
                    finally:
                        try:
                            self._event_queue.task_done()
                        except Exception:
                            pass
            finally:
                try:
                    loop.close()
                except Exception:
                    pass

        for i in range(self._event_max_workers):
            t = threading.Thread(target=worker_main, args=(i,), name=f"alpaca_event_worker_{i}", daemon=True)
            t.start()
            self._event_workers.append(t)

    def _start_task_workers(self) -> None:
        if self._task_workers:
            return

        def worker_main(worker_idx: int) -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                while not self._task_worker_stop.is_set() and not self.shutdown_requested:
                    try:
                        task = self._task_queue.get(timeout=0.5)
                    except queue.Empty:
                        continue
                    try:
                        loop.run_until_complete(self._process_task(task, worker_idx=worker_idx))
                        self._tasks_total_processed += 1
                    except Exception as e:
                        self._tasks_total_failed += 1
                        self.logger.error(f"Task worker {worker_idx} failed: {e}", exc_info=True)
                    finally:
                        try:
                            self._task_queue.task_done()
                        except Exception:
                            pass
            finally:
                try:
                    loop.close()
                except Exception:
                    pass

        for i in range(self._event_max_workers):
            t = threading.Thread(target=worker_main, args=(i,), name=f"alpaca_task_worker_{i}", daemon=True)
            t.start()
            self._task_workers.append(t)

    async def _process_event(self, evt: IngestedEvent, *, worker_idx: int) -> None:
        """
        Event processor:
        - extract symbols
        - persisted TTL dedupe
        - update regime/watch scores
        - enqueue per-symbol research tasks
        """
        _ = worker_idx
        symbols = extract_symbols(evt.text)

        ttl_s = 900.0
        try:
            custom = self.config.get("custom") if isinstance(self.config.get("custom"), dict) else {}
            dcfg = custom.get("dedupe") if isinstance(custom.get("dedupe"), dict) else {}
            ttl_s = float(dcfg.get("ttl_seconds", 900) or 900)
        except Exception:
            ttl_s = 900.0

        key = event_dedupe_key(evt.text, symbols)
        inserted_event_id = None
        if self.db is not None:
            ok_new = self.db.dedupe_check_and_set(key=key, ttl_s=ttl_s)
            if not ok_new:
                self.logger.info(f"[event] deduped ttl={ttl_s}s key={key[:10]}.. symbols={symbols}")
                return
            try:
                stored = self.db.insert_event(ts=evt.ts, source=evt.source, text=evt.text, text_hash=key, symbols=symbols)
                inserted_event_id = stored.event_id
            except Exception:
                pass

        # Update regime score
        if self.db is not None:
            try:
                current = float(self.db.get_global_score("regime_score", default=0.0))
                delta = float(regime_delta_from_text(evt.text))
                new_v = clamp(current + delta, -100.0, 100.0)
                self.db.set_global_score(key="regime_score", value=new_v)
            except Exception:
                pass

        # Update watch scores and create tasks
        bump = float(watch_bump_from_text(evt.text))
        allow_all, allow_stocks, allow_crypto = self._get_allowlists()
        for sym in symbols:
            if sym.endswith("USD"):
                if (not allow_all) and (sym not in allow_crypto):
                    continue
                asset = "crypto"
            else:
                if (not allow_all) and (sym not in allow_stocks):
                    continue
                asset = "stock"

            new_watch = None
            if self.db is not None:
                try:
                    w0 = float(self.db.get_watch_score(sym, default=0.0))
                    new_watch = clamp(w0 + bump, 0.0, 100.0)
                    self.db.set_watch_score(symbol=sym, value=new_watch)
                except Exception:
                    new_watch = None

            # Higher watch score => higher priority
            priority = float(new_watch if new_watch is not None else bump)
            reason = f"event:{evt.source}"
            task_id = 0
            if self.db is not None:
                try:
                    task_id = self.db.insert_task(symbol=sym, priority=priority, reason=reason, strategy_hint=None)
                except Exception:
                    task_id = 0
            self._tasks_total_created += 1

            task = ResearchTask(
                task_id=task_id,
                symbol=sym,
                priority=priority,
                reason=reason,
                strategy_hint=None,
                event_text=evt.text,
                created_from_event=str(inserted_event_id or key),
            )
            if self._enqueue_task(task):
                self.logger.info(f"[task] queued symbol={sym} asset={asset} priority={priority:.1f}")
            else:
                self.logger.warning(f"[task] dropped symbol={sym} asset={asset} priority={priority:.1f}")

    def _enqueue_task(self, task: ResearchTask) -> bool:
        try:
            self._task_queue.put_nowait(task)
            self._tasks_total_queued += 1
            return True
        except queue.Full:
            self._tasks_total_dropped += 1
            return False

    def _get_allowlists(self) -> tuple[bool, set[str], set[str]]:
        custom = self.config.get("custom") if isinstance(self.config.get("custom"), dict) else {}
        al = custom.get("allowlists") if isinstance(custom.get("allowlists"), dict) else {}
        allow_all = bool(al.get("allow_all", False))
        stocks = al.get("stocks") if isinstance(al.get("stocks"), list) else []
        crypto = al.get("crypto") if isinstance(al.get("crypto"), list) else []
        return (
            allow_all,
            {str(s).strip().upper() for s in stocks if str(s).strip()},
            {str(s).strip().upper() for s in crypto if str(s).strip()},
        )

    async def _process_task(self, task: ResearchTask, *, worker_idx: int) -> None:
        """
        Placeholder research task processor.

        Next steps will connect:
        - FinanceOffice research packet
        - multi-strategy routing
        - risk gate + execution
        """
        _ = worker_idx
        if self.db is not None and task.task_id:
            try:
                self.db.set_task_status(task_id=task.task_id, status="processing")
            except Exception:
                pass
        self.logger.info(f"[task] processing symbol={task.symbol} priority={task.priority:.1f} reason={task.reason}")
        asset_class = "crypto" if task.symbol.endswith("USD") else "stock"
        regime_score = 0.0
        watch_score = 0.0
        if self.db is not None:
            try:
                regime_score = float(self.db.get_global_score("regime_score", default=0.0))
                watch_score = float(self.db.get_watch_score(task.symbol, default=0.0))
            except Exception:
                pass

        plan = None
        if self.plan_controller is None or self.db is None or self.alpaca_mcp is None:
            self.logger.warning("[task] planning disabled (missing PlanController/DB/MCP)")
        else:
            # Choose account: planning requires an account_id
            account_id = ""
            try:
                ids = self.alpaca_mcp.account_ids()
                account_id = ids[0] if ids else ""
            except Exception:
                account_id = ""

            custom = self.config.get("custom") if isinstance(self.config.get("custom"), dict) else {}
            rcfg = custom.get("risk_caps") if isinstance(custom.get("risk_caps"), dict) else {}
            caps = rcfg.get("crypto") if asset_class == "crypto" else rcfg.get("stocks")
            caps = caps if isinstance(caps, dict) else {}

            plan = await self.plan_controller.build_trade_plan(
                symbol=task.symbol,
                asset_class=asset_class,
                event_text=task.event_text,
                regime_score=regime_score,
                watch_score=watch_score,
                account_id=account_id,
                risk_caps=caps,
                created_from_event=task.created_from_event,
            )

        if plan is not None and self.db is not None:
            try:
                pid = self.db.insert_trade_plan(
                    symbol=plan.symbol,
                    strategy_id=plan.strategy_id,
                    account_id=plan.account_id,
                    side=plan.side,
                    confidence=plan.confidence,
                    plan=plan.to_dict(),
                )
                self.logger.info(
                    f"[plan] created id={pid} symbol={plan.symbol} side={plan.side} conf={plan.confidence:.2f} strategy={plan.strategy_id}"
                )
                if self.notifier is not None:
                    try:
                        self.notifier.plan_created(plan_id=pid, payload={"symbol": plan.symbol, "plan": plan.to_dict()})
                    except Exception:
                        pass

                if self.risk_gate is not None:
                    acct_snap = None
                    mkt_snap = None
                    daily_used = None
                    try:
                        acct_snap = self.db.get_latest_reconcile_snapshot(account_id=plan.account_id)
                    except Exception:
                        acct_snap = None
                    try:
                        mkt_snap = self.db.get_latest_market_snapshot(symbol=plan.symbol)
                    except Exception:
                        mkt_snap = None
                    try:
                        from datetime import datetime, timezone, timedelta

                        now = datetime.now(tz=timezone.utc)
                        start = datetime.combine(now.date(), datetime.min.time(), tzinfo=timezone.utc).timestamp()
                        end = (datetime.combine(now.date(), datetime.min.time(), tzinfo=timezone.utc) + timedelta(days=1)).timestamp()
                        daily_used = float(self.db.sum_orders_notional(account_id=plan.account_id, start_ts=start, end_ts=end))
                    except Exception:
                        daily_used = None

                    verdict = await self.risk_gate.evaluate(
                        plan,
                        account_snapshot=acct_snap,
                        market_context=mkt_snap,
                        daily_notional_used_usd=daily_used,
                    )
                    if verdict.adjusted_plan is not None and verdict.adjusted_plan != plan:
                        plan = verdict.adjusted_plan
                    if verdict.ok:
                        self.db.set_trade_plan_status(plan_id=pid, status="approved")
                        self.db.add_trade_plan_audit(
                            plan_id=pid,
                            event="risk_gate_approved",
                            detail={"reason": verdict.reason, "meta": verdict.meta, "plan": plan.to_dict()},
                        )
                        self.logger.info(f"[risk] approved plan_id={pid} reason={verdict.reason}")
                        if self.execution is not None and self.execution.cfg.auto_execute_approved:
                            try:
                                res = await self.execution.execute_trade_plan(plan_id=pid, plan=plan)
                                self.db.add_trade_plan_audit(plan_id=pid, event="execution_attempt", detail=res)
                                if self.notifier is not None:
                                    try:
                                        self.notifier.order_submitted(plan_id=pid, payload={"symbol": plan.symbol, "execution": res})
                                    except Exception:
                                        pass
                            except Exception as e:
                                self.db.add_trade_plan_audit(
                                    plan_id=pid, event="execution_error", detail={"ok": False, "error": str(e)}
                                )
                    else:
                        self.db.set_trade_plan_status(plan_id=pid, status="rejected")
                        self.db.add_trade_plan_audit(
                            plan_id=pid,
                            event="risk_gate_rejected",
                            detail={"reason": verdict.reason, "meta": verdict.meta, "plan": plan.to_dict()},
                        )
                        self.logger.info(f"[risk] rejected plan_id={pid} reason={verdict.reason}")
            except Exception as e:
                self.logger.warning(f"Failed to persist trade plan for {task.symbol}: {e}")
        else:
            self.logger.info(f"[plan] skipped (router/db unavailable) symbol={task.symbol}")

        if self.db is not None and task.task_id:
            try:
                self.db.set_task_status(task_id=task.task_id, status="done")
            except Exception:
                pass

    def _start_reconcile_loop(self) -> None:
        if self._reconcile_thread is not None:
            return
        if self.execution is None:
            return

        interval = float(self.execution.cfg.reconcile_interval_s or 120.0)

        def run() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                while not self.shutdown_requested:
                    try:
                        loop.run_until_complete(self.execution.reconcile_once())
                    except Exception as e:
                        self.logger.warning(f"Reconcile loop error: {e}")
                    # sleep in small increments to respect shutdown
                    t0 = time.time()
                    while (time.time() - t0) < interval and not self.shutdown_requested:
                        time.sleep(0.5)
            finally:
                try:
                    loop.close()
                except Exception:
                    pass

        self._reconcile_thread = threading.Thread(target=run, name="alpaca_reconcile_loop", daemon=True)
        self._reconcile_thread.start()

    def _start_daily_summary_loop(self) -> None:
        if self._daily_summary_thread is not None:
            return
        if self.execution is None or self.db is None:
            return

        def run() -> None:
            last_date = None
            while not self.shutdown_requested:
                try:
                    from datetime import datetime, timezone, timedelta

                    now = datetime.now(tz=timezone.utc)
                    day = now.date().isoformat()
                    # Generate once per UTC day.
                    if day != last_date:
                        last_date = day
                        start = datetime.combine(now.date(), datetime.min.time(), tzinfo=timezone.utc).timestamp()
                        end = (datetime.combine(now.date(), datetime.min.time(), tzinfo=timezone.utc) + timedelta(days=1)).timestamp()
                        for acct in (self.alpaca_mcp.account_ids() if self.alpaca_mcp is not None else []):
                            summary = self.db.compute_daily_summary(account_id=acct, day_start_ts=start, day_end_ts=end)
                            self.db.upsert_daily_summary(date=day, account_id=acct, summary=summary)
                            if self.notifier is not None:
                                self.notifier.daily_summary(account_id=acct, payload={"date": day, "summary": summary})
                except Exception as e:
                    self.logger.warning(f"Daily summary loop error: {e}")
                # Check hourly
                t0 = time.time()
                while (time.time() - t0) < 3600 and not self.shutdown_requested:
                    time.sleep(0.5)

        self._daily_summary_thread = threading.Thread(target=run, name="alpaca_daily_summary_loop", daemon=True)
        self._daily_summary_thread.start()


