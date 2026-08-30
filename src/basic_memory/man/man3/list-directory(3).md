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
               project=None, project_id=None)
```

## DESCRIPTION

Returns a tree-style listing of a project directory: subfolders with paths,
files with their entity titles and modification dates, and a summary count.
`depth` (1–10) controls recursion; `file_name_glob` filters filenames
(`"*.md"`, `"*meeting*"`).

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

- [gotcha] Output is text only — there is no structured json output_format on this tool, unlike most siblings #output
- [gotcha] There is no bm tool list-directory CLI wrapper; use bm project ls for local listing #cli-parity

## SEE ALSO

- see_also [[move-note(3)]]
- see_also [[delete-note(3)]]
- see_also [[search-notes(3)]]
