# Pi memory transport decision — initial contracts

## Status

Accepted for the first implementation slice: ship a thin Pi package that supports Basic Memory CLI mode directly and MCP mode through `pi-mcp-adapter` when users install it.

## Findings

### Pi package and extension surface

Pi 0.85.1 supports package resources through `package.json` `pi.extensions` and `pi.skills`. Extensions can use `session_start`, `before_agent_start`, `agent_settled`, `session_before_compact`, `session_compact`, `session_shutdown`, reload, commands, tools, and custom session entries. Long-lived resources must start after `session_start` or on demand and must be cleaned up from `session_shutdown`.

### MCP adapter

`pi-mcp-adapter` 2.32.1 is MIT licensed, requires Node >=20, and exports `createMcpAdapter`, `registerMcpServer`, status events, runtime registration events, and public types. It is a Pi package with `pi.extensions: ["./index.ts"]`.

The supported integration path for another extension is the public runtime registration API or equivalent shared event bus:

- event: `pi-mcp-adapter:runtime-register:v1`
- version: `1`
- request fields: `name`, `definition`, `result`
- registrations are session/runtime scoped and never persisted
- duplicate names fail closed
- runtime servers are proxy-only (`directTools: false`)
- disposal closes the server and removes lifecycle state

This is enough for a Basic Memory Pi extension to register a Basic Memory MCP server without importing private adapter internals. If the adapter is absent, registration fails visibly.

### Basic Memory CLI contracts verified locally

Verified with isolated `BASIC_MEMORY_HOME` and `BASIC_MEMORY_CONFIG_DIR`, using `bm` from `/Users/phernandez/dev/basicmachines/basic-memory/.venv/bin/bm`:

- `bm project list --json` returns structured project data.
- `bm project add NAME PATH --default --local --no-wait` creates a local project without changing the user's real config when env vars are isolated.
- `bm tool write-note` accepts content from stdin when `--content` is omitted.
- `bm tool write-note` returns JSON by default in a piped/non-interactive context.
- `bm tool write-note --overwrite` updates an existing note.
- conflicting `write-note` without `--overwrite` exits `1` and returns a structured `NOTE_ALREADY_EXISTS` JSON payload.
- `bm tool read-note --plain` returns undecorated content.
- `bm tool read-note --json` and `bm tool search-notes --json` return structured payloads.
- `--project`, `--project-id`, `--local`, and `--cloud` are available on read/search/write commands.

The first write may trigger local embedding model download, which affects cold latency and should be measured separately from warm CLI overhead.

### Existing hook reuse

`bm hook` now supports a Pi harness profile for automatic lifecycle continuity. Reusable concepts:

- fail-open lifecycle behavior;
- strict JSON booleans for capture settings;
- explicit primary project mapping;
- bounded session-start briefs;
- graph-derived recall fenced as reference data, not instructions;
- lifecycle envelopes separate from durable knowledge;
- checkpoint prompts separate from raw transcript capture.

Pi still must not invoke `bm hook --harness codex|claude`. The package owns Pi event wiring, while `bm hook --harness pi` owns the shared Basic Memory lifecycle semantics: bounded recall, fenced reference data, explicit project routing, predictable `pi/sessions` checkpoint placement, and lifecycle trace distinct from authored knowledge. Users can set `useHookFlow: false` to fall back to the package's direct CLI search/write path.

## Initial defaults

- CLI mode is the no-extra-dependency default.
- MCP mode is opt-in and requires `pi-mcp-adapter` to be installed by the user.
- Project selection is explicit; the extension must not mutate the user's global Basic Memory default.
- Hook flow is enabled by default for automatic lifecycle behavior, with `useHookFlow: false` as the escape hatch.
- Auto-recall and auto-capture default on for an opinionated first-run experience.
- Without an explicit Pi project mapping, recall emits setup guidance and hook-backed capture has no write destination, so it does not silently mutate the user's ambient default project.
- Explicit `/bm-recall` and `/bm-capture` commands remain available for deliberate one-off use.

## Open questions

- Whether explicit `/bm-capture <title>` should also route through a hook verb or remain direct so user-supplied titles keep conflict-safe write semantics.
- Warm/cold latency comparison between direct CLI calls, hook-backed CLI lifecycle calls, and adapter MCP proxy calls.
