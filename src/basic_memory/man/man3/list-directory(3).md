---
title: list-directory(3)
type: manpage
section: 3
name: list-directory
summary: browse project folders with depth and glob filtering
generated: hand
tool: list_directory
verified: 0.21.6 mcp
---

# list-directory(3)

## NAME

**list-directory** — browse project folders with depth and glob filtering

## SYNOPSIS

MCP:

```
list_directory(dir_name="/", depth=1, file_name_glob=None,
               sort=None, page=1, page_size=10,
               project=None, project_id=None, output_format="text")
```

## DESCRIPTION

Returns a tree-style listing of a project directory: subfolders with paths,
files with their entity titles and modification dates, and a summary count.
`depth` (1–10) controls recursion; `file_name_glob` filters filenames
(`"*.md"`, `"*meeting*"`). `sort` orders files (`title_asc`, `title_desc`,
`updated_asc`, `updated_desc`; directories always come first), `page` and
`page_size` paginate (10 per page by default, 200 at most), and
`output_format="json"` returns the listing plus pagination data as
structured JSON.

This is the orientation tool — the equivalent of `ls` before surgical
operations like [[move-note(3)]] and [[delete-note(3)]].

## MCP USAGE

Verified against this manual:

```
list_directory(dir_name="/", depth=2, project="manual")
# → folders (man3, man5, man7, playground, schemas, playground/archive)
#   + files with titles and dates
#   + "Total: 16 items (6 directories, 10 files)"
```

## GOTCHAS

- [gotcha] There is no bm tool list-directory CLI wrapper; use bm project ls for local listing #cli-parity

## SEE ALSO

- see_also [[move-note(3)]]
- see_also [[delete-note(3)]]
- see_also [[search-notes(3)]]
