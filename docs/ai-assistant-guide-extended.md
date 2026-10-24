# AI Assistant Guide for Basic Memory - Extended Edition

Basic Memory is a persistent Markdown knowledge base shared by humans and their AI
assistants. This guide describes the 1.0 MCP surface. The installed tool schemas and
`bm_man` pages are authoritative for exact signatures. CLI commands retain their
shell names; host integrations may expose their own native wrappers.

## Understanding Basic Memory

A **project** is the isolation boundary for notes, graph state, and search. A **note**
is the canonical Markdown document; its indexed **entity** connects observations
and relations to search and graph traversal. Local projects keep files on disk;
Cloud projects provide hosted storage. Indexing makes edits available for retrieval.

Use memory for decisions, discoveries, plans, preferences, and reference material
that should survive a conversation. Search before creating, read before editing,
and preserve the user's project choice across calls. Follow the user's existing
capture authorization; ask before saving when no authorization exists.

## Project Management

Discover projects when the route is unknown or the user asks for their projects:

```python
await bm_project(command="list", output_format="json")
```

Pass a project name, `workspace/project`, or a returned `external_id` UUID through
one parameter: `project`. Use a qualified name or UUID when names collide across
Cloud workspaces. A project-qualified file path also routes a call; an explicit
`project` must agree with that prefix. Do not assume that a previous call selected
a project for later calls. Note-addressing tools can refuse unqualified identifiers
when several projects are available, even if a default is configured.

```python
await bm_project(command="recent", since="7d", project="main")
await bm_search(query="authentication", project="main")
await bm_read(identifier="specs/authentication.md", project="main")
```

For `command="recent"`, omitting `project` uses the default project; only when no
default exists does it aggregate projects. No recent activity does not establish
that a project is empty. Check indexing guidance and search before drawing that
conclusion. The CLI `bm status` reports local indexing status; there is no MCP
`sync_status` tool.

Create a project when requested:

```python
await bm_project(
    command="create",
    name="research",
    path="/Users/name/Documents/research",
    set_default=False,
)
```

Project deletion is a separate `bm_project_delete` tool, available only when enabled
by the server. Read its manual before using it; Cloud deletion removes note files.

## Knowledge Graph Fundamentals

Write categorized observations and typed WikiLinks where they improve recall:

```markdown
---
title: Authentication Design
type: spec
tags: [security, api]
status: draft
---

# Authentication Design

## Context
The API needs a consistent authentication policy.

## Observations
- [decision] Use short-lived access tokens #security
- [requirement] Support token revocation
- [question] Which clients need offline access?

## Relations
- implements [[Security Requirements]]
- requires [[User Database]]
```

Observations use `- [category] content`; relations use `- relation_type [[Target]]`.
Prefer specific facts, meaningful categories, and exact target titles or identifiers.
Forward references can resolve when their targets are later indexed. Do not invent
facts or add observations and links merely to meet a quota.

## Writing Knowledge

Create with an explicit file path, or with `title` and `directory`:

```python
await bm_write(
    identifier="decisions/authentication.md",
    title="Authentication Decision",
    content="# Authentication Decision\n\n## Observations\n- [decision] Use short-lived tokens",
    note_type="decision",
    metadata={"status": "open"},
    tags=["auth", "security"],
    overwrite=False,
    project="main",
)
```

```python
await bm_write(
    title="API Review",
    directory="meetings",
    content="# API Review\n\n## Decisions\nReview the token lifecycle next week.",
    project="main",
)
```

`overwrite=False` explicitly protects against replacing an existing note even when
the user's write default permits overwrites. Content frontmatter can supply note
metadata. The tool does not add an H1 for you. Search for related notes first; a
similar-note advisory after creation is a suggestion to inspect, not proof of a
duplicate.

For a full replacement, use the exact returned file path and checksum. A title may
resolve to a different filename than the one a new title-based write would derive.

```python
note = await bm_read(identifier="decisions/authentication.md", output_format="json", project="main")
await bm_write(
    identifier=note["file_path"],
    content="# Authentication Decision\n\nUpdated rationale.",
    overwrite=True,
    expected_checksum=note["checksum"],
    project="main",
)
```

## Reading and Navigation

