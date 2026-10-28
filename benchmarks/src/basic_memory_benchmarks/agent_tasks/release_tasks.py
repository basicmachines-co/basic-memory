"""Shared user goals for the v0.24.1 versus 1.0 release comparison.

Prompts name documents and desired outcomes, never version-specific tool names.
The original skill tasks remain intact; these isolate changed tool contracts.
"""

from basic_memory_benchmarks.agent_tasks.spec import (
    AgentTaskSpec,
    AnswerContains,
    AnswerSetEquals,
    FrontmatterEquals,
    NewNoteUnder,
    NoteCountDelta,
    NotesUntouched,
    ObservationLines,
    ToolCalled,
)

READ_TARGETS = (
    ("title", "OAuth Token Design"),
    ("path", "architecture/oauth-token-design.md"),
    ("permalink", "architecture/oauth-token-design"),
    ("memory-url", "memory://architecture/oauth-token-design"),
)

RELEASE_TASKS: tuple[AgentTaskSpec, ...] = (
    *(
        AgentTaskSpec(
            id=f"contract-read-{kind}",
            skill="release-contracts",
            source="docs/1.0.md: bm_read identifiers",
            prompt=(
                f"Read the note identified by this {kind}: {target}. "
                "Quote its security observation about refresh token reuse exactly."
            ),
            graders=(
                AnswerContains(needle="Refresh token reuse triggers whole-family revocation"),
                ToolCalled(name_pattern=r"bm_read|read_note|read_content|cat", required=True),
            ),
        )
        for kind, target in READ_TARGETS
    ),
    AgentTaskSpec(
        id="contract-write-exact-path",
        skill="release-contracts",
        source="docs/1.0.md: bm_write exact path",
        prompt=(
            "Create exactly one note at release/rc-check.md, with title 'Release Candidate Check', "
            "type 'note', and status 'draft' in frontmatter. Its body must contain the observation "
            "'- [verification] Compare the release candidate with the stable baseline'. "
            "Keep the requested filename even though it differs from the title. "
            "Do not change any existing note."
        ),
        graders=(
            NoteCountDelta(delta=1),
            NewNoteUnder(prefix="release/"),
            FrontmatterEquals(
                path="release/rc-check.md", key="title", value="Release Candidate Check"
            ),
            FrontmatterEquals(path="release/rc-check.md", key="type", value="note"),
            FrontmatterEquals(path="release/rc-check.md", key="status", value="draft"),
            ObservationLines(
                path="release/rc-check.md",
                pattern=r"^- \[verification\] Compare the release candidate with the stable baseline$",
                min_count=1,
            ),
            NotesUntouched(),
        ),
    ),
    AgentTaskSpec(
        id="contract-list-folder",
        skill="release-contracts",
        source="docs/1.0.md: bm_list folder",
        prompt=(
            "List the Markdown files directly in notes/, without reading their contents. "
            'Answer with project-relative paths including the .md extension as {"paths": [...]}.'
        ),
        graders=(
            AnswerSetEquals(
                key="paths",
                gold=frozenset(
                    {
                        "notes/coffee-brewing-log.md",
                        "notes/feature-x.md",
                        "notes/kubernetes-migration.md",
                        "notes/oauth-rollout-notes.md",
                        "notes/postgres-vacuum-notes.md",
                        "notes/rate-limiter.md",
                        "notes/redis-cache-tuning.md",
                        "notes/redis-quota-store.md",
                    }
                ),
            ),
            ToolCalled(name_pattern=r"bm_list|list_directory|ls", required=True),
        ),
    ),
)
