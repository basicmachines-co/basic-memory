import { beforeEach, describe, expect, it, jest } from "bun:test"
import type { BmClient } from "../bm-client.ts"
import type { BasicMemoryConfig } from "../config.ts"
import { buildCaptureHandler } from "./capture.ts"

describe("capture hook", () => {
  let mockClient: { indexConversation: jest.Mock }
  let mockConfig: BasicMemoryConfig

  beforeEach(() => {
    mockClient = {
      indexConversation: jest.fn().mockResolvedValue(undefined),
    }
    mockConfig = {
      project: "test-project",
      bmPath: "/usr/bin/bm",
      memoryDir: "memory/",
      memoryFile: "MEMORY.md",
      projectPath: "/tmp/test",
      autoCapture: true,
      captureMinChars: 10,
      autoRecall: true,
      recallPrompt: "Check for active tasks and recent activity.",
      debug: false,
    }
  })

  describe("buildCaptureHandler", () => {
    it("should return a function", () => {
      const handler = buildCaptureHandler(
        mockClient as unknown as BmClient,
        mockConfig,
      )
      expect(typeof handler).toBe("function")
    })
  })

  describe("capture handler execution", () => {
    let captureHandler: Function

    beforeEach(() => {
      captureHandler = buildCaptureHandler(
        mockClient as unknown as BmClient,
        mockConfig,
      )
    })

    it("should ignore events without success", async () => {
      await captureHandler({
        success: false,
        messages: [
          { role: "user", content: "Hello" },
          { role: "assistant", content: "Hi there" },
        ],
      })
      expect(mockClient.indexConversation).not.toHaveBeenCalled()
    })

    it("should ignore events without messages", async () => {
      await captureHandler({ success: true })
      expect(mockClient.indexConversation).not.toHaveBeenCalled()
    })

    it("should ignore events with non-array messages", async () => {
      await captureHandler({ success: true, messages: "not an array" })
      expect(mockClient.indexConversation).not.toHaveBeenCalled()
    })

    it("should ignore events with empty messages", async () => {
      await captureHandler({ success: true, messages: [] })
      expect(mockClient.indexConversation).not.toHaveBeenCalled()
    })

    it("should extract and index user-assistant conversation", async () => {
      await captureHandler({
        success: true,
        messages: [
          { role: "user", content: "What is the weather like?" },
          {
            role: "assistant",
            content: "I don't have access to real-time weather data.",
          },
        ],
      })
      expect(mockClient.indexConversation).toHaveBeenCalledWith(
        "What is the weather like?",
        "I don't have access to real-time weather data.",
      )
    })

    it("should find last user message when multiple users exist", async () => {
      await captureHandler({
        success: true,
        messages: [
          { role: "user", content: "First question" },
          { role: "assistant", content: "First answer" },
          { role: "user", content: "Second question" },
          { role: "assistant", content: "Second answer" },
        ],
      })
      expect(mockClient.indexConversation).toHaveBeenCalledWith(
        "Second question",
        "Second answer",
      )
    })

    it("should handle structured content blocks", async () => {
      await captureHandler({
        success: true,
        messages: [
          {
            role: "user",
            content: [
              { type: "text", text: "Please explain" },
              { type: "text", text: " how this works" },
            ],
          },
          {
            role: "assistant",
            content: [
              { type: "text", text: "Here's how it works:" },
              { type: "text", text: " step by step explanation" },
            ],
          },
        ],
      })
      expect(mockClient.indexConversation).toHaveBeenCalledWith(
        "Please explain\n how this works",
        "Here's how it works:\n step by step explanation",
      )
    })

    it("should skip conversations that are too short", async () => {
      await captureHandler({
        success: true,
        messages: [
          { role: "user", content: "Hi" },
          { role: "assistant", content: "Hello" },
        ],
      })
      expect(mockClient.indexConversation).not.toHaveBeenCalled()
    })

    it("should process conversation when at least one message is long enough", async () => {
      await captureHandler({
        success: true,
        messages: [
          { role: "user", content: "This is a longer user message" },
          { role: "assistant", content: "Ok" },
        ],
      })
      expect(mockClient.indexConversation).toHaveBeenCalledWith(
        "This is a longer user message",
        "Ok",
      )
    })

    it("should handle indexConversation errors gracefully", async () => {
      mockClient.indexConversation.mockRejectedValue(new Error("Failed"))
      await captureHandler({
        success: true,
        messages: [
          { role: "user", content: "This should cause an error" },
          { role: "assistant", content: "This response will fail to index" },
        ],
      })
      // Should not throw
    })

    it("should handle system messages between user and assistant", async () => {
      await captureHandler({
        success: true,
        messages: [
          { role: "user", content: "User question" },
          { role: "system", content: "System message" },
          { role: "assistant", content: "Assistant answer" },
        ],
      })
      expect(mockClient.indexConversation).toHaveBeenCalledWith(
        "User question",
        "Assistant answer",
      )
    })

    it("should handle conversation with only assistant message", async () => {
      await captureHandler({
        success: true,
        messages: [
          { role: "assistant", content: "This is a long assistant message" },
        ],
      })
      expect(mockClient.indexConversation).not.toHaveBeenCalled()
    })

    it("should respect custom captureMinChars threshold", async () => {
      const strictConfig = { ...mockConfig, captureMinChars: 50 }
      const strictHandler = buildCaptureHandler(
        mockClient as unknown as BmClient,
        strictConfig,
      )

      // Both messages under 50 chars — should skip
      await strictHandler({
        success: true,
        messages: [
          { role: "user", content: "This is a longer user message" },
          { role: "assistant", content: "And a longer assistant reply" },
        ],
      })
      expect(mockClient.indexConversation).not.toHaveBeenCalled()

      // One message over 50 chars — should capture
      await strictHandler({
        success: true,
        messages: [
          {
            role: "user",
            content:
              "This is a very long message that definitely exceeds fifty characters in total length",
          },
          { role: "assistant", content: "Ok" },
        ],
      })
      expect(mockClient.indexConversation).toHaveBeenCalledTimes(1)
    })

    it("should capture everything when captureMinChars is 0", async () => {
      const permissiveConfig = { ...mockConfig, captureMinChars: 0 }
      const permissiveHandler = buildCaptureHandler(
        mockClient as unknown as BmClient,
        permissiveConfig,
      )

      await permissiveHandler({
        success: true,
        messages: [
          { role: "user", content: "Hi" },
          { role: "assistant", content: "Hello" },
        ],
      })
      expect(mockClient.indexConversation).toHaveBeenCalledTimes(1)
    })
  })
})
