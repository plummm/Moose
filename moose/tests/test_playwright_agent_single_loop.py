from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

from moose.agents.playwright_agent.single_loop_agent import PlaywrightAgent
from moose.agents.playwright_agent.browser.controller import BrowserController
from moose.agents.playwright_agent.browser.event_logger import BrowserEventLogger
from moose.agents.playwright_agent.browser.loop_runtime import BrowserAutomation
from moose.agents.playwright_agent.browser.models import RunConfig, RunState
from moose.agents.playwright_agent.browser.ref_page_actions import PageActionsFeature
from moose.agents.playwright_agent.browser.session import BrowserSessionManager


class FakePage:
    def __init__(self, url: str, title: str):
        self.url = url
        self._title = title
        self._closed = False
        self.default_timeout = None
        self.default_navigation_timeout = None

    def set_default_timeout(self, value: float) -> None:
        self.default_timeout = value

    def set_default_navigation_timeout(self, value: float) -> None:
        self.default_navigation_timeout = value

    def is_closed(self) -> bool:
        return self._closed

    async def title(self) -> str:
        return self._title

    async def close(self) -> None:
        self._closed = True

    async def screenshot(self, path: str, full_page: bool = True) -> bytes:
        data = b"fake-image-bytes"
        Path(path).write_bytes(data)
        return data


class FakeContext:
    def __init__(self, pages: list[FakePage]):
        self.pages = pages


class FakeEventLogger:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.started = False
        self.initialized_runs: list[tuple[str, str, str]] = []
        self.completed_runs: list[tuple[str, str, str | None]] = []
        self.closed = False

    def start(self) -> None:
        self.started = True

    def init_run(self, run_id: str, request_id: str, agent_name: str) -> None:
        self.initialized_runs.append((run_id, request_id, agent_name))

    def complete_run(self, run_id: str, status: str, error: str | None = None) -> None:
        self.completed_runs.append((run_id, status, error))

    def close(self) -> None:
        self.closed = True


class FakeController:
    async def describe_state(self, run_state: RunState) -> dict:
        return {
            "active_tab_id": run_state.active_page_id,
            "url": run_state.current_url,
            "title": "Fake title",
            "tabs": [{"page_id": run_state.active_page_id, "is_active": True, "url": run_state.current_url}],
            "snapshot_id": None,
        }


class FakePageActions:
    def __init__(self, event_logger: FakeEventLogger):
        self.event_logger = event_logger
        self.controller = FakeController()
        self.bound_states: list[RunState | None] = []

    def set_run_state(self, run_state: RunState | None) -> None:
        self.bound_states.append(run_state)

    def get_tools(self) -> list:
        return [SimpleNamespace(name="browser_snapshot")]


class FakeNetworkInspector:
    def __init__(self):
        self.bound_states: list[RunState | None] = []
        self.attached = 0
        self.started = 0
        self.stopped = 0
        self.detached = 0

    def set_run_state(self, run_state: RunState | None) -> None:
        self.bound_states.append(run_state)

    async def attach_listeners(self, run_state: RunState) -> None:
        self.attached += 1

    async def start_capture(self, run_state: RunState) -> dict:
        self.started += 1
        return {"ok": True}

    async def stop_capture(self, run_state: RunState) -> dict:
        self.stopped += 1
        return {"ok": True}

    async def detach_listeners(self, run_state: RunState) -> None:
        self.detached += 1

    async def summarize_capture(self, run_state: RunState, **kwargs) -> dict:
        return {
            "ok": True,
            "counts": {
                "requests": 3,
                "failed_requests": 1,
                "console": 2,
                "console_errors": 1,
                "websocket": 0,
                "page_errors": 0,
            },
            "recent_requests": [
                {
                    "method": "GET",
                    "url": "https://start.example/api",
                    "status_code": 200,
                    "event_type": "response",
                }
            ],
            "failed_requests": [
                {
                    "method": "GET",
                    "url": "https://start.example/missing",
                    "status_code": 404,
                    "event_type": "response",
                }
            ],
            "console_errors": [
                {
                    "level": "error",
                    "text": "Request failed",
                    "page_url": "https://start.example",
                }
            ],
            "recent_websocket": [],
        }


class FakeDownloads:
    def __init__(self):
        self.bound_states: list[RunState | None] = []
        self.attached = 0
        self.detached = 0

    def set_run_state(self, run_state: RunState | None) -> None:
        self.bound_states.append(run_state)

    async def attach_listener(self, run_state: RunState) -> None:
        self.attached += 1

    async def detach_listener(self, run_state: RunState) -> None:
        self.detached += 1

    async def list_downloads(self, run_state: RunState, limit: int = 50) -> dict:
        return {"ok": True, "downloads": [{"download_id": "download_1", "status": "saved"}]}


