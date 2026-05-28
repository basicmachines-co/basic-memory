import { Type } from "@sinclair/typebox"
import type { OpenClawPluginApi } from "openclaw/plugin-sdk/plugin-entry"
import type { BmClient } from "../bm-client.ts"
import { log } from "../logger.ts"

export function registerMoveTool(
  api: OpenClawPluginApi,
  client: BmClient,
): void {
  api.registerTool(
    {
      name: "move_note",
      label: "Move Note",
      description:
        "Move a note to a different folder in the Basic Memory knowledge graph. " +
        "The note content is preserved; only the location changes.",
      parameters: Type.Object({
        identifier: Type.String({
          description: "Note title, permalink, or memory:// URL to move",
        }),
        newFolder: Type.String({
          description:
            "Destination folder (e.g., 'archive', 'projects/completed')",
        }),
        project: Type.Optional(
          Type.String({
            description: "Target project name (defaults to current project)",
          }),
        ),
      }),
      async execute(
        _toolCallId: string,
        params: { identifier: string; newFolder: string; project?: string },
      ) {
        log.debug(
          `move_note: identifier="${params.identifier}" → folder="${params.newFolder}"`,
        )

        try {
          const result = await client.moveNote(
            params.identifier,
            params.newFolder,
            params.project,
          )

          return {
            content: [
              {
                type: "text" as const,
                text: `Moved: ${result.title} → ${result.file_path}`,
              },
            ],
            details: {
              title: result.title,
              permalink: result.permalink,
              file_path: result.file_path,
            },
          }
        } catch (err) {
          log.error("move_note failed", err)
          return {
            content: [
              {
                type: "text" as const,
                text: `Failed to move "${params.identifier}". It may not exist.`,
              },
            ],
            details: {
              identifier: params.identifier,
              newFolder: params.newFolder,
              error: "move_note_failed",
            },
          }
        }
      },
    },
    { name: "move_note" },
  )
}
