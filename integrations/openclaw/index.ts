import { execFile } from "node:child_process"
import { mkdir } from "node:fs/promises"
import { promisify } from "node:util"

import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry"
import { BmClient } from "./bm-client.ts"
import { registerCli } from "./commands/cli.ts"
import { registerSkillCommands } from "./commands/skills.ts"
import { registerCommands } from "./commands/slash.ts"
import {
  basicMemoryConfigSchema,
  parseConfig,
  resolveProjectPath,
} from "./config.ts"
import { BasicMemoryContextEngine } from "./context-engine/basic-memory-context-engine.ts"
import { initLogger, log } from "./logger.ts"
import { buildBasicMemoryCapability } from "./memory/basic-memory-capability.ts"
import { CONVERSATION_SCHEMA_CONTENT } from "./schema/conversation-schema.ts"
import { TASK_SCHEMA_CONTENT } from "./schema/task-schema.ts"
import { registerContextTool } from "./tools/build-context.ts"
import { registerDeleteTool } from "./tools/delete-note.ts"
import { registerEditTool } from "./tools/edit-note.ts"
import { registerProjectListTool } from "./tools/list-memory-projects.ts"
import { registerWorkspaceListTool } from "./tools/list-workspaces.ts"
import { registerMoveTool } from "./tools/move-note.ts"
import { registerReadTool } from "./tools/read-note.ts"
import { registerSchemaDiffTool } from "./tools/schema-diff.ts"
import { registerSchemaInferTool } from "./tools/schema-infer.ts"
import { registerSchemaValidateTool } from "./tools/schema-validate.ts"
import { registerSearchTool } from "./tools/search-notes.ts"
import { registerWriteTool } from "./tools/write-note.ts"

const execFileAsync = promisify(execFile)

// Async so a probe or an install never blocks OpenClaw's event loop during
// plugin startup.
export async function isCommandAvailable(command: string): Promise<boolean> {
  try {
    await execFileAsync(command, ["--version"])
    return true
  } catch {
    return false
  }
}

async function installBasicMemoryIfMissing(bmBin: string): Promise<void> {
  if (await isCommandAvailable(bmBin)) return

  log.info("bm CLI not found on PATH — attempting auto-install...")
  if (!(await isCommandAvailable("uv"))) {
    throw new Error(
      "Cannot auto-install basic-memory: uv not found. " +
        "Install uv first (brew install uv, or curl -LsSf https://astral.sh/uv/install.sh | sh), " +
        "then restart the gateway.",
    )
  }

  log.info("installing basic-memory via uv (this may take a minute)...")
  // basic-memory pins a FastMCP pre-release; without this flag uv silently
  // installs the last release that had none (#1338).
  const { stdout } = await execFileAsync(
    "uv",
    ["tool", "install", "basic-memory", "--prerelease=allow", "--force"],
    { encoding: "utf-8", timeout: 120_000 },
  )
  log.info(`basic-memory installed: ${stdout.trim().split("\n").pop()}`)
  if (!(await isCommandAvailable(bmBin))) {
    throw new Error(
      "bm installed but not found on PATH. You may need to add uv's bin directory to your PATH (typically ~/.local/bin).",
    )
  }
}

async function seedSchemas(client: BmClient): Promise<void> {
  for (const [name, content] of [
    ["Task", TASK_SCHEMA_CONTENT],
    ["Conversation", CONVERSATION_SCHEMA_CONTENT],
  ] as const) {
    try {
      await client.readNote(`schema/${name}`)
      log.debug(`${name} schema already exists, skipping seed`)
    } catch {
      try {
        await client.writeNote(name, content, "schema")
        log.debug(`seeded ${name} schema note`)
      } catch (err) {
        log.debug(`${name} schema seed failed (non-fatal)`, err)
      }
    }
  }
}

export default definePluginEntry({
  id: "openclaw-basic-memory",
  name: "Basic Memory",
  description:
    "Local-first knowledge graph for OpenClaw — persistent memory with graph search, recall and conversation capture",
  configSchema: basicMemoryConfigSchema,

  register(api) {
    const cfg = parseConfig(api.pluginConfig)

    initLogger(api.logger, cfg.debug)

    log.info(
      `project=${cfg.project} memoryDir=${cfg.memoryDir} memoryFile=${cfg.memoryFile}`,
    )

    const client = new BmClient(cfg.bmPath, cfg.project)

    // --- BM Tools (always registered) ---
    registerSearchTool(api, client)
    registerProjectListTool(api, client)
    registerWorkspaceListTool(api, client)
    registerReadTool(api, client)
    registerWriteTool(api, client)
    registerEditTool(api, client)
    registerContextTool(api, client)
    registerDeleteTool(api, client)
    registerMoveTool(api, client)
    registerSchemaValidateTool(api, client)
    registerSchemaInferTool(api, client)
    registerSchemaDiffTool(api, client)

    // --- Memory capability ---
    // OpenClaw 2026.10 owns the memory_search and memory_get tool names
    // (memory-core registers them even when another plugin holds the memory
    // slot), so the plugin serves the host through registerMemoryCapability:
    // gateway memory search, status and doctor, Active Memory recall, and the
    // pre-compaction flush.
    api.registerMemoryCapability(buildBasicMemoryCapability(client))
    log.info("registered Basic Memory memory capability")
    api.registerContextEngine(
      "openclaw-basic-memory",
      () => new BasicMemoryContextEngine(client, cfg),
    )
    log.info("registered Basic Memory context engine")

    // --- Commands ---
    registerCommands(api, client)
    registerSkillCommands(api)
    registerCli(api, client, cfg)

    // --- Service lifecycle ---
    api.registerService({
      id: "openclaw-basic-memory",
      start: async (ctx: { config?: unknown; workspaceDir?: string }) => {
        log.info("starting...")

        const workspace = ctx.workspaceDir ?? process.cwd()
        const projectPath = resolveProjectPath(cfg.projectPath, workspace)
        cfg.projectPath = projectPath

        // bm runs with the workspace as its working directory. OpenClaw
        // creates its default workspace only on the first agent run, and
        // spawning into a missing directory fails with an ENOENT that names
        // the bm binary instead of the directory.
        await mkdir(workspace, { recursive: true })

        // OpenClaw gives a service about 5 s to start. A first start (bm
        // install, cold imports, first indexing) takes far longer, and a
        // timed-out start fails the plugin. So startup runs in the
        // background; tool calls wait for it inside BmClient, and a failed
        // startup is retried by the next tool call instead of failing here.
        client
          .start({
            cwd: workspace,
            projectPath,
            adoptExistingProject: !cfg.projectPathConfigured,
            prepare: () => installBasicMemoryIfMissing(cfg.bmPath || "bm"),
          })
          .then(async () => {
            log.info("connected — BM MCP stdio session running")
            await seedSchemas(client)
          })
          .catch((err) => {
            log.error(
              "Basic Memory startup failed; the next tool call will retry",
              err,
            )
          })
      },
      stop: async () => {
        log.info("stopping BM MCP session...")
        await client.stop()
        log.info("stopped")
      },
    })
  },
})
