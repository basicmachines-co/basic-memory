"""The shipped agent tasks, derived from the Basic Memory skills.

Each task is declarative data (see ``spec.py``); grading is deterministic for
all twelve (the judge seam exists but no shipped task uses it, so judge tokens
never touch the headline). Gold values are pinned to the seed corpus in
``benchmarks/datasets/agent-tasks/corpus`` and enforced by the fixture
integrity tests.
"""

from __future__ import annotations

from basic_memory_benchmarks.agent_tasks.spec import (
    NEW_FILE_SELECTOR,
    AgentTaskSpec,
    AnswerMatches,
    AnswerSetEquals,
    FrontmatterEquals,
    FrontmatterListLen,
    FrontmatterMatches,
    MarkerAbsent,
    MarkerPresent,
    NewNoteUnder,
    NoteCountDelta,
    NotesUntouched,
    ObservationLines,
    RelationResolves,
    ToolCalled,
)

ORPHAN_PERMALINKS = frozenset(
    {
        "notes/redis-cache-tuning",
        "notes/postgres-vacuum-notes",
        "notes/coffee-brewing-log",
    }
)

RECENT_TITLES = frozenset(
    {
        "2026-08-27 SPEC-9 Session 2",
        "2026-08-27 Infra Notes",
        "Migrate CI to uv",
    }
)

ACTIVE_HIGH_PRIORITY_PERMALINKS = frozenset(
    {"specs/spec-9-search-rework", "notes/kubernetes-migration"}
)

HIGH_CONFIDENCE_PERMALINKS = frozenset(
    {"specs/spec-9-search-rework", "architecture/oauth-token-design"}
)

PENDING_OAUTH_REVIEW_PERMALINKS = frozenset({"architecture/oauth-token-design"})

