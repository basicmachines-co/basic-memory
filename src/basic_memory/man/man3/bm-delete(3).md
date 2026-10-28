---
title: bm-delete(3)
type: manpage
section: 3
name: bm-delete
summary: delete a note or a directory
generated: registry
tool: bm_delete
verified: 0.24.1 mcp
---

# bm-delete(3)

## NAME

**bm-delete** — delete a note or a directory

## SYNOPSIS

```
bm_delete(project, identifier=None, path=None, recursive=False,
          expected_checksum=None, output_format="text")
```

## DESCRIPTION

Deletes a note (`identifier`) or a directory (`path`). A directory delete
removes every file under it and needs `recursive=true`; without it the error
says how many entries the directory holds. The project root cannot be deleted.

Pass `expected_checksum` (from bm_read) to delete a note only if it is still
the revision you read. A note that does not exist is an error. A delete cannot
be undone.

## PARAMETERS

- **project** (string, required) — Required project name, 'workspace/project', or project external id. Discover projects with bm_project(command='list'). Paths are relative to this project; a qualified path must agree with it.
- **identifier** (string | null, optional, default: None) — The note to delete: a path, permalink, memory:// URL or exact title.
- **path** (string | null, optional, default: None) — The directory to delete, with everything in it.
- **recursive** (boolean, optional, default: False) — Required true to delete a directory and everything under it.
- **expected_checksum** (string | null, optional, default: None) — Delete the note only while it is still this revision (from bm_read).
- **output_format** (optional, default: "text")

## EXAMPLES

```
bm_delete(path="old", project="demo")
# → error: INVALID_ARGUMENTS: 'old' is a directory with 1 entries at its top level.
#   Deleting it removes everything under it.
#   Try instead: bm_delete(path='old', recursive=true, project='<project external id>')

bm_delete(path="old", recursive=True, project="demo")
# → Deleted directory old (1 files)

bm_delete(identifier="plans/launch-plan.md", expected_checksum="stale", project="demo")
# → error: REVISION_CONFLICT: 'plans/launch-plan.md' changed since you read it
#   (current checksum 77a457af…). Nothing was deleted.
```

## SEE ALSO

- see_also [[bm-move(3)]]
- see_also [[bm-read(3)]]
- see_also [[bm-list(3)]]
