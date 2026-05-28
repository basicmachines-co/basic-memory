import { beforeEach, describe, expect, it, jest } from "bun:test"
import type { OpenClawPluginApi } from "openclaw/plugin-sdk/plugin-entry"
import type { BmClient } from "../bm-client.ts"
import { registerSchemaDiffTool } from "./schema-diff.ts"

describe("schema-diff tool", () => {
  let mockApi: OpenClawPluginApi
  let mockClient: BmClient

  beforeEach(() => {
    mockApi = { registerTool: jest.fn() } as any
    mockClient = { schemaDiff: jest.fn() } as any
  })

  describe("registerSchemaDiffTool", () => {
    it("should register schema_diff tool", () => {
      registerSchemaDiffTool(mockApi, mockClient)

      expect(mockApi.registerTool).toHaveBeenCalledWith(
        expect.objectContaining({
          name: "schema_diff",
          label: "Schema Diff",
          execute: expect.any(Function),
        }),
        { name: "schema_diff" },
      )
    })
  })

  describe("tool execution", () => {
    let execute: Function

    beforeEach(() => {
      registerSchemaDiffTool(mockApi, mockClient)
      const call = (mockApi.registerTool as jest.MockedFunction<any>).mock
        .calls[0]
      execute = call[0].execute
    })

    it("should show drift when fields differ", async () => {
      ;(mockClient.schemaDiff as jest.MockedFunction<any>).mockResolvedValue({
        entity_type: "person",
        schema_found: true,
        new_fields: [
          {
            name: "phone",
            source: "observation",
            count: 6,
            total: 10,
            percentage: 0.6,
          },
        ],
        dropped_fields: [
          { name: "fax", source: "observation", declared_in: "schema" },
        ],
        cardinality_changes: [],
      })

      const result = await execute("call-1", { noteType: "person" })

      expect(mockClient.schemaDiff).toHaveBeenCalledWith("person", undefined)
      expect(result.content[0].text).toContain("phone")
      expect(result.content[0].text).toContain("fax")
      expect(result.details.new_fields).toHaveLength(1)
    })

    it("should show no drift when in sync", async () => {
      ;(mockClient.schemaDiff as jest.MockedFunction<any>).mockResolvedValue({
        entity_type: "person",
        schema_found: true,
        new_fields: [],
        dropped_fields: [],
        cardinality_changes: [],
      })

      const result = await execute("call-1", { noteType: "person" })

      expect(result.content[0].text).toContain("No drift detected")
    })

    it("should handle missing schema", async () => {
      ;(mockClient.schemaDiff as jest.MockedFunction<any>).mockResolvedValue({
        entity_type: "unknown",
        schema_found: false,
        new_fields: [],
        dropped_fields: [],
        cardinality_changes: [],
      })

      const result = await execute("call-1", { noteType: "unknown" })

      expect(result.content[0].text).toContain("No schema found")
      expect(result.content[0].text).toContain("schema_infer")
    })

    it("should pass project to client.schemaDiff", async () => {
      ;(mockClient.schemaDiff as jest.MockedFunction<any>).mockResolvedValue({
        entity_type: "person",
        schema_found: true,
        new_fields: [],
        dropped_fields: [],
        cardinality_changes: [],
      })

      await execute("call-1", {
        noteType: "person",
        project: "other-project",
      })

      expect(mockClient.schemaDiff).toHaveBeenCalledWith(
        "person",
        "other-project",
      )
    })

    it("should handle errors gracefully", async () => {
      ;(mockClient.schemaDiff as jest.MockedFunction<any>).mockRejectedValue(
        new Error("server error"),
      )

      const result = await execute("call-1", { noteType: "person" })

      expect(result.content[0].text).toContain("failed")
    })
  })
})
