from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Optional

from moose.framework.llm_core import LLMClient


_JSON_OBJ_RE = re.compile(r"\{[\s\S]*\}\s*$")


@dataclass(frozen=True)
class RouterConfig:
    model: str = "gpt-5-mini"
    temperature: float = 0.2
    max_tool_iterations: int = 5
    enable_multi_stage_reasoning: bool = False


ROUTER_SYSTEM_PROMPT = """You are StockBotRouter for a Telegram finance bot.

OBJECTIVE
- Route a user message to one of:
  (1) answer_direct: answer using available tools (get_price, watchlist ops, next market open time)
  (2) clarify: ask for missing information needed to complete a finance task
  (3) dispatch_finance_office: produce instruction/context/analyzer_data for finance_office
  (4) refuse: if not finance/markets/news/investing related

HARD RULES
- Only handle finance-related requests (markets, stocks, crypto, macro, news impact, investing, portfolio).
- If user asks non-finance topics (e.g., food), refuse politely and suggest finance commands.
- Prefer tools for factual lookups (prices, watchlists, next open).
- Never fabricate prices or tickers. Use tools or ask clarifying questions.
- Output STRICT JSON only. No markdown. No extra keys.
- Output should always leave "user_message" and "system_message" blank in the "analyzer_data" object.

MISSING-ELEMENTS STEP
When user requests analysis/research, identify missing elements such as:
- symbol/asset
- timeframe/horizon
- aspects (fundamental/valuation/technical/news/macro/risk)
- constraints
- output format
If missing, return action=clarify with a single question and a SMALL set of suggested options.

CLARIFICATION OPTIONS RULES
- The question must be plain text (it will be sent as a normal Telegram message).
- Only the options become clickable buttons.
- Provide 2–4 options per field (hard max 4). Prefer fewer.
- Options must be short, mutually distinguishable, and MUST NOT repeat the question text.
- If the choice is NOT mutually exclusive (multi-select makes sense), set allow_multiple=true
  and include a final option "All of the above" (last option).

OUTPUT FORMAT (STRICT JSON)
{
  "is_finance_related": true|false,
  "action": "answer_direct"|"clarify"|"dispatch_finance_office"|"refuse",
  "direct_answer": {"text": "...", "parse_mode": "HTML"} | null,
  "clarification": {"question": "...", "missing_fields": [{"field": "...", "options": ["..."], "allow_multiple": true|false}]} | null,
  "finance_office": {"instruction": "...", "context": "...", "analyzer_data": {"user_message": "<leave blank>", "system_message": "<leave blank>", "extracted": {}}} | null
}
"""


def _extract_json(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    # Try direct parse first
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass
    # Try regex capture last JSON object
    m = _JSON_OBJ_RE.search(text)
    if m:
        obj = json.loads(m.group(0))
        if isinstance(obj, dict):
            return obj
    raise ValueError("Router returned non-JSON output")


async def run_router(
    *,
    cfg: RouterConfig,
    tools: list[Any],
    user_prompt: str,
) -> dict[str, Any]:
    llm = LLMClient(
        model=cfg.model,
        temperature=cfg.temperature,
        tools=tools,
        enable_multi_stage_reasoning=bool(cfg.enable_multi_stage_reasoning),
        max_tool_iterations=cfg.max_tool_iterations,
    )
    resp = await llm.send_message(user_prompt, system_message=ROUTER_SYSTEM_PROMPT)
    content = ""
    try:
        content = str(resp.content or "")
    except Exception:
        content = str(resp)
    return _extract_json(content)



