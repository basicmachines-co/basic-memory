import { Type } from "@sinclair/typebox"
import type { OpenClawPluginApi } from "openclaw/plugin-sdk/plugin-entry"
import type { BmClient } from "../bm-client.ts"
import { log } from "../logger.ts"

export function registerDeleteTool(
  api: OpenClawPluginApi,
  client: BmClient,
): void {
  api.registerTool(
    {
      name: "delete_note",
      label: "Delete Note",
      description:
        "Delete a note from the Basic Memory knowledge graph. " +
        "The note is permanently removed from the filesystem and the search index.",
      parameters: Type.Object({
        identifier: Type.String({
          description: "Note title, permalink, or memory:// URL to delete",
        }),
        project: Type.Optional(
          Type.String({
            description: "Target project name (defaults to current project)",
          }),
        ),
      }),
      async execute(
        _toolCallId: string,
        params: { identifier: string; project?: string },
      ) {
        log.debug(`delete_note: identifier="${params.identifier}"`)

        try {
          const result = await client.deleteNote(
            params.identifier,
            params.project,
          )

          return {
            content: [
              {
                type: "text" as const,
                text: `Deleted: ${result.title} (${result.permalink})`,
              },
            ],
            details: {
              title: result.title,
              permalink: result.permalink,
              file_path: result.file_path,
            },
          }
        } catch (err) {
          log.error("delete_note failed", err)
          return {
            content: [
              {
                type: "text" as const,
                text: `Failed to delete "${params.identifier}". It may not exist.`,
              },
            ],
            details: {
              identifier: params.identifier,
              error: "delete_note_failed",
            },
          }
        }
      },
    },
    { name: "delete_note" },
  )
}
