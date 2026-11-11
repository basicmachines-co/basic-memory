---
title: bm-recent(3)
type: manpage
section: 3
name: bm-recent
summary: show what changed lately in a project
generated: registry
tool: bm_recent
verified: 1.0.0 mcp
---

# bm-recent(3)

## NAME

**bm-recent** — show what changed lately in a project

## SYNOPSIS

```
bm_recent(project, since="7d", items=None, page=1, page_size=10,
          output_format="text")
```

## DESCRIPTION

Lists the notes, observations or relations (`items`) of `project` that changed
within `since`, newest first, paged. JSON output carries `results`, `page`,
`page_size` and `has_more`. A project that was never indexed returns no rows plus
`guidance` that says how to index it, so a miss is not mistaken for an empty
project.

The shell spelling is `bm tool recent`.

## PARAMETERS

- **project** (string, required) — Required project name, 'workspace/project', or project external id. Discover projects with bm_project_list(). Paths are relative to this project; a qualified path must agree with it.
- **since** (string, optional, default: "7d") — How far back to look: '7d', '24h', '2 days ago', 'last week', or a date. Windows shorter than one day are widened to one day.
- **items** (array | null, optional, default: None) — What to return: notes, observations, relations, or a mix. Default: notes.
- **page** (integer, optional, default: 1) — Page number.
- **page_size** (integer, optional, default: 10) — Results per page (1-100).
- **output_format** (optional, default: "text")

## EXAMPLES

```
bm_recent(since="1d", project="demo")
# → ## Recent Activity: demo (1d)
#   **📄 Recent Notes & Documents (1):**
#     • launch-plan (plans) [id: 686e696d…]
```

## SEE ALSO

- see_also [[bm-search(3)]]
- see_also [[bm-context(3)]]
- see_also [[bm-project-list(3)]]
