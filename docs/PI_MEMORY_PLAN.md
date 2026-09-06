# Pi memory integration: you are never starting over

## Goal

A fresh Pi session recovers the relevant decisions, working state, blockers, and next
steps without the user repeating the previous conversation. Knowledge belongs to the
user and remains usable across agents, projects, and local/cloud deployments.

Ship one Pi package with two user-selectable access modes: Basic Memory CLI and an
existing Pi MCP adapter. Share memory behavior and skills, not two independent memory
implementations. Switching transport must not require migrating notes.

## Confirmed direction

- Implement under `integrations/pi/` in the Basic Memory monorepo.
- Reuse canonical top-level memory skills and Basic Memory routing/authentication.
- Support CLI access and MCP through an existing extension; do not build a generic MCP host.
- Preserve Pi's native session history and compaction.
- Distinguish lifecycle metadata, raw transcripts, and synthesized durable knowledge.
- Keep project selection explicit; never change the user's global default implicitly.
- Surface capture/recall failures without blocking ordinary work or claiming a save succeeded.

## Phase 1 — investigate and prove transport contracts

- [x] Inspect `pi-mcp-adapter` and alternatives: current Pi compatibility, tool naming,
      discovery, cancellation, shutdown/reload, stdio and remote support, licensing.
- [x] Determine whether lifecycle hooks can invoke the adapter through a supported API.
      If not, document the limitation before choosing how automated recall/capture runs;
      do not depend on private adapter internals.
- [x] Verify installed `bm` CLI flags, structured output, write error/overwrite semantics,
      stdin support, project UUID routing, and local/cloud behavior against real calls.
- [x] Inspect existing `bm hook` recall/checkpoint implementation for reusable core logic.
      Its documented harnesses are Claude and Codex, not Pi.
- [x] Record a short architecture decision with version requirements and initial defaults.
      See `docs/PI_MEMORY_TRANSPORT_DECISION.md`.

## Phase 2 — smallest end-to-end continuity slice

- [x] Add Pi package metadata, TypeScript extension entrypoint, config validation,
      and hermetic test setup.
- [x] Support explicit CLI/MCP mode and project selection with visible status.
- [x] Bundle a focused set of shared skills: notes, capture, continue, and tasks.
- [x] Verify bundled skill tool/CLI instructions work in both modes without duplicate discovery.
- [x] Capture one coherent working-thread note with goal, decisions and rationale,
      current state, blockers, next steps, and source/session provenance.
- [x] Start a separate fresh Pi process and recover that thread through Basic Memory.
- [x] Repeat the identical scenario through the other access mode.
      See `docs/PI_MEMORY_E2E_RESULTS.md`.

## Phase 3 — lifecycle continuity

- [x] Bounded recall at session entry / first relevant prompt, including note identifiers
      and source provenance; avoid repeatedly injecting the same context.
- [x] Capture important decisions during work; support explicit remember/recall commands.
- [ ] Add compaction-aware durable checkpoints without replacing native compaction.
      Settle checkpoint timing using Pi lifecycle tests, not assumptions about reentrant turns.
- [x] Restore state on reload/resume and track forks/tree navigation without merging
      contradictory branches or duplicating captures.
- [x] Choose and document automatic capture defaults and destinations. Raw transcript
      capture is a separate opt-in decision, not an implied prerequisite for continuity.
- [x] Treat recalled notes as source material, not privileged instructions. Respect project
      trust and never route private session traces into shared projects implicitly.
- [x] Bound subprocess/request duration, propagate cancellation, and clean up owned resources.
      Never blindly retry non-idempotent writes after an ambiguous transport failure.

## Phase 4 — compare CLI and MCP

Run both modes with the same Basic Memory version, seeded notes, model, task prompts,
retrieval budgets, and capture policy. Use independent sessions and equivalent isolated
projects so one trial cannot benefit from another trial's captures.

Measure separately:

- cold startup, first operation, warm read/search/write latency;
- tool discovery and prompt overhead, tokens and provider usage;
- successful writes and recovery of their actual identifiers;
- recall correctness: decision, rationale, blocker, next action, source citation;
- duplication, routing isolation, and behavior after errors/reload/compaction.

Test local routing first. Cloud tests are opt-in against a dedicated test project;
never mutate existing personal/team projects as fixtures. Report model-backed quality
results separately from deterministic transport assertions. Recommend a default from
observed results while keeping both modes supported.

## Acceptance scenarios

1. Session A records a decision and unfinished next step. Fresh session B receives only
   the topic and recovers the decision, rationale, and next step with a real note reference.
2. CLI captures are recalled over MCP and vice versa without migration.
3. Reload, resume, compaction, and forks preserve usable context without duplicate writes
   or attributing another branch's state to the current branch.
4. Two projects with conflicting decisions remain isolated, including ambiguous names.
5. Missing CLI, unavailable MCP server, auth failure, cancellation, and failed writes are
   visible; Pi remains usable and never falsely confirms persistence.
6. Opted-out capture writes nothing; retrieved note instructions cannot change capture
   settings or destination routing.

## Phase 5 — distribution and documentation

- [x] Root `just package-check-pi` target and integration into consolidated package checks.
- [x] Package install/pack validation and isolated Pi subprocess smoke tests.
      See `docs/PI_MEMORY_E2E_RESULTS.md`.
- [x] README with CLI and MCP installation, explicit mode switching, project routing,
      privacy defaults, failure recovery, and supported version matrix.
- [ ] Add the Pi integration page to `docs.basicmemory.com` in a separate docs change.
- [x] Wire release metadata consistently with existing integration packages.
      Version bump wiring is in place; see `docs/PI_MEMORY_SHIPPING.md`.
- [ ] Add npm publishing workflow wiring for the Pi package.
      Publishing remains open until the release workflow includes `integrations/pi`.

## Development workflow

Worktree: `../basic-memory-pi`; branch: `feat/pi-memory` (created from local `origin/main`).
Use separate Pi print/JSON/RPC processes for end-to-end verification and isolated Basic
Memory config/data. Model-backed tests require available provider access and incur usage.
Use `/reload` for interactive development once the extension is configured; a full restart
is not normally required. Do not install extensions into the user's live config implicitly.

## References

Live website index: https://docs.basicmemory.com/llms.txt

Live Markdown read during planning:
- https://docs.basicmemory.com/raw/reference/ai-assistant-guide.md
- https://docs.basicmemory.com/raw/integrations/harness-capture.md

Additional documentation reviewed from the sibling website source checkout:
- https://docs.basicmemory.com/raw/reference/cli-reference.md
- https://docs.basicmemory.com/raw/cloud/cloud-cli.md
- https://docs.basicmemory.com/raw/cloud/routing.md
- https://docs.basicmemory.com/raw/integrations/hermes.md
- https://docs.basicmemory.com/raw/integrations/openclaw.md

Implementation references: `integrations/openclaw/`, `integrations/hermes/`, `skills/`.
MCP adapter: https://github.com/nicobailon/pi-mcp-adapter (`2.32.1` compatibility verified for the initial runtime-registration scenario).
Pi: installed README, extension/package/skill/compaction documentation and examples.
