import { beforeEach, describe, expect, it, jest } from "bun:test"
import type { ContextEngine } from "openclaw/plugin-sdk"
import type { BmClient } from "../bm-client.ts"
import type { BasicMemoryConfig } from "../config.ts"
import {
  BasicMemoryContextEngine,
  MAX_ASSEMBLE_RECALL_CHARS,
} from "./basic-memory-context-engine.ts"

type AgentMessage = Parameters<ContextEngine["assemble"]>[0]["messages"][number]

function makeConfig(overrides?: Partial<BasicMemoryConfig>): BasicMemoryConfig {
  return {
    project: "test-project",
    bmPath: "bm",
    memoryDir: "memory/",
    memoryFile: "MEMORY.md",
    projectPath: "/tmp/test-project",
    autoCapture: true,
    captureMinChars: 10,
    autoRecall: true,
    recallPrompt:
      "Check for active tasks and recent activity. Summarize anything relevant to the current session.",
    debug: false,
    ...overrides,
  }
}

function makeMessages(
  messages: Array<Record<string, unknown>>,
): AgentMessage[] {
  return messages as AgentMessage[]
}

describe("BasicMemoryContextEngine", () => {
  let mockClient: {
    search: jest.Mock
    recentActivity: jest.Mock
    indexConversation: jest.Mock
    writeNote: jest.Mock
    editNote: jest.Mock
    deleteNote: jest.Mock
  }

  beforeEach(() => {
    mockClient = {
      search: jest.fn().mockResolvedValue([
        {
          title: "Fix auth rollout",
          permalink: "fix-auth-rollout",
          content: "Continue staging verification for auth rollout.",
          file_path: "memory/tasks/fix-auth-rollout.md",
        },
      ]),
      recentActivity: jest.fn().mockResolvedValue([
        {
          title: "API review",
          permalink: "api-review",
          file_path: "memory/api-review.md",
          created_at: "2026-03-09T12:00:00Z",
        },
      ]),
      indexConversation: jest.fn().mockResolvedValue(undefined),
      writeNote: jest.fn().mockResolvedValue({
        title: "subagent-handoff-agent-test-subagent-child-1",
        permalink:
          "agent/subagents/subagent-handoff-agent-test-subagent-child-1",
        file_path:
          "memory/agent/subagents/subagent-handoff-agent-test-subagent-child-1.md",
        content: "",
      }),
      editNote: jest.fn().mockResolvedValue({
        title: "subagent-handoff-agent-test-subagent-child-1",
        permalink:
          "agent/subagents/subagent-handoff-agent-test-subagent-child-1",
        file_path:
          "memory/agent/subagents/subagent-handoff-agent-test-subagent-child-1.md",
        operation: "append",
      }),
      deleteNote: jest.fn().mockResolvedValue({
        title: "subagent-handoff-agent-test-subagent-child-1",
        permalink:
          "agent/subagents/subagent-handoff-agent-test-subagent-child-1",
        file_path:
          "memory/agent/subagents/subagent-handoff-agent-test-subagent-child-1.md",
      }),
    }
  })

  it("bootstraps recall state from active tasks and recent activity", async () => {
    const engine = new BasicMemoryContextEngine(
      mockClient as unknown as BmClient,
      makeConfig(),
    )

    await expect(
      engine.bootstrap({
        sessionId: "session-1",
        sessionFile: "/tmp/session-1.jsonl",
      }),
    ).resolves.toEqual({ bootstrapped: true })
    expect(mockClient.search).toHaveBeenCalledWith(undefined, 5, undefined, {
      note_types: ["Task"],
      status: "active",
    })
    expect(mockClient.recentActivity).toHaveBeenCalledWith("1d")
  })

  it("skips bootstrap when recall is disabled", async () => {
    const engine = new BasicMemoryContextEngine(
      mockClient as unknown as BmClient,
      makeConfig({ autoRecall: false }),
    )

    await expect(
      engine.bootstrap({
        sessionId: "session-2",
        sessionFile: "/tmp/session-2.jsonl",
      }),
    ).resolves.toEqual({
      bootstrapped: false,
      reason: "autoRecall disabled",
    })

    const result = await engine.assemble({
      sessionId: "session-2",
      messages: makeMessages([{ role: "user", content: "hello" }]),
    })

    expect(result).toEqual({
      messages: makeMessages([{ role: "user", content: "hello" }]),
      estimatedTokens: 0,
    })
  })

  it("injects bounded BM recall during assemble when bootstrap found context", async () => {
    const engine = new BasicMemoryContextEngine(
      mockClient as unknown as BmClient,
      makeConfig(),
    )

    await engine.bootstrap({
      sessionId: "session-assemble",
      sessionFile: "/tmp/session-assemble.jsonl",
    })

    const result = await engine.assemble({
      sessionId: "session-assemble",
      messages: makeMessages([{ role: "user", content: "hello" }]),
    })

    expect(result.messages).toEqual(
      makeMessages([{ role: "user", content: "hello" }]),
    )
    expect(result.systemPromptAddition).toContain("## Active Tasks")
    expect(result.systemPromptAddition).toContain("Fix auth rollout")
    expect(result.systemPromptAddition).toContain("## Recent Activity")
    expect(result.systemPromptAddition).toContain("API review")
  })

  it("returns a no-op bootstrap result when there is no recall context", async () => {
    mockClient.search.mockResolvedValue([])
    mockClient.recentActivity.mockResolvedValue([])

    const engine = new BasicMemoryContextEngine(
      mockClient as unknown as BmClient,
      makeConfig(),
    )

    await expect(
      engine.bootstrap({
        sessionId: "session-3",
        sessionFile: "/tmp/session-3.jsonl",
      }),
    ).resolves.toEqual({
      bootstrapped: false,
      reason: "no recall context found",
    })

    const result = await engine.assemble({
      sessionId: "session-3",
      messages: makeMessages([{ role: "user", content: "hello" }]),
    })

    expect(result).toEqual({
      messages: makeMessages([{ role: "user", content: "hello" }]),
      estimatedTokens: 0,
    })
  })

  it("keeps assemble recall stable and within the hard bound", async () => {
    mockClient.search.mockResolvedValue([
      {
        title: "Long task",
        permalink: "long-task",
        content: "A".repeat(4000),
        file_path: "memory/tasks/long-task.md",
      },
    ])
    mockClient.recentActivity.mockResolvedValue([
      {
        title: "Long recent item",
        permalink: "long-recent-item",
        file_path: "memory/long-recent-item.md",
        created_at: "2026-03-09T12:00:00Z",
      },
    ])

    const engine = new BasicMemoryContextEngine(
      mockClient as unknown as BmClient,
      makeConfig({
        recallPrompt: "P".repeat(4000),
      }),
    )

    await engine.bootstrap({
      sessionId: "session-bounded",
      sessionFile: "/tmp/session-bounded.jsonl",
    })

    const first = await engine.assemble({
      sessionId: "session-bounded",
      messages: makeMessages([{ role: "user", content: "hello" }]),
    })
    const second = await engine.assemble({
      sessionId: "session-bounded",
      messages: makeMessages([{ role: "user", content: "hello" }]),
    })

    expect(first.systemPromptAddition).toBeDefined()
    expect(first.systemPromptAddition?.length).toBeLessThanOrEqual(
      MAX_ASSEMBLE_RECALL_CHARS,
    )
    expect(first.systemPromptAddition).toContain(
      "[Basic Memory recall truncated]",
    )
    expect(second.systemPromptAddition).toBe(first.systemPromptAddition)
  })

  it("creates a parent-to-child BM handoff note on subagent spawn", async () => {
    const engine = new BasicMemoryContextEngine(
      mockClient as unknown as BmClient,
      makeConfig(),
    )

    await engine.bootstrap({
      sessionId: "parent-session",
      sessionFile: "/tmp/parent-session.jsonl",
    })

    const preparation = await engine.prepareSubagentSpawn({
      parentSessionKey: "parent-session",
      childSessionKey: "agent:test:subagent:child-1",
    })

    expect(preparation).toBeDefined()
    expect(mockClient.writeNote).toHaveBeenCalledWith(
      "subagent-handoff-agent-test-subagent-child-1",
      expect.stringContaining("## Parent Basic Memory Context"),
      "agent/subagents",
    )
  })

  it("rolls back the handoff note when subagent spawn fails after preparation", async () => {
    const engine = new BasicMemoryContextEngine(
      mockClient as unknown as BmClient,
      makeConfig(),
    )

    const preparation = await engine.prepareSubagentSpawn({
      parentSessionKey: "parent-session",
      childSessionKey: "agent:test:subagent:child-rollback",
    })

    expect(preparation).toBeDefined()
    await preparation?.rollback()

    expect(mockClient.deleteNote).toHaveBeenCalledWith(
      "agent/subagents/subagent-handoff-agent-test-subagent-child-1",
    )
  })

  it("appends completion details to the handoff note when a child session completes", async () => {
    const engine = new BasicMemoryContextEngine(
      mockClient as unknown as BmClient,
      makeConfig(),
    )

    await engine.prepareSubagentSpawn({
      parentSessionKey: "parent-session",
      childSessionKey: "agent:test:subagent:child-complete",
    })

    await engine.onSubagentEnded({
      childSessionKey: "agent:test:subagent:child-complete",
      reason: "completed",
    })

    expect(mockClient.editNote).toHaveBeenCalledWith(
      "agent/subagents/subagent-handoff-agent-test-subagent-child-1",
      "append",
      expect.stringContaining("Reason: completed"),
    )
    expect(mockClient.editNote).toHaveBeenCalledWith(
      "agent/subagents/subagent-handoff-agent-test-subagent-child-1",
      "append",
      expect.stringContaining(
        "Durable conversation capture continues through the normal afterTurn path.",
      ),
    )
  })

  it("handles deleted, released, and swept child endings without errors", async () => {
    const reasons = ["deleted", "released", "swept"] as const

    for (const reason of reasons) {
      const engine = new BasicMemoryContextEngine(
        mockClient as unknown as BmClient,
        makeConfig(),
      )

      await engine.prepareSubagentSpawn({
        parentSessionKey: "parent-session",
        childSessionKey: `agent:test:subagent:${reason}`,
      })

      await expect(
        engine.onSubagentEnded({
          childSessionKey: `agent:test:subagent:${reason}`,
          reason,
        }),
      ).resolves.toBeUndefined()
    }

    expect(mockClient.editNote).toHaveBeenCalledTimes(3)
  })

  it("captures only the current turn after prePromptMessageCount", async () => {
    const engine = new BasicMemoryContextEngine(
      mockClient as unknown as BmClient,
      makeConfig(),
    )

    await engine.afterTurn({
      sessionId: "session-4",
      sessionFile: "/tmp/session-4.jsonl",
      prePromptMessageCount: 2,
      messages: makeMessages([
        { role: "user", content: "Old question" },
        { role: "assistant", content: "Old answer" },
        { role: "user", content: "Current question with enough detail" },
        { role: "assistant", content: "Current answer with enough detail" },
      ]),
    })

    expect(mockClient.indexConversation).toHaveBeenCalledWith(
      "Current question with enough detail",
      "Current answer with enough detail",
    )
  })

  it("respects captureMinChars for afterTurn capture", async () => {
    const engine = new BasicMemoryContextEngine(
      mockClient as unknown as BmClient,
      makeConfig({ captureMinChars: 50 }),
    )

    await engine.afterTurn({
      sessionId: "session-5",
      sessionFile: "/tmp/session-5.jsonl",
      prePromptMessageCount: 0,
      messages: makeMessages([
        { role: "user", content: "short" },
        { role: "assistant", content: "tiny" },
      ]),
    })

    expect(mockClient.indexConversation).not.toHaveBeenCalled()
  })

  it("swallows capture failures in afterTurn", async () => {
    mockClient.indexConversation.mockRejectedValue(new Error("BM down"))
    const engine = new BasicMemoryContextEngine(
      mockClient as unknown as BmClient,
      makeConfig(),
    )

    await expect(
      engine.afterTurn({
        sessionId: "session-6",
        sessionFile: "/tmp/session-6.jsonl",
        prePromptMessageCount: 0,
        messages: makeMessages([
          { role: "user", content: "Current question with enough detail" },
          { role: "assistant", content: "Current answer with enough detail" },
        ]),
      }),
    ).resolves.toBeUndefined()
  })
})
