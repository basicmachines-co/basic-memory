"""Schema mistakes and domain refusals are different benchmark outcomes."""

from typing import Any

import pytest

from basic_memory_benchmarks.agent_tasks.driver import build_surface_summary
from basic_memory_benchmarks.agent_tasks.loop import AgentLoopError, ToolOutcome, run_agent_loop
from basic_memory_benchmarks.agent_tasks.models import AgentBudget, AgentTaskResult, SurfaceEcho
from basic_memory_benchmarks.llm.tool_agent import ScriptedToolAgent, ToolDef

READ = ToolDef(
    name="bm_read",
    description="Read a note",
    input_schema={
        "type": "object",
        "properties": {"identifier": {"type": "string"}},
        "required": ["identifier"],
        "additionalProperties": False,
    },
)


def test_surface_manifest_keeps_schema_and_description():
    echo = SurfaceEcho(
        name="bm", config_overrides={}, tool_allowlist=[READ.name], tool_definitions=[READ]
    )
    restored = SurfaceEcho.model_validate_json(echo.model_dump_json())
    assert restored.tool_definitions == [READ]


@pytest.mark.parametrize(
    "arguments,valid", [({}, False), ({"identifier": 42}, False), ({"identifier": "missing"}, True)]
)
def test_argument_metrics_do_not_change_server_feedback(arguments: dict[str, Any], valid: bool):
    calls: list[dict[str, Any]] = []

    def dispatch(name: str, arguments: dict[str, Any]) -> ToolOutcome:
        calls.append(arguments)
        return ToolOutcome(text="NOT_FOUND", is_error=True)

    model = ScriptedToolAgent(
        script={
            "tasks": {
                "read": [
                    {"tool_calls": [{"name": "bm_read", "arguments": arguments}]},
                    {"text": "done"},
                ]
            }
        }
    )
    result = run_agent_loop(
        model=model, dispatch=dispatch, tools=[READ], prompt="read", budget=AgentBudget()
    )
    assert calls == [arguments]
    assert result.turn_records[1].arguments_valid is valid
    assert result.turn_records[1].is_error
    assert result.turn_records[1].error_excerpt == "NOT_FOUND"
    summary = build_surface_summary(
        "bm",
        model.spec,
        [
            AgentTaskResult(
                surface="bm",
                task_id="read",
                skill="contracts",
                passed=False,
                turn_records=result.turn_records,
            )
        ],
    )
    counts = summary.per_tool_arguments["bm_read"]
    assert counts.calls == counts.schema_checked_calls == counts.tool_errors == 1
    assert counts.invalid_argument_rate == (0.0 if valid else 1.0)


def test_unknown_tool_has_no_argument_schema():
    model = ScriptedToolAgent(
        script={
            "tasks": {
                "read": [
                    {"tool_calls": [{"name": "unknown", "arguments": {}}]},
                    {"text": "done"},
                ]
            }
        }
    )
    result = run_agent_loop(
        model=model,
        dispatch=lambda name, arguments: pytest.fail("must not dispatch"),
        tools=[READ],
        prompt="read",
        budget=AgentBudget(),
    )
    summary = build_surface_summary(
        "bm",
        model.spec,
        [
            AgentTaskResult(
                surface="bm",
                task_id="read",
                skill="contracts",
                passed=False,
                turn_records=result.turn_records,
            )
        ],
    )
    assert summary.per_tool_arguments["unknown"].invalid_argument_rate is None
    assert summary.per_tool_arguments["unknown"].tool_errors == 1


def test_transport_failure_retains_argument_metric():
    def dispatch(name: str, arguments: dict[str, Any]) -> ToolOutcome:
        raise TimeoutError("transport failed")

    model = ScriptedToolAgent(
        script={
            "tasks": {
                "read": [
                    {"tool_calls": [{"name": "bm_read", "arguments": {}}]},
                ]
            }
        }
    )
    with pytest.raises(AgentLoopError) as caught:
        run_agent_loop(
            model=model, dispatch=dispatch, tools=[READ], prompt="read", budget=AgentBudget()
        )
    assert caught.value.partial.turn_records[-1].arguments_valid is False
