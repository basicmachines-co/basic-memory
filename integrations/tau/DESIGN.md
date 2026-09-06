# Basic Memory for Tau: continuity design

Issue: https://github.com/basicmachines-co/basic-memory/issues/1487
Integration: https://github.com/basicmachines-co/basic-memory/pull/1489
Required host work: https://github.com/huggingface/tau/pull/683

## Product contract

A fresh or compacted session recovers the objective, decisions, unfinished work,
verified findings, and next action through the shared Basic Memory graph. Full
MCP tool access supports that loop; it is not a substitute for it.

## Host dependencies, implemented separately

Stock Tau 0.4.1 only notifies extensions around overflow compaction. Its queued
custom messages run as follow-ups, which can cause an extra model response even
with trigger_turn=False. Its public context cannot read persisted custom receipts
or request a tool-free summary through the active provider.

Tau #683 supplies:

1. Awaited extension start/end notifications around manual, detailed manual,
   threshold and overflow compaction. No-op checks emit nothing. Failure and
   cancellation emit aborted end events. The original context remains available
   until start handlers finish.
2. `context.branch_entries`: deep-copied persisted active-path entries for receipt
   recovery and lineage without session-file scraping.
3. `context.summarize`: bounded tool-free active-model synthesis, no agent turn,
   history mutation, exposed credentials, or detached task.
4. `tau.append_message`: persist idle reference context before the next prompt,
   without queuing another turn.
5. Shutdown/start notifications around in-place tree branches on the same runtime.

The package pins the tested fork SHA until these interfaces are released upstream.
It does not modify installed Tau or pretend #506 is fully closed: that issue's
threshold/manual frontend-iterator/TUI-status work is separate from extension
callback delivery. Persisted-entry notifications are not required; branch snapshots
provide authoritative receipt reconstruction.

## Explicit ownership

`bridge.py` finishes paginated discovery during synchronous setup using a joined
temporary thread/process, because Tau composes tools before session_start. The
probe closes before setup returns. Runtime MCP contexts belong to one async owner
task; calls share them. Close cancels active requests and retires contexts in their
owner task. Restart creates a fresh stop event, including same-runtime tree branches.

`extension.py` registers every advertised tool, preserving schemas and forwarding
arguments unchanged. `results.py` converts native text/images, preserves other
blocks and structured data, and validates write receipts. Routing and auth remain
BM's responsibility; automatic memory requires an explicit project destination.

## Knowledge and receipt flow

`continuity.py` reads public persisted message entries, excluding reasoning,
tools, synthetic summaries, and injected context. The last confirmed handoff plus
new public messages are synthesized in bounded chunks under one checkpoint deadline.
Knowledge capture at settled/compaction/shutdown uses the same source-tip identity;
unchanged state reuses its receipt instead of making redundant model requests.

Before a remote write, append a pending intent containing project, capture id,
kind, source tip, and content digest. After a validated BM result, append a confirmed
receipt with its returned path. Reload/start reconciles pending intents by reading
remote content, never resubmitting writes. A sibling branch can recover the same
source-tip capture by identity and byte digest. Divergent source tips produce
separate snapshots linked to the prior active-branch checkpoint. Transcript notes
are distinct, opt-in, immutable segments; handoffs link their captured sources.

Startup reads confirmed active-branch checkpoints before broader scoped results,
expands the checkpoint's graph neighborhood, and includes shared recent activity.
Filter-only search supplies an epoch after_date to obtain BM's newest-first order
without excluding long-idle modern sessions. Topic search also retrieves shared
coding_session/task/decision notes. The inserted brief is bounded and labeled as
untrusted historical reference, not current repository facts.

## Failure and privacy policy

Automatic-memory failure is visible but does not stop coding. Host observation
hooks are awaited, not veto hooks; a failed write can precede a compaction that
continues. Only a confirmed checkpoint is referenced afterward. Closing always
releases MCP, even if summary generation fails. No shutdown promise applies to
process kills. No ambiguous remote write is retried or overwritten automatically.

Common credential patterns are masked before automatic capture and in generated
output, but arbitrary public-text secrets cannot be reliably detected. Controls
and destination disclosure are part of the privacy boundary. No raw tool payloads,
hidden reasoning, model credentials, or arbitrary server error text are captured.

## Evidence

Tests cover real host registration, persisted sessions, paginated stdio tools,
source-tip deduplication, branch lineage, all compaction paths, write/receipt
failures, cancellation, shutdown, fresh resume, and headless TUI reload. The real-BM
suite proves file writes, reads, searches, transcripts, checkpoints, compaction
reference restoration, reload and resume in temporary local projects. Synthesis
uses deterministic providers, so these tests do not claim live-model quality or
paid/cloud account end-to-end verification.
