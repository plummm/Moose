from __future__ import annotations

import asyncio

from moose.agents.alpaca_trader.models import OrderSpec, TradePlan
from moose.agents.alpaca_trader.services.risk_gate import GlobalCaps, RiskCaps, RiskConfig, RiskGate


class _AlwaysOpen:
    async def is_market_open(self, _dt):  # noqa: ANN001
        return True


def _mk_gate() -> RiskGate:
    cfg = RiskConfig(
        mode="paper",
        trading_enabled=True,
        stocks=RiskCaps(15000, 75000, 0.2),
        crypto=RiskCaps(5000, 25000, 0.1),
        global_caps=GlobalCaps(40, 150),
    )
    return RiskGate(cfg=cfg, allow_all=True, allow_stocks=set(), allow_crypto=set(), market_hours=_AlwaysOpen(), logger=None)


def test_buying_power_rejects() -> None:
    rg = _mk_gate()
    plan = TradePlan(
        symbol="AAPL",
        asset_class="stock",
        side="buy",
        account_id="paper",
        strategy_id="news_momentum",
        confidence=0.6,
        rationale="",
        holding_period_hint="",
        sizing_guidance="",
        notional_usd_hint=2000,
        order=OrderSpec(order_type="limit", time_in_force="day", limit_price=100.0, notional_usd=2000),
    )
    acct = {"account": {"buying_power": 1000}}
    verdict = asyncio.run(rg.evaluate(plan, account_snapshot=acct))
    assert verdict.ok is False
    assert verdict.reason == "insufficient_buying_power"


def test_daily_notional_cap_rejects() -> None:
    rg = _mk_gate()
    plan = TradePlan(
        symbol="BTCUSD",
        asset_class="crypto",
        side="buy",
        account_id="paper",
        strategy_id="news_momentum",
        confidence=0.6,
        rationale="",
        holding_period_hint="",
        sizing_guidance="",
        notional_usd_hint=4000,
        order=OrderSpec(order_type="market", time_in_force="day", notional_usd=4000),
    )
    # crypto cap per day = 25000, used 24000 + 4000 => reject
    verdict = asyncio.run(rg.evaluate(plan, daily_notional_used_usd=24000))
    assert verdict.ok is False
    assert verdict.reason == "daily_notional_cap_exceeded"


def test_limit_price_cross_rejects() -> None:
    rg = _mk_gate()
    plan = TradePlan(
        symbol="AAPL",
        asset_class="stock",
        side="buy",
        account_id="paper",
        strategy_id="news_momentum",
        confidence=0.6,
        rationale="",
        holding_period_hint="",
        sizing_guidance="",
        notional_usd_hint=1000,
        order=OrderSpec(order_type="limit", time_in_force="day", limit_price=105.0, notional_usd=1000, max_slippage_bps=50),
    )
    mkt = {"quote": {"bid": 100.0, "ask": 101.0, "last": 100.5}, "spread_pct": 1.0}
    verdict = asyncio.run(rg.evaluate(plan, market_context=mkt))
    assert verdict.ok is False
    assert verdict.reason in ("limit_price_crosses_ask", "limit_price_too_high")


def test_spread_too_wide_rejects() -> None:
    rg = _mk_gate()
    plan = TradePlan(
        symbol="AAPL",
        asset_class="stock",
        side="buy",
        account_id="paper",
        strategy_id="news_momentum",
        confidence=0.6,
        rationale="",
        holding_period_hint="",
        sizing_guidance="",
        notional_usd_hint=1000,
        order=OrderSpec(order_type="limit", time_in_force="day", limit_price=100.0, notional_usd=1000, max_spread_pct=0.1),
    )
    mkt = {"quote": {"bid": 99.0, "ask": 101.0, "last": 100.0}, "spread_pct": 2.0}
    verdict = asyncio.run(rg.evaluate(plan, market_context=mkt))
    assert verdict.ok is False
    assert verdict.reason == "spread_too_wide"


def test_symbol_exposure_rejects() -> None:
    rg = _mk_gate()
    plan = TradePlan(
        symbol="AAPL",
        asset_class="stock",
        side="buy",
        account_id="paper",
        strategy_id="news_momentum",
        confidence=0.6,
        rationale="",
        holding_period_hint="",
        sizing_guidance="",
        notional_usd_hint=1000,
        order=OrderSpec(order_type="market", time_in_force="day", notional_usd=1000),
    )
    # equity 10k, exposure cap 20% => 2k. Existing 1500 + attempt 1000 => 2500 reject.
    acct = {"account": {"equity": 10000}, "positions": [{"symbol": "AAPL", "market_value": 1500}], "open_orders": []}
    verdict = asyncio.run(rg.evaluate(plan, account_snapshot=acct))
    assert verdict.ok is False
    assert verdict.reason == "max_symbol_exposure_exceeded"


