import { beforeEach, describe, expect, it, jest } from "bun:test"
import type { OpenClawPluginApi } from "openclaw/plugin-sdk/plugin-entry"
import type { BmClient } from "../bm-client.ts"
import { registerProjectListTool } from "./list-memory-projects.ts"

describe("project list tool", () => {
  let mockApi: OpenClawPluginApi
  let mockClient: BmClient

  beforeEach(() => {
    mockApi = {
      registerTool: jest.fn(),
    } as any

    mockClient = {
      listProjects: jest.fn(),
    } as any
  })

  it("registers list_memory_projects with expected shape", () => {
    registerProjectListTool(mockApi, mockClient)

    expect(mockApi.registerTool).toHaveBeenCalledWith(
      expect.objectContaining({
        name: "list_memory_projects",
        label: "List Projects",
        description: expect.stringContaining(
          "List Basic Memory projects accessible to this agent",
        ),
        parameters: expect.objectContaining({
          type: "object",
          properties: expect.objectContaining({
            workspace: expect.objectContaining({ type: "string" }),
          }),
        }),
        execute: expect.any(Function),
      }),
      { name: "list_memory_projects" },
    )
  })

  it("formats and returns projects with required fields", async () => {
    registerProjectListTool(mockApi, mockClient)
    const registerCall = (mockApi.registerTool as jest.MockedFunction<any>).mock
      .calls[0]
    const execute = registerCall[0].execute

    ;(mockClient.listProjects as jest.MockedFunction<any>).mockResolvedValue([
      {
        name: "alpha",
        path: "/tmp/alpha",
        display_name: "Alpha Project",
        is_private: true,
        is_default: true,
      },
      {
        name: "beta",
        path: "/tmp/beta",
        is_private: false,
      },
    ])

    const result = await execute("call-1", {})

    expect(mockClient.listProjects).toHaveBeenCalledWith(undefined)
    expect(result.content[0].text).toContain("Found 2 project(s):")
    expect(result.content[0].text).toContain("**alpha** (default)")
    expect(result.content[0].text).toContain("Display Name: Alpha Project")
    expect(result.content[0].text).toContain("Private: false")
    expect(result.details).toEqual({
      count: 2,
      projects: [
        {
          name: "alpha",
          external_id: null,
          qualified_name: null,
          source: null,
          path: "/tmp/alpha",
          display_name: "Alpha Project",
          is_private: true,
          is_default: true,
          workspace_name: null,
          workspace_slug: null,
          workspace_type: null,
          workspace_tenant_id: null,
        },
        {
          name: "beta",
          external_id: null,
          qualified_name: null,
          source: null,
          path: "/tmp/beta",
          display_name: null,
          is_private: false,
          is_default: false,
          workspace_name: null,
          workspace_slug: null,
          workspace_type: null,
          workspace_tenant_id: null,
        },
      ],
    })
  })

  it("keeps source, external_id, and qualified_name for routing", async () => {
    registerProjectListTool(mockApi, mockClient)
    const execute = (mockApi.registerTool as jest.MockedFunction<any>).mock
      .calls[0][0].execute

    ;(mockClient.listProjects as jest.MockedFunction<any>).mockResolvedValue([
      {
        name: "getting-started",
        external_id: "6b1c3f0e-0f5e-4d6b-9a39-2a4f8c1d7e55",
        source: "cloud",
        qualified_name: "personal/getting-started",
        path: "/getting-started",
        is_default: true,
      },
      {
        name: "main",
        external_id: "d9b9ab97-c3f8-488e-9863-b9729ccc55db",
        source: "local",
        qualified_name: null,
        path: "/tmp/main",
      },
    ])

    const result = await execute("call-1", {})
    const text = result.content[0].text

    expect(text).toContain("ID: 6b1c3f0e-0f5e-4d6b-9a39-2a4f8c1d7e55")
    expect(text).toContain("Source: cloud")
    expect(text).toContain("Qualified Name: personal/getting-started")
    expect(text).toContain("ID: d9b9ab97-c3f8-488e-9863-b9729ccc55db")
    expect(text).toContain("Source: local")
    expect(result.details.projects[0]).toMatchObject({
      external_id: "6b1c3f0e-0f5e-4d6b-9a39-2a4f8c1d7e55",
      source: "cloud",
      qualified_name: "personal/getting-started",
    })
    expect(result.details.projects[1]).toMatchObject({
      external_id: "d9b9ab97-c3f8-488e-9863-b9729ccc55db",
      source: "local",
      qualified_name: null,
    })
  })

  it("handles empty project list", async () => {
    registerProjectListTool(mockApi, mockClient)
    const registerCall = (mockApi.registerTool as jest.MockedFunction<any>).mock
      .calls[0]
    const execute = registerCall[0].execute

    ;(mockClient.listProjects as jest.MockedFunction<any>).mockResolvedValue([])

    const result = await execute("call-1", {})

    expect(result).toEqual({
      content: [
        {
          type: "text",
          text: "No Basic Memory projects found.",
        },
      ],
      details: {
        count: 0,
        projects: [],
      },
    })
  })

  it("passes workspace filter to client.listProjects", async () => {
    registerProjectListTool(mockApi, mockClient)
    const registerCall = (mockApi.registerTool as jest.MockedFunction<any>).mock
      .calls[0]
    const execute = registerCall[0].execute

    ;(mockClient.listProjects as jest.MockedFunction<any>).mockResolvedValue([])

    await execute("call-1", { workspace: "my-org" })

    expect(mockClient.listProjects).toHaveBeenCalledWith("my-org")
  })

  it("handles listProjects errors gracefully", async () => {
    registerProjectListTool(mockApi, mockClient)
    const registerCall = (mockApi.registerTool as jest.MockedFunction<any>).mock
      .calls[0]
    const execute = registerCall[0].execute

    ;(mockClient.listProjects as jest.MockedFunction<any>).mockRejectedValue(
      new Error("boom"),
    )

    const result = await execute("call-1", {})

    expect(result).toEqual({
      content: [
        {
          type: "text",
          text: "Failed to list Basic Memory projects. Is Basic Memory running? Check logs for details.",
        },
      ],
      details: {
        count: 0,
        projects: [],
        workspace: null,
        error: "list_memory_projects_failed",
      },
    })
  })
})
