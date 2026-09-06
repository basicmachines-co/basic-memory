# Basic Memory for Pi

Basic Memory for Pi gives Pi durable continuity: capture a working thread, start fresh later, and recall the decision, rationale, blocker, and next step from a real Basic Memory note.

## Install from a checkout

```bash
pi install /path/to/basic-memory/integrations/pi
```

The package loads:

- `extensions/index.ts` — `/bm-status`, `/bm-recall`, `/bm-capture`, `bm_recall`, and `bm_capture`.
- `skills/basic-memory-pi/` — Pi-aware guidance for `bm_recall` and `bm_capture`.
- `skill-references/` — bundled canonical Basic Memory references as `REFERENCE.md` files. See [SKILLS.md](./SKILLS.md).

## Configuration

Create `.pi/basic-memory.json` in a trusted project:

```json
{
  "transport": "cli",
  "project": "main",
  "captureFolder": "pi/sessions",
  "autoRecall": false,
  "autoCapture": false
}
```

Keys:

- `transport`: `cli` or `mcp`. CLI is the default and needs only `bm` on PATH.
- `bmPath`: path to the Basic Memory CLI, default `bm`.
- `project` / `projectId`: explicit Basic Memory routing. `projectId` wins when set.
- `captureFolder`: folder for Pi session checkpoints, default `pi/sessions`.
- `recallTimeframe`: search window for recalls, default `7d`.
- `autoRecall`: inject one bounded recall before the first agent turn, default `false`.
- `autoCapture`: capture after settled turns, default `false`.
- `captureMinChars`: minimum session text before auto-capture, default `80`.
- `mcpServerName`: runtime MCP server name in MCP mode, default `basic-memory`.

The extension never changes the user's global Basic Memory default project.

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

The package exposes `basic-memory-pi` as the active Pi skill. It uses Pi's available `bm_recall` and `bm_capture` tools and keeps canonical Basic Memory skill text as `REFERENCE.md` files rather than active `SKILL.md` files that assume direct MCP tool names are present.

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
| Basic Memory CLI | 0.23.2 | Requires `bm tool write-note/read-note/search-notes` JSON/plain modes and project routing flags. |
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

Automatic recall and capture are disabled by default. Use explicit `/bm-recall` and `/bm-capture` first, then opt into automation deliberately in `.pi/basic-memory.json`.

Recalled notes are fenced as reference data, not instructions. Captures are synthesized working-thread checkpoints, not raw transcript dumps.
