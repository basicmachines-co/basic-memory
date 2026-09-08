import { describe, expect, it } from "bun:test"
import type { OpenClawPluginApi } from "openclaw/plugin-sdk/plugin-entry"
import {
  claimsMemorySave,
  registerSaveClaimGuard,
  SAVE_CLAIM_CORRECTION,
} from "./save-claims.ts"

function harness() {
  const handlers = new Map<string, (event: any, context: any) => any>()
  registerSaveClaimGuard({
    on: (name: string, handler: (event: any, context: any) => any) =>
      handlers.set(name, handler),
  } as unknown as OpenClawPluginApi)
  const dispatch = (name: string, event: object, context: object = {}) => {
    const handler = handlers.get(name)
    if (!handler) throw new Error(`Missing hook: ${name}`)
    return handler(event, context)
  }
  const begin = (runId = "run-1", sessionKey = "session-1") =>
    dispatch(
      "llm_input",
      { runId, prompt: "Remember this preference" },
      { sessionKey },
    )
  const write = (
    result: unknown,
    runId = "run-1",
    sessionKey = "session-1",
    toolName = "write_note",
  ) => dispatch("after_tool_call", { toolName, result }, { runId, sessionKey })
  const reply = (
    runId: string | undefined = "run-1",
    sessionKey = "session-1",
    kind = "final",
  ) =>
    dispatch("reply_payload_sending", {
      runId,
      sessionKey,
      kind,
      payload: {
        text: "I have saved that preference.",
        mediaUrl: "https://example.com/image.png",
      },
    })
  return { dispatch, begin, write, reply }
}

describe("save claim delivery guard", () => {
  for (const prompt of ["Save this", "Record this", "Note this"]) {
    it(`treats ${prompt} as a capture request`, () => {
      const host = harness()
      host.dispatch(
        "llm_input",
        { runId: "run-1", prompt },
        { sessionKey: "session-1" },
      )
      expect(host.reply().payload.text).toContain(SAVE_CLAIM_CORRECTION)
    })
  }
  it("corrects final claims and preserves other payload fields", () => {
    const host = harness()
    host.begin()
    expect(host.reply().payload).toEqual({
      text: `I have saved that preference.\n\n${SAVE_CLAIM_CORRECTION}`,
      mediaUrl: "https://example.com/image.png",
    })
    expect(host.reply("run-1", "session-1", "block")).toBeUndefined()
    expect(
      host.dispatch("reply_payload_sending", {
        runId: "run-1",
        sessionKey: "session-1",
        kind: "final",
        payload: host.reply().payload,
      }),
    ).toBeUndefined()
  })

  for (const toolName of ["write_note", "edit_note"]) {
    it(`preserves ${toolName} evidence across repeated model calls`, () => {
      const host = harness()
      host.begin()
      host.write(
        { details: { permalink: "preferences", file_path: "preferences.md" } },
        "run-1",
        "session-1",
        toolName,
      )
      host.begin()
      expect(host.reply()).toBeUndefined()
      host.begin("run-2")
      expect(host.reply("run-2").payload.text).toContain(SAVE_CLAIM_CORRECTION)
    })
  }

  for (const result of [
    undefined,
    {},
    { details: { error: "write_note_failed" } },
    {
      details: {
        permalink: "preferences",
        file_path: "preferences.md",
        error: "note_already_exists",
      },
    },
  ]) {
    it(`rejects unsuccessful evidence: ${JSON.stringify(result)}`, () => {
      const host = harness()
      host.begin()
      host.write(result)
      expect(host.reply().payload.text).toContain(SAVE_CLAIM_CORRECTION)
    })
  }

  it("never borrows another session or uncorrelated run", () => {
    const host = harness()
    host.begin()
    host.write(
      { details: { permalink: "preferences", file_path: "preferences.md" } },
      "run-1",
      "session-2",
    )
    expect(host.reply().payload.text).toContain(SAVE_CLAIM_CORRECTION)
    expect(host.reply("unknown")).toBeUndefined()
    expect(
      host.dispatch("reply_payload_sending", {
        kind: "final",
        sessionKey: "session-1",
        payload: { text: "Saved it." },
      }),
    ).toBeUndefined()
  })

  it("bounds evidence for aborted runs", () => {
    const host = harness()
    host.begin("oldest")
    for (let index = 0; index < 256; index++) host.begin(`run-${index}`)
    expect(host.reply("oldest")).toBeUndefined()
    expect(host.reply("run-255").payload.text).toContain(SAVE_CLAIM_CORRECTION)
  })
})

describe("claim recognition", () => {
  for (const text of [
    "No problem, I've saved it to Basic Memory.",
    "I've saved it. Do you need anything else?",
  ]) {
    it(`ignores unrelated qualifications: ${text}`, () => {
      expect(claimsMemorySave(text, true)).toBe(true)
    })
  }
  for (const text of [
    "Sure, I saved it in Basic Memory.",
    "Done — I've recorded it.",
  ]) {
    it(`recognizes conversational prefaces: ${text}`, () => {
      expect(claimsMemorySave(text, true)).toBe(true)
    })
  }
  for (const text of [
    "I saved it locally, but did not store it in Basic Memory.",
    "Saved? No, I did not.",
    "I have saved nothing.",
    "> I saved it.",
    "```\nI saved it.\n```",
    "I updated my response.",
    "Saved the image to disk.",
  ]) {
    it(`leaves qualified or quoted text alone: ${text}`, () => {
      expect(claimsMemorySave(text, true)).toBe(false)
    })
  }
  it("requires a memory request or explicit Basic Memory claim", () => {
    expect(claimsMemorySave("I saved the screenshot.", false)).toBe(false)
    expect(claimsMemorySave("I saved it in Basic Memory.", false)).toBe(true)
  })
})
