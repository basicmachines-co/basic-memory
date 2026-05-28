import type { BmClient, RecentResult, SearchResult } from "../bm-client.ts"
import type { BasicMemoryConfig } from "../config.ts"
import { log } from "../logger.ts"

export interface RecallState {
  tasks: SearchResult[]
  recent: RecentResult[]
  context: string
}

/**
 * Format recalled context from active tasks and recent activity.
 * Returns empty string if nothing was found.
 */
export function formatRecallContext(
  tasks: SearchResult[],
  recent: RecentResult[],
  prompt: string,
): string {
  const sections: string[] = []

  if (tasks.length > 0) {
    const taskLines = tasks.map((t) => {
      const preview =
        t.content.length > 120 ? `${t.content.slice(0, 120)}...` : t.content
      return `- **${t.title}** — ${preview}`
    })
    sections.push(`## Active Tasks\n${taskLines.join("\n")}`)
  }

  if (recent.length > 0) {
    const recentLines = recent.map((r) => `- ${r.title} (${r.file_path})`)
    sections.push(`## Recent Activity\n${recentLines.join("\n")}`)
  }

  if (sections.length === 0) return ""

  return `${sections.join("\n\n")}\n\n---\n${prompt}`
}

export async function loadRecallState(
  client: BmClient,
  cfg: BasicMemoryConfig,
): Promise<RecallState | null> {
  const [tasks, recent] = await Promise.all([
    client.search(undefined, 5, undefined, {
      note_types: ["Task"],
      status: "active",
    }),
    client.recentActivity("1d"),
  ])

  const context = formatRecallContext(tasks, recent, cfg.recallPrompt)
  if (!context) return null

  return { tasks, recent, context }
}

/**
 * Build the pre-turn recall handler.
 *
 * On agent_start, queries active tasks and recent activity from Basic Memory,
 * then returns formatted context for injection into the conversation.
 */
export function buildRecallHandler(client: BmClient, cfg: BasicMemoryConfig) {
  return async (_event: Record<string, unknown>) => {
    try {
      const recall = await loadRecallState(client, cfg)
      if (!recall) return {}

      log.debug(
        `recall: ${recall.tasks.length} active tasks, ${recall.recent.length} recent items`,
      )

      return { context: recall.context }
    } catch (err) {
      log.error("recall failed", err)
      return {}
    }
  }
}
