import { Type } from "@sinclair/typebox"
import type { OpenClawPluginApi } from "openclaw/plugin-sdk/plugin-entry"
import type { BmClient, WorkspaceResult } from "../bm-client.ts"
import { log } from "../logger.ts"

function formatWorkspace(ws: WorkspaceResult, idx: number): string {
  const subscription = ws.has_active_subscription ? "active" : "none"
  return (
    `${idx + 1}. **${ws.name}**\n` +
    `   Type: ${ws.workspace_type} | Role: ${ws.role} | Subscription: ${subscription}`
  )
}

export function registerWorkspaceListTool(
  api: OpenClawPluginApi,
  client: BmClient,
): void {
  api.registerTool(
    {
      name: "list_workspaces",
      label: "List Workspaces",
      description:
        "List all Basic Memory workspaces (personal and organization) accessible to this user",
      parameters: Type.Object({}),
      async execute(_toolCallId: string, _params: Record<string, never>) {
        log.debug("list_workspaces")

        try {
          const workspaces = await client.listWorkspaces()

          if (workspaces.length === 0) {
            return {
              content: [
                {
                  type: "text" as const,
                  text: "No workspaces found.",
                },
              ],
              details: {
                count: 0,
                workspaces: [],
              },
            }
          }

          const text = workspaces.map(formatWorkspace).join("\n\n")

          return {
            content: [
              {
                type: "text" as const,
                text: `Found ${workspaces.length} workspace(s):\n\n${text}`,
              },
            ],
            details: {
              count: workspaces.length,
              workspaces,
            },
          }
        } catch (err) {
          log.error("list_workspaces failed", err)
          return {
            content: [
              {
                type: "text" as const,
                text: "Failed to list workspaces. Is Basic Memory running? Check logs for details.",
              },
            ],
            details: {
              count: 0,
              workspaces: [],
              error: "list_workspaces_failed",
            },
          }
        }
      },
    },
    { name: "list_workspaces" },
  )
}
