import { Type } from "@sinclair/typebox"
import type { OpenClawPluginApi } from "openclaw/plugin-sdk/plugin-entry"
import type { BmClient } from "../bm-client.ts"
import { log } from "../logger.ts"

export function registerSearchTool(
  api: OpenClawPluginApi,
  client: BmClient,
): void {
  api.registerTool(
    {
      name: "search_notes",
      label: "Knowledge Search",
      description:
        "Search the Basic Memory knowledge graph for relevant notes, concepts, and connections. " +
        "Returns matching notes with titles, content previews, and relevance scores. " +
        "Optionally filter by frontmatter metadata fields, tags, or status.",
      parameters: Type.Object({
        query: Type.String({ description: "Search query" }),
        limit: Type.Optional(
          Type.Number({ description: "Max results (default: 10)" }),
        ),
        project: Type.Optional(
          Type.String({
            description: "Target project name (defaults to current project)",
          }),
        ),
        metadata_filters: Type.Optional(
          Type.Object(
            {},
            {
              additionalProperties: true,
              description:
                "Filter by frontmatter fields. Supports equality, $in, $gt/$gte/$lt/$lte, $between, and array-contains operators.",
            },
          ),
        ),
        tags: Type.Optional(
          Type.Array(Type.String(), {
            description: "Filter by frontmatter tags (all must match)",
          }),
        ),
        status: Type.Optional(
          Type.String({
            description: "Filter by frontmatter status field",
          }),
        ),
      }),
      async execute(
        _toolCallId: string,
        params: {
          query: string
          limit?: number
          project?: string
          metadata_filters?: Record<string, unknown>
          tags?: string[]
          status?: string
        },
      ) {
        const limit = params.limit ?? 10
        log.debug(
          `search_notes: query="${params.query}" limit=${limit} project="${params.project ?? "default"}"`,
        )

        const metadata =
          params.metadata_filters || params.tags || params.status
            ? {
                filters: params.metadata_filters,
                tags: params.tags,
                status: params.status,
              }
            : undefined

        try {
          const results = await client.search(
            params.query,
            limit,
            params.project,
            metadata,
          )

          if (results.length === 0) {
            return {
              content: [
                {
                  type: "text" as const,
                  text: "No matching notes found in the knowledge graph.",
                },
              ],
              details: {
                count: 0,
                results: [],
              },
            }
          }

          const text = results
            .map((r, i) => {
              const score = r.score ? ` (${(r.score * 100).toFixed(0)}%)` : ""
              const content = r.content ?? ""
              const preview =
                content.length > 200 ? `${content.slice(0, 200)}...` : content
              return `${i + 1}. **${r.title}**${score}\n   ${preview}`
            })
            .join("\n\n")

          return {
            content: [
              {
                type: "text" as const,
                text: `Found ${results.length} notes:\n\n${text}`,
              },
            ],
            details: {
              count: results.length,
              results: results.map((r) => ({
                title: r.title,
                permalink: r.permalink,
                score: r.score,
              })),
            },
          }
        } catch (err) {
          log.error("search_notes failed", err)
          return {
            content: [
              {
                type: "text" as const,
                text: "Search failed. Is Basic Memory running? Check logs for details.",
              },
            ],
            details: {
              count: 0,
              results: [],
              query: params.query,
              error: "search_notes_failed",
            },
          }
        }
      },
    },
    { name: "search_notes" },
  )
}
