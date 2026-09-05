---
title: list-directory(3)
type: manpage
section: 3
name: list-directory
summary: browse project folders with depth and glob filtering
generated: registry
tool: list_directory
verified: 0.21.6 mcp
---

# list-directory(3)

## NAME

**list-directory** — browse project folders with depth and glob filtering

## SYNOPSIS

MCP:

```
list_directory(dir_name="/", depth=1, file_name_glob=None, sort=None,
               page=1, page_size=10, output_format="text", project=None,
               project_id=None)
```

## PARAMETERS

- **dir_name** (string, optional, default: "/") — Directory path to list (default: root "/") Examples: "/", "/projects", "/research/ml"
- **depth** (integer, optional, default: 1) — Recursion depth (1-10, default: 1 for immediate children only) Higher values show subdirectory contents recursively
- **file_name_glob** (string | null, optional, default: None) — Optional glob pattern for filtering file names Examples: "*.md", "*meeting*", "project_*"
- **sort** (string | null, optional, default: None) — Optional file ordering: "title_asc", "title_desc", "updated_asc", or "updated_desc". Directories remain first.
- **page** (integer, optional, default: 1) — One-indexed result page (default: 1)
- **page_size** (integer, optional, default: 10) — Number of nodes per page (default: 10, maximum: 200)
- **output_format** (string, optional, default: "text") — "text" for a readable listing or "json" for structured pagination data
- **project** (string | null, optional, default: None) — Project name to list directory from. Optional - server will resolve using hierarchy. If unknown, use list_memory_projects() to discover available projects.
- **project_id** (string | null, optional, default: None) — Project external_id (UUID). Prefer this over `project` when known — it routes to the exact project regardless of name collisions across cloud workspaces. Takes precedence over `project`. Get from list_memory_projects().

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
