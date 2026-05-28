import { afterEach, describe, expect, it, jest } from "bun:test"
import { existsSync, rmSync } from "node:fs"
import { BmClient } from "./bm-client.ts"
import plugin, { isCommandAvailable } from "./index.ts"

describe("plugin service lifecycle", () => {
  afterEach(() => {
    jest.restoreAllMocks()
  })

  it("starts MCP client, ensures project path, and stops cleanly", async () => {
    const startSpy = jest
      .spyOn(BmClient.prototype, "start")
      .mockResolvedValue(undefined)
    const ensureProjectSpy = jest
      .spyOn(BmClient.prototype, "ensureProject")
      .mockResolvedValue(undefined)
    const readNoteSpy = jest
      .spyOn(BmClient.prototype, "readNote")
      .mockRejectedValue(new Error("Entity not found"))
    const writeNoteSpy = jest
      .spyOn(BmClient.prototype, "writeNote")
      .mockResolvedValue(undefined as any)
    const stopSpy = jest
      .spyOn(BmClient.prototype, "stop")
      .mockResolvedValue(undefined)

    const services: Array<{
      id: string
      start: (ctx: { workspaceDir?: string }) => Promise<void>
      stop: () => Promise<void>
    }> = []

    const api = {
      pluginConfig: {
        bmPath: process.execPath,
        project: "test-project",
        projectPath: "memory/",
      },
      logger: {
        info: jest.fn(),
        warn: jest.fn(),
        error: jest.fn(),
        debug: jest.fn(),
      },
      registerTool: jest.fn(),
      registerCommand: jest.fn(),
      registerCli: jest.fn(),
      registerContextEngine: jest.fn(),
      registerService: jest.fn((service: any) => {
        services.push(service)
      }),
      on: jest.fn(),
    }

    plugin.register(api as any)

    expect(services).toHaveLength(1)
    expect(api.registerContextEngine).toHaveBeenCalledWith(
      "openclaw-basic-memory",
      expect.any(Function),
    )
    expect(api.on).not.toHaveBeenCalled()

    await services[0].start({ workspaceDir: "/tmp/workspace" })

    expect(startSpy).toHaveBeenCalledWith({ cwd: "/tmp/workspace" })
    expect(ensureProjectSpy).toHaveBeenCalledWith("/tmp/workspace/memory")

    // Schema seed: readNote throws "not found" → writeNote called
    expect(readNoteSpy).toHaveBeenCalledWith("schema/Task")
    expect(writeNoteSpy).toHaveBeenCalledWith(
      "Task",
      expect.stringContaining("type: schema"),
      "schema",
    )

    await services[0].stop()

    expect(stopSpy).toHaveBeenCalledTimes(1)
  })

  it("checks configured bmPath without invoking a shell", () => {
    const marker = `/tmp/openclaw-bm-shell-${process.pid}`
    rmSync(marker, { force: true })

    expect(isCommandAvailable(`bm; touch ${marker}`)).toBe(false)
    expect(existsSync(marker)).toBe(false)
  })
})
