# template_agent

A template agent that demonstrates the core Moose agent structure and the main framework features:

- **BaseAgent lifecycle** (config loading, HTTP endpoints)
- **LLMClient** usage (async call, usage/cost metadata)
- **Logging + tracing** (Moose trace DB + `llm.log`)
- **Web UI friendliness** (HTML status page)

This is meant to be a starting point for new agents.

## Files

- `agent.py` — `TemplateAgent` implementation
- `agent_config.json` — example agent configuration
- `README.md` — usage and customization guide

## Quick start

1) Set environment variables (example for OpenAI):

```bash
export OPENAI_API_KEY="..."
export MOOSE_PROJECT_ID="default"
export MOOSE_PROJECTS_DIR="/path/to/projects"
```

2) Run via Moose (recommended):

```bash
python -m moose agent debug --name template_agent
```

3) Call the agent:

```bash
curl -X POST "http://localhost:3509/analyze" \
  -H "Content-Type: application/json" \
  -d '{"text": "Moose is an agent framework built on LangGraph.", "task": "summarize"}'
```

## API endpoints

Configured in `agent_config.json`:

- `POST /process` — sync wrapper around LLM analysis (calls `process`)
- `POST /analyze` — async LLM analysis (calls `analyze`)
- `GET /health` — health check
- `GET /status` — HTML status page

## Input / output

Input example:

```json
{
  "text": "Moose is an agent framework built on LangGraph.",
  "task": "summarize"
}
```

Output example:

```json
{
  "status": "success",
  "task": "summarize",
  "model": "gpt-4o",
  "output": "...",
  "usage": { "input_tokens": 123, "output_tokens": 45, "total_tokens": 168 },
  "cost": 0.00042,
  "request_id": "..."
}
```

## Configuration highlights

Key fields in `agent_config.json`:

- `entry_point` / `entry_class` — where Moose loads the agent.
- `interactive_mode.http_server.endpoints` — maps HTTP routes to methods.
- `custom.llm` — LLM settings used by the agent.
- `docker` — container defaults (ports, env, resources).

You can swap the model to Azure AI Foundry using the `azure:` prefix:

```json
{
  "custom": {
    "llm": {
      "model": "azure:gpt-4o"
    }
  }
}
```

For Azure AI, set:

```bash
export AZURE_AI_CREDENTIAL="..."
export AZURE_AI_ENDPOINT="https://<resource>.services.ai.azure.com/api/projects/<project>"
```

## Moose framework features showcased

- **LLM routing**: `LLMClient` auto-detects provider (e.g., `azure:` prefix).
- **Tracing**: Each HTTP request creates spans in `projects/<id>/logs/trace.db`.
- **Cost tracking**: `LLMResponse` includes usage + cost when available.
- **Web UI**: `/status` provides a simple HTML view you can extend.

## Extending this template

Common next steps:

- Add custom endpoints in `agent_config.json` and implement handlers in `agent.py`.
- Add tools (LangChain tools) and pass them into `LLMClient`.
- Add caching or persistence (SQLite, files, or external stores).
- Add background tasks (pollers, schedulers).

If you need more examples, see other agents in `moose/agents/`.
