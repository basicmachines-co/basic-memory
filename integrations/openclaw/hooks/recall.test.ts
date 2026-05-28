import { beforeEach, describe, expect, it, jest } from "bun:test"
import type { BmClient, RecentResult, SearchResult } from "../bm-client.ts"
import type { BasicMemoryConfig } from "../config.ts"
import { buildRecallHandler, formatRecallContext } from "./recall.ts"

const DEFAULT_PROMPT =
  "Check for active tasks and recent activity. Summarize anything relevant to the current session."

function makeTasks(titles: string[]): SearchResult[] {
  return titles.map((title) => ({
    title,
    permalink: title.toLowerCase().replace(/\s+/g, "-"),
    content: `Description of ${title}`,
    file_path: `memory/tasks/${title.toLowerCase().replace(/\s+/g, "-")}.md`,
  }))
}

function makeRecent(titles: string[]): RecentResult[] {
  return titles.map((title) => ({
    title,
    permalink: title.toLowerCase().replace(/\s+/g, "-"),
    file_path: `memory/${title.toLowerCase().replace(/\s+/g, "-")}.md`,
    created_at: "2026-02-21T12:00:00Z",
  }))
}

describe("recall hook", () => {
  describe("formatRecallContext", () => {
    it("should return empty string when no tasks or recent activity", () => {
      const result = formatRecallContext(makeTasks([]), [], DEFAULT_PROMPT)
      expect(result).toBe("")
    })

    it("should format active tasks", () => {
      const result = formatRecallContext(
        makeTasks(["Fix login bug", "Update docs"]),
        [],
        DEFAULT_PROMPT,
      )

      expect(result).toContain("## Active Tasks")
      expect(result).toContain("**Fix login bug**")
      expect(result).toContain("**Update docs**")
      expect(result).toContain(DEFAULT_PROMPT)
    })

    it("should format recent activity", () => {
      const result = formatRecallContext(
        makeTasks([]),
        makeRecent(["Daily standup notes", "API design"]),
        DEFAULT_PROMPT,
      )

      expect(result).toContain("## Recent Activity")
      expect(result).toContain("Daily standup notes")
      expect(result).toContain("API design")
    })

    it("should format both tasks and recent activity", () => {
      const result = formatRecallContext(
        makeTasks(["Fix bug"]),
        makeRecent(["Meeting notes"]),
        DEFAULT_PROMPT,
      )

      expect(result).toContain("## Active Tasks")
      expect(result).toContain("## Recent Activity")
      expect(result).toContain("---")
    })

    it("should truncate long task content", () => {
      const tasks: SearchResult[] = [
        {
          title: "Long Task",
          permalink: "long-task",
          content: "A".repeat(200),
          file_path: "memory/tasks/long-task.md",
        },
      ]

      const result = formatRecallContext(tasks, [], DEFAULT_PROMPT)
      expect(result).toContain("...")
      // 120 chars + "..." = content is truncated
      expect(result).not.toContain("A".repeat(200))
    })

    it("should use custom prompt", () => {
      const customPrompt = "Focus on blocked tasks and recent decisions."
      const result = formatRecallContext(
        makeTasks(["Task A"]),
        [],
        customPrompt,
      )

      expect(result).toContain(customPrompt)
      expect(result).not.toContain(DEFAULT_PROMPT)
    })
  })

  describe("buildRecallHandler", () => {
    let mockClient: {
      search: jest.Mock
      recentActivity: jest.Mock
    }
    let mockConfig: BasicMemoryConfig

    beforeEach(() => {
      mockClient = {
        search: jest.fn().mockResolvedValue(makeTasks([])),
        recentActivity: jest.fn().mockResolvedValue([]),
      }
      mockConfig = {
        project: "test-project",
        bmPath: "bm",
        memoryDir: "memory/",
        memoryFile: "MEMORY.md",
        projectPath: "/tmp/test",
        autoCapture: true,
        captureMinChars: 10,
        autoRecall: true,
        recallPrompt: DEFAULT_PROMPT,
        debug: false,
      }
    })

    it("should return a function", () => {
      const handler = buildRecallHandler(
        mockClient as unknown as BmClient,
        mockConfig,
      )
      expect(typeof handler).toBe("function")
    })

    it("should query active tasks and recent activity", async () => {
      const handler = buildRecallHandler(
        mockClient as unknown as BmClient,
        mockConfig,
      )

      await handler({})

      expect(mockClient.search).toHaveBeenCalledWith(undefined, 5, undefined, {
        note_types: ["Task"],
        status: "active",
      })
      expect(mockClient.recentActivity).toHaveBeenCalledWith("1d")
    })

    it("should return empty object when no context found", async () => {
      const handler = buildRecallHandler(
        mockClient as unknown as BmClient,
        mockConfig,
      )

      const result = await handler({})
      expect(result).toEqual({})
    })

    it("should return context when tasks are found", async () => {
      mockClient.search.mockResolvedValue(makeTasks(["Fix login bug"]))

      const handler = buildRecallHandler(
        mockClient as unknown as BmClient,
        mockConfig,
      )

      const result = await handler({})
      expect(result).toHaveProperty("context")
      expect(result.context).toContain("Fix login bug")
    })

    it("should return context when recent activity is found", async () => {
      mockClient.recentActivity.mockResolvedValue(
        makeRecent(["API design notes"]),
      )

      const handler = buildRecallHandler(
        mockClient as unknown as BmClient,
        mockConfig,
      )

      const result = await handler({})
      expect(result).toHaveProperty("context")
      expect(result.context).toContain("API design notes")
    })

    it("should handle errors gracefully", async () => {
      mockClient.search.mockRejectedValue(new Error("BM down"))

      const handler = buildRecallHandler(
        mockClient as unknown as BmClient,
        mockConfig,
      )

      const result = await handler({})
      expect(result).toEqual({})
    })

    it("should use custom recallPrompt from config", async () => {
      const customConfig = {
        ...mockConfig,
        recallPrompt: "Focus on blocked tasks.",
      }
      mockClient.search.mockResolvedValue(makeTasks(["Task A"]))

      const handler = buildRecallHandler(
        mockClient as unknown as BmClient,
        customConfig,
      )

      const result = await handler({})
      expect(result.context).toContain("Focus on blocked tasks.")
    })
  })
})
