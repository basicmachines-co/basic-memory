---
title: move-note(3)
type: manpage
section: 3
name: move-note
summary: move a note or directory, keeping the database consistent
generated: hand
tool: move_note
verified: 0.21.6 mcp
---

# move-note(3)

## NAME

**move-note** — move a note or directory, keeping the database consistent

## SYNOPSIS

MCP:

```
move_note(identifier,
          destination_path="" | destination_folder=None,
          is_directory=False,
          project=None, project_id=None, output_format="text")
```

## DESCRIPTION

Relocates a note (or, with `is_directory=True`, a whole directory tree) and
updates the index. Two mutually exclusive destination forms:

- **destination_folder** — move into a folder, keeping the filename
  (single-file moves only)
- **destination_path** — full new path including filename
  (`"work/meetings/note.md"`), or the new directory path for directory moves

Like [[edit-note(3)]], the identifier must be exact — no fuzzy matching for
destructive operations.

## MCP USAGE

Verified against playground/:

```
move_note("playground/demo-cli-stdin",
          destination_folder="playground/archive", project="manual",
          output_format="json")
# → {"moved": true,
#    "source": "playground/demo-cli-stdin",
#    "destination": "playground/archive/Demo - CLI stdin.md",
#    "permalink": "manual/playground/demo-cli-stdin"}
```

## GOTCHAS

- [gotcha] By default a permalink pinned in frontmatter survives the move unchanged — links keep working, but the permalink no longer mirrors the file path (note the example above: file in archive/, permalink still playground/). With update_permalinks_on_move=True in the project config, or when the note had no permalink, the permalink is rewritten from the destination path and old memory:// links stop resolving #permalinks
- [gotcha] destination_folder and destination_path are mutually exclusive, and destination_folder cannot be used for directory moves #parameters
- [gotcha] There is no bm tool move-note CLI wrapper — moves are MCP-only (or plain mv + re-sync for local projects) #cli-parity

## SEE ALSO

- see_also [[edit-note(3)]]
- see_also [[delete-note(3)]]
- see_also [[list-directory(3)]]
