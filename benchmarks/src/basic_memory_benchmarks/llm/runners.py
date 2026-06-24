"""LLM runner abstraction for answer generation and judging.

Two transports are supported:

- ``claude``: shells out to the Claude Code CLI in print mode (``claude -p``).
  Calls bill against the operator's Claude subscription plan, not an API key.
- ``openai-compat``: POSTs to any OpenAI-compatible ``/chat/completions``
  endpoint (Ollama, LM Studio, vLLM, or the real OpenAI API).

Runner specs are strings so they can flow through CLI flags and run manifests:

- ``claude:claude-haiku-4-5``
- ``openai-compat:llama3.1@http://localhost:11434/v1``
"""

from __future__ import annotations

import json
import subprocess
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass

import httpx


class LLMRunnerError(RuntimeError):
    """Raised when an LLM call fails after retries."""


@dataclass
class LLMResult:
    text: str
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: float


class LLMRunner(ABC):
    """A minimal single-prompt completion interface."""

    spec: str

    @abstractmethod
    def complete(self, prompt: str) -> LLMResult:
        """Run one prompt to completion and return the text plus usage."""

    def describe(self) -> dict[str, str]:
        return {"spec": self.spec}


class ClaudeCLIRunner(LLMRunner):
    """Run prompts through ``claude -p`` (plan-billed, no API key required).

    Each call is a fresh CLI session, so it pays the CLI's system-prompt cache
    overhead per call. Token counts reported here are the conversation tokens
    only (``usage.input_tokens`` + ``output_tokens``), which is what matters
    for cross-provider context-size comparisons.
    """

    def __init__(
        self,
        model: str,
        *,
        claude_bin: str = "claude",
        timeout_seconds: float = 300.0,
        max_retries: int = 2,
    ) -> None:
        self.model = model
        self.spec = f"claude:{model}"
        self._claude_bin = claude_bin
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries

    def complete(self, prompt: str) -> LLMResult:
        command = [
            self._claude_bin,
            "-p",
            "--model",
            self.model,
            "--output-format",
            "json",
            "--max-turns",
            "1",
        ]
        last_error: Exception | None = None
        for _ in range(self._max_retries + 1):
            started = time.perf_counter()
            try:
                completed = subprocess.run(
                    command,
                    input=prompt,
                    capture_output=True,
                    text=True,
                    timeout=self._timeout_seconds,
                    check=False,
                )
                payload = json.loads(completed.stdout)
                if completed.returncode != 0 or payload.get("is_error"):
                    raise LLMRunnerError(
                        f"claude -p failed (rc={completed.returncode}): "
                        f"{payload.get('result') or completed.stderr[:500]}"
                    )
                usage = payload.get("usage") or {}
                return LLMResult(
                    text=str(payload.get("result") or "").strip(),
                    model=self.model,
                    input_tokens=int(usage.get("input_tokens") or 0),
                    output_tokens=int(usage.get("output_tokens") or 0),
                    latency_ms=(time.perf_counter() - started) * 1000.0,
                )
            except (subprocess.TimeoutExpired, json.JSONDecodeError, LLMRunnerError) as exc:
                last_error = exc
        raise LLMRunnerError(
            f"claude -p failed after {self._max_retries + 1} attempts: {last_error}"
        )


class OpenAICompatRunner(LLMRunner):
    """Run prompts against an OpenAI-compatible chat-completions endpoint."""

    def __init__(
        self,
        model: str,
        base_url: str,
        *,
        api_key: str | None = None,
        timeout_seconds: float = 300.0,
        max_retries: int = 2,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.spec = f"openai-compat:{model}@{self.base_url}"
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries

    def complete(self, prompt: str) -> LLMResult:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        body = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
        }
        last_error: Exception | None = None
        for _ in range(self._max_retries + 1):
            started = time.perf_counter()
            try:
                response = httpx.post(
                    f"{self.base_url}/chat/completions",
                    json=body,
                    headers=headers,
                    timeout=self._timeout_seconds,
                )
                response.raise_for_status()
                payload = response.json()
                usage = payload.get("usage") or {}
                return LLMResult(
                    text=str(payload["choices"][0]["message"]["content"] or "").strip(),
                    model=self.model,
                    input_tokens=int(usage.get("prompt_tokens") or 0),
                    output_tokens=int(usage.get("completion_tokens") or 0),
                    latency_ms=(time.perf_counter() - started) * 1000.0,
                )
            except (httpx.HTTPError, KeyError, IndexError, json.JSONDecodeError) as exc:
                last_error = exc
        raise LLMRunnerError(
            f"openai-compat call to {self.base_url} failed after "
            f"{self._max_retries + 1} attempts: {last_error}"
        )


def create_runner(spec: str, *, api_key: str | None = None) -> LLMRunner:
    """Build a runner from a spec string.

    Formats: ``claude:<model>`` or ``openai-compat:<model>@<base_url>``.
    """
    transport, _, remainder = spec.partition(":")
    if transport == "claude" and remainder:
        return ClaudeCLIRunner(model=remainder)
    if transport == "openai-compat" and remainder:
        model, separator, base_url = remainder.partition("@")
        if not separator or not model or not base_url:
            raise ValueError(
                f"openai-compat spec must be 'openai-compat:<model>@<base_url>', got: {spec}"
            )
        return OpenAICompatRunner(model=model, base_url=base_url, api_key=api_key)
    raise ValueError(
        f"Unknown runner spec '{spec}'. Expected 'claude:<model>' or "
        f"'openai-compat:<model>@<base_url>'."
    )
