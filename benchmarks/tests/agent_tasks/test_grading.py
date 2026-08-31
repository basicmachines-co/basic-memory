"""Tests for every grader kind against a tmp_path mini-project."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from basic_memory_benchmarks.agent_tasks.grading import (
    GradingContext,
    JudgeUsage,
    evaluate_grader,
    extract_final_json,
    grade_task,
    normalize_answer_item,
    strip_own_project_prefix,
)
from basic_memory_benchmarks.agent_tasks.models import TurnRecord
from basic_memory_benchmarks.agent_tasks.spec import (
    AgentTaskSpec,
    AnswerContains,
    AnswerMatches,
    AnswerSetEquals,
    FileLineDiff,
    FrontmatterEquals,
    FrontmatterListLen,
    FrontmatterMatches,
    JudgeRubric,
    MarkerAbsent,
    MarkerPresent,
    NewNoteUnder,
    NoteCountDelta,
    NotesUntouched,
    ObservationLines,
    RelationResolves,
    ToolCalled,
)
from test_qa_scoring import FakeRunner

NOTE_TEXT = """---
title: Sample Note
type: task
status: active
steps:
  - one
  - two
current_step: 2
review:
  status: pending
---

# Sample Note

- [status] step one done BMEVAL-test-1111
"""


def _project(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    project_dir = tmp_path / "project"
    (project_dir / "tasks").mkdir(parents=True, exist_ok=True)
    (project_dir / "tasks" / "sample.md").write_text(NOTE_TEXT, encoding="utf-8")
    baseline = {"tasks/sample.md": NOTE_TEXT}
    return project_dir, baseline


def _ctx(
    tmp_path: Path,
    final_answer: str | None = None,
    turn_records: tuple[TurnRecord, ...] = (),
    judge: FakeRunner | None = None,
) -> GradingContext:
    project_dir, baseline = _project(tmp_path)
    return GradingContext(
        final_answer=final_answer,
        project_dir=project_dir,
        baseline=baseline,
        db_path=tmp_path / "memory.db",
        project_name="proj",
        turn_records=turn_records,
        judge=judge,
    )


class TestAnswerExtraction:
    def test_last_fenced_block_wins(self) -> None:
        answer = (
            'first\n```json\n{"permalinks": ["a"]}\n```\n'
            'revised\n```json\n{"permalinks": ["b"]}\n```\n'
        )
        assert extract_final_json(answer) == {"permalinks": ["b"]}

    def test_missing_block_returns_none(self) -> None:
        assert extract_final_json("no json here") is None
        assert extract_final_json(None) is None

    def test_unparseable_block_returns_none(self) -> None:
        assert extract_final_json("```json\n{oops\n```") is None

    def test_normalization(self) -> None:
        assert normalize_answer_item(" /Notes/Redis-Cache-Tuning.md ") == (
            "notes/redis-cache-tuning"
        )

    def test_strip_own_project_prefix_only_at_the_boundary(self) -> None:
        assert strip_own_project_prefix("proj/notes/a", "proj") == "notes/a"
        # The bare project name and a merely prefix-similar project stay intact.
        assert strip_own_project_prefix("proj", "proj") == "proj"
        assert strip_own_project_prefix("projx/notes/a", "proj") == "projx/notes/a"


class TestAnswerGraders:
    def test_answer_set_equals_pass_and_fail(self, tmp_path: Path) -> None:
        gold = frozenset({"notes/a", "notes/b"})
        grader = AnswerSetEquals(key="permalinks", gold=gold)
        good = _ctx(tmp_path, '```json\n{"permalinks": ["/notes/b.md", "Notes/A"]}\n```')
        assert evaluate_grader(grader, good).passed is True

        bad = _ctx(tmp_path, '```json\n{"permalinks": ["notes/a"]}\n```')
        result = evaluate_grader(grader, bad)
        assert result.passed is False
        assert "missing" in result.detail

    def test_answer_set_strips_the_tasks_own_project_prefix(self, tmp_path: Path) -> None:
        # Agents quote permalinks exactly as tools return them — prefixed with
        # the task's project name — while gold is project-relative. The first
        # real-model run failed every AnswerSetEquals task on exactly this.
        gold = frozenset({"notes/a", "notes/b"})
        grader = AnswerSetEquals(key="permalinks", gold=gold)
        ctx = _ctx(tmp_path, '```json\n{"permalinks": ["proj/notes/a", "/Proj/notes/b.md"]}\n```')
        assert evaluate_grader(grader, ctx).passed is True

    def test_answer_set_keeps_a_different_projects_prefix_failing(self, tmp_path: Path) -> None:
        # Cross-project leakage: a permalink quoted from ANOTHER task's project
        # is genuinely wrong even though its suffix matches gold.
        grader = AnswerSetEquals(key="permalinks", gold=frozenset({"notes/a"}))
        ctx = _ctx(tmp_path, '```json\n{"permalinks": ["other-proj/notes/a"]}\n```')
        result = evaluate_grader(grader, ctx)
        assert result.passed is False
        assert "other-proj/notes/a" in result.detail

    def test_answer_set_without_json_fails_with_detail(self, tmp_path: Path) -> None:
        grader = AnswerSetEquals(key="permalinks", gold=frozenset({"a"}))
        result = evaluate_grader(grader, _ctx(tmp_path, "no fenced block"))
        assert result.passed is False
        assert "no parseable fenced JSON block" in result.detail

    def test_markers_and_contains(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, "found BMEVAL-x-1 in the note")
        assert evaluate_grader(MarkerPresent(marker="BMEVAL-x-1"), ctx).passed is True
        assert evaluate_grader(MarkerAbsent(marker="BMEVAL-x-1"), ctx).passed is False
        assert evaluate_grader(MarkerAbsent(marker="BMEVAL-y-2"), ctx).passed is True
        assert evaluate_grader(AnswerContains(needle="Found"), ctx).passed is True

    def test_no_final_answer_fails_present_passes_absent(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, None)
        assert evaluate_grader(MarkerPresent(marker="BMEVAL-x-1"), ctx).passed is False
        assert evaluate_grader(MarkerAbsent(marker="BMEVAL-x-1"), ctx).passed is True

    def test_answer_matches_accepts_both_tool_spellings(self, tmp_path: Path) -> None:
        # The man-chain regression: the man page is edit-note(3) but the MCP
        # tool is edit_note — both spellings are substantively correct.
        grader = AnswerMatches(pattern=r"edit[-_]note")
        assert evaluate_grader(grader, _ctx(tmp_path, "the page is edit-note(3)")).passed is True
        assert evaluate_grader(grader, _ctx(tmp_path, "use the edit_note tool")).passed is True
        result = evaluate_grader(grader, _ctx(tmp_path, "use write_note instead"))
        assert result.passed is False
        assert "missing" in result.detail

    def test_answer_matches_is_case_insensitive_like_contains(self, tmp_path: Path) -> None:
        grader = AnswerMatches(pattern=r"edit[-_]note")
        assert evaluate_grader(grader, _ctx(tmp_path, "call Edit_Note")).passed is True
        assert evaluate_grader(grader, _ctx(tmp_path, None)).passed is False


class TestFileGraders:
    def test_note_count_delta(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path)
        assert evaluate_grader(NoteCountDelta(delta=0), ctx).passed is True
        (ctx.project_dir / "tasks" / "new.md").write_text("---\ntitle: N\n---\n")
        assert evaluate_grader(NoteCountDelta(delta=1), ctx).passed is True
        assert evaluate_grader(NoteCountDelta(delta=0), ctx).passed is False

    def test_notes_untouched_and_except_globs(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path)
        assert evaluate_grader(NotesUntouched(), ctx).passed is True
        (ctx.project_dir / "tasks" / "sample.md").write_text("changed", encoding="utf-8")
        assert evaluate_grader(NotesUntouched(), ctx).passed is False
        excused = NotesUntouched(except_globs=("tasks/sample.md",))
        assert evaluate_grader(excused, ctx).passed is True

    def test_frontmatter_equals_with_coercion_and_dot_notation(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path)
        path = "tasks/sample.md"
        assert evaluate_grader(
            FrontmatterEquals(path=path, key="status", value="active"), ctx
        ).passed
        assert evaluate_grader(
            FrontmatterEquals(path=path, key="current_step", value=2), ctx
        ).passed
        # str-comparison fallback: "2" matches the YAML int 2.
        assert evaluate_grader(
            FrontmatterEquals(path=path, key="current_step", value="2"), ctx
        ).passed
        assert evaluate_grader(
            FrontmatterEquals(path=path, key="review.status", value="pending"), ctx
        ).passed
        assert not evaluate_grader(
            FrontmatterEquals(path=path, key="review.status", value="done"), ctx
        ).passed
        assert not evaluate_grader(
            FrontmatterEquals(path=path, key="missing", value="x"), ctx
        ).passed

    def test_frontmatter_matches_and_list_len(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path)
        path = "tasks/sample.md"
        assert evaluate_grader(
            FrontmatterMatches(path=path, key="status", pattern=r"^act"), ctx
        ).passed
        assert evaluate_grader(FrontmatterListLen(path=path, key="steps", length=2), ctx).passed
        assert not evaluate_grader(FrontmatterListLen(path=path, key="steps", length=3), ctx).passed
        assert not evaluate_grader(
            FrontmatterListLen(path=path, key="status", length=1), ctx
        ).passed

    def test_new_selector_targets_added_file(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path)
        (ctx.project_dir / "tasks" / "added.md").write_text(
            "---\ntitle: Added\ntype: task\n---\n\n- [status] fresh\n", encoding="utf-8"
        )
        assert evaluate_grader(FrontmatterEquals(path="new", key="type", value="task"), ctx).passed
        assert evaluate_grader(
            ObservationLines(path="new", pattern=r"^- \[[a-z-]+\] ", min_count=1), ctx
        ).passed

    def test_new_selector_with_no_new_file_fails(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path)
        result = evaluate_grader(FrontmatterEquals(path="new", key="type", value="task"), ctx)
        assert result.passed is False
        assert "matched 0 files" in result.detail

    def test_new_note_under_requires_the_prompted_directory(self, tmp_path: Path) -> None:
        grader = NewNoteUnder(prefix="tasks/")
        ctx = _ctx(tmp_path)
        # No new note at all fails with detail — never a vacuous pass.
        no_new = evaluate_grader(grader, ctx)
        assert no_new.passed is False
        assert "no new notes" in no_new.detail

        (ctx.project_dir / "tasks" / "added.md").write_text(
            "---\ntitle: A\n---\n", encoding="utf-8"
        )
        assert evaluate_grader(grader, ctx).passed is True

    def test_new_note_outside_prefix_fails_with_the_path(self, tmp_path: Path) -> None:
        grader = NewNoteUnder(prefix="tasks/")
        ctx = _ctx(tmp_path)
        (ctx.project_dir / "misplaced.md").write_text("---\ntitle: M\n---\n", encoding="utf-8")
        result = evaluate_grader(grader, ctx)
        assert result.passed is False
        assert "misplaced.md" in result.detail

    def test_new_note_under_uses_directory_semantics_not_string_prefix(
        self, tmp_path: Path
    ) -> None:
        # "tasks-archive" starts with "tasks" as a string but is a different
        # directory; a bare-string prefix check would wrongly accept it.
        grader = NewNoteUnder(prefix="tasks")
        ctx = _ctx(tmp_path)
        (ctx.project_dir / "tasks-archive").mkdir()
        (ctx.project_dir / "tasks-archive" / "x.md").write_text(
            "---\ntitle: X\n---\n", encoding="utf-8"
        )
        assert evaluate_grader(grader, ctx).passed is False

    def test_observation_lines_counts(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path)
        grader = ObservationLines(path="tasks/sample.md", pattern=r"^- \[[a-z-]+\] ", min_count=2)
        assert evaluate_grader(grader, ctx).passed is False

    def test_file_line_diff_exactly_one_change(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path)
        changed = NOTE_TEXT.replace("status: active", "status: done")
        (ctx.project_dir / "tasks" / "sample.md").write_text(changed, encoding="utf-8")
        grader = FileLineDiff(
            path="tasks/sample.md",
            removed_pattern=r"status: active",
            added_pattern=r"status: done",
        )
        assert evaluate_grader(grader, ctx).passed is True

        two_changes = changed.replace("title: Sample Note", "title: Renamed")
        (ctx.project_dir / "tasks" / "sample.md").write_text(two_changes, encoding="utf-8")
        assert evaluate_grader(grader, ctx).passed is False


DB_SCHEMA = """
CREATE TABLE project (id INTEGER PRIMARY KEY, name TEXT);
CREATE TABLE entity (id INTEGER PRIMARY KEY, project_id INTEGER, permalink TEXT);
CREATE TABLE relation (
    id INTEGER PRIMARY KEY, from_id INTEGER, to_id INTEGER, to_name TEXT, relation_type TEXT
);
"""


def _make_db(path: Path, rows: dict[str, list[tuple]]) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(DB_SCHEMA)
    for table, table_rows in rows.items():
        if table_rows:
            placeholders = ",".join("?" for _ in table_rows[0])
            connection.executemany(f"INSERT INTO {table} VALUES ({placeholders})", table_rows)
    connection.commit()
    connection.close()


class TestRelationResolves:
    def test_resolved_relation_passes(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path)
        _make_db(
            ctx.db_path,
            {
                "project": [(1, "proj")],
                "entity": [(10, 1, "notes/source"), (11, 1, "notes/target")],
                "relation": [(100, 10, 11, "Target", "relates_to")],
            },
        )
        grader = RelationResolves(
            source_permalink="notes/source", targets=frozenset({"notes/target"})
        )
        assert evaluate_grader(grader, ctx).passed is True

    def test_relation_type_filter(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path)
        _make_db(
            ctx.db_path,
            {
                "project": [(1, "proj")],
                "entity": [(10, 1, "notes/source"), (11, 1, "notes/target")],
                "relation": [(100, 10, 11, "Target", "relates_to")],
            },
        )
        wrong_type = RelationResolves(
            source_permalink="notes/source",
            targets=frozenset({"notes/target"}),
            relation_type="depends_on",
        )
        assert evaluate_grader(wrong_type, ctx).passed is False

    def test_unresolved_forward_ref_fails(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path)
        _make_db(
            ctx.db_path,
            {
                "project": [(1, "proj")],
                "entity": [(10, 1, "notes/source")],
                # to_id NULL: a forward reference that never resolved.
                "relation": [(100, 10, None, "Target", "relates_to")],
            },
        )
        grader = RelationResolves(
            source_permalink="notes/source", targets=frozenset({"notes/target"})
        )
        assert evaluate_grader(grader, ctx).passed is False

    def test_missing_db_fails_loudly(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path)
        grader = RelationResolves(source_permalink="s", targets=frozenset({"t"}))
        with pytest.raises(RuntimeError, match="Index database not found"):
            evaluate_grader(grader, ctx)


class TestToolCalledAndJudge:
    def test_tool_called_diagnostic(self, tmp_path: Path) -> None:
        records = (
            TurnRecord(turn_index=0, kind="model"),
            TurnRecord(turn_index=1, kind="tool", tool_name="search_notes"),
        )
        ctx = _ctx(tmp_path, turn_records=records)
        grader = ToolCalled(name_pattern=r"search_notes|grep")
        result = evaluate_grader(grader, ctx)
        assert result.passed is True
        assert result.required is False
        assert evaluate_grader(ToolCalled(name_pattern=r"^cat$"), ctx).passed is False

    def test_judge_rubric_verdicts_and_usage(self, tmp_path: Path) -> None:
        judge = FakeRunner({"good answer": "CORRECT - covers the rubric"})
        ctx = _ctx(tmp_path, "good answer", judge=judge)
        usage = JudgeUsage()
        result = evaluate_grader(JudgeRubric(rubric="must mention X"), ctx, usage)
        assert result.passed is True
        assert usage.calls == 1
        assert usage.input_tokens == 10

        judge_bad = FakeRunner({"bad answer": "INCORRECT - misses X"})
        ctx_bad = _ctx(tmp_path / "b", "bad answer", judge=judge_bad)
        assert evaluate_grader(JudgeRubric(rubric="must mention X"), ctx_bad).passed is False

    def test_judge_without_runner_raises(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, "answer")
        with pytest.raises(RuntimeError, match="requires a judge"):
            evaluate_grader(JudgeRubric(rubric="r"), ctx)


class TestGradeTask:
    def test_required_predicates_gate_the_pass(self, tmp_path: Path) -> None:
        spec = AgentTaskSpec(
            id="t",
            skill="memory-continue",
            source="test",
            prompt="p",
            graders=(
                MarkerPresent(marker="BMEVAL-x-1"),
                ToolCalled(name_pattern="never"),  # diagnostic, must not gate
            ),
        )
        passed, predicates, usage = grade_task(spec, _ctx(tmp_path, "has BMEVAL-x-1"))
        assert passed is True
        assert [p.passed for p in predicates] == [True, False]
        assert usage.calls == 0

    def test_required_failure_fails_task(self, tmp_path: Path) -> None:
        spec = AgentTaskSpec(
            id="t",
            skill="memory-continue",
            source="test",
            prompt="p",
            graders=(MarkerPresent(marker="BMEVAL-x-1"),),
        )
        passed, _, _ = grade_task(spec, _ctx(tmp_path, "nothing"))
        assert passed is False
