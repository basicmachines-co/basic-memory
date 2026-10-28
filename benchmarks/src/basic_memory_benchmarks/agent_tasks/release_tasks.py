"""Shared user goals for the v0.24.1 versus 1.0 release comparison.

Prompts name documents and desired outcomes, never version-specific tool names.
The original skill tasks remain intact; these isolate changed tool contracts.
"""

from basic_memory_benchmarks.agent_tasks.spec import (
    AgentTaskSpec,
    AnswerContains,
    AnswerSetEquals,
    FrontmatterEquals,
    MarkerPresent,
    MarkerAbsent,
    AnswerMatches,
    ProjectIdUsed,
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
                ToolCalled(
                    name_pattern=r"^(bm_read|read_note|read_content|cat)$",
                    required=True,
                    outcome="success",
                ),
            ),
        )
        for kind, target in READ_TARGETS
    ),
    AgentTaskSpec(
        id="contract-read-long-section",
        skill="release-contracts",
        source="docs/1.0.md: bounded bm_read",
        prompt=(
            "Read only the Rollback checklist section of architecture/oauth-token-design.md. "
            "Report its exact tracking token, without tokens from archived validation or forward planning."
        ),
        graders=(
            MarkerPresent(marker="RC-ROLLBACK-CURRENT-7c2a"),
            MarkerAbsent(marker="RC-ARCHIVED-2c6b"),
            MarkerAbsent(marker="RC-FUTURE-8a1d"),
            ToolCalled(
                name_pattern=r"^(bm_read|read_note|read_content|cat)$",
                required=True,
                outcome="success",
            ),
        ),
    ),
    AgentTaskSpec(
        id="contract-read-first-lines",
        skill="release-contracts",
        source="docs/1.0.md: end_line without start_line",
        prompt=(
            "Read the first five physical lines of architecture/oauth-token-design.md, including "
            'frontmatter, and report its status as {"status": "..."}. Avoid reading the full note.'
        ),
        graders=(
            AnswerMatches(pattern=r'"status"\s*:\s*"draft"'),
            ToolCalled(
                name_pattern=r"^(bm_read|read_note|read_content|cat)$",
                required=True,
                outcome="success",
            ),
        ),
    ),
    AgentTaskSpec(
        id="contract-project-id",
        skill="release-contracts",
        source="docs/1.0.md: external project ID scope",
        prompt=(
            "Discover this project's external ID, then read architecture/oauth-token-design.md "
            "with that ID, rather than its name, in the read call's project scope argument. "
            "Quote the security observation about refresh token reuse exactly. Do not modify notes."
        ),
        graders=(
            AnswerContains(needle="Refresh token reuse triggers whole-family revocation"),
            ProjectIdUsed(name_pattern=r"^(bm_read|read_note|read_content|cat)$"),
            NotesUntouched(),
            NoteCountDelta(delta=0),
        ),
    ),
    AgentTaskSpec(
        id="contract-find-filename",
        skill="release-contracts",
        source="docs/1.0.md: recursive bm_find",
        prompt=(
            "Find every Markdown file whose filename contains oauth, recursively across this project. "
            'Report project-relative paths including .md as {"paths": [...]}.'
        ),
        graders=(
            AnswerSetEquals(
                key="paths",
                gold=frozenset(
                    {
                        "architecture/oauth-token-design.md",
                        "architecture/oauth-migration-plan.md",
                        "notes/oauth-rollout-notes.md",
                    }
                ),
            ),
        ),
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
            ToolCalled(
                name_pattern=r"^(bm_list|list_directory|ls)$", required=True, outcome="success"
            ),
        ),
    ),
)
