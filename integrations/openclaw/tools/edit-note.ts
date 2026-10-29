import { Type } from "@sinclair/typebox"
import type { OpenClawPluginApi } from "openclaw/plugin-sdk/plugin-entry"
import {
  type BmClient,
  describeToolError,
  type EditOperation,
  isNoteNotFoundError,
} from "../bm-client.ts"
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
        "Supports append, prepend, find/replace, replacing a section, and inserting " +
        "next to a section heading, without rewriting the entire note. To add to a " +
        "specific section (for example a new observation), use insert_after_section " +
        "with that heading; append always adds to the very end of the note. " +
        "The result returns the note's " +
        "permalink and file_path: cite those exact values when confirming " +
        "the edit — never report a save that no tool call performed.",
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
            Type.Literal("insert_before_section"),
            Type.Literal("insert_after_section"),
          ],
          {
            description:
              "Edit operation: append (add to end of note), prepend (add to start), " +
              "find_replace (replace matching text), replace_section (replace a heading section), " +
              "insert_after_section (add directly below a heading, at the top of its section), " +
              "insert_before_section (add directly above a heading, at the end of the section before it)",
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
              "Section heading, e.g. 'Observations' (required for replace_section, " +
              "insert_before_section and insert_after_section; not allowed with other operations)",
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
        metadata: Type.Optional(
          Type.Object(
            {},
            {
              additionalProperties: true,
              description:
                "Frontmatter fields to merge independently of the edit operation. Nested objects are supported.",
            },
          ),
        ),
      }),
      async execute(
        _toolCallId: string,
        params: {
          identifier: string
          operation: EditOperation
          content: string
          find_text?: string
          section?: string
          expected_replacements?: number
          project?: string
          metadata?: Record<string, unknown>
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
              metadata: params.metadata,
            },
            params.project,
          )

          return {
            content: [
              {
                type: "text" as const,
                text: `Note updated: ${note.title} (${note.permalink})\nFile: ${note.file_path}`,
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
          // Trigger: any edit failure, including invalid arguments and
          //   checksum conflicts, not only a missing note.
          // Why: a blanket "may not exist" sent agents hunting for the wrong
          //   problem; the server's message says what to change.
          // Outcome: the agent sees the server's reason verbatim.
          const reason = describeToolError(err)
          const text = isNoteNotFoundError(err)
            ? `Failed to edit note "${params.identifier}": it does not exist. ${reason}`
            : `Failed to edit note "${params.identifier}": ${reason}`
          return {
            content: [{ type: "text" as const, text }],
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
