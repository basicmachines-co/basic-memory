---
title: bm-list(3)
type: manpage
section: 3
name: bm-list
summary: 'list one folder: its subfolders, then its files'
generated: registry
tool: bm_list
verified: 0.24.1 mcp
---

# bm-list(3)

## NAME

**bm-list** — list one folder: its subfolders, then its files

## SYNOPSIS

```
bm_list(path="/", glob=None, sort=None, page=1, page_size=10,
        output_format="text", project=None)
```

## DESCRIPTION

Lists one level of a folder: subfolders first, then files with their title,
date and id. `bm_list(path="/")` with no project lists the projects you can
address; `<project>/path` lists inside another project, and the paths that come
back carry the same prefix.

Filter files with `glob` and order them with `sort`. Results are paged; the text
output ends with the call for the next page. For a recursive search by name or
frontmatter, use bm_find.

The shell counterpart is ls(1).

## PARAMETERS

- **path** (string, optional, default: "/") — A directory in the project, e.g. 'specs' or 'specs/active'. '/' is the project root; '<project>/path' addresses another project.
- **glob** (string | null, optional, default: None) — File-name glob, e.g. '*.md' or '*meeting*'.
- **sort** (string | null, optional, default: None) — File order. Folders always come first.
- **page** (integer, optional, default: 1) — Page number.
- **page_size** (integer, optional, default: 10) — Results per page (1-100).
- **output_format** (optional, default: "text")
- **project** (string | null, optional, default: None) — Project name, 'workspace/project', or project external id. Omit to use the default project.

## EXAMPLES

```
bm_list(path="/")
# → Projects:
#   📁 demo                           /demo
#   📁 main                           /main

bm_list(path="plans", project="demo")
# → Contents of 'plans':
#   📄 launch-plan.md         plans/launch-plan.md | launch-plan | 2026-10-23 | id: 686e696d…
#   📄 Release Checklist.md   plans/Release Checklist.md | Release Checklist | 2026-10-23 | id: 7bf40fbe…

bm_list(path="demo/plans", sort="title_asc", output_format="json")
# → {"nodes": [{"name": "launch-plan.md", "file_path": "demo/plans/launch-plan.md",
#    "type": "file", "title": "launch-plan", ...}, ...], "page": 1, "has_more": false}
```

## GOTCHAS

- `glob` and `sort` apply to files in a folder; on the project listing at `/`
  they are refused.

## SEE ALSO

- see_also [[ls(1)]]
- see_also [[bm-find(3)]]
- see_also [[bm-read(3)]]
