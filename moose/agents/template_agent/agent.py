"""Template agent demonstrating Moose framework usage."""

import asyncio
from typing import Any, Dict, Optional

from moose.framework import BaseAgent
from moose.framework.agent_core.prompt_loader import load_system_prompt
from moose.framework.llm_core import LLMClient, LLMResponse


class TemplateAgent(BaseAgent):
    """
    Template agent showcasing core Moose capabilities:
    - BaseAgent lifecycle + config loading
    - LLMClient usage (async)
    - HTTP endpoints (sync + async handlers)
    - Structured responses with usage/cost
    """

    name = "template_agent"
    description = "Template agent showcasing Moose framework usage"

    def __init__(self, config_path: Optional[str] = None, debug: bool = False):
        super().__init__(config_path, debug=debug)

        custom = self.config.get("custom", {}) if isinstance(self.config.get("custom"), dict) else {}
        llm_cfg = custom.get("llm", {}) if isinstance(custom.get("llm"), dict) else {}

        self.model = str(llm_cfg.get("model", "gpt-4o"))
        self.temperature = float(llm_cfg.get("temperature", 0.3))
        self.max_output_tokens = llm_cfg.get("max_output_tokens")
        self.enable_web_search = bool(llm_cfg.get("enable_web_search", False))
        self.system_prompt = load_system_prompt(
            system_prompt_path=str(llm_cfg.get("system_prompt_path") or ""),
            skills_dir=str(llm_cfg.get("skills_dir") or ""),
            logger=self.logger,
            label="template_agent.llm.system_prompt_path",
            required=True,
        )

        self.llm = LLMClient(
            model=self.model,
            temperature=self.temperature,
            max_output_tokens=self.max_output_tokens,
            enable_web_search=self.enable_web_search,
            **(llm_cfg.get("kwargs", {}) if isinstance(llm_cfg.get("kwargs"), dict) else {}),
        )

        self.logger.info(
            "TemplateAgent initialized (model=%s, temp=%.2f, web_search=%s)",
            self.model,
            self.temperature,
            self.enable_web_search,
        )

    def process(self, input_data: Any) -> Dict[str, Any]:
        """
        Sync entrypoint expected by BaseAgent. Delegates to async analyze().
        """
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Avoid nested asyncio.run; schedule on running loop.
                return asyncio.run_coroutine_threadsafe(self.analyze(input_data), loop).result()
        except RuntimeError:
            pass
        return asyncio.run(self.analyze(input_data))

    async def analyze(self, data: Any) -> Dict[str, Any]:
        """
        Async handler that calls the LLM and returns a structured response.

        Expected input (JSON):
          {
            "text": "your input",
            "task": "summarize | classify | extract | answer",
            "metadata": {...}
          }
        """
        payload = data if isinstance(data, dict) else {"text": str(data)}
        text = str(payload.get("text") or payload.get("input") or "").strip()
        task = str(payload.get("task") or "summarize").strip()

        if not text:
            return {"status": "error", "error": "Missing 'text' in request body"}

        prompt = (
            f"Task: {task}\n"
            "Return a concise response based on the input.\n\n"
            f"Input:\n{text}"
        )

        response: LLMResponse = await self.llm.send_message(
            message=prompt,
            system_message=self.system_prompt,
        )

        return {
            "status": "success",
            "task": task,
            "model": response.model,
            "output": response.content,
            "usage": response.usage,
            "cost": response.cost,
            "request_id": response.request_id,
        }

    def health(self, _data: Dict[str, Any]) -> Dict[str, Any]:
        return {"status": "ok", "agent": self.name, "model": self.model}

    def status_page(self, _data: Dict[str, Any]) -> Any:
        from flask import Response

        page = f"""<!doctype html>
<html>
  <head>
    <meta charset="utf-8"/>
    <meta name="viewport" content="width=device-width, initial-scale=1"/>
    <title>template_agent status</title>
    <style>
      body {{ font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial; margin: 24px; }}
      .card {{ border: 1px solid #e5e5e5; border-radius: 10px; padding: 16px; margin-top: 16px; }}
      code {{ background: #f6f6f6; padding: 2px 6px; border-radius: 4px; }}
    </style>
  </head>
  <body>
    <h2>template_agent — Status</h2>
    <div class="card">
      <div><b>Model:</b> {self.model}</div>
      <div><b>Temperature:</b> {self.temperature}</div>
      <div><b>Web search:</b> {self.enable_web_search}</div>
    </div>
    <div class="card">
      <div><b>Endpoints:</b></div>
      <ul>
        <li><code>POST /process</code> — sync wrapper around LLM call</li>
        <li><code>POST /analyze</code> — async LLM call</li>
        <li><code>GET /health</code> — health check</li>
      </ul>
    </div>
  </body>
</html>"""
        return Response(page, mimetype="text/html")
