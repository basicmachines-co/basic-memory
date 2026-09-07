# Basic Memory for Pi

Basic Memory for Pi gives Pi durable continuity: capture a working thread, start fresh later, and recall the decision, rationale, blocker, and next step from a real Basic Memory note.

## Install from Basic Memory

```bash
bm install pi --dry-run
bm install pi --yes
```

This copies the packaged Pi resources into `~/.pi/agent/packages/basic-memory` and registers them with `pi install`. Use `--local` to register the package in the current workspace's `.pi/settings.json` instead.

## Install from a checkout

```bash
pi install /path/to/basic-memory/integrations/pi
```

The package loads:

- `extensions/index.ts` — `/bm-status`, `/bm-recall`, `/bm-capture`, `bm_recall`, and `bm_capture`.
- `skills/basic-memory-pi/` — Pi-aware guidance for `bm_recall` and `bm_capture`.
- `skills/basic-memory-pi-setup/` — setup guidance for `.pi/basic-memory.json`.
- `skill-references/` — bundled canonical Basic Memory references as `REFERENCE.md` files. See [SKILLS.md](./SKILLS.md).

## Configuration

Create `.pi/basic-memory.json` in a trusted project:

```json
{
  "transport": "cli",
  "project": "main",
  "captureFolder": "pi/sessions",
  "useHookFlow": true,
  "autoRecall": true,
  "autoCapture": true
}
```

Keys:

- `transport`: `cli` or `mcp`. CLI is the default and needs only `bm` on PATH.
- `bmPath`: path to the Basic Memory CLI, default `bm`.
- `bmCommand`: optional argv array that overrides `bmPath` when the CLI needs a wrapper or prefix.
- `project` / `projectId`: explicit Basic Memory routing. `projectId` wins when set.
- `captureFolder`: folder for Pi session checkpoints, default `pi/sessions`.
- `recallTimeframe`: search window for recalls, default `7d`.
- `useHookFlow`: use `bm hook --harness pi` for automatic lifecycle recall/capture, default `true`.
- `autoRecall`: inject one bounded recall before the first agent turn, default `true`.
- `autoCapture`: capture after settled turns and before compaction, default `true`.
- `captureMinChars`: minimum session text before auto-capture, default `80`.
- `mcpServerName`: runtime MCP server name in MCP mode, default `basic-memory`.

The extension never changes the user's global Basic Memory default project.

For normal installs, leave `bmPath` alone and make sure `bm` is on Pi's PATH. For branch/local development, use `bmCommand` so Pi can run Basic Memory through `uv` without depending on the workspace's shell aliases:

```json
{
  "bmCommand": ["uv", "run", "--project", "/path/to/basic-memory", "basic-memory"]
}
```

`bmCommand` is passed as argv, not through a shell. The first item is the executable and the remaining items are prepended before the plugin's `bm` arguments.

## Commands and tools

- `/bm-status` — show effective package settings.
- `/bm-recall [topic]` — search recent Pi checkpoints and inject fenced reference data.
- `/bm-capture [title]` — write the current working thread as a `pi_session` note.
- `bm_recall` — LLM-callable recall tool.
- `bm_capture` — LLM-callable capture tool.

## MCP mode

MCP mode uses the existing `pi-mcp-adapter`; it does not implement a new MCP host.

```bash
pi install npm:pi-mcp-adapter
```

Then set:

```json
{ "transport": "mcp" }
```

On `session_start`, the extension registers a session-scoped Basic Memory MCP server through the adapter's public runtime registration event. If the adapter is not installed, Pi remains usable and the extension reports the missing dependency.

## Basic Memory skills

The package exposes `basic-memory-pi` and `basic-memory-pi-setup` as active Pi skills. They use Pi's available commands/tools and keep canonical Basic Memory skill text as `REFERENCE.md` files rather than active `SKILL.md` files that assume direct MCP tool names are present.

The package bundles this focused continuity reference set from the monorepo's canonical `skills/` source:

- `memory-notes`
- `memory-capture`
- `memory-continue`
- `memory-tasks`

Maintainers refresh the bundled references with `npm run fetch-skills`; package checks run that and fail if the generated references are not committed before packing. For local development you can also point Pi directly at the monorepo `skills/` directory, but published installs should use the Pi-aware skill plus bundled references so the package is self-contained.

## Supported versions

| Component | Minimum tested version | Notes |
| --- | --- | --- |
| Pi | 0.85.1 | Required extension, package, RPC, and runtime event APIs were verified on this version. |
| Basic Memory CLI | package-matched release | Requires `bm hook --harness pi` for default lifecycle automation; set `useHookFlow: false` to use direct `bm tool write-note/read-note/search-notes` JSON/plain modes. |
| pi-mcp-adapter | 2.32.1 | Required only for `transport: "mcp"`; runtime registration API verified. |
| Node.js | 22.19.0 | Matches the installed Pi package engine floor used by maintainer checks. |

## Maintainer test and package checks

From this directory:

```bash
npm ci --ignore-scripts
npm run fetch-skills
npm run check-types
npm test
npm pack --dry-run
```

From the monorepo root:

```bash
just package-check-pi
```

Model-backed end-to-end runs should use temporary `BASIC_MEMORY_HOME`, `BASIC_MEMORY_CONFIG_DIR`, Pi session directories, and throwaway Basic Memory projects. See `../../docs/PI_MEMORY_E2E_RESULTS.md` for the latest manual E2E evidence.

## Privacy defaults

Automatic recall and capture are enabled by default so a configured project gets continuity immediately. With no explicit `.pi/basic-memory.json` project mapping, recall shows setup guidance and hook-backed capture has no write destination, so it does not silently write to an ambient default project.

The package uses the shared `bm hook --harness pi` flow by default so Pi follows the same predictable Basic Memory lifecycle contract as other agent harnesses. Set `"autoRecall": false`, `"autoCapture": false`, or `"useHookFlow": false` to make the behavior quieter.

Recalled notes are fenced as reference data, not instructions. Captures are synthesized working-thread checkpoints under `pi/sessions/`, not raw transcript dumps.
