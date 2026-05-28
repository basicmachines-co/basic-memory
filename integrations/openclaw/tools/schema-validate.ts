import { Type } from "@sinclair/typebox"
import type { OpenClawPluginApi } from "openclaw/plugin-sdk/plugin-entry"
import type { BmClient } from "../bm-client.ts"
import { log } from "../logger.ts"

export function registerSchemaValidateTool(
  api: OpenClawPluginApi,
  client: BmClient,
): void {
  api.registerTool(
    {
      name: "schema_validate",
      label: "Schema Validate",
      description:
        "Validate notes against their Picoschema definitions. " +
        "Validates a specific note by identifier, or all notes of a given type.",
      parameters: Type.Object({
        noteType: Type.Optional(
          Type.String({
            description:
              'Note type to batch-validate (e.g., "person", "meeting")',
          }),
        ),
        identifier: Type.Optional(
          Type.String({
            description:
              "Specific note to validate (permalink, title, or path)",
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
        params: { noteType?: string; identifier?: string; project?: string },
      ) {
        log.debug(
          `schema_validate: noteType="${params.noteType ?? ""}" identifier="${params.identifier ?? ""}"`,
        )

        try {
          const result = await client.schemaValidate(
            params.noteType,
            params.identifier,
            params.project,
          )

          // Handle error responses from BM (e.g., no schema found)
          const resultRecord = result as unknown as Record<string, unknown>
          if ("error" in result && typeof resultRecord.error === "string") {
            return {
              content: [{ type: "text" as const, text: resultRecord.error }],
              details: result,
            }
          }

          const lines: string[] = []
          if (result.entity_type) {
            lines.push(`**Type:** ${result.entity_type}`)
          }
          lines.push(
            `**Notes:** ${result.total_notes ?? 0} | **Valid:** ${result.valid_count ?? 0} | **Warnings:** ${result.warning_count ?? 0} | **Errors:** ${result.error_count ?? 0}`,
          )

          if (result.results && result.results.length > 0) {
            lines.push("")
            for (const r of result.results) {
              const status = r.valid ? "valid" : "invalid"
              lines.push(`- **${r.identifier}** — ${status}`)
              for (const w of r.warnings) {
                lines.push(`  - warning: ${w}`)
              }
              for (const e of r.errors) {
                lines.push(`  - error: ${e}`)
              }
            }
          }

          return {
            content: [{ type: "text" as const, text: lines.join("\n") }],
            details: result,
          }
        } catch (err) {
          log.error("schema_validate failed", err)
          return {
            content: [
              {
                type: "text" as const,
                text: "Schema validation failed. Check logs for details.",
              },
            ],
            details: {
              noteType: params.noteType ?? null,
              identifier: params.identifier ?? null,
              error: "schema_validate_failed",
            },
          }
        }
      },
    },
    { name: "schema_validate" },
  )
}
