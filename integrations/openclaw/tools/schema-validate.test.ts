import { beforeEach, describe, expect, it, jest } from "bun:test"
import type { OpenClawPluginApi } from "openclaw/plugin-sdk/plugin-entry"
import type { BmClient } from "../bm-client.ts"
import { registerSchemaValidateTool } from "./schema-validate.ts"

describe("schema-validate tool", () => {
  let mockApi: OpenClawPluginApi
  let mockClient: BmClient

  beforeEach(() => {
    mockApi = { registerTool: jest.fn() } as any
    mockClient = { schemaValidate: jest.fn() } as any
  })

  describe("registerSchemaValidateTool", () => {
    it("should register schema_validate tool", () => {
      registerSchemaValidateTool(mockApi, mockClient)

      expect(mockApi.registerTool).toHaveBeenCalledWith(
        expect.objectContaining({
          name: "schema_validate",
          label: "Schema Validate",
          execute: expect.any(Function),
        }),
        { name: "schema_validate" },
      )
    })
  })

  describe("tool execution", () => {
    let execute: Function

    beforeEach(() => {
      registerSchemaValidateTool(mockApi, mockClient)
      const call = (mockApi.registerTool as jest.MockedFunction<any>).mock
        .calls[0]
      execute = call[0].execute
    })

    it("should validate by note type", async () => {
      ;(
        mockClient.schemaValidate as jest.MockedFunction<any>
      ).mockResolvedValue({
        entity_type: "person",
        total_notes: 5,
        total_entities: 5,
        valid_count: 4,
        warning_count: 1,
        error_count: 0,
        results: [
          {
            identifier: "john-doe",
            valid: false,
            warnings: ["missing optional field: email"],
            errors: [],
          },
        ],
      })

      const result = await execute("call-1", { noteType: "person" })

      expect(mockClient.schemaValidate).toHaveBeenCalledWith(
        "person",
        undefined,
        undefined,
      )
      expect(result.content[0].text).toContain("person")
      expect(result.content[0].text).toContain("Valid:** 4")
      expect(result.content[0].text).toContain("john-doe")
      expect(result.details.valid_count).toBe(4)
    })

    it("should validate by identifier", async () => {
      ;(
        mockClient.schemaValidate as jest.MockedFunction<any>
      ).mockResolvedValue({
        entity_type: null,
        total_notes: 1,
        total_entities: 1,
        valid_count: 1,
        warning_count: 0,
        error_count: 0,
        results: [],
      })

      const result = await execute("call-1", {
        identifier: "notes/my-note",
      })

      expect(mockClient.schemaValidate).toHaveBeenCalledWith(
        undefined,
        "notes/my-note",
        undefined,
      )
      expect(result.content[0].text).toContain("Valid:** 1")
    })

    it("should pass project to client.schemaValidate", async () => {
      ;(
        mockClient.schemaValidate as jest.MockedFunction<any>
      ).mockResolvedValue({
        entity_type: "person",
        total_notes: 1,
        total_entities: 1,
        valid_count: 1,
        warning_count: 0,
        error_count: 0,
        results: [],
      })

      await execute("call-1", { noteType: "person", project: "other-project" })

      expect(mockClient.schemaValidate).toHaveBeenCalledWith(
        "person",
        undefined,
        "other-project",
      )
    })

    it("should handle BM error response (no schema found)", async () => {
      ;(
        mockClient.schemaValidate as jest.MockedFunction<any>
      ).mockResolvedValue({
        error: "No schema found for type 'Task'",
      })

      const result = await execute("call-1", { noteType: "Task" })

      expect(result.content[0].text).toContain("No schema found")
    })

    it("should handle errors gracefully", async () => {
      ;(
        mockClient.schemaValidate as jest.MockedFunction<any>
      ).mockRejectedValue(new Error("connection lost"))

      const result = await execute("call-1", { noteType: "person" })

      expect(result.content[0].text).toContain("failed")
    })
  })
})
