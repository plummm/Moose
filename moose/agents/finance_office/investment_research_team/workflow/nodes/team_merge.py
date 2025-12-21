from __future__ import annotations

import json
from typing import Any, Dict
from .utils import extract_json, normalize_usage, raw_snapshot
from .base import BaseNode


class TeamMergeNode(BaseNode):
    """
    Node name: `team_merge`
    Config key: `custom.team_merge_llm_config` (via ResearchLead.get_node_llm_config)

    Reads:
    - merge_system_message / merge_user_message
    - routing / subagent_reports / evidence
    - ticker_memory (optional, only appended when non-neutral)

    Writes:
    - final envelope under state.final
    - llm_usage_total / llm_cost_total
    """

    def __init__(self, *, analyzer: Any, logger: Any):
        super().__init__(analyzer=analyzer, logger=logger)
        self.node_name = "team_merge"
        self.agent_client = self._build_agent_client(node_name="team_merge", tools=[])

    async def run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        merge_system_message = str(state.get("merge_system_message") or "")
        merge_user_message = str(state.get("merge_user_message") or "")
        routing = state.get("routing", {}) or {}
        subagent_reports = state.get("subagent_reports", {}) or {}
        evidence = state.get("evidence", []) or []
        current_ticker = str(state.get("current_ticker") or "").strip()

        if not merge_system_message or not merge_user_message:
            try:
                self.logger.error(
                    "team_merge missing required prompts: merge_system_message/merge_user_message must be provided in state"
                )
            except Exception:
                pass
            raise RuntimeError("team_merge missing required prompts (merge_system_message/merge_user_message)")

        neutral = bool((routing or {}).get("neutral_analysis"))
        ticker_memory = state.get("ticker_memory") if isinstance(state.get("ticker_memory"), dict) else {}
        if ticker_memory and not neutral:
            merge_user_message = (merge_user_message or "") + (
                "\n\nTICKER MONTHLY MEMORY:\n" + json.dumps(ticker_memory, ensure_ascii=False, indent=2)[:12000] + "\n"
            )

        if evidence or subagent_reports or routing:
            addon = "\n\nADDITIONAL CONTEXT (from sub-agents):\n"
            addon += "Routing:\n" + json.dumps(routing, ensure_ascii=False, indent=2) + "\n"
            addon += "Sub-agent reports:\n" + json.dumps(subagent_reports, ensure_ascii=False, indent=2)[:12000] + "\n"
            addon += "Evidence:\n" + json.dumps(evidence, ensure_ascii=False, indent=2)[:12000] + "\n"
            merge_user_message = (merge_user_message or "") + addon

        # Always include the current ticker (empty means macro/economy mode).
        merge_user_message = (merge_user_message or "") + f"\n\nCURRENT_TICKER: {current_ticker or 'MACRO/ECONOMY'}\n"

        resp = await self.agent_client.send_message(message=merge_user_message, system_message=merge_system_message)
        llm_usage_total = normalize_usage(state.get("llm_usage_total"))
        llm_usage_total["input_tokens"] += int(getattr(resp, "usage", {}).get("input_tokens", 0) or 0) if getattr(resp, "usage", None) else 0
        llm_usage_total["output_tokens"] += int(getattr(resp, "usage", {}).get("output_tokens", 0) or 0) if getattr(resp, "usage", None) else 0
        llm_usage_total["total_tokens"] += int(getattr(resp, "usage", {}).get("total_tokens", 0) or 0) if getattr(resp, "usage", None) else 0
        try:
            llm_cost_total = float(state.get("llm_cost_total") or 0.0) + float(getattr(resp, "cost", 0.0) or 0.0)
        except Exception:
            llm_cost_total = float(state.get("llm_cost_total") or 0.0)

        out = extract_json(getattr(resp, "content", "") or "") or {}
        if not isinstance(out, dict):
            out = {}
        out.setdefault("ticker", current_ticker)

        raw = raw_snapshot(
            {
                **state,
                "routing": routing,
                "subagent_reports": subagent_reports,
                "evidence": evidence,
                "llm_usage_total": llm_usage_total,
                "llm_cost_total": llm_cost_total,
            }
        )
        envelope = {"result": out, "raw": raw, "ok": True, "error": None}
        return {**state, "final": envelope, "llm_usage_total": llm_usage_total, "llm_cost_total": llm_cost_total}


