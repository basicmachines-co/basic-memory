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
        "Returns the full note content including observations and relations, " +
        "followed by the note's checksum: pass it to write_note as " +
        "expected_checksum (with overwrite=true) to replace only this revision.",
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
        project_id: Type.Optional(
          Type.String({
            description:
              "Target project external_id from list_memory_projects. Routes to " +
              "that exact project and takes precedence over `project`.",
          }),
        ),
      }),
      async execute(
        _toolCallId: string,
        params: {
          identifier: string
          include_frontmatter?: boolean
          project?: string
          project_id?: string
        },
      ) {
        log.debug(`read_note: identifier="${params.identifier}"`)

        try {
          const note = await client.readNote(
            params.identifier,
            {
              includeFrontmatter: params.include_frontmatter === true,
              projectId: params.project_id,
            },
            params.project,
          )
          // The checksum identifies the revision just read; the model sees only
          // `content`, so it has to travel in the text to reach a guarded write.
          const text = note.checksum
            ? `${note.content}\n\nChecksum: ${note.checksum}`
            : note.content

          return {
            content: [
              {
                type: "text" as const,
                text,
              },
            ],
            details: {
              title: note.title,
              permalink: note.permalink,
              file_path: note.file_path,
              frontmatter: note.frontmatter ?? null,
              checksum: note.checksum ?? null,
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
