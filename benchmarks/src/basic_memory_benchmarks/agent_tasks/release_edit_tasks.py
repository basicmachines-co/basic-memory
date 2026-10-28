"""State-graded edit, move, delete and guarded-write release tasks."""

from dataclasses import dataclass

from basic_memory_benchmarks.agent_tasks.spec import (
    AgentTaskSpec,
    BodyLinesEqual,
    FrontmatterEquals,
    FrontmatterUnchanged,
    NoteCountDelta,
    NotesUntouched,
    ToolErrorMatches,
)

EDIT_PATH = "notes/postgres-vacuum-notes.md"
VACUUM_BODY = (
    "# Postgres Vacuum Notes",
    'The old tracker labelled this "priority: high" in its free-text field, but the',
    "frontmatter above records the real triage priority after we measured the bloat.",
    "## Observations",
    "- [note] Autovacuum stalls on the events table during bulk imports",
    "- [note] A manual VACUUM ANALYZE after imports keeps bloat under 10 percent",
)
EDIT_LINE = "- [release] Verify vacuum settings before the release"


@dataclass(frozen=True)
class EditGoal:
    name: str
    instruction: str
    body_lines: tuple[str, ...]
    status: str = "active"


EDIT_GOALS = (
    EditGoal(
        "append",
        f"Append exactly this line to the end of the body: {EDIT_LINE}",
        (*VACUUM_BODY, EDIT_LINE),
    ),
    EditGoal(
        "prepend",
        f"Insert exactly this line at the start of the body, after frontmatter and before the title heading: {EDIT_LINE}",
        (EDIT_LINE, *VACUUM_BODY),
    ),
    EditGoal(
        "find-replace",
        f"Replace exactly this line: {VACUUM_BODY[4]}\nwith: {EDIT_LINE}",
        (*VACUUM_BODY[:4], EDIT_LINE, VACUUM_BODY[5]),
    ),
    EditGoal(
        "replace-section",
        f"Replace the contents under the Observations heading with just this line, retaining the heading: {EDIT_LINE}",
        (*VACUUM_BODY[:4], EDIT_LINE),
    ),
    EditGoal(
        "insert-before-section",
        f"Insert exactly this line immediately before the Observations heading: {EDIT_LINE}",
        (*VACUUM_BODY[:3], EDIT_LINE, *VACUUM_BODY[3:]),
    ),
    EditGoal(
        "insert-after-section",
        f"Insert exactly this line immediately after the Observations heading and before its existing observations: {EDIT_LINE}",
        (*VACUUM_BODY[:4], EDIT_LINE, *VACUUM_BODY[4:]),
    ),
    EditGoal(
        "set-metadata",
        "Set its frontmatter status to draft without changing the body or any other frontmatter field.",
        VACUUM_BODY,
        "draft",
    ),
)

COFFEE_BODY = (
    "# Coffee Brewing Log",
    "Personal brewing experiments; nothing to do with the codebase.",
    "## Observations",
    "- [method] The 1:16 pour-over ratio beat last month's 1:15 batch BMEVAL-oldnote2-8e7d",
    "- [preference] Ethiopian naturals taste best around day 12 off roast",
)

EDIT_TASKS: tuple[AgentTaskSpec, ...] = (
    *(
        AgentTaskSpec(
            id=f"contract-edit-{goal.name}",
            skill="release-contracts",
            source="docs/1.0.md: bm_edit",
            prompt=(
                f"Edit {EDIT_PATH}. {goal.instruction} Preserve every other body line "
                "and frontmatter field, except the status change if requested. Do not modify any other note or create notes."
            ),
            graders=(
                BodyLinesEqual(path=EDIT_PATH, lines=goal.body_lines),
                FrontmatterEquals(path=EDIT_PATH, key="status", value=goal.status),
                FrontmatterUnchanged(
                    path=EDIT_PATH, except_keys=("status",) if goal.name == "set-metadata" else ()
                ),
                NotesUntouched(except_globs=(EDIT_PATH,)),
                NoteCountDelta(delta=0),
            ),
        )
        for goal in EDIT_GOALS
    ),
    AgentTaskSpec(
        id="contract-move",
        skill="release-contracts",
        source="docs/1.0.md: bm_move",
        prompt="Move notes/coffee-brewing-log.md to archive/coffee.md. Preserve the title, other frontmatter and body; let its permalink follow the tool's normal move behavior. Do not change any other note or leave a copy at the old path.",
        graders=(
            BodyLinesEqual(path="archive/coffee.md", lines=COFFEE_BODY),
            FrontmatterUnchanged(
                path="archive/coffee.md",
                baseline_path="notes/coffee-brewing-log.md",
                except_keys=("permalink",),
            ),
            NotesUntouched(except_globs=("notes/coffee-brewing-log.md",)),
            NoteCountDelta(delta=0),
        ),
    ),
    AgentTaskSpec(
        id="contract-delete",
        skill="release-contracts",
        source="docs/1.0.md: bm_delete",
        prompt="Delete only notes/coffee-brewing-log.md. Keep every other note unchanged and do not create any notes.",
        graders=(
            NotesUntouched(except_globs=("notes/coffee-brewing-log.md",)),
            NoteCountDelta(delta=-1),
        ),
    ),
    AgentTaskSpec(
        id="contract-stale-checksum",
        skill="release-contracts",
        source="docs/1.0.md: guarded writes",
        prompt=(
            f"Test the revision guard on {EDIT_PATH}. Attempt to append '- [release] This guarded change must not land' using this deliberately stale expected checksum: "
            + "0" * 64
            + ". Report the refusal. Do not retry without the supplied checksum, and do not change any note."
        ),
        graders=(
            ToolErrorMatches(
                name_pattern=r"^(bm_edit|edit_note|bm_write|write_note)$",
                error_pattern=r"conflict|changed|mismatch|stale",
            ),
            NotesUntouched(),
            NoteCountDelta(delta=0),
        ),
    ),
)
