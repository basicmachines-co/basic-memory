"""Tests for the tool-use model seam (spec parsing and both transports)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

import basic_memory_benchmarks.llm.tool_agent as tool_agent
from basic_memory_benchmarks.llm.runners import LLMRunnerError
from basic_memory_benchmarks.llm.tool_agent import (
    AssistantTurn,
    OpenAICompatToolAgent,
    ScriptedToolAgent,
    ToolCall,
    ToolDef,
    ToolReturn,
    UserMessage,
    create_tool_agent_model,
    substitute_placeholders,
)

SEARCH_TOOL = ToolDef(
    name="search_notes",
    description="Search notes",
    input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
)


class TestCreateToolAgentModel:
    def test_openai_compat_spec(self) -> None:
        model = create_tool_agent_model("openai-compat:qwen3@http://localhost:11434/v1")
        assert isinstance(model, OpenAICompatToolAgent)
        assert model.model == "qwen3"
        assert model.base_url == "http://localhost:11434/v1"

    def test_scripted_spec(self, tmp_path: Path) -> None:
        script_path = tmp_path / "script.json"
        script_path.write_text(json.dumps({"tasks": {"hello": [{"text": "hi"}]}}))
        model = create_tool_agent_model(f"scripted:{script_path}")
        assert isinstance(model, ScriptedToolAgent)
        assert model.spec == f"scripted:{script_path}"

    def test_claude_rejected_with_explanation(self) -> None:
        with pytest.raises(ValueError, match="single-shot.*tool_use|tool_use.*single-shot"):
            create_tool_agent_model("claude:claude-haiku-4-5")

    def test_junk_rejected(self) -> None:
        with pytest.raises(ValueError):
            create_tool_agent_model("gemini:flash")
        with pytest.raises(ValueError):
            create_tool_agent_model("openai-compat:no-base-url")

    def test_scripted_file_without_tasks_rejected(self, tmp_path: Path) -> None:
        script_path = tmp_path / "bad.json"
        script_path.write_text(json.dumps({"not_tasks": {}}))
        with pytest.raises(ValueError, match="tasks"):
            create_tool_agent_model(f"scripted:{script_path}")


def _response(payload: dict[str, Any]) -> httpx.Response:
    return httpx.Response(
        status_code=200,
        json=payload,
        request=httpx.Request("POST", "http://localhost/v1/chat/completions"),
    )


def test_extra_headers_ride_every_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """--model-header values reach the endpoint (e.g. anthropic-workspace-id)."""
    captured: dict[str, Any] = {}

    def fake_post(url: str, **kwargs: Any) -> httpx.Response:
        captured["headers"] = kwargs["headers"]
        return _response(
            {
                "choices": [{"message": {"content": "ok", "tool_calls": []}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            }
        )

    monkeypatch.setattr(tool_agent.httpx, "post", fake_post)
    agent = create_tool_agent_model(
        "openai-compat:claude-sonnet-5@https://api.anthropic.com/v1",
        api_key="k",
        extra_headers={"anthropic-workspace-id": "wrkspc_test"},
    )
    assert isinstance(agent, OpenAICompatToolAgent)
    agent.propose([UserMessage(text="hi")], [SEARCH_TOOL])

    assert captured["headers"]["anthropic-workspace-id"] == "wrkspc_test"
    assert captured["headers"]["Authorization"] == "Bearer k"


def test_http_error_includes_response_body(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 4xx body names the actual rejection instead of a bare status code."""

    def fake_post(url: str, **kwargs: Any) -> httpx.Response:
        return httpx.Response(
            status_code=400,
            json={"error": {"message": "anthropic-workspace-id is required"}},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(tool_agent.httpx, "post", fake_post)
    agent = OpenAICompatToolAgent("m", "http://localhost/v1", max_retries=0)
    with pytest.raises(LLMRunnerError, match="anthropic-workspace-id is required"):
        agent.propose([UserMessage(text="hi")], [SEARCH_TOOL])


class TestOpenAICompatToolAgent:
    def _agent(self) -> OpenAICompatToolAgent:
        return OpenAICompatToolAgent("qwen3", "http://localhost:11434/v1", max_retries=0)

    def test_request_carries_tools_and_temperature(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, Any] = {}

        def fake_post(url: str, **kwargs: Any) -> httpx.Response:
            captured["url"] = url
            captured["body"] = kwargs["json"]
            return _response(
                {
                    "choices": [{"message": {"content": "done", "tool_calls": []}}],
                    "usage": {"prompt_tokens": 12, "completion_tokens": 3},
                }
            )

        monkeypatch.setattr(tool_agent.httpx, "post", fake_post)
        turn = self._agent().propose([UserMessage(text="find redis")], [SEARCH_TOOL])

        assert captured["url"].endswith("/chat/completions")
        body = captured["body"]
        assert body["temperature"] == 0
        assert body["tool_choice"] == "auto"
        assert body["tools"] == [
            {
                "type": "function",
                "function": {
                    "name": "search_notes",
                    "description": "Search notes",
                    "parameters": SEARCH_TOOL.input_schema,
                },
            }
        ]
        assert turn.text == "done"
        assert turn.tool_calls == ()
        assert turn.input_tokens == 12
        assert turn.output_tokens == 3

    def test_transcript_maps_assistant_and_tool_roles(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, Any] = {}

        def fake_post(url: str, **kwargs: Any) -> httpx.Response:
            captured["body"] = kwargs["json"]
            return _response(
                {
                    "choices": [{"message": {"content": "ok"}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                }
            )

        monkeypatch.setattr(tool_agent.httpx, "post", fake_post)
        transcript = [
            UserMessage(text="find redis"),
            AssistantTurn(
                text="",
                tool_calls=(
                    ToolCall(call_id="c1", name="search_notes", arguments={"query": "redis"}),
                ),
            ),
            ToolReturn(call_id="c1", name="search_notes", text="2 results", is_error=False),
        ]
        self._agent().propose(transcript, [SEARCH_TOOL])

        messages = captured["body"]["messages"]
        assert messages[0] == {"role": "user", "content": "find redis"}
        assert messages[1]["role"] == "assistant"
        assert messages[1]["tool_calls"][0]["function"]["name"] == "search_notes"
        assert json.loads(messages[1]["tool_calls"][0]["function"]["arguments"]) == {
            "query": "redis"
        }
        assert messages[2] == {"role": "tool", "tool_call_id": "c1", "content": "2 results"}

    def test_parses_tool_calls_from_response(self, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = {
            "choices": [
                {
                    "message": {
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call-9",
                                "function": {
                                    "name": "search_notes",
                                    "arguments": '{"query": "redis"}',
                                },
                            }
                        ],
                    }
                }
            ],
            "usage": {"prompt_tokens": 5, "completion_tokens": 7},
        }
        monkeypatch.setattr(tool_agent.httpx, "post", lambda url, **kw: _response(payload))
        turn = self._agent().propose([UserMessage(text="go")], [SEARCH_TOOL])

        assert turn.text == ""
        assert turn.tool_calls == (
            ToolCall(call_id="call-9", name="search_notes", arguments={"query": "redis"}),
        )

    def test_malformed_tool_arguments_raise(self, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = {
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {"id": "c", "function": {"name": "search_notes", "arguments": "{oops"}}
                        ]
                    }
                }
            ]
        }
        monkeypatch.setattr(tool_agent.httpx, "post", lambda url, **kw: _response(payload))
        with pytest.raises(LLMRunnerError, match="malformed tool arguments"):
            self._agent().propose([UserMessage(text="go")], [SEARCH_TOOL])

    def test_missing_usage_block_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Silent 0-token turns would corrupt the headline metric and disarm the
        # tokens budget, so an omitted usage block is an explicit error.
        payload = {"choices": [{"message": {"content": "done"}}]}
        monkeypatch.setattr(tool_agent.httpx, "post", lambda url, **kw: _response(payload))
        with pytest.raises(LLMRunnerError, match="usage"):
            self._agent().propose([UserMessage(text="go")], [SEARCH_TOOL])

    def test_incomplete_usage_block_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = {
            "choices": [{"message": {"content": "done"}}],
            "usage": {"prompt_tokens": 12},
        }
        monkeypatch.setattr(tool_agent.httpx, "post", lambda url, **kw: _response(payload))
        with pytest.raises(LLMRunnerError, match="token counts"):
            self._agent().propose([UserMessage(text="go")], [SEARCH_TOOL])

    def test_retry_exhaustion_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = {"count": 0}

        def failing_post(url: str, **kwargs: Any) -> httpx.Response:
            calls["count"] += 1
            raise httpx.ConnectError("refused")

        monkeypatch.setattr(tool_agent.httpx, "post", failing_post)
        agent = OpenAICompatToolAgent("m", "http://localhost:1", max_retries=1)
        with pytest.raises(LLMRunnerError, match="after 2 attempts"):
            agent.propose([UserMessage(text="go")], [SEARCH_TOOL])
        assert calls["count"] == 2


