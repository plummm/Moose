from __future__ import annotations

import json
from typing import Any, Dict, Optional

from .utils import extract_json, raw_snapshot
from .base import BaseNode


class PromptEngineerNode(BaseNode):
    """
    Node name: `prompt_engineer`
    Config key: `custom.prompt_engineer_llm_config` (via ResearchLead.get_node_llm_config)

    Reads:
    - state.task_instruction (str)
    - state.context_text (str)
    - state.metadata (dict)
    - state.ticker_memory (dict) (optional)

    Writes:
    - state.merge_system_message (str)
    - state.merge_user_message (str)
    - state.abort (bool)
    - on error: state.final envelope (ok=False)
    """

    def __init__(self, *, analyzer: Any, logger: Any):
        super().__init__(analyzer=analyzer, logger=logger)
        self.node_name = "prompt_engineer"
        self.agent_client = self._build_agent_client(node_name="prompt_engineer", tools=[])

    async def run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        task_instruction = str(state.get("task_instruction", "") or "").strip()
        if not task_instruction:
            envelope = {
                "result": {},
                "raw": raw_snapshot(state),
                "ok": False,
                "error": {
                    "code": "task_instruction_required",
                    "message": "Please provide a clear task_instruction for the Investment Research team.",
                },
            }
            return {**state, "final": envelope, "abort": True}

        pe_system = """You are a prompt engineer specializing in finance / investment research workflows.

Goal:
- Given an instruction from a Research Lead, produce a task-appropriate SYSTEM prompt and USER prompt for an Investment Research merge model.
- The merge model will synthesize specialist evidence into a final answer.

You MUST:
- Read and understand the instruction.
- Write a SYSTEM prompt that:
  - sets the role as an experienced investment research analyst
  - defines how to use evidence (prefer verifiable facts, do not fabricate) 
  - Center around the target company or macro/economy
  - defines the input fields the model will receive:
    - instruction
    - context_text (may be empty)
    - metadata (may include url/ticker/etc.)
    - routing/subagent_reports/evidence will be appended later
  - defines the output format as STRICT JSON (no markdown)
  - includes a suggested JSON schema appropriate to the task and briefly explains key fields (including confidence scale if you include confidence)
- Write a USER message that embeds the instruction and includes placeholders/sections for:
  - instruction
  - target company
  - context_text
  - metadata
  - (later appended) routing/subagent_reports/evidence

Return STRICT JSON only:
{
  "system_message": "...",
  "user_message": "..."
}
No extra keys."""

        pe_input = {
            "task_instruction": state.get("task_instruction") or "",
            "context_text": state.get("context_text") or "",
            "metadata": state.get("metadata") or {},
            "ticker_memory": state.get("ticker_memory") or {},
            "target_company": state.get("current_ticker") or "",
        }
        pe_user = f"""Generate prompts for this Investment Research task.

Inputs:
{json.dumps(pe_input, ensure_ascii=False, indent=2)}

Return STRICT JSON only:
{{"system_message":"...","user_message":"..."}}"""

        last_err: Optional[str] = None
        for _ in range(3):
            resp = await self.agent_client.send_message(message=pe_user, system_message=pe_system)
            data = extract_json(getattr(resp, "content", "") or "") or {}
            if not isinstance(data, dict):
                last_err = "Prompt engineer output was not JSON."
                continue
            sm = str(data.get("system_message") or "").strip()
            um = str(data.get("user_message") or "").strip()
            if sm and um:
                return {**state, "merge_system_message": sm, "merge_user_message": um, "abort": False}
            last_err = "Prompt engineer returned empty system_message or user_message."

        raise RuntimeError(f"prompt_engineer failed after 3 attempts: {last_err or 'unknown error'}")


