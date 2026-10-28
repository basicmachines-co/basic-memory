---
name: basic-memory
description: "Use the user's Basic Memory knowledge base: find notes for context, save linked notes, resume work, run Memory Quest. Use when a task needs prior notes, should be remembered, or mentions Memory Quest."
---

# Basic Memory

Basic Memory is the user's knowledge base: plain Markdown notes that you read and
write through the Basic Memory tools. Notes carry categorized observations and link
to each other through relations, so they form a graph that outlasts any one
conversation. What one session learns, the next one can find.

## Core habits

- **Search before you write.** Call `bm_search` before creating a note, and update
  the note you find instead of starting a duplicate.
- **Load context on purpose.** Use `bm_read` for a note you know, and
  `bm_context` with a `memory://` URL to pull in a note with its related notes.
- **Write connected notes.** Give each note observations (`- [decision] ...`) and
  relations (`- relates_to [[Other Note]]`) so it joins the graph.
- **Ask before saving.** Say what you would save and where, and write only after the
  user agrees, unless they have told you otherwise.
- **Don't overwrite by accident.** Pass `overwrite: false` to `bm_write` unless you
  are deliberately replacing a note you have read in full.
- **Name the project.** Discover projects with `bm_project(command="list")` when
  needed, then pass `project` on every note, folder, context and schema call and
  on recent activity. A project name, `workspace/project`, or external ID works;
  a qualified path does not replace it. Search requires `project`, `projects`, or
  `all_projects=True`. Discovery and bundled manuals need no scope.

## Routing

For these requests, read the listed file in full before acting, then follow it.
Paths are relative to this skill's folder.

<!-- routing-table -->
