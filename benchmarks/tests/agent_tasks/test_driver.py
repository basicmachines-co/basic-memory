"""Driver tests: orchestration, artifacts, skip/strict, dead sessions, fairness."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from mcp.types import CallToolResult, TextContent

import basic_memory_benchmarks.agent_tasks.driver as driver
from basic_memory_benchmarks.agent_tasks.driver import (
    SessionTerminatedError,
    SurfaceRuntime,
    build_surface_summary,
    run_agent_tasks,
    tool_outcome_from_result,
)
from basic_memory_benchmarks.agent_tasks.loop import ToolOutcome
from basic_memory_benchmarks.agent_tasks.models import AgentTaskResult, AgentTasksConfig
from basic_memory_benchmarks.agent_tasks.surfaces import (
    RICH_SURFACE,
    SurfaceUnavailableError,
)
from basic_memory_benchmarks.fairness import validate_surface_fairness
from basic_memory_benchmarks.llm.runners import LLMRunnerError
from basic_memory_benchmarks.llm.tool_agent import ScriptedToolAgent, ToolDef

CORPUS_DIR = Path(__file__).parents[2] / "benchmarks" / "datasets" / "agent-tasks" / "corpus"

ORPHAN_ANSWER = (
    "Orphans found.\n```json\n"
    '{"permalinks": ["notes/redis-cache-tuning", "notes/postgres-vacuum-notes",'
    ' "notes/coffee-brewing-log"]}\n```'
)


class FakeSession:
    """AgentSession stub over canned tool names/results (no BM subprocess)."""

    def __init__(
        self,
        tool_names: tuple[str, ...],
        *,
        die_on_call: bool = False,
    ) -> None:
        self.tool_names = tool_names
        self.die_on_call = die_on_call
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.started = False
        self.stopped = False

    def start(self) -> None:
        self.started = True

    def tools(self) -> list[ToolDef]:
        return [
            ToolDef(name=name, description="", input_schema={"type": "object"})
            for name in self.tool_names
        ]

    def call_tool(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        self.calls.append((name, arguments))
        if self.die_on_call:
            raise SessionTerminatedError("stdio session lost")
        return ToolOutcome(text="[]", is_error=False)

    def stop(self) -> None:
        self.stopped = True


def _stub_bm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(driver, "resolve_clean_checkout_sha", lambda checkout: "deadbeef")
    monkeypatch.setattr(
        driver, "run_command", lambda *args, **kwargs: SimpleNamespace(stdout="", stderr="")
    )
    monkeypatch.setattr(
        driver,
        "settle_index",
        lambda *, prefix, env, project_name, timeout_seconds: (0.0, "status-json"),
    )


def _config(tmp_path: Path, **overrides: Any) -> AgentTasksConfig:
    defaults: dict[str, Any] = {
        "run_id": "test-run",
        "surfaces": ["rich"],
        "task_ids": ["curate-orphans"],
        "model_spec": "scripted:inline",
        "corpus_dir": str(CORPUS_DIR),
        "output_root": str(tmp_path / "runs"),
        "bm_local_path": str(tmp_path / "bm-checkout"),
    }
    defaults.update(overrides)
    (tmp_path / "bm-checkout").mkdir(exist_ok=True)
    return AgentTasksConfig.model_validate(defaults)


def _orphan_agent(final_text: str = ORPHAN_ANSWER) -> ScriptedToolAgent:
    return ScriptedToolAgent(
        script={
            "tasks": {
                "orphan notes": [
                    {
                        "tool_calls": [
                            {
                                "name": "search_notes",
                                "arguments": {"query": "x", "project": "{project}"},
                            }
                        ]
                    },
                    {"text": final_text},
                ]
            }
        }
    )


def _run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    config: AgentTasksConfig,
    agent: ScriptedToolAgent,
    sessions: dict[str, FakeSession],
) -> Path:
    _stub_bm(monkeypatch)
    monkeypatch.chdir(tmp_path)

    def session_factory(runtime: SurfaceRuntime) -> FakeSession:
        return sessions[runtime.surface.name]

    return run_agent_tasks(
        config,
        model_factory=lambda spec: agent,
        session_factory=session_factory,
    )


def test_happy_path_writes_all_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    session = FakeSession(RICH_SURFACE.tool_allowlist)
    run_dir = _run(
        tmp_path,
        monkeypatch,
        config=_config(tmp_path),
        agent=_orphan_agent(),
        sessions={"rich": session},
    )

    for artifact in (
        "manifest.json",
        "surface-status.json",
        "per-turn.jsonl",
        "per-task-agent.jsonl",
        "agent-tasks-summary.json",
        "summary.md",
    ):
        assert (run_dir / artifact).exists(), artifact

    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["bm_resolved_sha"] == "deadbeef"
    assert manifest["corpus_file_count"] > 20
    assert manifest["surfaces"][0]["name"] == "rich"
    assert manifest["surfaces"][0]["tool_allowlist"] == list(RICH_SURFACE.tool_allowlist)
    assert manifest["surfaces"][0]["observed_tools"] == sorted(RICH_SURFACE.tool_allowlist)
    assert manifest["budget"]["max_turns"] == 20

    summary = json.loads((run_dir / "agent-tasks-summary.json").read_text())
    surface_summary = summary["surfaces"][0]
    assert surface_summary["tasks_passed"] == 1
    assert surface_summary["tokens_per_completed_task"] is not None
    assert summary["fairness_warnings"] == []

    task_rows = [
        json.loads(line) for line in (run_dir / "per-task-agent.jsonl").read_text().splitlines()
    ]
    assert task_rows[0]["passed"] is True
    assert task_rows[0]["stopped_reason"] == "final"
    assert "turn_records" not in task_rows[0]

    turn_rows = [json.loads(line) for line in (run_dir / "per-turn.jsonl").read_text().splitlines()]
    assert {row["kind"] for row in turn_rows} == {"model", "tool"}

    # The scripted {project} placeholder was substituted before dispatch.
    assert session.calls[0][1]["project"] == "at-test-run-curate-orphans"
    assert session.stopped is True


def test_headline_is_none_not_zero_when_nothing_completes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wrong = 'nope\n```json\n{"permalinks": ["notes/wrong"]}\n```'
    run_dir = _run(
        tmp_path,
        monkeypatch,
        config=_config(tmp_path),
        agent=_orphan_agent(final_text=wrong),
        sessions={"rich": FakeSession(RICH_SURFACE.tool_allowlist)},
    )
    summary = json.loads((run_dir / "agent-tasks-summary.json").read_text())
    surface_summary = summary["surfaces"][0]
    assert surface_summary["tasks_passed"] == 0
    assert surface_summary["tokens_per_completed_task"] is None
    assert "n/a — 0 tasks completed" in (run_dir / "summary.md").read_text()


def test_posix_without_tools_is_skipped_with_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A rich-only BM: the posix session starts (shared write verbs exist) but
    # exposes no cat/grep/... — recorded as an explicit skip, never dropped.
    config = _config(tmp_path, surfaces=["rich", "posix"])
    sessions = {
        "rich": FakeSession(RICH_SURFACE.tool_allowlist),
        "posix": FakeSession(("write_note", "edit_note", "move_note", "delete_note")),
    }
    run_dir = _run(tmp_path, monkeypatch, config=config, agent=_orphan_agent(), sessions=sessions)

    statuses = {
        row["provider"]: row for row in json.loads((run_dir / "surface-status.json").read_text())
    }
    assert statuses["rich"]["state"] == "ok"
    assert statuses["posix"]["state"] == "skipped"
    assert "cat" in statuses["posix"]["reason"]
    assert "enable_posix_tools" in statuses["posix"]["reason"]
    assert sessions["posix"].stopped is True

    summary = json.loads((run_dir / "agent-tasks-summary.json").read_text())
    assert [row["surface"] for row in summary["surfaces"]] == ["rich"]

    # The human-facing report must say the A/B is incomplete, not just the
    # status artifact and console output.
    report = (run_dir / "summary.md").read_text()
    assert "Surfaces not run" in report
    assert "posix (skipped)" in report
    assert "enable_posix_tools" in report


def test_strict_surfaces_raises_instead_of_skipping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path, surfaces=["posix"], allow_surface_skip=False)
    sessions = {"posix": FakeSession(("write_note", "edit_note"))}
    with pytest.raises(SurfaceUnavailableError, match="posix"):
        _run(tmp_path, monkeypatch, config=config, agent=_orphan_agent(), sessions=sessions)


def test_dead_session_marks_remaining_tasks_errored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path, task_ids=["curate-orphans", "man-chain"])
    session = FakeSession(RICH_SURFACE.tool_allowlist, die_on_call=True)
    run_dir = _run(
        tmp_path, monkeypatch, config=config, agent=_orphan_agent(), sessions={"rich": session}
    )

    task_rows = [
        json.loads(line) for line in (run_dir / "per-task-agent.jsonl").read_text().splitlines()
    ]
    assert len(task_rows) == 2  # never silently dropped
    assert all("mcp session terminated" in row["error"] for row in task_rows)

    # The first task died mid-loop AFTER a real model call: its spent tokens
    # and per-turn records must survive on the errored row. The second task
    # never started (poisoned session), so it carries none.
    first, second = task_rows
    assert first["stopped_reason"] == "error"
    assert first["turns"] == 1
    assert first["tool_calls"] == 1
    assert first["total_input_tokens"] == 10
    assert first["total_output_tokens"] == 5
    assert second["stopped_reason"] is None
    assert second["total_input_tokens"] == 0

    turn_rows = [json.loads(line) for line in (run_dir / "per-turn.jsonl").read_text().splitlines()]
    assert [row["kind"] for row in turn_rows] == ["model", "tool"]
    assert turn_rows[1]["is_error"] is True

    summary = json.loads((run_dir / "agent-tasks-summary.json").read_text())
    assert summary["surfaces"][0]["tasks_errored"] == 2
    assert summary["surfaces"][0]["tasks_passed"] == 0
    # The headline denominator still sees the spent tokens.
    assert summary["surfaces"][0]["total_input_tokens"] == 10


def test_mid_run_model_error_keeps_partial_accounting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The script exhausts after one tool-call turn, so the SECOND propose
    # raises LLMRunnerError mid-loop: the errored row must keep the cost of
    # the model call that already happened.
    one_turn_agent = ScriptedToolAgent(
        script={
            "tasks": {
                "orphan notes": [
                    {"tool_calls": [{"name": "search_notes", "arguments": {"query": "x"}}]}
                ]
            }
        }
    )
    run_dir = _run(
        tmp_path,
        monkeypatch,
        config=_config(tmp_path),
        agent=one_turn_agent,
        sessions={"rich": FakeSession(RICH_SURFACE.tool_allowlist)},
    )

    (row,) = [
        json.loads(line) for line in (run_dir / "per-task-agent.jsonl").read_text().splitlines()
    ]
    assert "exhausted" in row["error"]
    assert row["stopped_reason"] == "error"
    assert row["turns"] == 1
    assert row["total_input_tokens"] == 10
    assert row["total_output_tokens"] == 5


def test_unknown_dispatch_bug_aborts_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class BuggySession(FakeSession):
        def call_tool(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
            raise ValueError("harness bug")

    with pytest.raises(driver.AgentLoopError, match="harness bug"):
        _run(
            tmp_path,
            monkeypatch,
            config=_config(tmp_path),
            agent=_orphan_agent(),
            sessions={"rich": BuggySession(RICH_SURFACE.tool_allowlist)},
        )


def test_judge_failure_keeps_loop_accounting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing_grade(spec: Any, ctx: Any) -> Any:
        raise LLMRunnerError("judge down")

    monkeypatch.setattr(driver, "grade_task", failing_grade)
    run_dir = _run(
        tmp_path,
        monkeypatch,
        config=_config(tmp_path),
        agent=_orphan_agent(),
        sessions={"rich": FakeSession(RICH_SURFACE.tool_allowlist)},
    )

    (row,) = [
        json.loads(line) for line in (run_dir / "per-task-agent.jsonl").read_text().splitlines()
    ]
    assert row["error"] == "grading failed: judge down"
    # The loop completed before grading failed: its full accounting survives.
    assert row["stopped_reason"] == "final"
    assert row["turns"] == 2
    assert row["total_input_tokens"] == 20
    assert row["total_output_tokens"] == 10


def test_reused_run_id_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config(tmp_path)
    _run(
        tmp_path,
        monkeypatch,
        config=config,
        agent=_orphan_agent(),
        sessions={"rich": FakeSession(RICH_SURFACE.tool_allowlist)},
    )
    with pytest.raises(RuntimeError, match="Home already exists"):
        _run(
            tmp_path,
            monkeypatch,
            config=config,
            agent=_orphan_agent(),
            sessions={"rich": FakeSession(RICH_SURFACE.tool_allowlist)},
        )


def _task_row(surface: str, task_id: str) -> AgentTaskResult:
    return AgentTaskResult(surface=surface, task_id=task_id, skill="memory-continue", passed=True)


def test_validate_surface_fairness_flags_task_set_mismatch() -> None:
    results = {
        "rich": [_task_row("rich", "a"), _task_row("rich", "b")],
        "posix": [_task_row("posix", "a")],
    }
    warnings = validate_surface_fairness(results)
    assert len(warnings) == 1
    # sorted() makes 'posix' the baseline, so rich's extra task 'b' is flagged.
    assert "task mismatch" in warnings[0]
    assert "'b'" in warnings[0]

    aligned = {
        "rich": [_task_row("rich", "a")],
        "posix": [_task_row("posix", "a")],
    }
    assert validate_surface_fairness(aligned) == []
    assert validate_surface_fairness({"rich": [_task_row("rich", "a")]}) == []


def test_build_surface_summary_excludes_errored_from_means() -> None:
    ok = AgentTaskResult(
        surface="rich",
        task_id="a",
        skill="memory-continue",
        passed=True,
        stopped_reason="final",
        turns=3,
        tool_calls=2,
        total_input_tokens=100,
        total_output_tokens=50,
        wall_seconds=1.0,
    )
    errored = AgentTaskResult(
        surface="rich",
        task_id="b",
        skill="memory-curate",
        passed=False,
        total_input_tokens=40,
        total_output_tokens=10,
        error="mcp session terminated",
    )
    summary = build_surface_summary("rich", "scripted:x", [ok, errored])

    assert summary.tasks_total == 2
    assert summary.tasks_errored == 1
    assert summary.tasks_passed == 1
    assert summary.pass_rate == 1.0  # over graded tasks only
    # Headline counts ALL attempted tokens (errored calls still cost tokens).
    assert summary.tokens_per_completed_task == 200.0
    assert summary.mean_tool_calls == 2.0  # errored rows excluded from means
    assert summary.per_skill["memory-curate"].tokens_per_completed is None


class TestToolOutcomeFromResult:
    def test_plain_text_success_is_not_an_error(self) -> None:
        result = CallToolResult(
            content=[TextContent(type="text", text="# markdown body")], isError=False
        )
        outcome = tool_outcome_from_result(result)
        assert outcome == ToolOutcome(text="# markdown body", is_error=False)

    def test_mcp_error_flag(self) -> None:
        result = CallToolResult(content=[TextContent(type="text", text="boom")], isError=True)
        assert tool_outcome_from_result(result).is_error is True

    def test_json_error_payload(self) -> None:
        result = CallToolResult(
            content=[TextContent(type="text", text='{"error": "not found"}')],
            isError=False,
        )
        outcome = tool_outcome_from_result(result)
        assert outcome.is_error is True
        assert "not found" in outcome.text

    def test_structured_content_fallback(self) -> None:
        result = CallToolResult(
            content=[], structuredContent={"result": {"title": "x"}}, isError=False
        )
        outcome = tool_outcome_from_result(result)
        assert outcome.is_error is False
        assert "title" in outcome.text
