---
title: bm-move(3)
type: manpage
section: 3
name: bm-move
summary: move or rename a note or a directory, within a project or into another
generated: registry
tool: bm_move
verified: 0.24.1 mcp
---

# bm-move(3)

## NAME

**bm-move** — move or rename a note or a directory, within a project or into another

## SYNOPSIS

```
bm_move(destination, project, identifier=None, path=None,
        expected_checksum=None, destination_project=None,
        output_format="text")
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
revision you read.

`destination_project` moves into another project; `destination` is then a path
in that project. The note is written there and then deleted here, so it is a
new note: it gets a new id and its own version history, and links in either
project are not rewritten. The original is deleted only after the copy is
written, and only if it is still the revision that was copied; if that delete
is refused, the copy is removed again and nothing has moved. A note already at
the destination is never replaced, and a locked note is refused. A directory
moves note by note; files that are not notes stay, and the result lists
whatever did not move.

A `destination` that starts with another project's name, without
`destination_project`, is refused rather than becoming a folder of that name.

## PARAMETERS

- **destination** (string, required) — New path, e.g. 'archive/plan.md'. A trailing '/' ('archive/') moves the note or directory into that folder under its current name.
- **project** (string, required) — Required project name, 'workspace/project', or project external id. Discover projects with bm_project(command='list'). Paths are relative to this project; a qualified path must agree with it.
- **identifier** (string | null, optional, default: None) — The note to move: a path, permalink, memory:// URL or exact title.
- **path** (string | null, optional, default: None) — The directory to move, with everything in it, e.g. 'drafts/2025'.
- **expected_checksum** (string | null, optional, default: None) — Move the note only while it is still this revision (from bm_read).
- **destination_project** (string | null, optional, default: None) — Move into this project instead: a project name, 'workspace/project', or project external id. `destination` is then a path in that project.
- **output_format** (optional, default: "text")

## EXAMPLES

```
bm_move(identifier="plans/release-checklist.md", destination="archive/", project="demo")
# → Moved plans/Release Checklist.md to archive/Release Checklist.md

bm_move(path="archive", destination="old", project="demo")
# → Moved directory archive to old (1 files)

bm_move(identifier="plans/launch.md", destination="inbox/", project="demo",
        destination_project="research")
# → Moved plans/launch.md (project demo) to inbox/launch.md (project research)
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
