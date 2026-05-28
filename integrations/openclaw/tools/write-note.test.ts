import { beforeEach, describe, expect, it, jest } from "bun:test"
import type { OpenClawPluginApi } from "openclaw/plugin-sdk/plugin-entry"
import type { BmClient } from "../bm-client.ts"
import { NoteAlreadyExistsError } from "../bm-client.ts"
import { registerWriteTool } from "./write-note.ts"

describe("write tool", () => {
  let mockApi: OpenClawPluginApi
  let mockClient: BmClient

  beforeEach(() => {
    mockApi = {
      registerTool: jest.fn(),
    } as any

    mockClient = {
      writeNote: jest.fn(),
    } as any
  })

  describe("registerWriteTool", () => {
    it("should register write_note tool with correct configuration", () => {
      registerWriteTool(mockApi, mockClient)

      expect(mockApi.registerTool).toHaveBeenCalledWith(
        expect.objectContaining({
          name: "write_note",
          label: "Write Note",
          description: expect.stringContaining(
            "Create a note in the Basic Memory knowledge graph",
          ),
          parameters: expect.objectContaining({
            type: "object",
            properties: expect.objectContaining({
              title: expect.objectContaining({
                type: "string",
                description: "Note title",
              }),
              content: expect.objectContaining({
                type: "string",
                description: "Note content in Markdown format",
              }),
              folder: expect.objectContaining({
                type: "string",
                description: "Folder to write the note in",
              }),
              project: expect.objectContaining({
                type: "string",
              }),
              overwrite: expect.objectContaining({
                type: "boolean",
              }),
            }),
          }),
          execute: expect.any(Function),
        }),
        { name: "write_note" },
      )
    })
  })

  describe("tool execution", () => {
    let executeFunction: Function

    beforeEach(() => {
      registerWriteTool(mockApi, mockClient)
      const registerCall = (mockApi.registerTool as jest.MockedFunction<any>)
        .mock.calls[0]
      executeFunction = registerCall[0].execute
    })

    it("should write note with title, content, and folder", async () => {
      const mockResult = {
        title: "Test Note",
        permalink: "test-note",
        content: "This is test content",
        file_path: "notes/test-note.md",
      }

      ;(mockClient.writeNote as jest.MockedFunction<any>).mockResolvedValue(
        mockResult,
      )

      const result = await executeFunction("tool-call-id", {
        title: "Test Note",
        content: "This is test content",
        folder: "notes",
      })

      expect(mockClient.writeNote).toHaveBeenCalledWith(
        "Test Note",
        "This is test content",
        "notes",
        undefined,
        undefined,
      )

      expect(result).toEqual({
        content: [
          {
            type: "text",
            text: "Note saved: Test Note (test-note)",
          },
        ],
        details: {
          title: "Test Note",
          permalink: "test-note",
          file_path: "notes/test-note.md",
        },
      })
    })

    it("should handle empty folder", async () => {
      const mockResult = {
        title: "Root Note",
        permalink: "root-note",
        content: "Root level note",
        file_path: "root-note.md",
      }

      ;(mockClient.writeNote as jest.MockedFunction<any>).mockResolvedValue(
        mockResult,
      )

      await executeFunction("tool-call-id", {
        title: "Root Note",
        content: "Root level note",
        folder: "",
      })

      expect(mockClient.writeNote).toHaveBeenCalledWith(
        "Root Note",
        "Root level note",
        "",
        undefined,
        undefined,
      )
    })

    it("should handle nested folder paths", async () => {
      const mockResult = {
        title: "Nested Note",
        permalink: "nested-note",
        content: "Nested folder note",
        file_path: "projects/subfolder/nested-note.md",
      }

      ;(mockClient.writeNote as jest.MockedFunction<any>).mockResolvedValue(
        mockResult,
      )

      await executeFunction("tool-call-id", {
        title: "Nested Note",
        content: "Nested folder note",
        folder: "projects/subfolder",
      })

      expect(mockClient.writeNote).toHaveBeenCalledWith(
        "Nested Note",
        "Nested folder note",
        "projects/subfolder",
        undefined,
        undefined,
      )
    })

    it("should handle markdown content formatting", async () => {
      const markdownContent = `# Main Heading

## Sub Heading

This is **bold** and *italic* text.

- List item 1
- List item 2

\`\`\`javascript
const code = "example";
\`\`\`

> Blockquote text

[Link](https://example.com)`

      const mockResult = {
        title: "Markdown Note",
        permalink: "markdown-note",
        content: markdownContent,
        file_path: "notes/markdown-note.md",
      }

      ;(mockClient.writeNote as jest.MockedFunction<any>).mockResolvedValue(
        mockResult,
      )

      await executeFunction("tool-call-id", {
        title: "Markdown Note",
        content: markdownContent,
        folder: "notes",
      })

      expect(mockClient.writeNote).toHaveBeenCalledWith(
        "Markdown Note",
        markdownContent,
        "notes",
        undefined,
        undefined,
      )
    })

    it("should handle special characters in title", async () => {
      const specialTitle = "Note with Special Characters: @#$%^&*()"
      const mockResult = {
        title: specialTitle,
        permalink: "note-with-special-characters",
        content: "Content",
        file_path: "notes/note-with-special-characters.md",
      }

      ;(mockClient.writeNote as jest.MockedFunction<any>).mockResolvedValue(
        mockResult,
      )

      await executeFunction("tool-call-id", {
        title: specialTitle,
        content: "Content",
        folder: "notes",
      })

      expect(mockClient.writeNote).toHaveBeenCalledWith(
        specialTitle,
        "Content",
        "notes",
        undefined,
        undefined,
      )
    })

    it("should handle unicode characters", async () => {
      const unicodeTitle = "Unicode Note 🚀 中文 العربية"
      const unicodeContent = "Content with unicode: 🎉 多言語対応 привет"

      const mockResult = {
        title: unicodeTitle,
        permalink: "unicode-note",
        content: unicodeContent,
        file_path: "notes/unicode-note.md",
      }

      ;(mockClient.writeNote as jest.MockedFunction<any>).mockResolvedValue(
        mockResult,
      )

      await executeFunction("tool-call-id", {
        title: unicodeTitle,
        content: unicodeContent,
        folder: "notes",
      })

      expect(mockClient.writeNote).toHaveBeenCalledWith(
        unicodeTitle,
        unicodeContent,
        "notes",
        undefined,
        undefined,
      )
    })

    it("should handle very long content", async () => {
      const longContent = "Very long content.\n".repeat(1000)
      const mockResult = {
        title: "Long Note",
        permalink: "long-note",
        content: longContent,
        file_path: "notes/long-note.md",
      }

      ;(mockClient.writeNote as jest.MockedFunction<any>).mockResolvedValue(
        mockResult,
      )

      await executeFunction("tool-call-id", {
        title: "Long Note",
        content: longContent,
        folder: "notes",
      })

      expect(mockClient.writeNote).toHaveBeenCalledWith(
        "Long Note",
        longContent,
        "notes",
        undefined,
        undefined,
      )
    })

    it("should handle empty content", async () => {
      const mockResult = {
        title: "Empty Note",
        permalink: "empty-note",
        content: "",
        file_path: "notes/empty-note.md",
      }

      ;(mockClient.writeNote as jest.MockedFunction<any>).mockResolvedValue(
        mockResult,
      )

      await executeFunction("tool-call-id", {
        title: "Empty Note",
        content: "",
        folder: "notes",
      })

      expect(mockClient.writeNote).toHaveBeenCalledWith(
        "Empty Note",
        "",
        "notes",
        undefined,
        undefined,
      )
    })

    it("should pass project to client.writeNote", async () => {
      ;(mockClient.writeNote as jest.MockedFunction<any>).mockResolvedValue({
        title: "Note",
        permalink: "note",
        content: "content",
        file_path: "notes/note.md",
      })

      await executeFunction("tool-call-id", {
        title: "Note",
        content: "content",
        folder: "notes",
        project: "other-project",
      })

      expect(mockClient.writeNote).toHaveBeenCalledWith(
        "Note",
        "content",
        "notes",
        "other-project",
        undefined,
      )
    })

    it("should pass overwrite=true to client.writeNote", async () => {
      ;(mockClient.writeNote as jest.MockedFunction<any>).mockResolvedValue({
        title: "Note",
        permalink: "note",
        content: "content",
        file_path: "notes/note.md",
        action: "updated",
      })

      const result = await executeFunction("tool-call-id", {
        title: "Note",
        content: "content",
        folder: "notes",
        overwrite: true,
      })

      expect(mockClient.writeNote).toHaveBeenCalledWith(
        "Note",
        "content",
        "notes",
        undefined,
        true,
      )

      expect(result.content[0].text).toContain("Note saved: Note")
    })

    it("should return helpful hint when note already exists", async () => {
      ;(mockClient.writeNote as jest.MockedFunction<any>).mockRejectedValue(
        new NoteAlreadyExistsError("Existing Note", "notes/existing-note"),
      )

      const result = await executeFunction("tool-call-id", {
        title: "Existing Note",
        content: "New content",
        folder: "notes",
      })

      const text = result.content[0].text
      expect(text).toContain('Note already exists: "Existing Note"')
      expect(text).toContain("notes/existing-note")
      expect(text).toContain("edit_note")
      expect(text).toContain("overwrite=true")
      expect(text).toContain("read_note")
    })

    it("should handle write errors gracefully", async () => {
      const writeError = new Error("Failed to write note")
      ;(mockClient.writeNote as jest.MockedFunction<any>).mockRejectedValue(
        writeError,
      )

      const result = await executeFunction("tool-call-id", {
        title: "Failed Note",
        content: "Content",
        folder: "notes",
      })

      expect(result).toEqual({
        content: [
          {
            type: "text",
            text: "Failed to write note. Is Basic Memory running? Check logs for details.",
          },
        ],
        details: {
          title: "Failed Note",
          folder: "notes",
          error: "write_note_failed",
        },
      })
    })

    it("should preserve whitespace and formatting in content", async () => {
      const formattedContent = `    Indented content
        More indentation

Normal line
    Back to indented

\t\tTab indentation`

      const mockResult = {
        title: "Formatted Note",
        permalink: "formatted-note",
        content: formattedContent,
        file_path: "notes/formatted-note.md",
      }

      ;(mockClient.writeNote as jest.MockedFunction<any>).mockResolvedValue(
        mockResult,
      )

      await executeFunction("tool-call-id", {
        title: "Formatted Note",
        content: formattedContent,
        folder: "notes",
      })

      expect(mockClient.writeNote).toHaveBeenCalledWith(
        "Formatted Note",
        formattedContent,
        "notes",
        undefined,
        undefined,
      )
    })

    it("should handle titles with line breaks", async () => {
      const multilineTitle = "Title with\nLine Break"
      const mockResult = {
        title: multilineTitle,
        permalink: "title-with-line-break",
        content: "Content",
        file_path: "notes/title-with-line-break.md",
      }

      ;(mockClient.writeNote as jest.MockedFunction<any>).mockResolvedValue(
        mockResult,
      )

      await executeFunction("tool-call-id", {
        title: multilineTitle,
        content: "Content",
        folder: "notes",
      })

      expect(mockClient.writeNote).toHaveBeenCalledWith(
        multilineTitle,
        "Content",
        "notes",
        undefined,
        undefined,
      )
    })

    it("should include all result details in response", async () => {
      const mockResult = {
        title: "Detailed Note",
        permalink: "detailed-note-permalink",
        content: "Detailed content",
        file_path: "custom/folder/detailed-note.md",
      }

      ;(mockClient.writeNote as jest.MockedFunction<any>).mockResolvedValue(
        mockResult,
      )

      const result = await executeFunction("tool-call-id", {
        title: "Detailed Note",
        content: "Detailed content",
        folder: "custom/folder",
      })

      expect(result.details).toEqual({
        title: "Detailed Note",
        permalink: "detailed-note-permalink",
        file_path: "custom/folder/detailed-note.md",
      })
    })

    it("should handle network or service errors", async () => {
      const networkError = new Error("Connection refused")
      networkError.code = "ECONNREFUSED"
      ;(mockClient.writeNote as jest.MockedFunction<any>).mockRejectedValue(
        networkError,
      )

      const result = await executeFunction("tool-call-id", {
        title: "Network Note",
        content: "Content",
        folder: "notes",
      })

      expect(result.content[0].text).toContain(
        "Failed to write note. Is Basic Memory running?",
      )
    })

    it("should handle concurrent writes", async () => {
      const mockResults = [
        {
          title: "Note 1",
          permalink: "note-1",
          content: "Content 1",
          file_path: "notes/note-1.md",
        },
        {
          title: "Note 2",
          permalink: "note-2",
          content: "Content 2",
          file_path: "notes/note-2.md",
        },
      ]

      ;(mockClient.writeNote as jest.MockedFunction<any>)
        .mockResolvedValueOnce(mockResults[0])
        .mockResolvedValueOnce(mockResults[1])

      const promises = [
        executeFunction("tool-call-1", {
          title: "Note 1",
          content: "Content 1",
          folder: "notes",
        }),
        executeFunction("tool-call-2", {
          title: "Note 2",
          content: "Content 2",
          folder: "notes",
        }),
      ]

      const results = await Promise.all(promises)

      expect(results[0].content[0].text).toContain("Note saved: Note 1")
      expect(results[1].content[0].text).toContain("Note saved: Note 2")
      expect(mockClient.writeNote).toHaveBeenCalledTimes(2)
    })
  })
})
