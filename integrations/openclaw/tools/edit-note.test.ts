import { beforeEach, describe, expect, it, jest } from "bun:test"
import type { OpenClawPluginApi } from "openclaw/plugin-sdk/plugin-entry"
import type { BmClient } from "../bm-client.ts"
import { registerEditTool } from "./edit-note.ts"

describe("edit tool", () => {
  let mockApi: OpenClawPluginApi
  let mockClient: BmClient

  beforeEach(() => {
    mockApi = {
      registerTool: jest.fn(),
    } as any

    mockClient = {
      editNote: jest.fn(),
    } as any
  })

  describe("registerEditTool", () => {
    it("registers edit_note with MCP-shaped parameters", () => {
      registerEditTool(mockApi, mockClient)

      expect(mockApi.registerTool).toHaveBeenCalledWith(
        expect.objectContaining({
          name: "edit_note",
          parameters: expect.objectContaining({
            type: "object",
            properties: expect.objectContaining({
              identifier: expect.objectContaining({ type: "string" }),
              operation: expect.any(Object),
              content: expect.objectContaining({ type: "string" }),
              find_text: expect.objectContaining({ type: "string" }),
              section: expect.objectContaining({ type: "string" }),
              expected_replacements: expect.objectContaining({
                type: "number",
              }),
              project: expect.objectContaining({ type: "string" }),
            }),
          }),
          execute: expect.any(Function),
        }),
        { name: "edit_note" },
      )
    })
  })

  describe("tool execution", () => {
    let executeFunction: Function

    beforeEach(() => {
      registerEditTool(mockApi, mockClient)
      const registerCall = (mockApi.registerTool as jest.MockedFunction<any>)
        .mock.calls[0]
      executeFunction = registerCall[0].execute
    })

    it("passes append operation through to client", async () => {
      ;(mockClient.editNote as jest.MockedFunction<any>).mockResolvedValue({
        title: "Test Note",
        permalink: "test-note",
        file_path: "notes/test-note.md",
        operation: "append",
        checksum: "abc123",
      })

      const result = await executeFunction("tool-call-id", {
        identifier: "test-note",
        operation: "append",
        content: "\nappended",
      })

      expect(mockClient.editNote).toHaveBeenCalledWith(
        "test-note",
        "append",
        "\nappended",
        {
          find_text: undefined,
          section: undefined,
          expected_replacements: undefined,
        },
        undefined,
      )
      expect(result.details).toEqual({
        title: "Test Note",
        permalink: "test-note",
        file_path: "notes/test-note.md",
        operation: "append",
        checksum: "abc123",
      })
    })

    it("passes find_replace options including expected_replacements", async () => {
      ;(mockClient.editNote as jest.MockedFunction<any>).mockResolvedValue({
        title: "Test Note",
        permalink: "test-note",
        file_path: "notes/test-note.md",
        operation: "find_replace",
        checksum: null,
      })

      await executeFunction("tool-call-id", {
        identifier: "test-note",
        operation: "find_replace",
        content: "new text",
        find_text: "old text",
        expected_replacements: 3,
      })

      expect(mockClient.editNote).toHaveBeenCalledWith(
        "test-note",
        "find_replace",
        "new text",
        {
          find_text: "old text",
          section: undefined,
          expected_replacements: 3,
        },
        undefined,
      )
    })

    it("passes replace_section options", async () => {
      ;(mockClient.editNote as jest.MockedFunction<any>).mockResolvedValue({
        title: "Test Note",
        permalink: "test-note",
        file_path: "notes/test-note.md",
        operation: "replace_section",
      })

      await executeFunction("tool-call-id", {
        identifier: "test-note",
        operation: "replace_section",
        content: "section content",
        section: "## This Week",
      })

      expect(mockClient.editNote).toHaveBeenCalledWith(
        "test-note",
        "replace_section",
        "section content",
        {
          find_text: undefined,
          section: "## This Week",
          expected_replacements: undefined,
        },
        undefined,
      )
    })

    it("passes project to client.editNote", async () => {
      ;(mockClient.editNote as jest.MockedFunction<any>).mockResolvedValue({
        title: "Test Note",
        permalink: "test-note",
        file_path: "notes/test-note.md",
        operation: "append",
      })

      await executeFunction("tool-call-id", {
        identifier: "test-note",
        operation: "append",
        content: "new content",
        project: "other-project",
      })

      expect(mockClient.editNote).toHaveBeenCalledWith(
        "test-note",
        "append",
        "new content",
        {
          find_text: undefined,
          section: undefined,
          expected_replacements: undefined,
        },
        "other-project",
      )
    })

    it("returns friendly error when edit fails", async () => {
      ;(mockClient.editNote as jest.MockedFunction<any>).mockRejectedValue(
        new Error("edit failed"),
      )

      const result = await executeFunction("tool-call-id", {
        identifier: "missing-note",
        operation: "append",
        content: "new",
      })

      expect(result).toEqual({
        content: [
          {
            type: "text",
            text: 'Failed to edit note "missing-note". It may not exist.',
          },
        ],
        details: {
          identifier: "missing-note",
          operation: "append",
          error: "edit_note_failed",
        },
      })
    })
  })
})
