import type { BmClient } from "../bm-client.ts"
import type { BasicMemoryConfig } from "../config.ts"
import { log } from "../logger.ts"

/**
 * Extract text content from a message object.
 */
export function extractText(msg: Record<string, unknown>): string {
  const content = msg.content
  if (typeof content === "string") return content

  if (Array.isArray(content)) {
    const parts: string[] = []
    for (const block of content) {
      if (!block || typeof block !== "object") continue
      const b = block as Record<string, unknown>
      if (b.type === "text" && typeof b.text === "string") {
        parts.push(b.text)
      }
    }
    return parts.join("\n")
  }

  return ""
}

/**
 * Find the last user+assistant turn from the messages array.
 */
export function getLastTurn(messages: unknown[]): {
  userText: string
  assistantText: string
} {
  let lastUserIdx = -1
  for (let i = messages.length - 1; i >= 0; i--) {
    const msg = messages[i]
    if (
      msg &&
      typeof msg === "object" &&
      (msg as Record<string, unknown>).role === "user"
    ) {
      lastUserIdx = i
      break
    }
  }

  if (lastUserIdx < 0) return { userText: "", assistantText: "" }

  let userText = ""
  let assistantText = ""

  for (let i = lastUserIdx; i < messages.length; i++) {
    const msg = messages[i] as Record<string, unknown>
    if (!msg?.role) continue
    const text = extractText(msg)
    if (msg.role === "user") userText = text
    else if (msg.role === "assistant") assistantText = text
  }

  return { userText, assistantText }
}

export function selectCaptureTurn(
  messages: unknown[],
  minChars: number,
): { userText: string; assistantText: string } | null {
  const turn = getLastTurn(messages)
  if (!turn.userText && !turn.assistantText) return null
  if (turn.userText.length < minChars && turn.assistantText.length < minChars) {
    return null
  }

  return turn
}

/**
 * Build the post-turn capture handler for Mode B.
 *
 * After each agent turn, extracts the conversation content and indexes it
 * into the Basic Memory knowledge graph as a conversation note.
 */
export function buildCaptureHandler(client: BmClient, cfg: BasicMemoryConfig) {
  const minChars = cfg.captureMinChars
  return async (event: Record<string, unknown>) => {
    if (
      !event.success ||
      !Array.isArray(event.messages) ||
      event.messages.length === 0
    ) {
      return
    }

    const turn = selectCaptureTurn(event.messages, minChars)
    if (!turn) return
    const { userText, assistantText } = turn

    log.debug(
      `capturing conversation: user=${userText.length} chars, assistant=${assistantText.length} chars`,
    )

    try {
      await client.indexConversation(userText, assistantText)
    } catch (err) {
      log.error("capture failed", err)
    }
  }
}
