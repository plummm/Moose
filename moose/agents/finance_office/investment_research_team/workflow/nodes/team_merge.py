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
        per_ticker_merge_mode = bool(state.get("per_ticker_merge_mode", True))
        
        if per_ticker_merge_mode:
            # New mode: Per-ticker merge calls
            return await self._run_per_ticker_merge(state)
        else:
            # Old mode: Single ticker merge
            return await self._run_single_ticker_merge(state)

    @staticmethod
    def _json_decode_error(text: str) -> str:
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

    async def _repair_json_once(
        self,
        *,
        bad_output: str,
        error_hint: str,
    ) -> Any:
        """
        Ask the model to re-emit a STRICT JSON object, given its previous invalid output.
        Returns the new LLM response object.
        """
        repair_system_message = (
            "You are a JSON repair tool.\n"
            "CRITICAL OUTPUT REQUIREMENT:\n"
            "- Return ONLY a single valid JSON object (no markdown fences, no leading/trailing quotes, no commentary).\n"
            "- JSON strings MUST be valid: do not include raw double quotes (\") inside string values.\n"
            "  If you need to quote text, use \\\" ... \\\" or use Chinese quotes 「...」.\n"
            "- Use \\n for newlines inside string values.\n"
        )

        bad_out = (bad_output or "").strip()

        repair_user_message = (
            "Your previous output was invalid JSON and could not be parsed.\n"
            f"Parser error: {error_hint}\n\n"
            "Fix the INVALID OUTPUT below so it becomes strict valid JSON.\n"
            "Keep the same keys/structure and preserve content as much as possible.\n\n"
            "INVALID OUTPUT:\n"
            + bad_out
            + "\n\nNow output the corrected JSON object ONLY."
        )
        return await self.agent_client.send_message(message=repair_user_message, system_message=repair_system_message)
    
    async def _run_single_ticker_merge(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Old mode: Single LLM call for one ticker."""
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

        update_memory = bool((routing or {}).get("update_memory"))
        ticker_memory = state.get("ticker_memory") if isinstance(state.get("ticker_memory"), dict) else {}
        if ticker_memory and not update_memory:
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

        content = getattr(resp, "content", "") or ""
        out = extract_json(content)
        if out is None:
            err = self._json_decode_error(content)
            try:
                self.logger.warning("team_merge: invalid JSON from LLM; retrying once. %s", err)
            except Exception:
                pass
            resp2 = await self._repair_json_once(
                bad_output=str(content),
                error_hint=err,
            )
            # accumulate usage/cost for the repair attempt
            llm_usage_total["input_tokens"] += int(getattr(resp2, "usage", {}).get("input_tokens", 0) or 0) if getattr(resp2, "usage", None) else 0
            llm_usage_total["output_tokens"] += int(getattr(resp2, "usage", {}).get("output_tokens", 0) or 0) if getattr(resp2, "usage", None) else 0
            llm_usage_total["total_tokens"] += int(getattr(resp2, "usage", {}).get("total_tokens", 0) or 0) if getattr(resp2, "usage", None) else 0
            try:
                llm_cost_total += float(getattr(resp2, "cost", 0.0) or 0.0)
            except Exception:
                pass
            out = extract_json(getattr(resp2, "content", "") or "")
        out = out or {}
        if not isinstance(out, dict):
            out = {}
        out.setdefault("ticker", current_ticker)

        # Align output schema with per-ticker merge:
        # {"by_ticker": {TICKER: <analysis>}, "tickers": [TICKER, ...]}
        ticker_list = state.get("ticker_list", []) if isinstance(state.get("ticker_list"), list) else []
        if ticker_list:
            results_by_ticker: Dict[str, Any] = {}
            for t in ticker_list:
                tk = str(t).upper().strip()
                # Replicate the single analysis across all tickers, but set per-entry ticker field.
                try:
                    entry = dict(out)
                except Exception:
                    entry = out
                if isinstance(entry, dict):
                    entry["ticker"] = tk
                results_by_ticker[tk] = entry
            final_result = {"by_ticker": results_by_ticker, "tickers": ticker_list}
        else:
            tk = str(current_ticker).upper().strip()
            results_by_ticker = {tk: out}
            if isinstance(out, dict):
                out["ticker"] = tk
            final_result = {"by_ticker": results_by_ticker, "tickers": [current_ticker]}

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
        envelope = {"result": final_result, "raw": raw, "ok": True, "error": None}
        return {**state, "final": envelope, "llm_usage_total": llm_usage_total, "llm_cost_total": llm_cost_total}
    
    async def _run_per_ticker_merge(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """New mode: Per-ticker merge calls with shared evidence."""
        merge_system_message = str(state.get("merge_system_message") or "")
        base_merge_user_message = str(state.get("merge_user_message") or "")
        routing = state.get("routing", {}) or {}
        subagent_reports = state.get("subagent_reports", {}) or {}
        evidence = state.get("evidence", []) or []
        ticker_list = state.get("ticker_list", []) if isinstance(state.get("ticker_list"), list) else []

        if not merge_system_message or not base_merge_user_message:
            try:
                self.logger.error(
                    "team_merge missing required prompts: merge_system_message/merge_user_message must be provided in state"
                )
            except Exception:
                pass
            raise RuntimeError("team_merge missing required prompts (merge_system_message/merge_user_message)")

        update_memory = bool((routing or {}).get("update_memory"))
        ticker_memory = state.get("ticker_memory") if isinstance(state.get("ticker_memory"), dict) else {}
        
        # Shared context addon (same for all tickers)
        shared_addon = ""
        if evidence or subagent_reports or routing:
            shared_addon = "\n\nADDITIONAL CONTEXT (from sub-agents):\n"
            shared_addon += "Routing:\n" + json.dumps(routing, ensure_ascii=False, indent=2) + "\n"
            shared_addon += "Sub-agent reports:\n" + json.dumps(subagent_reports, ensure_ascii=False, indent=2)[:12000] + "\n"
            shared_addon += "Evidence:\n" + json.dumps(evidence, ensure_ascii=False, indent=2)[:12000] + "\n"
        
        # Per-ticker merge results
        results_by_ticker: Dict[str, Any] = {}
        llm_usage_total = normalize_usage(state.get("llm_usage_total"))
        llm_cost_total = float(state.get("llm_cost_total") or 0.0)
        
        for ticker in ticker_list:
            ticker = str(ticker).upper().strip()
            display_ticker = ticker or "MACRO/ECONOMY"
            
            # Build ticker-specific message
            ticker_merge_message = base_merge_user_message
            
            # Format base message with ticker if it has placeholder
            if base_merge_user_message:
                try:
                    ticker_merge_message = base_merge_user_message.format(display_ticker)
                except Exception:
                    ticker_merge_message = base_merge_user_message
            
            # Add ticker-specific memory if available and not updating memory from new external context
            if ticker in ticker_memory and not update_memory:
                ticker_merge_message = (ticker_merge_message or "") + (
                    "\n\nTICKER MONTHLY MEMORY:\n" + json.dumps({ticker: ticker_memory[ticker]}, ensure_ascii=False, indent=2)[:12000] + "\n"
                )
            
            # Add shared context
            ticker_merge_message = (ticker_merge_message or "") + shared_addon
            
            # Add current ticker info
            ticker_merge_message = (ticker_merge_message or "") + f"\n\nCURRENT_TICKER: {display_ticker}\n"
            
            # Make LLM call for this ticker
            resp = await self.agent_client.send_message(message=ticker_merge_message, system_message=merge_system_message)
            
            # Accumulate usage/cost
            llm_usage_total["input_tokens"] += int(getattr(resp, "usage", {}).get("input_tokens", 0) or 0) if getattr(resp, "usage", None) else 0
            llm_usage_total["output_tokens"] += int(getattr(resp, "usage", {}).get("output_tokens", 0) or 0) if getattr(resp, "usage", None) else 0
            llm_usage_total["total_tokens"] += int(getattr(resp, "usage", {}).get("total_tokens", 0) or 0) if getattr(resp, "usage", None) else 0
            try:
                llm_cost_total += float(getattr(resp, "cost", 0.0) or 0.0)
            except Exception:
                pass
            
            # Extract result
            content = getattr(resp, "content", "") or ""
            out = extract_json(content)
            if out is None:
                err = self._json_decode_error(content)
                try:
                    self.logger.warning("team_merge(%s): invalid JSON from LLM; retrying once. %s", display_ticker, err)
                except Exception:
                    pass
                resp2 = await self._repair_json_once(
                    bad_output=str(content),
                    error_hint=err,
                )
                # accumulate usage/cost for the repair attempt
                llm_usage_total["input_tokens"] += int(getattr(resp2, "usage", {}).get("input_tokens", 0) or 0) if getattr(resp2, "usage", None) else 0
                llm_usage_total["output_tokens"] += int(getattr(resp2, "usage", {}).get("output_tokens", 0) or 0) if getattr(resp2, "usage", None) else 0
                llm_usage_total["total_tokens"] += int(getattr(resp2, "usage", {}).get("total_tokens", 0) or 0) if getattr(resp2, "usage", None) else 0
                try:
                    llm_cost_total += float(getattr(resp2, "cost", 0.0) or 0.0)
                except Exception:
                    pass
                out = extract_json(getattr(resp2, "content", "") or "")
            out = out or {}
            if not isinstance(out, dict):
                out = {}
            out.setdefault("ticker", ticker)
            
            results_by_ticker[ticker] = out
        
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
        
        # Return results in expected format: {"by_ticker": {...}, "tickers": [...]}
        final_result = {"by_ticker": results_by_ticker, "tickers": ticker_list}
        envelope = {"result": final_result, "raw": raw, "ok": True, "error": None}
        return {**state, "final": envelope, "llm_usage_total": llm_usage_total, "llm_cost_total": llm_cost_total}


