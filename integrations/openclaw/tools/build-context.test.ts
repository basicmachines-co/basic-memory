import { beforeEach, describe, expect, it, jest } from "bun:test"
import type { OpenClawPluginApi } from "openclaw/plugin-sdk/plugin-entry"
import type { BmClient } from "../bm-client.ts"
import { registerContextTool } from "./build-context.ts"

describe("context tool", () => {
  let mockApi: OpenClawPluginApi
  let mockClient: BmClient

  beforeEach(() => {
    mockApi = {
      registerTool: jest.fn(),
    } as any

    mockClient = {
      buildContext: jest.fn(),
    } as any
  })

  describe("registerContextTool", () => {
    it("should register build_context tool with correct configuration", () => {
      registerContextTool(mockApi, mockClient)

      expect(mockApi.registerTool).toHaveBeenCalledWith(
        expect.objectContaining({
          name: "build_context",
          label: "Build Context",
          description: expect.stringContaining(
            "Navigate the Basic Memory knowledge graph via memory:// URLs",
          ),
          parameters: expect.objectContaining({
            type: "object",
            properties: expect.objectContaining({
              url: expect.objectContaining({
                type: "string",
                description: expect.stringContaining(
                  "memory://agents/decisions",
                ),
              }),
              depth: expect.objectContaining({
                type: "number",
                description: "How many relation hops to follow (default: 1)",
              }),
              project: expect.objectContaining({ type: "string" }),
            }),
          }),
          execute: expect.any(Function),
        }),
        { name: "build_context" },
      )
    })
  })

  describe("tool execution", () => {
    let executeFunction: Function

    beforeEach(() => {
      registerContextTool(mockApi, mockClient)
      const registerCall = (mockApi.registerTool as jest.MockedFunction<any>)
        .mock.calls[0]
      executeFunction = registerCall[0].execute
    })

    it("should build context with provided URL and depth", async () => {
      const mockContext = {
        results: [
          {
            primary_result: {
              title: "Main Note",
              permalink: "main-note",
              content: "This is the main note content",
              file_path: "notes/main-note.md",
            },
            observations: [
              { category: "decision", content: "Important decision was made" },
              { category: "insight", content: "Key insight discovered" },
            ],
            related_results: [
              {
                type: "entity",
                title: "Related Note 1",
                permalink: "related-note-1",
                relation_type: "references",
              },
              {
                type: "entity",
                title: "Related Note 2",
                permalink: "related-note-2",
                relation_type: "follows_from",
              },
            ],
          },
        ],
      }

      ;(mockClient.buildContext as jest.MockedFunction<any>).mockResolvedValue(
        mockContext,
      )

      const result = await executeFunction("tool-call-id", {
        url: "memory://projects/my-project",
        depth: 2,
      })

      expect(mockClient.buildContext).toHaveBeenCalledWith(
        "memory://projects/my-project",
        2,
        undefined,
      )

      expect(result).toEqual({
        content: [
          {
            type: "text",
            text: expect.stringContaining("## Main Note"),
          },
        ],
        details: {
          url: "memory://projects/my-project",
          depth: 2,
          resultCount: 1,
        },
      })

      // Check that the text contains expected sections
      const text = result.content[0].text
      expect(text).toContain("## Main Note")
      expect(text).toContain("This is the main note content")
      expect(text).toContain("### Observations")
      expect(text).toContain("[decision] Important decision was made")
      expect(text).toContain("[insight] Key insight discovered")
      expect(text).toContain("### Related")
      expect(text).toContain("references → **Related Note 1**")
      expect(text).toContain("follows_from → **Related Note 2**")
    })

    it("should use default depth when not provided", async () => {
      const mockContext = { results: [] }
      ;(mockClient.buildContext as jest.MockedFunction<any>).mockResolvedValue(
        mockContext,
      )

      await executeFunction("tool-call-id", {
        url: "memory://test/url",
      })

      expect(mockClient.buildContext).toHaveBeenCalledWith(
        "memory://test/url",
        1,
        undefined,
      )
    })

    it("should handle context with no results", async () => {
      const mockContext = { results: [] }
      ;(mockClient.buildContext as jest.MockedFunction<any>).mockResolvedValue(
        mockContext,
      )

      const result = await executeFunction("tool-call-id", {
        url: "memory://nonexistent/path",
        depth: 1,
      })

      expect(result).toEqual({
        content: [
          {
            type: "text",
            text: 'No context found for "memory://nonexistent/path".',
          },
        ],
        details: {
          url: "memory://nonexistent/path",
          depth: 1,
          resultCount: 0,
        },
      })
    })

    it("should handle context with undefined results", async () => {
      const mockContext = {}
      ;(mockClient.buildContext as jest.MockedFunction<any>).mockResolvedValue(
        mockContext,
      )

      const result = await executeFunction("tool-call-id", {
        url: "memory://test/url",
      })

      expect(result.content[0].text).toContain("No context found")
    })

    it("should handle multiple results", async () => {
      const mockContext = {
        results: [
          {
            primary_result: {
              title: "First Note",
              permalink: "first-note",
              content: "First note content",
              file_path: "notes/first-note.md",
            },
            observations: [],
            related_results: [],
          },
          {
            primary_result: {
              title: "Second Note",
              permalink: "second-note",
              content: "Second note content",
              file_path: "notes/second-note.md",
            },
            observations: [{ category: "note", content: "Single observation" }],
            related_results: [
              {
                type: "entity",
                title: "Related to Second",
                permalink: "related-to-second",
                relation_type: "depends_on",
              },
            ],
          },
        ],
      }

      ;(mockClient.buildContext as jest.MockedFunction<any>).mockResolvedValue(
        mockContext,
      )

      const result = await executeFunction("tool-call-id", {
        url: "memory://multi/result",
      })

      const text = result.content[0].text
      expect(text).toContain("## First Note")
      expect(text).toContain("First note content")
      expect(text).toContain("## Second Note")
      expect(text).toContain("Second note content")
      expect(text).toContain("### Observations")
      expect(text).toContain("[note] Single observation")
      expect(text).toContain("### Related")
      expect(text).toContain("depends_on → **Related to Second**")

      expect(result.details.resultCount).toBe(2)
    })

    it("should handle results with no observations", async () => {
      const mockContext = {
        results: [
          {
            primary_result: {
              title: "No Observations Note",
              permalink: "no-observations",
              content: "Content without observations",
              file_path: "notes/no-observations.md",
            },
            observations: [],
            related_results: [],
          },
        ],
      }

      ;(mockClient.buildContext as jest.MockedFunction<any>).mockResolvedValue(
        mockContext,
      )

      const result = await executeFunction("tool-call-id", {
        url: "memory://test/url",
      })

      const text = result.content[0].text
      expect(text).toContain("## No Observations Note")
      expect(text).toContain("Content without observations")
      expect(text).not.toContain("### Observations")
    })

    it("should handle results with no related results", async () => {
      const mockContext = {
        results: [
          {
            primary_result: {
              title: "No Relations Note",
              permalink: "no-relations",
              content: "Content without relations",
              file_path: "notes/no-relations.md",
            },
            observations: [{ category: "test", content: "Test observation" }],
            related_results: [],
          },
        ],
      }

      ;(mockClient.buildContext as jest.MockedFunction<any>).mockResolvedValue(
        mockContext,
      )

      const result = await executeFunction("tool-call-id", {
        url: "memory://test/url",
      })

      const text = result.content[0].text
      expect(text).toContain("## No Relations Note")
      expect(text).toContain("### Observations")
      expect(text).toContain("[test] Test observation")
      expect(text).not.toContain("### Related")
    })

    it("should format permalinks correctly in related results", async () => {
      const mockContext = {
        results: [
          {
            primary_result: {
              title: "Main Note",
              permalink: "main-note",
              content: "Content",
              file_path: "notes/main-note.md",
            },
            observations: [],
            related_results: [
              {
                type: "entity",
                title: "Related Note with Long Title",
                permalink: "related-note-with-long-title",
                relation_type: "references",
              },
              {
                type: "entity",
                title: "Short Note",
                permalink: "short",
                relation_type: "follows_from",
              },
            ],
          },
        ],
      }

      ;(mockClient.buildContext as jest.MockedFunction<any>).mockResolvedValue(
        mockContext,
      )

      const result = await executeFunction("tool-call-id", {
        url: "memory://test/url",
      })

      const text = result.content[0].text
      expect(text).toContain(
        "references → **Related Note with Long Title** (related-note-with-long-title)",
      )
      expect(text).toContain("follows_from → **Short Note** (short)")
    })

    it("should handle different memory URL formats", async () => {
      const mockContext = { results: [] }
      ;(mockClient.buildContext as jest.MockedFunction<any>).mockResolvedValue(
        mockContext,
      )

      const urls = [
        "memory://projects/my-project",
        "projects/simple-path",
        "memory://agents/decisions/important",
        "single-note",
      ]

      for (const url of urls) {
        await executeFunction("tool-call-id", { url })
        expect(mockClient.buildContext).toHaveBeenCalledWith(url, 1, undefined)
      }

      expect(mockClient.buildContext).toHaveBeenCalledTimes(urls.length)
    })

    it("should handle various depth values", async () => {
      const mockContext = { results: [] }
      ;(mockClient.buildContext as jest.MockedFunction<any>).mockResolvedValue(
        mockContext,
      )

      const depths = [1, 2, 3, 5, 10]

      for (const depth of depths) {
        await executeFunction("tool-call-id", {
          url: "memory://test",
          depth,
        })
        expect(mockClient.buildContext).toHaveBeenCalledWith(
          "memory://test",
          depth,
          undefined,
        )
      }

      expect(mockClient.buildContext).toHaveBeenCalledTimes(depths.length)
    })

    it("should pass project to client.buildContext", async () => {
      const mockContext = { results: [] }
      ;(mockClient.buildContext as jest.MockedFunction<any>).mockResolvedValue(
        mockContext,
      )

      await executeFunction("tool-call-id", {
        url: "memory://test",
        project: "other-project",
      })

      expect(mockClient.buildContext).toHaveBeenCalledWith(
        "memory://test",
        1,
        "other-project",
      )
    })

    it("should handle buildContext errors gracefully", async () => {
      const contextError = new Error("Failed to build context")
      ;(mockClient.buildContext as jest.MockedFunction<any>).mockRejectedValue(
        contextError,
      )

      const result = await executeFunction("tool-call-id", {
        url: "memory://error/test",
        depth: 1,
      })

      expect(result).toEqual({
        content: [
          {
            type: "text",
            text: 'Failed to build context for "memory://error/test". Check logs for details.',
          },
        ],
        details: {
          url: "memory://error/test",
          depth: 1,
          error: "build_context_failed",
        },
      })
    })

    it("should handle network errors", async () => {
      const networkError = new Error("Connection refused")
      networkError.code = "ECONNREFUSED"
      ;(mockClient.buildContext as jest.MockedFunction<any>).mockRejectedValue(
        networkError,
      )

      const result = await executeFunction("tool-call-id", {
        url: "memory://network/error",
      })

      expect(result.content[0].text).toContain("Failed to build context")
    })

    it("should format complex observation categories", async () => {
      const mockContext = {
        results: [
          {
            primary_result: {
              title: "Complex Observations",
              permalink: "complex-obs",
              content: "Content",
              file_path: "notes/complex-obs.md",
            },
            observations: [
              {
                category: "decision-point",
                content: "Critical decision made here",
              },
              {
                category: "user_preference",
                content: "User prefers dark mode",
              },
              {
                category: "technical-detail",
                content: "Uses TypeScript for safety",
              },
            ],
            related_results: [],
          },
        ],
      }

      ;(mockClient.buildContext as jest.MockedFunction<any>).mockResolvedValue(
        mockContext,
      )

      const result = await executeFunction("tool-call-id", {
        url: "memory://complex",
      })

      const text = result.content[0].text
      expect(text).toContain("[decision-point] Critical decision made here")
      expect(text).toContain("[user_preference] User prefers dark mode")
      expect(text).toContain("[technical-detail] Uses TypeScript for safety")
    })

    it("should preserve content formatting in primary results", async () => {
      const formattedContent = `# Main Title

This is **bold** text with *italic* parts.

## Subsection

- List item 1
- List item 2

\`\`\`javascript
const code = "example";
\`\`\``

      const mockContext = {
        results: [
          {
            primary_result: {
              title: "Formatted Note",
              permalink: "formatted-note",
              content: formattedContent,
              file_path: "notes/formatted-note.md",
            },
            observations: [],
            related_results: [],
          },
        ],
      }

      ;(mockClient.buildContext as jest.MockedFunction<any>).mockResolvedValue(
        mockContext,
      )

      const result = await executeFunction("tool-call-id", {
        url: "memory://formatted",
      })

      const text = result.content[0].text
      expect(text).toContain(formattedContent)
    })

    it("should include URL and depth in response details", async () => {
      const mockContext = { results: [] }
      ;(mockClient.buildContext as jest.MockedFunction<any>).mockResolvedValue(
        mockContext,
      )

      const result = await executeFunction("tool-call-id", {
        url: "memory://details/test",
        depth: 3,
      })

      expect(result.details).toEqual({
        url: "memory://details/test",
        depth: 3,
        resultCount: 0,
      })
    })
  })
})
