import { execFileSync } from "node:child_process"

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
import { CONVERSATION_SCHEMA_CONTENT } from "./schema/conversation-schema.ts"
import { TASK_SCHEMA_CONTENT } from "./schema/task-schema.ts"
import { registerContextTool } from "./tools/build-context.ts"
import { registerDeleteTool } from "./tools/delete-note.ts"
import { registerEditTool } from "./tools/edit-note.ts"
import { registerProjectListTool } from "./tools/list-memory-projects.ts"
import { registerWorkspaceListTool } from "./tools/list-workspaces.ts"
import {
  registerMemoryProvider,
  setWorkspaceDir,
} from "./tools/memory-provider.ts"
import { registerMoveTool } from "./tools/move-note.ts"
import { registerReadTool } from "./tools/read-note.ts"
import { registerSchemaDiffTool } from "./tools/schema-diff.ts"
import { registerSchemaInferTool } from "./tools/schema-infer.ts"
import { registerSchemaValidateTool } from "./tools/schema-validate.ts"
import { registerSearchTool } from "./tools/search-notes.ts"
import { registerWriteTool } from "./tools/write-note.ts"

export function isCommandAvailable(command: string): boolean {
  try {
    execFileSync(command, ["--version"], { stdio: "ignore" })
    return true
  } catch {
    return false
  }
}

export default definePluginEntry({
  id: "openclaw-basic-memory",
  name: "Basic Memory",
  description:
    "Local-first knowledge graph for OpenClaw — persistent memory with graph search and composited memory_search",
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

    // --- Composited memory_search + memory_get (always registered) ---
    registerMemoryProvider(api, client, cfg)
    log.info("registered composited memory_search + memory_get")
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

        // Auto-install bm CLI if not found
        const bmBin = cfg.bmPath || "bm"
        if (!isCommandAvailable(bmBin)) {
          log.info("bm CLI not found on PATH — attempting auto-install...")
          try {
            if (!isCommandAvailable("uv")) {
              throw new Error("uv not found")
            }
            log.info(
              "installing basic-memory via uv (this may take a minute)...",
            )
            const result = execFileSync(
              "uv",
              ["tool", "install", "basic-memory", "--force"],
              {
                encoding: "utf-8",
                timeout: 120_000,
                stdio: ["ignore", "pipe", "pipe"],
              },
            )
            log.info(
              `basic-memory installed: ${result.trim().split("\n").pop()}`,
            )
            // Verify it worked
            if (isCommandAvailable(bmBin)) {
              log.info("bm CLI now available on PATH")
            } else {
              log.error(
                "bm installed but not found on PATH. You may need to add uv's bin directory to your PATH (typically ~/.local/bin).",
              )
            }
          } catch (_uvErr) {
            log.error(
              "Cannot auto-install basic-memory: uv not found. " +
                "Install uv first (brew install uv, or curl -LsSf https://astral.sh/uv/install.sh | sh), " +
                "then restart the gateway.",
            )
          }
        }

        const workspace = ctx.workspaceDir ?? process.cwd()
        const projectPath = resolveProjectPath(cfg.projectPath, workspace)
        cfg.projectPath = projectPath

        await client.start({ cwd: workspace })
        await client.ensureProject(projectPath)
        log.debug(`project "${cfg.project}" at ${projectPath}`)

        // Seed schemas if not already present
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

        setWorkspaceDir(workspace)

        log.info("connected — BM MCP stdio session running")
      },
      stop: async () => {
        log.info("stopping BM MCP session...")
        await client.stop()
        log.info("stopped")
      },
    })
  },
})
