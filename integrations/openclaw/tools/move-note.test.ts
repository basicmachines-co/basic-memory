import { beforeEach, describe, expect, it, jest } from "bun:test"

describe("move tool", () => {
  let registeredTool: { name: string; execute: Function } | null = null
  let mockClient: Record<string, Function>
  let mockApi: Record<string, Function>

  beforeEach(() => {
    registeredTool = null
    mockClient = {
      moveNote: jest.fn(),
    }
    mockApi = {
      registerTool: jest.fn((tool: any) => {
        registeredTool = tool
      }),
    }
  })

  async function loadAndRegister() {
    const { registerMoveTool } = await import("./move-note.ts")
    registerMoveTool(mockApi as any, mockClient as any)
    return registeredTool!
  }

  it("should register move_note tool", async () => {
    await loadAndRegister()
    expect(registeredTool).not.toBeNull()
    expect(registeredTool?.name).toBe("move_note")
  })

  it("should move a note successfully", async () => {
    const tool = await loadAndRegister()
    ;(mockClient.moveNote as any).mockResolvedValue({
      title: "my-note",
      permalink: "archive/my-note",
      file_path: "archive/my-note.md",
    })

    const result = await tool.execute("call-1", {
      identifier: "notes/my-note",
      newFolder: "archive",
    })
    expect(result.content[0].text).toContain("Moved")
    expect(result.content[0].text).toContain("archive/my-note.md")
  })

  it("should pass project to client.moveNote", async () => {
    const tool = await loadAndRegister()
    ;(mockClient.moveNote as any).mockResolvedValue({
      title: "my-note",
      permalink: "archive/my-note",
      file_path: "archive/my-note.md",
    })

    await tool.execute("call-1", {
      identifier: "notes/my-note",
      newFolder: "archive",
      project: "other-project",
    })
    expect(mockClient.moveNote).toHaveBeenCalledWith(
      "notes/my-note",
      "archive",
      "other-project",
    )
  })

  it("should handle move failure", async () => {
    const tool = await loadAndRegister()
    ;(mockClient.moveNote as any).mockRejectedValue(new Error("not found"))

    const result = await tool.execute("call-1", {
      identifier: "nonexistent",
      newFolder: "archive",
    })
    expect(result.content[0].text).toContain("Failed to move")
  })
})
