import type { MemoryPluginCapability } from "openclaw/plugin-sdk/memory-host-core"
import {
  type BmClient,
  isNoteNotFoundError,
  searchResultText,
} from "../bm-client.ts"
import { log } from "../logger.ts"

// OpenClaw documents deriving the provider types from the public capability
// type rather than importing its private memory-host-search facade.
type MemoryProviderRuntime = NonNullable<
  MemoryPluginCapability["providerRuntime"]
>
type MemoryProviderHandle = NonNullable<
  Awaited<ReturnType<MemoryProviderRuntime["open"]>>["provider"]
>
type MemorySearchRequest = Parameters<MemoryProviderHandle["search"]>[0]
type MemorySearchPage = Awaited<ReturnType<MemoryProviderHandle["search"]>>
type MemoryGetRequest = Parameters<MemoryProviderHandle["get"]>[0]
type MemoryGetResult = Awaited<ReturnType<MemoryProviderHandle["get"]>>
type MemoryHealth = Awaited<ReturnType<MemoryProviderHandle["health"]>>
type MemoryCallerContext = Parameters<
  MemoryProviderRuntime["open"]
>[0]["context"]
type MemoryProviderFlushPlanResolver = NonNullable<
  MemoryPluginCapability["providerFlushPlanResolver"]
>

export const MEMORY_PROVIDER_ID = "openclaw-basic-memory"

// Agent tools this plugin registers that Active Memory may use for deep
// recall, and that the pre-compaction flush may use to look before writing.
export const RECALL_TOOL_NAMES = [
  "search_notes",
  "read_note",
  "build_context",
] as const
const FLUSH_PERSISTENCE_TOOL_NAMES = ["write_note", "edit_note"] as const
const FLUSH_LOOKUP_TOOL_NAMES = ["search_notes", "read_note"] as const

const DEFAULT_MAX_RESULTS = 10
const MAX_EXCERPT_CHARS = 700
// memory-core's flush prompts use the same token for "nothing to save".
const SILENT_REPLY_TOKEN = "NO_REPLY"

// --- Search and retrieval ---

/**
 * Basic Memory fuses keyword and vector scores into [0, 1.3] (see
 * search_reader.py FUSION_BONUS). OpenClaw compares provider scores against a
 * caller's minScore on a 0–1 scale, so a fused score above 1 is capped at 1.
 */
function hostScore(score: number | undefined): number | undefined {
  return score === undefined ? undefined : Math.min(score, 1)
}

function excerptOf(content: string): string {
  return content.length > MAX_EXCERPT_CHARS
    ? `${content.slice(0, MAX_EXCERPT_CHARS)}…`
    : content
}

/**
 * Apply the host's optional line window to a note body. `from` is 1-based,
 * matching the host's memory_get contract.
 */
function sliceLines(
  text: string,
  from: number | undefined,
  lines: number | undefined,
): Pick<
  Extract<MemoryGetResult, { status: "ok" }>,
  "text" | "truncated" | "from" | "lines" | "nextFrom"
> {
  if (from === undefined && lines === undefined) return { text }

  const all = text.split("\n")
  const start = Math.max(1, from ?? 1)
  const count = lines ?? all.length - start + 1
  const window = all.slice(start - 1, start - 1 + count)
  const end = start - 1 + window.length
  const truncated = end < all.length
  return {
    text: window.join("\n"),
    from: start,
    lines: window.length,
    truncated,
    ...(truncated ? { nextFrom: end + 1 } : {}),
  }
}

/**
 * Open a caller-bound reader over the plugin's shared BM MCP session.
 *
 * The host rechecks the caller's authority around every provider call;
 * context.assertCurrent() is also called before and after each BM request so
 * a revoked caller is refused rather than served. Every note in the project
 * is visible to every caller, which matches what the plugin's own
 * search_notes and read_note tools already expose.
 */
