import { Type } from "@sinclair/typebox"
import type { OpenClawPluginApi } from "openclaw/plugin-sdk/plugin-entry"
import type { BmClient } from "../bm-client.ts"
import { log } from "../logger.ts"

export function registerContextTool(
  api: OpenClawPluginApi,
  client: BmClient,
): void {
  api.registerTool(
    {
      name: "build_context",
      label: "Build Context",
      description:
        "Navigate the Basic Memory knowledge graph via memory:// URLs. " +
        "Returns the target note plus related notes connected through the graph. " +
        "Use this to follow relations and discover connected concepts.",
      parameters: Type.Object({
        url: Type.String({
          description:
            'Memory URL to navigate, e.g. "memory://agents/decisions" or "projects/my-project"',
        }),
        depth: Type.Optional(
          Type.Number({
            description: "How many relation hops to follow (default: 1)",
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
        params: { url: string; depth?: number; project?: string },
      ) {
        const depth = params.depth ?? 1
        log.debug(`build_context: url="${params.url}" depth=${depth}`)

        try {
          const ctx = await client.buildContext(
            params.url,
            depth,
            params.project,
          )

          if (!ctx.results || ctx.results.length === 0) {
            return {
              content: [
                {
                  type: "text" as const,
                  text: `No context found for "${params.url}".`,
                },
              ],
              details: {
                url: params.url,
                depth,
                resultCount: 0,
              },
            }
          }

          const sections: string[] = []

          for (const result of ctx.results) {
            const primary = result.primary_result
            sections.push(`## ${primary.title}\n${primary.content}`)

            if (result.observations?.length > 0) {
              const obs = result.observations
                .map((o) => `- [${o.category}] ${o.content}`)
                .join("\n")
              sections.push(`### Observations\n${obs}`)
            }

            if (result.related_results?.length > 0) {
              const rels = result.related_results
                .map((r) => {
                  if (r.type === "relation") {
                    return `- ${r.relation_type} → **${r.to_entity}**`
                  }
                  const label = r.relation_type
                    ? `${r.relation_type} → **${r.title}**`
                    : `**${r.title}**`
                  return r.permalink
                    ? `- ${label} (${r.permalink})`
                    : `- ${label}`
                })
                .join("\n")
              sections.push(`### Related\n${rels}`)
            }
          }

          return {
            content: [
              {
                type: "text" as const,
                text: sections.join("\n\n"),
              },
            ],
            details: {
              url: params.url,
              depth,
              resultCount: ctx.results.length,
            },
          }
        } catch (err) {
          log.error("build_context failed", err)
          return {
            content: [
              {
                type: "text" as const,
                text: `Failed to build context for "${params.url}". Check logs for details.`,
              },
            ],
            details: {
              url: params.url,
              depth,
              error: "build_context_failed",
            },
          }
        }
      },
    },
    { name: "build_context" },
  )
}
