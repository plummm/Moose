from __future__ import annotations

from moose.framework.llm_core.config_utils import create_llm_client_from_config, merge_llm_config
import moose.framework.llm_core.config_utils as config_utils_module


def test_merge_llm_config_merges_kwargs_and_default_call_kwargs():
    merged = merge_llm_config(
        {
            "model": "gpt-5.2",
            "enable_multi_stage_reasoning": True,
            "kwargs": {"timeout": 30, "reasoning": {"effort": "medium"}},
            "default_call_kwargs": {"thinking": {"type": "adaptive"}},
        },
        {
            "temperature": 0.2,
            "kwargs": {"reasoning": {"summary": "auto"}, "use_responses_api": True},
            "default_call_kwargs": {"thinking": {"budget": 32}},
        },
        extra_kwargs={"timeout": 10},
        extra_default_call_kwargs={"output_config": {"verbosity": "low"}},
    )

    assert merged["model"] == "gpt-5.2"
    assert merged["enable_multi_stage_reasoning"] is True
    assert merged["temperature"] == 0.2
    assert merged["kwargs"]["timeout"] == 10
    assert merged["kwargs"]["reasoning"] == {"effort": "medium", "summary": "auto"}
    assert merged["kwargs"]["use_responses_api"] is True
    assert merged["default_call_kwargs"]["thinking"] == {"type": "adaptive", "budget": 32}
    assert merged["default_call_kwargs"]["output_config"] == {"verbosity": "low"}


def test_create_llm_client_from_config_filters_prompt_keys_and_preserves_supported_fields(monkeypatch):
    captured = {}

    class FakeLLMClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(config_utils_module, "LLMClient", FakeLLMClient)

    client = create_llm_client_from_config(
        {
            "model": "gpt-5.2",
            "temperature": 0.2,
            "enable_multi_stage_reasoning": True,
            "max_tool_iterations": 24,
            "system_prompt_path": "/tmp/system_prompt.md",
            "skills_dir": "/tmp/skills",
            "default_call_kwargs": {"reasoning": {"effort": "high"}},
            "kwargs": {"use_responses_api": True},
        },
        tools=["tool-a"],
        agent_name="playwright_agent",
        runtime_overrides={"timeout": 4.321},
    )

    assert client is not None
    assert captured["model"] == "gpt-5.2"
    assert captured["temperature"] == 0.2
    assert captured["enable_multi_stage_reasoning"] is True
    assert captured["max_tool_iterations"] == 24
    assert captured["default_call_kwargs"] == {"reasoning": {"effort": "high"}}
    assert captured["tools"] == ["tool-a"]
    assert captured["agent_name"] == "playwright_agent"
    assert captured["timeout"] == 4.321
    assert captured["use_responses_api"] is True
    assert "system_prompt_path" not in captured
    assert "skills_dir" not in captured
