import { readdir, readFile } from "node:fs/promises"
import { join, resolve } from "node:path"
import { Type } from "@sinclair/typebox"
import type { OpenClawPluginApi } from "openclaw/plugin-sdk/plugin-entry"
import type { BmClient } from "../bm-client.ts"
import type { BasicMemoryConfig } from "../config.ts"
import { log } from "../logger.ts"

/**
 * Search MEMORY.md for lines matching the query.
 * Returns matching lines with 1 line of surrounding context.
 */
async function searchMemoryFile(
  query: string,
  workspaceDir: string,
  memoryFile: string,
): Promise<string> {
  try {
    const filePath = resolve(workspaceDir, memoryFile)
    const content = await readFile(filePath, "utf-8")
    const lines = content.split("\n")
    const queryLower = query.toLowerCase()
    const terms = queryLower.split(/\s+/).filter((t) => t.length > 0)

    // Find lines that match any search term
    const matchingIndices = new Set<number>()
    for (let i = 0; i < lines.length; i++) {
      const lineLower = lines[i].toLowerCase()
      if (terms.some((term) => lineLower.includes(term))) {
        // Add the matching line + 1 line before/after for context
        if (i > 0) matchingIndices.add(i - 1)
        matchingIndices.add(i)
        if (i < lines.length - 1) matchingIndices.add(i + 1)
      }
    }

    if (matchingIndices.size === 0) return ""

    // Group consecutive lines into snippets
    const sorted = [...matchingIndices].sort((a, b) => a - b)
    const snippets: string[] = []
    let current: string[] = []
    let lastIdx = -2

    for (const idx of sorted) {
      if (idx !== lastIdx + 1 && current.length > 0) {
        snippets.push(current.join("\n"))
        current = []
      }
      current.push(`- ${lines[idx]}`)
      lastIdx = idx
    }
    if (current.length > 0) snippets.push(current.join("\n"))

    return snippets.join("\n…\n")
  } catch {
    return ""
  }
}

/**
 * Search for active tasks via BM knowledge graph.
 * Uses search_notes with note_types and status filters for precise querying.
 * Falls back to filesystem scan if search fails.
 */
async function searchActiveTasks(
  query: string,
  client: BmClient,
  workspaceDir: string,
  memoryDir: string,
): Promise<string> {
  // Try structured search first
  try {
    const results = await client.search(query || undefined, 10, undefined, {
      note_types: ["task"],
      status: "active",
    })

    if (results.length > 0) {
      const matches: string[] = []
      for (const r of results) {
        const score = r.score ? ` (${r.score.toFixed(2)})` : ""
        const preview =
          (r.content ?? "").length > 200
            ? `${(r.content ?? "").slice(0, 200)}…`
            : (r.content ?? "")
        matches.push(
          `- **${r.title}**${score} — ${r.file_path}\n  > ${preview.replace(/\n/g, "\n  > ")}`,
        )
      }
      return matches.join("\n\n")
    }
  } catch (err) {
    log.debug("BM task search failed, falling back to filesystem scan", err)
  }

  // Fallback: filesystem scan for tasks not yet indexed
  try {
    const tasksDir = resolve(workspaceDir, memoryDir, "tasks")
    let entries: import("node:fs").Dirent[]
    try {
      entries = (await readdir(tasksDir, {
        withFileTypes: true,
      })) as unknown as import("node:fs").Dirent[]
    } catch {
      return ""
    }

    const queryLower = query.toLowerCase()
    const terms = queryLower.split(/\s+/).filter((t) => t.length > 0)
    const matches: string[] = []

    for (const entry of entries) {
      if (!entry.isFile() || !entry.name.endsWith(".md")) continue

      const filePath = join(entry.parentPath ?? tasksDir, entry.name)
      const content = await readFile(filePath, "utf-8")

      const statusMatch = content.match(/status:\s*(\S+)/)
      const status = statusMatch?.[1] ?? "unknown"
      if (status === "done") continue

      const contentLower = content.toLowerCase()
      const matchesQuery =
        terms.length === 0 || terms.some((term) => contentLower.includes(term))
      if (!matchesQuery) continue

      const titleMatch = content.match(/title:\s*(.+)/)
      const title = titleMatch?.[1] ?? entry.name.replace(/\.md$/, "")
      const stepMatch = content.match(/current_step:\s*(\S+)/)
      const currentStep = stepMatch?.[1] ?? "?"

      const contextMatch = content.match(
        /## Context\s*\n([\s\S]*?)(?=\n##|\n---|$)/,
      )
      const context = contextMatch?.[1]?.trim().slice(0, 150) ?? ""

      matches.push(
        `- **${title}** (status: ${status}, step: ${currentStep})\n  ${context}`,
      )
    }

    return matches.join("\n")
  } catch {
    return ""
  }
}

