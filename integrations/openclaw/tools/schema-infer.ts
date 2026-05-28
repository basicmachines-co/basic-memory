import { Type } from "@sinclair/typebox"
import type { OpenClawPluginApi } from "openclaw/plugin-sdk/plugin-entry"
import type { BmClient } from "../bm-client.ts"
import { log } from "../logger.ts"

export function registerSchemaInferTool(
  api: OpenClawPluginApi,
  client: BmClient,
): void {
  api.registerTool(
    {
      name: "schema_infer",
      label: "Schema Infer",
      description:
        "Analyze existing notes of a given type and suggest a Picoschema definition. " +
        "Examines observation categories and relation types to infer required and optional fields.",
      parameters: Type.Object({
        noteType: Type.String({
          description:
            'The note type to analyze (e.g., "person", "meeting", "Task")',
        }),
        threshold: Type.Optional(
          Type.Number({
            description:
              "Minimum frequency (0-1) for a field to be suggested as optional. Default 0.25.",
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
        params: { noteType: string; threshold?: number; project?: string },
      ) {
        log.debug(
          `schema_infer: noteType="${params.noteType}" threshold=${params.threshold ?? 0.25}`,
        )

        try {
          const result = await client.schemaInfer(
            params.noteType,
            params.threshold,
            params.project,
          )

          const lines: string[] = [
            `**Type:** ${result.entity_type}`,
            `**Notes analyzed:** ${result.notes_analyzed}`,
          ]

          if (result.suggested_required.length > 0) {
            lines.push(
              `**Required fields:** ${result.suggested_required.join(", ")}`,
            )
          }
          if (result.suggested_optional.length > 0) {
            lines.push(
              `**Optional fields:** ${result.suggested_optional.join(", ")}`,
            )
          }

          if (result.field_frequencies.length > 0) {
            lines.push("", "### Field Frequencies")
            for (const f of result.field_frequencies) {
              lines.push(
                `- **${f.name}** — ${(f.percentage * 100).toFixed(0)}% (${f.count} notes)`,
              )
            }
          }

          if (Object.keys(result.suggested_schema).length > 0) {
            lines.push(
              "",
              "### Suggested Schema",
              "```json",
              JSON.stringify(result.suggested_schema, null, 2),
              "```",
            )
          }

          return {
            content: [{ type: "text" as const, text: lines.join("\n") }],
            details: result,
          }
        } catch (err) {
          log.error("schema_infer failed", err)
          return {
            content: [
              {
                type: "text" as const,
                text: "Schema inference failed. Check logs for details.",
              },
            ],
            details: {
              noteType: params.noteType,
              threshold: params.threshold ?? 0.25,
              error: "schema_infer_failed",
            },
          }
        }
      },
    },
    { name: "schema_infer" },
  )
}
