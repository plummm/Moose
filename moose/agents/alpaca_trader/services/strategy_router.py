from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from moose.framework.llm_core import LLMClient

try:  # Moose mode
    from moose.agents.alpaca_trader.models import TradePlan
    from moose.agents.alpaca_trader.strategies.playbooks import default_playbooks
except Exception:  # Standalone mode
    from models import TradePlan
    from strategies.playbooks import default_playbooks


@dataclass(frozen=True)
class StrategyRouterConfig:
    default_account_id: str = "default"
    # If False, router will use heuristics only (no LLM).
    llm_enabled: bool = True


class StrategyRouter:
    """
    Produce an initial TradePlan for a symbol using a strategy playbook.

    Long-only; this component is allowed to output HOLD when uncertain.
    """

    def __init__(self, *, cfg: StrategyRouterConfig, llm: Optional[LLMClient], logger: Any):
        self.cfg = cfg
        self.llm = llm
        self.logger = logger
        self.playbooks = default_playbooks()

    def _choose_strategy_id(self, *, event_text: str) -> str:
        t = (event_text or "").lower()
        if any(w in t for w in ["earnings", "guidance", "cpi", "fed", "rates"]):
            return "event_driven"
        if any(w in t for w in ["breakout", "new high", "trend"]):
            return "trend_breakout"
        if any(w in t for w in ["overreaction", "gap down", "gap up", "panic", "crash"]):
            return "mean_reversion"
        return "news_momentum"

    async def build_trade_plan(
        self,
        *,
        symbol: str,
        asset_class: str,
        event_text: str,
        regime_score: float,
        watch_score: float,
        account_id: Optional[str] = None,
        strategy_hint: Optional[str] = None,
        created_from_event: Optional[str] = None,
    ) -> TradePlan:
        acct = (account_id or "").strip() or self.cfg.default_account_id

        # Strategy selection (hint wins).
        strategy_id = (strategy_hint or "").strip() or self._choose_strategy_id(event_text=event_text)
        if strategy_id not in self.playbooks:
            strategy_id = "news_momentum"
        pb = self.playbooks[strategy_id]

        # Baseline (heuristic) plan.
        # Default to HOLD unless attention is high.
        side = "hold"
        conf = 0.25
        notional_hint = None
        rationale = (
            f"Initial heuristic plan. regime_score={regime_score:.1f}, watch_score={watch_score:.1f}. "
            f"Strategy={pb.title}. Event='{(event_text or '').strip()[:240]}'"
        )

        if watch_score >= 50:
            side = "buy"
            conf = 0.45
        if watch_score >= 80:
            side = "buy"
            conf = 0.60
        if regime_score <= -70 and side == "buy":
            # panic regime: be more conservative by default
            conf = max(0.35, conf - 0.15)

        # Optional LLM refinement.
        if self.cfg.llm_enabled and self.llm is not None:
            try:
                prompt = (
                    "You are a trading strategy router for a paper-trading system.\n"
                    "Constraints:\n"
                    "- Long-only.\n"
                    "- Output MUST be valid JSON with keys: side (buy|sell|hold), confidence (0..1), "
                    "holding_period_hint, rationale, notional_usd_hint (number|null), strategy_id.\n"
                    "- Prefer HOLD if uncertain.\n\n"
                    f"symbol: {symbol}\n"
                    f"asset_class: {asset_class}\n"
                    f"regime_score (-100..100): {regime_score}\n"
                    f"watch_score (0..100): {watch_score}\n"
                    f"strategy_playbook_id: {pb.strategy_id}\n"
                    f"strategy_playbook_title: {pb.title}\n"
                    f"strategy_playbook_description: {pb.description}\n"
                    f"sizing_guidance: {pb.sizing_guidance}\n"
                    f"holding_guidance: {pb.holding_guidance}\n"
                    f"event_text: {event_text}\n"
                )
                resp = await self.llm.send_message(prompt)
                # Best-effort parse without adding dependencies.
                import json as _json

                j = _json.loads((resp.content or "").strip())
                side = str(j.get("side") or side).strip().lower()
                if side not in {"buy", "sell", "hold"}:
                    side = "hold"
                try:
                    conf = float(j.get("confidence", conf))
                except Exception:
                    pass
                conf = max(0.0, min(1.0, conf))
                holding_hint = str(j.get("holding_period_hint") or pb.holding_guidance).strip() or pb.holding_guidance
                rationale = str(j.get("rationale") or rationale).strip() or rationale
                nh = j.get("notional_usd_hint", None)
                if nh is None:
                    notional_hint = None
                else:
                    try:
                        notional_hint = float(nh)
                    except Exception:
                        notional_hint = None
                sid = str(j.get("strategy_id") or strategy_id).strip()
                if sid in self.playbooks:
                    strategy_id = sid
                    pb = self.playbooks[strategy_id]
            except Exception as e:
                if self.logger:
                    self.logger.warning(f"LLM strategy routing failed; using heuristic plan. err={e}")
                holding_hint = pb.holding_guidance
        else:
            holding_hint = pb.holding_guidance

        return TradePlan(
            symbol=symbol,
            asset_class=("crypto" if str(asset_class) == "crypto" else "stock"),
            side=side,  # type: ignore[arg-type]
            account_id=acct,
            strategy_id=strategy_id,
            confidence=float(conf),
            rationale=rationale,
            holding_period_hint=holding_hint,
            sizing_guidance=pb.sizing_guidance,
            notional_usd_hint=notional_hint,
            created_from_event=created_from_event,
        )


