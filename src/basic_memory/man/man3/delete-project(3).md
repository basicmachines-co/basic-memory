---
title: delete-project(3)
type: manpage
section: 3
name: delete-project
summary: remove a project from configuration and index (files survive)
generated: hand
tool: delete_project
verified: 0.21.6 mcp
---

# delete-project(3)

## NAME

**delete-project** — remove a project from configuration and index (files survive)

## SYNOPSIS

MCP:

```
delete_project(project_name, workspace=None)
```

CLI:

```
bm project remove NAME
```

## DESCRIPTION

Unregisters a project from Basic Memory's configuration and database. The
markdown files are **not** deleted — the project simply stops being tracked,
and re-adding it restores access to all content. This makes delete-project
far less dangerous than [[delete-note(3)]], which does remove files.

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
- [gotcha] Files remain on disk; this is unregistration, not deletion — but the search index rows for the project are dropped and rebuilt on re-add #semantics

## SEE ALSO

- see_also [[create-memory-project(3)]]
- see_also [[list-memory-projects(3)]]
- see_also [[delete-note(3)]]
