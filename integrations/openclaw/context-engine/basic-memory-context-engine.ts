import type {
  AssembleResult,
  BootstrapResult,
  CompactResult,
  ContextEngine,
  SubagentSpawnPreparation,
} from "openclaw/plugin-sdk"
import { delegateCompactionToRuntime } from "openclaw/plugin-sdk/core"
import type { BmClient } from "../bm-client.ts"
import type { BasicMemoryConfig } from "../config.ts"
import { selectCaptureTurn } from "../hooks/capture.ts"
import { loadRecallState } from "../hooks/recall.ts"
import { log } from "../logger.ts"

export const MAX_ASSEMBLE_RECALL_CHARS = 1200
const TRUNCATED_RECALL_SUFFIX = "\n\n[Basic Memory recall truncated]"
const SUBAGENT_HANDOFF_FOLDER = "agent/subagents"
const MAX_SUBAGENT_RECALL_CHARS = 800

type BootstrapParams = Parameters<NonNullable<ContextEngine["bootstrap"]>>[0]
type AssembleParams = Parameters<ContextEngine["assemble"]>[0]
type AfterTurnParams = Parameters<NonNullable<ContextEngine["afterTurn"]>>[0]
type CompactParams = Parameters<ContextEngine["compact"]>[0]
type PrepareSubagentSpawnParams = Parameters<
  NonNullable<ContextEngine["prepareSubagentSpawn"]>
>[0]
type OnSubagentEndedParams = Parameters<
  NonNullable<ContextEngine["onSubagentEnded"]>
>[0]

interface SessionMemoryState {
  recallContext: string
}

interface SubagentHandoffState {
  noteIdentifier: string
  noteTitle: string
  parentSessionKey: string
}

function boundRecallContext(context: string): string {
  if (context.length <= MAX_ASSEMBLE_RECALL_CHARS) {
    return context
  }

  const trimmed = context
    .slice(
      0,
      Math.max(0, MAX_ASSEMBLE_RECALL_CHARS - TRUNCATED_RECALL_SUFFIX.length),
    )
    .trimEnd()

  return `${trimmed}${TRUNCATED_RECALL_SUFFIX}`
}

function slugifySessionKey(sessionKey: string): string {
  return sessionKey
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 80)
}

function buildSubagentNoteTitle(childSessionKey: string): string {
  return `subagent-handoff-${slugifySessionKey(childSessionKey)}`
}

function buildSubagentHandoffContent(params: {
  parentSessionKey: string
  childSessionKey: string
  recallContext?: string
}): string {
  const sections = [
    "# Subagent Handoff",
    "",
    "## Sessions",
    `- Parent: ${params.parentSessionKey}`,
    `- Child: ${params.childSessionKey}`,
    "",
    "## Lifecycle",
    `- Spawned: ${new Date().toISOString()}`,
  ]

  if (params.recallContext) {
    sections.push(
      "",
      "## Parent Basic Memory Context",
      params.recallContext.slice(0, MAX_SUBAGENT_RECALL_CHARS).trimEnd(),
    )
  }

  return sections.join("\n")
}

function buildSubagentCompletionUpdate(params: {
  childSessionKey: string
  reason: "deleted" | "completed" | "swept" | "released"
}): string {
  const statusLine =
    params.reason === "completed"
      ? "Child run completed. Durable conversation capture continues through the normal afterTurn path."
      : `Child run ended with reason: ${params.reason}.`

  return [
    "",
    "## Completion",
    `- Child: ${params.childSessionKey}`,
    `- Ended: ${new Date().toISOString()}`,
    `- Reason: ${params.reason}`,
    "",
    statusLine,
  ].join("\n")
}

export class BasicMemoryContextEngine implements ContextEngine {
  readonly info = {
    id: "openclaw-basic-memory",
    name: "Basic Memory Context Engine",
    version: "0.1.5",
    ownsCompaction: false,
  } as const

  private readonly sessionState = new Map<string, SessionMemoryState>()
  private readonly subagentState = new Map<string, SubagentHandoffState>()

