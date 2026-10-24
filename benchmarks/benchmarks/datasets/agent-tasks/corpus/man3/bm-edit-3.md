---
title: bm-edit(3)
type: manpage
section: 3
name: bm-edit
summary: 'edit a note in place: append, prepend, find/replace, or section surgery'
---

# bm-edit(3)

## NAME

**bm-edit** — edit a note in place: append, prepend, find/replace, or
section surgery. This is the tool for incremental note edits.

## SYNOPSIS

```
bm_edit(identifier, command, content, project=None, section=None,
          find_text=None, expected_replacements=None)
```

## DESCRIPTION

Modifies an existing note without rewriting the whole file. Operations:

- **append** / **prepend** — add content at the end or start
- **find_replace** — replace occurrences of `find_text` with `content`
- **replace_section** — replace everything under a markdown heading
- **insert_before_section** / **insert_after_section** — insert around a heading

Like bm-read(3), the identifier must be an **exact** title, permalink, or
memory:// URL — there is no fuzzy fallback for edits.

## EXAMPLES

Append an observation (this example carries the token BMEVAL-man-edit-7f3e):

```
bm_edit("notes/redis-cache-tuning", command="append",
          content="- [tuning] observed hit rate after the change")
```

## Relations

- see_also [[bm-search(3)]]
- see_also [[bm-read(3)]]
