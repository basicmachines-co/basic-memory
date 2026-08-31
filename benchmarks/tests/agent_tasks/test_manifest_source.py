"""Task-manifest source tests: parse, fail-fast schema, id filter, state predicate."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from basic_memory_benchmarks.agent_tasks.manifest import load_task_manifest
from basic_memory_benchmarks.agent_tasks.spec import (
    AgentTaskSpec,
    AnswerContains,
    AnswerMatches,
    AnswerSetEquals,
    Grader,
    JudgeRubric,
    MarkerAbsent,
    MarkerPresent,
    NoteCountDelta,
    NotesUntouched,
    RelationResolves,
    ToolCalled,
    spec_needs_project_state,
)


def _row(task_id: str = "xafs-dp001-q01", group: str = "xafs-dp001", **overrides: Any) -> dict:
    row: dict[str, Any] = {
        "id": task_id,
        "skill": "single_hop",
        "group": group,
        "source": "supermemory/xAFS dp_001 q01 @21142b2c",
        "prompt": "What was the amount of the invoice?",
        "graders": [{"kind": "judge_rubric", "rubric": "Question: ...\nGold answer: ..."}],
    }
    row.update(overrides)
    return row


def _write_manifest(tmp_path: Path, rows: object) -> Path:
    path = tmp_path / "tasks.json"
    path.write_text(json.dumps(rows), encoding="utf-8")
    return path


def test_manifest_rows_parse_into_specs(tmp_path: Path) -> None:
    path = _write_manifest(tmp_path, [_row()])

    (spec,) = load_task_manifest(path)

    assert spec == AgentTaskSpec(
        id="xafs-dp001-q01",
        skill="single_hop",
        source="supermemory/xAFS dp_001 q01 @21142b2c",
        prompt="What was the amount of the invoice?",
        graders=(JudgeRubric(rubric="Question: ...\nGold answer: ..."),),
        group="xafs-dp001",
    )
    assert spec.graders[0].required is True


def test_tool_called_grader_parses_as_diagnostic(tmp_path: Path) -> None:
    row = _row(graders=[{"kind": "tool_called", "name_pattern": "search_.*"}])
    path = _write_manifest(tmp_path, [row])

    (spec,) = load_task_manifest(path)

    assert spec.graders == (ToolCalled(name_pattern="search_.*"),)
    assert spec.graders[0].required is False  # diagnostics never gate a pass


def test_tasks_are_ordered_group_then_id(tmp_path: Path) -> None:
    # Shuffled input: the loader orders (group, id) so the driver ingests each
    # group corpus once and runs its tasks contiguously.
    rows = [
        _row("xafs-dp002-q01", group="xafs-dp002"),
        _row("xafs-dp001-q02"),
        _row("xafs-dp001-q01"),
    ]
    path = _write_manifest(tmp_path, rows)

    specs = load_task_manifest(path)

    assert [spec.id for spec in specs] == [
        "xafs-dp001-q01",
        "xafs-dp001-q02",
        "xafs-dp002-q01",
    ]


def test_id_filter_selects_subset_and_dedupes(tmp_path: Path) -> None:
    rows = [_row("a-q1", group="a"), _row("a-q2", group="a"), _row("b-q1", group="b")]
    path = _write_manifest(tmp_path, rows)

    specs = load_task_manifest(path, task_ids=["b-q1", "a-q1", "b-q1"])

    assert [spec.id for spec in specs] == ["a-q1", "b-q1"]


def test_unknown_filter_id_rejected_listing_known(tmp_path: Path) -> None:
    path = _write_manifest(tmp_path, [_row()])

    with pytest.raises(ValueError, match=r"Unknown task ids: \['nope'\]") as excinfo:
        load_task_manifest(path, task_ids=["nope"])
    assert "xafs-dp001-q01" in str(excinfo.value)


def test_missing_manifest_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_task_manifest(tmp_path / "missing.json")


@pytest.mark.parametrize("payload", [{}, [], "rows"])
def test_manifest_must_be_nonempty_array(tmp_path: Path, payload: object) -> None:
    path = _write_manifest(tmp_path, payload)

    with pytest.raises(ValueError, match="non-empty JSON array"):
        load_task_manifest(path)


def test_non_object_task_row_fails(tmp_path: Path) -> None:
    path = _write_manifest(tmp_path, ["not a task"])

    with pytest.raises(ValueError, match="task at index 0 is not an object"):
        load_task_manifest(path)


@pytest.mark.parametrize("missing", ["id", "skill", "group", "source", "prompt", "graders"])
def test_missing_task_key_is_named(tmp_path: Path, missing: str) -> None:
    row = _row()
    del row[missing]
    path = _write_manifest(tmp_path, [row])

    with pytest.raises(ValueError, match=f"missing keys \\['{missing}'\\]"):
        load_task_manifest(path)


def test_empty_string_field_fails(tmp_path: Path) -> None:
    path = _write_manifest(tmp_path, [_row(prompt="   ")])

    with pytest.raises(ValueError, match="empty or non-string 'prompt'"):
        load_task_manifest(path)


@pytest.mark.parametrize("graders", [[], "judge"])
def test_empty_or_non_list_graders_fails(tmp_path: Path, graders: object) -> None:
    path = _write_manifest(tmp_path, [_row(graders=graders)])

    with pytest.raises(ValueError, match="empty or non-list 'graders'"):
        load_task_manifest(path)


def test_non_object_grader_fails(tmp_path: Path) -> None:
    path = _write_manifest(tmp_path, [_row(graders=["judge_rubric"])])

    with pytest.raises(ValueError, match="grader is not an object"):
        load_task_manifest(path)


def test_unknown_grader_kind_fails_listing_v1_kinds(tmp_path: Path) -> None:
    # v1 manifest kinds are the state-free pair only: manifest tasks share
    # read-only group projects where project-state graders would
    # cross-contaminate. Unknown kinds fail fast, never silently skip.
    row = _row(graders=[{"kind": "note_count_delta", "delta": 1}])
    path = _write_manifest(tmp_path, [row])

    with pytest.raises(ValueError, match="unknown grader kind 'note_count_delta'") as excinfo:
        load_task_manifest(path)
    assert "judge_rubric, tool_called" in str(excinfo.value)


def test_judge_rubric_without_rubric_text_fails(tmp_path: Path) -> None:
    path = _write_manifest(tmp_path, [_row(graders=[{"kind": "judge_rubric", "rubric": ""}])])

    with pytest.raises(ValueError, match="judge_rubric has no rubric text"):
        load_task_manifest(path)


def test_tool_called_without_pattern_fails(tmp_path: Path) -> None:
    path = _write_manifest(tmp_path, [_row(graders=[{"kind": "tool_called"}])])

    with pytest.raises(ValueError, match="tool_called has no name_pattern"):
        load_task_manifest(path)


def test_duplicate_task_id_fails(tmp_path: Path) -> None:
    path = _write_manifest(tmp_path, [_row(), _row()])

    with pytest.raises(ValueError, match="duplicate task id 'xafs-dp001-q01'"):
        load_task_manifest(path)


# --- Grader kind -> project-state predicate mapping ---


def _spec_with(*graders: Grader) -> AgentTaskSpec:
    return AgentTaskSpec(id="t", skill="s", source="src", prompt="p", graders=tuple(graders))


@pytest.mark.parametrize(
    "grader",
    [
        AnswerSetEquals(key="k", gold=frozenset({"x"})),
        MarkerPresent(marker="M"),
        MarkerAbsent(marker="M"),
        AnswerContains(needle="x"),
        AnswerMatches(pattern="x"),
        ToolCalled(name_pattern="x"),
        JudgeRubric(rubric="r"),
    ],
)
def test_answer_and_trace_graders_are_state_free(grader: Grader) -> None:
    assert spec_needs_project_state(_spec_with(grader)) is False


@pytest.mark.parametrize(
    "grader",
    [
        NoteCountDelta(delta=1),
        NotesUntouched(),
        RelationResolves(source_permalink="p", targets=frozenset({"t"})),
    ],
)
def test_project_state_graders_keep_the_full_flow(grader: Grader) -> None:
    assert spec_needs_project_state(_spec_with(grader)) is True


def test_one_stateful_grader_makes_the_spec_stateful() -> None:
    spec = _spec_with(JudgeRubric(rubric="r"), NoteCountDelta(delta=0))
    assert spec_needs_project_state(spec) is True


def test_manifest_loaded_specs_are_state_free(tmp_path: Path) -> None:
    # The v1 manifest kinds are exactly the state-free set, so every
    # manifest-loaded task skips baseline snapshot and post-loop settle.
    rows = [
        _row("a-q1", group="a"),
        _row("a-q2", group="a", graders=[{"kind": "tool_called", "name_pattern": "read_.*"}]),
    ]
    path = _write_manifest(tmp_path, rows)

    specs = load_task_manifest(path)

    assert all(spec_needs_project_state(spec) is False for spec in specs)