  constructor(
    private readonly client: BmClient,
    private readonly cfg: BasicMemoryConfig,
  ) {}

  async bootstrap(params: BootstrapParams): Promise<BootstrapResult> {
    if (!this.cfg.autoRecall) {
      this.sessionState.delete(params.sessionId)
      return { bootstrapped: false, reason: "autoRecall disabled" }
    }

    try {
      const recall = await loadRecallState(this.client, this.cfg)
      if (!recall) {
        this.sessionState.delete(params.sessionId)
        return { bootstrapped: false, reason: "no recall context found" }
      }

      this.sessionState.set(params.sessionId, {
        recallContext: boundRecallContext(recall.context),
      })

      log.debug(
        `context-engine bootstrap: session=${params.sessionId} tasks=${recall.tasks.length} recent=${recall.recent.length}`,
      )

      return { bootstrapped: true }
    } catch (err) {
      this.sessionState.delete(params.sessionId)
      log.error("context-engine bootstrap failed", err)
      return { bootstrapped: false, reason: "recall failed" }
    }
  }

  async ingest(): Promise<{ ingested: boolean }> {
    return { ingested: false }
  }

  async assemble(params: AssembleParams): Promise<AssembleResult> {
    const state = this.sessionState.get(params.sessionId)

    return {
      messages: params.messages,
      estimatedTokens: 0,
      systemPromptAddition: state?.recallContext,
    }
  }

  async afterTurn(params: AfterTurnParams): Promise<void> {
    if (!this.cfg.autoCapture) return

    const newMessages = params.messages.slice(params.prePromptMessageCount)
    const turn =
      selectCaptureTurn(newMessages, this.cfg.captureMinChars) ??
      selectCaptureTurn(params.messages, this.cfg.captureMinChars)

    if (!turn) return

    log.debug(
      `context-engine afterTurn: session=${params.sessionId} user=${turn.userText.length} assistant=${turn.assistantText.length}`,
    )

    try {
      await this.client.indexConversation(turn.userText, turn.assistantText)
    } catch (err) {
      log.error("context-engine capture failed", err)
    }
  }

  async compact(params: CompactParams): Promise<CompactResult> {
    return delegateCompactionToRuntime(params)
  }

  async prepareSubagentSpawn(
    params: PrepareSubagentSpawnParams,
  ): Promise<SubagentSpawnPreparation | undefined> {
    const parentState = this.sessionState.get(params.parentSessionKey)
    const noteTitle = buildSubagentNoteTitle(params.childSessionKey)

    try {
      const note = await this.client.writeNote(
        noteTitle,
        buildSubagentHandoffContent({
          parentSessionKey: params.parentSessionKey,
          childSessionKey: params.childSessionKey,
          recallContext: parentState?.recallContext,
        }),
        SUBAGENT_HANDOFF_FOLDER,
      )

      this.subagentState.set(params.childSessionKey, {
        noteIdentifier: note.permalink,
        noteTitle: note.title,
        parentSessionKey: params.parentSessionKey,
      })

      return {
        rollback: async () => {
          const handoff = this.subagentState.get(params.childSessionKey)
          this.subagentState.delete(params.childSessionKey)
          if (!handoff) return

          try {
            await this.client.deleteNote(handoff.noteIdentifier)
          } catch (err) {
            log.error("context-engine subagent rollback failed", err)
          }
        },
      }
    } catch (err) {
      log.error("context-engine prepareSubagentSpawn failed", err)
      return undefined
    }
  }

  async onSubagentEnded(params: OnSubagentEndedParams): Promise<void> {
    const handoff = this.subagentState.get(params.childSessionKey)
    if (!handoff) return

    this.subagentState.delete(params.childSessionKey)

    try {
      await this.client.editNote(
        handoff.noteIdentifier,
        "append",
        buildSubagentCompletionUpdate(params),
      )
    } catch (err) {
      log.error("context-engine onSubagentEnded failed", err)
    }
  }

  async dispose(): Promise<void> {
    this.sessionState.clear()
    this.subagentState.clear()
  }
}
