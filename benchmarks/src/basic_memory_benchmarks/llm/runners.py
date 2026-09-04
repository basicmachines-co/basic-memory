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
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any
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


def _claude_result_event(payload: object) -> dict[str, Any]:
    """The `result` record of a `claude -p --output-format json` run.

    Claude Code 2.1 prints a JSON array of session events (system, assistant,
    rate_limit_event, result); earlier releases printed the result object
    alone. Anything else is a runner error rather than a crash, so the caller
    records the judge failure against the task instead of losing the run.
    """
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, list):
        results = [
            item for item in payload if isinstance(item, dict) and item.get("type") == "result"
        ]
        if len(results) == 1:
            return results[0]
        raise LLMRunnerError(
            f"claude -p emitted {len(results)} result events in {len(payload)} records; expected 1"
        )
    raise LLMRunnerError(f"claude -p emitted {type(payload).__name__}, expected an object or array")


def empty_mcp_config_path() -> Path:
    """A persistent empty MCP config for `claude -p --strict-mcp-config`."""
    path = Path(tempfile.gettempdir()) / "bm-bench-empty-mcp.json"
    if not path.exists():
        path.write_text('{"mcpServers": {}}\n', encoding="utf-8")
    return path


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
            # A plain `claude -p` boots the operator's whole Claude Code
            # configuration: every configured MCP server (npx-launched browser
            # tooling among them) starts for one answer, per call. Four hundred
            # judge calls did that to a laptop. An empty strict MCP config keeps
            # the OAuth login and drops the servers; --bare would drop them too
            # but forces API-key auth, which is not the account these runs bill.
            "--strict-mcp-config",
            "--mcp-config",
            str(empty_mcp_config_path()),
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
                payload = _claude_result_event(json.loads(completed.stdout))
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
        temperature: float | None = 0.0,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.spec = f"openai-compat:{model}@{self.base_url}"
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        # None omits the parameter: Claude 5 models reject any temperature
        # ("`temperature` is deprecated for this model"); local servers default
        # to nonzero sampling unless pinned, so 0 stays the default.
        self._temperature = temperature

    def complete(self, prompt: str) -> LLMResult:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
        }
        if self._temperature is not None:
            body["temperature"] = self._temperature
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
            except httpx.HTTPStatusError as exc:
                # The status alone ("400 Bad Request") cannot tell a spent
                # credit balance from a rejected parameter; the body can. Six
                # BEAM conversations were excluded as bare 400s before this.
                error_body = exc.response.text.strip().replace("\n", " ")[:300]
                last_error = LLMRunnerError(
                    f"HTTP {exc.response.status_code} from {exc.request.url}: {error_body}"
                )
            except (httpx.HTTPError, KeyError, IndexError, json.JSONDecodeError) as exc:
                last_error = exc
        raise LLMRunnerError(
            f"openai-compat call to {self.base_url} failed after "
            f"{self._max_retries + 1} attempts: {last_error}"
        )


def create_runner(
    spec: str, *, api_key: str | None = None, temperature: float | None = 0.0
) -> LLMRunner:
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
        resolved_api_key = api_key if api_key is not None else os.getenv("OPENAI_API_KEY")
        return OpenAICompatRunner(
            model=model, base_url=base_url, api_key=resolved_api_key, temperature=temperature
        )
    raise ValueError(
        f"Unknown runner spec '{spec}'. Expected 'claude:<model>' or "
        f"'openai-compat:<model>@<base_url>'."
    )
