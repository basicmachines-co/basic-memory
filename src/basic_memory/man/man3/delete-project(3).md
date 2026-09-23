---
title: delete-project(3)
type: manpage
section: 3
name: delete-project
summary: remove a project (local files survive by default; cloud files are always deleted)
generated: registry
tool: delete_project
verified: 0.21.6 mcp
---

# delete-project(3)

## NAME

**delete-project** — remove a project (local files survive by default; cloud files are always deleted)

## SYNOPSIS

MCP:

```
delete_project(project_name, delete_notes=False, workspace=None)
```

CLI:

```
bm project remove NAME
```

## PARAMETERS

- **project_name** (string, required) — Name of the project to delete
- **delete_notes** (boolean, optional, default: False) — Local projects only: also delete the note files from disk. Defaults to False, which only stops tracking the project. Ignored for cloud projects, whose files are always deleted.
- **workspace** (string | null, optional, default: None) — Optional cloud workspace selector to delete the project from. Slug is preferred for AI callers, but tenant_id and unique name are also accepted. When omitted, the connection's default workspace is used. A workspace selector implies cloud routing: without cloud credentials the call fails fast, matching create_memory_project behavior (#954).

## DESCRIPTION

Unregisters a project from Basic Memory's configuration and database.

For a **local** project the markdown files are **not** deleted by default —
the project simply stops being tracked, and re-adding it restores access to
all content. `delete_notes=True` also deletes the note files from disk; with
it, this call is as destructive as [[delete-note(3)]] applied to every note.

For a **cloud** project the note files in cloud storage are **always**
deleted, whatever `delete_notes` says. Files kept under a deleted cloud
project could not be reached, so the cloud service purges them on every
delete. They can be recovered only from a cloud snapshot
(`bm cloud snapshot list`, `bm cloud restore`). `bm project remove` asks for
confirmation first (`--yes` skips it) and keeps a local sync directory unless
`--delete-local-files` is passed.

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
- [gotcha] Local project files remain on disk by default; this is unregistration, not deletion — the search index rows for the project are dropped and rebuilt on re-add #semantics
- [gotcha] Cloud projects always lose their cloud files on delete; delete_notes=False does not keep them, and only a cloud snapshot brings them back #destructive
- [gotcha] The MCP tool does not ask twice — there is no confirmation step (the CLI's `bm project remove` prompts for cloud projects) #destructive

## SEE ALSO

- see_also [[create-memory-project(3)]]
- see_also [[list-memory-projects(3)]]
- see_also [[delete-note(3)]]
