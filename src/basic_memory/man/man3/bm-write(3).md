---
title: bm-write(3)
type: manpage
section: 3
name: bm-write
summary: create or replace a note
generated: registry
tool: bm_write
verified: 0.24.1 mcp
---

# bm-write(3)

## NAME

**bm-write** — create or replace a note

## SYNOPSIS

```
bm_write(content, identifier=None, title=None, directory=None,
         note_type="note", tags=None, metadata=None, overwrite=None,
         expected_checksum=None, output_format="text", project=None)
```

## DESCRIPTION

Writes a whole note. Give the file path as `identifier` (`notes/plan.md`), or a
`title` plus a `directory`. With a path the file lands exactly there.

The title comes from the content's frontmatter `title:`, then the `title`
argument, then the file name. A `# Heading` in the content does not set it.

Writing over an existing note is an `ALREADY_EXISTS` error unless
`overwrite=true`. Pass `expected_checksum` (from bm_read) to replace a note only
if it is still the revision you read. To change part of a note, use bm_edit.

Content may hold frontmatter, observations (`- [category] fact #tag`) and
relations (`- relation_type [[Other Note]]`). Observations and relations are
indexed after the write, so they show up in bm_context and bm_search moments
later.

The result reports the accepted note's path and checksum. Read its indexed
observations and relations with bm_context.

## PARAMETERS

- **content** (string, required) — The note's Markdown. Saved as given; it may start with frontmatter.
- **identifier** (string | null, optional, default: None) — File path for the note, e.g. 'notes/plan.md'; '.md' is added when missing. '<project>/path' writes into another project. Use instead of title + directory.
- **title** (string | null, optional, default: None) — Note title. With `directory`, it also names the file. With `identifier`, it is used when the content has no frontmatter title.
- **directory** (string | null, optional, default: None) — Folder for a note addressed by title, e.g. 'meetings'; '/' is the root.
- **note_type** (string, optional, default: "note") — Frontmatter `type:`. A `type:` in the content wins.
- **tags** (array | string | null, optional, default: None) — Tags, as a list or 'a,b,c'.
- **metadata** (object | null, optional, default: None) — Extra frontmatter fields, merged into the note's frontmatter.
- **overwrite** (boolean | null, optional, default: None) — Replace a note that already exists. Default: the write_note_overwrite_default setting (false unless changed).
- **expected_checksum** (string | null, optional, default: None) — Replace the note only while it is still this revision (the checksum from bm_read). Implies overwrite=true.
- **output_format** (optional, default: "text")
- **project** (string | null, optional, default: None) — Project name, 'workspace/project', or project external id. With multiple addressable projects, scope note and search calls explicitly with this parameter or a project-qualified target.

## EXAMPLES

```
bm_write(identifier="plans/launch-plan.md", note_type="plan", tags=["release"],
         content="# Launch Plan\n\n- [goal] ship the 1.0 tools #release\n"
                 "- depends_on [[Release Checklist]]\n",
         project="demo")
# → Created plans/launch-plan.md
#   title: launch-plan
#   permalink: demo/plans/launch-plan
#   checksum: 13e0d14c…

bm_write(title="Release Checklist", directory="plans",
         content="# Release Checklist\n\n- [step] tag the release\n", project="demo")
# → Created plans/Release Checklist.md

bm_write(identifier="plans/launch-plan.md", content="# again", project="demo")
# → error: ALREADY_EXISTS: A note already exists at 'plans/launch-plan.md'. Nothing was written.
#   Try instead: bm_edit(identifier='plans/launch-plan.md', command='append', content='...')
#   to add to it, or bm_write(..., overwrite=true) to replace it
```

## GOTCHAS

- A note written by path is titled after the file (`launch-plan`) unless the
  content's frontmatter or the `title` argument says otherwise.
- With `title` plus `directory` the file name keeps the title's spelling
  (`Release Checklist.md`); its permalink is the slug (`release-checklist`).
- A folder name the model would rewrite (unsupported characters) is refused
  rather than silently changed.

## SEE ALSO

- see_also [[bm-edit(3)]]
- see_also [[bm-read(3)]]
- see_also [[bm-move(3)]]
- see_also [[bm-delete(3)]]
