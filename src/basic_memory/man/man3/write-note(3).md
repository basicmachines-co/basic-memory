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
           overwrite=None, output_format="text")
```

CLI:

```
bm tool write-note --title TITLE --folder FOLDER [--content TEXT | < stdin]
                   [--tags TAG] [--type TYPE] [--project NAME | --project-id UUID]
                   [--overwrite] [--local | --cloud]
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

## PARAMETERS

- **title** (string, required) — The title of the note; written to frontmatter and drives the permalink. No H1 is added for you: content is saved as given, so include a "# Title" heading yourself if the note should open with one.
- **content** (string, required) — Markdown content for the note, can include observations and relations. May carry its own frontmatter; a `type:` in content frontmatter takes precedence over the note_type parameter.
- **directory** (string, required) — Directory path relative to project root where the file should be saved. Use forward slashes (/) as separators. Use "/" or "" to write to project root. Examples: "notes", "projects/2025", "research/ml", "/" (root). MCP accepts the aliases folder, dir, and path; the CLI flag is --folder.
- **project** (string | null, optional, default: None) — Project name to write to. Optional - server will resolve using the hierarchy above. Omitting both project and project_id writes to the session's active project (the last one this session touched), and only falls back to the configured default project when there is none — so after working in another project, pass project explicitly. Use "workspace/project" to route to a project in a specific cloud workspace. A bare name that exists in multiple workspaces resolves to the default workspace, so use the qualified form (or project_id) to disambiguate. If unknown, use list_memory_projects() to discover available projects and their qualified names.
- **workspace** (string | null, optional, default: None) — Workspace slug, name, or tenant_id. When provided with `project`, routes as `workspace/project`. Cannot be combined with `project_id`.
- **project_id** (string | null, optional, default: None) — Project external_id (UUID). Prefer this over `project` when known — it routes to the exact project regardless of name collisions across cloud workspaces. Takes precedence over `project`. Get from list_memory_projects().
- **tags** (array | string | null, optional, default: None) — Tags to categorize the note. Can be a list of strings, a comma-separated string, or None. Note: If passing from external MCP clients, use a string format (e.g. "tag1,tag2,tag3")
- **note_type** (string, optional, default: "note") — Type of note to create (stored in frontmatter `type:`). Defaults to "note". Can be "guide", "report", "config", "person", etc. The CLI flag is --type. A `type:` in content frontmatter takes precedence over this parameter, and this is what schema validation keys on.
- **metadata** (object | null, optional, default: None) — Optional dict of extra frontmatter fields merged into entity_metadata. Useful for schema notes or any note that needs custom YAML frontmatter beyond title/type/tags. Nested dicts are supported. Not available from the CLI.
- **overwrite** (boolean | null, optional, default: None) — If True, replace existing note on conflict. If False, error on conflict. If None (default), consult write_note_overwrite_default config setting.
- **output_format** (string, optional, default: "text") — "text" returns the existing markdown summary. "json" returns machine-readable metadata; on conflict it returns action: "conflict" with an error code instead of raising.

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
