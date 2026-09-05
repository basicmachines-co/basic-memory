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

- **identifier** (string, required) — The title or permalink of the note to read. Can be a full memory:// URL, a permalink, a title, or search text. From the CLI this is a positional argument, not a flag.
- **project** (string | null, optional, default: None) — Project name to read from. Optional - server will resolve using the hierarchy above. If unknown, use list_memory_projects() to discover available projects.
- **project_id** (string | null, optional, default: None) — Project external_id (UUID). Prefer this over `project` when known — it routes to the exact project regardless of name collisions across cloud workspaces. Takes precedence over `project`. Get from list_memory_projects().
- **page** (integer, optional, default: 1) — Page of fallback-search results to use when the identifier does not resolve to a note directly (default: 1). A direct or exact-title match always returns the full note content — page/page_size never chunk the note itself, and the title-match lookup pages through fixed-size pages of title results until an exact match is found or results are exhausted, regardless of page or page_size. Aliases: page_number.
- **page_size** (integer, optional, default: 10) — Number of fallback-search results per page (default: 10). When no match is found, this caps how many related-note suggestions are listed. Aliases: limit, per_page.
- **output_format** (string, optional, default: "text") — "text" returns markdown content or guidance text. "json" returns a structured object with title/permalink/file_path/content/frontmatter.
- **include_frontmatter** (boolean, optional, default: False) — When output_format="json", whether content should include the opening YAML frontmatter block; the parsed frontmatter object is returned either way. The CLI flag is --frontmatter (--include-frontmatter is a deprecated alias).

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
