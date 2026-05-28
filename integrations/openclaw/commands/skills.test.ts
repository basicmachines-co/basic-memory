import { describe, expect, it, jest } from "bun:test"
import type { OpenClawPluginApi } from "openclaw/plugin-sdk/plugin-entry"
import { registerSkillCommands } from "./skills.ts"

describe("skill slash commands", () => {
  it("should register all skill commands", () => {
    const mockApi = {
      registerCommand: jest.fn(),
    } as unknown as OpenClawPluginApi

    registerSkillCommands(mockApi)

    const callCount = (mockApi.registerCommand as jest.MockedFunction<any>).mock
      .calls.length
    expect(callCount).toBeGreaterThanOrEqual(9)

    const names = (
      mockApi.registerCommand as jest.MockedFunction<any>
    ).mock.calls.map((call: any[]) => call[0].name)
    expect(names).toEqual(
      expect.arrayContaining([
        "tasks",
        "reflect",
        "defrag",
        "schema",
        "notes",
        "metadata-search",
        "lifecycle",
        "ingest",
        "research",
      ]),
    )
  })

  it("should set correct metadata on each command", () => {
    const mockApi = {
      registerCommand: jest.fn(),
    } as unknown as OpenClawPluginApi

    registerSkillCommands(mockApi)

    for (const call of (mockApi.registerCommand as jest.MockedFunction<any>)
      .mock.calls) {
      const cmd = call[0]
      expect(cmd.acceptsArgs).toBe(true)
      expect(cmd.requireAuth).toBe(true)
      expect(typeof cmd.description).toBe("string")
      expect(cmd.description.length).toBeGreaterThan(0)
    }
  })

  describe("handler behavior", () => {
    let commands: Record<string, any>

    function setup(): void {
      commands = {}
      const mockApi = {
        registerCommand: jest.fn((cmd: any) => {
          commands[cmd.name] = cmd
        }),
      } as unknown as OpenClawPluginApi

      registerSkillCommands(mockApi)
    }

    it("should return skill content without prefix when no args", async () => {
      setup()

      const result = await commands.tasks.handler({})
      expect(result.text).toStartWith("Follow this workflow:\n\n")
      expect(result.text).toContain("# Memory Tasks")
    })

    it("should return skill content with empty args trimmed", async () => {
      setup()

      const result = await commands.reflect.handler({ args: "   " })
      expect(result.text).toStartWith("Follow this workflow:\n\n")
      expect(result.text).toContain("# Memory Reflect")
    })

    it("should prepend user request when args provided", async () => {
      setup()

      const result = await commands.defrag.handler({
        args: "clean up old tasks",
      })
      expect(result.text).toStartWith(
        "User request: clean up old tasks\n\nFollow this workflow:\n\n",
      )
      expect(result.text).toContain("# Memory Defrag")
    })

    it("should include full skill content for each command", async () => {
      setup()

      const tasksResult = await commands.tasks.handler({})
      expect(tasksResult.text).toContain("## Task Schema")

      const reflectResult = await commands.reflect.handler({})
      expect(reflectResult.text).toContain("## When to Run")

      const defragResult = await commands.defrag.handler({})
      expect(defragResult.text).toContain("## When to Run")

      const schemaResult = await commands.schema.handler({})
      expect(schemaResult.text).toContain("## Picoschema Syntax Reference")

      const notesResult = await commands.notes.handler({})
      expect(notesResult.text).toContain("## Note Anatomy")

      const metadataResult = await commands["metadata-search"].handler({})
      expect(metadataResult.text).toContain("## Filter Syntax")

      const lifecycleResult = await commands.lifecycle.handler({})
      expect(lifecycleResult.text).toContain("# Memory Lifecycle")

      const ingestResult = await commands.ingest.handler({})
      expect(ingestResult.text).toContain("# Memory Ingest")

      const researchResult = await commands.research.handler({})
      expect(researchResult.text).toContain("# Memory Research")
    })
  })
})
