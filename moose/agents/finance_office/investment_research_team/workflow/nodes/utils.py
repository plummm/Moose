from __future__ import annotations

import json
from typing import Any, Dict, Optional


def extract_json(text: str) -> Optional[dict]:
    s = (text or "").strip()
    if not s:
        return None
    start = s.find("{")
    end = s.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(s[start : end + 1])
    except Exception:
        return None


def json_decode_error(text: str) -> str:
    """
    Best-effort JSON error message for LLM outputs.
    We intentionally keep extraction rules aligned with `extract_json`.
    """
    s = (text or "").strip()
    if not s:
        return "Empty output."
    start = s.find("{")
    end = s.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return "No JSON object boundaries found (missing '{' or '}')."
    try:
        json.loads(s[start : end + 1])
        return "Unknown JSON error (parsed successfully in diagnostic)."
    except Exception as e:
        return f"{type(e).__name__}: {e}"


async def repair_json_once(
    llm_client: Any,
    *,
    bad_output: str,
    error_hint: str,
) -> Any:
    """
    Ask the model to re-emit a STRICT JSON object, given its previous invalid output.
    Minimal-context: we only provide the invalid output and a parse error hint.
    Returns the new LLM response object (provider-specific type).
    """
    repair_system_message = (
        "You are a JSON repair tool.\n"
        "CRITICAL OUTPUT REQUIREMENT:\n"
        "- Return ONLY a single valid JSON object (no markdown fences, no leading/trailing quotes, no commentary).\n"
        "- JSON strings MUST be valid: do not include raw double quotes (\") inside string values.\n"
        "  If you need to quote text, use \\\" ... \\\" or use Chinese quotes 「...」.\n"
        "- Use \\n for newlines inside string values.\n"
    )
    repair_user_message = (
        "Your previous output was invalid JSON and could not be parsed.\n"
        f"Parser error: {error_hint}\n\n"
        "Fix the INVALID OUTPUT below so it becomes strict valid JSON.\n"
        "Keep the same keys/structure and preserve content as much as possible.\n\n"
        "INVALID OUTPUT:\n"
        + str(bad_output or "")
        + "\n\nNow output the corrected JSON object ONLY."
    )
    return await llm_client.send_message(message=repair_user_message, system_message=repair_system_message)


def normalize_usage(u: Any) -> Dict[str, int]:
    if not isinstance(u, dict):
        return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    try:
        it = int(u.get("input_tokens", 0) or 0)
        ot = int(u.get("output_tokens", 0) or 0)
        tt = int(u.get("total_tokens", it + ot) or (it + ot))
    except Exception:
        return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    return {"input_tokens": max(0, it), "output_tokens": max(0, ot), "total_tokens": max(0, tt)}


def raw_snapshot(state: Dict[str, Any]) -> Dict[str, Any]:
    snap = dict(state or {})
    # Avoid recursive embedding if caller already set an envelope
    if isinstance(snap.get("final"), dict) and set(snap["final"].keys()) >= {"ok", "error", "result", "raw"}:
        snap.pop("final", None)
    return snap


def shrink_for_state(x: Any, *, max_str: int = 2000, max_list: int = 50, max_depth: int = 4) -> Any:
    """
    Keep objects small so it is safe to carry through LangGraph state.
    - Truncates long strings
    - Limits list sizes
    - Limits recursion depth
    """
    if max_depth <= 0:
        return None
    if isinstance(x, str):
        s = x.strip()
        return s if len(s) <= max_str else (s[:max_str] + "…")
    if isinstance(x, (int, float, bool)) or x is None:
        return x
    if isinstance(x, dict):
        out: Dict[str, Any] = {}
        for k, v in x.items():
            kk = str(k)
            out[kk] = shrink_for_state(v, max_str=max_str, max_list=max_list, max_depth=max_depth - 1)
        return out
    if isinstance(x, list):
        trimmed = x[:max_list]
        return [shrink_for_state(v, max_str=max_str, max_list=max_list, max_depth=max_depth - 1) for v in trimmed]
    # Fallback: stringify unknown types
    try:
        return shrink_for_state(str(x), max_str=max_str, max_list=max_list, max_depth=max_depth - 1)
    except Exception:
        return None


