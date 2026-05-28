import { Type } from "@sinclair/typebox"
import type { OpenClawPluginApi } from "openclaw/plugin-sdk/plugin-entry"
import type { BmClient } from "../bm-client.ts"
import { log } from "../logger.ts"

export function registerEditTool(
  api: OpenClawPluginApi,
  client: BmClient,
): void {
  api.registerTool(
    {
      name: "edit_note",
      label: "Edit Note",
      description:
        "Incrementally edit an existing note in the Basic Memory knowledge graph. " +
        "Supports append, prepend, find/replace, and section replacement " +
        "without rewriting the entire note.",
      parameters: Type.Object({
        identifier: Type.String({
          description: "Note title, permalink, or memory:// URL to edit",
        }),
        operation: Type.Union(
          [
            Type.Literal("append"),
            Type.Literal("prepend"),
            Type.Literal("find_replace"),
            Type.Literal("replace_section"),
          ],
          {
            description:
              "Edit operation: append (add to end), prepend (add to start), " +
              "find_replace (replace matching text), replace_section (replace a heading section)",
          },
        ),
        content: Type.String({
          description: "New content to add or replace with",
        }),
        find_text: Type.Optional(
          Type.String({
            description: "Text to find (required for find_replace)",
          }),
        ),
        section: Type.Optional(
          Type.String({
            description:
              "Section heading to replace (required for replace_section)",
          }),
        ),
        expected_replacements: Type.Optional(
          Type.Number({
            description:
              "Expected replacement count for find_replace (default: 1)",
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
          operation: "append" | "prepend" | "find_replace" | "replace_section"
          content: string
          find_text?: string
          section?: string
          expected_replacements?: number
          project?: string
        },
      ) {
        log.debug(`edit_note: id="${params.identifier}" op=${params.operation}`)

        try {
          const note = await client.editNote(
            params.identifier,
            params.operation,
            params.content,
            {
              find_text: params.find_text,
              section: params.section,
              expected_replacements: params.expected_replacements,
            },
            params.project,
          )

          return {
            content: [
              {
                type: "text" as const,
                text: `Note updated: ${note.title} (${note.permalink})`,
              },
            ],
            details: {
              title: note.title,
              permalink: note.permalink,
              file_path: note.file_path,
              operation: params.operation,
              checksum: note.checksum ?? null,
            },
          }
        } catch (err) {
          log.error("edit_note failed", err)
          return {
            content: [
              {
                type: "text" as const,
                text: `Failed to edit note "${params.identifier}". It may not exist.`,
              },
            ],
            details: {
              identifier: params.identifier,
              operation: params.operation,
              error: "edit_note_failed",
            },
          }
        }
      },
    },
    { name: "edit_note" },
  )
}
