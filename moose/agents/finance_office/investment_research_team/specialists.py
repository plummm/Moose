from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Set


@dataclass
class SpecialistResult:
    agent: str
    summary: str
    key_findings: List[str]
    evidence: List[Dict[str, Any]]
    confidence: int
    next_steps: List[Dict[str, Any]]
    raw: Any


def _tool_names_from_specs(specs: Sequence[Dict[str, Any]]) -> Set[str]:
    out: Set[str] = set()
    for s in specs or []:
        nm = (s or {}).get("name")
        if isinstance(nm, str) and nm:
            out.add(nm)
    return out


def build_tool_scopes() -> Dict[str, Set[str]]:
    """
    Define tool-name sets per specialist agent using category classes' list_mcp_tools().
    """
    from moose.agents.finance_office.investment_research_team.edgar_mcp_tools import EdgarAllMCPTools
    from moose.agents.finance_office.investment_research_team.fmp_mcp_tools import (
        AnalystMCPTools,
        CalendarMCPTools,
        ChartMCPTools,
        CompanyMCPTools,
        EconomicsMCPTools,
        FinanceMCPTools,
        IndicatorMCPTools,
        MarketMCPTools,
        NewsMCPTools,
        QuoteMCPTools,
    )

    scopes: Dict[str, Set[str]] = {}
    scopes["edgar"] = _tool_names_from_specs(EdgarAllMCPTools.list_mcp_tools())
    scopes["fmp_news"] = _tool_names_from_specs(NewsMCPTools.list_mcp_tools())
    scopes["fmp_fundamentals"] = (
        _tool_names_from_specs(FinanceMCPTools.list_mcp_tools())
        | _tool_names_from_specs(CompanyMCPTools.list_mcp_tools())
        | _tool_names_from_specs(AnalystMCPTools.list_mcp_tools())
        | _tool_names_from_specs(CalendarMCPTools.list_mcp_tools())
    )
    scopes["fmp_macro"] = _tool_names_from_specs(EconomicsMCPTools.list_mcp_tools()) | _tool_names_from_specs(MarketMCPTools.list_mcp_tools())
    scopes["fmp_price"] = (
        _tool_names_from_specs(QuoteMCPTools.list_mcp_tools())
        | _tool_names_from_specs(ChartMCPTools.list_mcp_tools())
        | _tool_names_from_specs(IndicatorMCPTools.list_mcp_tools())
    )
    return scopes


def filter_langchain_tools_by_name(all_tools: Sequence[Any], allowed: Set[str]) -> List[Any]:
    out: List[Any] = []
    for t in all_tools or []:
        nm = getattr(t, "name", None)
        if isinstance(nm, str) and nm in allowed:
            out.append(t)
    return out


def build_specialist_llm_clients(
    *,
    base_model: str,
    base_temperature: float,
    llm_extra_params: Optional[Dict[str, Any]],
    tools_provider: Any,
    max_tool_iterations_by_agent: Dict[str, int],
    agent_name: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Build per-specialist LLMClient instances with scoped tools bound.
    """
    from moose.framework.llm_core import LLMClient

    scopes = build_tool_scopes()
    all_tools = tools_provider.get_langchain_tools()

    clients: Dict[str, Any] = {}
    for agent, allowed_names in scopes.items():
        tool_list = filter_langchain_tools_by_name(all_tools, allowed_names)
        clients[agent] = LLMClient(
            model=base_model,
            temperature=base_temperature,
            tools=tool_list,
            enable_multi_stage_reasoning=True,
            max_tool_iterations=int(max_tool_iterations_by_agent.get(agent, 6)),
            agent_name=str(agent_name or "").strip() or None,
            **(llm_extra_params or {}),
        )
    return clients


def _extract_json(text: str) -> Optional[dict]:
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


async def run_specialist(
    *,
    agent: str,
    llm_client: Any,
    context_text: str,
    task: Dict[str, Any],
    prior_reports: Optional[Dict[str, Any]] = None,
    tool_summary: str = "",
) -> SpecialistResult:
    """
    Execute a specialist sub-agent run (with scoped tools already bound to llm_client).
    """
            
    system_message = f"""You are the `{agent}` sub-agent in an Investment Research team.

You have a scoped toolset (listed below). Your job is to use tools to gather evidence and return a structured analysis report.
You do NOT write the final department answer; you produce research inputs that a merge step can use.

Tool usage:
- Prefer tool-driven facts over assumptions.
- Tool results may include `meta.recommended_next_tools`; follow when useful.
- Do NOT fabricate numbers, filings, dates, or quotes. If you cannot verify, say so.

Output: Return STRICT JSON ONLY (no markdown, no extra keys):
{{
  "summary": "<short paragraph (3-5 sentences, ~60-120 words) answering: what did you learn that matters for the goal?>",
  "key_findings": [
    "<3-8 bullet-style strings; each is one concrete claim or observation backed by evidence or clearly labeled as inference>",
    "..."
  ],
  "evidence": [
    {{
      "source": "{agent}",
      "tool": "<tool_name you actually called>",
      "key_fields": {{"ticker":"...", "date_range":"...", "...":"..."}},
      "excerpt": "<short quote or key numbers (<= ~300 chars) copied/summarized from tool output>"
    }}
  ],
  "confidence": <int 1-10>,
  "next_steps": [
    {{
      "tool": "<tool_name to call next (must be in your tool list)>",
      "reason": "<why it helps the goal / what uncertainty it resolves>",
      "args_template": {{"ticker":"...", "...":"..."}}
    }}
  ]
}}

Confidence scale (1-10):
- 1-2: mostly unverified / missing key facts / tool failures
- 3-4: weak evidence; partial coverage; high uncertainty
- 5-6: moderate evidence; enough to be directionally useful
- 7-8: strong evidence from relevant tools; minor gaps only
- 9-10: very strong; primary-source confirmation where applicable (e.g., filings) and consistent across sources

Rules:
- If you did not call any tool, set `evidence` to [] and explain why tools were unnecessary or unavailable.
- `key_fields` must be minimal and help reproduce the call (ticker, accession, date window, etc.).
- `next_steps` is optional; use [] if you are confident you’ve answered your part of the task.

Scoped tools for this sub-agent:
{tool_summary.strip()}
"""

    effective_context = (context_text or "").strip() or "(none provided)"

    user_message = f"""You are `{agent}`. Execute your part of the research.

Task (from Research Lead):
{json.dumps(task or {}, ensure_ascii=False, indent=2)}

Context (may be a news article, notes, or a user instruction):
{effective_context}

Return STRICT JSON only."""

    resp = await llm_client.send_message(message=user_message, system_message=system_message)
    content = getattr(resp, "content", "") or ""
    if type(content) != str:
        raise ValueError(f"Tool {agent} repsonse is not a string: {type(content)}")
    data = _extract_json(content) or {}

    summary = str(data.get("summary") or content[:800]).strip()
    key_findings = [str(x).strip() for x in (data.get("key_findings") or []) if str(x).strip()]
    evidence = data.get("evidence") if isinstance(data.get("evidence"), list) else []
    next_steps = data.get("next_steps") if isinstance(data.get("next_steps"), list) else []
    try:
        confidence = int(data.get("confidence") or 5)
    except Exception:
        confidence = 5
    confidence = max(1, min(10, confidence))

    return SpecialistResult(
        agent=agent,
        summary=summary,
        key_findings=key_findings,
        evidence=[e for e in evidence if isinstance(e, dict)],
        confidence=confidence,
        next_steps=[e for e in next_steps if isinstance(e, dict)],
        raw={"text": resp, "parsed": data},
    )


