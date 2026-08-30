---
title: write-note(3)
type: manpage
section: 3
name: write-note
summary: create or overwrite a markdown note in the knowledge base
generated: hand
tool: write_note
verified: 0.21.6 mcp+cli
---

# write-note(3)

## NAME

**write-note** — create or overwrite a markdown note in the knowledge base

## SYNOPSIS

MCP:

```
write_note(title, content, directory,
           project=None, workspace=None, project_id=None,
           tags=None, note_type="note", metadata=None, overwrite=None,
           output_format="text")
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

- **title** — note title; written to frontmatter and drives the permalink.
  No H1 is added for you: `content` is saved as given, so include
  `# Title` yourself if the note should open with a heading
- **content** — markdown body; may include observations, relations, and its own
  frontmatter (a `type:` in content frontmatter takes precedence over the
  `note_type` parameter)
- **directory** — folder path relative to project root; `/` or empty writes to
  root. MCP accepts the aliases `folder`, `dir`, and `path`; the CLI flag is
  `--folder`
- **project** / **project_id** — target project by name or UUID; `project_id`
  wins and is unambiguous across workspaces. Omitting both writes to the
  session's active project — the last one this session touched — and only
  falls back to the configured default when there is none, so after working
  in another project pass `project` explicitly. Qualified names
  (`workspace/project`) route across workspaces
- **workspace** — cloud workspace slug, name, or tenant_id; with `project`,
  routes as `workspace/project`. Cannot be combined with `project_id`
- **tags** — list or comma-separated string; external MCP clients should pass
  the string form (`"a,b,c"`)
- **note_type** (CLI: `--type`) — frontmatter `type:`, default `note`; this is
  what schema validation keys on (see [[bm-schema(5)]])
- **metadata** — dict merged into frontmatter; the reliable way to write
  nested YAML (schema notes, custom fields). Not available from the CLI
- **overwrite** — `True` replaces on conflict; `False` errors; unset consults
  the `write_note_overwrite_default` config setting
- **output_format** — `text` (markdown summary) or `json` (machine-readable;
  conflicts come back as `action: "conflict"` with an `error` code instead of
  raising)

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
