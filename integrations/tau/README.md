# Basic Memory for Tau

Basic Memory's structured knowledge workflow, native to Tau: shared schemas,
repository-aware checkpoints, tasks and decisions, observations and relations,
and explicitly routed shared recall. Other agents can find and understand the
same notes using ordinary Basic Memory queries.

Every server-advertised MCP tool is available, with automatic startup recall,
ongoing knowledge capture, awaited pre-compaction checkpoints, optional public
transcripts, and shutdown summaries. Tau supplies the lifecycle; Basic Memory
supplies the shared memory contract.

**Upstream dependency:** [Tau PR #687](https://github.com/huggingface/tau/pull/687).
The isolated environment pins `basicmachines-co/tau` at `d8216af`, including the
single-copy active-branch snapshot fix. Stock Tau 0.4.1 lacks the
required APIs; the extension refuses to load there rather than silently offering
weaker continuity. No installed Tau files are patched.

## Run from this repository

Prerequisites: Python 3.13+, uv, and an installed/configured Basic Memory CLI.

```bash
uv sync --project integrations/tau
uv run --project integrations/tau tau -e ./integrations/tau
```

This uses the pinned Tau fork and MCP 2 in a separate environment. The MCP server
defaults to `bm mcp --transport stdio` from PATH. No extension operation installs
dependencies, creates projects, or changes credentials. Do not load two copies.

For development, `/reload` reads the explicitly loaded source directory. For a
copied install, use `tau install ./integrations/tau` **from the compatible Tau
environment**; update it with `tau install --force ./integrations/tau`, then
`/reload`. Tau's installer does not install dependencies. Wait for an upstream
release containing #687 before using an ordinary released Tau environment.

## Guided setup skill

[Basic Memory setup](skills/basic-memory-setup/SKILL.md) walks through prerequisites,
compatibility, an explicitly chosen destination, tools-only/recall-only/full-continuity
policies, coding/general profiles, approved schema seeding, placement conventions,
configuration validation, and optional approved write/read/schema verification.
It does not choose a team destination, install dependencies, or write notes without
approval. Configuring a destination enables automatic writes by default, so read
and approve the policy before launching the extension.

Tau discovers skills separately from extensions. Neither `tau -e` nor copying an
extension with `tau install` makes this nested skill discoverable. To install the
skill into your user-level Tau skills directory, run from this checkout:

```bash
target="$HOME/.tau/skills/basic-memory-setup"
if [ -e "$target" ] || [ -L "$target" ]; then
  printf 'Setup skill already exists; inspect it before updating.\n'
else
  mkdir -p "$HOME/.tau/skills"
  cp -R integrations/tau/skills/basic-memory-setup "$target"
fi
```

Then `/reload` in Tau and invoke `/skill:basic-memory-setup`, or ask Tau to help
set up Basic Memory. You can also ask an assistant to read the source `SKILL.md`
directly without installing it. The copied skill asks you to locate the integration
checkout; it does not assume its installed directory contains the extension.

If capture is already running, reload first shuts down the old lifecycle, which
can still save using its old configuration. Changing config does not immediately
stop a running session's capture or redirect that final shutdown write.

## Configure the destination and capture policy

Create `~/.tau/basic-memory.json`, choosing an existing Basic Memory project:

```json
{
  "project": "my-memory-project",
  "auto_recall": true,
  "capture_knowledge": true,
  "checkpoint_on_compact": true,
  "summarize_on_shutdown": true,
  "capture_transcript": false
}
```

**Setting a project enables automatic synthesized writes by default.** Summaries
use the active Tau model/provider and its ordinary network routing and billing.
They add model requests and latency. Turn off `capture_knowledge` to summarize
only at compaction/shutdown, or disable those settings too for tools/recall only.

Only user-level configuration is discovered. `TAU_BASIC_MEMORY_CONFIG` can select
an explicit alternate file. Ambient repository files cannot select an executable
or capture destination. Unknown/invalid settings fail validation. Reload changes.

| Setting | Default | Meaning |
| --- | --- | --- |
| `command` | `bm` | Executable, launched directly without a shell |
| `args` | `["mcp", "--transport", "stdio"]` | Server arguments |
| `project` | unset | General-profile automatic-memory destination, including workspace/project routing |
| `read_projects` | `[]` | Up to six explicitly approved read-only recall projects |
| `placement_conventions` | decisions/tasks by topic | Guidance for deliberate notes, separate from checkpoint placement |
| `repositories` | `[]` | User-approved coding profiles keyed by absolute Git checkout root |
| `auto_recall` | `true` | Restore branch checkpoint and relevant shared context on start/reload/resume/branch |
| `capture_knowledge` | `true` | Synthesize new public conversation at `agent_settled` |
| `checkpoint_on_compact` | `true` | Await a checkpoint before manual, threshold, or overflow compaction |
| `summarize_on_shutdown` | `true` | Save outstanding public work before closing/replacing a session |
| `capture_transcript` | `false` | Separate immutable public user/final-assistant message notes |
| `capture_folder` | `tau/transcripts` | Transcript directory within the project |
| `checkpoint_folder` | `tau/checkpoints` | Synthesized handoff directory |
| `timeout_seconds` | `30` | MCP initialization/discovery/call timeout, at most 300 seconds |
| `summary_timeout_seconds` | `60` | Entire checkpoint deadline, including synthesis and persistence, at most 300 seconds |
| `summary_chunk_chars` | `16000` | Public input processed per summary request; previous handoff is also included |
| `recall_chars` | `12000` | Maximum recalled data payload; user placement policy and warning/truncation text are additional |

With no project in the active profile, tools remain available but automatic memory stays off. Tool
arguments are forwarded unchanged; the plugin does not inject its capture project
into arbitrary agent calls. Configure local/cloud routing and authentication
through Basic Memory. Choosing a cloud or team project sends automatic writes
there; use a team destination only when you intend that disclosure.

## Coding profiles and the shared note contract

The general profile writes `session` notes. For coding, explicitly register a
checkout in the same user-owned config; no repository-local config is read:

```json
{
  "project": null,
  "repositories": [
    {
      "kind": "coding",
      "root": "/absolute/path/to/checkout",
      "repository": "owner/repository",
      "project": "my-memory-project",
      "read_projects": ["team/shared"],
      "checkpoint_folder": "tau/checkpoints",
      "placement_conventions": "Decisions in decisions/, tasks in tasks/. Search before creating notes."
    }
  ]
}
```

A coding profile has its own destination, read sources, and placement settings;
it does not inherit the general destination. The closest approved root wins.
Git must confirm that root before a new coding checkpoint is written; missing Git
history or an unconfigured nested checkout fails visibly, not as a generic note.
Register each worktree explicitly, using the same confirmed `repository` identity
for cross-checkout recall. Controls such as `capture_knowledge` remain global.
An omitted profile destination disables automatic memory in that profile.

Coding checkpoints include actual Git root, branch and SHA plus optional GitHub PR
metadata, timestamps, project, capture method, and `tau_session_id`. PR lookup is
optional when `gh` is missing, unavailable, or times out; malformed successful JSON
is an error. Git reads have a five-second per-command bound and run without a shell.
The model supplies knowledge synthesis, not repository identity. General and coding
notes use the shared Session and Coding Session schemas respectively; transcript
segments remain separate `tau_transcript` notes.

Canonical seeds live in [`../shared/schemas`](../shared/schemas); this package
bundles copies in `schemas/`. Setup asks before writing missing schemas and never
overwrites user-customized definitions. `scripts/sync_memory_schemas.py --check`
and package tests prevent drift between Claude Code, Codex, and Tau bundles.
Existing Tau receipts and old notes remain unchanged; general cwd recall still
finds legacy `coding_session` snapshots. Coding recall requires confirmed repository
metadata rather than pretending old cwd-only notes identify the current repository.

## Continuity lifecycle

- **Start/resume/reload/branch:** reconcile outstanding write receipts by reading,
  restore the latest receipt on the active branch, read its graph neighborhood,
  then retrieve repository-scoped coding checkpoints (cwd-scoped general sessions),
  active tasks and open decisions, followed by configured read-only shared sources
  and topic matches. Coding profiles omit the unscoped recent-session feed; general
  profiles retain broader recent activity. Queries stop when the recall budget is full. References are inserted
  before the next prompt, not queued as an extra model turn. They are untrusted
  historical evidence; the agent must verify live repository state.
- **After work settles:** summarize new public messages together with the prior
  handoff. Chunked synthesis processes the selected public text without silently
  dropping its oldest portion. Oversized model output or the deadline fails
  visibly instead of manufacturing a fallback summary.
- **Before compaction:** finish the same receipt-backed write while original
  context exists. Already-saved state is reused. After successful compaction,
  insert the confirmed checkpoint reference into the new context. Aborted
  compaction does not announce a restored reference.
- **Shutdown/replacement:** summarize any outstanding work, then close MCP even
  when memory fails. No new agent turn or detached background writer is needed.

Knowledge snapshots are ordinary `session` or `coding_session` Markdown notes with
schema-aligned observations and relations, linked to the previous checkpoint and, when enabled,
the captured source messages. Explicit remember workflows search/update existing
knowledge notes. Other agents can read the same graph using normal BM tools.

## Tools and commands

Every discovered tool becomes `bm_<original-name>` with its actual schema and
description. Discovery follows all pages, rejects duplicate names/cursor cycles,
and respects server feature gates. `/reload` refreshes the inventory.

- `/bm-orient [topic]`: retrieve and insert relevant notes without an agent turn.
- `/bm-checkpoint [focus]`: synthesize and persist through the serialized input
  hook; reuse a confirmed checkpoint when the source state has not changed.
- `/bm-remember <text>`: ask the agent to search/update or create connected knowledge.
- `/bm-status`: connection, destination, controls, last checkpoint and failure.

A command's initial acknowledgement is a request, not a save receipt. Checkpoint
success is notified only after a BM write/read reconciliation and a durable Tau
receipt. Tool errors remain errors; non-text/non-image MCP blocks are explicitly
serialized rather than discarded, and structured results remain intact.

## Replay, branches, and failures

A capture identity includes project, session id, kind, and persisted source entry
id. An intent is appended to Tau before a write; confirmation follows a validated
BM receipt. Reload/resume reads these records on the active branch. Sibling
branches sharing a source tip can recover the same immutable capture by identity
and content digest. Divergent tips get separate notes and parent-checkpoint links.

Pending writes are reconciled by reads only, including at startup. Missing,
ambiguous, or changed remote content stays visibly unconfirmed. No ambiguous write
is automatically resubmitted and no existing capture is overwritten. Inspect the
configured destination and BM availability, then `/reload` to reconcile again.
A deliberately abandoned pending intent remains diagnostic rather than being
silently marked successful.

Automatic-memory failures warn rather than stop coding. Tau's observation hooks
are awaited but are not veto hooks: if persistence fails, compaction may continue.
A process kill cannot run shutdown handlers. Neither case is reported as a save.

## Privacy

Raw transcripts are off by default. Only actual persisted public user text and
final assistant text qualify. Hidden reasoning, raw tool arguments/results,
images, custom reference messages, and synthetic compaction summaries are not
captured as conversation segments. Synthesized handoffs use that same public
input, not a hidden tool-output dump.

Common credential forms (private-key blocks, recognized token prefixes, Bearer
values, JWT-shaped strings, and credential assignments) are masked before
synthesis/transcript writes and again in synthesized output. **This is not a
general secret detector.** Unknown secrets in public text may remain. Disable
automatic capture for sensitive conversations; do not rely on a model or a regex
as a privacy boundary. Server stderr and arbitrary background exception payloads
are not echoed into Tau notifications.

## Verification

```bash
just package-check-tau
BM_TAU_TEST_COMMAND="$PWD/.venv/bin/bm" \
  uv run --project integrations/tau pytest -c integrations/tau/pyproject.toml \
  integrations/tau/tests -q
```

Tests exercise real Tau sessions/storage, real stdio MCP processes, all four
compaction entry points, reload, branch/resume, cancellation, receipt failures,
privacy controls, and headless Textual `/reload`. The real-BM interoperability test
seeds shared schemas, validates a Tau coding checkpoint without warnings, finds it
with the hook-style repository query, and recalls Tau/other-agent notes from a
second checkout without recalling them for another repository. The opt-in real-BM tests isolate
HOME/configuration/notes, force local routing, and disable updates, semantic
model downloads, and telemetry. Model behavior is tested with deterministic fake
providers, not a paid live model or production memory project.
