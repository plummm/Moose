from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from moose.framework.llm_core import LLMClient

try:
    # LangChain tool wrapper (preferred)
    from langchain_core.tools import StructuredTool  # type: ignore
except Exception:  # pragma: no cover
    StructuredTool = None  # type: ignore

try:  # Moose mode
    from moose.agents.alpaca_trader.services.finance_office_client import FinanceOfficeClient
except Exception:  # Standalone mode
    from services.finance_office_client import FinanceOfficeClient


@dataclass(frozen=True)
class SpecialistToolConfig:
    model: str
    temperature: float = 0.2
    max_tool_iterations: int = 4


def _as_float(v: Any, default: float) -> float:
    try:
        return float(v)
    except Exception:
        return float(default)


def _as_int(v: Any, default: int) -> int:
    try:
        return int(v)
    except Exception:
        return int(default)


async def _llm_json(
    *,
    model: str,
    system: str,
    user: str,
    temperature: float,
    max_tool_iterations: int,
    tools: Optional[List[Any]] = None,
    agent_name: str,
) -> Dict[str, Any]:
    llm = LLMClient(
        model=str(model),
        temperature=float(temperature),
        tools=(tools or None),
        enable_multi_stage_reasoning=bool(tools),
        max_tool_iterations=int(max_tool_iterations),
        agent_name=str(agent_name),
    )
    resp = await llm.send_message(user, system_message=system)
    txt = (resp.content or "").strip()
    try:
        j = json.loads(txt)
    except Exception:
        j = {"ok": False, "error": "non_json_response", "raw": txt}
    return j if isinstance(j, dict) else {"ok": False, "error": "invalid_json_type", "raw": j}


def _mk_tool(*, name: str, description: str, coroutine) -> Any:
    if StructuredTool is None:
        raise RuntimeError("langchain_core StructuredTool not available")
    return StructuredTool.from_function(name=name, description=description, coroutine=coroutine)


