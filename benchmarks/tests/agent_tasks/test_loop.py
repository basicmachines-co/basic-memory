"""Tests for the agent loop: budgets, dispatch, truncation, accounting."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest

from basic_memory_benchmarks.agent_tasks.loop import (
    TOOL_RESULT_MAX_CHARS,
    AgentLoopError,
    ToolOutcome,
    run_agent_loop,
)
from basic_memory_benchmarks.agent_tasks.models import AgentBudget
from basic_memory_benchmarks.llm.runners import LLMRunnerError
from basic_memory_benchmarks.llm.tool_agent import (
    AgentTurn,
    AssistantTurn,
    ScriptedToolAgent,
    ToolAgentModel,
    ToolCall,
    ToolDef,
    ToolReturn,
    TranscriptItem,
)

SEARCH_TOOL = ToolDef(name="search_notes", description="", input_schema={"type": "object"})
TOOLS = [SEARCH_TOOL]


class RecordingDispatch:
    def __init__(self, outcome: ToolOutcome | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.outcome = outcome or ToolOutcome(text="ok", is_error=False)

    def __call__(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        self.calls.append((name, arguments))
        return self.outcome


def _scripted(turns: list[dict[str, Any]], needle: str = "Task") -> ScriptedToolAgent:
    return ScriptedToolAgent(script={"tasks": {needle: turns}})


def _run(model: ToolAgentModel, dispatch: RecordingDispatch, **budget: Any):
    return run_agent_loop(
        model=model,
        dispatch=dispatch,
        tools=TOOLS,
        prompt="Task: do the thing",
        budget=AgentBudget(**budget),
    )


def test_final_answer_path() -> None:
    model = _scripted([{"text": "the answer"}])
    dispatch = RecordingDispatch()
    result = _run(model, dispatch)

    assert result.final_answer == "the answer"
    assert result.stopped_reason == "final"
    assert result.turns == 1
    assert result.tool_call_count == 0
    assert dispatch.calls == []
    assert len(result.turn_records) == 1
    assert result.turn_records[0].kind == "model"
    assert result.turn_records[0].finalized is True


def test_multi_turn_feeds_tool_results_back() -> None:
    class TranscriptSpy(ToolAgentModel):
        spec = "spy:test"

        def __init__(self) -> None:
            self.seen: list[list[TranscriptItem]] = []

        def propose(
            self, transcript: Sequence[TranscriptItem], tools: Sequence[ToolDef]
        ) -> AgentTurn:
            self.seen.append(list(transcript))
            if len(self.seen) == 1:
                return AgentTurn(
                    text="",
                    tool_calls=(ToolCall(call_id="c1", name="search_notes", arguments={"q": "x"}),),
                    model="spy",
                    input_tokens=10,
                    output_tokens=2,
                    latency_ms=1.0,
                )
            return AgentTurn(
                text="done",
                tool_calls=(),
                model="spy",
                input_tokens=20,
                output_tokens=4,
                latency_ms=1.0,
            )

    model = TranscriptSpy()
    dispatch = RecordingDispatch(ToolOutcome(text="search result text", is_error=False))
    result = _run(model, dispatch)

    assert result.final_answer == "done"
    assert dispatch.calls == [("search_notes", {"q": "x"})]
    # The second propose sees the assistant turn plus the tool result.
    second_transcript = model.seen[1]
    assert isinstance(second_transcript[1], AssistantTurn)
    tool_return = second_transcript[2]
    assert isinstance(tool_return, ToolReturn)
    assert tool_return.text == "search result text"
    assert result.total_input_tokens == 30
    assert result.total_output_tokens == 6


def test_turn_budget_stop() -> None:
    model = _scripted([{"tool_calls": [{"name": "search_notes", "arguments": {}}]}] * 5)
    dispatch = RecordingDispatch()
    result = _run(model, dispatch, max_turns=2)

    assert result.stopped_reason == "turns"
    assert result.final_answer is None
    assert result.turns == 2


def test_token_budget_stop() -> None:
    # Scripted turns cost 15 fake tokens each; a 25-token budget allows two
    # model calls (gate checks BEFORE the call: 0 < 25, 15 < 25, 30 >= 25).
    model = _scripted([{"tool_calls": [{"name": "search_notes", "arguments": {}}]}] * 5)
    result = _run(model, RecordingDispatch(), max_total_tokens=25)

    assert result.stopped_reason == "tokens"
    assert result.turns == 2


def test_wall_clock_budget_stop_uses_injected_clock() -> None:
    ticks = iter([0.0, 0.0, 100.0, 100.0, 100.0])
    model = _scripted([{"tool_calls": [{"name": "search_notes", "arguments": {}}]}] * 5)
    result = run_agent_loop(
        model=model,
        dispatch=RecordingDispatch(),
        tools=TOOLS,
        prompt="Task: x",
        budget=AgentBudget(max_task_seconds=50.0),
        clock=lambda: next(ticks),
    )

    assert result.stopped_reason == "wall_clock"
    assert result.turns == 1


def test_off_allowlist_call_never_reaches_dispatch() -> None:
    model = _scripted(
        [
            {"tool_calls": [{"name": "made_up_tool", "arguments": {}}]},
            {"text": "recovered"},
        ]
    )
    dispatch = RecordingDispatch()
    result = _run(model, dispatch)

    assert dispatch.calls == []
    assert result.final_answer == "recovered"
    tool_record = result.turn_records[1]
    assert tool_record.kind == "tool"
    assert tool_record.tool_name == "made_up_tool"
    assert tool_record.is_error is True


def test_tool_result_truncated_at_fixed_cap() -> None:
    class TruncationSpy(ToolAgentModel):
        spec = "spy:test"

        def __init__(self) -> None:
            self.fed_back: str | None = None

        def propose(
            self, transcript: Sequence[TranscriptItem], tools: Sequence[ToolDef]
        ) -> AgentTurn:
            for item in transcript:
                if isinstance(item, ToolReturn):
                    self.fed_back = item.text
            if self.fed_back is None:
                return AgentTurn(
                    text="",
                    tool_calls=(ToolCall(call_id="c", name="search_notes", arguments={}),),
                    model="spy",
                    input_tokens=1,
                    output_tokens=1,
                    latency_ms=0.0,
                )
            return AgentTurn(
                text="done",
                tool_calls=(),
                model="spy",
                input_tokens=1,
                output_tokens=1,
                latency_ms=0.0,
            )

    model = TruncationSpy()
    huge = "x" * (TOOL_RESULT_MAX_CHARS + 500)
    _run(model, RecordingDispatch(ToolOutcome(text=huge, is_error=False)))

    assert model.fed_back is not None
    assert model.fed_back.startswith("x" * 100)
    assert "truncated" in model.fed_back
    assert len(model.fed_back) < len(huge)


def test_model_error_is_wrapped_with_partial_accounting() -> None:
    model = _scripted([{"text": "unreachable"}], needle="never-matches")
    with pytest.raises(AgentLoopError) as excinfo:
        _run(model, RecordingDispatch())

    assert isinstance(excinfo.value.cause, LLMRunnerError)
    partial = excinfo.value.partial
    assert partial.stopped_reason == "error"
    assert partial.turns == 0
    assert partial.total_input_tokens == 0


def test_mid_loop_model_error_keeps_spent_tokens() -> None:
    # One scripted turn, then the script exhausts: the second propose fails
    # AFTER a real model call and tool dispatch — that cost must be preserved.
    model = _scripted([{"tool_calls": [{"name": "search_notes", "arguments": {}}]}])
    with pytest.raises(AgentLoopError) as excinfo:
        _run(model, RecordingDispatch())

    partial = excinfo.value.partial
    assert partial.stopped_reason == "error"
    assert partial.turns == 1
    assert partial.tool_call_count == 1
    assert partial.total_input_tokens == 10
    assert partial.total_output_tokens == 5
    assert [record.kind for record in partial.turn_records] == ["model", "tool"]


def test_dispatch_error_is_wrapped_and_records_the_dying_call() -> None:
    class ExplodingDispatch:
        def __call__(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
            raise RuntimeError("stdio session lost")

    model = _scripted([{"tool_calls": [{"name": "search_notes", "arguments": {"q": "x"}}]}])
    with pytest.raises(AgentLoopError) as excinfo:
        run_agent_loop(
            model=model,
            dispatch=ExplodingDispatch(),
            tools=TOOLS,
            prompt="Task: x",
            budget=AgentBudget(),
        )

    assert isinstance(excinfo.value.cause, RuntimeError)
    partial = excinfo.value.partial
    assert partial.total_input_tokens == 10  # the model turn already happened
    assert partial.tool_call_count == 1
    tool_record = partial.turn_records[-1]
    assert tool_record.kind == "tool"
    assert tool_record.tool_name == "search_notes"
    assert tool_record.is_error is True


def test_wall_clock_gate_between_tool_dispatches() -> None:
    # One assistant turn with three tool calls; the injected clock breaches the
    # budget after the first dispatch, so the remaining calls never dispatch.
    ticks = iter([0.0, 0.0, 10.0, 100.0, 100.0])
    model = _scripted([{"tool_calls": [{"name": "search_notes", "arguments": {}}] * 3}])
    dispatch = RecordingDispatch()
    result = run_agent_loop(
        model=model,
        dispatch=dispatch,
        tools=TOOLS,
        prompt="Task: x",
        budget=AgentBudget(max_task_seconds=50.0),
        clock=lambda: next(ticks),
    )

    assert result.stopped_reason == "wall_clock"
    assert result.final_answer is None
    assert len(dispatch.calls) == 1
    assert result.tool_call_count == 1


def test_per_turn_records_account_tokens_and_counts() -> None:
    model = _scripted(
        [
            {"tool_calls": [{"name": "search_notes", "arguments": {"q": "a"}}]},
            {"text": "done"},
        ]
    )
    result = _run(model, RecordingDispatch())

    kinds = [record.kind for record in result.turn_records]
    assert kinds == ["model", "tool", "model"]
    model_records = [record for record in result.turn_records if record.kind == "model"]
    assert sum(record.input_tokens for record in model_records) == result.total_input_tokens
    assert sum(record.output_tokens for record in model_records) == result.total_output_tokens
    tool_record = result.turn_records[1]
    assert tool_record.tool_name == "search_notes"
    assert tool_record.result_chars == len("ok")
    assert result.tool_call_count == 1
