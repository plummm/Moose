from __future__ import annotations

import os
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple, Literal, cast
import json
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from decimal import Decimal

try:
    import pandas as pd  # type: ignore
except Exception:  # pragma: no cover
    pd = None  # type: ignore

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

def mcp_json_safe(obj: Any) -> Any:
    """
    Convert common edgartools/Python objects into JSON-serializable primitives.
    - Decimal -> float
    - date/datetime -> ISO string
    - pandas.DataFrame -> list[dict]
    - dataclasses -> dict
    - sets/tuples -> list
    """
    if obj is None:
        return None

    if isinstance(obj, (str, int, float, bool)):
        return obj

    if isinstance(obj, Decimal):
        return float(obj)

    if isinstance(obj, (date, datetime)):
        return obj.isoformat()

    # pandas DataFrame / Series
    if pd is not None:
        try:
            if isinstance(obj, pd.DataFrame):
                return obj.replace({pd.NA: None}).where(obj.notna(), None).to_dict(orient="records")
            if isinstance(obj, pd.Series):
                return obj.replace({pd.NA: None}).where(obj.notna(), None).to_dict()
        except Exception:
            pass

    if is_dataclass(obj):
        try:
            if not isinstance(obj, type):
                return mcp_json_safe(asdict(obj))
        except Exception:
            pass

    if isinstance(obj, dict):
        return {str(k): mcp_json_safe(v) for k, v in obj.items()}

    if isinstance(obj, (list, tuple, set)):
        return [mcp_json_safe(v) for v in obj]

    return str(obj)


def mcp_envelope_ok(
    data: Any,
    meta: Optional[Dict[str, Any]] = None,
    text_fallback: Optional[str] = None,
    *,
    tool: Optional[str] = None,
    dependencies: Optional[List[Dict[str, Any]]] = None,
    outputs: Optional[List[Dict[str, Any]]] = None,
    recommended_next_tools: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    meta_out: Dict[str, Any] = dict(meta or {})
    if tool:
        meta_out["tool"] = tool
    meta_out.setdefault("dependencies", [])
    meta_out.setdefault("outputs", [])
    meta_out.setdefault("recommended_next_tools", [])
    if dependencies is not None:
        meta_out["dependencies"] = dependencies
    if outputs is not None:
        meta_out["outputs"] = outputs
    if recommended_next_tools is not None:
        meta_out["recommended_next_tools"] = recommended_next_tools

    return {
        "ok": True,
        "data": mcp_json_safe(data),
        "error": None,
        "meta": mcp_json_safe(meta_out),
        "text_fallback": text_fallback,
    }


def mcp_envelope_err(
    message: str,
    error_type: str = "Error",
    meta: Optional[Dict[str, Any]] = None,
    text_fallback: Optional[str] = None,
    *,
    tool: Optional[str] = None,
    dependencies: Optional[List[Dict[str, Any]]] = None,
    outputs: Optional[List[Dict[str, Any]]] = None,
    recommended_next_tools: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    meta_out: Dict[str, Any] = dict(meta or {})
    if tool:
        meta_out["tool"] = tool
    meta_out.setdefault("dependencies", [])
    meta_out.setdefault("outputs", [])
    meta_out.setdefault("recommended_next_tools", [])
    if dependencies is not None:
        meta_out["dependencies"] = dependencies
    if outputs is not None:
        meta_out["outputs"] = outputs
    if recommended_next_tools is not None:
        meta_out["recommended_next_tools"] = recommended_next_tools

    return {
        "ok": False,
        "data": None,
        "error": {"type": error_type, "message": message},
        "meta": mcp_json_safe(meta_out),
        "text_fallback": text_fallback,
    }
class FMPMCPTools():
    """
    FinancialModelingPrep MCP Tools - Define customized FinancialModelingPrep tools through MCP
    """
    def __init__(self, api_key: Optional[str] = None, logger=None):
        """
        Initialize FMP MCP Tools.
        """
        self.api_key = api_key
        self.logger = logger

        # FinancialModelingPrep Stable API base URL
        self.base_url = "https://financialmodelingprep.com/stable"

        # API key is required for all requests
        if self.api_key is None:
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
    
    @classmethod
    def list_mcp_tools(cls) -> List[Dict[str, Any]]:
        """
        Reflect on the class to find all methods decorated with `@mcp_tool`.
        
        Returns:
            List of dicts with keys: name, method_name, doc, examples
        """
        out: List[Dict[str, Any]] = []
        for attr_name in dir(cls):
            if attr_name.startswith("_"):
                continue
            fn = getattr(cls, attr_name, None)
            if callable(fn) and getattr(fn, "_is_mcp_tool", False):
                out.append(
                    {
                        "name": getattr(fn, "_mcp_name", attr_name),
                        "method_name": attr_name,
                        "doc": (getattr(fn, "__doc__", None) or "").strip(),
                        "examples": list(getattr(fn, "_mcp_examples", []) or []),
                    }
                )
        return out