# Basic Memory for Tau

A Tau extension that exposes **every tool advertised by your Basic Memory MCP
server**, recalls recent work automatically, and encourages connected, durable
knowledge capture throughout a conversation.

**Preview:** tools and startup recall are implemented. Tau 0.4.1 only emits
compaction events for overflow recovery. Automatic checkpointing currently queues
an agent-authored checkpoint request after successful overflow compaction; it is
not a guaranteed pre-compaction write. Normal/manual compaction coverage depends
on [Tau #506](https://github.com/huggingface/tau/issues/506). Use `/bm-checkpoint`
before manual compaction. This preview does not yet satisfy the full continuity
contract in [Basic Memory #1487](https://github.com/basicmachines-co/basic-memory/issues/1487).

## Run from this repository

Prerequisites: Python 3.13+, uv, and an installed/configured Basic Memory CLI.
No plugin operation installs dependencies, creates projects, or changes credentials.

From the Basic Memory repository root:

```bash
uv sync --project integrations/tau
uv run --project integrations/tau tau -e ./integrations/tau
```

The isolated environment includes Tau 0.4.1 and MCP 2. It does not change your
installed Tau package. Configure Basic Memory itself using its normal CLI; the
plugin defaults to launching `bm mcp --transport stdio` from PATH.

For an existing Tau installation, the MCP 2 Python package must be available in
**Tau's** environment, not only Basic Memory's. Once that prerequisite is satisfied:

```bash
tau install ./integrations/tau
```

Tau copies the directory into its user extension directory. To update a copied
installation, explicitly reinstall with `tau install --force ./integrations/tau`.
Use `/reload` afterward. When developing with `-e`, `/reload` reads source changes
directly. The plugin closes the outgoing MCP session and rediscoveries use a fresh
runtime. Do not install and explicitly load two copies simultaneously.

## Configuration

Create `~/.tau/basic-memory.json`:

```json
{
  "project": "my-memory-project",
  "auto_recall": true,
  "checkpoint_on_compact": true,
  "capture_transcript": false
}
```

Only user-level configuration is discovered. `TAU_BASIC_MEMORY_CONFIG` may select
an explicit alternate file. Repository configuration is not read implicitly:
a project file must not silently change the executable or capture destination.
Malformed/unknown settings fail validation. Reload after changing settings.

| Setting | Default | Meaning |
| --- | --- | --- |
| `command` | `bm` | Executable, passed directly without a shell |
| `args` | `["mcp", "--transport", "stdio"]` | MCP server arguments |
| `project` | unset | Destination for automatic memory and command workflows |
| `auto_recall` | `true` | Inject recent activity on start/resume/reload when a project is set |
| `checkpoint_on_compact` | `true` | Queue a checkpoint request on a successful compaction-end event |
| `capture_transcript` | `false` | Opt in to capture public user/final-assistant text |
| `capture_folder` | `tau/transcripts` | Directory inside the capture project |
| `timeout_seconds` | `30` | Initialization/discovery/call timeout, up to 300 seconds |
| `recall_chars` | `12000` | Maximum recall payload characters |

With no project configured, all server tools remain available, but automatic
recall/capture/checkpoint requests stay off. Agent tool calls retain their original
arguments; the plugin does not inject its capture project into arbitrary calls.
Use Basic Memory project/workspace discovery to choose an explicit destination.

Local and cloud routing belongs to Basic Memory. Configure a project's cloud
route and authentication through the BM CLI, then select that project here.
A cloud-routed capture project sends captured content to the cloud. Only configure
a team destination if you intend automatic writes there.

## Tools and workflows

Every tool becomes `bm_<original-name>` with its actual MCP input schema and
description. Discovery follows pagination, rejects duplicate names/cursor cycles,
and respects server feature gates. No hardcoded tool subset. The inventory is a
startup snapshot; `/reload` rediscovers it after changing server configuration.

- `/bm-orient [topic]`: ask the agent to search/read connected context and verify live state.
- `/bm-checkpoint [focus]`: ask the agent to write a synthesized handoff with decisions,
  verification, blockers, and a next action, citing the successful write result.
- `/bm-remember <text>`: ask the agent to find/update or create an appropriate note.
- `/bm-status`: connection, capture settings, compaction limitation, and latest background error.

Commands request an agent workflow; they do **not** themselves certify a save.
Recall injects a bounded recent-activity brief (last seven days, ten items), not an
exhaustive reconstruction of older active tasks. Use orientation/search to expand.
Prompt guidance encourages ongoing decision capture and shared graph navigation.

## Capture and privacy

Transcript capture is separate from synthesized knowledge. When enabled, each
public user message and final assistant response produces an immutable
`tau_transcript` note. Session id, message timestamp, role, and a content digest
identify the message. Thinking, tool calls/results, images, and custom extension
messages are excluded. This is not a complete raw execution log.

**Public text can still contain private information or pasted secrets.** The
plugin is not a secret-redaction system. Leave transcript capture off for sensitive
sessions. Read any automatically captured notes using ordinary Basic Memory tools.

Within a runtime, successful captures are deduplicated. Deterministic titles and
`overwrite=false` prevent a replay from replacing existing content. Replaying an
already-written message after reload reports a conflict rather than assuming a
new save; durable replay reconciliation and cross-branch lineage are follow-ups.
Capture only marks success after validating a structured BM write receipt.

No raw transcript is uploaded by default. There is no automatic shutdown summary
yet. A compaction checkpoint request is synthesized by the active agent and may
require another turn; closing before it executes does not save a checkpoint.

## Lifecycle and failures

Tau's synchronous setup requires tool schemas before session start. A temporary
MCP discovery process runs in a joined thread and closes before setup returns.
Session start then creates a persistent MCP process, owned by one asynchronous
task. Tool calls share that connection; shutdown cancels active calls and closes
its contexts in the owning task. No background daemon thread or automatic reconnect.

Failed calls are not retried automatically, especially writes whose result is
ambiguous. MCP errors raise Tau tool errors. Structured content is retained in
results; text/images are native Tau blocks, while other MCP blocks are explicitly
serialized as text rather than discarded. Server stderr is suppressed to avoid
leaking arbitrary logs into Tau; inspect BM's own logs when diagnosing startup.

Background recall/capture failures are warnings, not success and not a reason to
stop the coding session. `/bm-status` preserves the last failure. Check prerequisites,
project routing, and configuration, then `/reload` to reconnect.

## Development

From the repository root:

```bash
just package-check-tau
```

For the real, isolated BM test, first prepare the root environment with `uv sync`, then:

```bash
BM_TAU_TEST_COMMAND="$PWD/.venv/bin/bm" \
  uv run --project integrations/tau pytest -c integrations/tau/pyproject.toml \
  integrations/tau/tests/test_integration.py -q
```

The integration test uses temporary HOME, configuration, and note directories,
forces local routing, and disables semantic downloads, auto-updates, and telemetry.
It writes/reads/searches a note, then loads a separate Tau runtime to recall it.
No live model or production project is used.
