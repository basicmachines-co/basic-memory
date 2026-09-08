import type { OpenClawPluginApi } from "openclaw/plugin-sdk/plugin-entry"

export const SAVE_CLAIM_CORRECTION =
  "Basic Memory verification: no successful Basic Memory write was observed in this run. The save claim above is unverified."

interface RunEvidence {
  memoryRequested: boolean
  writeObserved: boolean
}

const MAX_TRACKED_RUNS = 256
const MEMORY_DESTINATION =
  /\b(?:to|in|on|into)\s+(?:basic[- ]memory\b|memory:\/\/)/i
const OTHER_DESTINATION = /\blocally\b|\b(?:to|on|in)\s+\S/i

export function claimsMemorySave(
  text: string,
  memoryRequested: boolean,
): boolean {
  let fenced = false
  for (const raw of text.split("\n")) {
    const line = raw.trim()
    if (/^(?:```|~~~)/.test(line)) {
      fenced = !fenced
      continue
    }
    if (fenced || /^[>"']/.test(line)) continue
    const plain = line
      .replace(/\*\*|__/g, "")
      .replace(/^(?:[-+*]|\d+[.)])\s+/, "")
    for (const sentence of plain.split(/(?<=[.!?])\s+/)) {
      const match =
        /(?:^|[,:;—–]\s+)(?:I(?:['’]ve| have)?\s+|(?:it|that|this)(?:['’]s| is| has been)\s+(?:now\s+)?)?(?:saved|stored|recorded|remembered)\b/i.exec(
          sentence,
        )
      if (!match) continue
      // A later retraction of the save still qualifies it; a denial of an
      // unrelated action (such as changing settings) does not.
      if (
        /\b(?:not|never)\s+(?:actually\s+)?(?:save|store|record|remember|write|persist)\b/i.test(
          sentence.slice(match.index),
        )
      )
        continue
      const claim = sentence
        .slice(match.index)
        .replace(/^[,:;—–]\s+/, "")
        .split(/[;,]\s+/)[0]
      if (!memoryRequested && !MEMORY_DESTINATION.test(claim)) continue
      // Only the save clause supplies qualifications; a greeting or later question does not.
      if (/\b(?:not|never|nothing|none|zero|no)\b|\?/i.test(claim)) continue
      if (
        /\b(?:yesterday|previously|earlier|already|last\s+(?:time|week|month|year|session))\b/i.test(
          claim,
        )
      )
        continue
      if (!MEMORY_DESTINATION.test(claim) && OTHER_DESTINATION.test(claim))
        continue
      return true
    }
  }
  return false
}

function successfulWrite(result: unknown): boolean {
  if (typeof result !== "object" || result === null || !("details" in result))
    return false
  const details = result.details
  return (
    typeof details === "object" &&
    details !== null &&
    !("error" in details) &&
    "permalink" in details &&
    typeof details.permalink === "string" &&
    details.permalink.length > 0 &&
    "file_path" in details &&
    typeof details.file_path === "string" &&
    details.file_path.length > 0
  )
}

export function registerSaveClaimGuard(api: OpenClawPluginApi): void {
  const runs = new Map<string, RunEvidence>()
  const key = (sessionKey: string, runId: string) =>
    JSON.stringify([sessionKey, runId])

  api.on("llm_input", (event, context) => {
    if (!context.sessionKey || !event.runId) return
    const runKey = key(context.sessionKey, event.runId)
    // A model retry within the same run must retain successful write evidence.
    if (runs.has(runKey)) return
    runs.set(runKey, {
      memoryRequested:
        /\bremember\b/i.test(event.prompt) ||
        MEMORY_DESTINATION.test(event.prompt) ||
        (/\b(?:save|record|note)\s+/i.test(event.prompt) &&
          !OTHER_DESTINATION.test(event.prompt)),
      writeObserved: false,
    })
    // Aborted runs may never deliver a final payload; bound retained evidence.
    if (runs.size > MAX_TRACKED_RUNS) {
      const oldest = runs.keys().next().value
      if (oldest !== undefined) runs.delete(oldest)
    }
  })

  api.on("after_tool_call", (event, context) => {
    const runId = context.runId ?? event.runId
    if (!context.sessionKey || !runId || event.error) return
    if (event.toolName !== "write_note" && event.toolName !== "edit_note")
      return
    const evidence = runs.get(key(context.sessionKey, runId))
    if (evidence && successfulWrite(event.result)) evidence.writeObserved = true
  })

  api.on("reply_payload_sending", (event) => {
    if (event.kind !== "final" || !event.sessionKey || !event.runId) return
    const evidence = runs.get(key(event.sessionKey, event.runId))
    // Missing correlation (including durable replay) cannot borrow another run's state.
    if (!evidence || evidence.writeObserved || !event.payload.text) return
    if (event.payload.text.includes(SAVE_CLAIM_CORRECTION)) return
    if (!claimsMemorySave(event.payload.text, evidence.memoryRequested)) return
    return {
      payload: {
        ...event.payload,
        text: `${event.payload.text}\n\n${SAVE_CLAIM_CORRECTION}`,
      },
    }
  })
}
