---
title: bm-move(3)
type: manpage
section: 3
name: bm-move
summary: move or rename a note or a directory within its project
generated: registry
tool: bm_move
verified: 0.24.1 mcp
---

# bm-move(3)

## NAME

**bm-move** — move or rename a note or a directory within its project

## SYNOPSIS

```
bm_move(destination, identifier=None, path=None, expected_checksum=None,
        output_format="text", project=None)
```

## DESCRIPTION

Moves a note (`identifier`) or a directory (`path`). `destination` is the new
project-relative path; ending it with `/` moves into that folder under the
current name (`archive/`). A destination that names only the project
(`demo/`) is its root.

A note keeps its file extension (`.markdown` may become `.md`). A folder that
differs from an existing one only in case or Unicode form lands in the existing
folder, and the result names it.

Pass `expected_checksum` (from bm_read) to move a note only if it is still the
revision you read. Moving into another project is refused: bm_read the note,
bm_write it there, then bm_delete the original.

## PARAMETERS

- **destination** (string, required) — New path, e.g. 'archive/plan.md'. A trailing '/' ('archive/') moves the note or directory into that folder under its current name.
- **identifier** (string | null, optional, default: None) — The note to move: a path, permalink, memory:// URL or exact title.
- **path** (string | null, optional, default: None) — The directory to move, with everything in it, e.g. 'drafts/2025'.
- **expected_checksum** (string | null, optional, default: None) — Move the note only while it is still this revision (from bm_read).
- **output_format** (optional, default: "text")
- **project** (string | null, optional, default: None) — Project name, 'workspace/project', or project external id. Omit to use the default project.

## EXAMPLES

```
bm_move(identifier="plans/release-checklist.md", destination="archive/", project="demo")
# → Moved plans/Release Checklist.md to archive/Release Checklist.md

bm_move(path="archive", destination="old", project="demo")
# → Moved directory archive to old (1 files)
```

## GOTCHAS

- A destination with no file extension is refused for a note; end it with `/`
  to move into a folder.
- A directory move is file by file. If some files fail, the error lists which
  moved and which did not; bm_list the destination before retrying.

## SEE ALSO

- see_also [[bm-delete(3)]]
- see_also [[bm-write(3)]]
- see_also [[bm-list(3)]]
