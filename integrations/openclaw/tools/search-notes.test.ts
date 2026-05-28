import { beforeEach, describe, expect, it, jest } from "bun:test"
import type { OpenClawPluginApi } from "openclaw/plugin-sdk/plugin-entry"
import type { BmClient } from "../bm-client.ts"
import { registerSearchTool } from "./search-notes.ts"

describe("search tool", () => {
  let mockApi: OpenClawPluginApi
  let mockClient: BmClient

  beforeEach(() => {
    mockApi = {
      registerTool: jest.fn(),
    } as any

    mockClient = {
      search: jest.fn(),
    } as any
  })

  describe("registerSearchTool", () => {
    it("should register search_notes tool with correct configuration", () => {
      registerSearchTool(mockApi, mockClient)

      expect(mockApi.registerTool).toHaveBeenCalledWith(
        expect.objectContaining({
          name: "search_notes",
          label: "Knowledge Search",
          description: expect.stringContaining(
            "Search the Basic Memory knowledge graph",
          ),
          parameters: expect.objectContaining({
            type: "object",
            properties: expect.objectContaining({
              query: expect.objectContaining({
                type: "string",
                description: "Search query",
              }),
              limit: expect.objectContaining({
                type: "number",
                description: "Max results (default: 10)",
              }),
              project: expect.objectContaining({
                type: "string",
              }),
            }),
          }),
          execute: expect.any(Function),
        }),
        { name: "search_notes" },
      )
    })
  })

  describe("tool execution", () => {
    let executeFunction: Function

    beforeEach(() => {
      registerSearchTool(mockApi, mockClient)
      const registerCall = (mockApi.registerTool as jest.MockedFunction<any>)
        .mock.calls[0]
      executeFunction = registerCall[0].execute
    })

    it("should execute search with provided parameters", async () => {
      const mockResults = [
        {
          title: "Test Note",
          permalink: "test-note",
          content: "This is test content for the note",
          score: 0.95,
          file_path: "notes/test-note.md",
        },
        {
          title: "Another Note",
          permalink: "another-note",
          content: "This is another test note with different content",
          score: 0.8,
          file_path: "notes/another-note.md",
        },
      ]

      ;(mockClient.search as jest.MockedFunction<any>).mockResolvedValue(
        mockResults,
      )

      const result = await executeFunction("tool-call-id", {
        query: "test query",
        limit: 5,
      })

      expect(mockClient.search).toHaveBeenCalledWith(
        "test query",
        5,
        undefined,
        undefined,
      )
      expect(result).toEqual({
        content: [
          {
            type: "text",
            text: expect.stringContaining("Found 2 notes:"),
          },
        ],
        details: {
          count: 2,
          results: [
            {
              title: "Test Note",
              permalink: "test-note",
              score: 0.95,
            },
            {
              title: "Another Note",
              permalink: "another-note",
              score: 0.8,
            },
          ],
        },
      })
    })

    it("should use default limit when not provided", async () => {
      ;(mockClient.search as jest.MockedFunction<any>).mockResolvedValue([])

      await executeFunction("tool-call-id", { query: "test query" })

      expect(mockClient.search).toHaveBeenCalledWith(
        "test query",
        10,
        undefined,
        undefined,
      )
    })

    it("should format results with scores as percentages", async () => {
      const mockResults = [
        {
          title: "High Score Note",
          permalink: "high-score",
          content: "Content with high relevance score",
          score: 0.95,
          file_path: "notes/high-score.md",
        },
        {
          title: "Medium Score Note",
          permalink: "medium-score",
          content: "Content with medium relevance score",
          score: 0.67,
          file_path: "notes/medium-score.md",
        },
      ]

      ;(mockClient.search as jest.MockedFunction<any>).mockResolvedValue(
        mockResults,
      )

      const result = await executeFunction("tool-call-id", { query: "test" })

      const text = result.content[0].text
      expect(text).toContain("**High Score Note** (95%)")
      expect(text).toContain("**Medium Score Note** (67%)")
    })

    it("should handle results without scores", async () => {
      const mockResults = [
        {
          title: "No Score Note",
          permalink: "no-score",
          content: "Content without score",
          file_path: "notes/no-score.md",
        },
      ]

      ;(mockClient.search as jest.MockedFunction<any>).mockResolvedValue(
        mockResults,
      )

      const result = await executeFunction("tool-call-id", { query: "test" })

      const text = result.content[0].text
      expect(text).toContain("No Score Note")
      expect(text).not.toContain("(%)")
    })

    it("should truncate long content with ellipsis", async () => {
      const longContent = "a".repeat(300)
      const mockResults = [
        {
          title: "Long Content Note",
          permalink: "long-content",
          content: longContent,
          score: 0.8,
          file_path: "notes/long-content.md",
        },
      ]

      ;(mockClient.search as jest.MockedFunction<any>).mockResolvedValue(
        mockResults,
      )

      const result = await executeFunction("tool-call-id", { query: "test" })

      const text = result.content[0].text
      expect(text).toContain(`${"a".repeat(200)}...`)
      expect(text).not.toContain("a".repeat(250))
    })

    it("should not truncate short content", async () => {
      const shortContent = "Short content that should not be truncated"
      const mockResults = [
        {
          title: "Short Content Note",
          permalink: "short-content",
          content: shortContent,
          score: 0.8,
          file_path: "notes/short-content.md",
        },
      ]

      ;(mockClient.search as jest.MockedFunction<any>).mockResolvedValue(
        mockResults,
      )

      const result = await executeFunction("tool-call-id", { query: "test" })

      const text = result.content[0].text
      expect(text).toContain(shortContent)
      expect(text).not.toContain("...")
    })

    it("should return appropriate message when no results found", async () => {
      ;(mockClient.search as jest.MockedFunction<any>).mockResolvedValue([])

      const result = await executeFunction("tool-call-id", {
        query: "nonexistent",
      })

      expect(result).toEqual({
        content: [
          {
            type: "text",
            text: "No matching notes found in the knowledge graph.",
          },
        ],
        details: {
          count: 0,
          results: [],
        },
      })
    })

    it("should pass project to client.search", async () => {
      ;(mockClient.search as jest.MockedFunction<any>).mockResolvedValue([])

      await executeFunction("tool-call-id", {
        query: "test query",
        project: "other-project",
      })

      expect(mockClient.search).toHaveBeenCalledWith(
        "test query",
        10,
        "other-project",
        undefined,
      )
    })

    it("should pass metadata_filters to client.search", async () => {
      ;(mockClient.search as jest.MockedFunction<any>).mockResolvedValue([])

      await executeFunction("tool-call-id", {
        query: "auth",
        metadata_filters: { status: "draft", type: "spec" },
      })

      expect(mockClient.search).toHaveBeenCalledWith("auth", 10, undefined, {
        filters: { status: "draft", type: "spec" },
        tags: undefined,
        status: undefined,
      })
    })

    it("should pass tags to client.search", async () => {
      ;(mockClient.search as jest.MockedFunction<any>).mockResolvedValue([])

      await executeFunction("tool-call-id", {
        query: "",
        tags: ["security", "oauth"],
      })

      expect(mockClient.search).toHaveBeenCalledWith("", 10, undefined, {
        filters: undefined,
        tags: ["security", "oauth"],
        status: undefined,
      })
    })

    it("should pass status to client.search", async () => {
      ;(mockClient.search as jest.MockedFunction<any>).mockResolvedValue([])

      await executeFunction("tool-call-id", {
        query: "planning",
        status: "active",
      })

      expect(mockClient.search).toHaveBeenCalledWith(
        "planning",
        10,
        undefined,
        {
          filters: undefined,
          tags: undefined,
          status: "active",
        },
      )
    })

    it("should pass all metadata params together to client.search", async () => {
      ;(mockClient.search as jest.MockedFunction<any>).mockResolvedValue([])

      await executeFunction("tool-call-id", {
        query: "oauth flow",
        limit: 5,
        project: "research",
        metadata_filters: { confidence: { $gt: 0.7 } },
        tags: ["security"],
        status: "in-progress",
      })

      expect(mockClient.search).toHaveBeenCalledWith(
        "oauth flow",
        5,
        "research",
        {
          filters: { confidence: { $gt: 0.7 } },
          tags: ["security"],
          status: "in-progress",
        },
      )
    })

    it("should not pass metadata when no metadata params provided", async () => {
      ;(mockClient.search as jest.MockedFunction<any>).mockResolvedValue([])

      await executeFunction("tool-call-id", {
        query: "test query",
      })

      expect(mockClient.search).toHaveBeenCalledWith(
        "test query",
        10,
        undefined,
        undefined,
      )
    })

    it("should handle search errors gracefully", async () => {
      const searchError = new Error("Search service unavailable")
      ;(mockClient.search as jest.MockedFunction<any>).mockRejectedValue(
        searchError,
      )

      const result = await executeFunction("tool-call-id", { query: "test" })

      expect(result).toEqual({
        content: [
          {
            type: "text",
            text: "Search failed. Is Basic Memory running? Check logs for details.",
          },
        ],
        details: {
          count: 0,
          results: [],
          query: "test",
          error: "search_notes_failed",
        },
      })
    })

    it("should number results sequentially", async () => {
      const mockResults = [
        {
          title: "First Note",
          permalink: "first",
          content: "First content",
          file_path: "first.md",
        },
        {
          title: "Second Note",
          permalink: "second",
          content: "Second content",
          file_path: "second.md",
        },
        {
          title: "Third Note",
          permalink: "third",
          content: "Third content",
          file_path: "third.md",
        },
      ]

      ;(mockClient.search as jest.MockedFunction<any>).mockResolvedValue(
        mockResults,
      )

      const result = await executeFunction("tool-call-id", { query: "test" })

      const text = result.content[0].text
      expect(text).toContain("1. **First Note**")
      expect(text).toContain("2. **Second Note**")
      expect(text).toContain("3. **Third Note**")
    })

    it("should include all result details in response details", async () => {
      const mockResults = [
        {
          title: "Note 1",
          permalink: "note-1",
          content: "Content 1",
          score: 0.9,
          file_path: "note-1.md",
        },
        {
          title: "Note 2",
          permalink: "note-2",
          content: "Content 2",
          file_path: "note-2.md",
        },
      ]

      ;(mockClient.search as jest.MockedFunction<any>).mockResolvedValue(
        mockResults,
      )

      const result = await executeFunction("tool-call-id", { query: "test" })

      expect(result.details).toEqual({
        count: 2,
        results: [
          {
            title: "Note 1",
            permalink: "note-1",
            score: 0.9,
          },
          {
            title: "Note 2",
            permalink: "note-2",
            score: undefined,
          },
        ],
      })
    })
  })
})
