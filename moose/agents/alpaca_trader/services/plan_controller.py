from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, Optional

from moose.framework.llm_core import LLMClient

try:  # Moose mode
    from moose.agents.alpaca_trader.models import OrderSpec, TradePlan
    from moose.agents.alpaca_trader.strategies.playbooks import default_playbooks
except Exception:  # Standalone mode
    from models import OrderSpec, TradePlan
    from strategies.playbooks import default_playbooks


def _extract_json_object(text: str) -> Optional[Dict[str, Any]]:
    s = (text or "").strip()
    if not s:
        return None
    start = s.find("{")
    end = s.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        obj = json.loads(s[start : end + 1])
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


@dataclass(frozen=True)
class PlanControllerConfig:
    model: str
    temperature: float = 0.2
    max_tool_iterations: int = 8


class PlanController:
    """
    V2 planning controller (LLM) that orchestrates specialist tools and emits a concrete TradePlan+OrderSpec.
    """

    def __init__(self, *, cfg: PlanControllerConfig, tools: list[Any], logger: Any, agent_name: str):
        self.cfg = cfg
        self.tools = tools
        self.logger = logger
        self.agent_name = agent_name
        self.playbooks = default_playbooks()

    def _system_prompt(self) -> str:
        allowed_strategies = sorted(self.playbooks.keys())
        return (
            "You are the V2 trading plan controller for a PAPER long-only system.\n"
            "Your job: orchestrate specialist tools and produce ONE final JSON plan.\n"
            "Rules:\n"
            "- Long-only: do not propose opening a short.\n"
            "- Prefer HOLD if uncertain.\n"
            "- Use specialist tools for data/thesis/entry/sizing.\n"
            "- Output JSON only.\n"
            "Required final JSON schema:\n"
            "{\n"
            '  \"ok\": true|false,\n'
            '  \"trade_plan\": {\n'
            '    \"symbol\": str,\n'
            '    \"asset_class\": \"stock\"|\"crypto\",\n'
            '    \"side\": \"buy\"|\"sell\"|\"hold\",\n'
            '    \"account_id\": str,\n'
            '    \"strategy_id\": str,\n'
            '    \"confidence\": number,\n'
            '    \"rationale\": str,\n'
            '    \"holding_period_hint\": str,\n'
            '    \"sizing_guidance\": str,\n'
            '    \"created_from_event\": str|null\n'
            "  },\n"
            '  \"order\": {\n'
            '    \"order_type\": \"limit\"|\"market\",\n'
            '    \"time_in_force\": \"day\"|\"gtc\",\n'
            '    \"limit_price\": number|null,\n'
            '    \"notional_usd\": number|null,\n'
            '    \"qty\": number|null,\n'
            '    \"max_spread_pct\": number|null,\n'
            '    \"max_slippage_bps\": number|null\n'
            "  }\n"
            "}\n"
            f"Allowed strategy_id values: {allowed_strategies}\n"
        )

    async def build_trade_plan(
        self,
        *,
        symbol: str,
        asset_class: str,
        event_text: str,
        regime_score: float,
        watch_score: float,
        account_id: str,
        risk_caps: Dict[str, Any],
        created_from_event: Optional[str] = None,
    ) -> TradePlan:
        sym = str(symbol or "").strip().upper()
        acct = str(account_id or "").strip()
        ac = "crypto" if str(asset_class) == "crypto" else "stock"

        sizing_guidance = self.playbooks.get("news_momentum").sizing_guidance
        holding_guidance = self.playbooks.get("news_momentum").holding_guidance

        user_payload = {
            "symbol": sym,
            "asset_class": ac,
            "account_id": acct,
            "event_text": str(event_text or "")[:8000],
            "regime_score": float(regime_score),
            "watch_score": float(watch_score),
            "risk_caps": risk_caps,
            "created_from_event": (str(created_from_event) if created_from_event is not None else None),
            "default_guidance": {"sizing_guidance": sizing_guidance, "holding_guidance": holding_guidance},
            "instructions": [
                "1) fetch_account_snapshot(account_id)",
                "2) fetch_market_context(symbol, asset_class, account_id)",
                "3) fetch_research_packet(symbol, event_text)",
                "4) build_trade_thesis(symbol, asset_class, event_text, regime_score, watch_score, research_packet)",
                "5) design_entry_and_order_type(symbol, side, market_context)",
                "6) compute_position_size(symbol, side, confidence, account_snapshot, risk_caps)",
                "7) produce final JSON plan",
            ],
        }

        llm = LLMClient(
            model=str(self.cfg.model),
            temperature=float(self.cfg.temperature),
            tools=self.tools,
            enable_multi_stage_reasoning=True,
            max_tool_iterations=int(self.cfg.max_tool_iterations),
            agent_name=self.agent_name,
        )
        resp = await llm.send_message(
            json.dumps(user_payload, ensure_ascii=False),
            system_message=self._system_prompt(),
        )
        j = _extract_json_object(resp.content or "")
        if not j:
            # Fallback: HOLD
            return TradePlan(
                symbol=sym,
                asset_class=ac,  # type: ignore[arg-type]
                side="hold",
                account_id=acct,
                strategy_id="news_momentum",
                confidence=0.25,
                rationale="V2 controller returned non-JSON; fallback HOLD.",
                holding_period_hint=holding_guidance,
                sizing_guidance=sizing_guidance,
                notional_usd_hint=None,
                order=None,
                created_from_event=(str(created_from_event) if created_from_event is not None else None),
            )

        tp = j.get("trade_plan") if isinstance(j.get("trade_plan"), dict) else {}
        od = j.get("order") if isinstance(j.get("order"), dict) else {}

        side = str(tp.get("side") or "hold").strip().lower()
        if side not in ("buy", "sell", "hold"):
            side = "hold"
        strategy_id = str(tp.get("strategy_id") or "news_momentum").strip()
        if strategy_id not in self.playbooks:
            strategy_id = "news_momentum"
        try:
            conf = float(tp.get("confidence", 0.25))
        except Exception:
            conf = 0.25
        conf = max(0.0, min(1.0, conf))
        rationale = str(tp.get("rationale") or "").strip() or "V2 plan."
        holding = str(tp.get("holding_period_hint") or holding_guidance).strip() or holding_guidance
        sizing = str(tp.get("sizing_guidance") or sizing_guidance).strip() or sizing_guidance

        # OrderSpec (optional)
        order = None
        try:
            order_type = str(od.get("order_type") or "").strip().lower()
            tif = str(od.get("time_in_force") or "day").strip().lower()
            if order_type not in ("limit", "market"):
                order_type = "limit"
            if tif not in ("day", "gtc"):
                tif = "day"
            limit_price = od.get("limit_price", None)
            try:
                limit_price_f = float(limit_price) if limit_price is not None else None
            except Exception:
                limit_price_f = None
            notional = od.get("notional_usd", None)
            try:
                notional_f = float(notional) if notional is not None else None
            except Exception:
                notional_f = None
            qty = od.get("qty", None)
            try:
                qty_f = float(qty) if qty is not None else None
            except Exception:
                qty_f = None
            msp = od.get("max_spread_pct", None)
            try:
                msp_f = float(msp) if msp is not None else None
            except Exception:
                msp_f = None
            msb = od.get("max_slippage_bps", None)
            try:
                msb_f = float(msb) if msb is not None else None
            except Exception:
                msb_f = None

            order = OrderSpec(
                order_type=order_type,  # type: ignore[arg-type]
                time_in_force=tif,  # type: ignore[arg-type]
                limit_price=limit_price_f,
                notional_usd=notional_f,
                qty=qty_f,
                max_spread_pct=msp_f,
                max_slippage_bps=msb_f,
            )
        except Exception:
            order = None

        # Keep legacy notional_usd_hint populated for RiskGate/execution back-compat.
        notional_hint = None
        if order is not None and isinstance(order.notional_usd, (int, float)):
            notional_hint = float(order.notional_usd)

        return TradePlan(
            symbol=sym,
            asset_class=ac,  # type: ignore[arg-type]
            side=side,  # type: ignore[arg-type]
            account_id=acct,
            strategy_id=strategy_id,
            confidence=float(conf),
            rationale=rationale,
            holding_period_hint=holding,
            sizing_guidance=sizing,
            notional_usd_hint=notional_hint,
            order=order,
            created_from_event=(str(created_from_event) if created_from_event is not None else None),
        )



