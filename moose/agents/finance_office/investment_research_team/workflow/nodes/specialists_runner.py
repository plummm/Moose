from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple, Optional, Sequence, Set

from .utils import json_decode_error, normalize_usage, repair_json_once
from .base import BaseNode


@dataclass
class SpecialistResult:
    agent: str
    summary: str
    key_findings: List[str]
    evidence: List[Dict[str, Any]]
    confidence: int
    next_steps: List[Dict[str, Any]]
    raw: Any


class SpecialistsRunnerNode(BaseNode):
    """
    Node name: `run_selected_specialists_parallel`
    Config key: `custom.run_selected_specialists_parallel_llm_config` (via ResearchLead.get_node_llm_config)

    Reads:
    - state.selected_agents (list[str])
    - state.task_instruction (str)
    - state.context_text (str)
    - state.agent_tasks (dict)
    - state.specialist_clients (dict)
    - state.subagent_reports (dict) (prior)
    - state.ticker_memory (dict) (optional, appended to context_text)

    Writes:
    - state.subagent_reports
    - state.evidence
    - llm_usage_total / llm_cost_total
    """

    def __init__(self, *, analyzer: Any, logger: Any, debug_mode: bool):
        super().__init__(analyzer=analyzer, logger=logger)
        self.node_name = "run_selected_specialists_parallel"
        self.debug_mode = bool(debug_mode)
        # Tool-less fallback client (used when a specialist client is missing)
        self.agent_client = self._build_agent_client(node_name="run_selected_specialists_parallel", tools=[])

    @staticmethod
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

    @staticmethod
    def _extract_tool_errors(content: str, data: Dict[str, Any]) -> Optional[str]:
        """
        Extract error messages from tool failures in the LLM response content.
        Looks for FMP API errors and other tool failure messages.
        Returns None if no errors found, otherwise returns a formatted error message.
        """
        import re
        
        errors_found = []
        content_lower = content.lower()
        
        # Check for FMP-specific errors
        if "fmp" in content_lower or "financialmodelingprep" in content_lower:
            # Look for status code 402 (Payment Required)
            status_match = re.search(r"status_code['\"]?\s*:\s*(\d{3})", content, re.IGNORECASE)
            if status_match:
                status_code = status_match.group(1)
                if status_code == "402":
                    # Try to extract URL
                    url_match = re.search(r"url['\"]?\s*:\s*['\"]([^'\"]+)['\"]", content, re.IGNORECASE)
                    url = url_match.group(1) if url_match else "FMP API"
                    # Clean URL to remove API key if present
                    if "apikey=" in url:
                        url = url.split("apikey=")[0] + "apikey=..."
                    errors_found.append(f"FMP request failed: 402 Client Error: Payment Required for url: {url}")
                else:
                    url_match = re.search(r"url['\"]?\s*:\s*['\"]([^'\"]+)['\"]", content, re.IGNORECASE)
                    url = url_match.group(1) if url_match else "FMP API"
                    if "apikey=" in url:
                        url = url.split("apikey=")[0] + "apikey=..."
                    errors_found.append(f"FMP request failed: {status_code} Client Error for url: {url}")
            
            # Look for subscription error message
            sub_match = re.search(r"not available under your current subscription", content, re.IGNORECASE)
            if sub_match and not errors_found:
                errors_found.append("FMP API: Feature not available under current subscription plan")
        
        # Extract from fmp_error_details if present
        fmp_details_match = re.search(r"fmp_error_details['\"]?\s*:\s*\{([^}]+)\}", content, re.IGNORECASE | re.DOTALL)
        if fmp_details_match:
            details = fmp_details_match.group(1)
            status_match = re.search(r"status_code['\"]?\s*:\s*(\d{3})", details, re.IGNORECASE)
            url_match = re.search(r"url['\"]?\s*:\s*['\"]([^'\"]+)['\"]", details, re.IGNORECASE)
            if status_match:
                status_code = status_match.group(1)
                url = url_match.group(1) if url_match else "FMP API"
                if "apikey=" in url:
                    url = url.split("apikey=")[0] + "apikey=..."
                if status_code == "402":
                    errors_found.append(f"FMP request failed: 402 Client Error: Payment Required for url: {url}")
                else:
                    errors_found.append(f"FMP request failed: {status_code} Client Error for url: {url}")
        
        # Extract generic error messages from tool responses
        # Look for 'ok': False patterns
        if "'ok': False" in content or '"ok": False' in content:
            # Try to extract error message
            error_msg_match = re.search(
                r"error['\"]?\s*:\s*\{[^}]*['\"]message['\"]?\s*:\s*['\"]([^'\"]+)['\"]",
                content,
                re.IGNORECASE
            )
            if error_msg_match:
                error_msg = error_msg_match.group(1)
                if error_msg not in errors_found:
                    errors_found.append(error_msg)
        
        # Look for "Failed to fetch data from" patterns
        failed_match = re.search(r"Failed to fetch data from ([^\\n]+)", content, re.IGNORECASE)
        if failed_match and not errors_found:
            errors_found.append(f"Failed to fetch data from {failed_match.group(1).strip()}")
        
        # Fallback: look for any "Error:" pattern
        if not errors_found:
            error_pattern = re.search(r"Error:\s*([^\\n]+)", content, re.IGNORECASE)
            if error_pattern:
                error_text = error_pattern.group(1).strip()
                if error_text and len(error_text) < 500:  # Limit length
                    errors_found.append(f"Tool error: {error_text}")
        
        if errors_found:
            # Return the first meaningful error, or combine them if multiple
            return "; ".join(errors_found[:2])  # Limit to 2 errors to keep it concise
        
        return None

    @staticmethod
    async def run_specialist(
        *,
        agent: str,
        llm_client: Any,
        context_text: str,
        task: Dict[str, Any],
        prior_reports: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SpecialistResult:
        """
        Execute a specialist sub-agent run (with scoped tools already bound to llm_client).
        """
        _ = prior_reports  # reserved for future prompt augmentations

        system_message = f"""You are the `{agent}` sub-agent in an Investment Research team.

You have a scoped toolset (available via tool calling). Your job is to use tools to gather evidence and return a structured analysis report.
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

Cross-specialist help:
Cross-specialist help is not available in this context.
You do not need other tools to help with your analysis, you are only responsible for gathering evidence using your existing tools.
The final report will be produced by a different agent with combination of evidences from all the specialists, including the evidence you wanted from other tools, so please proceed your analysis even it will be incomplete.
"""

        effective_context = (context_text or "").strip() or "(none provided)"

        current_ticker = str((task or {}).get("current_ticker") or "").strip()
        ticker_list = (task or {}).get("ticker_list", [])
        ticker_info = ""
        if isinstance(ticker_list, list) and ticker_list:
            # New mode: Multiple tickers
            ticker_info = f"Ticker list: {', '.join(str(t) for t in ticker_list if t)}"
        else:
            # Old mode: Single ticker
            ticker_info = f"Current ticker (empty means macro/economy mode): {current_ticker or 'MACRO/ECONOMY'}"
        
        user_message = f"""You are `{agent}`. Execute your part of the research.

Task (from Research Lead):
{json.dumps(task or {}, ensure_ascii=False, indent=2)}

Metadata (may be empty): 
{json.dumps(metadata or {}, ensure_ascii=False, indent=2)}

{ticker_info}

Context (may be a news article, notes, or a user instruction):
{effective_context}

Return STRICT JSON only."""

        resp = await llm_client.send_message(message=user_message, system_message=system_message)
        content = getattr(resp, "content", "") or ""
        if type(content) != str:
            raise ValueError(f"Tool {agent} repsonse is not a string: {type(content)}")
        data = SpecialistsRunnerNode._extract_json(content)
        if data is None:
            # One-shot JSON repair retry
            try:
                repaired = await repair_json_once(llm_client, bad_output=str(content), error_hint=json_decode_error(content))
                repaired_content = getattr(repaired, "content", "") or ""
                if type(repaired_content) != str:
                    repaired_content = str(repaired_content)
                data = SpecialistsRunnerNode._extract_json(repaired_content)
            except Exception:
                data = None
        data = data or {}

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

    @staticmethod
    def filter_langchain_tools_by_name(all_tools: Sequence[Any], allowed: Set[str]) -> List[Any]:
        out: List[Any] = []
        for t in all_tools or []:
            nm = getattr(t, "name", None)
            if isinstance(nm, str) and nm in allowed:
                out.append(t)
        return out

    @staticmethod
    def build_specialist_llm_clients(
        *,
        analyzer: Any,
        base_model: str,
        base_temperature: float,
        llm_extra_params: Optional[Dict[str, Any]],
        tools_provider: Any,
        meeting_room_enabled: bool = False,
        max_tool_iterations: Optional[int] = 20,
        agent_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Build per-specialist LLMClient instances with scoped tools bound.
        """
        from moose.framework.llm_core import LLMClient

        scopes = analyzer.build_tool_scopes()
        all_tools = tools_provider.get_langchain_tools(meeting_room_enabled=meeting_room_enabled)

        clients: Dict[str, Any] = {}
        for agent, allowed_names in (scopes or {}).items():
            tool_list = SpecialistsRunnerNode.filter_langchain_tools_by_name(all_tools, allowed_names)
            clients[agent] = LLMClient(
                model=base_model,
                temperature=base_temperature,
                tools=tool_list,
                enable_multi_stage_reasoning=True,
                agent_name=str(agent_name or "").strip() or None,
                max_tool_iterations=max_tool_iterations,
                **(llm_extra_params or {}),
            )
        return clients

    async def _run_one(
        self,
        *,
        sem: asyncio.Semaphore,
        agent_name: str,
        task_instruction: str,
        context_text: str,
        agent_tasks: Dict[str, Any],
        clients: Dict[str, Any],
        prior_reports: Dict[str, Any],
        current_ticker: str,
        ticker_list: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Tuple[str, Dict[str, Any], List[Dict[str, Any]], Dict[str, int], float]:
        async with sem:
            task = dict((agent_tasks or {}).get(agent_name, {}) or {})
            task.setdefault("task_instruction", task_instruction)
            task["current_ticker"] = str(current_ticker or "").strip()
            if ticker_list is not None:
                task["ticker_list"] = ticker_list

            client = clients.get(agent_name) or self.agent_client

            res = await self.run_specialist(
                agent=agent_name,
                llm_client=client,
                context_text=context_text,
                task=task,
                prior_reports=prior_reports,
                metadata=metadata,
            )

            normalized: List[Dict[str, Any]] = []
            for e in (res.evidence or []):
                if not isinstance(e, dict):
                    continue
                ee = dict(e)
                ee.setdefault("source", agent_name)
                ee.setdefault("tool", ee.get("tool") or "")
                ee.setdefault("key_fields", ee.get("key_fields") or {})
                ee.setdefault("excerpt", ee.get("excerpt") or "")
                normalized.append(ee)

            # Extract error messages from tool failures
            error_msg = None
            try:
                if isinstance(res.raw, dict):
                    raw_text = res.raw.get("text")
                    parsed_data = res.raw.get("parsed", {}) or {}
                    
                    # Try to get content from response object
                    content_str = ""
                    if raw_text:
                        content = getattr(raw_text, "content", "") or ""
                        if isinstance(content, list):
                            # Handle list of content blocks (e.g., from Gemini)
                            content_str = " ".join(str(c.get("text", "")) if isinstance(c, dict) else str(c) for c in content)
                        else:
                            content_str = str(content) if content else ""
                    
                    # Also check raw_response if available
                    if not content_str and hasattr(raw_text, "raw_response"):
                        raw_resp = getattr(raw_text, "raw_response")
                        if raw_resp:
                            # Try to extract content from raw response
                            if hasattr(raw_resp, "content"):
                                raw_content = getattr(raw_resp, "content", "")
                                if isinstance(raw_content, list):
                                    content_str = " ".join(str(c.get("text", "")) if isinstance(c, dict) else str(c) for c in raw_content)
                                else:
                                    content_str = str(raw_content) if raw_content else ""
                    
                    # Extract errors from content
                    if content_str:
                        error_msg = SpecialistsRunnerNode._extract_tool_errors(content_str, parsed_data)
            except Exception:
                pass

            report = {
                "summary": res.summary,
                "key_findings": res.key_findings,
                "confidence": res.confidence,
                "next_steps": res.next_steps,
                "tool_evidence_refs": [],
            }
            
            # Add error field if tool failures were detected
            if error_msg:
                report["error"] = error_msg
            llm_resp = None
            try:
                if isinstance(res.raw, dict):
                    llm_resp = res.raw.get("text")
            except Exception:
                llm_resp = None
            usage = normalize_usage(getattr(llm_resp, "usage", None))
            try:
                cost = float(getattr(llm_resp, "cost", 0.0) or 0.0)
            except Exception:
                cost = 0.0
            return agent_name, report, normalized, usage, cost

    async def run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        selected_agents = [str(x).strip() for x in (state.get("selected_agents") or []) if str(x).strip()]
        if not selected_agents:
            return state

        context_text = str(state.get("context_text", "") or "")
        ticker_memory = state.get("ticker_memory") if isinstance(state.get("ticker_memory"), dict) else {}
        if ticker_memory:
            addon = "\n\nTICKER MONTHLY MEMORY (short-lived; may be stale; do not treat as primary source):\n"
            addon += json.dumps(ticker_memory, ensure_ascii=False, indent=2)[:12000]
            context_text = (context_text or "") + addon

        task_instruction = str(state.get("task_instruction", "") or "")
        agent_tasks = state.get("agent_tasks", {}) or {}
        clients = state.get("specialist_clients", {}) or {}
        prior_reports = state.get("subagent_reports", {}) or {}
        per_ticker_merge_mode = bool(state.get("per_ticker_merge_mode", False))
        metadata = state.get("metadata", {}) or {}
        
        # Determine ticker info based on mode
        current_ticker = ""
        ticker_list = None
        if per_ticker_merge_mode:
            # New mode: Use ticker_list
            ticker_list = state.get("ticker_list", []) if isinstance(state.get("ticker_list"), list) else []
        else:
            # Old mode: Use current_ticker
            current_ticker = str(state.get("current_ticker") or "").strip()

        try:
            max_parallel = int(state.get("max_parallel_specialists") or 5)
        except Exception:
            max_parallel = 5
        max_parallel = max(1, min(5, max_parallel))
        sem = asyncio.Semaphore(max_parallel)

        try:
            run_sequential = bool(self.debug_mode)
            if run_sequential:
                results: List[Any] = []
                for a in selected_agents:
                    try:
                        results.append(
                            await self._run_one(
                                sem=sem,
                                agent_name=a,
                                task_instruction=task_instruction,
                                context_text=context_text,
                                agent_tasks=agent_tasks,
                                clients=clients,
                                prior_reports=prior_reports,
                                current_ticker=current_ticker,
                                ticker_list=ticker_list,
                                metadata=metadata,
                            )
                        )
                    except Exception as e:
                        results.append(e)
            else:
                results = await asyncio.gather(
                    *[
                        self._run_one(
                            sem=sem,
                            agent_name=a,
                            task_instruction=task_instruction,
                            context_text=context_text,
                            agent_tasks=agent_tasks,
                            clients=clients,
                            prior_reports=prior_reports,
                            current_ticker=current_ticker,
                            ticker_list=ticker_list,
                            metadata=metadata,
                        )
                        for a in selected_agents
                    ],
                    return_exceptions=True,
                )
        finally:
            pass

        reports_out = dict(prior_reports)
        evidence_out = list(state.get("evidence") or [])
        llm_usage_total = normalize_usage(state.get("llm_usage_total"))
        try:
            llm_cost_total = float(state.get("llm_cost_total") or 0.0)
        except Exception:
            llm_cost_total = 0.0

        for item in results:
            if isinstance(item, Exception):
                nm = "unknown"
                try:
                    nm = getattr(item, "agent", None) or nm
                except Exception:
                    pass
                reports_out[str(nm)] = {
                    "summary": f"Specialist failed: {item}",
                    "key_findings": [],
                    "confidence": 1,
                    "next_steps": [],
                    "tool_evidence_refs": [],
                }
                continue

            agent_name, report, normalized, usage, cost = item
            start_idx = len(evidence_out)
            evidence_out.extend(normalized)
            report["tool_evidence_refs"] = list(range(start_idx, start_idx + len(normalized)))
            reports_out[agent_name] = report
            llm_usage_total["input_tokens"] += int(usage.get("input_tokens", 0) or 0)
            llm_usage_total["output_tokens"] += int(usage.get("output_tokens", 0) or 0)
            llm_usage_total["total_tokens"] += int(usage.get("total_tokens", 0) or 0)
            llm_cost_total += float(cost or 0.0)

        return {
            **state,
            "subagent_reports": reports_out,
            "evidence": evidence_out,
            "llm_usage_total": llm_usage_total,
            "llm_cost_total": llm_cost_total,
        }


