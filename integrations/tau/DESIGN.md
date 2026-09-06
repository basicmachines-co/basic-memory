# Basic Memory for Tau: implementation design

Tracked in https://github.com/basicmachines-co/basic-memory/issues/1487.

## Product contract

Continuity is the product: a fresh or compacted session recovers the objective,
decisions, unfinished work, verified results, and next action. Full MCP tool access
is infrastructure for that loop, not the whole integration.

Use the configured Basic Memory projects and shared graph rather than a Tau-only
knowledge format. Automatic recall, ongoing knowledge capture, configurable
transcripts, and compaction checkpoints complement explicit commands.

## Verified host interfaces

Inspected installed tau-ai 0.4.1 on 2026-09-05:

- `setup(tau)` is synchronous; tools are composed into the harness before session
  start. Live MCP discovery therefore must complete before tool composition, not
  register tools belatedly in a session-start callback.
- `ExtensionAPI` supports tool/command registration, prompt sections, event
  subscriptions, custom messages, and extension-owned session entries.
- `ExtensionContext.transcript` exposes deep-copied active-path messages, with
  existing compaction summaries folded into user messages.
- Session-start and shutdown events distinguish startup, reload, new, resume,
  branch, and quit.
- `/reload` rebuilds the extension runtime. The integration must close the outgoing
  MCP connection and avoid duplicate capture across replacement generations.

## Compaction blocker

The installed API advertises `compaction_start`, `compaction_end`, and
`entry_appended`, but declarations do not guarantee delivery:

- `CodingSession.prompt` emits awaited start/end notifications for overflow recovery.
- `compact`, `compact_detailed`, and `_maybe_auto_compact` do not emit those
  notifications around normal/manual compaction.
- `_append_compaction` persists a compaction and leaf, then replaces active context.
- `_append_session_entry` writes storage without emitting `entry_appended`.

Do not present overflow-only checkpointing as complete compaction coverage. Resolve
this through a supported upstream lifecycle seam, not monkey-patching installed
Python or scraping session files. Check upstream source before proposing a patch:
https://github.com/huggingface/tau.

## Bridge design constraints

Hermes uses a dedicated thread/async loop to own its persistent MCP session because
its host interface is synchronous. OpenClaw uses a persistent asynchronous client.
Tau requires synchronous discovery during setup but asynchronous execution after
startup. Choose the smallest lifecycle-owned bridge that satisfies both; prove
cleanup for failed setup as well as normal shutdown before adopting a threaded
actor. A subprocess per call is not equivalent to a persistent connection.

Discover every tools/list page, retain schemas, namespace host names, and forward
original MCP names/arguments unchanged. Do not auto-enable server feature gates.
Preserve content and error semantics. No blind retries of ambiguous writes.

## Verification plan

1. Real Tau loader registration against a deterministic paginated MCP server.
2. Tool forwarding and text/image/structured/error result conversion.
3. Startup failure, cancellation, shutdown, and repeated reload without leaked children.
4. Bounded automatic recall with verified note references.
5. Capture controls, explicit destination, replay deduplication, branch/resume handling.
6. Manual, threshold, and overflow compaction checkpoints, including failure reporting.
7. Real Basic Memory temporary-project write/read/search and fresh-session recovery.
8. Root package checks, static checks, and CI wiring before the PR is ready.

## Preview implementation

`bridge.py` performs joined, short-lived discovery during setup and owns the
persistent runtime MCP session in one async task. `extension.py` registers every
advertised tool, injects bounded startup recall, optionally captures public
messages, and queues explicit or overflow-triggered checkpoint requests.

The isolated real BM test verifies write/read/search and a fresh runtime's recall.
The full contract remains open: normal/manual compaction events are tracked by
https://github.com/huggingface/tau/issues/506, and durable replay reconciliation,
branch lineage, richer task-oriented recall, and shutdown summaries are follow-ups.
Checkpoint requests are not acknowledgements of persisted notes. See README.md
for exact preview limits and configuration.
