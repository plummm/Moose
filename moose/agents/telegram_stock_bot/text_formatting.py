from __future__ import annotations

import html
import json

# Telegram hard limit is 4096 UTF-16 code units for message text. In practice, python-telegram-bot
# will error if `text` is too long. Keep a buffer to reduce the risk of edge-case overflows.
TELEGRAM_MESSAGE_CHAR_LIMIT = 4096
TELEGRAM_SAFE_CHAR_LIMIT = 4000


def split_telegram_html(text: str, limit: int = TELEGRAM_SAFE_CHAR_LIMIT) -> list[str]:
    """
    Split a long Telegram message into chunks under `limit` chars.

    We keep the algorithm conservative and split on natural boundaries:
    - paragraphs (\\n\\n)
    - lines (\\n)
    - spaces
    - hard slice as a last resort

    This is intended for HTML parse_mode messages where we control the tag structure and avoid
    emitting long, unclosed tags that might be split across chunk boundaries.
    """
    s = (text or "")
    if limit <= 0:
        return [s]
    if len(s) <= limit:
        return [s]

    out: list[str] = []
    buf: list[str] = []
    buf_len = 0

    def _flush():
        nonlocal buf_len
        chunk = "".join(buf).strip()
        buf.clear()
        buf_len = 0
        if chunk:
            out.append(chunk)

    # Prefer paragraph boundaries first
    paragraphs = s.split("\n\n")
    for pi, para in enumerate(paragraphs):
        piece = para if pi == 0 else "\n\n" + para
        if len(piece) > limit:
            # Fall back to line splitting
            lines = piece.split("\n")
            for li, line in enumerate(lines):
                lp = line if li == 0 else "\n" + line
                if len(lp) > limit:
                    # Fall back to word splitting
                    words = lp.split(" ")
                    for wi, w in enumerate(words):
                        wp = w if wi == 0 else " " + w
                        if len(wp) > limit:
                            # Hard-slice very long tokens
                            for i in range(0, len(wp), limit):
                                part = wp[i : i + limit]
                                if buf_len + len(part) > limit:
                                    _flush()
                                buf.append(part)
                                buf_len += len(part)
                            continue

                        if buf_len + len(wp) > limit:
                            _flush()
                        buf.append(wp)
                        buf_len += len(wp)
                    continue

                if buf_len + len(lp) > limit:
                    _flush()
                buf.append(lp)
                buf_len += len(lp)
            continue

        if buf_len + len(piece) > limit:
            _flush()
        buf.append(piece)
        buf_len += len(piece)

    _flush()
    return out or [s[:limit]]


