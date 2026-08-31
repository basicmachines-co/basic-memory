"""The minimal agent loop: model proposes tool calls, the harness dispatches.

Budgets (turns, total tokens, wall clock) are enforced before each model call,
and the wall clock is re-checked between tool dispatches — one assistant turn
can carry many calls, each worth up to the tool timeout. Per-turn accounting
is recorded for both model turns and tool dispatches. The loop never swallows
model or dispatch errors: a mid-loop failure raises ``AgentLoopError`` wrapping
the cause plus the partial accounting, so the driver records the task as an
explicit error (never a silent zero score) without discarding tokens already
spent on real model calls.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from basic_memory_benchmarks.agent_tasks.models import AgentBudget, StopReason, TurnRecord
from basic_memory_benchmarks.llm.tool_agent import (
    AssistantTurn,
    ToolAgentModel,
    ToolDef,
    ToolReturn,
    TranscriptItem,
    UserMessage,
)

# Fixed for both surfaces — part of the fairness contract (disclosed in docs).
TOOL_RESULT_MAX_CHARS = 8_000
TRUNCATION_SUFFIX = "\n…[tool result truncated]"


@dataclass(frozen=True)
class ToolOutcome:
    """What a dispatched tool call produced: text to feed back, error flag."""

    text: str
    is_error: bool


class ToolDispatch(Protocol):
    def __call__(self, name: str, arguments: dict[str, Any]) -> ToolOutcome: ...


@dataclass
class AgentLoopResult:
    final_answer: str | None
    stopped_reason: StopReason
    turns: int
    tool_call_count: int
    total_input_tokens: int
    total_output_tokens: int
    wall_seconds: float
    turn_records: list[TurnRecord] = field(default_factory=list)


class AgentLoopError(RuntimeError):
    """A model call or tool dispatch failed mid-loop.

    Carries the partial ``AgentLoopResult`` (``stopped_reason="error"``) so the
    driver can keep the tokens and per-turn records already spent on the
    errored task — real cost is never discarded just because the task died.
    """

    def __init__(self, cause: Exception, partial: AgentLoopResult) -> None:
        super().__init__(str(cause))
        self.cause = cause
        self.partial = partial


def _truncate_result(text: str) -> str:
    if len(text) <= TOOL_RESULT_MAX_CHARS:
        return text
    return text[:TOOL_RESULT_MAX_CHARS] + TRUNCATION_SUFFIX


def run_agent_loop(
    *,
    model: ToolAgentModel,
    dispatch: ToolDispatch,
    tools: Sequence[ToolDef],
    prompt: str,
    budget: AgentBudget,
    clock: Callable[[], float] = time.monotonic,
) -> AgentLoopResult:
    allowed_names = {tool.name for tool in tools}
    transcript: list[TranscriptItem] = [UserMessage(text=prompt)]
    turn_records: list[TurnRecord] = []
    model_turns = 0
    tool_call_count = 0
    total_input_tokens = 0
    total_output_tokens = 0
    started = clock()

    def stop(reason: StopReason, final_answer: str | None) -> AgentLoopResult:
        return AgentLoopResult(
            final_answer=final_answer,
            stopped_reason=reason,
            turns=model_turns,
            tool_call_count=tool_call_count,
            total_input_tokens=total_input_tokens,
            total_output_tokens=total_output_tokens,
            wall_seconds=clock() - started,
            turn_records=turn_records,
        )

    budget_stop: Literal["turns", "tokens", "wall_clock"] | None = None
    while True:
        # Budget gate BEFORE each model call: a breach returns without a final
        # answer, so budget-stopped tasks fail their answer predicates honestly.
        if clock() - started >= budget.max_task_seconds:
            budget_stop = "wall_clock"
        elif model_turns >= budget.max_turns:
            budget_stop = "turns"
        elif total_input_tokens + total_output_tokens >= budget.max_total_tokens:
            budget_stop = "tokens"
        if budget_stop is not None:
            return stop(budget_stop, None)

        try:
            turn = model.propose(transcript, tools)
        except Exception as exc:
            raise AgentLoopError(exc, stop("error", None)) from exc
        model_turns += 1
        total_input_tokens += turn.input_tokens
        total_output_tokens += turn.output_tokens
        finalized = not turn.tool_calls
        turn_records.append(
            TurnRecord(
                turn_index=len(turn_records),
                kind="model",
                input_tokens=turn.input_tokens,
                output_tokens=turn.output_tokens,
                latency_ms=round(turn.latency_ms, 2),
                tool_call_count=len(turn.tool_calls),
                finalized=finalized,
            )
        )
        transcript.append(AssistantTurn(text=turn.text, tool_calls=turn.tool_calls))
        if finalized:
            return stop("final", turn.text)

        for call in turn.tool_calls:
            # Trigger: wall clock exhausted between tool dispatches.
            # Why: gating only before model calls would let one assistant turn
            # carrying many calls overrun the budget by n_calls x tool_timeout.
            # Outcome: stop with 'wall_clock' and no final answer, exactly like
            # the pre-model-call gate.
            if clock() - started >= budget.max_task_seconds:
                return stop("wall_clock", None)
            tool_call_count += 1
            if call.name not in allowed_names:
                # Hallucinated or off-surface tool: never reaches BM; the error
                # is fed back so the model can self-correct within budget.
                outcome = ToolOutcome(
                    text=f"tool '{call.name}' is not available on this surface",
                    is_error=True,
                )
                latency_ms = 0.0
            else:
                dispatch_started = time.perf_counter()
                try:
                    outcome = dispatch(call.name, call.arguments)
                except Exception as exc:
                    # Record the call that was in flight when the dispatch died
                    # so per-turn.jsonl shows where the failure happened.
                    turn_records.append(
                        TurnRecord(
                            turn_index=len(turn_records),
                            kind="tool",
                            latency_ms=round((time.perf_counter() - dispatch_started) * 1000.0, 2),
                            tool_name=call.name,
                            arguments_chars=len(str(call.arguments)),
                            is_error=True,
                        )
                    )
                    raise AgentLoopError(exc, stop("error", None)) from exc
                latency_ms = (time.perf_counter() - dispatch_started) * 1000.0
            fed_back = _truncate_result(outcome.text)
            turn_records.append(
                TurnRecord(
                    turn_index=len(turn_records),
                    kind="tool",
                    latency_ms=round(latency_ms, 2),
                    tool_name=call.name,
                    arguments_chars=len(str(call.arguments)),
                    result_chars=len(fed_back),
                    is_error=outcome.is_error,
                )
            )
            transcript.append(
                ToolReturn(
                    call_id=call.call_id,
                    name=call.name,
                    text=fed_back,
                    is_error=outcome.is_error,
                )
            )
