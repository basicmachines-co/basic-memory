---
title: delete-project(3)
type: manpage
section: 3
name: delete-project
summary: remove a project from configuration and index (files survive by default)
generated: hand
tool: delete_project
verified: 0.21.6 mcp
---

# delete-project(3)

## NAME

**delete-project** — remove a project from configuration and index (files survive by default)

## SYNOPSIS

MCP:

```
delete_project(project_name, delete_notes=False, workspace=None)
```

CLI:

```
bm project remove NAME
```

## DESCRIPTION

Unregisters a project from Basic Memory's configuration and database. By
default the markdown files are **not** deleted — the project simply stops
being tracked, and re-adding it restores access to all content.
`delete_notes=True` also deletes the note files themselves (from local disk
for local projects, from cloud storage for cloud projects); with it, this
call is as destructive as [[delete-note(3)]] applied to every note.

`workspace` targets a project in a specific cloud workspace (added for
cross-workspace disambiguation).

## MCP USAGE

Verified (create-then-delete of a scratch local project):

```
delete_project("manual-scratch-952")
# → "✓ Project 'manual-scratch-952' removed successfully ...
#    Files remain on disk but project is no longer tracked."
```

## GOTCHAS

- [gotcha] Unlike every sibling tool, delete_project takes no project_id and no output_format — name + workspace is the only addressing mode, and output is text only #parity
- [gotcha] Files remain on disk by default; this is unregistration, not deletion — the search index rows for the project are dropped and rebuilt on re-add #semantics
- [gotcha] delete_notes=True removes the note files too, and nothing asks twice — there is no confirmation step and no undo #destructive

## SEE ALSO

- see_also [[create-memory-project(3)]]
- see_also [[list-memory-projects(3)]]
- see_also [[delete-note(3)]]
