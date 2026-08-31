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
from basic_memory_benchmarks.agent_tasks.spec import AgentTaskSpec, JudgeRubric
from basic_memory_benchmarks.agent_tasks.surfaces import (
    RICH_SURFACE,
    SurfaceUnavailableError,
    read_only_view,
)
from basic_memory_benchmarks.converters.xafs_to_corpus import convert_xafs_to_corpus
from basic_memory_benchmarks.fairness import validate_surface_fairness
from basic_memory_benchmarks.llm.runners import LLMResult, LLMRunner, LLMRunnerError
from basic_memory_benchmarks.llm.tool_agent import ScriptedToolAgent, ToolDef
from basic_memory_benchmarks.utils import sha256_file
from xafs_fixture import (
    DP1_CROSS_FORMAT_ANSWER,
    DP1_DUE_DATE,
    DP1_INVOICE_AMOUNT,
    DP1_REFERRER,
    DP2_TRAINING_SUMMARY,
    write_xafs_root,
)

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
    assert manifest["task_manifest_sha256"] is None  # shipped-task run
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

    # The scripted {project} placeholder was substituted before dispatch. No
    # "at-" literal here: generated run ids already carry it (the first real
    # run produced "at-at-..." project names).
    assert session.calls[0][1]["project"] == "test-run-curate-orphans"
    assert session.stopped is True


def test_state_graded_task_reindexes_before_grading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # First real-model run: a correct, project-scoped edit_note left its new
    # wikilink relation unresolved at grading time (rich curate-connect) —
    # settle only watches file-sync work, while forward references resolve in
    # a project-index pass. State-graded tasks must re-run that pass, then
    # settle, before graders read the index.
    commands, settles = _stub_bm_recording(monkeypatch)
    monkeypatch.chdir(tmp_path)
    agent = ScriptedToolAgent(
        script={
            "tasks": {
                "Migrate CI to uv": [
                    {
                        "tool_calls": [
                            {
                                "name": "edit_note",
                                "arguments": {"identifier": "x", "project": "{project}"},
                            }
                        ]
                    },
                    {"text": "done\n```json\n{}\n```"},
                ]
            }
        }
    )
    run_agent_tasks(
        _config(tmp_path, task_ids=["tasks-complete"]),
        model_factory=lambda spec: agent,
        session_factory=lambda runtime: FakeSession(RICH_SURFACE.tool_allowlist),
    )

    project = "test-run-tasks-complete"
    reindexes = [cmd[cmd.index("reindex") + 1 :] for cmd in commands if "reindex" in cmd]
    assert reindexes == [
        ["-p", project, "--full", "--search"],  # seed indexing before the loop
        ["-p", project, "--search"],  # post-loop: resolve the agent's writes
    ]
    # Settle follows BOTH passes: seed settle, then the pre-grading settle.
    assert settles == [project, project]


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


# --- Dataset-manifest (grouped) runs: the xAFS execution path ---


MANIFEST_TASK_IDS = [
    "xafs-dp001-q01",
    "xafs-dp001-q02",
    "xafs-dp001-q03",
    "xafs-dp001-q04",
    "xafs-dp002-q01",
    "xafs-dp002-q02",
]


class _StubJudgeRunner(LLMRunner):
    """Judge stub: CORRECT unless the judged final answer contains 'WRONG'."""

    def __init__(self) -> None:
        self.spec = "stub:judge"
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> LLMResult:
        self.prompts.append(prompt)
        # Everything after the template's "Final answer:" is the agent's text;
        # judging on it (not the rubric half) keeps the verdict answer-driven.
        answer = prompt.split("Final answer:", 1)[1]
        verdict = "INCORRECT - decoy answer" if "WRONG" in answer else "CORRECT - matches gold"
        return LLMResult(
            text=verdict, model="stub", input_tokens=7, output_tokens=3, latency_ms=0.0
        )