def create_specialist_tools(
    *,
    agent_name: str,
    logger: Any,
    db: Any,  # TraderDb
    alpaca_mcp: Any,  # AlpacaMcpToolRegistry
    finance_office_endpoint: str,
    planning_cfg: Dict[str, Any],
) -> List[Any]:
    """
    Return a list of specialist tools for the V2 planning controller.

    Each tool returns strict JSON objects and is designed to be invoked by an LLM controller.
    """
    finance_client = FinanceOfficeClient(endpoint=str(finance_office_endpoint or ""), logger=logger, timeout_s=180.0)

    specialist_models = (
        planning_cfg.get("specialist_models")
        if isinstance(planning_cfg.get("specialist_models"), dict)
        else {}
    )
    default_model = str(planning_cfg.get("controller_model") or "").strip() or str(planning_cfg.get("model") or "").strip()
    default_temperature = _as_float(planning_cfg.get("temperature", 0.2), 0.2)
    default_iters = _as_int(planning_cfg.get("max_tool_iterations", 4), 4)

    def _tool_cfg(key: str, *, temp_default: float) -> SpecialistToolConfig:
        m = str(specialist_models.get(key) or "").strip() or default_model
        t = _as_float((planning_cfg.get("specialist_temperatures") or {}).get(key, temp_default) if isinstance(planning_cfg.get("specialist_temperatures"), dict) else temp_default, temp_default)
        it = _as_int((planning_cfg.get("specialist_max_tool_iterations") or {}).get(key, default_iters) if isinstance(planning_cfg.get("specialist_max_tool_iterations"), dict) else default_iters, default_iters)
        return SpecialistToolConfig(model=m, temperature=t, max_tool_iterations=it)

    cfg_data = _tool_cfg("data", temp_default=0.0)
    cfg_thesis = _tool_cfg("thesis", temp_default=0.2)
    cfg_entry = _tool_cfg("entry", temp_default=0.2)
    cfg_size = _tool_cfg("size", temp_default=0.2)

    async def fetch_account_snapshot(account_id: str) -> Dict[str, Any]:
        acct = str(account_id or "").strip()
        if not acct:
            ids = alpaca_mcp.account_ids() if alpaca_mcp is not None else []
            acct = ids[0] if ids else ""
        if not acct:
            return {"ok": False, "error": "missing_account_id"}

        tools = await alpaca_mcp.get_tools(account_id=acct)
        system = (
            "You are a data fetcher for Alpaca PAPER trading.\n"
            "Call tools to retrieve:\n"
            "- account state (cash/buying_power/equity if available)\n"
            "- current positions\n"
            "- open orders\n"
            "Return JSON only with keys: ok, account_id, ts, account, positions, open_orders.\n"
        )
        user = f"account_id: {acct}"
        j = await _llm_json(
            model=cfg_data.model,
            system=system,
            user=user,
            temperature=float(cfg_data.temperature),
            max_tool_iterations=int(cfg_data.max_tool_iterations),
            tools=tools,
            agent_name=agent_name,
        )
        out = {"ok": True, "account_id": acct, "ts": time.time(), **(j if isinstance(j, dict) else {"raw": j})}
        try:
            if db is not None:
                db.insert_reconcile_snapshot(account_id=acct, snapshot=out)
        except Exception:
            pass
        return out

    async def fetch_market_context(symbol: str, asset_class: str, account_id: Optional[str] = None) -> Dict[str, Any]:
        sym = str(symbol or "").strip().upper()
        if not sym:
            return {"ok": False, "error": "missing_symbol"}
        acct = str(account_id or "").strip()
        if not acct:
            ids = alpaca_mcp.account_ids() if alpaca_mcp is not None else []
            acct = ids[0] if ids else ""
        tools = await alpaca_mcp.get_tools(account_id=acct) if acct else []

        system = (
            "You are a market data fetcher for Alpaca PAPER trading.\n"
            "Call tools to retrieve best-effort market context for the given symbol:\n"
            "- latest quote (bid/ask/last)\n"
            "- latest trade price if separate\n"
            "- basic recent bars if available\n"
            "Compute and include spread_pct if bid/ask present.\n"
            "Return JSON only with keys: ok, symbol, asset_class, ts, quote, bars, spread_pct.\n"
        )
        user = f"symbol: {sym}\nasset_class: {str(asset_class or '').strip()}"
        j = await _llm_json(
            model=cfg_data.model,
            system=system,
            user=user,
            temperature=float(cfg_data.temperature),
            max_tool_iterations=int(cfg_data.max_tool_iterations),
            tools=tools or None,
            agent_name=agent_name,
        )
        out = {"ok": True, "symbol": sym, "asset_class": str(asset_class or ""), "ts": time.time(), **(j if isinstance(j, dict) else {"raw": j})}
        try:
            if db is not None:
                db.insert_market_snapshot(symbol=sym, snapshot=out)
        except Exception:
            pass
        return out

    async def fetch_research_packet(symbol: str, event_text: str) -> Dict[str, Any]:
        sym = str(symbol or "").strip().upper()
        if not sym:
            return {"ok": False, "error": "missing_symbol"}
        if not str(finance_office_endpoint or "").strip():
            return {"ok": False, "error": "finance_office_endpoint_not_configured"}

        instruction = (
            "Create a compact trade research packet for this event and symbol.\n"
            "Return JSON with keys: summary, catalysts, key_risks, sentiment (bullish|bearish|mixed|unknown), "
            "related_symbols (list), time_horizon.\n"
            "Be concise and do not hallucinate numbers."
        )
        context = f"symbol: {sym}\nevent_text: {str(event_text or '').strip()[:4000]}"
        analyzer_data = {"symbol": sym, "source": "alpaca_trader_v2"}
        data = await finance_client.run_task(
            instruction=instruction,
            context=context,
            analyzer_data=analyzer_data,
            granularity="minimal",
        )
        out = {"ok": True, "symbol": sym, "ts": time.time(), "packet": data}
        try:
            if db is not None:
                db.insert_research_packet(symbol=sym, source="finance_office", packet=out)
        except Exception:
            pass
        return out

    async def build_trade_thesis(
        symbol: str,
        asset_class: str,
        event_text: str,
        regime_score: float,
        watch_score: float,
        research_packet: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        sym = str(symbol or "").strip().upper()
        system = (
            "You are a trade-thesis specialist for a PAPER long-only system.\n"
            "Output JSON only with keys: action (buy|hold|sell), confidence (0..1), holding_period, thesis, invalidation, key_risks.\n"
            "Prefer HOLD if uncertain. Do not mention order types or sizing here.\n"
        )
        user = json.dumps(
            {
                "symbol": sym,
                "asset_class": str(asset_class or ""),
                "event_text": str(event_text or "")[:6000],
                "regime_score": float(regime_score),
                "watch_score": float(watch_score),
                "research_packet": research_packet,
            },
            ensure_ascii=False,
        )
        return await _llm_json(
            model=cfg_thesis.model,
            system=system,
            user=user,
            temperature=float(cfg_thesis.temperature),
            max_tool_iterations=int(cfg_thesis.max_tool_iterations),
            tools=None,
            agent_name=agent_name,
        )

    async def design_entry_and_order_type(
        symbol: str,
        side: str,
        market_context: Dict[str, Any],
        urgency: str = "normal",
    ) -> Dict[str, Any]:
        sym = str(symbol or "").strip().upper()
        system = (
            "You are an entry-design specialist for a PAPER long-only system.\n"
            "Goal: produce a LIMIT entry plan and a safe limit price.\n"
            "Output JSON only with keys: order_type (limit|market), time_in_force (day|gtc), limit_price (number|null), "
            "max_spread_pct (number|null), max_slippage_bps (number|null), entry_rules.\n"
            "Prefer limit orders. If bid/ask is missing, you may fall back to market but explain in entry_rules.\n"
        )
        user = json.dumps(
            {"symbol": sym, "side": str(side or ""), "urgency": str(urgency or "normal"), "market_context": market_context},
            ensure_ascii=False,
        )
        return await _llm_json(
            model=cfg_entry.model,
            system=system,
            user=user,
            temperature=float(cfg_entry.temperature),
            max_tool_iterations=int(cfg_entry.max_tool_iterations),
            tools=None,
            agent_name=agent_name,
        )

    async def compute_position_size(
        symbol: str,
        side: str,
        confidence: float,
        account_snapshot: Dict[str, Any],
        risk_caps: Dict[str, Any],
    ) -> Dict[str, Any]:
        sym = str(symbol or "").strip().upper()
        system = (
            "You are a position sizing specialist for a PAPER long-only system.\n"
            "Output JSON only with keys: notional_usd (number), qty (number|null), sizing_rationale.\n"
            "Use buying_power/cash if present; otherwise size conservatively.\n"
        )
        user = json.dumps(
            {
                "symbol": sym,
                "side": str(side or ""),
                "confidence": float(confidence),
                "account_snapshot": account_snapshot,
                "risk_caps": risk_caps,
            },
            ensure_ascii=False,
        )
        return await _llm_json(
            model=cfg_size.model,
            system=system,
            user=user,
            temperature=float(cfg_size.temperature),
            max_tool_iterations=int(cfg_size.max_tool_iterations),
            tools=None,
            agent_name=agent_name,
        )

    tools_out = [
        _mk_tool(
            name="fetch_account_snapshot",
            description="Fetch account state + positions + open orders for an account_id (via Alpaca MCP) and persist snapshot.",
            coroutine=fetch_account_snapshot,
        ),
        _mk_tool(
            name="fetch_market_context",
            description="Fetch market context (quote/bars/spread) for a symbol using Alpaca MCP and persist snapshot.",
            coroutine=fetch_market_context,
        ),
        _mk_tool(
            name="fetch_research_packet",
            description="Fetch a compact research packet from finance_office /run_task for a symbol+event_text.",
            coroutine=fetch_research_packet,
        ),
        _mk_tool(
            name="build_trade_thesis",
            description="Build a trade thesis (buy|hold|sell) with confidence + invalidation + key risks (no sizing, no order type).",
            coroutine=build_trade_thesis,
        ),
        _mk_tool(
            name="design_entry_and_order_type",
            description="Design a safe entry plan and order type (prefer limit) using market context; returns limit_price/TIF and safety thresholds.",
            coroutine=design_entry_and_order_type,
        ),
        _mk_tool(
            name="compute_position_size",
            description="Compute notional_usd (and optional qty) using account snapshot and risk caps.",
            coroutine=compute_position_size,
        ),
    ]

    # Ensure finance client is closed on process exit when possible (best-effort).
    try:
        setattr(finance_client, "_alpaca_trader_keepalive", True)
    except Exception:
        pass

    return tools_out



