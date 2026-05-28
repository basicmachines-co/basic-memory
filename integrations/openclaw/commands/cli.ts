import type { OpenClawPluginApi } from "openclaw/plugin-sdk/plugin-entry"
import type { BmClient } from "../bm-client.ts"
import type { BasicMemoryConfig } from "../config.ts"
import { log } from "../logger.ts"

export function registerCli(
  api: OpenClawPluginApi,
  client: BmClient,
  cfg: BasicMemoryConfig,
): void {
  api.registerCli(
    // biome-ignore lint/suspicious/noExplicitAny: openclaw SDK does not ship types
    ({ program }: { program: any }) => {
      const cmd = program
        .command("basic-memory")
        .description("Basic Memory knowledge graph commands")

      cmd
        .command("search")
        .argument("<query>", "Search query")
        .option("--limit <n>", "Max results", "10")
        .action(async (query: string, opts: { limit: string }) => {
          const limit = Number.parseInt(opts.limit, 10) || 10
          log.debug(`cli search: query="${query}" limit=${limit}`)

          const results = await client.search(query, limit)

          if (results.length === 0) {
            console.log("No notes found.")
            return
          }

          for (const r of results) {
            const score = r.score ? ` (${(r.score * 100).toFixed(0)}%)` : ""
            console.log(`- ${r.title}${score}`)
            if (r.content) {
              const preview =
                r.content.length > 100
                  ? `${r.content.slice(0, 100)}...`
                  : r.content
              console.log(`  ${preview}`)
            }
          }
        })

      cmd
        .command("read")
        .argument("<identifier>", "Note title, permalink, or memory:// URL")
        .option("--raw", "Return raw markdown including frontmatter", false)
        .action(async (identifier: string, opts: { raw?: boolean }) => {
          log.debug(`cli read: identifier="${identifier}"`)

          const note = await client.readNote(identifier, {
            includeFrontmatter: opts.raw === true,
          })
          console.log(`# ${note.title}`)
          console.log(`permalink: ${note.permalink}`)
          console.log(`file: ${note.file_path}`)
          console.log("")
          console.log(note.content)
        })

      cmd
        .command("edit")
        .argument("<identifier>", "Note title, permalink, or memory:// URL")
        .requiredOption(
          "--operation <operation>",
          "Edit operation: append|prepend|find_replace|replace_section",
        )
        .requiredOption("--content <content>", "Edit content")
        .option("--find-text <text>", "Text to find for find_replace")
        .option("--section <heading>", "Section heading for replace_section")
        .option(
          "--expected-replacements <n>",
          "Expected replacement count for find_replace",
          "1",
        )
        .action(
          async (
            identifier: string,
            opts: {
              operation:
                | "append"
                | "prepend"
                | "find_replace"
                | "replace_section"
              content: string
              findText?: string
              section?: string
              expectedReplacements: string
            },
          ) => {
            const expectedReplacements =
              Number.parseInt(opts.expectedReplacements, 10) || 1
            log.debug(
              `cli edit: identifier="${identifier}" op=${opts.operation} expected_replacements=${expectedReplacements}`,
            )

            const result = await client.editNote(
              identifier,
              opts.operation,
              opts.content,
              {
                find_text: opts.findText,
                section: opts.section,
                expected_replacements: expectedReplacements,
              },
            )

            console.log(`Edited: ${result.title}`)
            console.log(`permalink: ${result.permalink}`)
            console.log(`file: ${result.file_path}`)
            console.log(`operation: ${result.operation}`)
            if (result.checksum) {
              console.log(`checksum: ${result.checksum}`)
            }
          },
        )

      cmd
        .command("context")
        .argument("<url>", "Memory URL to navigate")
        .option("--depth <n>", "Relation hops to follow", "1")
        .action(async (url: string, opts: { depth: string }) => {
          const depth = Number.parseInt(opts.depth, 10) || 1
          log.debug(`cli context: url="${url}" depth=${depth}`)

          const ctx = await client.buildContext(url, depth)

          if (!ctx.results || ctx.results.length === 0) {
            console.log(`No context found for "${url}".`)
            return
          }

          for (const result of ctx.results) {
            console.log(`## ${result.primary_result.title}`)
            console.log(result.primary_result.content)
            console.log("")
          }
        })

      cmd
        .command("recent")
        .option("--timeframe <t>", "Timeframe (e.g. 24h, 7d)", "24h")
        .action(async (opts: { timeframe: string }) => {
          log.debug(`cli recent: timeframe="${opts.timeframe}"`)

          const results = await client.recentActivity(opts.timeframe)

          if (results.length === 0) {
            console.log("No recent activity.")
            return
          }

          for (const r of results) {
            console.log(`- ${r.title} (${r.permalink})`)
          }
        })

      cmd
        .command("status")
        .description("Show plugin status")
        .action(() => {
          console.log(`Project: ${cfg.project}`)
          console.log(`Project path: ${cfg.projectPath}`)
          console.log(`BM CLI: ${cfg.bmPath}`)
          console.log(`Memory dir: ${cfg.memoryDir}`)
          console.log(`Memory file: ${cfg.memoryFile}`)
          console.log(`Auto-capture: ${cfg.autoCapture}`)
          console.log(`Debug: ${cfg.debug}`)
        })
    },
    { commands: ["basic-memory"] },
  )
}
