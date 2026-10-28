---
title: bm-edit(3)
type: manpage
section: 3
name: bm-edit
summary: change part of an existing note
generated: registry
tool: bm_edit
verified: 0.24.1 mcp
---

# bm-edit(3)

## NAME

**bm-edit** — change part of an existing note

## SYNOPSIS

```
bm_edit(identifier, command, project, content=None, section=None,
        find_text=None, expected_replacements=1, replace_subsections=True,
        metadata=None, expected_checksum=None, output_format="text")
```

## DESCRIPTION

Edits a note in place. `command` picks the change:

- `append` / `prepend` — add `content` at the end or after the frontmatter.
- `find_replace` — replace the exact text `find_text` with `content`. It must
  occur `expected_replacements` times (default 1).
- `replace_section`, `insert_before_section`, `insert_after_section` — act on the
  `section` heading, named as bm_read takes it (`Decisions`, `Auth/Decisions`,
  `Decisions[1]`).
- `set_metadata` — merge `metadata` into the frontmatter without touching the
  body. `metadata` also merges with any other command.

Pass `expected_checksum` (from bm_read) to edit only the revision you read; a
changed note is a `REVISION_CONFLICT` error carrying the current checksum. A
missing note is an error; create notes with bm_write.

## PARAMETERS

- **identifier** (string, required) — The note or file: a file path ('notes/plan.md'), permalink, memory:// URL, exact title, external id, or '<project>/path'.
- **command** (required) — The edit.
- **project** (string, required) — Required project name, 'workspace/project', or project external id. Discover projects with bm_project(command='list'). Paths are relative to this project; a qualified path must agree with it.
- **content** (string | null, optional, default: None) — The text to add or the replacement. Not used by set_metadata.
- **section** (string | null, optional, default: None) — The heading for section commands: "Decisions", "Auth/Decisions" for one under "Auth", or "Decisions[1]" for the second of two.
- **find_text** (string | null, optional, default: None) — For find_replace: the exact, case-sensitive text to replace.
- **expected_replacements** (integer, optional, default: 1) — For find_replace: how many times find_text must occur.
- **replace_subsections** (boolean, optional, default: True) — For replace_section: true replaces the section's subsections too; false stops at the first subsection heading.
- **metadata** (object | null, optional, default: None) — Frontmatter fields to set; other fields are kept.
- **expected_checksum** (string | null, optional, default: None) — Edit only while the note is still this revision (from bm_read).
- **output_format** (optional, default: "text")

## EXAMPLES

```
bm_edit(identifier="plans/launch-plan.md", command="find_replace",
        find_text="one PR per tool group", content="one PR per tool", project="demo")
# → Edited plans/launch-plan.md (find_replace)

bm_edit(identifier="plans/launch-plan.md", command="set_metadata",
        metadata={"status": "active"}, project="demo")
# → Edited plans/launch-plan.md (set_metadata)

bm_edit(identifier="plans/launch-plan.md", command="append",
        content="- [risk] the docs lag", project="demo")
# → Edited plans/launch-plan.md (append)

bm_edit(identifier="plans/launch-plan.md", command="append", content="x",
        expected_checksum="stale", project="demo")
# → error: REVISION_CONFLICT: 'plans/launch-plan.md' changed since you read it
#   (current checksum 77a457af…). Nothing was changed.
#   Try instead: bm_read(identifier='plans/launch-plan.md', project='demo'), then retry with
#   expected_checksum='77a457af…'
```

## GOTCHAS

- `section` belongs to the section commands only; `append` with a `section` is
  refused. Use `insert_after_section` to add under a heading.
- `set_metadata` refuses `title` and `permalink`: they are the note's identity.
  Rename with bm_move.

## SEE ALSO

- see_also [[bm-read(3)]]
- see_also [[bm-write(3)]]
- see_also [[bm-move(3)]]
