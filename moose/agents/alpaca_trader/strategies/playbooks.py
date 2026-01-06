from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass(frozen=True)
class StrategyPlaybook:
    """
    Lightweight strategy definition.

    We keep this as metadata + prompt guidance. The LLM/rules engine can use it
    to choose holding period guidance, sizing hints, and what to look for in research.
    """

    strategy_id: str
    title: str
    description: str
    sizing_guidance: str
    holding_guidance: str


def default_playbooks() -> Dict[str, StrategyPlaybook]:
    return {
        "news_momentum": StrategyPlaybook(
            strategy_id="news_momentum",
            title="News momentum",
            description="Trade strong catalysts and narrative momentum; avoid weak/noisy headlines.",
            sizing_guidance="Base size: moderate. Increase only if confidence high and liquidity is good.",
            holding_guidance="Typically hours to a few days; exit if the catalyst fades or price reverses sharply.",
        ),
        "event_driven": StrategyPlaybook(
            strategy_id="event_driven",
            title="Event-driven",
            description="Trade around structured events (earnings, guidance, macro).",
            sizing_guidance="Prefer smaller initial size; add only after confirmation.",
            holding_guidance="From intraday to a few days; exit after event is absorbed or thesis invalidates.",
        ),
        "trend_breakout": StrategyPlaybook(
            strategy_id="trend_breakout",
            title="Trend/breakout",
            description="Join clear breakouts/continuations; avoid chop.",
            sizing_guidance="Start small; pyramid only with confirmation.",
            holding_guidance="Days to weeks; exit on trend break / regime change.",
        ),
        "mean_reversion": StrategyPlaybook(
            strategy_id="mean_reversion",
            title="Mean reversion (fade overreaction)",
            description="Fade overreactions and normalize to fair value; requires clear evidence of overshoot.",
            sizing_guidance="Smaller size; scale in carefully. Avoid catching falling knives without confirmation.",
            holding_guidance="Hours to days; exit quickly if bounce fails.",
        ),
        "risk_regime_overlay": StrategyPlaybook(
            strategy_id="risk_regime_overlay",
            title="Risk-regime overlay",
            description="Adjust aggressiveness based on market regime (panic/greed).",
            sizing_guidance="In panic: smaller entries, prefer quality. In greed: avoid chasing, tighten entries.",
            holding_guidance="Varies; use as modifier to other strategies.",
        ),
    }


