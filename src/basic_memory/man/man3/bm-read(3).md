---
title: bm-read(3)
type: manpage
section: 3
name: bm-read
summary: read a note or file by path, permalink, memory:// URL or title
generated: registry
tool: bm_read
verified: 0.24.1 mcp
---

# bm-read(3)

## NAME

**bm-read** — read a note or file by path, permalink, memory:// URL or title

## SYNOPSIS

```
bm_read(identifier, section=None, start_line=None, end_line=None,
        max_tokens=None, include_frontmatter=True, include_content=True,
        output_format="text", project=None)
```

Cloud additionally supports the following hosted signature. These options are not
available on the local core tool:

```
bm_read(identifier, section=None, start_line=None, end_line=None,
        max_tokens=None, include_frontmatter=True, include_content=True,
        review_mode="structured", view="markdown", version=None,
        versions=False, cursor=None, output_format="text", project=None)
```

## DESCRIPTION

Returns one note's Markdown, frontmatter included. The identifier can be a file
path (`notes/plan.md`), a permalink, a `memory://` URL, an exact title, an
external id, or a project-qualified path (`research/notes/plan.md`).

Read part of a long note with `section` (a heading), `start_line`/`end_line`,
or `max_tokens`. Line numbers count the whole file, frontmatter included, so
they match `cat -n`. A partial read says which lines it returned and where to
continue.

`include_content=false` returns only the title, paths, checksum and
`updated_at`. The checksum is the `expected_checksum` that bm_write, bm_edit,
bm_move and bm_delete take to guard a change.

Images come back as images and other binary files as documents. A missing note
is a `NOT_FOUND` error; find notes with bm_search.

The shell counterpart is cat(1).

## PARAMETERS

- **identifier** (string, required) — The note or file: a file path ('notes/plan.md'), permalink, memory:// URL, exact title, external id, or '<project>/path'.
- **section** (string | null, optional, default: None) — A heading to read: "Decisions", "Auth/Decisions" for a heading under "Auth", or "Decisions[1]" for the second "Decisions" heading.
- **start_line** (integer | null, optional, default: None)
- **end_line** (integer | null, optional, default: None)
- **max_tokens** (integer | null, optional, default: None) — Approximate token budget. Longer content is cut at a section or paragraph boundary, and the result says which line to continue from.
- **include_frontmatter** (boolean, optional, default: True) — Include the YAML frontmatter in a whole-note read.
- **include_content** (boolean, optional, default: True) — False returns only title, paths, checksum and updated_at.
- **output_format** (optional, default: "text")
- **project** (string | null, optional, default: None) — Project name, 'workspace/project', or project external id. With multiple addressable projects, scope note and search calls explicitly with this parameter or a project-qualified target.

## CLOUD PARAMETERS

Cloud's `bm_read` adds these parameters to the core parameters above:

- **review_mode** ("structured" | "raw", optional, default: "structured") — Return prose with open review comments and suggestions as threads, or the stored bytes including review markup with "raw".
- **view** ("markdown" | "text", optional, default: "markdown") — "text" renders `::bm-*` components as Markdown and runs their live queries. Edit from the Markdown view.
- **version** (string | null, optional, default: None) — Read a stored note version, using a `version_id` from `versions=True`.
- **versions** (boolean, optional, default: False) — List stored note versions, newest first, instead of reading the note.
- **cursor** (string | null, optional, default: None) — With `versions=True`, continue from the previous page's `next_cursor`.

A versions listing cannot be combined with a version, slices, `view="text"`,
or `include_content=False`. A cursor requires `versions=True`. Stored versions
and the text view must be read whole. The text view requires
`review_mode="structured"` and cannot read a stored version because live queries
show current data. Stored versions are available for Markdown notes only.

## EXAMPLES

```
bm_read(identifier="plans/launch-plan.md", project="demo")
# → ---
#   title: launch-plan
#   type: plan
#   permalink: demo/plans/launch-plan
#   ...
#   # Launch Plan
#   ...

bm_read(identifier="plans/launch-plan.md", section="Decisions", project="demo")
# → Lines 15-18 of 21 (document, including frontmatter); next: start_line=19, end_line=21
#   15: ## Decisions
#   16:
#   17: - [decision] one PR per tool group
#   18:

bm_read(identifier="plans/launch-plan.md", include_content=False,
        output_format="json", project="demo")
# → {"title": "launch-plan", "permalink": "demo/plans/launch-plan",
#    "file_path": "plans/launch-plan.md", "checksum": "13e0d14c…",
#    "updated_at": "2026-10-23T14:47:04.550062-05:00"}

bm_read(identifier="demo/plans/launch-plan.md", start_line=1, end_line=4)
# → Lines 1-4 of 21 (document, including frontmatter); next: start_line=5, end_line=8
#   1: ---
#   2: title: launch-plan
#   ...

bm_read(identifier="plans/missing.md", project="demo")
# → error: NOT_FOUND: No note matches 'plans/missing.md' in project 'demo'.
#   Try instead: bm_search(query='plans/missing.md')
```

## GOTCHAS

- With more than one project, an unqualified identifier is refused even when a
  default project is set. Pass `project`, or qualify the path (`demo/plans/...`).
- A section read returns numbered lines, like `start_line`/`end_line`; a whole
  note comes back without line numbers.
- The token budget is approximate. The first nonblank line is returned whole
  even when it exceeds the budget, so a read does not return only leading blank
  lines. Follow the returned line coordinates to continue.

## SEE ALSO

- see_also [[cat(1)]]
- see_also [[head(1)]]
- see_also [[bm-search(3)]]
- see_also [[bm-edit(3)]]
- see_also [[bm-context(3)]]