class TestScriptedToolAgent:
    def _agent(self) -> ScriptedToolAgent:
        return ScriptedToolAgent(
            script={
                "tasks": {
                    "orphan": [
                        {
                            "tool_calls": [
                                {
                                    "name": "search_notes",
                                    "arguments": {"query": "x", "project": "{project}"},
                                }
                            ]
                        },
                        {"text": "final answer"},
                    ]
                }
            }
        )

    def test_substring_keying_selects_turn_by_transcript_position(self) -> None:
        agent = self._agent()
        first = agent.propose([UserMessage(text="please find the orphan notes")], [])
        assert first.tool_calls[0].name == "search_notes"

        transcript = [
            UserMessage(text="please find the orphan notes"),
            AssistantTurn(text="", tool_calls=first.tool_calls),
            ToolReturn(call_id="c", name="search_notes", text="results", is_error=False),
        ]
        second = agent.propose(transcript, [])
        assert second.text == "final answer"
        assert second.tool_calls == ()

    def test_placeholder_substitution_is_recursive(self) -> None:
        arguments = {"project": "{project}", "nested": {"p": "{project}"}, "n": 3}
        resolved = substitute_placeholders(arguments, {"project": "at-run-task"})
        assert resolved == {"project": "at-run-task", "nested": {"p": "at-run-task"}, "n": 3}

    def test_unmatched_prompt_raises(self) -> None:
        with pytest.raises(LLMRunnerError, match="no entry matching"):
            self._agent().propose([UserMessage(text="something else")], [])

    def test_exhausted_script_raises(self) -> None:
        agent = self._agent()
        transcript = [
            UserMessage(text="orphan"),
            AssistantTurn(text="", tool_calls=()),
            AssistantTurn(text="", tool_calls=()),
        ]
        with pytest.raises(LLMRunnerError, match="exhausted"):
            agent.propose(transcript, [])
