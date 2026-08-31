"""Tool-use model seam for the agent-task eval (basic-memory#1401).

The single-prompt ``LLMRunner`` seam in ``runners.py`` cannot pause at a tool
call, so the agent under test speaks a richer contract: the model receives a
neutral transcript plus tool definitions and returns either tool calls or a
final answer. Two transports:

- ``openai-compat``: any ``/chat/completions`` endpoint that implements the
  ``tools`` parameter (Ollama, vLLM, LM Studio, OpenAI — and Anthropic models
  behind a LiteLLM proxy).
- ``scripted``: a canned JSON script for offline tests and the LLM-free smoke.

``claude:<model>`` is deliberately unsupported here: ``claude -p`` runs its own
agent loop with ``--max-turns 1`` semantics and never hands a ``tool_use``
block back to the harness.
"""

from __future__ import annotations

import json
import os
import time
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from basic_memory_benchmarks.llm.runners import LLMRunnerError

# --- Neutral transcript and tool types (transport-agnostic, loop-owned) ---


@dataclass(frozen=True)
class ToolDef:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    call_id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ToolReturn:
    call_id: str
    name: str
    text: str
    is_error: bool


@dataclass(frozen=True)
class UserMessage:
    text: str


@dataclass(frozen=True)
class AssistantTurn:
    text: str
    tool_calls: tuple[ToolCall, ...]


type TranscriptItem = UserMessage | AssistantTurn | ToolReturn


@dataclass(frozen=True)
class AgentTurn:
    """One model response: text and/or tool calls, plus usage accounting."""

    text: str
    tool_calls: tuple[ToolCall, ...]
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: float


class ToolAgentModel(ABC):
    """The model side of the agent loop: transcript in, one turn out."""

    spec: str

    @abstractmethod
    def propose(self, transcript: Sequence[TranscriptItem], tools: Sequence[ToolDef]) -> AgentTurn:
        """Return the model's next turn given the transcript so far."""

    def describe(self) -> dict[str, str]:
        return {"spec": self.spec}


# --- OpenAI-compatible transport ---


def _transcript_to_messages(transcript: Sequence[TranscriptItem]) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for item in transcript:
        match item:
            case UserMessage(text=text):
                messages.append({"role": "user", "content": text})
            case AssistantTurn(text=text, tool_calls=tool_calls):
                message: dict[str, Any] = {"role": "assistant", "content": text or None}
                if tool_calls:
                    message["tool_calls"] = [
                        {
                            "id": call.call_id,
                            "type": "function",
                            "function": {
                                "name": call.name,
                                "arguments": json.dumps(call.arguments),
                            },
                        }
                        for call in tool_calls
                    ]
                messages.append(message)
            case ToolReturn(call_id=call_id, text=text):
                messages.append({"role": "tool", "tool_call_id": call_id, "content": text})
    return messages


def _tools_to_functions(tools: Sequence[ToolDef]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.input_schema,
            },
        }
        for tool in tools
    ]


