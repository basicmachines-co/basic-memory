import { Type } from "@sinclair/typebox"
import type { OpenClawPluginApi } from "openclaw/plugin-sdk/plugin-entry"
import type { BmClient } from "../bm-client.ts"
import { NoteAlreadyExistsError } from "../bm-client.ts"
import { log } from "../logger.ts"

export function registerWriteTool(
  api: OpenClawPluginApi,
  client: BmClient,
): void {
  api.registerTool(
    {
      name: "write_note",
      label: "Write Note",
      description:
        "Create a note in the Basic Memory knowledge graph. " +
        "Call this whenever the user asks to remember, record, save, or note " +
        "something — saying 'saved' without this call saves nothing. The result " +
        "returns the note's permalink and file_path: cite those exact values " +
        "when confirming, never a recalled filename. " +
        "If the note already exists, returns an error by default. " +
        "Pass overwrite=true to replace, or use edit_note for incremental updates.",
      parameters: Type.Object({
        title: Type.String({ description: "Note title" }),
        content: Type.String({
          description: "Note content in Markdown format",
        }),
        folder: Type.String({ description: "Folder to write the note in" }),
        project: Type.Optional(
          Type.String({
            description: "Target project name (defaults to current project)",
          }),
        ),
        overwrite: Type.Optional(
          Type.Boolean({
            description:
              "Set to true to replace an existing note. Defaults to false.",
          }),
        ),
        metadata: Type.Optional(
          Type.Object(
            {},
            {
              additionalProperties: true,
              description:
                "Extra frontmatter fields to merge into the note. Nested objects are supported.",
            },
          ),
        ),
        tags: Type.Optional(
          Type.Union([Type.Array(Type.String()), Type.String()], {
            description:
              "Tags for the note, as an array or comma-separated string.",
          }),
        ),
        note_type: Type.Optional(
          Type.String({
            description: 'Note type stored in frontmatter. Defaults to "note".',
          }),
        ),
      }),
      async execute(
        _toolCallId: string,
        params: {
          title: string
          content: string
          folder: string
          project?: string
          overwrite?: boolean
          metadata?: Record<string, unknown>
          tags?: string[] | string
          note_type?: string
        },
      ) {
        log.debug(`write_note: title=${params.title} folder=${params.folder}`)

        const options =
          params.metadata !== undefined ||
          params.tags !== undefined ||
          params.note_type !== undefined
            ? {
                metadata: params.metadata,
                tags: params.tags,
                noteType: params.note_type,
              }
            : undefined

        try {
          const note = await client.writeNote(
            params.title,
            params.content,
            params.folder,
            params.project,
            params.overwrite,
            options,
          )

          const msg = `Note saved: ${note.title} (${note.permalink})`
          return {
            content: [
              {
                type: "text" as const,
                text: msg,
              },
            ],
            details: {
              title: note.title,
              permalink: note.permalink,
              file_path: note.file_path,
            },
          }
        } catch (err) {
          if (err instanceof NoteAlreadyExistsError) {
            const hint = [
              `Note already exists: "${params.title}" (${err.permalink})`,
              "",
              "To update this note, use one of:",
              `  - edit_note("${err.permalink}", operation="append", content="...")`,
              `  - edit_note("${err.permalink}", operation="replace_section", section="...", content="...")`,
              `  - write_note("${params.title}", ..., overwrite=true) to fully replace`,
              `  - read_note("${err.permalink}") to inspect current content first`,
            ].join("\n")

            return {
              content: [{ type: "text" as const, text: hint }],
              details: {
                title: params.title,
                permalink: err.permalink,
                error: "note_already_exists",
              },
            }
          }

          log.error("write_note failed", err)
          return {
            content: [
              {
                type: "text" as const,
                text: "Failed to write note. Is Basic Memory running? Check logs for details.",
              },
            ],
            details: {
              title: params.title,
              folder: params.folder,
              error: "write_note_failed",
            },
          }
        }
      },
    },
    { name: "write_note" },
  )
}
