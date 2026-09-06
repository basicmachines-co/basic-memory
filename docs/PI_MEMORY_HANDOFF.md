---
title: Pi Memory Plugin — You Are Never Starting Over
type: note
thread_id: 01a074b5-e8f2-706d-8aad-84cbcff0e060
tags: [pi, basic-memory, integrations, continuity]
---

# Pi Memory Plugin — You Are Never Starting Over

## Context

Paul welcomed this Pi agent into Basic Memory and asked us to build a memory plugin.
The harness reports model `gpt-6-astra`, provider `openai-codex`; this is configured
identity, not independent verification of the underlying model. Our product goal is
**you are never starting over**: recover decisions, rationale, working state, blockers,
and next steps without asking Paul to explain the previous session again.

## Settled direction

Ship one Pi package with both CLI and MCP access modes. Users choose; notes and memory
behavior stay portable between modes. Pi favors CLI tools plus skills, but existing
MCP adapters mean we need not build another MCP host. Compare both empirically rather
than selecting transport on philosophy alone.

Basic Memory owns storage, search, graph operations, project routing, and authentication.
The Pi integration should be thin: shared skills plus lifecycle continuity. Durable
knowledge, transcripts, and lifecycle envelopes are distinct. Automatic capture defaults
and destinations still need a deliberate decision. Do not equate logging every turn
with useful memory, or silently send private traces into a team project.

## Where to work

- Worktree: `/Users/phernandez/dev/basicmachines/basic-memory-pi`
- Branch: `feat/pi-memory`, created from local `origin/main` (not freshly fetched).
- Main working directory remains on unrelated `feat/locked-notes`; leave it alone.
- Plan: `docs/PI_MEMORY_PLAN.md` in the Pi worktree.
- GitHub issue: https://github.com/basicmachines-co/basic-memory/issues/1488
- Intended package: `integrations/pi/`.
- No plugin implementation or end-to-end tests have been completed yet.
- Plan and this handoff are not committed yet.

## Evidence and references

Existing host integrations live at `integrations/openclaw/` and `integrations/hermes/`,
not `plugins/`. OpenClaw has a TypeScript MCP client and context engine. Hermes carries
sync/async thread bridging and host compatibility workarounds that Pi should not need.
Canonical shared skills live in top-level `skills/`.

Read the installed Pi README and full extension, package, skill, and compaction docs.
Pi supports async hooks, commands, context injection, session persistence, and reload.
Resource-owning extensions must handle session shutdown and replacement explicitly.

Candidate MCP adapter: https://github.com/nicobailon/pi-mcp-adapter
Npm search found version 2.32.1. README fetched to `/tmp/pi-mcp-adapter-readme.md`,
but not yet read or compatibility-verified. No extension has been installed.

Paul explicitly pointed out that the documentation website serves Markdown. Initially
we read source files in `../docs.basicmemory.com`; subsequently fetched live:
- https://docs.basicmemory.com/llms.txt
- https://docs.basicmemory.com/raw/reference/ai-assistant-guide.md
- https://docs.basicmemory.com/raw/integrations/harness-capture.md

The live assistant guide emphasizes search-before-answer, capture during work, editing
rather than duplicating, and graph context on follow-ups. `bm hook` currently documents
Claude and Codex lifecycle support, not Pi. Inspect core reuse before adding new logic.

## Immediate next steps

1. Read the MCP adapter README/source; verify installed Pi compatibility and whether
   lifecycle extensions can call it through a supported API, without private internals.
2. Verify actual CLI contracts: structured outputs, write failures, stdin, overwrite,
   routing, cancellation, and latency. Do not infer these solely from documentation.
3. Implement a minimal capture → fresh Pi session → recall scenario in both modes.
4. Extend to reload, resume, forks, compaction, project isolation, and failure handling.
5. Compare equivalent isolated fixtures with the same model and memory policies.

Separate Pi subprocesses can run end-to-end tests without restarting Paul's live Pi.
Model-backed tests use provider access and incur usage. Use temporary Basic Memory
config/data and dedicated opt-in cloud projects, not existing user projects. `/reload`
can refresh a configured extension interactively. Do not alter live Pi config implicitly.

## Current blocker discovered during this capture

`bm project list --json` returned:

```text
Error listing projects: Can't locate revision identified by 't3q4r5s6x7y8'
```

`bm tool write-note --help` works. The root cause of the migration mismatch has not
been investigated. No database repair or reset was attempted. This note is saved as a
local Markdown handoff only, not confirmed indexed in Basic Memory. Same-thread graph
lookup could not proceed after project discovery failed. When indexing becomes available,
search by `thread_id` above and synthesize into the existing note if one exists.

## Observations

- [decision] Support CLI and existing MCP-adapter access, with shared continuity semantics.
- [requirement] A fresh session must recover the decision, rationale, and next action with a real note reference.
- [principle] Transport is secondary to continuity across sessions and agents.
- [constraint] Switching modes must not require migrating knowledge.
- [status] Issue and plan exist; implementation has not started.
- [blocker] Installed CLI project listing fails on an unavailable database migration revision.
