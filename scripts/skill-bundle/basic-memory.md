---
name: basic-memory
description: "Work from the user's Basic Memory knowledge base: find and read notes for context, save linked notes, resume and organize work. Use when a task could benefit from prior notes or should be remembered."
---

# Basic Memory

Basic Memory is the user's knowledge base: plain Markdown notes that you read and
write through the Basic Memory tools. Notes carry categorized observations and link
to each other through relations, so they form a graph that outlasts any one
conversation. What one session learns, the next one can find.

## Core habits

- **Search before you write.** Call `search_notes` before creating a note, and update
  the note you find instead of starting a duplicate.
- **Load context on purpose.** Use `read_note` for a note you know, and
  `build_context` with a `memory://` URL to pull in a note with its related notes.
- **Write connected notes.** Give each note observations (`- [decision] ...`) and
  relations (`- relates_to [[Other Note]]`) so it joins the graph.
- **Ask before saving.** Say what you would save and where, and write only after the
  user agrees, unless they have told you otherwise.
- **Don't overwrite by accident.** Pass `overwrite: false` to `write_note` unless you
  are deliberately replacing a note you have read in full.
- **Name the project.** Pass the project on every call. Ask when it is unclear.

## Routing

For these requests, read the listed file in full before acting, then follow it.
Paths are relative to this skill's folder.

<!-- routing-table -->
