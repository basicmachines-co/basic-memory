import { describe, expect, it } from "bun:test"
import { homedir } from "node:os"
import { parseConfig, resolveProjectPath } from "./config.ts"

describe("config", () => {
  describe("parseConfig", () => {
    it("should return default config for empty input", () => {
      const config = parseConfig(undefined)

      expect(config.bmPath).toBe("bm")
      expect(config.memoryDir).toBe("memory/")
      expect(config.memoryFile).toBe("MEMORY.md")
      expect(config.autoCapture).toBe(true)
      expect(config.captureMinChars).toBe(10)
      expect(config.autoRecall).toBe(true)
      expect(config.recallPrompt).toContain("Check for active tasks")
      expect(config.debug).toBe(false)
      expect(config.project).toMatch(/^openclaw-/)
      expect(config.projectPath).toBe(".")
    })

    it("should return default config for null input", () => {
      const config = parseConfig(null)
      expect(config.memoryDir).toBe("memory/")
    })

    it("should return default config for non-object input", () => {
      expect(parseConfig("string").memoryDir).toBe("memory/")
      expect(parseConfig(123).memoryDir).toBe("memory/")
      expect(parseConfig([]).memoryDir).toBe("memory/")
    })

    it("should use provided project name", () => {
      const config = parseConfig({ project: "my-custom-project" })
      expect(config.project).toBe("my-custom-project")
    })

    it("should use default project for empty string", () => {
      const config = parseConfig({ project: "" })
      expect(config.project).toMatch(/^openclaw-/)
    })

    it("should use provided bmPath", () => {
      const config = parseConfig({ bmPath: "/custom/path/to/bm" })
      expect(config.bmPath).toBe("/custom/path/to/bm")
    })

    it("should use provided memoryDir", () => {
      const config = parseConfig({ memoryDir: "notes/" })
      expect(config.memoryDir).toBe("notes/")
    })

    it("should support snake_case memory_dir", () => {
      const config = parseConfig({ memory_dir: "notes/" })
      expect(config.memoryDir).toBe("notes/")
    })

    it("should use provided memoryFile", () => {
      const config = parseConfig({ memoryFile: "MY_MEMORY.md" })
      expect(config.memoryFile).toBe("MY_MEMORY.md")
    })

    it("should support snake_case memory_file", () => {
      const config = parseConfig({ memory_file: "MY_MEMORY.md" })
      expect(config.memoryFile).toBe("MY_MEMORY.md")
    })

    it("should use provided projectPath", () => {
      const config = parseConfig({ projectPath: "/custom/project/path" })
      expect(config.projectPath).toBe("/custom/project/path")
    })

    it("should default projectPath to workspace root", () => {
      const config = parseConfig({ memoryDir: "notes/" })
      expect(config.projectPath).toBe(".")
    })

    it("should use provided autoCapture", () => {
      expect(parseConfig({ autoCapture: false }).autoCapture).toBe(false)
      expect(parseConfig({ autoCapture: true }).autoCapture).toBe(true)
    })

    it("should use provided debug flag", () => {
      expect(parseConfig({ debug: true }).debug).toBe(true)
      expect(parseConfig({ debug: false }).debug).toBe(false)
    })

    it("should use provided captureMinChars", () => {
      expect(parseConfig({ captureMinChars: 25 }).captureMinChars).toBe(25)
      expect(parseConfig({ captureMinChars: 0 }).captureMinChars).toBe(0)
    })

    it("should support snake_case capture_min_chars", () => {
      expect(parseConfig({ capture_min_chars: 50 }).captureMinChars).toBe(50)
    })

    it("should default captureMinChars for non-number input", () => {
      expect(parseConfig({ captureMinChars: "abc" }).captureMinChars).toBe(10)
      expect(parseConfig({ captureMinChars: -5 }).captureMinChars).toBe(10)
    })

    it("should use provided autoRecall", () => {
      expect(parseConfig({ autoRecall: false }).autoRecall).toBe(false)
      expect(parseConfig({ autoRecall: true }).autoRecall).toBe(true)
    })

    it("should support snake_case auto_recall", () => {
      expect(parseConfig({ auto_recall: false }).autoRecall).toBe(false)
    })

    it("should default autoRecall to true", () => {
      expect(parseConfig({}).autoRecall).toBe(true)
    })

    it("should use provided recallPrompt", () => {
      const config = parseConfig({ recallPrompt: "Custom prompt" })
      expect(config.recallPrompt).toBe("Custom prompt")
    })

    it("should support snake_case recall_prompt", () => {
      const config = parseConfig({ recall_prompt: "Custom snake" })
      expect(config.recallPrompt).toBe("Custom snake")
    })

    it("should default recallPrompt for empty string", () => {
      const config = parseConfig({ recallPrompt: "" })
      expect(config.recallPrompt).toContain("Check for active tasks")
    })

    it("should reject cloud config", () => {
      expect(() =>
        parseConfig({
          cloud: {
            url: "https://cloud.basicmemory.com",
            api_key: "test-key",
          },
        }),
      ).toThrow("basic-memory config has unknown keys: cloud")
    })

    it("should throw error for unknown config keys", () => {
      expect(() => parseConfig({ unknownKey: "value" })).toThrow(
        "basic-memory config has unknown keys: unknownKey",
      )
    })

    it("should handle complete config object", () => {
      const config = parseConfig({
        project: "test-project",
        bmPath: "/usr/bin/bm",
        memoryDir: "notes/",
        memoryFile: "NOTES.md",
        projectPath: "/tmp/test-project",
        autoCapture: false,
        debug: true,
      })

      expect(config.project).toBe("test-project")
      expect(config.memoryDir).toBe("notes/")
      expect(config.memoryFile).toBe("NOTES.md")
    })

    it("should not throw for empty config", () => {
      expect(() => parseConfig({})).not.toThrow()
    })
  })

  describe("resolveProjectPath", () => {
    it("resolves relative projectPath against workspace", () => {
      expect(resolveProjectPath("memory/", "/tmp/workspace")).toBe(
        "/tmp/workspace/memory",
      )
    })

    it("expands tilde paths", () => {
      expect(resolveProjectPath("~/memory", "/tmp/workspace")).toBe(
        `${homedir()}/memory`,
      )
    })

    it("keeps absolute paths unchanged", () => {
      expect(resolveProjectPath("/var/data/memory", "/tmp/workspace")).toBe(
        "/var/data/memory",
      )
    })
  })
})
