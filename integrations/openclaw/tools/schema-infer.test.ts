import { beforeEach, describe, expect, it, jest } from "bun:test"
import type { OpenClawPluginApi } from "openclaw/plugin-sdk/plugin-entry"
import type { BmClient } from "../bm-client.ts"
import { registerSchemaInferTool } from "./schema-infer.ts"

describe("schema-infer tool", () => {
  let mockApi: OpenClawPluginApi
  let mockClient: BmClient

  beforeEach(() => {
    mockApi = { registerTool: jest.fn() } as any
    mockClient = { schemaInfer: jest.fn() } as any
  })

  describe("registerSchemaInferTool", () => {
    it("should register schema_infer tool", () => {
      registerSchemaInferTool(mockApi, mockClient)

      expect(mockApi.registerTool).toHaveBeenCalledWith(
        expect.objectContaining({
          name: "schema_infer",
          label: "Schema Infer",
          execute: expect.any(Function),
        }),
        { name: "schema_infer" },
      )
    })
  })

  describe("tool execution", () => {
    let execute: Function

    beforeEach(() => {
      registerSchemaInferTool(mockApi, mockClient)
      const call = (mockApi.registerTool as jest.MockedFunction<any>).mock
        .calls[0]
      execute = call[0].execute
    })

    it("should infer schema for a note type", async () => {
      ;(mockClient.schemaInfer as jest.MockedFunction<any>).mockResolvedValue({
        entity_type: "person",
        notes_analyzed: 12,
        field_frequencies: [
          {
            name: "name",
            percentage: 1.0,
            count: 12,
            total: 12,
            source: "observation",
          },
          {
            name: "email",
            percentage: 0.75,
            count: 9,
            total: 12,
            source: "observation",
          },
        ],
        suggested_schema: { name: "string", "email?": "string" },
        suggested_required: ["name"],
        suggested_optional: ["email"],
        excluded: [],
      })

      const result = await execute("call-1", { noteType: "person" })

      expect(mockClient.schemaInfer).toHaveBeenCalledWith(
        "person",
        undefined,
        undefined,
      )
      expect(result.content[0].text).toContain("person")
      expect(result.content[0].text).toContain("12")
      expect(result.content[0].text).toContain("name")
      expect(result.details.suggested_required).toContain("name")
    })

    it("should pass custom threshold", async () => {
      ;(mockClient.schemaInfer as jest.MockedFunction<any>).mockResolvedValue({
        entity_type: "task",
        notes_analyzed: 5,
        field_frequencies: [],
        suggested_schema: {},
        suggested_required: [],
        suggested_optional: [],
        excluded: [],
      })

      await execute("call-1", { noteType: "task", threshold: 0.5 })

      expect(mockClient.schemaInfer).toHaveBeenCalledWith(
        "task",
        0.5,
        undefined,
      )
    })

    it("should pass project to client.schemaInfer", async () => {
      ;(mockClient.schemaInfer as jest.MockedFunction<any>).mockResolvedValue({
        entity_type: "person",
        notes_analyzed: 1,
        field_frequencies: [],
        suggested_schema: {},
        suggested_required: [],
        suggested_optional: [],
        excluded: [],
      })

      await execute("call-1", {
        noteType: "person",
        project: "other-project",
      })

      expect(mockClient.schemaInfer).toHaveBeenCalledWith(
        "person",
        undefined,
        "other-project",
      )
    })

    it("should handle errors gracefully", async () => {
      ;(mockClient.schemaInfer as jest.MockedFunction<any>).mockRejectedValue(
        new Error("timeout"),
      )

      const result = await execute("call-1", { noteType: "person" })

      expect(result.content[0].text).toContain("failed")
    })
  })
})
