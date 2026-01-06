from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

try:
    from langchain_mcp_adapters.client import MultiServerMCPClient
except Exception as e:  # pragma: no cover
    print(f"Error importing langchain_mcp_adapters: {e}")
    MultiServerMCPClient = None  # type: ignore


@dataclass(frozen=True)
class AlpacaMcpAccountConfig:
    """
    Configuration for one Alpaca MCP server sidecar.

    We intentionally treat the sidecar as the authentication boundary: the sidecar
    holds Alpaca API credentials; the trader connects to it over localhost/network.
    """

    account_id: str
    url: str
    headers: Optional[Dict[str, str]] = None


class AlpacaMcpToolRegistry:
    """
    Lazy + cached loader for MCP tools, one MCP server per account.

    Note: langchain-mcp-adapters creates a new MCP session per tool call by default.
    We use it purely as a tool schema + invocation adapter.
    """

    def __init__(
        self,
        *,
        accounts: List[AlpacaMcpAccountConfig],
        logger: Any,
        refresh_interval_s: float = 300.0,
    ) -> None:
        self.logger = logger
        self.refresh_interval_s = float(refresh_interval_s)

        self._accounts: Dict[str, AlpacaMcpAccountConfig] = {a.account_id: a for a in (accounts or [])}
        self._tools_by_account: Dict[str, List[Any]] = {}
        self._last_refresh_by_account: Dict[str, float] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def from_custom_config(custom: Dict[str, Any], *, logger: Any) -> "AlpacaMcpToolRegistry":
        accounts_cfg = custom.get("accounts") if isinstance(custom.get("accounts"), dict) else {}
        accounts: List[AlpacaMcpAccountConfig] = []
        for account_id, spec in (accounts_cfg or {}).items():
            if not isinstance(spec, dict):
                continue
            url = str(spec.get("mcp_url") or "").strip()
            if not url:
                continue
            headers = spec.get("headers") if isinstance(spec.get("headers"), dict) else None
            # Normalize header keys/values to strings.
            if headers is not None:
                headers = {str(k): str(v) for k, v in headers.items()}
            accounts.append(AlpacaMcpAccountConfig(account_id=str(account_id), url=url, headers=headers))
        refresh = custom.get("mcp_tools_refresh_seconds", 300.0)
        try:
            refresh_s = float(refresh)
        except Exception:
            refresh_s = 300.0
        return AlpacaMcpToolRegistry(accounts=accounts, logger=logger, refresh_interval_s=refresh_s)

    def account_ids(self) -> List[str]:
        return sorted(self._accounts.keys())

    async def get_tools(self, *, account_id: str, force_refresh: bool = False) -> List[Any]:
        """
        Return LangChain tools for the given account_id.

        This method caches tool schemas and refreshes occasionally to pick up MCP server changes.
        """
        if MultiServerMCPClient is None:
            raise ImportError("langchain-mcp-adapters is required for Alpaca MCP integration")

        acct = self._accounts.get(str(account_id))
        if acct is None:
            raise KeyError(f"Unknown Alpaca account_id '{account_id}'. Known: {self.account_ids()}")

        now = time.time()
        last = self._last_refresh_by_account.get(acct.account_id, 0.0)
        should_refresh = force_refresh or (now - last) >= self.refresh_interval_s

        async with self._lock:
            if (not should_refresh) and acct.account_id in self._tools_by_account:
                return self._tools_by_account[acct.account_id]

            # Build MCP connection config for this account
            conn: Dict[str, Any] = {"transport": "streamable_http", "url": acct.url}
            if acct.headers:
                conn["headers"] = dict(acct.headers)

            mcp_client = MultiServerMCPClient(connections={acct.account_id: conn})
            try:
                tools = await mcp_client.get_tools(server_name=acct.account_id)
            except Exception as e:
                # Keep existing cached tools (if any) so the agent can still function.
                cached = self._tools_by_account.get(acct.account_id)
                if cached is not None:
                    if self.logger:
                        self.logger.warning(
                            f"Alpaca MCP tool refresh failed for account_id={acct.account_id}; using cached tools. err={e}"
                        )
                    return cached
                raise RuntimeError(
                    f"Failed to load MCP tools from Alpaca sidecar for account_id={acct.account_id} url={acct.url}: {e}"
                ) from e

            self._tools_by_account[acct.account_id] = tools
            self._last_refresh_by_account[acct.account_id] = now
            if self.logger:
                self.logger.info(f"Loaded {len(tools)} Alpaca MCP tools for account_id={acct.account_id}")
            return tools


