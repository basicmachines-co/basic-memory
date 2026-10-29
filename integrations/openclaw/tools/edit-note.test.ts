import { beforeEach, describe, expect, it, jest } from "bun:test"
import type { OpenClawPluginApi } from "openclaw/plugin-sdk/plugin-entry"
import { type BmClient, BmToolError } from "../bm-client.ts"
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
              metadata: expect.objectContaining({
                type: "object",
                additionalProperties: true,
              }),
            }),
          }),
          execute: expect.any(Function),
        }),
        { name: "edit_note" },
      )
    })

    it("tells the model to cite the result permalink (#1341)", () => {
      registerEditTool(mockApi, mockClient)

      const toolConfig = (mockApi.registerTool as jest.MockedFunction<any>).mock
        .calls[0][0]
      expect(toolConfig.description).toContain("permalink")
      expect(toolConfig.description).toContain(
        "never report a save that no tool call performed",
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
          metadata: undefined,
        },
        undefined,
      )
      // The description tells agents to cite file_path, so it must be in the text.
      expect(result.content[0].text).toBe(
        "Note updated: Test Note (test-note)\nFile: notes/test-note.md",
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
          metadata: undefined,
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
          metadata: undefined,
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
          metadata: undefined,
        },
        "other-project",
      )
    })

    it("passes metadata through independently of the edit operation", async () => {
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
        metadata: {
          status: "complete",
          review: { approved: true },
        },
      })

      expect(mockClient.editNote).toHaveBeenCalledWith(
        "test-note",
        "append",
        "new content",
        {
          find_text: undefined,
          section: undefined,
          expected_replacements: undefined,
          metadata: {
            status: "complete",
            review: { approved: true },
          },
        },
        undefined,
      )
    })

    it("passes insert_after_section and its heading through to bm_edit", async () => {
      ;(mockClient.editNote as jest.MockedFunction<any>).mockResolvedValue({
        title: "Test Note",
        permalink: "test-note",
        file_path: "notes/test-note.md",
        operation: "insert_after_section",
      })

      await executeFunction("tool-call-id", {
        identifier: "test-note",
        operation: "insert_after_section",
        content: "- [test] a new observation",
        section: "Observations",
      })

      expect(mockClient.editNote).toHaveBeenCalledWith(
        "test-note",
        "insert_after_section",
        "- [test] a new observation",
        {
          find_text: undefined,
          section: "Observations",
          expected_replacements: undefined,
          metadata: undefined,
        },
        undefined,
      )
    })

    // Regression: every failure used to read "It may not exist", which hid
    // the server's actual reason (here, `section` passed with append).
    it("shows the server's reason when bm_edit refuses the arguments", async () => {
      ;(mockClient.editNote as jest.MockedFunction<any>).mockRejectedValue(
        new BmToolError(
          "bm_edit",
          JSON.stringify({
            error: "INVALID_ARGUMENTS",
            message: "`section` is only for section commands, not append.",
          }),
        ),
      )

      const result = await executeFunction("tool-call-id", {
        identifier: "test-note",
        operation: "append",
        content: "new",
        section: "Observations",
      })

      expect(result).toEqual({
        content: [
          {
            type: "text",
            text: 'Failed to edit note "test-note": INVALID_ARGUMENTS: `section` is only for section commands, not append.',
          },
        ],
        details: {
          identifier: "test-note",
          operation: "append",
          error: "edit_note_failed",
        },
      })
    })

    it("says a missing note does not exist", async () => {
      ;(mockClient.editNote as jest.MockedFunction<any>).mockRejectedValue(
        new BmToolError(
          "bm_edit",
          JSON.stringify({ error: "NOT_FOUND", message: "Note not found" }),
        ),
      )

      const result = await executeFunction("tool-call-id", {
        identifier: "missing-note",
        operation: "append",
        content: "new",
      })

      expect(result.content[0].text).toBe(
        'Failed to edit note "missing-note": it does not exist. NOT_FOUND: Note not found',
      )
    })

    it("passes through a non-tool error message", async () => {
      ;(mockClient.editNote as jest.MockedFunction<any>).mockRejectedValue(
        new Error("BM MCP unavailable: connection closed"),
      )

      const result = await executeFunction("tool-call-id", {
        identifier: "test-note",
        operation: "append",
        content: "new",
      })

      expect(result.content[0].text).toBe(
        'Failed to edit note "test-note": BM MCP unavailable: connection closed',
      )
    })
  })
})
