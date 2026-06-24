"""Tests for LLM runner spec parsing and transports."""

from __future__ import annotations

import json
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
    def _completed(self, payload: dict, returncode: int = 0) -> subprocess.CompletedProcess:
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