`bm_read` resolves one file by path, permalink, exact title, external ID, or memory
URL. Use returned identifiers rather than guessing paths or normalizing titles.
Missing notes produce errors; wildcard discovery belongs in `bm_find`, `bm_search`,
or `bm_context`.

```python
await bm_read(identifier="specs/authentication.md", project="main")
await bm_read(identifier="research/specs/api-design.md")
await bm_read(identifier="specs/authentication.md", section="Decisions", project="main")
await bm_read(identifier="specs/authentication.md", start_line=20, end_line=60, project="main")
await bm_read(identifier="specs/authentication.md", max_tokens=1000, project="main")
```

Long notes use sections, line ranges, or token budgets, not page numbers. Line
numbers include frontmatter. Follow the returned continuation information when a
read is truncated. Use `output_format="json"` when code needs response fields;
ordinary reads default to text. `include_content=False` returns metadata and the
checksum without the body. Graph traversal belongs in `bm_context`, not assumed
`relations` fields on a `bm_read` response.

The same tool reads non-Markdown files; images return as images and other binary
files as documents. Reading content does not open a host UI or guarantee a rendered
artifact.

```python
await bm_read(identifier="config/settings.json", project="main")
await bm_read(identifier="diagrams/architecture.png", project="main")
await bm_list(path="specs", glob="*.md", project="main")
await bm_find(path="specs", name="*auth*", depth=3, project="main")
```

`bm_list` lists one level. With `/` and no project constraint it lists project mount
points. `bm_find` searches recursively, up to ten levels. Both paginate; follow
`has_more` rather than assuming one page is complete.

## Search and Discovery

Use `bm_search` for ranked text or meaning search, and `bm_find` for folder-scoped
frontmatter inventories. Search defaults to hybrid when semantic search is
available; choose `mode="text"` for keyword matching.

```python
await bm_search(query="JWT authentication", mode="text", project="main")
await bm_search(query="user login security", mode="semantic", project="main")
await bm_search(query="API design", mode="hybrid", project="main")
await bm_search(query="API Design", mode="title", project="main")
await bm_search(query="specs/*", mode="permalink", project="main")
await bm_search(
    query="authentication",
    note_types=["spec", "decision"],
    filters={"status": "draft", "tags": ["security"]},
    since="30d",
    project="main",
)
await bm_search(query="performance", items=["observation"], categories=["problem"], project="main")
```

`items` selects `note`, `observation`, and/or `relation`; `note_types` selects
frontmatter types. `categories` selects observation categories. Put tags and status
inside `filters`. See [Metadata Search](metadata-search.md) for operators.

```python
await bm_find(
    path="specs",
    filters={"status": "draft", "priority": {"$in": ["high", "critical"]}},
    fields=["title", "priority"],
    project="main",
)
await bm_search(query="retry", mode="text", lines=2, max_matches=5, project="main")
await bm_search(query="authentication", projects=["main", "research"], output_format="json")
```

`bm_find` cannot combine frontmatter filters with `name` or a non-default depth;
use `path` to scope the inventory. `bm_search` accepts one scope among `identifier`,
`path`, a memory URL query, `projects`, or `all_projects`. Line windows report
literal matches within indexed candidates, not an exhaustive filesystem scan.

Search/list/find pages accept 1–100 results. Inspect `has_more` and advance `page`.
Read selected results with `bm_read`; snippets and compact results are not complete
notes. Inspect the installed JSON response instead of assuming an undocumented shape.

## Building Context

Follow relations from a known note or pattern:

```python
await bm_context(
    identifier="specs/authentication.md",
    depth=2,
    since="30d",
    max_related=20,
    compact=True,
    project="main",
)
```

`depth` is 1–5. `since` defaults to `7d`, so omission does not mean all history.
Choose an explicit older date when historical context matters. Context defaults to
JSON, accepts up to 50 primary results per page, and uses `max_related` to cap related
results. Compact mode omits bodies and observations while preserving graph shape;
read the selected notes before quoting their contents.

For continuity, search for the thread, read its latest checkpoint, follow relevant
relations, and verify unresolved claims against current evidence.

## Recording Conversations

Capture a coherent account of what matters: the problem, decisions and rationale,
actual results, unresolved questions, and next action. Link to earlier discussions
instead of copying them. Distinguish verified facts from proposals or estimates.

