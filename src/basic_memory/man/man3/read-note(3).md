---
title: read-note(3)
type: manpage
section: 3
name: read-note
summary: read a note by title, permalink, or memory:// URL
generated: registry
tool: read_note
verified: 0.21.6 mcp+cli
---

# read-note(3)

## NAME

**read-note** — read a note by title, permalink, or memory:// URL

## SYNOPSIS

MCP:

```
read_note(identifier, project=None, project_id=None, page=1, page_size=10,
          output_format="text", include_frontmatter=False, start_line=None,
          end_line=None)
```

CLI:

```
bm tool read-note IDENTIFIER [--project NAME | --project-id UUID]
                  [--start-line N] [--end-line N] [--frontmatter]
                  [--local | --cloud]
```

## DESCRIPTION

Returns the raw markdown of a note. The identifier is resolved through a
cascade: direct permalink lookup, then exact title match, then full-text
search. If nothing matches exactly, MCP text mode returns guidance:
a ranked list of related notes, each with a copy-pasteable
`read_note()` call, plus suggested `search_notes()` and `write_note()` next
steps. JSON mode returns null note fields plus `error: "NOTE_NOT_FOUND"`,
a message naming the identifier, and `related_results` when available.
The CLI exits with status 1 for a missing note in every output mode, retaining
suggestions in JSON, plain, and Rich output. An existing empty note still succeeds.

Accepted identifier forms (all verified):

- exact title — `"Demo - CLI stdin"`
- permalink — `"playground/demo-cli-stdin"`
- memory URL — `"memory://playground/demo-cli-stdin"`
- workspace-qualified permalink — `"<workspace>/manual/playground/demo-cli-stdin"`

## PARAMETERS

- **identifier** (string, required) — The title or permalink of the note to read. Can be a full memory:// URL, a permalink, a title, or search text. From the CLI this is a positional argument, not a flag.
- **project** (string | null, optional, default: None) — Project name to read from. Optional - server will resolve using the hierarchy above. If unknown, use list_memory_projects() to discover available projects.
- **project_id** (string | null, optional, default: None) — Project external_id (UUID). Prefer this over `project` when known — it routes to the exact project regardless of name collisions across cloud workspaces. Takes precedence over `project`. Get from list_memory_projects().
- **page** (integer, optional, default: 1) — Page of fallback-search results to use when the identifier does not resolve to a note directly (default: 1). A direct or exact-title match returns the note content — page/page_size never chunk the note itself, and the title-match lookup pages through fixed-size pages of title results until an exact match is found or results are exhausted, regardless of page or page_size. Aliases: page_number.
- **page_size** (integer, optional, default: 10) — Number of fallback-search results per page (default: 10). When no match is found, this caps how many related-note suggestions are listed. Aliases: limit, per_page.
- **output_format** (string, optional, default: "text") — "text" returns markdown content or guidance text. "json" returns a structured object with title/permalink/file_path/content/frontmatter. Unresolved notes carry error="NOTE_NOT_FOUND" and a message, with related_results when suggestions are available.
- **include_frontmatter** (boolean, optional, default: False) — For unsliced JSON reads, include opening YAML in content; parsed frontmatter is returned either way. Explicit line ranges are never stripped. CLI: --frontmatter (--include-frontmatter is a deprecated alias).
- **start_line** (integer | null, optional, default: None) — First document line to read (1-based, inclusive). Defaults to 1 when only end_line is given. Line scans count the full Markdown, including frontmatter, matching cat's default line coordinates.
- **end_line** (integer | null, optional, default: None) — Last document line to read (inclusive); omitted means EOF. Out-of-file ranges return empty content; invalid/reversed ranges fail. With either bound, text output is numbered and JSON carries coordinates, has_more, and next_start_line/next_end_line. include_frontmatter does not strip an explicitly addressed range. Edits between calls may shift lines.

## MCP USAGE

```
read_note("Demo - CLI stdin", project="manual")
# → raw markdown, frontmatter included

read_note("memory://playground/demo-cli-stdin", project="manual")
# → same note via memory URL
```

## CLI EQUIVALENT

```
bm tool read-note "playground/demo-cli-stdin" --project manual
# → JSON: {"title": ..., "content": "<body without frontmatter>",
#          "frontmatter": {...}}
```

## LINE SCANNING

Pass `start_line` and/or `end_line` to read a 1-based inclusive range. An omitted
start means line 1; an omitted end means EOF. Coordinates count the full Markdown,
including frontmatter, regardless of `include_frontmatter`. They match literal
grep context and `cat` with its default `include_frontmatter=True`. Cat's explicit
`include_frontmatter=False` line reads remain body-relative.

Text scans show numbered lines and the next bounded range. JSON scans return
`content`, `start_line`, `end_line`, `total_lines`, `has_more`, `next_start_line`,
and `next_end_line`; both next bounds are null at EOF. End bounds clamp to EOF;
a start beyond EOF returns empty content with `end_line < start_line`. Empty
notes have zero total lines. Bounds below 1 and reversed ranges are errors.
Line endings normalize to LF, with CR/LF/CRLF counted consistently. A trailing
newline does not create an extra line. Lines may move after an intervening edit.

```
read_note("runbook", start_line=120, end_line=180)
bm tool read-note runbook --start-line 120 --end-line 180 --plain
```

## EXAMPLES

A miss in MCP text mode returns suggestions (run against the dev project):

```
read_note("xyzzy definitely missing note", project="dev")
# → "# Note Not Found in dev ..." with 3 ranked related notes,
#   each with a ready-to-run read_note() call, plus search_notes()
#   and write_note() suggestions
```

## GOTCHAS

- [gotcha] Unsliced text mode includes frontmatter; include_frontmatter controls unsliced JSON content #output
- [gotcha] page/page_size never chunk the note — use start_line/end_line for within-note scanning; page/page_size only page the miss-suggestion listing #pagination
- [gotcha] The CLI identifier is a positional argument, unlike write-note where everything is a flag #cli-parity
- [gotcha] Exact-title lookup walks its own fixed-size internal pages, so a tiny page_size cannot displace an exact match out of the lookup window #pagination

## SEE ALSO

- see_also [[write-note(3)]]
- see_also [[view-note(3)]]
- see_also [[read-content(3)]]
- see_also [[search-notes(3)]]
- see_also [[build-context(3)]]
