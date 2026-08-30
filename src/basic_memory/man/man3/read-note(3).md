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
          output_format="text", include_frontmatter=False)
```

CLI:

```
bm tool read-note IDENTIFIER [--project NAME | --project-id UUID]
                  [--page N] [--page-size N] [--frontmatter]
                  [--local | --cloud]
```

## DESCRIPTION

Returns the raw markdown of a note. The identifier is resolved through a
cascade: direct permalink lookup, then exact title match, then full-text
search. If nothing matches exactly, read-note returns guidance text instead
of an error: a ranked list of related notes, each with a copy-pasteable
`read_note()` call, plus suggested `search_notes()` and `write_note()` next
steps. A miss is a navigable dead end, not an exception.

Accepted identifier forms (all verified):

- exact title — `"Demo - CLI stdin"`
- permalink — `"playground/demo-cli-stdin"`
- memory URL — `"memory://playground/demo-cli-stdin"`
- workspace-qualified permalink — `"<workspace>/manual/playground/demo-cli-stdin"`

## PARAMETERS

- **identifier** — title, permalink, or memory:// URL (CLI: positional
  argument, not a flag)
- **project** / **project_id** — target project; same semantics as
  [[write-note(3)]]
- **page**, **page_size** — apply only to the fallback suggestion listing.
  They never paginate the note itself: a direct or exact-title match always
  returns the full note. Aliases accepted: `page_number`, `limit`, `per_page`
- **output_format** — `text` (raw markdown) or `json` (structured object
  with title/permalink/file_path/content/frontmatter)
- **include_frontmatter** — json mode only: when true, `content` includes the
  opening YAML block; the parsed `frontmatter` object is returned either way.
  CLI flag: `--frontmatter` (`--include-frontmatter` is a deprecated alias)

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

## EXAMPLES

A miss returns suggestions, not an error (run against the dev project):

```
read_note("xyzzy definitely missing note", project="dev")
# → "# Note Not Found in dev ..." with 3 ranked related notes,
#   each with a ready-to-run read_note() call, plus search_notes()
#   and write_note() suggestions
```

## GOTCHAS

- [gotcha] Text mode always includes frontmatter; include_frontmatter only controls the json content field #output
- [gotcha] page/page_size never chunk the note — an exact match returns the full note regardless; they only page the miss-suggestion listing #pagination
- [gotcha] The CLI identifier is a positional argument, unlike write-note where everything is a flag #cli-parity
- [gotcha] Exact-title lookup walks its own fixed-size internal pages, so a tiny page_size cannot displace an exact match out of the lookup window #pagination

## SEE ALSO

- see_also [[write-note(3)]]
- see_also [[view-note(3)]]
- see_also [[read-content(3)]]
- see_also [[search-notes(3)]]
- see_also [[build-context(3)]]
