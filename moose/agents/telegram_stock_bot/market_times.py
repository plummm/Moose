from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo


NY_TZ = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class MarketClock:
    now_ny: datetime

    @property
    def ny_date(self) -> date:
        return self.now_ny.date()


def now_in_ny() -> MarketClock:
    return MarketClock(now_ny=datetime.now(tz=NY_TZ))


def is_weekend(d: date) -> bool:
    return d.weekday() >= 5


def near_time(now: datetime, hh: int, mm: int, window_seconds: int = 60) -> bool:
    target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    delta = abs((now - target).total_seconds())
    return delta <= window_seconds


def next_open_phrase(today: date, next_open: date) -> str:
    """
    Phrase rules (NY market schedule):
    - tomorrow
    - this Monday-Friday
    - Next Monday-Friday
    """
    if next_open == today + timedelta(days=1):
        return "tomorrow"

    # Same ISO week?
    if today.isocalendar()[:2] == next_open.isocalendar()[:2]:
        return f"this {next_open.strftime('%A')}"
    return f"Next {next_open.strftime('%A')}"


