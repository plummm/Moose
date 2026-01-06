from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Optional
from zoneinfo import ZoneInfo

from .fmp_client import FmpClient


NY_TZ = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class MarketHoursConfig:
    """
    Stock market-hours gating config.

    We currently model a simple NYSE/NASDAQ-like schedule:
    - open: 09:30 NY time
    - close: 16:00 NY time
    - open days: weekdays excluding `holidays-by-exchange` closed days from FMP

    This matches telegram_stock_bot behavior.
    """

    exchange: str = "NASDAQ"
    open_hh: int = 9
    open_mm: int = 30
    close_hh: int = 16
    close_mm: int = 0
    holiday_cache_days: int = 14


class MarketHoursService:
    def __init__(self, *, fmp: FmpClient, cfg: MarketHoursConfig, logger: Any):
        self.fmp = fmp
        self.cfg = cfg
        self.logger = logger

        self._holiday_closed_dates: set[str] = set()
        self._holiday_cache_day: str | None = None

    @staticmethod
    def _is_weekend(d: date) -> bool:
        return d.weekday() >= 5

    async def _refresh_holidays_if_needed(self, today: date) -> None:
        day_str = today.isoformat()
        if self._holiday_cache_day == day_str:
            return

        start = day_str
        end = (today + timedelta(days=max(7, int(self.cfg.holiday_cache_days or 14)))).isoformat()
        try:
            holidays = await self.fmp.holidays_by_exchange(self.cfg.exchange, start, end)
        except Exception as e:
            if self.logger:
                self.logger.warning(f"Failed to fetch holidays-by-exchange: {e}")
            holidays = []

        self._holiday_closed_dates = {
            h.get("date") for h in holidays if h.get("isClosed") is True and h.get("date")
        }
        self._holiday_cache_day = day_str

    async def is_open_day(self, d: date) -> bool:
        if self._is_weekend(d):
            return False
        await self._refresh_holidays_if_needed(d)
        return d.isoformat() not in self._holiday_closed_dates

    async def is_market_open(self, now: Optional[datetime] = None) -> bool:
        """
        Return True if stock market is open now (NY time), based on open/close times and FMP holidays.
        """
        now_ny = now.astimezone(NY_TZ) if now is not None else datetime.now(tz=NY_TZ)
        today = now_ny.date()
        if not (await self.is_open_day(today)):
            return False

        open_dt = datetime.combine(today, time(self.cfg.open_hh, self.cfg.open_mm), tzinfo=NY_TZ)
        close_dt = datetime.combine(today, time(self.cfg.close_hh, self.cfg.close_mm), tzinfo=NY_TZ)
        return open_dt <= now_ny < close_dt

    async def next_market_open(self, now: Optional[datetime] = None) -> datetime:
        """
        Return the next open datetime (NY tz).
        """
        now_ny = now.astimezone(NY_TZ) if now is not None else datetime.now(tz=NY_TZ)
        today = now_ny.date()

        open_dt_today = datetime.combine(today, time(self.cfg.open_hh, self.cfg.open_mm), tzinfo=NY_TZ)
        if (await self.is_open_day(today)) and now_ny < open_dt_today:
            return open_dt_today

        # Find next open day within a window.
        d = today + timedelta(days=1)
        horizon = today + timedelta(days=60)
        while d <= horizon:
            if await self.is_open_day(d):
                return datetime.combine(d, time(self.cfg.open_hh, self.cfg.open_mm), tzinfo=NY_TZ)
            d += timedelta(days=1)

        # Fallback: return tomorrow open.
        return datetime.combine(today + timedelta(days=1), time(self.cfg.open_hh, self.cfg.open_mm), tzinfo=NY_TZ)


