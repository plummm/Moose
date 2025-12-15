from __future__ import annotations

import re
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional, Tuple

from edgar import set_identity

try:
    import pandas as pd  # type: ignore
except Exception:  # pragma: no cover
    pd = None  # type: ignore


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


def mcp_tool(
    _func: Optional[Callable[..., Any]] = None,
    *,
    name: Optional[str] = None,
    examples: Optional[List[str]] = None,
) -> Any:
    """
    Decorator to mark a method as an MCP-exposed tool.
    """

    def _decorate(func: Callable[..., Any]) -> Callable[..., Any]:
        setattr(func, "_is_mcp_tool", True)
        setattr(func, "_mcp_name", name or func.__name__)
        setattr(func, "_mcp_examples", examples or [])
        return func

    if _func is None:
        return _decorate
    return _decorate(_func)


def _since_date_range(since_days: int) -> str:
    start = (date.today() - timedelta(days=since_days)).isoformat()
    return f"{start}:"


def _as_list(x: Any) -> List[Any]:
    if x is None:
        return []
    if isinstance(x, list):
        return x
    return [x]


def _df_keyed_holdings(df: Any) -> Tuple[Dict[str, Dict[str, Any]], List[str]]:
    if pd is None or df is None or not isinstance(df, pd.DataFrame):
        return {}, []

    def _clean(x: Any) -> str:
        if x is None:
            return ""
        s = str(x).strip()
        return "" if s.lower() in ("nan", "none") else s

    m: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for _, row in df.iterrows():
        r = row.to_dict()
        ticker = _clean(r.get("Ticker"))
        cusip = _clean(r.get("Cusip"))
        issuer = _clean(r.get("Issuer"))
        sec_class = _clean(r.get("Class"))
        key = ticker or cusip or f"{issuer}::{sec_class}"
        if key not in m:
            order.append(key)
        m[key] = r
    return m, order


def _extract_section_by_markers(text: str, start_markers: List[str], end_markers: List[str]) -> Optional[str]:
    if not text:
        return None
    upper = text.upper()
    starts = [upper.find(m.upper()) for m in start_markers]
    starts = [i for i in starts if i != -1]
    if not starts:
        return None
    s = min(starts)
    e_candidates = []
    for em in end_markers:
        idx = upper.find(em.upper(), s + 1)
        if idx != -1:
            e_candidates.append(idx)
    e = min(e_candidates) if e_candidates else len(text)
    return text[s:e].strip()


def _diff_highlights(before: str, after: str, max_lines: int = 200) -> Dict[str, Any]:
    import difflib

    before_lines = [l.rstrip() for l in (before or "").splitlines()]
    after_lines = [l.rstrip() for l in (after or "").splitlines()]
    ud = list(difflib.unified_diff(before_lines, after_lines, lineterm=""))
    if len(ud) > max_lines:
        ud = ud[:max_lines] + ["... diff truncated ..."]

    def _sentences(s: str) -> List[str]:
        parts = re.split(r"(?<=[.!?])\\s+", s.replace("\\n", " "))
        out: List[str] = []
        for p in parts:
            p2 = p.strip()
            if 40 <= len(p2) <= 260:
                out.append(p2)
        return out

    bset = set(_sentences(before))
    aset = set(_sentences(after))
    added = list(aset - bset)[:25]
    removed = list(bset - aset)[:25]
    return {"unified_diff": ud, "added_snippets": added, "removed_snippets": removed}


class EdgarMCPTools:
    """
    Base class: holds identity initialization + tool enumeration.
    Category tool classes should inherit from this.
    """

    def __init__(self, identity: str, logger: Any = None):
        set_identity(identity)
        self.logger = logger

    @classmethod
    def list_mcp_tools(cls) -> List[Dict[str, Any]]:
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