TASKS: tuple[AgentTaskSpec, ...] = (
    AgentTaskSpec(
        id="continue-spec9",
        skill="memory-continue",
        source="skills/memory-continue/SKILL.md",
        prompt=(
            "Where were we on SPEC-9? List every open item and the next step, quoting "
            "each item's text verbatim (including any tracking tokens)."
        ),
        graders=(
            MarkerPresent(marker="BMEVAL-s9open1-4c1a"),
            MarkerPresent(marker="BMEVAL-s9open2-9d2b"),
            MarkerPresent(marker="BMEVAL-s9open3-77e0"),
            MarkerPresent(marker="BMEVAL-s9next-b881"),
            MarkerAbsent(marker="BMEVAL-s8open-1f2e"),
            MarkerAbsent(marker="BMEVAL-s8next-c9a4"),
            ToolCalled(name_pattern=r"search_notes|build_context|read_note|grep|cat"),
        ),
    ),
    AgentTaskSpec(
        id="continue-recent-window",
        skill="memory-continue",
        source="skills/memory-continue/SKILL.md",
        prompt=(
            "What did we work on in the last three days? Answer with the note titles "
            'as {"titles": [...]}.'
        ),
        graders=(
            AnswerSetEquals(key="titles", gold=RECENT_TITLES),
            MarkerAbsent(marker="BMEVAL-oldnote1-2b3c"),
            MarkerAbsent(marker="BMEVAL-oldnote2-8e7d"),
        ),
    ),
    AgentTaskSpec(
        id="continue-two-hop",
        skill="memory-continue",
        source="skills/memory-continue/SKILL.md",
        prompt=(
            "Continue work on Feature X: what constraint does its dependency chain "
            "impose on the storage layer? Quote the constraint's tracking token "
            "verbatim."
        ),
        graders=(MarkerPresent(marker="BMEVAL-quota-fa11"),),
    ),
    AgentTaskSpec(
        id="curate-orphans",
        skill="memory-curate",
        source="skills/memory-curate/SKILL.md",
        prompt=(
            "Find all orphan notes in this project (notes with no relations to or "
            'from any other note). Answer with {"permalinks": [...]}.'
        ),
        graders=(AnswerSetEquals(key="permalinks", gold=ORPHAN_PERMALINKS),),
    ),
    AgentTaskSpec(
        id="curate-connect",
        skill="memory-curate",
        source="skills/memory-curate/SKILL.md",
        prompt=(
            "The note 'Redis Cache Tuning' is unlinked. Connect it to the single most "
            "relevant existing note by adding one typed relation to its Relations "
            "section. Do not modify any other note and do not create new notes."
        ),
        graders=(
            RelationResolves(
                source_permalink="notes/redis-cache-tuning",
                targets=frozenset({"architecture/redis-cache-architecture"}),
            ),
            NotesUntouched(except_globs=("notes/redis-cache-tuning.md",)),
            NoteCountDelta(delta=0),
        ),
    ),
    AgentTaskSpec(
        id="meta-status-priority",
        skill="memory-metadata-search",
        source="skills/memory-metadata-search/SKILL.md",
        prompt=(
            "Using the notes' frontmatter metadata, which notes are active (status) "
            "with high or critical priority? Answer with their permalinks as "
            '{"permalinks": [...]}.'
        ),
        graders=(AnswerSetEquals(key="permalinks", gold=ACTIVE_HIGH_PRIORITY_PERMALINKS),),
    ),
    AgentTaskSpec(
        id="meta-confidence-gt",
        skill="memory-metadata-search",
        source="skills/memory-metadata-search/SKILL.md",
        prompt=(
            "Which notes have a frontmatter confidence strictly greater than 0.7? "
            'Answer with their permalinks as {"permalinks": [...]}.'
        ),
        graders=(AnswerSetEquals(key="permalinks", gold=HIGH_CONFIDENCE_PERMALINKS),),
    ),
    AgentTaskSpec(
        id="meta-nested-review",
        skill="memory-metadata-search",
        source="skills/memory-metadata-search/SKILL.md",
        prompt=(
            "Find the draft notes about OAuth whose review status is still pending "
            '(a nested frontmatter field). Answer with {"permalinks": [...]}.'
        ),
        graders=(AnswerSetEquals(key="permalinks", gold=PENDING_OAUTH_REVIEW_PERMALINKS),),
    ),
    AgentTaskSpec(
        id="tasks-create",
        skill="memory-tasks",
        source="skills/memory-tasks/SKILL.md",
        prompt=(
            "Track this work: migrate the docs build to uv — (1) audit pip usage, "
            "(2) rewrite the CI workflow, (3) verify the deploy. Create a task note "
            "in tasks/ following this project's Task schema (see the schemas/ "
            "directory), starting at the first step."
        ),
        graders=(
            NoteCountDelta(delta=1),
            NewNoteUnder(prefix="tasks/"),
            FrontmatterEquals(path=NEW_FILE_SELECTOR, key="type", value="task"),
            FrontmatterEquals(path=NEW_FILE_SELECTOR, key="status", value="active"),
            FrontmatterListLen(path=NEW_FILE_SELECTOR, key="steps", length=3),
            FrontmatterEquals(path=NEW_FILE_SELECTOR, key="current_step", value=1),
            ObservationLines(path=NEW_FILE_SELECTOR, pattern=r"^- \[[a-z-]+\] ", min_count=1),
        ),
    ),
    AgentTaskSpec(
        id="tasks-resume",
        skill="memory-tasks",
        source="skills/memory-tasks/SKILL.md",
        prompt=(
            "You've lost all context. Find your active tasks and report, for each "
            "one, its context field and the step it is currently on — quote both "
            "verbatim (including any tracking tokens). Skip tasks that are not "
            "active."
        ),
        graders=(
            MarkerPresent(marker="BMEVAL-ci-ctx-90bf"),
            MarkerPresent(marker="BMEVAL-ci-step2-51aa"),
            MarkerPresent(marker="BMEVAL-rsi-ctx-33cd"),
            MarkerPresent(marker="BMEVAL-rsi-step1-6d21"),
            MarkerAbsent(marker="BMEVAL-done-ab12"),
            MarkerAbsent(marker="BMEVAL-blocked-cd34"),
        ),
    ),
    AgentTaskSpec(
        id="tasks-complete",
        skill="memory-tasks",
        source="skills/memory-tasks/SKILL.md",
        prompt=(
            "Mark the task 'Migrate CI to uv' as done, recording today's completion "
            "date in its frontmatter. Do not modify any other note."
        ),
        graders=(
            FrontmatterEquals(path="tasks/migrate-ci-to-uv.md", key="status", value="done"),
            FrontmatterMatches(
                path="tasks/migrate-ci-to-uv.md",
                key="completed",
                pattern=r"^\d{4}-\d{2}-\d{2}",
            ),
            NotesUntouched(except_globs=("tasks/migrate-ci-to-uv.md",)),
            NoteCountDelta(delta=0),
        ),
    ),
    AgentTaskSpec(
        id="man-chain",
        skill="manual",
        source="SPEC-47 manual chain (search manual -> read man page section)",
        prompt=(
            "Manual pages for the memory tools live in man3/. Which page documents "
            "the tool for incremental note edits? Read that page and report the "
            "exact token from its EXAMPLES section that starts with BMEVAL-."
        ),
        graders=(
            MarkerPresent(marker="BMEVAL-man-edit-7f3e"),
            # Both tool spellings are substantively correct: the man page is
            # edit-note(3) while the MCP tool itself is edit_note.
            AnswerMatches(pattern=r"edit[-_]note"),
            ToolCalled(name_pattern=r"read_note|read_content|search_notes|cat|man|grep"),
        ),
    ),
)

TASKS_BY_ID: dict[str, AgentTaskSpec] = {task.id: task for task in TASKS}


def select_tasks(task_ids: list[str]) -> list[AgentTaskSpec]:
    """Resolve a task-id filter (empty = all), rejecting unknown ids loudly."""
    if not task_ids:
        return sorted(TASKS, key=lambda task: task.id)
    unknown = [task_id for task_id in task_ids if task_id not in TASKS_BY_ID]
    if unknown:
        raise ValueError(f"Unknown task ids: {unknown}. Known: {sorted(TASKS_BY_ID)}")
    # dict.fromkeys dedupes while preserving input order; a duplicated id must
    # not create two same-named per-task projects.
    unique_ids = dict.fromkeys(task_ids)
    return sorted((TASKS_BY_ID[task_id] for task_id in unique_ids), key=lambda task: task.id)
