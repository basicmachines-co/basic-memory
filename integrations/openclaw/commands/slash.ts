import { execSync } from "node:child_process"
import { dirname, resolve } from "node:path"
import { fileURLToPath } from "node:url"
import type { OpenClawPluginApi } from "openclaw/plugin-sdk/plugin-entry"
import type { BmClient } from "../bm-client.ts"
import { log } from "../logger.ts"

const __dirname = dirname(fileURLToPath(import.meta.url))

export function registerCommands(
  api: OpenClawPluginApi,
  client: BmClient,
): void {
  api.registerCommand({
    name: "bm-setup",
    description: "Install or update the Basic Memory CLI (requires uv)",
    requireAuth: true,
    handler: async () => {
      const scriptPath = api.resolvePath
        ? api.resolvePath("scripts/setup-bm.sh")
        : resolve(__dirname, "..", "scripts", "setup-bm.sh")
      log.info(`/bm-setup: running ${scriptPath}`)

      try {
        const output = execSync(`bash "${scriptPath}"`, {
          encoding: "utf-8",
          timeout: 180_000,
          stdio: "pipe",
          env: { ...process.env },
        })
        return { text: output.trim() }
      } catch (err: unknown) {
        const execErr = err as { stderr?: string; stdout?: string }
        const detail = execErr.stderr || execErr.stdout || String(err)
        log.error("/bm-setup failed", err)
        return {
          text: `Setup failed:\n${detail.trim()}`,
        }
      }
    },
  })

  api.registerCommand({
    name: "remember",
    description: "Save something to the Basic Memory knowledge graph",
    acceptsArgs: true,
    requireAuth: true,
    handler: async (ctx: { args?: string }) => {
      const text = ctx.args?.trim()
      if (!text) {
        return { text: "Usage: /remember <text to save as a note>" }
      }

      log.debug(`/remember: "${text.slice(0, 50)}"`)

      try {
        const title = text.length > 60 ? text.slice(0, 60) : text
        await client.writeNote(title, text, "agent/memories")

        const preview = text.length > 60 ? `${text.slice(0, 60)}...` : text
        return { text: `Remembered: "${preview}"` }
      } catch (err) {
        log.error("/remember failed", err)
        return {
          text: "Failed to save memory. Is Basic Memory running?",
        }
      }
    },
  })

  api.registerCommand({
    name: "recall",
    description: "Search the Basic Memory knowledge graph",
    acceptsArgs: true,
    requireAuth: true,
    handler: async (ctx: { args?: string }) => {
      const query = ctx.args?.trim()
      if (!query) {
        return { text: "Usage: /recall <search query>" }
      }

      log.debug(`/recall: "${query}"`)

      try {
        const results = await client.search(query, 5)

        if (results.length === 0) {
          return { text: `No notes found for: "${query}"` }
        }

        const lines = results.map((r, i) => {
          const score = r.score ? ` (${(r.score * 100).toFixed(0)}%)` : ""
          return `${i + 1}. ${r.title}${score}`
        })

        return {
          text: `Found ${results.length} notes:\n\n${lines.join("\n")}`,
        }
      } catch (err) {
        log.error("/recall failed", err)
        return {
          text: "Failed to search. Is Basic Memory running?",
        }
      }
    },
  })
}
