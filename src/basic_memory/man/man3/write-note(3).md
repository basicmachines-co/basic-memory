---
title: write-note(3)
type: manpage
section: 3
name: write-note
summary: create or overwrite a markdown note in the knowledge base
generated: registry
tool: write_note
verified: 0.21.6 mcp+cli
---

# write-note(3)

## NAME

**write-note** — create or overwrite a markdown note in the knowledge base

## SYNOPSIS

MCP:

```
write_note(title, content, directory, project=None, workspace=None,
           project_id=None, tags=None, note_type="note", metadata=None,
           overwrite=None, expected_checksum=None, output_format="text")
```

CLI:

```
bm tool write-note --title TITLE --folder FOLDER [--content TEXT | < stdin]
                   [--tags TAG] [--type TYPE] [--project NAME | --project-id UUID]
                   [--overwrite [--expected-checksum CHECKSUM]] [--local | --cloud]
```

## DESCRIPTION

Creates a markdown note and indexes it into the knowledge graph. The content
is parsed for semantic **observations** (`- [category] text #tag`) and
**relations** (`- relation_type [[Target]]`, plus inline `[[wikilinks]]`);
both become queryable graph edges. See [[bm-note(5)]] for the full format.

If a note with the same title and folder already exists, write-note returns a
conflict error by default. Pass `overwrite=True` (CLI: `--overwrite`) to
replace it. For incremental changes prefer [[edit-note(3)]], which appends,
prepends, or edits sections in place without rewriting the file.

Overwrite addresses the exact file path generated from the title and directory.
If that path is vacant but its retained permalink identifies a moved note,
the tool returns `NOTE_PATH_CONFLICT` with the current path. Read or edit the
moved note by its identifier, or use `overwrite=False` to create a separate note.
If a replacement note already occupies the requested path, overwrite updates
that replacement. Locked notes refuse replacement without changing their content.

Pass `expected_checksum` with `overwrite=True` (CLI: `--expected-checksum`) to
make the replacement conditional on the revision you read. If the note changed
since, or no note owns the path any more, nothing is written and the tool
returns `NOTE_REVISION_CONFLICT` with the current checksum in `currentChecksum`,
the same shape as edit_note's conflict. Without it,
`overwrite=True` replaces the note unconditionally.

## PARAMETERS

- **title** (string, required) — The title of the note; written to frontmatter and drives the permalink. No H1 is added for you: content is saved as given, so include a "# Title" heading yourself if the note should open with one.
- **content** (string, required) — Markdown content for the note, can include observations and relations. May carry its own frontmatter; a `type:` in content frontmatter takes precedence over the note_type parameter.
- **directory** (string, required) — Directory path relative to project root where the file should be saved. Use forward slashes (/) as separators. Use "/" or "" to write to project root. Examples: "notes", "projects/2025", "research/ml", "/" (root). MCP accepts the aliases folder, dir, and path; the CLI flag is --folder.
- **project** (string | null, optional, default: None) — Project name to write to. Optional. Omitting both project and project_id writes to the session's active project (the last one this session touched), and only falls back to the configured default project when there is none — so after working in another project, pass project explicitly. Use "workspace/project" to route to a project in a specific cloud workspace. A bare name that exists in multiple workspaces resolves to the default workspace, so use the qualified form (or project_id) to disambiguate. If unknown, use list_memory_projects() to discover available projects and their qualified names.
- **workspace** (string | null, optional, default: None) — Workspace slug, name, or tenant_id. When provided with `project`, routes as `workspace/project`. Cannot be combined with `project_id`.
- **project_id** (string | null, optional, default: None) — Project external_id (UUID). Prefer this over `project` when known — it routes to the exact project regardless of name collisions across cloud workspaces. Takes precedence over `project`. Get from list_memory_projects().
- **tags** (array | string | null, optional, default: None) — Tags to categorize the note. Can be a list of strings, a comma-separated string, or None. Note: If passing from external MCP clients, use a string format (e.g. "tag1,tag2,tag3")
- **note_type** (string, optional, default: "note") — Type of note to create (stored in frontmatter `type:`). Defaults to "note". Can be "guide", "report", "config", "person", etc. The CLI flag is --type. A `type:` in content frontmatter takes precedence over this parameter, and this is what schema validation keys on.
- **metadata** (object | null, optional, default: None) — Optional dict of extra frontmatter fields merged into entity_metadata. Useful for schema notes or any note that needs custom YAML frontmatter beyond title/type/tags. Nested dicts are supported. Not available from the CLI.
- **overwrite** (boolean | null, optional, default: None) — If True, replace existing note on conflict. If False, error on conflict. If None (default), consult write_note_overwrite_default config setting. Overwrite replaces the body but merges frontmatter: keys the existing note has and this write does not set (custom fields) are kept, while keys this write sets (title, type, tags, metadata, or frontmatter in content) replace the old values. Existing frontmatter that cannot be parsed is discarded rather than merged. To remove a key, rewrite it with edit_note or edit the file.
- **expected_checksum** (string | null, optional, default: None) — Optional revision precondition for overwrite=True: the checksum of the note you read (from a JSON read_note, write_note or edit_note result). The note is replaced only while it is still that revision; otherwise the tool reports a revision conflict with the current checksum and changes nothing. Omit it to replace the note unconditionally.
- **output_format** (string, optional, default: "text") — "text" returns a markdown summary. "json" returns machine-readable metadata. A refused write (note exists, revision conflict, moved target, disallowed directory) is a tool error; in JSON mode its message is the payload with action: "conflict" and an error code.

## MCP USAGE

```
write_note(
    title="Demo - Pour Over Method",
    directory="playground",
    project="manual",
    tags=["demo", "manpage-example"],
    content="...markdown with observations and [[relations]]...",
    output_format="json",
)
# → {"action": "created", "permalink": "<workspace>/manual/playground/demo-pour-over-method", ...}
```

## CLI EQUIVALENT

```
echo "# CLI Demo Note ..." | bm tool write-note \
    --title "Demo - CLI stdin" --folder playground --project manual
# → {"action": "created", "permalink": "manual/playground/demo-cli-stdin", ...}
```

## EXAMPLES

Create, collide, replace (all run against this project's playground/):

```
write_note(title="Demo - Pour Over Method", directory="playground", ...)
# → action: "created"

write_note(title="Demo - Pour Over Method", directory="playground", ...)
# → action: "conflict", error: "NOTE_ALREADY_EXISTS"

write_note(title="Demo - Pour Over Method", directory="playground",
           overwrite=True, ...)
# → action: "updated"
```

## GOTCHAS

- [gotcha] MCP returns workspace-qualified permalinks for cloud projects while the CLI returns project-relative ones — same write, two canonical forms #permalinks
- [gotcha] The json-mode conflict response permalink is project-relative even though success responses are workspace-qualified #permalinks
- [gotcha] Nested frontmatter (schema:, settings:) must go through the metadata parameter, not content frontmatter — some clients mangle nested YAML in content #frontmatter
- [gotcha] A type: key inside content frontmatter silently overrides the note_type parameter #frontmatter
- [gotcha] CLI flag names diverge from MCP parameter names: --folder vs directory, --type vs note_type #cli-parity
- [gotcha] The CLI has no --metadata flag, so schema notes and custom frontmatter can only be written via MCP or by hand #cli-parity

## SEE ALSO

- see_also [[edit-note(3)]]
- see_also [[read-note(3)]]
- see_also [[delete-note(3)]]
- see_also [[bm-note(5)]]
- see_also [[bm-observation(5)]]
- see_also [[bm-relation(5)]]
