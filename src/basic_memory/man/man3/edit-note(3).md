---
title: edit-note(3)
type: manpage
section: 3
name: edit-note
summary: 'edit a note in place: append, prepend, find/replace, or section surgery'
generated: registry
tool: edit_note
verified: 0.21.6 mcp+cli
---

# edit-note(3)

## NAME

**edit-note** — edit a note in place: append, prepend, find/replace, or section surgery

## SYNOPSIS

MCP:

```
edit_note(identifier, operation, content, project=None, workspace=None,
          project_id=None, section=None, find_text=None,
          expected_replacements=None, replace_subsections=None,
          metadata=None, output_format="text")
```

CLI:

```
bm tool edit-note IDENTIFIER --operation OP --content TEXT
                  [--find-text TEXT] [--section "## Heading"]
                  [--expected-replacements N] [--project NAME]
```

## DESCRIPTION

Modifies an existing note without rewriting the whole file. Six operations:

- **append** / **prepend** — add content at the end or start; both create
  the note if it does not exist
- **find_replace** — replace occurrences of `find_text` with `content`;
  optionally validated by `expected_replacements`
- **replace_section** — replace everything under a markdown heading
- **insert_before_section** / **insert_after_section** — insert content
  around a heading without consuming it

`replace_section` is the mechanism this manual uses for regeneration:
generator-owned sections can be rewritten while curated sections survive
(see [[Manpage]]).

Unlike [[read-note(3)]], the identifier must be an **exact** title,
permalink, or memory:// URL — there is no fuzzy fallback for edits.

## PARAMETERS

- **identifier** (string, required) — The exact title, permalink, or memory:// URL of the note to edit. Must be an exact match - fuzzy matching is not supported for edit operations. Use search_notes() or read_note() first to find the correct identifier if uncertain. From the CLI this is a positional argument, not a flag.
- **operation** (string, required) — The editing operation to perform: - "append": Add content to the end of the note (creates the note if it doesn't exist) - "prepend": Add content to the beginning of the note (creates the note if it doesn't exist) - "find_replace": Replace occurrences of find_text with content (note must exist) - "replace_section": Replace a markdown section identified by its header (note must exist). By default the section spans through the next heading of the same or higher level, so its subsections are replaced too; see replace_subsections. - "insert_before_section": Insert content before a section heading without consuming it (note must exist) - "insert_after_section": Insert content after a section heading without consuming it (note must exist)
- **content** (string, required) — The content to add or use for replacement
- **project** (string | null, optional, default: None) — Project name to edit in. Optional - server will resolve using hierarchy. Use "workspace/project" to route to a project in a specific cloud workspace. If unknown, use list_memory_projects() to discover available projects.
- **workspace** (string | null, optional, default: None) — Workspace slug, name, or tenant_id. When provided with `project`, routes as `workspace/project`. Cannot be combined with `project_id`.
- **project_id** (string | null, optional, default: None) — Project external_id (UUID). Prefer this over `project` when known — it routes to the exact project regardless of name collisions across cloud workspaces. Takes precedence over `project`. Get from list_memory_projects().
- **section** (string | null, optional, default: None) — For replace_section operation - the markdown header to replace content under (e.g., "## Notes", "### Implementation")
- **find_text** (string | null, optional, default: None) — For find_replace operation - the text to find and replace
- **expected_replacements** (integer | null, optional, default: None) — For find_replace operation - the expected number of replacements (validation will fail if actual doesn't match)
- **replace_subsections** (boolean | null, optional, default: None) — For replace_section operation. Default (true): the section spans everything through the next heading of the same or higher level in the original note, so replacing "## Section" also replaces its "###" subsections — the replacement content may freely introduce new headings. Set to false to replace only the immediate content under the header, stopping at the next heading of any level and preserving subsections.
- **metadata** (object | null, optional, default: None) — Optional dict of frontmatter fields to merge, independent of `operation`. Provided keys overwrite existing frontmatter values (or are added if new); unrelated frontmatter keys and the note body are left untouched. Can be combined with any operation in the same call. `title` and `permalink` are ignored since those have their own dedicated handling; `type` is applied like any other frontmatter field. Key deletion is not supported.
- **output_format** (string, optional, default: "text") — "text" returns the existing markdown summary. "json" returns machine-readable edit metadata.

## MCP USAGE

All verified against playground/ notes:

```
edit_note("playground/demo-cli-stdin", "append",
          "\n## Appended Section\n\n- [example] ... #edit",
          project="manual")
# → operation: "append", fileCreated: false

edit_note("playground/demo-pour-over-method", "replace_section",
          "- [method] ...\n- [example] regenerated #edit\n",
          section="## Observations", project="manual")
# → operation: "replace_section"

edit_note("playground/demo-pour-over-method", "find_replace", "96°C",
          find_text="205°F", expected_replacements=1, project="manual")
# → operation: "find_replace"
```

## CLI EQUIVALENT

```
bm tool edit-note playground/demo-cli-stdin \
    --operation append --content "more" --project manual
```

## EXAMPLES

Replacement-count validation fails fast and changes nothing:

```
edit_note("playground/demo-cli-stdin", "find_replace", "standard input",
          find_text="stdin", expected_replacements=99, project="manual")
# → error: "Expected 99 occurrences of 'stdin', but found 4"
```

## GOTCHAS

- [gotcha] find_replace searches the whole file including YAML frontmatter — title and permalink fields can be silently rewritten if your find_text matches them; count occurrences with expected_replacements to guard #frontmatter
- [gotcha] The CLI defaults --expected-replacements to 1, but the MCP tool defaults to no validation at all — the same edit can fail via CLI and succeed via MCP #cli-parity
- [gotcha] append and prepend create missing notes instead of erroring; the other four operations require the note to exist #semantics
- [gotcha] edit_note accepts a workspace parameter that most sibling tools lack — prefer project_id for unambiguous cross-workspace routing #routing
- [pattern] Use expected_replacements on every scripted find_replace; it converts silent over-replacement into a loud failure #safety

## SEE ALSO

- see_also [[write-note(3)]]
- see_also [[read-note(3)]]
- see_also [[move-note(3)]]
- see_also [[delete-note(3)]]