// Store workspace dir for use in memory_search (set during service start)
let _workspaceDir = ""
export function setWorkspaceDir(dir: string) {
  _workspaceDir = dir
}

/**
 * Register composited memory_search and memory_get tools.
 *
 * memory_search queries 3 sources in parallel:
 * 1. MEMORY.md — grep/text search
 * 2. BM knowledge graph — semantic + FTS search
 * 3. Active tasks — memory/tasks/ files with status != done
 *
 * memory_get reads a specific note by identifier.
 */
export function registerMemoryProvider(
  api: OpenClawPluginApi,
  client: BmClient,
  cfg: BasicMemoryConfig,
): void {
  // --- composited memory_search ---
  api.registerTool(
    {
      name: "memory_search",
      label: "Memory Search",
      description:
        "Search across all memory sources: MEMORY.md (working memory), " +
        "Basic Memory knowledge graph (long-term archive), and active tasks. " +
        "Returns composited results from all sources.",
      parameters: Type.Object({
        query: Type.String({
          description: "Search query — natural language or keywords",
        }),
      }),
      async execute(_toolCallId: string, params: { query: string }) {
        log.debug(`memory_search: query="${params.query}"`)

        const workspaceDir = _workspaceDir || process.cwd()

        // Query all 3 sources in parallel
        const [memoryMd, bmResults, taskResults] = await Promise.all([
          searchMemoryFile(params.query, workspaceDir, cfg.memoryFile),
          client
            .search(params.query, 5)
            .then((results) => {
              if (results.length === 0) return ""
              return results
                .map((r) => {
                  const score = r.score ? ` (${r.score.toFixed(2)})` : ""
                  const preview =
                    (r.content ?? "").length > 200
                      ? `${(r.content ?? "").slice(0, 200)}…`
                      : (r.content ?? "")
                  const source = r.file_path || r.permalink
                  return `- ${source}${score}\n  > ${preview.replace(/\n/g, "\n  > ")}`
                })
                .join("\n\n")
            })
            .catch((err) => {
              log.error("BM search failed in composited search", err)
              return "(search unavailable)"
            }),
          searchActiveTasks(params.query, client, workspaceDir, cfg.memoryDir),
        ])

        // Build composited result
        const sections: string[] = []

        if (memoryMd) {
          sections.push(`## ${cfg.memoryFile}\n${memoryMd}`)
        }

        if (bmResults) {
          sections.push(`## Knowledge Graph (${cfg.memoryDir})\n${bmResults}`)
        }

        if (taskResults) {
          sections.push(`## Active Tasks\n${taskResults}`)
        }

        if (sections.length === 0) {
          return {
            content: [
              {
                type: "text" as const,
                text: "No matches found across memory sources.",
              },
            ],
            details: {
              query: params.query,
              sectionCount: 0,
              hasMemoryFileMatches: false,
              hasKnowledgeGraphMatches: false,
              hasTaskMatches: false,
            },
          }
        }

        return {
          content: [
            {
              type: "text" as const,
              text: sections.join("\n\n"),
            },
          ],
          details: {
            query: params.query,
            sectionCount: sections.length,
            hasMemoryFileMatches: memoryMd.length > 0,
            hasKnowledgeGraphMatches: bmResults.length > 0,
            hasTaskMatches: taskResults.length > 0,
          },
        }
      },
    },
    { names: ["memory_search"] },
  )

  // --- memory_get (unchanged) ---
  api.registerTool(
    {
      name: "memory_get",
      label: "Memory Get",
      description:
        "Read a specific note from the knowledge graph by title, permalink, or path. " +
        "Returns the full note content. Use after memory_search to read a specific result in full.",
      parameters: Type.Object({
        path: Type.String({
          description:
            "Note identifier — title, permalink, memory:// URL, or file path",
        }),
        from: Type.Optional(
          Type.Number({
            description:
              "Starting line number (ignored — included for compatibility)",
          }),
        ),
        lines: Type.Optional(
          Type.Number({
            description:
              "Number of lines to read (ignored — included for compatibility)",
          }),
        ),
      }),
      async execute(
        _toolCallId: string,
        params: { path: string; from?: number; lines?: number },
      ) {
        log.debug(`memory_get: path="${params.path}"`)

        try {
          const note = await client.readNote(params.path)

          return {
            content: [
              {
                type: "text" as const,
                text: `# ${note.title}\n\n${note.content}`,
              },
            ],
            details: {
              title: note.title,
              permalink: note.permalink,
              file_path: note.file_path,
            },
          }
        } catch (err) {
          log.error("memory_get failed", err)
          return {
            content: [
              {
                type: "text" as const,
                text: `Could not read "${params.path}". It may not exist in the knowledge graph.`,
              },
            ],
            details: {
              path: params.path,
              error: "memory_get_failed",
            },
          }
        }
      },
    },
    { names: ["memory_get"] },
  )
}
