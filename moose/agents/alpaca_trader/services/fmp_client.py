from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Optional

import httpx


@dataclass(frozen=True)
class FmpConfig:
    api_key: str
    timeout_seconds: float = 10.0
    max_retries: int = 3
    backoff_base_seconds: float = 0.6


class FmpClient:
    """
    Minimal async FMP client (stable endpoints), matching telegram_stock_bot patterns.
    """

    def __init__(self, cfg: FmpConfig):
        self.cfg = cfg
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(cfg.timeout_seconds),
            headers={"User-Agent": "alpaca_trader/1.0"},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get_json(self, url: str, params: dict[str, Any]) -> Any:
        params = dict(params)
        params["apikey"] = self.cfg.api_key

        last_exc: Optional[Exception] = None
        for attempt in range(self.cfg.max_retries):
            try:
                r = await self._client.get(url, params=params)
                if r.status_code in (429, 500, 502, 503, 504):
                    raise httpx.HTTPStatusError(
                        f"Transient HTTP {r.status_code}",
                        request=r.request,
                        response=r,
                    )
                r.raise_for_status()
                return r.json()
            except (httpx.TimeoutException, httpx.TransportError, httpx.HTTPStatusError) as e:
                last_exc = e
                if attempt >= self.cfg.max_retries - 1:
                    break
                sleep_s = self.cfg.backoff_base_seconds * (2**attempt)
                await asyncio.sleep(sleep_s)
        if last_exc:
            raise last_exc
        raise RuntimeError("FMP request failed")

    async def holidays_by_exchange(self, exchange: str, start_date: str, end_date: str) -> list[dict[str, Any]]:
        data = await self._get_json(
            "https://financialmodelingprep.com/stable/holidays-by-exchange",
            {"exchange": exchange, "from": start_date, "to": end_date},
        )
        return data if isinstance(data, list) else []


