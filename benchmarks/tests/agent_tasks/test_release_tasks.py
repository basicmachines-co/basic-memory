"""Release-task gold values and exact-path write failure controls."""

from pathlib import Path

from basic_memory_benchmarks.agent_tasks.grading import GradingContext, grade_task
from basic_memory_benchmarks.agent_tasks.release_tasks import RELEASE_TASKS, READ_TARGETS
from basic_memory_benchmarks.agent_tasks.spec import AnswerContains, AnswerSetEquals
from basic_memory_benchmarks.agent_tasks.models import TurnRecord

CORPUS = Path(__file__).parents[2] / "benchmarks/datasets/agent-tasks/corpus"


def test_release_read_answers_are_grounded_in_same_document():
    text = (CORPUS / "architecture/oauth-token-design.md").read_text()
    ids = {f"contract-read-{kind}" for kind, _ in READ_TARGETS}
    tasks = [task for task in RELEASE_TASKS if task.id in ids]
    assert len(tasks) == 4
    for task in tasks:
        for grader in task.graders:
            if isinstance(grader, AnswerContains):
                assert grader.needle in text
                assert grader.needle not in task.prompt


def test_release_folder_gold_matches_fixture():
    task = next(task for task in RELEASE_TASKS if task.id == "contract-list-folder")
    gold = next(grader.gold for grader in task.graders if isinstance(grader, AnswerSetEquals))
    assert gold == frozenset(f"notes/{path.name}" for path in (CORPUS / "notes").glob("*.md"))


def test_long_read_target_is_beyond_shared_result_cap():
    text = (CORPUS / "architecture/oauth-token-design.md").read_text()
    assert text.index("RC-ROLLBACK-CURRENT-7c2a") > 8000
    assert text.index("RC-ARCHIVED-2c6b") < 8000
    assert text.index("RC-FUTURE-8a1d") > text.index("RC-ROLLBACK-CURRENT-7c2a")


def test_filename_find_gold_matches_fixture():
    task = next(task for task in RELEASE_TASKS if task.id == "contract-find-filename")
    gold = next(grader.gold for grader in task.graders if isinstance(grader, AnswerSetEquals))
    assert gold == frozenset(str(path.relative_to(CORPUS)) for path in CORPUS.rglob("*oauth*.md"))


def test_refused_read_cannot_pass_with_a_guessed_answer(tmp_path: Path):
    task = next(task for task in RELEASE_TASKS if task.id == "contract-read-title")
    ctx = GradingContext(
        final_answer="Refresh token reuse triggers whole-family revocation",
        project_dir=tmp_path,
        baseline={},
        db_path=tmp_path / "unused.db",
        project_name="test",
        turn_records=[TurnRecord(turn_index=0, kind="tool", tool_name="bm_read", is_error=True)],
    )
    assert not grade_task(task, ctx)[0]
    ctx.turn_records = [TurnRecord(turn_index=0, kind="tool", tool_name="bm_read", is_error=False)]
    assert grade_task(task, ctx)[0]


def test_write_requires_exact_filename_and_preserves_existing_notes(tmp_path: Path):
    task = next(task for task in RELEASE_TASKS if task.id == "contract-write-exact-path")
    existing = tmp_path / "existing.md"
    existing.write_text("An existing note\n")
    baseline = {existing.name: existing.read_text()}
    release = tmp_path / "release"
    release.mkdir()
    content = (
        "---\ntitle: Release Candidate Check\ntype: note\nstatus: draft\n---\n\n"
        "- [verification] Compare the release candidate with the stable baseline\n"
    )
    written = release / "rc-check.md"
    written.write_text(content)
    ctx = GradingContext(
        final_answer="done",
        project_dir=tmp_path,
        baseline=baseline,
        db_path=tmp_path / "unused.db",
        project_name="test",
    )
    assert grade_task(task, ctx)[0]
    written.rename(release / "release-candidate-check.md")
    assert not grade_task(task, ctx)[0]
    (release / "release-candidate-check.md").rename(written)
    existing.write_text("Unexpected modification\n")
    assert not grade_task(task, ctx)[0]
