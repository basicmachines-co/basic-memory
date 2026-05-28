# hermes-basic-memory

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)

Hermes Memory Provider plugin that gives [Hermes Agent](https://github.com/NousResearch/hermes-agent) a persistent knowledge graph backed by [Basic Memory](https://github.com/basicmachines-co/basic-memory).

The plugin replaces Hermes's "no external memory provider" with a real graph: search-before-answer recall, per-turn capture, end-of-session summaries, and ten `bm_*` tools the agent can call directly. Local mode by default; one CLI flip switches to true cloud routing through Basic Memory Cloud.

## Install

```bash
hermes plugins install basicmachines-co/basic-memory --path integrations/hermes
```

Then activate it in `~/.hermes/config.yaml`:

```yaml
memory:
  provider: basic-memory
```

If you run the gateway, restart it (`hermes gateway restart`). Done.

If your installed Hermes build does not support `--path`, use the final deprecated `basicmachines-co/hermes-basic-memory` pointer release until Hermes subpath installs are available. Ongoing development now lives in [`basic-memory/integrations/hermes`](https://github.com/basicmachines-co/basic-memory/tree/main/integrations/hermes).

The plugin self-installs the `basic-memory` CLI on first init via `uv tool install basic-memory` (one-time ~10s pause if it isn't already present). The bm binary lands at `~/.local/bin/bm` — the same location a manual `uv tool install basic-memory` would produce, so a later manual install or upgrade is a no-op rather than a second install.

### Prerequisites

- [Hermes Agent](https://github.com/NousResearch/hermes-agent)
- [`uv`](https://docs.astral.sh/uv/) on PATH (used for the bootstrap install)
- The `mcp` Python package in the Hermes venv. If `hermes plugins install` doesn't auto-install it (it follows `pip_dependencies` in `plugin.yaml`), run:
  ```bash
  uv pip install --python ~/.hermes/hermes-agent/venv/bin/python mcp
  ```

### Verify

```bash
hermes memory status
```

Expected:
```
  Provider:  basic-memory
  Plugin:    installed ✓
  Status:    available ✓
```

## What the agent gets

Ten tools (curated subset of Basic Memory's MCP surface):

| Tool | Use |
|---|---|
| `bm_search` | Semantic + full-text search; **call this before answering** |
| `bm_read` | Fetch a note by title, permalink, or `memory://` URL |
| `bm_write` | Create a new note (capture decisions, meeting notes, insights) |
| `bm_edit` | Append, prepend, find/replace, replace-section |
| `bm_context` | Navigate via `memory://` URLs to find related notes |
| `bm_delete` | Delete a note |
| `bm_move` | Move a note to a different folder |
| `bm_recent` | List notes updated recently (default `7d`; accepts natural-language timeframes) |
| `bm_projects` | List available projects with their UUIDs (for cross-project routing) |
| `bm_workspaces` | List Basic Memory Cloud workspaces |

Every read/write tool also accepts optional `project` / `project_id` for per-call routing — write or read against a project other than the configured one without reconfiguring the plugin.

Plus automatic capture:
- **Per turn**: every user/assistant exchange appends to a running session-transcript note
- **End of session**: a separate summary note is written, linked back to the transcript via a `summary_of` relation

A bundled skill (`skill:view basic-memory:basic-memory`) gives the agent a longer reference doc on top of the always-on `system_prompt_block`.

## Slash commands

For direct, in-session use without going through the agent (requires Hermes ≥ v0.11.0):

| Command | Use |
|---|---|
| `/bm-search <query>` | Search the knowledge graph; returns compact title/permalink/preview rows. |
| `/bm-read <identifier>` | Read a note by title, permalink, or `memory://` URL. |
| `/bm-context <identifier>` | Build context for a note (target + related). |
| `/bm-recent [timeframe]` | Recently updated notes. Default `7d`; accepts `"2 weeks"`, `"yesterday"`, etc. |
| `/bm-status` | Plugin/provider state: mode, project, capture flags, bm CLI path. |
| `/bm-remember <text>` | Capture a quick note. Title = first line (≤80 chars), folder = `remember_folder` (default `bm-remember`), tagged `manual-capture`. |
| `/bm-project` | List all known projects; the active one is marked. |
| `/bm-workspace` | List BM Cloud workspaces. Cloud mode only — prints an explanatory line in local mode. |

Examples:

```text
/bm-search Q3 OKRs
/bm-read decisions/auth-rewrite
/bm-recent yesterday
/bm-remember Reminder: switch the staging job to the new image after the rebase lands.
```

`/bm-project` and `/bm-workspace` are read-only in 0.2.0 — mid-session switching is intentionally not supported because auto-capture would otherwise land in the wrong place. Tracked as a follow-up.

### Known issue: `/bm-*` commands may not appear in some Hermes gateway builds

Plugin v0.2.0 and later register the commands above, but some Hermes Agent gateway builds do not discover slash commands contributed by an **exclusive memory-provider plugin** during startup. The symptoms are:

- the memory tools work for the agent (`bm_search`, `bm_read`, etc.);
- `hermes memory status` shows `Provider: basic-memory` and `Status: available`; but
- Discord/native slash command pickers do not show `/bm-search`, `/bm-read`, `/bm-context`, and the other `/bm-*` commands after `hermes gateway restart`.

This is a Hermes Agent plugin-discovery issue, not a Basic Memory runtime issue. It is tracked upstream in [NousResearch/hermes-agent#23603](https://github.com/NousResearch/hermes-agent/issues/23603). Updating the Basic Memory plugin alone cannot fix affected gateway startup discovery; Hermes Agent itself must include or receive the compatibility patch. Until the upstream Hermes fix is available in your installed Hermes version, use one of these workarounds:

1. apply the Hermes Agent-side patch described in [MONKEYPATCH.md](MONKEYPATCH.md), which includes compatibility notes for Hermes Agent v0.13.x and v0.14.0; or
2. use the agent tools directly (`bm_search`, `bm_read`, `bm_recent`, etc.) instead of native slash commands.

After applying an updated or patched Hermes build, restart the gateway so Discord/native slash commands are re-synced:

```bash
hermes gateway restart
```

If Discord still does not show the commands immediately, type `/bm` directly or reload the Discord client; global command propagation can lag briefly.

## Configuration

Defaults are reasonable for local use:

| Key | Default | Notes |
|---|---|---|
| `mode` | `local` | `local` (in-process) or `cloud` (route through BM Cloud API) |
| `project` | `hermes-memory` | BM project name |
| `project_path` | `~/hermes-memory/` | Local mode only — where session notes land |
| `capture_folder` | `hermes-sessions` | Folder within the project for session notes |
| `capture_per_turn` | `true` | Append every turn to a session transcript |
| `capture_session_end` | `true` | Write a summary note when the session ends |
| `remember_folder` | `bm-remember` | Folder where `/bm-remember` captures land (kept separate from session transcripts) |

To override, write `~/.hermes/basic-memory.json` or run `hermes memory setup basic-memory`:

```json
{
  "mode": "local",
  "project": "hermes-memory",
  "project_path": "~/hermes-memory/",
  "capture_per_turn": true,
  "capture_session_end": true,
  "capture_folder": "hermes-sessions",
  "remember_folder": "bm-remember"
}
```

In local mode the plugin auto-creates the BM project on first init via `bm project add`. In cloud mode it doesn't — you create the cloud-routed project yourself (see below) and the plugin verifies it's registered before initializing.

### Cloud mode

When `mode: cloud`, tool calls route directly through the BM cloud API — no local file mirror, no bisync. You set this up once with the BM CLI:

```bash
# Authenticate (OAuth) or save an API key
bm cloud login                     # OAuth — interactive
# OR for headless/automation:
bm cloud create-key "hermes"
bm cloud set-key bmc_...

# Create the project, then flip it to cloud routing.
# --workspace is required if you belong to more than one workspace
# (otherwise BM auto-resolves the only one available).
bm project add hermes-memory-cloud
bm project set-cloud hermes-memory-cloud --workspace Personal

# Point the plugin at it
cat > ~/.hermes/basic-memory.json <<EOF
{
  "mode": "cloud",
  "project": "hermes-memory-cloud",
  "capture_per_turn": true,
  "capture_session_end": true,
  "capture_folder": "hermes-sessions"
}
EOF

hermes gateway restart
```

Tool calls now route from `bm mcp` → `<cloud_host>/proxy` over HTTPS using your OAuth token (or API key). Notes never touch local disk.

**Don't confuse cloud mode with `bm cloud bisync`.** Bisync is rclone-style two-way file sync between a *local* project and cloud storage, intended for keeping local working copies. For agent-driven capture you want true cloud routing (`set-cloud`), not bisync.

## Updating / removing

```bash
hermes plugins update basic-memory
hermes plugins remove basic-memory     # then revert memory.provider in config.yaml
```

## Foot-guns

- **`<memory-context>` tags in notes**: Hermes's streaming output scrubber strips literal `<memory-context>...</memory-context>` blocks from assistant text. If a note contains those tags and the assistant echoes the body verbatim, the echoed copy gets eaten mid-stream. Tool results inbound are unaffected. Avoid those tags in BM notes; if you must include them, fence in a code block.
- **Single external provider**: Hermes accepts only one external memory provider at a time. Activating basic-memory displaces any other.
- **CLI cold start**: `hermes -z ...` invocations spawn `bm mcp` per run (~2-5s). Long-running gateway sessions amortize this.
- **Multiple cloud workspaces**: if your BM Cloud account belongs to more than one workspace, `bm project set-cloud` must be invoked with `--workspace <name>`. Otherwise tool calls fail with "Multiple workspaces are available".

## Development

The plugin is a single-file Python module at `__init__.py`. The Hermes plugin loader expects `register(ctx)` and grep-detects either `register_memory_provider` or `MemoryProvider` in the file.

For local development (point Hermes at your working tree instead of going through `hermes plugins install`):

```bash
git clone https://github.com/basicmachines-co/basic-memory ~/code/basic-memory
mkdir -p ~/.hermes/plugins
ln -snf ~/code/basic-memory/integrations/hermes ~/.hermes/plugins/basic-memory
```

### Running tests

```bash
# From the monorepo root
just package-check-hermes

# Or from integrations/hermes
just check

# Unit tests (fast, hermetic — no Hermes or bm required)
uv run --with pytest pytest

# Integration tests (gated — exercise every tool against a real bm MCP server)
BM_INTEGRATION=1 uv run --with pytest --with mcp pytest tests/test_integration.py
```

The unit suite stubs out Hermes-internal imports (`agent.memory_provider`, `tools.registry`) so it runs without a Hermes install. `mcp` is optional at unit-test time — its absence just makes `is_available()` return False, which the tests verify.

Integration tests require `BM_INTEGRATION=1`, `bm` CLI on PATH, and `mcp` Python package importable. Each session creates a unique throwaway BM project (under `tempfile.mkdtemp`) and removes it on teardown, so they never touch your real BM projects.

## License

AGPL-3.0-or-later, matching [basic-memory](https://github.com/basicmachines-co/basic-memory). See [LICENSE](LICENSE).
