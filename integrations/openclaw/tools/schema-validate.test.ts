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

    // Regression (#1997): fixtures follow BM's ValidationReport
    // (src/basic_memory/schemas/schema.py). The tool used to read identifier,
    // valid and entity_type, which BM never sends, so every note rendered as
    // "**undefined** — invalid".
    it("renders each note by note_identifier and its passed result", async () => {
      ;(
        mockClient.schemaValidate as jest.MockedFunction<any>
      ).mockResolvedValue({
        note_type: "person",
        total_notes: 3,
        total_entities: 3,
        valid_count: 2,
        warning_count: 1,
        error_count: 1,
        results: [
          {
            note_identifier: "people/ada",
            schema_entity: "schema/person",
            passed: true,
            field_results: [],
            unmatched_observations: {},
            unmatched_relations: [],
            warnings: [],
            errors: [],
          },
          {
            note_identifier: "people/bob",
            schema_entity: "schema/person",
            passed: true,
            field_results: [],
            unmatched_observations: {},
            unmatched_relations: [],
            warnings: ["Missing optional field: email"],
            errors: [],
          },
          {
            note_identifier: "people/cy",
            schema_entity: "schema/person",
            passed: false,
            field_results: [],
            unmatched_observations: {},
            unmatched_relations: [],
            warnings: [],
            errors: ["Missing required field: name"],
          },
        ],
        type_summaries: [],
      })

      const result = await execute("call-1", { noteType: "person" })

      expect(mockClient.schemaValidate).toHaveBeenCalledWith(
        "person",
        undefined,
        undefined,
      )
      expect(result.content[0].text).toBe(
        [
          "**Type:** person",
          "**Notes:** 3 | **Valid:** 2 | **Warnings:** 1 | **Errors:** 1",
          "",
          "- **people/ada** — valid",
          // A warning does not fail a note: passed stays true.
          "- **people/bob** — valid",
          "  - warning: Missing optional field: email",
          "- **people/cy** — invalid",
          "  - error: Missing required field: name",
        ].join("\n"),
      )
      expect(result.content[0].text).not.toContain("undefined")
      expect(result.details.valid_count).toBe(2)
    })

    it("should validate by identifier", async () => {
      ;(
        mockClient.schemaValidate as jest.MockedFunction<any>
      ).mockResolvedValue({
        total_notes: 1,
        total_entities: 1,
        valid_count: 1,
        warning_count: 0,
        error_count: 0,
        results: [
          {
            note_identifier: "notes/my-note",
            schema_entity: "schema/note",
            passed: true,
            warnings: [],
            errors: [],
          },
        ],
      })

      const result = await execute("call-1", {
        identifier: "notes/my-note",
      })

      expect(mockClient.schemaValidate).toHaveBeenCalledWith(
        undefined,
        "notes/my-note",
        undefined,
      )
      // BM drops a null note_type from JSON, so no Type line is rendered.
      expect(result.content[0].text).toBe(
        "**Notes:** 1 | **Valid:** 1 | **Warnings:** 0 | **Errors:** 0\n\n- **notes/my-note** — valid",
      )
    })

    it("should pass project to client.schemaValidate", async () => {
      ;(
        mockClient.schemaValidate as jest.MockedFunction<any>
      ).mockResolvedValue({
        note_type: "person",
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
