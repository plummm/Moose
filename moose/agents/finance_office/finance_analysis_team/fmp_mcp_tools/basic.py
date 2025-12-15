import os
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple, Literal, cast
import json
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

def mcp_tool(
    _func: Optional[Callable[..., Any]] = None,
    *,
    name: Optional[str] = None,
    examples: Optional[List[str]] = None,
) -> Any:
    """
    Decorator to mark a method as an MCP-exposed tool.

    This enables later extraction via reflection for building LangChain tools.
    Internal/private helpers should NOT be decorated.
    """
    def _decorate(func: Callable[..., Any]) -> Callable[..., Any]:
        setattr(func, "_is_mcp_tool", True)
        setattr(func, "_mcp_name", name or func.__name__)
        setattr(func, "_mcp_examples", examples or [])
        return func

    if _func is None:
        return _decorate
    return _decorate(_func)

class FMPMCPTools():
    """
    FinancialModelingPrep MCP Tools - Define customized FinancialModelingPrep tools through MCP
    """
    def __init__(self, identity: str, logger=None):
        """
        Initialize FMP MCP Tools.
        """
        self.identity = identity
        self.logger = logger

        # FinancialModelingPrep Stable API base URL
        self.base_url = "https://financialmodelingprep.com/stable"

        # API key is required for all requests
        self.api_key = os.getenv("FMP_API_KEY")
        if not self.api_key and self.logger:
            self.logger.warning(
                "FMP_API_KEY is not set. FMPMCPTools requests will fail until the key is provided."
            )

        # Optional dependency
        try:
            import requests  # type: ignore
            self._requests = requests
        except Exception:
            self._requests = None

    def _error(self, message: str, *, details: Optional[dict] = None) -> dict:
        """Create a consistent error payload."""
        payload: dict = {"error": message}
        if details:
            payload["details"] = details
        return payload

    def _request_json(self, path: str, params: Optional[dict] = None) -> Any:
        """
        Internal helper to call the FMP Stable API and return parsed JSON.

        Uses `requests` if available, otherwise falls back to `urllib`.
        """
        if not self.api_key:
            return self._error(
                "Missing FinancialModelingPrep API key. Set environment variable FMP_API_KEY."
            )

        qp = dict(params or {})
        qp["apikey"] = self.api_key

        # Build URL with query string
        url = f"{self.base_url}/{path.lstrip('/')}"
        url_with_qs = f"{url}?{urlencode(qp)}"

        # Prefer requests if installed
        if self._requests is not None:
            try:
                # Note: pass params separately so API key isn't logged as part of the base URL by requests.
                resp = self._requests.get(url, params=qp, timeout=20)
                resp.raise_for_status()
                return resp.json()
            except Exception as e:
                if self.logger:
                    self.logger.warning(f"FMP request failed: {e}")
                # Provide best-effort context
                status_code = getattr(getattr(e, "response", None), "status_code", None)
                text = getattr(getattr(e, "response", None), "text", None)
                return self._error(
                    "Failed to fetch data from FinancialModelingPrep.",
                    details={"url": url_with_qs, "status_code": status_code, "response_text": text},
                )

        # urllib fallback
        try:
            req = Request(url_with_qs, headers={"Accept": "application/json"})
            with urlopen(req, timeout=20) as resp:  # nosec - URL is constructed from known base
                body = resp.read().decode("utf-8", errors="replace")
                return json.loads(body) if body else None
        except HTTPError as e:
            body = None
            try:
                body = e.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            if self.logger:
                self.logger.warning(f"FMP HTTPError {e.code}: {body}")
            return self._error(
                "Failed to fetch data from FinancialModelingPrep.",
                details={"url": url_with_qs, "status_code": e.code, "response_text": body},
            )
        except (URLError, TimeoutError, json.JSONDecodeError) as e:
            if self.logger:
                self.logger.warning(f"FMP urllib request failed: {e}")
            return self._error(
                "Failed to fetch data from FinancialModelingPrep.",
                details={"url": url_with_qs, "exception": str(e)},
            )

    def _coerce_first_record(self, payload: Any) -> Optional[dict]:
        """
        FMP endpoints typically return a list of objects; sometimes a single dict.
        This helper returns the first record dict (or None).
        """
        if payload is None:
            return None
        if isinstance(payload, dict):
            # Could be an error payload or a single record
            return payload
        if isinstance(payload, list) and payload:
            first = payload[0]
            return first if isinstance(first, dict) else None
        return None