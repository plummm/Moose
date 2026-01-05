from __future__ import annotations

import asyncio

from moose.agents.alpaca_trader.services.finance_office_client import FinanceOfficeClient, _normalize_run_task_endpoint
from moose.agents.alpaca_trader.services.plan_controller import PlanController, PlanControllerConfig


def test_finance_office_endpoint_normalization() -> None:
    assert _normalize_run_task_endpoint("http://x") == "http://x/run_task"
    assert _normalize_run_task_endpoint("http://x/") == "http://x/run_task"
    assert _normalize_run_task_endpoint("http://x/run_task") == "http://x/run_task"


def test_plan_controller_parses_tradeplan_and_orderspec(monkeypatch) -> None:
    # Patch LLMClient.send_message to avoid real model calls.
    from moose.framework.llm_core import client as llm_client_mod

    async def _fake_send_message(self, message, system_message=None, **kwargs):  # noqa: ARG001
        class R:
            content = (
                "{"
                '"ok": true,'
                '"trade_plan": {"symbol":"AAPL","asset_class":"stock","side":"buy","account_id":"paper","strategy_id":"news_momentum",'
                '"confidence":0.55,"rationale":"x","holding_period_hint":"1-3d","sizing_guidance":"small","created_from_event":null},'
                '"order": {"order_type":"limit","time_in_force":"day","limit_price":100.0,"notional_usd":1000.0,'
                '"qty":null,"max_spread_pct":0.5,"max_slippage_bps":50}'
                "}"
            )

        return R()

    monkeypatch.setattr(llm_client_mod.LLMClient, "send_message", _fake_send_message, raising=True)

    pc = PlanController(cfg=PlanControllerConfig(model="gpt-4o", temperature=0.0, max_tool_iterations=2), tools=[], logger=None, agent_name="alpaca_trader")
    plan = asyncio.run(
        pc.build_trade_plan(
            symbol="AAPL",
            asset_class="stock",
            event_text="Breaking news",
            regime_score=0.0,
            watch_score=80.0,
            account_id="paper",
            risk_caps={"max_notional_per_trade_usd": 15000},
            created_from_event="evt",
        )
    )
    assert plan.symbol == "AAPL"
    assert plan.side == "buy"
    assert plan.order is not None
    assert plan.order.order_type == "limit"
    assert plan.order.limit_price == 100.0
    assert plan.notional_usd_hint == 1000.0


