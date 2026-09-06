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

This is a one-way switch through the note API, not filesystem access control. To
unlock a note, edit its Markdown file directly and remove `locked: true` or set it
to `false`. Normal local reconciliation picks up that edit. There is no API unlock
flag, privileged unlock endpoint, or distinction between human and agent callers.
Imports are raw file writes and are outside this contract, even when invoked through an
import endpoint. They may overwrite locked files. Direct filesystem edits and deletion
of a file or its directory also override the lock; normal indexing reconciles the result.
Basic Memory does not block or undo those changes. Agents with direct filesystem access
can therefore bypass this API-only protection.

Only a YAML boolean enables the lock; the string `"true"` is ordinary metadata.
Schema validation enforcement and Teams authorization are separate features.
