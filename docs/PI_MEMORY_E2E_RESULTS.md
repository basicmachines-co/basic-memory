# Pi memory end-to-end results

## Environment

- Pi: 0.85.1
- Basic Memory CLI: 0.23.2 from `/Users/phernandez/dev/basicmachines/basic-memory/.venv/bin/bm`
- Model-backed Pi RPC runs: `openai-codex/gpt-5.5`
- All Basic Memory data/config used isolated temp directories via `BASIC_MEMORY_HOME` and `BASIC_MEMORY_CONFIG_DIR`.
- Project config used per-temp-workspace `.pi/basic-memory.json`; no live Pi config was modified.

## CLI capture → fresh CLI recall

Temp project: `pi-e2e-cli` in `/tmp/pi-bm-e2e-cli.B41VJy`.

Flow:

1. Created isolated local Basic Memory project with `bm project add pi-e2e-cli ... --default --local --no-wait`.
2. Started Pi RPC with the local Basic Memory Pi package in CLI mode.
3. Sent a working-thread prompt containing:
   - decision: support CLI and MCP modes with shared Basic Memory notes;
   - rationale: transport is secondary to never starting over;
   - blocker: MCP adapter mode still needs an isolated run;
   - next step: run fresh-session recall.
4. Ran `/bm-capture Pi E2E CLI continuity checkpoint`.
5. Verified note through `bm tool search-notes`.
6. Started a separate fresh Pi RPC session and ran `/bm-recall transport is secondary`.

Result:

- Created note: `pi-e2e-cli/pi/sessions/pi-e2-e-cli-continuity-checkpoint`
- Fresh-session recall injected a `basic-memory-pi` custom message containing the note permalink, decision, rationale, blocker, and next step.

## CLI-written note → MCP adapter recall

Same isolated `pi-e2e-cli` project.

Flow:

1. Switched `.pi/basic-memory.json` to `transport: "mcp"` and `mcpServerName: "basic-memory-e2e"`.
2. Started Pi RPC with both `npm:pi-mcp-adapter@2.32.1` and the local Basic Memory Pi package.
3. Asked the model to use the `mcp` proxy tool to search server `basic-memory-e2e` for `transport is secondary` in project `pi-e2e-cli`.

Result:

- The model connected the runtime-registered Basic Memory MCP server, discovered `basic-memory-e2e_search_notes`, called it, and answered with:
  - permalink: `pi-e2e-cli/pi/sessions/pi-e2-e-cli-continuity-checkpoint`
  - decision: Pi integration must support CLI and MCP modes with shared Basic Memory notes;
  - rationale: transport is secondary to never starting over;
  - blocker: MCP adapter mode still needs an isolated run;
  - next step: run fresh-session recall.

## MCP adapter write → CLI recall

Temp project: `pi-e2e-mcp` in `/tmp/pi-bm-e2e-mcp.Lt83CA`.

Flow:

1. Created isolated local Basic Memory project with `bm project add pi-e2e-mcp ... --default --local --no-wait`.
2. Started Pi RPC with `npm:pi-mcp-adapter@2.32.1` and the local Basic Memory Pi package in MCP mode.
3. Asked the model to use the `mcp` proxy tool and server `basic-memory-e2e-mcp` to call `write_note` in project `pi-e2e-mcp`.
4. Verified the MCP-written note through the CLI with `bm tool search-notes`.

Result:

- Created note: `pi-e2e-mcp/pi/sessions/pi-e2-e-mcp-continuity-checkpoint`
- CLI search found the MCP-written note with:
  - decision: MCP adapter writes use the same portable Basic Memory notes;
  - rationale: CLI and MCP should interoperate without migration;
  - blocker: automation defaults remain unsettled;
  - next step: verify CLI recall of this MCP-written note.

## Issues observed

- The first CLI capture driver timed out waiting for Pi RPC idle, but the `/bm-capture` command had already succeeded. The later fresh recall and direct CLI search confirmed the note was durable.
- In MCP mode the model first listed the server before connecting, then called `mcp({ connect })`. This is expected adapter behavior for lazy runtime servers.
- The current `/bm-capture` and `/bm-recall` extension commands are CLI-backed even when `transport: "mcp"`; MCP mode currently exposes Basic Memory to the model through `pi-mcp-adapter`. A future iteration can decide whether commands should also invoke MCP through a supported adapter call surface if one becomes available.
