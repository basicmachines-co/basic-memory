---
title: bm-find(3)
type: manpage
section: 3
name: bm-find
summary: find files by name or notes by frontmatter, recursively
generated: registry
tool: bm_find
verified: 0.24.1 mcp
---

# bm-find(3)

## NAME

**bm-find** — find files by name or notes by frontmatter, recursively

## SYNOPSIS

```
bm_find(project, path="/", name=None, filters=None, fields=None, depth=10,
        page=1, page_size=10, output_format="text")
```

## DESCRIPTION

Searches a folder and everything under it. With `name`, matches file names
against a glob (`*meeting*`). With `filters`, matches notes by frontmatter,
e.g. `{"status": "active"}` or `{"priority": {"$in": ["high"]}}`; `fields`
returns just those frontmatter values per note. `path` limits the search to a
folder within the required `project`. A qualified path must agree with it.

For ranked text or meaning search, use bm_search.

The shell counterpart is find(1).

## PARAMETERS

- **project** (string, required) — Required project name, 'workspace/project', or project external id. Discover projects with bm_project(command='list'). Paths are relative to this project; a qualified path must agree with it.
- **path** (string, optional, default: "/") — A directory in the project, e.g. 'specs' or 'specs/active'. '/' is the project root; '<project>/path' addresses another project.
- **name** (string | null, optional, default: None) — File-name glob, e.g. '*.md'. Not with `filters`.
- **filters** (object | null, optional, default: None) — Frontmatter filters as {field: condition}. Nested fields use dot notation ("schema.confidence"). A condition is a value for equality ({"status": "draft"}), null for absent-or-null ({"owner": null}), a list for contains-all ({"tags": ["a", "b"]}), or an operator object with $gt, $gte, $lt, $lte, $in, $between, $contains or $exists, e.g. {"priority": {"$in": ["high"]}}. Several operators on one field AND together.
- **fields** (array | null, optional, default: None) — Frontmatter fields to return per note (dot paths allowed), e.g. ["title", "priority"]. Needs `filters`.
- **depth** (integer, optional, default: 10) — Folder levels to search (1-10).
- **page** (integer, optional, default: 1) — Page number.
- **page_size** (integer, optional, default: 10) — Results per page (1-100).
- **output_format** (optional, default: "text")

## EXAMPLES

```
bm_find(path="demo/", name="*plan*", project="demo")
# → Files under 'demo/' matching '*plan*':
#   📁 plans                          /demo/plans
#   📄 launch-plan.md                 demo/plans/launch-plan.md | launch-plan | …

bm_find(path="demo/", filters={"status": "active"}, fields=["status"], project="demo")
# → 1 notes under 'demo/' match:
#   - demo/plans/launch-plan.md | launch-plan | status='active'
```

## GOTCHAS

- A `name` glob matches folders as well as files, so `*plan*` also returns the
  `plans` folder.

## SEE ALSO

- see_also [[find(1)]]
- see_also [[bm-list(3)]]
- see_also [[bm-search(3)]]
