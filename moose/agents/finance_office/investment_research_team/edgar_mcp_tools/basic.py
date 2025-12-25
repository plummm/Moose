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


def filings_is_empty(filings: Any) -> bool:
    """Robust emptiness check for edgar filings objects.

    The upstream `edgar` library has changed return types over time:
    - sometimes a collection-like object with `.empty`
    - sometimes an iterable / slice with `.latest(...)`
    - sometimes a single `EntityFiling` (which does NOT have `.empty`)

    This helper treats a single filing as non-empty, and only returns True
    when we can confidently determine the collection is empty.
    """
    if filings is None:
        return True

    empty_attr = getattr(filings, "empty", None)
    if isinstance(empty_attr, bool):
        return empty_attr

    # Common: sized containers
    try:
        return len(filings) == 0  # type: ignore[arg-type]
    except Exception:
        pass

    # Common: list-like attribute
    for attr in ("filings", "items", "data"):
        v = getattr(filings, attr, None)
        if isinstance(v, list):
            return len(v) == 0

    # Try iteration: empty iterator means empty collection.
    try:
        it = iter(filings)  # type: ignore[arg-type]
        next(it)
        return False
    except StopIteration:
        return True
    except Exception:
        # If it's not iterable (e.g., a single EntityFiling), treat as non-empty.
        return False


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
    meeting_room_only: bool = False,
) -> Any:
    """
    Decorator to mark a method as an MCP-exposed tool.
    """

    def _decorate(func: Callable[..., Any]) -> Callable[..., Any]:
        setattr(func, "_is_mcp_tool", True)
        setattr(func, "_mcp_name", name or func.__name__)
        setattr(func, "_mcp_examples", examples or [])
        setattr(func, "_mcp_meeting_room_only", bool(meeting_room_only))
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
                        "meeting_room_only": bool(getattr(fn, "_mcp_meeting_room_only", False)),
                    }
                )
        return out

    @mcp_tool(meeting_room_only=True)
    async def ask_specialist(self, target: str, instruction: str, thread_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Ask another specialist to do work you cannot do with your current tool scope.

        When to use
        - Use this tool when you need another specialist's domain tools or expertise (e.g., you are the EDGAR specialist
          but you need FMP data; or you are missing a specific tool in your current scope).
        - Ask for a *specific*, reproducible action. Provide exact tool name(s) and argument values when possible.

        Parameters
        - target: str
          The specialist identifier to ask. Use one of the meeting-room participant ids, for example:
          - \"fmp_fundamentals\" (financial statements, fundamentals, company snapshot)
          - \"fmp_price\" (quotes, charts, technical indicators)
          - \"fmp_news\" (news/search/news context)
          - \"fmp_macro\" (macro/economic indicators)
          (Exact available targets depend on the current meeting room setup.)

        - instruction: str
          A clear instruction to the target specialist. Include:
          - the goal (what question you need answered),
          - required tool call(s) and exact args (preferred),
          - the expected output format (short summary + evidence).

          Good example:
            \"Get Nvidia's market cap and company profile.\"

          Bad example:
            \"Tell me about NVDA\" (too vague; may waste tokens and miss the data you need)

        - thread_id: Optional[str]
          Optional private-thread id for correlation / follow-ups.
          - If you pass the same thread_id again, the target will see the same private thread context.
          - If omitted, a new thread is created automatically.

        Return value (MCP JSON envelope)
        - ok: bool
        - data: dict (present when ok=true)
          - target: str (the same target you requested)
          - thread_id: str (private thread id used)
          - request: dict (the request message you sent)
          - reply: dict (the target's first reply message)

        Message dict schema (`request` / `reply`)
        - id: str
        - ts: float (unix timestamp)
        - sender_id: str
        - role: str (\"system\"|\"human\"|\"assistant\"|\"tool\")
        - content: str
        - targets: list[str] | null
        - thread_id: str | null
        - metadata: dict

        - error: null | {\"type\": str, \"message\": str}
        - meta: dict (includes tool inputs)

        Notes for best results
        - Prefer asking the target to call 1–2 concrete tools and return compact evidence.
        - If you need a follow-up, reuse `thread_id` to stay in the same private thread.
        """
        meta = {"tool": "ask_specialist", "target": target, "thread_id": thread_id}
        if not isinstance(target, str) or not target.strip():
            return mcp_envelope_err("target is required", meta=meta)
        if not isinstance(instruction, str) or not instruction.strip():
            return mcp_envelope_err("instruction is required", meta=meta)

        try:
            from moose.framework.meet_room.room import MeetingRoom
            from moose.framework.meet_room.types import MeetingRole
        except Exception as e:
            return mcp_envelope_err(f"MeetingRoom is not available: {e}", meta=meta)

        room = MeetingRoom.current()
        if room is None:
            return mcp_envelope_err(
                "MeetingRoom context is not set. Use MeetingRoom.set_current(room) around the specialist run.",
                meta=meta,
            )

        target_id = target.strip()
        sender_id = MeetingRoom.current_sender_id() or "edgar"
        result = await room.ask_private(
            sender_id=sender_id,
            role=MeetingRole.HUMAN,
            content=instruction.strip(),
            targets=[target_id],
            thread_id=thread_id,
        )

        if not bool(result.get("complete")):
            missing = result.get("missing_targets") or [target_id]
            return mcp_envelope_err(
                f"Timeout waiting for specialist reply: missing={missing}",
                meta={**meta, "missing_targets": missing, "thread_id": result.get("thread_id")},
            )

        replies = result.get("replies") if isinstance(result.get("replies"), dict) else {}
        reply = replies.get(target_id) if isinstance(replies.get(target_id), dict) else None
        if reply is None:
            return mcp_envelope_err(
                "No reply payload found for target despite completion flag.",
                meta={**meta, "thread_id": result.get("thread_id")},
            )

        # Attribute helper LLM usage/cost back to the requesting LLMClient request (if running inside ToolRuntime).
        try:
            from moose.framework.llm_core.tool_runtime import ToolRuntime

            rt = ToolRuntime.current()
            if rt is not None and isinstance(reply, dict):
                rmeta = reply.get("metadata") if isinstance(reply.get("metadata"), dict) else {}
                rt.add_external_llm_usage(
                    usage=rmeta.get("llm_usage") if isinstance(rmeta.get("llm_usage"), dict) else None,
                    cost=rmeta.get("llm_cost") if isinstance(rmeta.get("llm_cost"), (int, float)) else None,
                )
        except Exception:
            pass

        data = {
            "target": target_id,
            "thread_id": str(result.get("thread_id") or ""),
            "request": result.get("request"),
            "reply": reply,
        }
        tf = f"Asked specialist '{target_id}' via meeting room; received reply."
        return mcp_envelope_ok(data=data, meta=meta, text_fallback=tf)


