---
title: delete-note(3)
type: manpage
section: 3
name: delete-note
summary: delete a note or directory from the knowledge base
generated: registry
tool: delete_note
verified: 0.21.6 mcp+cli
---

# delete-note(3)

## NAME

**delete-note** — delete a note or directory from the knowledge base

## SYNOPSIS

MCP:

```
delete_note(identifier, is_directory=False, project=None, project_id=None,
            output_format="text")
```

CLI:

```
bm tool delete-note IDENTIFIER [--is-directory] [--project NAME]
```

## DESCRIPTION

Removes a note (or, with `is_directory=True`, an entire directory and its
contents) from both the filesystem and the index. The file is gone — for
local projects an external backup or git history is the only undo; cloud
projects can fall back to snapshots (see bm-cloud(1) snapshots).

For directories the identifier is the directory path without file
extension (`"docs"`, `"projects/2025"`).

## MCP USAGE

Verified against playground/ (create-then-delete):

```
delete_note("playground/demo-doomed-note", project="manual",
            output_format="json")
# → {"deleted": true, "title": "Demo - Doomed Note",
#    "permalink": "manual/playground/demo-doomed-note"}
```

## GOTCHAS

- [gotcha] is_directory=True deletes recursively with no confirmation step — list_directory first and check what you are about to remove #safety
- [gotcha] Identifier accepts title or permalink; with same-titled notes in different folders, prefer the permalink #identifiers
- [gotcha] Relations pointing at a deleted note are kept as unresolved rows (the target id is cleared, the link text stays) and relink on their own when a note with that name is written again, so deletion is recoverable for the graph — but the note's own outgoing relations and observations are gone with the file #graph
- [pattern] For notes that might be referenced elsewhere, prefer moving to an archive/ folder over deletion — move-note keeps every relation resolved instead of leaving them unresolved until a recreate #workflow

## SEE ALSO

- see_also [[move-note(3)]]
- see_also [[write-note(3)]]
- see_also [[list-directory(3)]]
