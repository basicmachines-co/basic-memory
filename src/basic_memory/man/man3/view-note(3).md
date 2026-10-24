---
title: view-note(3)
type: manpage
section: 3
name: view-note
summary: retrieve a note formatted for artifact display
generated: registry
tool: view_note
verified: 0.21.6 mcp
---

# view-note(3)

## NAME

**view-note** — retrieve a note formatted for artifact display

## SYNOPSIS

```
view_note(identifier, project=None, project_id=None)
```

## PARAMETERS

- **identifier** (string, required) — The title or permalink of the note to view
- **project** (string | null, optional, default: None) — Project name to read from. Optional - server will resolve using hierarchy. If unknown, use bm_project(command="list") to discover available projects.
- **project_id** (string | null, optional, default: None) — Project external_id (UUID). Prefer this over `project` when known — it routes to the exact project regardless of name collisions across cloud workspaces. Takes precedence over `project`. Get from bm_project(command="list").

## DESCRIPTION

A thin presentational wrapper over [[bm-read(3)]]: returns the note's full
content (frontmatter included) wrapped in an instruction telling the client
to render it as a markdown artifact. Use it in chat clients that support
artifacts; use bm_read everywhere else.

## MCP USAGE

Verified:

```
view_note("man3/recent-activity-3", project="manual")
# → 'Note retrieved: ... Display this note as a markdown artifact ...'
#   followed by the full note content
```

## GOTCHAS

- [gotcha] No output_format or pagination parameters — this is bm_read minus the options, plus a rendering instruction #parameters
- [gotcha] Missing notes raise a tool error; exact resolution never returns search suggestions as content #errors

## SEE ALSO

- see_also [[bm-read(3)]]
- see_also [[bm-read(3)]]
