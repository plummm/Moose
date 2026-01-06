from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from moose.framework.llm_core import LLMClient

try:  # Moose mode
    from moose.agents.alpaca_trader.models import TradePlan
except Exception:  # Standalone mode
    from models import TradePlan


@dataclass(frozen=True)
class ExecutionConfig:
    model: str
    temperature: float = 0.2
    max_tool_iterations: int = 6
    auto_execute_approved: bool = True
    reconcile_interval_s: float = 120.0


class AlpacaExecutionService:
    """
    Execution via Alpaca MCP tools.

    Implementation approach:
    - Use LLM tool-calling to interact with the Alpaca MCP toolset without hardcoding tool names.
    - Enforce idempotency via client_order_id and DB checks.
    """

    def __init__(
        self,
        *,
        cfg: ExecutionConfig,
        mcp_registry: Any,  # AlpacaMcpToolRegistry
        db: Any,  # TraderDb
        logger: Any,
        agent_name: str,
    ) -> None:
        self.cfg = cfg
        self.mcp_registry = mcp_registry
        self.db = db
        self.logger = logger
        self.agent_name = agent_name

    async def execute_trade_plan(self, *, plan_id: int, plan: TradePlan) -> Dict[str, Any]:
        if plan.side not in {"buy", "sell"}:
            return {"ok": True, "skipped": True, "reason": "side_not_executable", "side": plan.side}

        # OrderSpec sanity (best-effort; RiskGate should already enforce)
        if getattr(plan, "order", None) is not None:
            try:
                o = plan.order
                if o is not None and getattr(o, "order_type", None) == "limit" and getattr(o, "limit_price", None) is None:
                    return {"ok": False, "error": "missing_limit_price"}
            except Exception:
                pass

        # Idempotency check
        existing = self.db.get_order_by_plan_id(plan_id=plan_id)
        if existing is not None:
            return {"ok": True, "skipped": True, "reason": "already_submitted", "order": existing}

        client_order_id = f"{self.agent_name}-{plan_id}-{uuid.uuid4().hex[:10]}"
        notional_hint = plan.notional_usd_hint
        try:
            if getattr(plan, "order", None) is not None and plan.order is not None and plan.order.notional_usd is not None:
                notional_hint = float(plan.order.notional_usd)
        except Exception:
            pass
        self.db.insert_order_stub(
            plan_id=plan_id,
            account_id=plan.account_id,
            client_order_id=client_order_id,
            symbol=plan.symbol,
            side=plan.side,
            notional_usd_hint=notional_hint,
            status="submitting",
        )

        tools = await self.mcp_registry.get_tools(account_id=plan.account_id)
        llm = LLMClient(
            model=self.cfg.model,
            temperature=float(self.cfg.temperature),
            tools=tools,
            enable_multi_stage_reasoning=True,
            max_tool_iterations=int(self.cfg.max_tool_iterations),
            agent_name=self.agent_name,
        )

        system_prompt = (
            "You are an execution agent placing a PAPER trade via Alpaca MCP tools.\n"
            "Rules:\n"
            "- Long-only.\n"
            "- Place exactly ONE order matching the provided order_spec (do not change price/qty/type).\n"
            "- Use the provided client_order_id.\n"
            "- After submitting, fetch the order details.\n"
            "Output JSON only with keys: ok, client_order_id, alpaca_order_id, status, filled_qty, avg_fill_price, raw_order.\n"
        )
        order_spec = {}
        try:
            if getattr(plan, "order", None) is not None and plan.order is not None:
                order_spec = plan.order.to_dict()
        except Exception:
            order_spec = {}
        user_prompt = json.dumps(
            {
                "trade_plan": plan.to_dict(),
                "order_spec": order_spec,
                "client_order_id": client_order_id,
            },
            ensure_ascii=False,
        )

        resp = await llm.send_message(user_prompt, system_message=system_prompt)
        text = (resp.content or "").strip()
        self.db.update_order_execution_output(plan_id=plan_id, raw_output=text)

        # Best-effort parse; store even if parsing fails.
        try:
            data = json.loads(text)
        except Exception:
            data = {"ok": False, "error": "non_json_response", "raw": text}

        alpaca_order_id = None
        status = None
        try:
            alpaca_order_id = str(data.get("alpaca_order_id") or "") or None
            status = str(data.get("status") or "") or None
        except Exception:
            pass

        self.db.finalize_order_submission(
            plan_id=plan_id,
            alpaca_order_id=alpaca_order_id,
            status=status or "submitted",
            parsed_json=data if isinstance(data, dict) else {"raw": data},
        )
        return {"ok": True, "client_order_id": client_order_id, "alpaca_order_id": alpaca_order_id, "status": status, "raw": data}

    async def reconcile_once(self) -> None:
        """
        Periodic reconciliation snapshot.

        Uses LLM to call MCP tools to list open orders/positions without hardcoding tool names.
        """
        for account_id in self.mcp_registry.account_ids():
            try:
                tools = await self.mcp_registry.get_tools(account_id=account_id)
                llm = LLMClient(
                    model=self.cfg.model,
                    temperature=0,
                    tools=tools,
                    enable_multi_stage_reasoning=True,
                    max_tool_iterations=4,
                    agent_name=self.agent_name,
                )
                system_prompt = (
                    "You are reconciling an Alpaca PAPER account.\n"
                    "Call the appropriate MCP tools to fetch open orders and current positions.\n"
                    "Return JSON only with keys: ok, account_id, ts, open_orders, positions.\n"
                )
                user_prompt = json.dumps({"account_id": account_id}, ensure_ascii=False)
                resp = await llm.send_message(user_prompt, system_message=system_prompt)
                txt = (resp.content or "").strip()
                try:
                    j = json.loads(txt)
                except Exception:
                    j = {"ok": False, "error": "non_json_response", "raw": txt}
                self.db.insert_reconcile_snapshot(account_id=account_id, snapshot=j)
            except Exception as e:
                if self.logger:
                    self.logger.warning(f"Reconcile failed account_id={account_id}: {e}")