Useful note types include decisions, meetings, research, tasks, and session handoffs.
Use a readable title, an H1, relevant frontmatter, and sections suited to the work.
Confirm the write using its returned identifier; never invent a web-app link.

## Editing Notes

`bm_edit` changes existing notes. Missing notes are errors; create them deliberately
with `bm_write`. Supported commands include append, prepend, find_replace,
replace_section, insert_before_section, insert_after_section, and set_metadata.

```python
await bm_edit(
    identifier="specs/authentication.md",
    command="append",
    content="\n- [requirement] Document refresh-token rotation",
    project="main",
)
await bm_edit(
    identifier="specs/authentication.md",
    command="find_replace",
    find_text="draft policy",
    content="approved policy",
    expected_replacements=1,
    project="main",
)
await bm_edit(
    identifier="specs/authentication.md",
    command="replace_section",
    section="Decisions",
    content="## Decisions\n\nUse rotating refresh tokens.",
    project="main",
)
await bm_edit(
    identifier="specs/authentication.md",
    command="set_metadata",
    metadata={"status": "approved"},
    project="main",
)
```

Read before editing and pass `expected_checksum` when the change depends on the
revision you read. For repeated headings use a section path (`Auth/Decisions`) or an
occurrence (`Decisions[1]`). `expected_replacements` must match the actual count;
there is no `replace_all` parameter. Metadata updates preserve other fields and
cannot remove keys with null or change title/permalink identity.

## Moving and Organizing

```python
await bm_move(
    identifier="specs/authentication.md",
    destination="archive/authentication.md",
    project="main",
)
```

A move changes the file location; do not assume it rewrites the title or permalink.
Reuse the result's identifiers for later operations. Read the manual for directory
moves and permalink behavior. To copy knowledge to another project, read the source
and deliberately create a new note in the destination project.

Use `bm_delete` only for notes the user authorized removing. Move useful historical
material into an archive when retaining provenance is appropriate.

## Error Handling

Failures are MCP tool errors, not successful empty results. With JSON output, inspect
the error code and remediation. Common cases include:

- `NOT_FOUND`: search or list to locate the right identifier; do not pretend the read succeeded.
- `ALREADY_EXISTS`: read the existing note and choose an edit or an explicit overwrite.
- Revision conflict: re-read and reconcile before retrying with a new checksum.
- `INVALID_ARGUMENTS`: consult `bm_man` and correct the call; do not silently drop filters.
- Ambiguous route: pass a project UUID or workspace-qualified name.

An empty search is different from a failed search. Check `has_more` before calling a
result set complete. Do not retry destructive operations blindly after transport errors.

## Advanced Patterns

For a bulk update, first enumerate and review the target set, then read each note and
apply checksum-guarded changes. Preserve provenance when consolidating notes and link
related material before deleting a source. Avoid reconstructing Markdown from assumed
search-result observation/relation fields.

Use schemas to validate conventions and identify drift:

```python
await bm_schema(command="validate", identifier="specs/authentication.md", project="main")
await bm_schema(command="infer", note_type="decision", project="main")
await bm_schema(command="diff", note_type="decision", project="main")
```

Schema inference proposes structure; it does not authorize rewriting existing notes.
For knowledge visualization, gather context and create an ordinary JSON Canvas file
using filesystem access when available. Do not treat note-writing tools as arbitrary
binary-file writers.

## Tool Reference

```python
await bm_man()
await bm_man(page="bm-write(3)")
await bm_man(query="frontmatter filters")
```

The manual also lives at `memory://man` and `memory://man/bm-write(3)`. In a shell,
use `bm man bm-write`. See [Manual Pages](manual-pages.md) for its layout and
verification rules, and the [short assistant guide](../src/basic_memory/mcp/resources/ai_assistant_guide.md)
for session-start guidance.

## Best Practices

- Keep project routes explicit and reuse returned identifiers.
- Search before creating; read before editing or making factual claims.
- Use bounded reads and pagination to control context size without hiding omissions.
- Preserve Markdown, metadata, and provenance during updates.
- Write specific observations and meaningful links rather than filling quotas.
- Respect existing user authorization and confirm meaningful writes.
- Verify the installed schema when a tool call fails or a host exposes different wrappers.
