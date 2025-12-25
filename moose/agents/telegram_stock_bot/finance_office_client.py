from __future__ import annotations

import os
from typing import Any, Optional

import httpx

from moose.framework.agent_core.agent_endpoints import resolve_agent_base_url


class FinanceOfficeClient:
    def __init__(self, *, base_url: Optional[str] = None, timeout_s: float = 300.0):
        # Local debug: https://localhost:<port>
        # Docker: http://{image_prefix}finance_office-{project_id}:3501
        self.base_url = base_url or resolve_agent_base_url(agent_name="finance_office", port=3501)
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(timeout_s))

    async def aclose(self) -> None:
        await self._client.aclose()

    async def run_task(self, *, instruction: str, context: str, analyzer_data: dict[str, Any]) -> dict[str, Any]:
        """
        Call finance_office /run_task.
        """
        url = f"{self.base_url}/run_task"
        try:
            r = await self._client.post(url, json={"instruction": instruction, "context": context, "analyzer_data": analyzer_data})
        except httpx.TransportError:
            # If local scheme is configured as https but the local server is plain HTTP, retry once.
            if url.startswith("https://localhost:"):
                url2 = "http://" + url[len("https://") :]
                r = await self._client.post(url2, json={"instruction": instruction, "context": context, "analyzer_data": analyzer_data})
            else:
                raise
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, dict) else {"status": "error", "error": "invalid_response", "result": data}



