from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

import httpx

from moose.framework.logging.http_client import traced_httpx_post


def _normalize_run_task_endpoint(endpoint: str) -> str:
    e = str(endpoint or "").strip().rstrip("/")
    if not e:
        return ""
    if e.endswith("/run_task"):
        return e
    return f"{e}/run_task"


@dataclass
class FinanceOfficeClient:
    """
    Minimal HTTP client for finance_office /run_task.

    alpaca_trader V2 uses this to fetch a compact research packet that can be embedded into planning.
    """

    endpoint: str
    logger: Any
    timeout_s: float = 180.0

    def __post_init__(self) -> None:
        self.endpoint = _normalize_run_task_endpoint(self.endpoint)
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(float(self.timeout_s)))

    async def aclose(self) -> None:
        try:
            await self._client.aclose()
        except Exception:
            pass

    async def run_task(
        self,
        *,
        instruction: str,
        context: str,
        analyzer_data: Dict[str, Any],
        granularity: str = "minimal",
    ) -> Dict[str, Any]:
        """
        Request body schema matches finance_office /run_task.
        """
        url = self.endpoint
        if not url:
            return {"ok": False, "error": "missing_finance_office_endpoint"}

        g = str(granularity or "minimal").strip().lower() or "minimal"
        if g not in ("minimal", "standard", "maximum"):
            g = "minimal"

        body = {"instruction": instruction, "context": context, "analyzer_data": analyzer_data, "granularity": g}
        try:
            r = await traced_httpx_post(self._client, url, json=body)
        except httpx.TransportError as e:
            # If local scheme is configured as https but the local server is plain HTTP, retry once.
            if url.startswith("https://localhost:"):
                url2 = "http://" + url[len("https://") :]
                r = await traced_httpx_post(self._client, url2, json=body)
            else:
                return {"ok": False, "error": "transport_error", "detail": str(e)}

        try:
            r.raise_for_status()
        except Exception as e:
            return {"ok": False, "error": "http_error", "status": int(getattr(r, "status_code", 0) or 0), "detail": str(e)}

        try:
            data = r.json()
        except Exception:
            data = None
        return data if isinstance(data, dict) else {"ok": False, "error": "invalid_response", "raw": data}



