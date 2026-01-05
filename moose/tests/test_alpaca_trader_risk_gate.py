from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone

from moose.agents.alpaca_trader.models import TradePlan
from moose.agents.alpaca_trader.services.risk_gate import GlobalCaps, RiskCaps, RiskConfig, RiskGate


@dataclass
class DummyMarketHours:
    open_now: bool = True

    async def is_market_open(self, now: datetime | None = None) -> bool:  # noqa: ARG002
        return bool(self.open_now)


def test_risk_gate_rejects_non_allowlisted_stock() -> None:
    g = RiskConfig(
        mode="paper",
        trading_enabled=True,
        stocks=RiskCaps(15000, 75000, 0.2),
        crypto=RiskCaps(5000, 25000, 0.1),
        global_caps=GlobalCaps(40, 150),
    )
    rg = RiskGate(cfg=g, allow_all=False, allow_stocks={"AAPL"}, allow_crypto=set(), market_hours=DummyMarketHours(), logger=None)
    plan = TradePlan(
        symbol="MSFT",
        asset_class="stock",
        side="buy",
        account_id="default",
        strategy_id="news_momentum",
        confidence=0.5,
        rationale="",
        holding_period_hint="",
        sizing_guidance="",
        notional_usd_hint=1000,
    )
    verdict = asyncio.run(rg.evaluate(plan))
    assert verdict.ok is False
    assert "allowlisted" in verdict.reason


def test_risk_gate_blocks_market_closed_stocks() -> None:
    g = RiskConfig(
        mode="paper",
        trading_enabled=True,
        stocks=RiskCaps(15000, 75000, 0.2),
        crypto=RiskCaps(5000, 25000, 0.1),
        global_caps=GlobalCaps(40, 150),
    )
    rg = RiskGate(cfg=g, allow_all=False, allow_stocks={"AAPL"}, allow_crypto=set(), market_hours=DummyMarketHours(open_now=False), logger=None)
    plan = TradePlan(
        symbol="AAPL",
        asset_class="stock",
        side="buy",
        account_id="default",
        strategy_id="news_momentum",
        confidence=0.5,
        rationale="",
        holding_period_hint="",
        sizing_guidance="",
        notional_usd_hint=1000,
    )
    verdict = asyncio.run(rg.evaluate(plan))
    assert verdict.ok is False
    assert verdict.reason == "market_closed"


def test_risk_gate_caps_notional() -> None:
    g = RiskConfig(
        mode="paper",
        trading_enabled=True,
        stocks=RiskCaps(15000, 75000, 0.2),
        crypto=RiskCaps(5000, 25000, 0.1),
        global_caps=GlobalCaps(40, 150),
    )
    rg = RiskGate(cfg=g, allow_all=False, allow_stocks=set(), allow_crypto={"BTCUSD"}, market_hours=None, logger=None)
    plan = TradePlan(
        symbol="BTCUSD",
        asset_class="crypto",
        side="buy",
        account_id="default",
        strategy_id="news_momentum",
        confidence=0.5,
        rationale="",
        holding_period_hint="",
        sizing_guidance="",
        notional_usd_hint=999999,
    )
    verdict = asyncio.run(rg.evaluate(plan))
    assert verdict.ok is True
    assert verdict.adjusted_plan is not None
    assert verdict.adjusted_plan.notional_usd_hint == 5000


def test_risk_gate_allow_all_bypasses_allowlist() -> None:
    g = RiskConfig(
        mode="paper",
        trading_enabled=True,
        stocks=RiskCaps(15000, 75000, 0.2),
        crypto=RiskCaps(5000, 25000, 0.1),
        global_caps=GlobalCaps(40, 150),
    )
    rg = RiskGate(cfg=g, allow_all=True, allow_stocks=set(), allow_crypto=set(), market_hours=DummyMarketHours(open_now=True), logger=None)
    plan = TradePlan(
        symbol="SOME",
        asset_class="stock",
        side="buy",
        account_id="default",
        strategy_id="news_momentum",
        confidence=0.5,
        rationale="",
        holding_period_hint="",
        sizing_guidance="",
        notional_usd_hint=1000,
    )
    verdict = asyncio.run(rg.evaluate(plan))
    assert verdict.ok is True


