from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


class TradingActivityNotifier:
    """
    Stub notification interface.

    Later we will implement a real HTTP client to telegram_stock_bot `push_trading_activity`.
    """

    def plan_created(self, *, plan_id: int, payload: Dict[str, Any]) -> None:
        raise NotImplementedError

    def order_submitted(self, *, plan_id: int, payload: Dict[str, Any]) -> None:
        raise NotImplementedError

    def partial_fill(self, *, plan_id: int, payload: Dict[str, Any]) -> None:
        raise NotImplementedError

    def full_fill(self, *, plan_id: int, payload: Dict[str, Any]) -> None:
        raise NotImplementedError

    def cancel(self, *, plan_id: int, payload: Dict[str, Any]) -> None:
        raise NotImplementedError

    def exit(self, *, plan_id: int, payload: Dict[str, Any]) -> None:
        raise NotImplementedError

    def daily_summary(self, *, account_id: str, payload: Dict[str, Any]) -> None:
        raise NotImplementedError


@dataclass(frozen=True)
class StubNotifierConfig:
    enabled: bool = True


class DbTradingActivityNotifier(TradingActivityNotifier):
    """
    Logs + stores notifications in SQLite (local stub sink).
    """

    def __init__(self, *, cfg: StubNotifierConfig, db: Any, logger: Any) -> None:
        self.cfg = cfg
        self.db = db
        self.logger = logger

    def _emit(self, event: str, payload: Dict[str, Any]) -> None:
        if not bool(self.cfg.enabled):
            return
        try:
            if self.logger:
                self.logger.info(f"[notify] {event} {payload.get('symbol') or payload.get('account_id') or ''}".strip())
        except Exception:
            pass
        try:
            self.db.insert_notification(event=event, payload=payload)
        except Exception:
            pass

    def plan_created(self, *, plan_id: int, payload: Dict[str, Any]) -> None:
        self._emit("plan_created", {"plan_id": int(plan_id), **payload})

    def order_submitted(self, *, plan_id: int, payload: Dict[str, Any]) -> None:
        self._emit("order_submitted", {"plan_id": int(plan_id), **payload})

    def partial_fill(self, *, plan_id: int, payload: Dict[str, Any]) -> None:
        self._emit("partial_fill", {"plan_id": int(plan_id), **payload})

    def full_fill(self, *, plan_id: int, payload: Dict[str, Any]) -> None:
        self._emit("full_fill", {"plan_id": int(plan_id), **payload})

    def cancel(self, *, plan_id: int, payload: Dict[str, Any]) -> None:
        self._emit("cancel", {"plan_id": int(plan_id), **payload})

    def exit(self, *, plan_id: int, payload: Dict[str, Any]) -> None:
        self._emit("exit", {"plan_id": int(plan_id), **payload})

    def daily_summary(self, *, account_id: str, payload: Dict[str, Any]) -> None:
        self._emit("daily_summary", {"account_id": str(account_id), **payload})


