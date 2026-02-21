# Simplified Local/Cloud Routing

## Context

Basic Memory now uses explicit, project-aware routing without a global cloud-mode toggle.
Routing is determined by command-level flags and project mode, not by a global `cloud_mode` state.

This document is the canonical contract for local/cloud routing behavior in CLI, MCP, and API-adjacent clients.

## Goals

1. Remove global `cloud_mode` from runtime/routing semantics.
2. Keep MCP HTTP/SSE local-only; let stdio honor per-project routing.
3. Make CLI routing explicit and easy to reason about.
4. Support projects that exist in both local and cloud without ambiguity.

## Routing Contract

Routing is resolved in this order:

1. Injected client factory (for composition/integration contexts)
2. Explicit routing override (`--local` / `--cloud` or env vars below)
3. Project-scoped routing (`project.mode`) when a project is known
4. Default local routing

### Routing Environment Variables

- `BASIC_MEMORY_FORCE_LOCAL=true`: force local transport
- `BASIC_MEMORY_FORCE_CLOUD=true`: force cloud proxy transport
- `BASIC_MEMORY_EXPLICIT_ROUTING=true`: marks routing as explicitly chosen for this command

When explicit routing is active, project mode does not override the selected route.

## Config Semantics

- `project.mode` is the only config-based routing signal for project-scoped operations.
- Legacy `cloud_mode` values may be encountered during migration/loading but are not used for routing behavior.
- Normalization saves remove stale `cloud_mode` from `~/.basic-memory/config.json`.

### Example Config

```json
{
  "projects": {
    "main": {
      "path": "/Users/me/basic-memory",
      "mode": "local",
      "local_sync_path": null,
      "bisync_initialized": false,
      "last_sync": null
    },
    "specs": {
      "path": "specs",
      "mode": "cloud",
      "local_sync_path": "/Users/me/dev/specs",
      "bisync_initialized": true,
      "last_sync": "2026-02-06T17:36:38.544153"
    }
  },
  "default_project": "main",
  "cloud_api_key": "bmc_abc123...",
  "cloud_host": "https://cloud.basicmemory.com"
}
```

## Cloud Commands Are Auth-Only

`bm cloud login`, `bm cloud logout`, and `bm cloud status` manage authentication state.

- `bm cloud login`
  - performs OAuth device flow
  - stores/refreshes token material
  - may verify cloud health/subscription
  - does not change routing defaults
- `bm cloud logout`
  - removes stored OAuth session tokens
  - does not change routing defaults
- `bm cloud status`
  - reports auth state (API key, OAuth token validity)
  - runs health checks only when credentials are available

## MCP Transport Routing

### Stdio (default)

`bm mcp --transport stdio` uses natural per-project routing.

- Local-mode projects route through the in-process ASGI transport.
- Cloud-mode projects route to the cloud proxy with Bearer auth (API key).
- No explicit routing env vars are injected by the CLI command.
- Externally-set env vars are honored (e.g. `BASIC_MEMORY_FORCE_CLOUD=true` for cloud deployments).
- Users who need all projects forced local can set `BASIC_MEMORY_FORCE_LOCAL=true` externally.

### HTTP and SSE Transports

`bm mcp --transport streamable-http` and `bm mcp --transport sse` always route locally.

These transports set explicit local routing (`BASIC_MEMORY_FORCE_LOCAL=true` and
`BASIC_MEMORY_EXPLICIT_ROUTING=true`) before starting the server. This prevents cloud
routing regardless of project mode, since HTTP/SSE serve as local API endpoints.

## Project List UX for Dual Presence

Projects may exist in both local and cloud. `bm project list` should display that clearly in one row per logical
project identity, with explicit source/target signals.

Recommended display contract:

1. Keep one row per normalized project name/permalink.
2. Show both local and cloud presence as separate columns/indicators.
3. Show an explicit `MCP (stdio)` target column that always resolves to `local`.
4. Keep CLI route semantics explicit:
   - no flags: default local for non-project commands
   - `--cloud`: force cloud
   - `--local`: force local

## Project LS Targeting

`bm project ls` should clearly identify which project instance is being listed.

Targeting rules:

1. No routing flags: list local project files.
2. `--cloud`: list cloud project files.
3. `--local`: list local project files (explicit override).
4. Output should label the active target (`LOCAL` or `CLOUD`) in heading or status line.

## Runtime Mode

Runtime mode is no longer a cloud/local routing switch for local app flows.

- `resolve_runtime_mode(is_test_env)` resolves to:
  - `TEST` when running in test environment
  - `LOCAL` otherwise
- `RuntimeMode.CLOUD` may remain for compatibility with existing tests/call sites but is not selected by normal local
  runtime resolution.

## Verification Checklist

1. Loading config with legacy `cloud_mode` succeeds.
2. Saving config strips legacy `cloud_mode`.
3. `--local/--cloud` always override per-project mode for that command.
4. No-project + no-flags commands route local by default.
5. `bm cloud login/logout` do not toggle routing behavior.
6. `bm mcp` stdio routes per-project mode; HTTP/SSE remain local-forced.
7. `bm project list` communicates dual local/cloud presence without ambiguity.
8. `bm project ls` output identifies route target explicitly.