export function openBasicMemoryReader(
  client: BmClient,
  context: MemoryCallerContext,
): MemoryProviderHandle {
  const guarded = async <T>(run: () => Promise<T>): Promise<T> => {
    context.assertCurrent()
    const result = await run()
    context.assertCurrent()
    return result
  }

  // Trigger: the reader opens while the plugin service is not running, as in
  //   one-shot CLI commands like `openclaw memory status`.
  // Why: the first BM call then spawns a `bm mcp` child that nothing else
  //   will stop, and its open pipes keep the CLI process from exiting.
  // Outcome: this reader owns that connection and stops it on close().
  const ownsConnection = !client.isServiceRunning()

  return {
    capabilities: {
      sources: ["memory"],
      pagination: false,
      candidates: [],
      projectFilter: false,
    },

    async search(request: MemorySearchRequest): Promise<MemorySearchPage> {
      const results = await guarded(() =>
        client.search(request.query, request.maxResults ?? DEFAULT_MAX_RESULTS),
      )

      const hits = results
        .map((result) => ({ result, score: hostScore(result.score) }))
        // Trigger: the caller set a score floor.
        // Why: the host contract requires every provider to enforce minScore.
        // Outcome: unscored or lower-scored hits are dropped.
        .filter(
          ({ score }) =>
            request.minScore === undefined ||
            (score !== undefined && score >= request.minScore),
        )
        .map(({ result, score }) => ({
          reference: { providerId: MEMORY_PROVIDER_ID, id: result.permalink },
          excerpt: excerptOf(searchResultText(result)),
          source: result.file_path,
          ...(score === undefined ? {} : { score }),
        }))

      return { hits }
    },

    async get(request: MemoryGetRequest): Promise<MemoryGetResult> {
      try {
        const note = await guarded(() => client.readNote(request.reference.id))
        return {
          status: "ok",
          reference: { providerId: MEMORY_PROVIDER_ID, id: note.permalink },
          ...sliceLines(note.content, request.from, request.lines),
        }
      } catch (err) {
        // A missing note is an answer; any other failure must surface so the
        // host does not report it as an empty success.
        if (isNoteNotFoundError(err)) return { status: "not_found" }
        throw err
      }
    },

    async health(): Promise<MemoryHealth> {
      // Listing projects proves the BM MCP session answers without reading
      // any note content, so it is safe for status-only callers.
      try {
        await guarded(() => client.listProjects())
        return { status: "ready" }
      } catch (err) {
        log.warn(
          `memory health check failed: ${err instanceof Error ? err.message : String(err)}`,
        )
        return {
          status: "unavailable",
          message: "Basic Memory is not reachable",
        }
      }
    },

    // In the gateway the BM MCP session is shared by the whole plugin and
    // owned by its service, so closing one caller's reader releases nothing.
    async close(): Promise<void> {
      if (ownsConnection) await client.stop()
    },
  }
}

// --- Pre-compaction flush ---

const FLUSH_PROMPT = [
  "Pre-compaction memory flush.",
  "Save durable facts, decisions and open tasks from this session to Basic Memory.",
  "Use search_notes or read_note first to find an existing note, then edit_note to update it;",
  "use write_note only for a new topic.",
  `If nothing is worth saving, reply with ${SILENT_REPLY_TOKEN}.`,
].join(" ")

const FLUSH_SYSTEM_PROMPT = [
  "Pre-compaction memory flush turn.",
  "The session is near auto-compaction; capture durable memories in Basic Memory with write_note or edit_note.",
  `You may reply, but usually ${SILENT_REPLY_TOKEN} is correct.`,
].join(" ")

// Returning a tools-arm plan makes the host persist through this plugin's own
// tools instead of appending to a workspace memory file. The host has already
// honored agents.defaults.compaction.memoryFlush.enabled before calling this.
const resolveFlushPlan: MemoryProviderFlushPlanResolver = () => ({
  prompt: FLUSH_PROMPT,
  systemPrompt: FLUSH_SYSTEM_PROMPT,
  persistenceToolNames: FLUSH_PERSISTENCE_TOOL_NAMES,
  lookupToolNames: FLUSH_LOOKUP_TOOL_NAMES,
})

// --- Prompt guidance ---

/**
 * OpenClaw fills any capability field the slot owner leaves empty from other
 * memory registrations, including memory-core's prompt section, which points
 * agents at its file-only memory_search. Supplying our own section keeps the
 * agent's guidance on the Basic Memory tools.
 */
function buildPromptSection(params: { availableTools: Set<string> }): string[] {
  if (!params.availableTools.has("search_notes")) return []
  return [
    "## Basic Memory",
    "Long-term memory lives in Basic Memory, a knowledge graph of Markdown notes.",
    "Before answering questions about prior work, decisions, people or preferences, search it with search_notes and read the best match with read_note.",
    "Earlier conversations are saved automatically in daily conversation notes.",
    "memory_search and memory_get cover only the workspace MEMORY.md and memory/ files, not Basic Memory.",
  ]
}

// --- Capability ---

export function buildBasicMemoryCapability(
  client: BmClient,
): MemoryPluginCapability {
  return {
    providerRuntime: {
      async open({ context }) {
        return { provider: openBasicMemoryReader(client, context) }
      },
    },
    recallToolNames: RECALL_TOOL_NAMES,
    providerFlushPlanResolver: resolveFlushPlan,
    promptBuilder: buildPromptSection,
  }
}
