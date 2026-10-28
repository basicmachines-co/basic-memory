"""Desired file states and negative controls for release mutation tasks."""

import shutil
from pathlib import Path

import frontmatter
import pytest

from basic_memory_benchmarks.agent_tasks.corpus import snapshot_baseline
from basic_memory_benchmarks.agent_tasks.grading import GradingContext, grade_task
from basic_memory_benchmarks.agent_tasks.models import TurnRecord
from basic_memory_benchmarks.agent_tasks.release_edit_tasks import (
    COFFEE_BODY,
    EDIT_GOALS,
    EDIT_LINE,
    EDIT_PATH,
    EDIT_TASKS,
    VACUUM_BODY,
)

CORPUS = Path(__file__).parents[2] / "benchmarks/datasets/agent-tasks/corpus"
BY_ID = {task.id: task for task in EDIT_TASKS}


@pytest.fixture
def project_context(tmp_path: Path) -> GradingContext:
    project = tmp_path / "project"
    shutil.copytree(CORPUS, project)
    return GradingContext(
        final_answer="done",
        project_dir=project,
        baseline=snapshot_baseline(project),
        db_path=tmp_path / "unused.db",
        project_name="test",
    )


def test_gold_bodies_match_original_fixtures():
    for path, gold in ((EDIT_PATH, VACUUM_BODY), ("notes/coffee-brewing-log.md", COFFEE_BODY)):
        body = frontmatter.loads((CORPUS / path).read_text(encoding="utf-8")).content
        assert tuple(line for line in body.splitlines() if line.strip()) == gold


@pytest.mark.parametrize("operation", [goal.name for goal in EDIT_GOALS])
def test_edit_goals_accept_requested_change_and_reject_extra_changes(
    operation: str, project_context: GradingContext
):
    path = project_context.project_dir / EDIT_PATH
    note = frontmatter.loads(path.read_text(encoding="utf-8"))
    match operation:
        case "append":
            note.content += "\n\n" + EDIT_LINE
        case "prepend":
            note.content = EDIT_LINE + "\n\n" + note.content
        case "find-replace":
            note.content = note.content.replace(VACUUM_BODY[4], EDIT_LINE)
        case "replace-section":
            note.content = (
                note.content.split("## Observations")[0] + "## Observations\n\n" + EDIT_LINE
            )
        case "insert-before-section":
            note.content = note.content.replace(
                "## Observations", EDIT_LINE + "\n\n## Observations"
            )
        case "insert-after-section":
            note.content = note.content.replace(
                "## Observations", "## Observations\n\n" + EDIT_LINE
            )
        case "set-metadata":
            note["status"] = "draft"
        case _:
            raise AssertionError(operation)
    path.write_text(frontmatter.dumps(note))
    task = BY_ID[f"contract-edit-{operation}"]
    assert grade_task(task, project_context)[0]
    note.content += "\nUnexpected change"
    path.write_text(frontmatter.dumps(note))
    assert not grade_task(task, project_context)[0]
    note.content = note.content.removesuffix("\nUnexpected change")
    note["unexpected_metadata"] = True
    path.write_text(frontmatter.dumps(note))
    assert not grade_task(task, project_context)[0]


def test_move_requires_old_path_removed_and_other_metadata_preserved(
    project_context: GradingContext,
):
    source = project_context.project_dir / "notes/coffee-brewing-log.md"
    destination = project_context.project_dir / "archive/coffee.md"
    destination.parent.mkdir()
    shutil.copyfile(source, destination)
    task = BY_ID["contract-move"]
    assert not grade_task(task, project_context)[0]
    source.unlink()
    assert grade_task(task, project_context)[0]
    note = frontmatter.loads(destination.read_text(encoding="utf-8"))
    note["title"] = "Wrong title"
    destination.write_text(frontmatter.dumps(note))
    assert not grade_task(task, project_context)[0]


def test_delete_only_requested_note(project_context: GradingContext):
    (project_context.project_dir / "notes/coffee-brewing-log.md").unlink()
    task = BY_ID["contract-delete"]
    assert grade_task(task, project_context)[0]
    (project_context.project_dir / EDIT_PATH).unlink()
    assert not grade_task(task, project_context)[0]


def test_stale_guard_requires_actual_conflict_and_unchanged_files(project_context: GradingContext):
    task = BY_ID["contract-stale-checksum"]
    assert not grade_task(task, project_context)[0]
    project_context.turn_records = [
        TurnRecord(
            turn_index=0,
            kind="tool",
            tool_name="bm_edit",
            is_error=True,
            error_excerpt="INVALID_ARGUMENTS: content required",
        )
    ]
    assert not grade_task(task, project_context)[0]
    project_context.turn_records = [
        TurnRecord(
            turn_index=0,
            kind="tool",
            tool_name="bm_edit",
            is_error=True,
            error_excerpt="REVISION_CONFLICT: note changed since the supplied revision",
        )
    ]
    assert grade_task(task, project_context)[0]
    with (project_context.project_dir / EDIT_PATH).open("a") as stream:
        stream.write("\nUnintended edit\n")
    assert not grade_task(task, project_context)[0]
