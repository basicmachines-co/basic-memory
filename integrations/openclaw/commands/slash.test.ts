import { describe, expect, it, jest } from "bun:test"
import type { OpenClawPluginApi } from "openclaw/plugin-sdk/plugin-entry"
import type { BmClient } from "../bm-client.ts"
import { registerCommands } from "./slash.ts"

type Command = Parameters<OpenClawPluginApi["registerCommand"]>[0]

describe("native remember command", () => {
  for (const fails of [false, true]) {
    it(`reports ${fails ? "failure" : "the exact write location"} without model narration`, async () => {
      const writeNote = fails
        ? jest.fn().mockRejectedValue(new Error("write failed"))
        : jest.fn().mockResolvedValue({
            permalink: "workspace/project/agent/memories/actual-result",
            file_path: "agent/memories/Actual Result.md",
          })
      const commands: Command[] = []
      registerCommands(
        {
          registerCommand: (command: Command) => commands.push(command),
        } as unknown as OpenClawPluginApi,
        { writeNote } as unknown as BmClient,
      )
      const command = commands.find((entry) => entry.name === "remember")
      expect(command).toBeDefined()
      const response = await command!.handler({
        args: "Remember the renewal deadline",
      } as Parameters<Command["handler"]>[0])
      expect(writeNote).toHaveBeenCalledWith(
        "Remember the renewal deadline",
        "Remember the renewal deadline",
        "agent/memories",
      )
      if (fails) {
        expect(response.text).toContain("Failed to save memory")
        expect(response.text).not.toContain("permalink:")
      } else {
        expect(response.text).toContain(
          "permalink: workspace/project/agent/memories/actual-result",
        )
        expect(response.text).toContain(
          "file_path: agent/memories/Actual Result.md",
        )
      }
    })
  }
})
