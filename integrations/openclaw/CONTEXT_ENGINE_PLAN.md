# Basic Memory ContextEngine Plan

## Goal

Complete the Basic Memory integration with OpenClaw's native memory lifecycle so BM works as a decorator around the default OpenClaw flow instead of relying on `agent_start` / `agent_end` shims.

The target model is:

- OpenClaw owns session state, context assembly pipeline, and compaction.
- Basic Memory owns durable knowledge, cross-session recall, and long-term capture.
- This plugin enriches the default flow without replacing it.

## Scope

This plan is for [issue #34](https://github.com/basicmachines-co/openclaw-basic-memory/issues/34), updated to match the "complement, don't replace" direction discussed there.

We will use the new OpenClaw `ContextEngine` lifecycle introduced in OpenClaw `2026.3.7` on March 6, 2026, but we will not implement a custom compaction strategy.

## Non-Goals

- Do not replace or override OpenClaw compaction behavior.
- Do not compete with lossless-claw or other alternate context engines.
- Do not turn BM into the canonical source of current-session state.
- Do not remove existing BM tools such as `memory_search`, `memory_get`, `search_notes`, `read_note`, and note CRUD tools.
- Do not add aggressive semantic retrieval on every turn.

## Design Principles

### Decorator, not replacement

The plugin should behave like a wrapper around the default OpenClaw memory model:

- OpenClaw tracks the live conversation.
- BM stores durable notes, tasks, decisions, and cross-session context.
- The plugin bridges the two systems at official lifecycle boundaries.

### Keep the baseline flow intact

Where the ContextEngine API requires behavior that OpenClaw already provides well, we should pass through to the default behavior instead of re-implementing it.

### Add value only where BM is strongest

BM should improve:

- session bootstrap recall
- durable post-turn capture
- subagent memory inheritance
- cross-session continuity through notes and graph search

BM should not try to improve:

- session-local compaction
- low-level pruning logic
- runtime token budgeting heuristics

## Current State

Today the plugin uses:

- `api.on("agent_start", ...)` for recall
- `api.on("agent_end", ...)` for capture
- composited `memory_search` / `memory_get` tools for explicit retrieval

This works, but it lives beside OpenClaw's memory lifecycle instead of inside it.

Relevant current files:

- `index.ts`
- `hooks/recall.ts`
- `hooks/capture.ts`
- `tools/memory-provider.ts`
- `types/openclaw.d.ts`

Current dependency constraint:

- `package.json` currently pins `openclaw` peer support to `>=2026.1.29`
- the local installed dependency is `openclaw@2026.2.6`
- ContextEngine work requires moving to the `2026.3.7+` SDK surface

## Target Architecture

Add a `BasicMemoryContextEngine` that composes with the default OpenClaw flow.

Expected lifecycle usage:

- `bootstrap`
  - initialize BM session-side recall state
  - gather small, high-signal context such as active tasks and recent activity
- `assemble`
  - pass through OpenClaw messages
  - optionally add a compact BM recall block when useful
- `afterTurn`
  - persist durable takeaways from the completed turn into BM
- `prepareSubagentSpawn`
  - prepare a minimal BM handoff for a child session
- `onSubagentEnded`
  - capture child results back into BM
- `compact`
  - do not customize
  - use legacy/default pass-through behavior only if the interface requires it

## Phase Plan

## Phase 1

### Commit goal

`feat(context-engine): move recall and capture into native lifecycle`

### Deliverables

- bump OpenClaw compatibility to `2026.3.7+`
- replace the local SDK shim with the real ContextEngine-capable SDK types where possible
- add a `BasicMemoryContextEngine`
- register the engine through `api.registerContextEngine(...)`
- migrate recall behavior from `agent_start` into `bootstrap`
- migrate capture behavior from `agent_end` into `afterTurn`
- keep existing BM tools and service startup behavior intact
- keep compaction fully default

### Expected behavior

- session startup still recalls active tasks and recent activity
- turns still get captured into BM
- plugin behavior is functionally similar to today, but now uses official lifecycle hooks

### Test coverage

- engine registration works
- `bootstrap` returns expected initialized state when recall finds data
- `bootstrap` is a no-op when recall finds nothing
- `afterTurn` captures only valid turn content
- `afterTurn` handles failures without breaking the run
- existing service startup and BM client lifecycle tests still pass

## Phase 2

### Commit goal

`feat(context-engine): add bounded assemble-time BM recall`

### Deliverables

- implement a minimal `assemble` hook
- preserve incoming OpenClaw messages in order
- add an optional BM recall block only when there is useful context
- bound the size of injected BM context so it stays cheap and predictable
- avoid per-turn graph-heavy retrieval unless explicitly configured later

### Expected behavior

- the model sees a small BM memory summary automatically when helpful
- explicit `memory_search` and `memory_get` remain available for deeper retrieval
- OpenClaw remains in charge of the actual context pipeline and compaction

### Test coverage

- `assemble` returns original messages unchanged when no recall block exists
- `assemble` adds a BM block when recall content exists
- injected content is size-bounded
- assembly remains stable across repeated turns when recall content is unchanged

## Phase 3

### Commit goal

`feat(context-engine): add subagent memory handoff`

### Deliverables

- implement `prepareSubagentSpawn`
- implement `onSubagentEnded`
- create a small BM handoff model for parent to child context transfer
- capture child outputs or summaries back into the parent knowledge base
- keep subagent integration lightweight and failure-tolerant

### Expected behavior

- subagents start with relevant BM context instead of a cold memory start
- useful child outputs become durable BM knowledge after completion
- failures in handoff/capture do not break subagent execution

### Test coverage

- child handoff is created for subagent sessions
- rollback path works if spawn fails after preparation
- child completion writes back expected BM artifacts
- delete/release/sweep paths are handled safely

## Implementation Notes

### Engine shape

Prefer a small, explicit implementation instead of pushing logic back into `index.ts`.

Likely new files:

- `context-engine/basic-memory-context-engine.ts`
- `context-engine/basic-memory-context-engine.test.ts`
- optional small helper modules for recall/capture formatting

### Hook migration

After Phase 1 lands, the old event-hook path in `index.ts` should be removed or disabled so we do not double-capture or double-recall.

### Tool preservation

The BM tool surface remains part of the product even after lifecycle integration:

- composited `memory_search` and `memory_get`
- graph CRUD tools
- schema tools
- slash commands and CLI commands

Lifecycle integration complements explicit retrieval; it does not replace it.

### Compatibility posture

This work should be shipped as the canonical BM integration path for OpenClaw `2026.3.7+`.

If we need a temporary compatibility story for older OpenClaw versions, keep it shallow and time-boxed. The long-term target should be one code path based on the native lifecycle.

## Risks

### Single-slot context engine model

OpenClaw currently resolves one `contextEngine` slot, not a middleware stack.

Implication:

- our engine must behave like "default behavior plus BM enrichment"
- we should not assume we can stack with other context engines automatically

### Over-injection

If `assemble` injects too much, BM could bloat prompt cost and work against the default system.

Mitigation:

- keep Phase 2 narrow
- bound injected size
- prefer summaries over raw note dumps

### Double-processing during migration

If old hooks and new lifecycle paths run together, recall and capture may happen twice.

Mitigation:

- Phase 1 should explicitly remove or disable the legacy hook wiring
- add tests that assert only one capture path is active

## Success Criteria

This feature is complete when:

- recall and capture happen through ContextEngine lifecycle hooks, not event shims
- BM enriches default session context without taking over compaction
- subagents inherit and return useful durable memory
- explicit BM tools remain intact
- the architecture clearly reflects "BM decorates OpenClaw memory"

## Commit Sequence

1. `feat(context-engine): move recall and capture into native lifecycle`
2. `feat(context-engine): add bounded assemble-time BM recall`
3. `feat(context-engine): add subagent memory handoff`

## Out of Scope for This Stack

- custom `compact` logic
- BM-driven token budgeting
- replacing post-compaction context reinjection
- new retrieval heuristics beyond a compact recall block
- multi-engine composition support inside OpenClaw core
