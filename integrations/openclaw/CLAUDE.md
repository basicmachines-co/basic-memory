# CLAUDE.md

This file provides guidance to Claude Code when working with the OpenClaw package inside the Basic Memory monorepo.

## Project Overview

`@basicmemory/openclaw-basic-memory` is a TypeScript OpenClaw plugin that integrates [Basic Memory](https://github.com/basicmachines-co/basic-memory) with the OpenClaw agent framework. It lives under `integrations/openclaw/` in the monorepo, manages a persistent MCP stdio session to a `bm mcp` process, exposes 14 agent tools (including workspace/project management and cross-project operations), composited memory search/get providers, slash commands, CLI commands, and optional auto-capture of conversations.

## Development Commands

```bash
# Install dependencies (uses Bun)
bun install

# Run all unit tests (Bun native test runner)
bun test

# Run a single test file
bun test tools/search-notes.test.ts

# Integration tests (requires basic-memory CLI installed)
bun run test:int

# Type checking (no emit)
bun run check-types

# Lint (Biome)
bun run lint

# Lint + auto-fix
bun run lint:fix

# All quality checks (fetch skills + type-check + lint + build + tests)
just check

# Release readiness (check + npm pack dry-run)
just release-check
```

## Architecture

### Plugin Lifecycle (`index.ts`)

The default export is an OpenClaw plugin object (`id: "openclaw-basic-memory"`, `kind: "memory"`). The `register(api)` function:

1. Parses config via `parseConfig()` from `config.ts`
2. Creates a `BmClient` instance (the MCP stdio client)
3. Registers all tools, providers, hooks, commands, and the service lifecycle
4. The service `start()` launches the MCP process (`bm mcp --transport stdio`), ensures the project exists, and sets the workspace directory
5. The service `stop()` tears down the MCP connection

### MCP Client (`bm-client.ts` — largest file)

Central orchestration layer that:
- Spawns and manages a **persistent** `bm mcp --transport stdio` child process via `@modelcontextprotocol/sdk`
- Validates the `REQUIRED_TOOLS` list at connect time
- Implements reconnection with bounded retries (500ms, 1s, 2s exponential backoff)
- Distinguishes recoverable errors (broken pipe, transport closed) from fatal errors
- All tool calls require `output_format: "json"` and extract `structuredContent.result`
- Public methods: `search`, `readNote`, `writeNote`, `editNote`, `deleteNote`, `moveNote`, `buildContext`, `recentActivity`, `indexConversation`, `ensureProject`, `listProjects`, `listWorkspaces`, `schemaValidate`, `schemaInfer`, `schemaDiff`
- All content methods accept an optional `project` parameter for cross-project operations
- `listProjects` accepts an optional `workspace` parameter for workspace-scoped listing

### Tools (`tools/`)

Each tool file exports a function that calls `api.registerTool()` with a TypeBox schema and handler. Tools delegate to `BmClient` methods and return OpenClaw-standard responses (`{ content: [{type: "text", text}], details? }`).

- `search-notes.ts`, `read-note.ts`, `write-note.ts`, `edit-note.ts`, `delete-note.ts`, `move-note.ts`, `build-context.ts`, `list-memory-projects.ts`, `list-workspaces.ts`, `schema-validate.ts`, `schema-infer.ts`, `schema-diff.ts` — thin wrappers around `BmClient`; all content tools accept an optional `project` param for cross-project operations
- `memory-provider.ts` — composited `memory_search` + `memory_get` providers. `memory_search` queries 3 sources in parallel: MEMORY.md (grep), BM knowledge graph (FTS + vector), and active task notes (YAML frontmatter scan)

### Commands & Hooks

- `commands/slash.ts` — `/remember` and `/recall` slash commands
- `commands/cli.ts` — `openclaw basic-memory <subcommand>` CLI registration
- `hooks/capture.ts` — auto-capture hook on `agent_end` events, writes timestamped daily conversation notes

### Configuration (`config.ts`)

Flexible config with defaults, snake_case aliases (`memory_dir`/`memory_file`), tilde/relative/absolute path resolution, and unknown-key validation. Cloud routing is configured through `bm cloud` and per-project BM settings, not plugin config.

## Key Patterns

- **TypeBox schemas** (`@sinclair/typebox`) for all tool parameter validation
- **Bun-native test runner** with `describe`/`it`/`expect` and `jest.fn()` mocking
- **ES modules** (`"type": "module"` in package.json)
- **Biome** for linting and formatting (configured in `biome.json`)
- **Build output** — `bun run build` emits `dist/` for `runtimeExtensions`; TypeScript source also stays in the package for source-compatible hosts
- **Strict TypeScript** with `noEmit` (type-checking only)

## Testing

- Unit tests live alongside source files (`*.test.ts`) and mock `BmClient` / `OpenClawPluginApi`
- Integration tests in `integration/` launch a real `bm mcp` process against a temp project
- `scripts/bm-local.sh` runs BM from the monorepo root via `uv run --project ...` when available, then falls back to `bm` on PATH

## CI/CD

- **Package CI** (root `.github/workflows/consolidated-packages.yml`): validates skills, typechecks, lints, builds, tests, and runs `npm pack --dry-run`.
- **Release** (root `.github/workflows/release.yml`): runs from Basic Memory tags and publishes this npm package after the Python release job. Version bumps are handled by the root `just release` / `just beta` recipes.

## Dependencies

- **Runtime**: `@modelcontextprotocol/sdk` (MCP client/transport), `@sinclair/typebox` (schema validation)
- **Peer**: `openclaw` (>=2026.5.2)
- **Dev**: `typescript`, `@biomejs/biome`, `@types/node`
- **External**: Basic Memory CLI (`bm`) must be installed separately (Python, installed via `uv`)

## Runtime Agent Guidance

Runtime agent guidance lives in the tool descriptions under `tools/` and the bundled `skills/`.
Longer tool-usage notes for humans are in `docs/agent-guide.md`.
