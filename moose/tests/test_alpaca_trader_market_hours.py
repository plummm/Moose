from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, timezone

from moose.agents.alpaca_trader.services.market_hours import MarketHoursConfig, MarketHoursService


@dataclass
class DummyFmp:
    closed_dates: set[str]

    async def holidays_by_exchange(self, exchange: str, start_date: str, end_date: str):  # noqa: ARG002
        # Return a list of records like FMP does.
        out = []
        for d in self.closed_dates:
            out.append({"date": d, "isClosed": True})
        return out


def test_market_open_skips_holiday() -> None:
    # Mark 2026-01-01 as closed
    fmp = DummyFmp(closed_dates={"2026-01-01"})
    svc = MarketHoursService(fmp=fmp, cfg=MarketHoursConfig(exchange="NASDAQ"), logger=None)

    # 2026-01-01 10:00 NY -> market should be closed due to holiday
    dt = datetime(2026, 1, 1, 15, 0, tzinfo=timezone.utc)  # 10:00 NY
    assert asyncio.run(svc.is_market_open(dt)) is False


