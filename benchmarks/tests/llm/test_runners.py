"""Tests for LLM runner spec parsing and transports."""

from __future__ import annotations

import json

import httpx
import subprocess

import pytest

from basic_memory_benchmarks.llm.runners import (
    ClaudeCLIRunner,
    LLMRunnerError,
    OpenAICompatRunner,
    create_runner,
)


class TestCreateRunner:
    def test_claude_spec(self):
        runner = create_runner("claude:claude-haiku-4-5")
        assert isinstance(runner, ClaudeCLIRunner)
        assert runner.model == "claude-haiku-4-5"
        assert runner.spec == "claude:claude-haiku-4-5"

    def test_openai_compat_spec(self):
        runner = create_runner("openai-compat:llama3.1@http://localhost:11434/v1")
        assert isinstance(runner, OpenAICompatRunner)
        assert runner.model == "llama3.1"
        assert runner.base_url == "http://localhost:11434/v1"

    def test_openai_compat_uses_openai_api_key(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")

        runner = create_runner("openai-compat:gpt-4o-mini@https://api.openai.com/v1")

        assert isinstance(runner, OpenAICompatRunner)
        assert runner._api_key == "test-key"

    def test_openai_compat_spec_requires_base_url(self):
        with pytest.raises(ValueError):
            create_runner("openai-compat:llama3.1")

    def test_unknown_transport_rejected(self):
        with pytest.raises(ValueError):
            create_runner("gemini:flash")

    def test_empty_model_rejected(self):
        with pytest.raises(ValueError):
            create_runner("claude:")


class TestClaudeCLIRunner:
    def _completed(self, payload: object, returncode: int = 0) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(
            args=[], returncode=returncode, stdout=json.dumps(payload), stderr=""
        )

    def test_parses_result_and_usage(self, monkeypatch):
        payload = {
            "is_error": False,
            "result": "Paris",
            "usage": {"input_tokens": 120, "output_tokens": 8},
        }
        captured: dict = {}

        def fake_run(command, **kwargs):
            captured["command"] = command
            captured["input"] = kwargs.get("input")
            return self._completed(payload)

        monkeypatch.setattr(subprocess, "run", fake_run)
        runner = ClaudeCLIRunner(model="claude-haiku-4-5")
        result = runner.complete("What is the capital of France?")

        assert result.text == "Paris"
        assert result.input_tokens == 120
        assert result.output_tokens == 8
        assert captured["input"] == "What is the capital of France?"
        assert "--max-turns" in captured["command"]
        assert "claude-haiku-4-5" in captured["command"]

    def test_parses_the_event_array_claude_code_2_1_prints(self, monkeypatch):
        """`--output-format json` became an array of session events; the result
        record is the one that carries the answer and usage. The earlier object
        shape (above) must keep working alongside."""
        events = [
            {"type": "system", "cwd": "/tmp"},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "Paris"}]}},
            {"type": "rate_limit_event", "rate_limit_info": {}},
            {
                "type": "result",
                "is_error": False,
                "result": "Paris",
                "usage": {"input_tokens": 9, "output_tokens": 23},
            },
        ]
        monkeypatch.setattr(subprocess, "run", lambda command, **kwargs: self._completed(events))
        result = ClaudeCLIRunner(model="claude-sonnet-4-6").complete("capital?")

        assert result.text == "Paris"
        assert result.input_tokens == 9
        assert result.output_tokens == 23

    def test_unexpected_output_shape_is_a_runner_error_not_a_crash(self, monkeypatch):
        """A judge that cannot be parsed must surface as LLMRunnerError, which the
        harness records against the task; an AttributeError escaped and aborted a
        whole xAFS run."""
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda command, **kwargs: self._completed([{"type": "system"}, {"type": "assistant"}]),
        )
        with pytest.raises(LLMRunnerError, match="0 result events"):
            ClaudeCLIRunner(model="claude-sonnet-4-6", max_retries=0).complete("hello")

        monkeypatch.setattr(subprocess, "run", lambda command, **kwargs: self._completed(42))
        with pytest.raises(LLMRunnerError, match="expected an object or array"):
            ClaudeCLIRunner(model="claude-sonnet-4-6", max_retries=0).complete("hello")

    def test_error_payload_raises_after_retries(self, monkeypatch):
        attempts = {"count": 0}

        def fake_run(command, **kwargs):
            attempts["count"] += 1
            return self._completed({"is_error": True, "result": "overloaded"}, returncode=1)

        monkeypatch.setattr(subprocess, "run", fake_run)
        runner = ClaudeCLIRunner(model="claude-haiku-4-5", max_retries=1)
        with pytest.raises(LLMRunnerError):
            runner.complete("hello")
        assert attempts["count"] == 2

    def test_retry_then_success(self, monkeypatch):
        attempts = {"count": 0}
        good = {"is_error": False, "result": "ok", "usage": {}}

        def fake_run(command, **kwargs):
            attempts["count"] += 1
            if attempts["count"] == 1:
                return subprocess.CompletedProcess(
                    args=[], returncode=0, stdout="not json", stderr=""
                )
            return self._completed(good)

        monkeypatch.setattr(subprocess, "run", fake_run)
        runner = ClaudeCLIRunner(model="claude-haiku-4-5", max_retries=1)
        assert runner.complete("hello").text == "ok"
        assert attempts["count"] == 2


def test_openai_compat_error_includes_the_response_body(monkeypatch):
    """A 400 with a JSON error body must surface the body: it is what says
    "credit balance too low" versus "temperature is deprecated"."""
    request = httpx.Request("POST", "https://api.example.test/v1/chat/completions")

    def fake_post(url, **kwargs):
        return httpx.Response(
            400,
            json={"error": {"message": "Your credit balance is too low to access the API."}},
            request=request,
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    runner = OpenAICompatRunner(model="m", base_url="https://api.example.test/v1", max_retries=0)
    with pytest.raises(LLMRunnerError) as excinfo:
        runner.complete("hello")
    assert "HTTP 400" in str(excinfo.value)
    assert "credit balance is too low" in str(excinfo.value)
