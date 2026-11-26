# Locked notes

Set the YAML boolean `locked: true` in a note's frontmatter to protect it from
Basic Memory content mutations:

```yaml
---
title: Release Runbook
locked: true
---
```

An agent can create a locked note or lock an existing note through a write or edit.
After that, API writes, edits, overwrites, and deletes return HTTP 423 with an
explanation. MCP and CLI note tools use that same API contract. Incoming metadata,
replacement Markdown, or find/replace operations cannot remove or weaken the lock:
the check uses the existing canonical Markdown before preparing the proposed edit.

API deletion of a directory containing a locked note is rejected before deleting any of
its notes. Moves remain allowed
and preserve the lock, including when the move updates a permalink. Reads and
indexing remain available.

This is a one-way switch for agents, not filesystem access control. To unlock a
note locally, edit its Markdown file directly and remove `locked: true` or set it to
`false`. Normal local reconciliation picks up that edit.

A hosted runtime may vouch that a write comes from a person in its own editor (in
Basic Memory Cloud, a signed-in person in the web app). Only that runtime's actor
resolver can set this, as `lock_authority=PersonInWebApp()`. Such a person may unlock
a note with a full replacement (PUT) that removes `locked` or sets it to `false` and
changes nothing else; the body and every other frontmatter key must stay the same, so
unlocking and editing are two separate saves. Their edits (PATCH) and deletes of a
locked note are still refused. Every other caller, including every local API, MCP and
CLI caller, is an agent and gets HTTP 423 for any change that would unlock the note.
Imports are raw file writes and are outside this contract, even when invoked through an
import endpoint. They may overwrite locked files. Direct filesystem edits and deletion
of a file or its directory also override the lock; normal indexing reconciles the result.
Basic Memory does not block or undo those changes. Agents with direct filesystem access
can therefore bypass this API-only protection.

Only a YAML boolean enables the lock; the string `"true"` is ordinary metadata.
Schema validation enforcement and Teams authorization are separate features.