class FakeSessionManager:
    def __init__(self, run_state: RunState):
        self.run_state = run_state
        self.shutdown_calls = 0

    async def start(self, run_config: RunConfig) -> RunState:
        return self.run_state

    async def shutdown(self, run_state: RunState | None) -> None:
        self.shutdown_calls += 1


@dataclass
class FakeLoopResult:
    ok: bool = True
    run_id: str = "loop_run"
    request_id: str = "loop_request"
    final_response: object = field(
        default_factory=lambda: SimpleNamespace(content="completed", model="fake-model")
    )
    total_usage: dict = None
    total_cost: float = 0.25
    error_message: str | None = None

    def __post_init__(self) -> None:
        if self.total_usage is None:
            self.total_usage = {"input_tokens": 11, "output_tokens": 7, "total_tokens": 18}

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "request_id": self.request_id,
            "events": [{"event_type": "run_start"}, {"event_type": "run_end"}],
            "total_usage": self.total_usage,
            "total_cost": self.total_cost,
        }


class FakeLLM:
    def __init__(self, loop_result: FakeLoopResult):
        self.loop_result = loop_result
        self.calls: list[dict] = []

    async def collect_agent_loop(self, **kwargs):
        self.calls.append(kwargs)
        return self.loop_result


class FakeRefinerLLM:
    def __init__(self, content: str):
        self.content = content
        self.calls: list[dict] = []

    async def send_message(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(content=self.content)


class FakeTextLLM:
    def __init__(self, content: str):
        self.content = content
        self.calls: list[dict] = []

    async def send_message(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(content=self.content, model="fake-summary-model")


def test_page_actions_exposes_ref_based_tools(tmp_path):
    feature = PageActionsFeature(
        session_manager=BrowserSessionManager(downloads_dir=str(tmp_path)),
        event_logger=BrowserEventLogger(tmp_path / "activity.db"),
    )
    tool_names = {tool.name for tool in feature.get_tools()}
    assert {
        "browser_navigate",
        "browser_snapshot",
        "browser_click",
        "browser_type",
        "browser_press",
        "browser_scroll",
        "browser_wait",
        "browser_screenshot",
        "browser_list_tabs",
        "browser_select_tab",
        "browser_close_tab",
        "browser_new_tab",
    }.issubset(tool_names)


def test_session_manager_tracks_tabs_and_snapshot_ids():
    manager = BrowserSessionManager(downloads_dir="/tmp/playwright-agent-tests")
    page_one = FakePage("https://one.example", "One")
    context = FakeContext([page_one])
    run_state = RunState(
        run_id="run-1",
        request_id="req-1",
        active=True,
        current_url=page_one.url,
        browser_context=context,
        page=page_one,
        metadata={"timeout_ms": 4321},
    )

    new_ids = manager.sync_pages(run_state, preferred_page=page_one)
    assert new_ids == ["tab_1"]
    assert run_state.active_page_id == "tab_1"

    page_two = FakePage("https://two.example", "Two")
    context.pages.append(page_two)
    new_ids = manager.sync_pages(run_state, preferred_page=page_two)
    assert new_ids == ["tab_2"]
    assert run_state.active_page_id == "tab_2"

    assert manager.next_snapshot_id(run_state) == "snapshot_1"
    assert manager.next_snapshot_id(run_state) == "snapshot_2"


def test_controller_screenshot_returns_base64_payload(tmp_path):
    page = FakePage("https://shot.example", "Shot")
    run_state = RunState(
        run_id="shot-run",
        request_id="shot-req",
        active=True,
        current_url=page.url,
        active_page_id="tab_1",
        browser_context=FakeContext([page]),
        page=page,
        metadata={"screenshots_dir": str(tmp_path)},
    )
    controller = BrowserController(
        session_manager=BrowserSessionManager(downloads_dir=str(tmp_path)),
        event_logger=None,
    )

    result = asyncio.run(controller.screenshot(run_state))

    assert result["mime_type"] == "image/png"
    assert result["base64"] == base64.b64encode(b"fake-image-bytes").decode("ascii")
    assert result["path"].endswith(".png")
    assert Path(result["path"]).exists()


def test_controller_finalize_after_dom_mutation_suggests_snapshot(tmp_path):
    page = FakePage("https://after-click.example", "After Click")
    context = FakeContext([page])
    run_state = RunState(
        run_id="finalize-run",
        request_id="finalize-req",
        active=True,
        current_url=page.url,
        active_page_id=None,
        browser_context=context,
        page=page,
        metadata={"screenshots_dir": str(tmp_path)},
    )
    session_manager = BrowserSessionManager(downloads_dir=str(tmp_path))
    session_manager.sync_pages(run_state, preferred_page=page)
    controller = BrowserController(session_manager=session_manager, event_logger=None)

    result = asyncio.run(
        controller.finalize_after_dom_mutation(
            run_state,
            previous_tab_ids=["tab_1"],
            action="click",
        )
    )

    assert result["snapshot_stale"] is True
    assert result["suggested_next_tool"] == "browser_snapshot"
    assert "Verify the page state after browser_click." == result["suggested_next_tool_reason"]


def test_browser_automation_uses_collect_agent_loop_and_returns_loop_payload(tmp_path):
    run_state = RunState(
        run_id="browser-run",
        request_id="request-1",
        active=True,
        current_url="https://start.example",
        active_page_id="tab_1",
        metadata={
            "downloads_dir": str(tmp_path / "downloads"),
            "screenshots_dir": str(tmp_path / "screenshots"),
        },
    )
    session_manager = FakeSessionManager(run_state)
    event_logger = FakeEventLogger(tmp_path / "activity.db")
    page_actions = FakePageActions(event_logger)
    network_inspector = FakeNetworkInspector()
    downloads = FakeDownloads()
    automation = BrowserAutomation(
        session_manager=session_manager,
        page_actions=page_actions,
        network_inspector=network_inspector,
        downloads=downloads,
        llm_settings={},
        logger=None,
    )

    fake_loop_result = FakeLoopResult()
    fake_llm = FakeLLM(fake_loop_result)
    automation._create_worker_llm_client = lambda tools: fake_llm  # type: ignore[method-assign]

    run_config = RunConfig(
        run_id="browser-run",
        request_id="request-1",
        start_url="https://start.example",
        system_prompt="system prompt",
        user_prompt="Find the download link",
    )

    result = asyncio.run(
        automation.run(
            run_config=run_config,
            tools=[SimpleNamespace(name="browser_snapshot")],
            include_loop_events=False,
        )
    )

    assert result["status"] == "success"
    assert result["output"] == "completed"
    assert result["final_url"] == "https://start.example"
    assert result["downloads"] == [{"download_id": "download_1", "status": "saved"}]
    assert result["network"]["counts"]["requests"] == 3
    assert result["network"]["failed_requests"][0]["status_code"] == 404
    assert result["loop"]["run_id"] == "loop_run"
    assert "events" not in result["loop"]
    assert result["artifacts"]["activity_db_path"].endswith("activity.db")
    assert fake_llm.calls[0]["system_message"] == "system prompt"
    assert fake_llm.calls[0]["raise_on_error"] is False
    assert "Start URL: https://start.example" in fake_llm.calls[0]["message"]
    assert network_inspector.attached == 1
    assert network_inspector.started == 1
    assert network_inspector.stopped == 1
    assert network_inspector.detached == 1
    assert downloads.attached == 1
    assert downloads.detached == 1
    assert session_manager.shutdown_calls == 1


def test_task_refiner_refines_prompt_and_target_url():
    agent = object.__new__(PlaywrightAgent)
    agent.task_refiner_cfg = {}
    agent.task_refiner_system_prompt = "Refine"
    agent.task_refiner_user_prompt_template = "Prompt: {{user_prompt}}\nURL: {{start_url}}"
    fake_llm = FakeRefinerLLM(
        '{"refined_user_prompt":"Find the download URL and save the final link.",'
        '"target_url":"https://example.com/downloads","return_format":"JSON",'
        '"notes":["Prefer direct file URL"]}'
    )
    agent._create_task_refiner_llm_client = lambda: fake_llm  # type: ignore[method-assign]

    result = asyncio.run(agent._run_task_refiner({"user_prompt": "find download url on https://example.com"}))

    assert result["status"] == "ok"
    assert result["refined_user_prompt"] == "Find the download URL and save the final link."
    assert result["target_url"] == "https://example.com/downloads"
    assert result["return_format"] == "JSON"
    assert result["notes"] == ["Prefer direct file URL"]
    assert "https://example.com" in fake_llm.calls[0]["message"]


def test_summarizer_uses_requested_return_format_and_omits_base64():
    agent = object.__new__(PlaywrightAgent)
    agent.summarizer_cfg = {}
    agent.summarizer_system_prompt = "Summarize"
    agent.summarizer_user_prompt_template = (
        "Original user request:\n{{raw_user_prompt}}\n\n"
        "User-specified return format:\n{{user_specified_return_format}}\n\n"
        "Structured execution summary:\n{{execution_summary_json}}"
    )
    fake_llm = FakeTextLLM("Final summary")
    agent._create_summarizer_llm_client = lambda: fake_llm  # type: ignore[method-assign]

    result = asyncio.run(
        agent._run_summarizer(
            payload={"user_prompt": "Find the download URL and return JSON"},
            task_refiner_result={
                "raw_user_prompt": "Find the download URL and return JSON",
                "refined_user_prompt": "Find the download URL autonomously.",
                "target_url": "https://example.com",
                "return_format": "JSON",
                "notes": ["Keep the final answer structured."],
            },
            browser_result={
                "status": "success",
                "output": "Found the final file URL.",
                "start_url": "https://example.com",
                "final_url": "https://example.com/download.zip",
                "downloads": [
                    {
                        "download_id": "download_1",
                        "status": "saved",
                        "suggested_filename": "download.zip",
                        "saved_path": "/tmp/download.zip",
                    }
                ],
                "network": {
                    "counts": {
                        "requests": 4,
                        "failed_requests": 1,
                        "console": 1,
                        "console_errors": 1,
                        "websocket": 0,
                        "page_errors": 0,
                    },
                    "failed_requests": [
                        {
                            "url": "https://example.com/missing",
                            "status_code": 404,
                            "event_type": "response",
                        }
                    ],
                    "console_errors": [
                        {
                            "level": "error",
                            "text": "Request failed",
                            "page_url": "https://example.com",
                        }
                    ],
                    "recent_requests": [],
                    "recent_websocket": [],
                },
                "artifacts": {
                    "downloads_dir": "/tmp/downloads",
                    "screenshots_dir": "/tmp/screenshots",
                    "activity_db_path": "/tmp/activity.db",
                },
                "loop": {
                    "run_id": "loop-1",
                    "events": [
                        {
                            "event_type": "tool_call_success",
                            "iteration": 1,
                            "tool_name": "browser_screenshot",
                            "tool_args": {"full_page": True},
                            "tool_result": {
                                "ok": True,
                                "mime_type": "image/png",
                                "base64": "AAAA",
                                "path": "/tmp/example.png",
                            },
                        }
                    ],
                },
            },
        )
    )

    assert result["status"] == "ok"
    assert result["content"] == "Final summary"
    assert result["requested_return_format"] == "JSON"
    assert "User-specified return format:\nJSON" in fake_llm.calls[0]["message"]
    assert '"failed_requests": 1' in fake_llm.calls[0]["message"]
    assert "/tmp/downloads" in fake_llm.calls[0]["message"]
    assert "<omitted>" in fake_llm.calls[0]["message"]


def test_process_uses_refined_prompt_before_browser_run():
    agent = object.__new__(PlaywrightAgent)
    agent.browser_system_prompt = "Browser system"
    captured: dict = {}

    async def fake_run(*, run_config, tools, include_loop_events):
        captured["run_config"] = run_config
        captured["tools"] = tools
        captured["include_loop_events"] = include_loop_events
        return {
            "status": "success",
            "output": "Raw browser worker output",
            "loop": {"events": [{"event_type": "tool_call_success", "tool_name": "browser_snapshot"}]},
        }

    agent.browser_automation = SimpleNamespace(run=fake_run)
    agent._run_task_refiner = lambda payload: asyncio.sleep(0, result={  # type: ignore[method-assign]
        "status": "ok",
        "raw_user_prompt": payload["user_prompt"],
        "refined_user_prompt": "Refined browser task",
        "target_url": "https://refined.example",
        "return_format": "markdown table",
        "notes": ["Keep evidence concise"],
    })
    agent._run_summarizer = lambda **kwargs: asyncio.sleep(0, result={  # type: ignore[method-assign]
        "status": "ok",
        "content": "Final summarized output",
        "requested_return_format": "markdown table",
        "model": "fake-summary-model",
    })
    agent._build_tools = lambda: ["tool_a"]  # type: ignore[method-assign]

    result = asyncio.run(
        agent.process({"user_prompt": "raw task", "include_loop_events": False})
    )

    assert result["status"] == "success"
    assert result["task_refiner"]["refined_user_prompt"] == "Refined browser task"
    assert result["task_refiner"]["return_format"] == "markdown table"
    assert captured["run_config"].user_prompt == "Refined browser task"
    assert captured["run_config"].start_url == "https://refined.example"
    assert captured["include_loop_events"] is True
    assert result["output"] == "Final summarized output"
    assert result["browser_output"] == "Raw browser worker output"
    assert result["summarizer"]["requested_return_format"] == "markdown table"
    assert "events" not in result["loop"]