def _stub_bm_recording(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[list[list[str]], list[str]]:
    """Like _stub_bm, but records every bm command and settle for assertions."""
    commands: list[list[str]] = []
    settles: list[str] = []
    monkeypatch.setattr(driver, "resolve_clean_checkout_sha", lambda checkout: "deadbeef")

    def record_command(command: list[str], **kwargs: Any) -> SimpleNamespace:
        commands.append(list(command))
        return SimpleNamespace(stdout="", stderr="")

    monkeypatch.setattr(driver, "run_command", record_command)

    def record_settle(
        *, prefix: list[str], env: dict[str, str], project_name: str, timeout_seconds: float
    ) -> tuple[float, str]:
        settles.append(project_name)
        return (0.0, "status-json")

    monkeypatch.setattr(driver, "settle_index", record_settle)
    return commands, settles


def _forbid_baseline(project_dir: Path) -> dict[str, str]:
    raise AssertionError("snapshot_baseline must not run for state-free manifest tasks")


def _xafs_agent() -> ScriptedToolAgent:
    """One scripted answer per fixture question, keyed by prompt substring.

    format_spanning questions read the raw file resource (read_content);
    single/multi-hop search notes. dp_002 q01 answers with a WRONG decoy the
    stub judge marks INCORRECT, so pass/fail is judge-driven.
    """

    def turns(tool: str, arguments: dict[str, Any], answer: str) -> list[dict[str, Any]]:
        return [
            {"tool_calls": [{"name": tool, "arguments": {**arguments, "project": "{project}"}}]},
            {"text": f"{answer}\n```json\n{{}}\n```"},
        ]

    return ScriptedToolAgent(
        script={
            "tasks": {
                "amount of the Acme onboarding invoice": turns(
                    "search_notes", {"query": "invoice"}, f"The invoice was {DP1_INVOICE_AMOUNT}."
                ),
                "referred the client": turns(
                    "search_notes", {"query": "referral"}, f"{DP1_REFERRER} referred Acme."
                ),
                "when is payment due": turns(
                    "read_content",
                    {"path": "data/mail/2026-04-01_invoice.eml"},
                    f"Payment is due {DP1_DUE_DATE}.",
                ),
                "revenue first exceed": turns(
                    "read_content",
                    {"path": "data/notes/metrics.csv.md"},
                    f"In {DP1_CROSS_FORMAT_ANSWER}.",
                ),
                "goal pace": turns("search_notes", {"query": "pace"}, "WRONG: 9:30 per mile."),
                "longest run": turns("search_notes", {"query": "race"}, f"{DP2_TRAINING_SUMMARY}."),
            }
        }
    )


def _manifest_config(tmp_path: Path) -> AgentTasksConfig:
    root = write_xafs_root(tmp_path)
    groups_dir, tasks_path, _, _ = convert_xafs_to_corpus(
        dataset_root=root, output_dir=tmp_path / "generated"
    )
    return _config(
        tmp_path,
        run_id="xafs-run",
        task_ids=[],
        task_manifest=str(tasks_path),
        corpus_dir=str(groups_dir),
        judge_spec="stub:judge",
    )


def test_grouped_manifest_run_shares_projects_and_reports_groups(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _manifest_config(tmp_path)
    commands, settles = _stub_bm_recording(monkeypatch)
    monkeypatch.setattr(driver, "snapshot_baseline", _forbid_baseline)
    monkeypatch.chdir(tmp_path)
    session = FakeSession(RICH_SURFACE.tool_allowlist)
    judge = _StubJudgeRunner()

    run_dir = run_agent_tasks(
        config,
        model_factory=lambda spec: _xafs_agent(),
        session_factory=lambda runtime: session,
        judge_factory=lambda spec: judge,
    )

    # Ingest once per (surface, group): one `project add` and one settle per
    # persona; NO post-loop settles (every manifest task is state-free), and
    # snapshot_baseline (monkeypatched to raise) was never touched.
    adds = [cmd for cmd in commands if "project" in cmd and "add" in cmd]
    added_projects = sorted(cmd[cmd.index("add") + 1] for cmd in adds)
    assert added_projects == ["xafs-run-xafs-dp001", "xafs-run-xafs-dp002"]
    assert sorted(settles) == added_projects
    assert len(settles) == 2

    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["task_ids"] == MANIFEST_TASK_IDS  # (group, id) order
    # tasks.json is pinned alongside the corpus: a corrections re-run changes
    # gold answers/rubrics without touching the corpus checksum.
    assert config.task_manifest is not None
    assert manifest["task_manifest_sha256"] == sha256_file(Path(config.task_manifest))
    # The read-only surface view is what actually ran, echoed for the
    # fairness audit trail: no write verbs on a shared warm project.
    echo = manifest["surfaces"][0]
    assert echo["tool_allowlist"] == list(read_only_view(RICH_SURFACE).tool_allowlist)
    assert "write_note" not in echo["tool_allowlist"]

    rows = [
        json.loads(line) for line in (run_dir / "per-task-agent.jsonl").read_text().splitlines()
    ]
    assert [row["task_id"] for row in rows] == MANIFEST_TASK_IDS
    assert [row["group"] for row in rows] == ["xafs-dp001"] * 4 + ["xafs-dp002"] * 2
    # skill = family: the per-skill report is the per-question-type breakdown.
    assert {row["skill"] for row in rows} == {"single_hop", "multi_hop", "format_spanning"}

    # Judge-driven pass/fail: the WRONG decoy fails as a graded task (never an
    # error row); everything else passes.
    (failed,) = [row for row in rows if not row["passed"]]
    assert failed["task_id"] == "xafs-dp002-q01"
    assert failed["error"] is None
    assert failed["predicates"][0]["kind"] == "JudgeRubric"
    assert failed["predicates"][0]["passed"] is False

    summary = json.loads((run_dir / "agent-tasks-summary.json").read_text())
    surface = summary["surfaces"][0]
    assert surface["tasks_passed"] == 5
    assert surface["tasks_errored"] == 0
    # Headline excludes judge tokens: 6 tasks x 2 scripted model turns at
    # 10 in / 5 out each; the judge's 7/3 per call lands only in judge_*.
    assert surface["total_input_tokens"] == 120
    assert surface["total_output_tokens"] == 60
    assert surface["tokens_per_completed_task"] == 36.0
    assert surface["judge_calls"] == 6
    assert surface["judge_input_tokens"] == 42
    assert surface["judge_output_tokens"] == 18
    assert surface["per_skill"]["single_hop"] == {
        "total": 2,
        "passed": 1,
        "tokens_per_completed": 60.0,
    }
    # tokens/correct per group (persona) is the corpus-scaling curve.
    assert surface["per_group"]["xafs-dp001"] == {
        "total": 4,
        "passed": 4,
        "tokens_per_completed": 30.0,
    }
    assert surface["per_group"]["xafs-dp002"] == {
        "total": 2,
        "passed": 1,
        "tokens_per_completed": 60.0,
    }

    report = (run_dir / "summary.md").read_text()
    assert "## Per group (persona)" in report
    assert "| rich | xafs-dp001 | 4/4 | 30.0 |" in report
    assert "| rich | xafs-dp002 | 1/2 | 60.0 |" in report
    assert "## Judge usage (never part of the headline)" in report
    assert "- rich: 60 tokens over 6 calls" in report

    # The scripted {project} placeholder resolved to the shared group project.
    assert session.calls[0][1]["project"] == "xafs-run-xafs-dp001"
    assert len(judge.prompts) == 6


def test_mixed_grouped_and_ungrouped_task_set_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_bm(monkeypatch)
    monkeypatch.chdir(tmp_path)
    mixed = [
        AgentTaskSpec(
            id="a",
            skill="s",
            source="src",
            prompt="p",
            graders=(JudgeRubric(rubric="r"),),
            group="g1",
        ),
        AgentTaskSpec(
            id="b", skill="s", source="src", prompt="p", graders=(JudgeRubric(rubric="r"),)
        ),
    ]
    monkeypatch.setattr(driver, "load_task_manifest", lambda path, task_ids=None: mixed)
    # The manifest file must exist: the driver fingerprints it before loading.
    (tmp_path / "tasks.json").write_text("[]", encoding="utf-8")
    config = _config(tmp_path, task_ids=[], task_manifest="tasks.json")

    with pytest.raises(ValueError, match="mixes grouped and ungrouped"):
        run_agent_tasks(
            config,
            model_factory=lambda spec: _orphan_agent(),
            session_factory=lambda runtime: FakeSession(RICH_SURFACE.tool_allowlist),
        )


def test_grouped_run_requires_the_converter_groups_layout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_bm(monkeypatch)
    monkeypatch.chdir(tmp_path)
    root = write_xafs_root(tmp_path)
    _, tasks_path, _, _ = convert_xafs_to_corpus(
        dataset_root=root, output_dir=tmp_path / "generated"
    )
    # corpus_dir left at the shipped flat corpus: the run must refuse rather
    # than checksum one corpus and ingest another.
    config = _config(tmp_path, task_ids=[], task_manifest=str(tasks_path))

    with pytest.raises(ValueError, match="must point at the converter's groups/"):
        run_agent_tasks(
            config,
            model_factory=lambda spec: _xafs_agent(),
            session_factory=lambda runtime: FakeSession(RICH_SURFACE.tool_allowlist),
        )


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
