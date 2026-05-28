import { beforeEach, describe, expect, it, jest } from "bun:test"
import type { OpenClawPluginApi } from "openclaw/plugin-sdk/plugin-entry"
import type { BmClient } from "../bm-client.ts"
import { registerWorkspaceListTool } from "./list-workspaces.ts"

describe("workspace list tool", () => {
  let mockApi: OpenClawPluginApi
  let mockClient: BmClient

  beforeEach(() => {
    mockApi = {
      registerTool: jest.fn(),
    } as any

    mockClient = {
      listWorkspaces: jest.fn(),
    } as any
  })

  it("registers list_workspaces with expected shape", () => {
    registerWorkspaceListTool(mockApi, mockClient)

    expect(mockApi.registerTool).toHaveBeenCalledWith(
      expect.objectContaining({
        name: "list_workspaces",
        label: "List Workspaces",
        description:
          "List all Basic Memory workspaces (personal and organization) accessible to this user",
        parameters: expect.objectContaining({
          type: "object",
          properties: {},
        }),
        execute: expect.any(Function),
      }),
      { name: "list_workspaces" },
    )
  })

  describe("execution", () => {
    let execute: Function

    beforeEach(() => {
      registerWorkspaceListTool(mockApi, mockClient)
      const call = (mockApi.registerTool as jest.MockedFunction<any>).mock
        .calls[0]
      execute = call[0].execute
    })

    it("formats and returns workspaces", async () => {
      ;(
        mockClient.listWorkspaces as jest.MockedFunction<any>
      ).mockResolvedValue([
        {
          tenant_id: "t-1",
          name: "Personal",
          workspace_type: "personal",
          role: "owner",
          organization_id: null,
          has_active_subscription: true,
        },
        {
          tenant_id: "t-2",
          name: "Acme Corp",
          workspace_type: "organization",
          role: "member",
          organization_id: "org-1",
          has_active_subscription: false,
        },
      ])

      const result = await execute("call-1", {})

      expect(mockClient.listWorkspaces).toHaveBeenCalledWith()
      expect(result.content[0].text).toContain("Found 2 workspace(s):")
      expect(result.content[0].text).toContain("**Personal**")
      expect(result.content[0].text).toContain("Type: personal")
      expect(result.content[0].text).toContain("Subscription: active")
      expect(result.content[0].text).toContain("**Acme Corp**")
      expect(result.content[0].text).toContain("Subscription: none")
      expect(result.details).toEqual({
        count: 2,
        workspaces: [
          {
            tenant_id: "t-1",
            name: "Personal",
            workspace_type: "personal",
            role: "owner",
            organization_id: null,
            has_active_subscription: true,
          },
          {
            tenant_id: "t-2",
            name: "Acme Corp",
            workspace_type: "organization",
            role: "member",
            organization_id: "org-1",
            has_active_subscription: false,
          },
        ],
      })
    })

    it("handles empty workspace list", async () => {
      ;(
        mockClient.listWorkspaces as jest.MockedFunction<any>
      ).mockResolvedValue([])

      const result = await execute("call-1", {})

      expect(result).toEqual({
        content: [
          {
            type: "text",
            text: "No workspaces found.",
          },
        ],
        details: {
          count: 0,
          workspaces: [],
        },
      })
    })

    it("handles errors gracefully", async () => {
      ;(
        mockClient.listWorkspaces as jest.MockedFunction<any>
      ).mockRejectedValue(new Error("boom"))

      const result = await execute("call-1", {})

      expect(result).toEqual({
        content: [
          {
            type: "text",
            text: "Failed to list workspaces. Is Basic Memory running? Check logs for details.",
          },
        ],
        details: {
          count: 0,
          workspaces: [],
          error: "list_workspaces_failed",
        },
      })
    })
  })
})
