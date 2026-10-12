import { beforeEach, describe, expect, it, jest } from "bun:test"
import type { OpenClawPluginApi } from "openclaw/plugin-sdk/plugin-entry"
import type { BmClient } from "../bm-client.ts"
import { registerReadTool } from "./read-note.ts"

describe("read tool", () => {
  let mockApi: OpenClawPluginApi
  let mockClient: BmClient

  beforeEach(() => {
    mockApi = {
      registerTool: jest.fn(),
    } as any

    mockClient = {
      readNote: jest.fn(),
    } as any
  })

  describe("registerReadTool", () => {
    it("registers read_note with include_frontmatter parameter", () => {
      registerReadTool(mockApi, mockClient)

      expect(mockApi.registerTool).toHaveBeenCalledWith(
        expect.objectContaining({
          name: "read_note",
          parameters: expect.objectContaining({
            type: "object",
            properties: expect.objectContaining({
              identifier: expect.objectContaining({ type: "string" }),
              include_frontmatter: expect.objectContaining({ type: "boolean" }),
              project: expect.objectContaining({ type: "string" }),
            }),
          }),
          execute: expect.any(Function),
        }),
        { name: "read_note" },
      )
    })
  })

  describe("tool execution", () => {
    let executeFunction: Function

    beforeEach(() => {
      registerReadTool(mockApi, mockClient)
      const registerCall = (mockApi.registerTool as jest.MockedFunction<any>)
        .mock.calls[0]
      executeFunction = registerCall[0].execute
    })

    it("reads note with stripped content by default", async () => {
      const mockNote = {
        title: "Test Note",
        permalink: "test-note",
        content: "Body only content",
        file_path: "notes/test-note.md",
        frontmatter: { title: "Test Note", status: "active" },
      }

      ;(mockClient.readNote as jest.MockedFunction<any>).mockResolvedValue(
        mockNote,
      )

      const result = await executeFunction("tool-call-id", {
        identifier: "test-note",
      })

      expect(mockClient.readNote).toHaveBeenCalledWith(
        "test-note",
        { includeFrontmatter: false },
        undefined,
      )
      expect(result).toEqual({
        content: [
          {
            type: "text",
            text: "Body only content",
          },
        ],
        details: {
          title: "Test Note",
          permalink: "test-note",
          file_path: "notes/test-note.md",
          frontmatter: { title: "Test Note", status: "active" },
          checksum: null,
        },
      })
    })

    it("reads note with raw content when include_frontmatter is true", async () => {
      const raw =
        "---\ntitle: Test Note\nstatus: active\n---\n\nBody only content"
      const mockNote = {
        title: "Test Note",
        permalink: "test-note",
        content: raw,
        file_path: "notes/test-note.md",
        frontmatter: { title: "Test Note", status: "active" },
      }

      ;(mockClient.readNote as jest.MockedFunction<any>).mockResolvedValue(
        mockNote,
      )

      const result = await executeFunction("tool-call-id", {
        identifier: "test-note",
        include_frontmatter: true,
      })

      expect(mockClient.readNote).toHaveBeenCalledWith(
        "test-note",
        { includeFrontmatter: true },
        undefined,
      )
      expect(result.content[0].text).toBe(raw)
      expect(result.details.frontmatter).toEqual({
        title: "Test Note",
        status: "active",
      })
    })

    it("returns null frontmatter details when not present", async () => {
      ;(mockClient.readNote as jest.MockedFunction<any>).mockResolvedValue({
        title: "No FM",
        permalink: "no-fm",
        content: "content",
        file_path: "notes/no-fm.md",
      })

      const result = await executeFunction("tool-call-id", {
        identifier: "no-fm",
      })

      expect(result.details.frontmatter).toBeNull()
    })

    it("passes project to client.readNote", async () => {
      ;(mockClient.readNote as jest.MockedFunction<any>).mockResolvedValue({
        title: "Test",
        permalink: "test",
        content: "content",
        file_path: "test.md",
      })

      await executeFunction("tool-call-id", {
        identifier: "test",
        project: "other-project",
      })

      expect(mockClient.readNote).toHaveBeenCalledWith(
        "test",
        { includeFrontmatter: false },
        "other-project",
      )
    })

    it("routes by project_id and surfaces the checksum to the model", async () => {
      ;(mockClient.readNote as jest.MockedFunction<any>).mockResolvedValue({
        title: "Memory Master",
        permalink: "memory-quest/memory-master",
        content: "---\ntype: memory_master\n---\n\nbody",
        file_path: "memory-quest/Memory Master.md",
        frontmatter: { type: "memory_master" },
        checksum: "abc123",
      })

      const result = await executeFunction("tool-call-id", {
        identifier: "memory-quest/memory-master",
        include_frontmatter: true,
        project_id: "6b1c3f0e-0f5e-4d6b-9a39-2a4f8c1d7e55",
      })

      expect(mockClient.readNote).toHaveBeenCalledWith(
        "memory-quest/memory-master",
        {
          includeFrontmatter: true,
          projectId: "6b1c3f0e-0f5e-4d6b-9a39-2a4f8c1d7e55",
        },
        undefined,
      )
      expect(result.content[0].text).toBe(
        "---\ntype: memory_master\n---\n\nbody\n\nChecksum: abc123",
      )
      expect(result.details.checksum).toBe("abc123")
      expect(result.details.frontmatter).toEqual({ type: "memory_master" })
    })

    it("handles errors gracefully", async () => {
      ;(mockClient.readNote as jest.MockedFunction<any>).mockRejectedValue(
        new Error("not found"),
      )

      const result = await executeFunction("tool-call-id", {
        identifier: "missing-note",
      })

      expect(result).toEqual({
        content: [
          {
            type: "text",
            text: 'Could not read note "missing-note". It may not exist yet.',
          },
        ],
        details: {
          identifier: "missing-note",
          include_frontmatter: false,
          error: "read_note_failed",
        },
      })
    })
  })
})
