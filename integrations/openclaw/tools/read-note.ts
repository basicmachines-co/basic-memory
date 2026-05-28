import { Type } from "@sinclair/typebox"
import type { OpenClawPluginApi } from "openclaw/plugin-sdk/plugin-entry"
import type { BmClient } from "../bm-client.ts"
import { log } from "../logger.ts"

export function registerReadTool(
  api: OpenClawPluginApi,
  client: BmClient,
): void {
  api.registerTool(
    {
      name: "read_note",
      label: "Read Note",
      description:
        "Read a specific note from the Basic Memory knowledge graph by title or permalink. " +
        "Returns the full note content including observations and relations.",
      parameters: Type.Object({
        identifier: Type.String({
          description: "Note title, permalink, or memory:// URL to read",
        }),
        include_frontmatter: Type.Optional(
          Type.Boolean({
            description:
              "If true, returns raw note content including YAML frontmatter.",
          }),
        ),
        project: Type.Optional(
          Type.String({
            description: "Target project name (defaults to current project)",
          }),
        ),
      }),
      async execute(
        _toolCallId: string,
        params: {
          identifier: string
          include_frontmatter?: boolean
          project?: string
        },
      ) {
        log.debug(`read_note: identifier="${params.identifier}"`)

        try {
          const note = await client.readNote(
            params.identifier,
            { includeFrontmatter: params.include_frontmatter === true },
            params.project,
          )

          return {
            content: [
              {
                type: "text" as const,
                text: note.content,
              },
            ],
            details: {
              title: note.title,
              permalink: note.permalink,
              file_path: note.file_path,
              frontmatter: note.frontmatter ?? null,
            },
          }
        } catch (err) {
          log.error("read_note failed", err)
          return {
            content: [
              {
                type: "text" as const,
                text: `Could not read note "${params.identifier}". It may not exist yet.`,
              },
            ],
            details: {
              identifier: params.identifier,
              include_frontmatter: params.include_frontmatter === true,
              error: "read_note_failed",
            },
          }
        }
      },
    },
    { name: "read_note" },
  )
}
