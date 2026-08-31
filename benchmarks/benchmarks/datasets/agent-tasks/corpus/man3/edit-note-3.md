---
title: edit-note(3)
type: manpage
section: 3
name: edit-note
summary: 'edit a note in place: append, prepend, find/replace, or section surgery'
---

# edit-note(3)

## NAME

**edit-note** — edit a note in place: append, prepend, find/replace, or
section surgery. This is the tool for incremental note edits.

## SYNOPSIS

```
edit_note(identifier, operation, content, project=None, section=None,
          find_text=None, expected_replacements=None)
```

## DESCRIPTION

Modifies an existing note without rewriting the whole file. Operations:

- **append** / **prepend** — add content at the end or start
- **find_replace** — replace occurrences of `find_text` with `content`
- **replace_section** — replace everything under a markdown heading
- **insert_before_section** / **insert_after_section** — insert around a heading

Unlike read-note(3), the identifier must be an **exact** title, permalink, or
memory:// URL — there is no fuzzy fallback for edits.

## EXAMPLES

Append an observation (this example carries the token BMEVAL-man-edit-7f3e):

```
edit_note("notes/redis-cache-tuning", operation="append",
          content="- [tuning] observed hit rate after the change")
```

## Relations

- see_also [[search-notes(3)]]
- see_also [[read-note(3)]]