def format_finance_office_reply(*, instruction: str, resp: dict) -> str:
    """
    Format FinanceOffice responses for Telegram presentation (HTML, but fully escaped).
    """
    def _unwrap_result(payload):
        """
        Unwrap common nested envelopes to get the actual final payload.

        Shapes handled:
        - finance_office: {"status","error","result": X}
        - investment_research_team/team_merge: {"ok","error","result": Y} (and optionally "raw")
        """
        cur = payload
        for _ in range(5):  # avoid infinite loops
            if not isinstance(cur, dict):
                break
            keys = set(cur.keys())

            # team_merge / investment_research_team envelope
            if "result" in cur and ("ok" in cur or keys.issuperset({"ok", "error", "result"})):
                cur = cur.get("result")
                continue

            # generic status envelope
            if "result" in cur and ("status" in cur or keys.issuperset({"status", "error", "result"})):
                cur = cur.get("result")
                continue

            break
        return cur

    instr = (instruction or "").strip()
    status = str(resp.get("status") or "").strip().lower()
    if status != "success":
        err = html.escape(str(resp.get("error") or "unknown"))
        obj = html.escape(instr) if instr else "(no instruction provided)"
        return (
            "<b>⚠️ FinanceOffice error</b>\n"
            f"- {err}\n\n"
            "<b>🎯 Objective</b>\n"
            f"- {obj}"
        )

    payload = _unwrap_result(resp.get("result"))

    # investment_research_team contract for telegram:
    # {"ok","error","result": {"by_ticker": {"<ticker>": {"objective","approach","analysis_results"}}, "tickers":[...]}}
    # Note: upstream unwrapping may already have removed the {"ok","error","result": ...} layer, so handle both.
    analysis_data: dict = {}
    if isinstance(payload, dict) and (isinstance(payload.get("by_ticker"), dict) or isinstance(payload.get("tickers"), list)):
        analysis_data = payload
    else:
        envelope = payload if isinstance(payload, dict) else {}
        ok_flag = bool(envelope.get("ok")) if isinstance(envelope.get("ok"), bool) else None
        if ok_flag is False:
            err = html.escape(str(envelope.get("error") or "unknown"))
            obj = html.escape(instr) if instr else "(no instruction provided)"
            return (
                "<b>⚠️ Investment research error</b>\n"
                f"- {err}\n\n"
                "<b>🎯 Objective</b>\n"
                f"- {obj}"
            )
        analysis_data = envelope.get("result") if isinstance(envelope.get("result"), dict) else {}
    by_ticker = analysis_data.get("by_ticker") if isinstance(analysis_data.get("by_ticker"), dict) else {}
    tickers = analysis_data.get("tickers") if isinstance(analysis_data.get("tickers"), list) else []
    tickers = [str(t).upper().strip() for t in tickers if str(t).strip() or t == ""]
    if not tickers and by_ticker:
        tickers = [str(t).upper().strip() for t in by_ticker.keys()]
    if not tickers:
        tickers = [""]  # macro fallback

    def _as_lines(v) -> list[str]:
        if v is None:
            return []
        if isinstance(v, list):
            return [str(x) for x in v if str(x).strip()]
        s = str(v).strip()
        if not s:
            return []
        # Preserve author newlines, but split into non-empty lines for bullets.
        return [ln.strip() for ln in s.splitlines() if ln.strip()]

    header_obj = html.escape(instr) if instr else "(no instruction provided)"

    usage = resp.get("llm_usage_total") if isinstance(resp.get("llm_usage_total"), dict) else None
    cost = resp.get("llm_cost_total")
    footer_lines: list[str] = []
    if isinstance(usage, dict):
        it = int(usage.get("input_tokens", 0) or 0)
        ot = int(usage.get("output_tokens", 0) or 0)
        tt = int(usage.get("total_tokens", it + ot) or (it + ot))
        footer_lines.append(f"- Tokens: in={it} out={ot} total={tt}")
    if cost is not None:
        try:
            footer_lines.append(f"- Cost: ${float(cost):.6f}")
        except Exception:
            footer_lines.append(f"- Cost: {html.escape(str(cost))}")

    parts: list[str] = []
    parts.append("<b>🎯 Instruction</b>")
    parts.append(f"- {header_obj}")
    parts.append("")
    parts.append("<b>📌 Results</b>")

    for t in tickers:
        display_ticker = (str(t or "").upper().strip() or "MACRO/ECONOMY")
        ar = by_ticker.get(t) or by_ticker.get(display_ticker) or {}
        ar = ar if isinstance(ar, dict) else {}

        obj = str(ar.get("objective") or "").strip()
        appr = ar.get("approach")
        res = str(ar.get("analysis_results") or ar.get("analysis_result") or "").strip()

        parts.append("")
        parts.append(f"<b>🏷️ {html.escape(display_ticker)}</b>")

        if obj:
            parts.append("<b>1. Objective</b>")
            parts.append(f"- {html.escape(obj)}")
            parts.append("")

        appr_lines = _as_lines(appr)
        if appr_lines:
            parts.append("<b>2. Approach</b>")
            for ln in appr_lines:
                parts.append(f"- {html.escape(ln)}")
            parts.append("")
            
        if res:
            parts.append("<b>3. Analysis</b>")
            parts.append(html.escape(res))
            parts.append("")

        if not (obj or appr_lines or res):
            # Fallback: show whatever we got (escaped), but keep it compact.
            try:
                fallback = json.dumps(ar, ensure_ascii=False, indent=2, sort_keys=True) if isinstance(ar, dict) else str(ar)
            except Exception:
                fallback = str(ar)
            parts.append(html.escape(fallback or "(empty)"))

    if footer_lines:
        parts.append("")
        parts.append("<b>📊 Usage</b>")
        parts.extend(footer_lines)

    return "\n".join(parts).strip()