class OpenAICompatToolAgent(ToolAgentModel):
    """Tool-use agent over an OpenAI-compatible chat-completions endpoint."""

    def __init__(
        self,
        model: str,
        base_url: str,
        *,
        api_key: str | None = None,
        extra_headers: dict[str, str] | None = None,
        timeout_seconds: float = 300.0,
        max_retries: int = 2,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.spec = f"openai-compat:{model}@{self.base_url}"
        self._api_key = api_key
        # Some endpoints require headers beyond auth — e.g. Anthropic's
        # OpenAI-compat layer demands anthropic-workspace-id for
        # identity-linked API keys. Values may be sensitive, so they are
        # never recorded in run artifacts.
        self._extra_headers = dict(extra_headers or {})
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        headers.update(self._extra_headers)
        last_error: Exception | None = None
        error_body = ""
        for _ in range(self._max_retries + 1):
            try:
                response = httpx.post(
                    f"{self.base_url}/chat/completions",
                    json=body,
                    headers=headers,
                    timeout=self._timeout_seconds,
                )
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise KeyError("response body is not a JSON object")
                return payload
            except (httpx.HTTPError, KeyError, json.JSONDecodeError) as exc:
                last_error = exc
                # A 4xx/5xx body names the actual rejection (bad model id,
                # missing header, quota) — without it the operator sees only
                # a bare status code.
                if isinstance(exc, httpx.HTTPStatusError):
                    error_body = exc.response.text[:300]
        detail = f": {error_body}" if error_body else ""
        raise LLMRunnerError(
            f"openai-compat call to {self.base_url} failed after "
            f"{self._max_retries + 1} attempts: {last_error}{detail}"
        )

    def propose(self, transcript: Sequence[TranscriptItem], tools: Sequence[ToolDef]) -> AgentTurn:
        body = {
            "model": self.model,
            "messages": _transcript_to_messages(transcript),
            "tools": _tools_to_functions(tools),
            "tool_choice": "auto",
            "temperature": 0,
        }
        started = time.perf_counter()
        payload = self._post(body)
        latency_ms = (time.perf_counter() - started) * 1000.0

        try:
            message = payload["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMRunnerError(f"openai-compat response has no message: {exc}") from exc

        tool_calls: list[ToolCall] = []
        for index, raw_call in enumerate(message.get("tool_calls") or []):
            function = raw_call.get("function") or {}
            raw_arguments = function.get("arguments") or "{}"
            try:
                arguments = json.loads(raw_arguments)
            except json.JSONDecodeError as exc:
                # Malformed arguments are an explicit task error, never a
                # silent skip: the loop propagates this to the driver.
                raise LLMRunnerError(
                    f"model returned malformed tool arguments for "
                    f"'{function.get('name')}': {raw_arguments[:200]}"
                ) from exc
            if not isinstance(arguments, dict):
                raise LLMRunnerError(
                    f"model returned non-object tool arguments for "
                    f"'{function.get('name')}': {raw_arguments[:200]}"
                )
            tool_calls.append(
                ToolCall(
                    call_id=str(raw_call.get("id") or f"call-{index}"),
                    name=str(function.get("name") or ""),
                    arguments=arguments,
                )
            )

        # Token accounting is the headline metric AND the tokens budget input:
        # an endpoint that omits usage would silently report 0-token turns and
        # never trip max_total_tokens, so a missing block is an explicit error.
        usage = payload.get("usage")
        if not isinstance(usage, dict):
            raise LLMRunnerError(
                f"openai-compat response from {self.base_url} has no 'usage' block; "
                "token accounting would be silently wrong"
            )
        try:
            input_tokens = int(usage["prompt_tokens"])
            output_tokens = int(usage["completion_tokens"])
        except (KeyError, TypeError, ValueError) as exc:
            raise LLMRunnerError(
                f"openai-compat usage block from {self.base_url} has missing or "
                f"malformed token counts: {usage!r}"
            ) from exc
        return AgentTurn(
            text=str(message.get("content") or "").strip(),
            tool_calls=tuple(tool_calls),
            model=self.model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
        )


# --- Scripted transport (offline tests and the LLM-free smoke) ---

SCRIPTED_FAKE_INPUT_TOKENS = 10
SCRIPTED_FAKE_OUTPUT_TOKENS = 5


def substitute_placeholders(value: Any, substitutions: Mapping[str, str]) -> Any:
    """Replace ``{name}`` placeholders in string values, recursively.

    Scripted tool calls cannot know per-run values like the project name, so
    the driver substitutes them just before dispatch.
    """
    if isinstance(value, str):
        for name, replacement in substitutions.items():
            value = value.replace("{" + name + "}", replacement)
        return value
    if isinstance(value, dict):
        return {key: substitute_placeholders(item, substitutions) for key, item in value.items()}
    if isinstance(value, list):
        return [substitute_placeholders(item, substitutions) for item in value]
    return value


@dataclass(frozen=True)
class ScriptedToolAgent(ToolAgentModel):
    """Replays canned turns keyed by substring match on the first user message.

    Script shape: ``{"tasks": {"<substring>": [turn, ...]}}`` where each turn
    is ``{"tool_calls": [{"name": ..., "arguments": {...}}]}`` or
    ``{"text": "..."}``. The agent is stateless: the number of AssistantTurns
    already in the transcript selects the next scripted turn, so one instance
    serves any number of tasks. Test/smoke-only — the script may "know" the
    answer; it proves harness plumbing, not model quality.
    """

    script: dict[str, Any]
    spec: str = field(default="scripted:<inline>")

    @classmethod
    def from_path(cls, path: Path) -> ScriptedToolAgent:
        script = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(script, dict) or not isinstance(script.get("tasks"), dict):
            raise ValueError(f"scripted model file must contain a 'tasks' object: {path}")
        return cls(script=script, spec=f"scripted:{path}")

    def _turns_for(self, first_user_text: str) -> tuple[str, list[dict[str, Any]]]:
        tasks = self.script.get("tasks")
        if not isinstance(tasks, dict):
            raise LLMRunnerError(f"scripted model has no 'tasks' object ({self.spec})")
        for needle, turns in tasks.items():
            if needle in first_user_text:
                return needle, list(turns)
        raise LLMRunnerError(
            f"scripted model has no entry matching prompt: {first_user_text[:120]}"
        )

    def propose(self, transcript: Sequence[TranscriptItem], tools: Sequence[ToolDef]) -> AgentTurn:
        first_user = next((item for item in transcript if isinstance(item, UserMessage)), None)
        if first_user is None:
            raise LLMRunnerError("scripted model called with no user message in transcript")
        needle, turns = self._turns_for(first_user.text)
        emitted = sum(1 for item in transcript if isinstance(item, AssistantTurn))
        if emitted >= len(turns):
            raise LLMRunnerError(
                f"scripted model exhausted after {len(turns)} turns for key '{needle}'"
            )
        turn_spec = turns[emitted]
        tool_calls = tuple(
            ToolCall(
                call_id=f"scripted-{emitted}-{index}",
                name=str(raw["name"]),
                arguments=dict(raw.get("arguments") or {}),
            )
            for index, raw in enumerate(turn_spec.get("tool_calls") or [])
        )
        return AgentTurn(
            text=str(turn_spec.get("text") or ""),
            tool_calls=tool_calls,
            model="scripted",
            input_tokens=SCRIPTED_FAKE_INPUT_TOKENS,
            output_tokens=SCRIPTED_FAKE_OUTPUT_TOKENS,
            latency_ms=0.0,
        )


# --- Spec parsing ---


def create_tool_agent_model(
    spec: str,
    *,
    api_key: str | None = None,
    extra_headers: dict[str, str] | None = None,
) -> ToolAgentModel:
    """Build a tool-use agent from a spec string.

    Formats: ``openai-compat:<model>@<base_url>`` or ``scripted:<path.json>``.
    ``extra_headers`` are sent on every openai-compat request (ignored for
    scripted) and never recorded in run artifacts.
    """
    transport, _, remainder = spec.partition(":")
    if transport == "claude":
        raise ValueError(
            "claude -p is single-shot and cannot pause at tool_use; use openai-compat "
            "(e.g. an Anthropic model behind a LiteLLM proxy) or scripted:<path.json>"
        )
    if transport == "openai-compat" and remainder:
        model, separator, base_url = remainder.partition("@")
        if not separator or not model or not base_url:
            raise ValueError(
                f"openai-compat spec must be 'openai-compat:<model>@<base_url>', got: {spec}"
            )
        resolved_api_key = api_key if api_key is not None else os.getenv("OPENAI_API_KEY")
        return OpenAICompatToolAgent(
            model=model,
            base_url=base_url,
            api_key=resolved_api_key,
            extra_headers=extra_headers,
        )
    if transport == "scripted" and remainder:
        return ScriptedToolAgent.from_path(Path(remainder))
    raise ValueError(
        f"Unknown tool-agent spec '{spec}'. Expected 'openai-compat:<model>@<base_url>' "
        f"or 'scripted:<path.json>'."
    )
