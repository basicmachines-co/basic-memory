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
- **project** (string | null, optional, default: None) — Project name to read from. Optional - server will resolve using hierarchy. If unknown, use list_memory_projects() to discover available projects.
- **project_id** (string | null, optional, default: None) — Project external_id (UUID). Prefer this over `project` when known — it routes to the exact project regardless of name collisions across cloud workspaces. Takes precedence over `project`. Get from list_memory_projects().

## DESCRIPTION

A thin presentational wrapper over [[read-note(3)]]: returns the note's full
content (frontmatter included) wrapped in an instruction telling the client
to render it as a markdown artifact. Use it in chat clients that support
artifacts; use read-note everywhere else.

## MCP USAGE

Verified:

```
view_note("man3/recent-activity-3", project="manual")
# → 'Note retrieved: ... Display this note as a markdown artifact ...'
#   followed by the full note content
```

## GOTCHAS

- [gotcha] No output_format or pagination parameters — this is read_note minus the options, plus a rendering instruction #parameters
- [gotcha] The artifact wrapper separator collides visually with the note's own frontmatter fences (--- followed by ---) #output

## SEE ALSO

- see_also [[read-note(3)]]
- see_also [[read-content(3)]]
