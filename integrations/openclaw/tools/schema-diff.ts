import { Type } from "@sinclair/typebox"
import type { OpenClawPluginApi } from "openclaw/plugin-sdk/plugin-entry"
import type { BmClient } from "../bm-client.ts"
import { log } from "../logger.ts"

export function registerSchemaDiffTool(
  api: OpenClawPluginApi,
  client: BmClient,
): void {
  api.registerTool(
    {
      name: "schema_diff",
      label: "Schema Diff",
      description:
        "Detect drift between a Picoschema definition and actual note usage. " +
        "Identifies new fields in notes not declared in the schema, " +
        "declared fields no longer used, and cardinality changes.",
      parameters: Type.Object({
        noteType: Type.String({
          description:
            'The note type to check for drift (e.g., "person", "meeting")',
        }),
        project: Type.Optional(
          Type.String({
            description: "Target project name (defaults to current project)",
          }),
        ),
      }),
      async execute(
        _toolCallId: string,
        params: { noteType: string; project?: string },
      ) {
        log.debug(`schema_diff: noteType="${params.noteType}"`)

        try {
          const result = await client.schemaDiff(
            params.noteType,
            params.project,
          )

          if (!result.schema_found) {
            return {
              content: [
                {
                  type: "text" as const,
                  text: `No schema found for type "${params.noteType}". Use schema_infer to generate one.`,
                },
              ],
              details: result,
            }
          }

          const lines: string[] = [`**Type:** ${result.entity_type}`]

          if (
            result.new_fields.length === 0 &&
            result.dropped_fields.length === 0 &&
            result.cardinality_changes.length === 0
          ) {
            lines.push("No drift detected — schema and notes are in sync.")
          } else {
            if (result.new_fields.length > 0) {
              lines.push("", "### New Fields (in notes, not in schema)")
              for (const f of result.new_fields) {
                lines.push(
                  `- **${f.name}** — ${(f.percentage * 100).toFixed(0)}% of notes`,
                )
              }
            }

            if (result.dropped_fields.length > 0) {
              lines.push("", "### Dropped Fields (in schema, not in notes)")
              for (const f of result.dropped_fields) {
                lines.push(`- **${f.name}** — declared in ${f.declared_in}`)
              }
            }

            if (result.cardinality_changes.length > 0) {
              lines.push("", "### Cardinality Changes")
              for (const c of result.cardinality_changes) {
                lines.push(`- ${c}`)
              }
            }
          }

          return {
            content: [{ type: "text" as const, text: lines.join("\n") }],
            details: result,
          }
        } catch (err) {
          log.error("schema_diff failed", err)
          return {
            content: [
              {
                type: "text" as const,
                text: "Schema diff failed. Check logs for details.",
              },
            ],
            details: {
              noteType: params.noteType,
              error: "schema_diff_failed",
            },
          }
        }
      },
    },
    { name: "schema_diff" },
  )
}
