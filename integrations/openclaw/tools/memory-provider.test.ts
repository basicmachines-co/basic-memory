import { beforeEach, describe, expect, it, jest } from "bun:test"
import type { OpenClawPluginApi } from "openclaw/plugin-sdk/plugin-entry"
import type { BmClient } from "../bm-client.ts"
import type { BasicMemoryConfig } from "../config.ts"
import { registerMemoryProvider, setWorkspaceDir } from "./memory-provider.ts"

function makeCfg(overrides?: Partial<BasicMemoryConfig>): BasicMemoryConfig {
  return {
    project: "test",
    bmPath: "bm",
    memoryDir: "memory/",
    memoryFile: "MEMORY.md",
    projectPath: "/tmp/bm-test",
    autoCapture: true,
    captureMinChars: 10,
    autoRecall: true,
    recallPrompt: "Check for active tasks and recent activity.",
    debug: false,
    ...overrides,
  }
}

describe("memory provider tools", () => {
  let mockApi: OpenClawPluginApi
  let mockClient: BmClient

  beforeEach(() => {
    mockApi = {
      registerTool: jest.fn(),
    } as any

    mockClient = {
      search: jest.fn().mockResolvedValue([]),
      readNote: jest.fn(),
    } as any

    // Use a temp dir that won't have MEMORY.md
    setWorkspaceDir("/tmp/nonexistent-workspace-for-test")
  })

  describe("registerMemoryProvider", () => {
    it("should register both memory_search and memory_get tools", () => {
      registerMemoryProvider(mockApi, mockClient, makeCfg())

      expect(mockApi.registerTool).toHaveBeenCalledTimes(2)

      expect(mockApi.registerTool).toHaveBeenCalledWith(
        expect.objectContaining({
          name: "memory_search",
          label: "Memory Search",
        }),
        { names: ["memory_search"] },
      )

      expect(mockApi.registerTool).toHaveBeenCalledWith(
        expect.objectContaining({
          name: "memory_get",
          label: "Memory Get",
        }),
        { names: ["memory_get"] },
      )
    })
  })

  describe("memory_search tool (composited)", () => {
    let searchExecute: Function

    beforeEach(() => {
      registerMemoryProvider(mockApi, mockClient, makeCfg())
      const searchCall = (mockApi.registerTool as jest.MockedFunction<any>).mock
        .calls[0]
      searchExecute = searchCall[0].execute
    })

    it("should return BM results in Knowledge Graph section", async () => {
      ;(mockClient.search as jest.MockedFunction<any>).mockResolvedValue([
        {
          title: "Test Note",
          permalink: "test-note",
          content: "Some content about testing",
          score: 0.95,
          file_path: "memory/test-note.md",
        },
      ])

      const result = await searchExecute("id", { query: "testing" })
      const text = result.content[0].text

      expect(text).toContain("## Knowledge Graph")
      expect(text).toContain("memory/test-note.md")
      expect(text).toContain("Some content about testing")
    })

    it("should return no matches message when all sources empty", async () => {
      ;(mockClient.search as jest.MockedFunction<any>).mockResolvedValue([])

      const result = await searchExecute("id", { query: "nonexistent" })

      expect(result.content[0].text).toBe(
        "No matches found across memory sources.",
      )
    })

    it("should handle BM search errors gracefully", async () => {
      ;(mockClient.search as jest.MockedFunction<any>).mockRejectedValue(
        new Error("BM down"),
      )

      const result = await searchExecute("id", { query: "test" })
      const text = result.content[0].text

      expect(text).toContain("(search unavailable)")
    })
  })

  describe("memory_get tool", () => {
    let getExecute: Function

    beforeEach(() => {
      registerMemoryProvider(mockApi, mockClient, makeCfg())
      const getCall = (mockApi.registerTool as jest.MockedFunction<any>).mock
        .calls[1]
      getExecute = getCall[0].execute
    })

    it("should read note and format with title", async () => {
      ;(mockClient.readNote as jest.MockedFunction<any>).mockResolvedValue({
        title: "Test Note",
        permalink: "test-note",
        content: "Note content here",
        file_path: "notes/test.md",
      })

      const result = await getExecute("id", { path: "test-note" })

      expect(result.content[0].text).toBe("# Test Note\n\nNote content here")
    })

    it("should handle errors gracefully", async () => {
      ;(mockClient.readNote as jest.MockedFunction<any>).mockRejectedValue(
        new Error("Not found"),
      )

      const result = await getExecute("id", { path: "missing" })

      expect(result.content[0].text).toContain('Could not read "missing"')
    })
  })
})
