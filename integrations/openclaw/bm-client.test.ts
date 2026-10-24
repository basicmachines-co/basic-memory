import { beforeEach, describe, expect, it, jest } from "bun:test"
import {
  BmClient,
  BmToolError,
  NoteAlreadyExistsError,
  NoteRevisionConflictError,
} from "./bm-client.ts"

const DEFAULT_PROJECT = "test-project"

function mcpResult(payload: unknown) {
  return {
    structuredContent: { result: payload },
    content: [
      {
        type: "text",
        text: JSON.stringify(payload),
      },
    ],
  }
}

function mcpToolError(text: string) {
  return { isError: true, content: [{ type: "text", text }] }
}

function setConnected(client: BmClient, callTool: jest.Mock) {
  ;(client as any).client = {
    callTool,
    close: jest.fn().mockResolvedValue(undefined),
  }
  ;(client as any).transport = {
    close: jest.fn().mockResolvedValue(undefined),
  }
}

describe("BmClient MCP behavior", () => {
  let client: BmClient

  beforeEach(() => {
    client = new BmClient("/usr/local/bin/bm", DEFAULT_PROJECT)
  })

  it("readNote calls bm_read with JSON output and no frontmatter by default", async () => {
    const callTool = jest.fn().mockResolvedValue(
      mcpResult({
        title: "t",
        permalink: "p",
        content: "body",
        file_path: "notes/t.md",
        frontmatter: null,
      }),
    )
    setConnected(client, callTool)

    const note = await client.readNote("t")

    expect(callTool).toHaveBeenCalledWith({
      name: "bm_read",
      arguments: {
        identifier: "t",
        include_frontmatter: false,
        output_format: "json",
        project: DEFAULT_PROJECT,
      },
    })
    expect(note.content).toBe("body")
  })

  it("readNote includes frontmatter when requested", async () => {
    const raw = "---\ntitle: t\n---\n\nbody"
    const callTool = jest.fn().mockResolvedValue(
      mcpResult({
        title: "t",
        permalink: "p",
        content: raw,
        file_path: "notes/t.md",
        frontmatter: { title: "t" },
      }),
    )
    setConnected(client, callTool)

    const note = await client.readNote("t", { includeFrontmatter: true })

    expect(callTool).toHaveBeenCalledWith({
      name: "bm_read",
      arguments: {
        identifier: "t",
        include_frontmatter: true,
        output_format: "json",
        project: DEFAULT_PROJECT,
      },
    })
    expect(note.content).toBe(raw)
    expect(note.frontmatter).toEqual({ title: "t" })
  })

  it("writeNote calls bm_write with JSON output", async () => {
    const callTool = jest.fn().mockResolvedValue(
      mcpResult({
        title: "Note",
        permalink: "notes/note",
        file_path: "notes/note.md",
        checksum: "abc123",
        action: "created",
      }),
    )
    setConnected(client, callTool)

    const result = await client.writeNote("Note", "hello", "notes")

    expect(callTool).toHaveBeenCalledWith({
      name: "bm_write",
      arguments: {
        title: "Note",
        content: "hello",
        directory: "notes",
        output_format: "json",
        project: DEFAULT_PROJECT,
      },
    })
    expect(result.checksum).toBe("abc123")
    expect(result.action).toBe("created")
  })

  it("writeNote passes overwrite flag when provided", async () => {
    const callTool = jest.fn().mockResolvedValue(
      mcpResult({
        title: "Note",
        permalink: "notes/note",
        file_path: "notes/note.md",
        action: "updated",
      }),
    )
    setConnected(client, callTool)

    await client.writeNote("Note", "hello", "notes", undefined, true)

    expect(callTool).toHaveBeenCalledWith({
      name: "bm_write",
      arguments: {
        title: "Note",
        content: "hello",
        directory: "notes",
        output_format: "json",
        project: DEFAULT_PROJECT,
        overwrite: true,
      },
    })
  })

  it("writeNote passes metadata, tags, and note_type when provided", async () => {
    const callTool = jest.fn().mockResolvedValue(
      mcpResult({
        title: "Task Note",
        permalink: "tasks/task-note",
        file_path: "tasks/task-note.md",
        action: "created",
      }),
    )
    setConnected(client, callTool)

    await client.writeNote(
      "Task Note",
      "hello",
      "tasks",
      undefined,
      undefined,
      {
        metadata: {
          status: "active",
          scheduling: { due: "2026-09-02" },
        },
        tags: "work,priority",
        noteType: "Task",
      },
    )

    expect(callTool).toHaveBeenCalledWith({
      name: "bm_write",
      arguments: {
        title: "Task Note",
        content: "hello",
        directory: "tasks",
        output_format: "json",
        project: DEFAULT_PROJECT,
        metadata: {
          status: "active",
          scheduling: { due: "2026-09-02" },
        },
        tags: "work,priority",
        note_type: "Task",
      },
    })
  })

  it("readNote routes by project_id alone and returns the checksum", async () => {
    const callTool = jest.fn().mockResolvedValue(
      mcpResult({
        title: "t",
        permalink: "p",
        content: "body",
        file_path: "notes/t.md",
        frontmatter: { type: "memory_master" },
        checksum: "abc123",
      }),
    )
    setConnected(client, callTool)

    const note = await client.readNote("t", {
      includeFrontmatter: true,
      projectId: "6b1c3f0e-0f5e-4d6b-9a39-2a4f8c1d7e55",
    })

    expect(callTool).toHaveBeenCalledWith({
      name: "bm_read",
      arguments: {
        identifier: "t",
        include_frontmatter: true,
        output_format: "json",
        project: "6b1c3f0e-0f5e-4d6b-9a39-2a4f8c1d7e55",
      },
    })
    expect(callTool.mock.calls[0][0].arguments).not.toHaveProperty("project_id")
    expect(note.checksum).toBe("abc123")
  })

  it("writeNote routes by project_id alone and passes expected_checksum", async () => {
    const callTool = jest.fn().mockResolvedValue(
      mcpResult({
        title: "Memory Master",
        permalink: "memory-quest/memory-master",
        file_path: "memory-quest/Memory Master.md",
        checksum: "new",
        action: "updated",
      }),
    )
    setConnected(client, callTool)

    await client.writeNote(
      "Memory Master",
      "body",
      "memory-quest",
      undefined,
      true,
      {
        noteType: "memory_master",
        metadata: {
          memory_quest_code: "MQ-1",
          memory_quest_guidance: "active",
        },
        projectId: "6b1c3f0e-0f5e-4d6b-9a39-2a4f8c1d7e55",
        expectedChecksum: "old",
      },
    )

    expect(callTool).toHaveBeenCalledWith({
      name: "bm_write",
      arguments: {
        title: "Memory Master",
        content: "body",
        directory: "memory-quest",
        output_format: "json",
        project: "6b1c3f0e-0f5e-4d6b-9a39-2a4f8c1d7e55",
        overwrite: true,
        note_type: "memory_master",
        metadata: {
          memory_quest_code: "MQ-1",
          memory_quest_guidance: "active",
        },
        expected_checksum: "old",
      },
    })
    expect(callTool.mock.calls[0][0].arguments).not.toHaveProperty("project_id")
  })

  it("writeNote maps a JSON ALREADY_EXISTS tool error to NoteAlreadyExistsError", async () => {
    const callTool = jest.fn().mockResolvedValue(
      mcpToolError(
        JSON.stringify({
          error: "ALREADY_EXISTS",
          message:
            "A note exists at memory-quest/Memory Master.md. Nothing was written.",
        }),
      ),
    )
    setConnected(client, callTool)

    const err = await client
      .writeNote("Memory Master", "body", "memory-quest", undefined, false)
      .catch((e: unknown) => e)

    expect(err).toBeInstanceOf(NoteAlreadyExistsError)
    expect((err as NoteAlreadyExistsError).message).toContain(
      "memory-quest/Memory Master.md",
    )
    expect((err as NoteAlreadyExistsError).permalink).toBe("")
  })

  it("writeNote maps REVISION_CONFLICT to NoteRevisionConflictError", async () => {
    const callTool = jest.fn().mockResolvedValue(
      mcpToolError(
        JSON.stringify({
          error: "REVISION_CONFLICT",
          message: "Changed since read; read again before retrying",
        }),
      ),
    )
    setConnected(client, callTool)

    const err = await client
      .writeNote("Memory Master", "body", "memory-quest", undefined, true, {
        expectedChecksum: "stale",
      })
      .catch((e: unknown) => e)

    expect(err).toBeInstanceOf(NoteRevisionConflictError)
    expect((err as NoteRevisionConflictError).currentChecksum).toBeNull()
  })

  it("writeNote rethrows tool errors that are not refusal payloads", async () => {
    const callTool = jest
      .fn()
      .mockResolvedValue(
        mcpToolError("expected_checksum requires overwrite=True"),
      )
    setConnected(client, callTool)

    const err = await client
      .writeNote("Note", "body", "notes", undefined, false, {
        expectedChecksum: "abc",
      })
      .catch((e: unknown) => e)

    expect(err).toBeInstanceOf(BmToolError)
    expect((err as Error).message).toBe(
      "BM MCP tool bm_write failed: expected_checksum requires overwrite=True",
    )
  })

  it("editNote calls bm_edit with MCP argument names", async () => {
    const callTool = jest.fn().mockResolvedValue(
      mcpResult({
        title: "t",
        permalink: "p",
        file_path: "notes/t.md",
        command: "find_replace",
        checksum: "abc",
      }),
    )
    setConnected(client, callTool)

    const result = await client.editNote("t", "find_replace", "new", {
      find_text: "old",
      expected_replacements: 2,
    })

    expect(callTool).toHaveBeenCalledWith({
      name: "bm_edit",
      arguments: {
        identifier: "t",
        command: "find_replace",
        content: "new",
        find_text: "old",
        section: undefined,
        expected_replacements: 2,
        output_format: "json",
        project: DEFAULT_PROJECT,
      },
    })
    expect(result.checksum).toBe("abc")
  })

  it("editNote passes metadata to bm_edit", async () => {
    const callTool = jest.fn().mockResolvedValue(
      mcpResult({
        title: "t",
        permalink: "p",
        file_path: "notes/t.md",
        command: "append",
      }),
    )
    setConnected(client, callTool)

    await client.editNote("t", "append", "new", {
      metadata: { status: "complete", review: { approved: true } },
    })

    expect(callTool).toHaveBeenCalledWith({
      name: "bm_edit",
      arguments: {
        identifier: "t",
        command: "append",
        content: "new",
        metadata: { status: "complete", review: { approved: true } },
        output_format: "json",
        project: DEFAULT_PROJECT,
      },
    })
  })

  it("search calls bm_search with paging params", async () => {
    const callTool = jest.fn().mockResolvedValue(
      mcpResult({
        results: [
          {
            title: "x",
            permalink: "x",
            content: "c",
            file_path: "notes/x.md",
            score: 0.9,
          },
        ],
      }),
    )
    setConnected(client, callTool)

    const results = await client.search("marketing strategy", 3)

    expect(callTool).toHaveBeenCalledWith({
      name: "bm_search",
      arguments: {
        query: "marketing strategy",
        page: 1,
        page_size: 3,
        output_format: "json",
        project: DEFAULT_PROJECT,
      },
    })
    expect(results).toHaveLength(1)
    expect(results[0].title).toBe("x")
  })

  it("search passes metadata_filters, tags, and status to bm_search", async () => {
    const callTool = jest.fn().mockResolvedValue(
      mcpResult({
        results: [
          {
            title: "Auth Design",
            permalink: "auth-design",
            content: "OAuth spec",
            file_path: "specs/auth-design.md",
            score: 0.85,
          },
        ],
      }),
    )
    setConnected(client, callTool)

    const results = await client.search("oauth", 5, "research", {
      filters: { type: "spec", confidence: { $gt: 0.7 } },
      tags: ["security"],
      status: "in-progress",
    })

    expect(callTool).toHaveBeenCalledWith({
      name: "bm_search",
      arguments: {
        query: "oauth",
        page: 1,
        page_size: 5,
        output_format: "json",
        project: "research",
        filters: {
          type: "spec",
          confidence: { $gt: 0.7 },
          tags: { $contains: ["security"] },
          status: "in-progress",
        },
      },
    })
    expect(results).toHaveLength(1)
    expect(results[0].title).toBe("Auth Design")
  })

  it("search preserves explicit filters over conflicting shortcuts", async () => {
    const callTool = jest.fn().mockResolvedValue(mcpResult({ results: [] }))
    setConnected(client, callTool)
    const filters = {
      status: { $in: ["draft", "review"] },
      tags: { $contains: ["explicit"] },
    }

    await client.search(undefined, 10, undefined, {
      filters,
      tags: ["shortcut"],
      status: "published",
    })

    expect(callTool.mock.calls[0][0].arguments.filters).toEqual(filters)
    expect(filters).toEqual({
      status: { $in: ["draft", "review"] },
      tags: { $contains: ["explicit"] },
    })
  })

  it("search omits metadata args when not provided", async () => {
    const callTool = jest.fn().mockResolvedValue(mcpResult({ results: [] }))
    setConnected(client, callTool)

    await client.search("test", 10)

    expect(callTool).toHaveBeenCalledWith({
      name: "bm_search",
      arguments: {
        query: "test",
        page: 1,
        page_size: 10,
        output_format: "json",
        project: DEFAULT_PROJECT,
      },
    })
  })

  it("buildContext calls bm_context using output_format=json", async () => {
    const callTool = jest.fn().mockResolvedValue(
      mcpResult({
        results: [
          {
            primary_result: {
              title: "x",
              permalink: "x",
              content: "body",
              file_path: "notes/x.md",
            },
            observations: [],
            related_results: [],
          },
        ],
      }),
    )
    setConnected(client, callTool)

    const ctx = await client.buildContext("memory://notes/x", 2)

    expect(callTool).toHaveBeenCalledWith({
      name: "bm_context",
      arguments: {
        identifier: "memory://notes/x",
        depth: 2,
        output_format: "json",
        project: DEFAULT_PROJECT,
      },
    })
    expect(ctx.results).toHaveLength(1)
  })

  it("recentActivity calls bm_project with JSON output", async () => {
    const callTool = jest.fn().mockResolvedValue(
      mcpResult({
        results: [
          {
            title: "x",
            permalink: "x",
            file_path: "notes/x.md",
            created_at: "2026-01-01T00:00:00Z",
          },
        ],
      }),
    )
    setConnected(client, callTool)

    const recent = await client.recentActivity("7d")

    expect(callTool).toHaveBeenCalledWith({
      name: "bm_project",
      arguments: {
        command: "recent",
        since: "7d",
        output_format: "json",
        project: DEFAULT_PROJECT,
      },
    })
    expect(recent).toHaveLength(1)
  })

  it("listProjects calls bm_project with JSON output", async () => {
    const callTool = jest.fn().mockResolvedValue(
      mcpResult({
        projects: [
          {
            name: "alpha",
            path: "/tmp/alpha",
            is_default: true,
          },
        ],
      }),
    )
    setConnected(client, callTool)

    const projects = await client.listProjects()

    expect(callTool).toHaveBeenCalledWith({
      name: "bm_project",
      arguments: {
        command: "list",
        output_format: "json",
      },
    })
    expect(projects[0].name).toBe("alpha")
  })

  it("listProjects filters workspace client-side without passing unsupported MCP args", async () => {
    const callTool = jest.fn().mockResolvedValue(
      mcpResult({
        projects: [
          {
            name: "alpha",
            path: "/tmp/alpha",
            workspace_name: "Team Alpha",
            workspace_slug: "team-alpha",
            workspace_tenant_id: "tenant-alpha",
          },
          {
            name: "beta",
            path: "/tmp/beta",
            workspace_name: "Team Beta",
            workspace_slug: "team-beta",
            workspace_tenant_id: "tenant-beta",
          },
        ],
      }),
    )
    setConnected(client, callTool)

    const projects = await client.listProjects("team-alpha")

    expect(callTool).toHaveBeenCalledWith({
      name: "bm_project",
      arguments: {
        command: "list",
        output_format: "json",
      },
    })
    expect(projects.map((project) => project.name)).toEqual(["alpha"])
  })

  it("listWorkspaces deduplicates project workspaces and excludes local projects", async () => {
    const callTool = jest.fn().mockResolvedValue(
      mcpResult({
        projects: [
          {
            name: "one",
            workspace_tenant_id: "team-id",
            workspace_name: "Team",
            workspace_type: "team",
          },
          {
            name: "two",
            workspace_tenant_id: "team-id",
            workspace_name: "Team",
            workspace_type: "team",
          },
          { name: "local", path: "/tmp/local" },
        ],
      }),
    )
    setConnected(client, callTool)
    expect(await client.listWorkspaces()).toEqual([
      { tenant_id: "team-id", name: "Team", workspace_type: "team" },
    ])
    expect(callTool).toHaveBeenCalledWith({
      name: "bm_project",
      arguments: { command: "list", output_format: "json" },
    })
  })

  it("ensureProject calls bm_project in idempotent JSON mode", async () => {
    const callTool = jest.fn().mockResolvedValue(
      mcpResult({
        name: "test-project",
        path: "/tmp/memory",
        created: false,
        already_exists: true,
      }),
    )
    setConnected(client, callTool)

    await client.ensureProject("/tmp/memory")

    expect(callTool).toHaveBeenCalledWith({
      name: "bm_project",
      arguments: {
        command: "create",
        name: "test-project",
        path: "/tmp/memory",
        set_default: true,
        output_format: "json",
      },
    })
  })

  it("deleteNote calls bm_delete with JSON output", async () => {
    const callTool = jest.fn().mockResolvedValue(
      mcpResult({
        action: "deleted",
        title: "old-note",
        permalink: "notes/old-note",
        file_path: "notes/old-note.md",
      }),
    )
    setConnected(client, callTool)

    const result = await client.deleteNote("notes/old-note")

    expect(callTool).toHaveBeenCalledWith({
      name: "bm_delete",
      arguments: {
        identifier: "notes/old-note",
        output_format: "json",
        project: DEFAULT_PROJECT,
      },
    })
    expect(result.file_path).toBe("notes/old-note.md")
  })

  it("schemaValidate calls bm_schema with JSON output", async () => {
    const callTool = jest.fn().mockResolvedValue(
      mcpResult({
        entity_type: "person",
        total_notes: 3,
        total_entities: 3,
        valid_count: 3,
        warning_count: 0,
        error_count: 0,
        results: [],
      }),
    )
    setConnected(client, callTool)

    const result = await client.schemaValidate("person")

    expect(callTool).toHaveBeenCalledWith({
      name: "bm_schema",
      arguments: {
        command: "validate",
        note_type: "person",
        output_format: "json",
        project: DEFAULT_PROJECT,
      },
    })
    expect(result.entity_type).toBe("person")
    expect(result.valid_count).toBe(3)
  })

  it("schemaValidate passes identifier when provided", async () => {
    const callTool = jest.fn().mockResolvedValue(
      mcpResult({
        entity_type: null,
        total_notes: 1,
        total_entities: 1,
        valid_count: 1,
        warning_count: 0,
        error_count: 0,
        results: [],
      }),
    )
    setConnected(client, callTool)

    await client.schemaValidate(undefined, "notes/my-note")

    expect(callTool).toHaveBeenCalledWith({
      name: "bm_schema",
      arguments: {
        command: "validate",
        identifier: "notes/my-note",
        output_format: "json",
        project: DEFAULT_PROJECT,
      },
    })
  })

  it("schemaInfer calls bm_schema with threshold", async () => {
    const callTool = jest.fn().mockResolvedValue(
      mcpResult({
        entity_type: "task",
        notes_analyzed: 10,
        field_frequencies: [],
        suggested_schema: {},
        suggested_required: [],
        suggested_optional: [],
        excluded: [],
      }),
    )
    setConnected(client, callTool)

    const result = await client.schemaInfer("task", 0.5)

    expect(callTool).toHaveBeenCalledWith({
      name: "bm_schema",
      arguments: {
        command: "infer",
        note_type: "task",
        threshold: 0.5,
        output_format: "json",
        project: DEFAULT_PROJECT,
      },
    })
    expect(result.notes_analyzed).toBe(10)
  })

  it("schemaDiff calls bm_schema with JSON output", async () => {
    const callTool = jest.fn().mockResolvedValue(
      mcpResult({
        entity_type: "person",
        schema_found: true,
        new_fields: [{ field: "phone", frequency: 0.6 }],
        dropped_fields: [],
        cardinality_changes: [],
      }),
    )
    setConnected(client, callTool)

    const result = await client.schemaDiff("person")

    expect(callTool).toHaveBeenCalledWith({
      name: "bm_schema",
      arguments: {
        command: "diff",
        note_type: "person",
        output_format: "json",
        project: DEFAULT_PROJECT,
      },
    })
    expect(result.new_fields).toHaveLength(1)
  })

  it("search with note_types and status filters (no query)", async () => {
    const callTool = jest.fn().mockResolvedValue(
      mcpResult({
        results: [
          {
            title: "Task 1",
            permalink: "tasks/task-1",
            content: "active task",
            file_path: "tasks/task-1.md",
            score: 0.9,
          },
        ],
      }),
    )
    setConnected(client, callTool)

    const result = await client.search(undefined, 10, undefined, {
      note_types: ["task"],
      status: "active",
    })

    expect(callTool).toHaveBeenCalledWith({
      name: "bm_search",
      arguments: {
        page: 1,
        page_size: 10,
        note_types: ["task"],
        filters: { status: "active" },
        output_format: "json",
        project: DEFAULT_PROJECT,
      },
    })
    expect(result).toHaveLength(1)
    expect(result[0].title).toBe("Task 1")
  })

  it("moveNote calls bm_move with destination_folder in a single MCP call", async () => {
    const callTool = jest.fn().mockResolvedValue(
      mcpResult({
        action: "moved",
        title: "My Note",
        permalink: "archive/my-note",
        file_path: "archive/my-note.md",
        source: "notes/my-note",
        destination: "archive/my-note.md",
      }),
    )
    setConnected(client, callTool)

    const result = await client.moveNote("notes/my-note", "archive")

    expect(callTool).toHaveBeenCalledTimes(1)
    expect(callTool).toHaveBeenCalledWith({
      name: "bm_move",
      arguments: {
        identifier: "notes/my-note",
        destination: "archive/",
        output_format: "json",
        project: DEFAULT_PROJECT,
      },
    })
    expect(result.title).toBe("My Note")
    expect(result.file_path).toBe("archive/my-note.md")
  })

  it("indexConversation surfaces non-not-found append errors without creating", async () => {
    ;(client as any).editNote = jest
      .fn()
      .mockRejectedValue(new Error("validation failed"))
    ;(client as any).writeNote = jest.fn()

    await expect(
      client.indexConversation(
        "user message long enough",
        "assistant reply long enough",
      ),
    ).rejects.toThrow("validation failed")

    expect((client as any).writeNote).not.toHaveBeenCalled()
  })

  it("indexConversation creates fallback note only on note-not-found errors", async () => {
    ;(client as any).editNote = jest
      .fn()
      .mockRejectedValue(
        new BmToolError(
          "bm_edit",
          JSON.stringify({ error: "NOT_FOUND", message: "Missing note" }),
        ),
      )
    ;(client as any).writeNote = jest.fn().mockResolvedValue({
      title: "conversations",
      permalink: "conversations",
      content: "x",
      file_path: "conversations/x.md",
    })

    await client.indexConversation(
      "user message long enough",
      "assistant reply long enough",
    )

    expect((client as any).writeNote).toHaveBeenCalledTimes(1)
    const args = (client as any).writeNote.mock.calls[0]
    expect(args[3]).toBeUndefined() // project
    expect(args[4]).toBeUndefined() // overwrite
  })

  it("retries recoverable MCP failures with bounded attempts", async () => {
    ;(client as any).retryDelaysMs = [0, 0, 0]

    const callTool = jest
      .fn()
      .mockRejectedValue(new Error("connection closed by peer"))

    ;(client as any).ensureConnected = jest.fn().mockResolvedValue({ callTool })
    ;(client as any).disconnectCurrent = jest.fn().mockResolvedValue(undefined)
    ;(client as any).client = { close: jest.fn().mockResolvedValue(undefined) }
    ;(client as any).transport = {
      close: jest.fn().mockResolvedValue(undefined),
    }

    await expect(
      (client as any).callToolRaw("bm_search", { query: "x" }),
    ).rejects.toThrow("BM MCP unavailable")

    expect((client as any).ensureConnected).toHaveBeenCalledTimes(4)
    expect((client as any).disconnectCurrent).toHaveBeenCalledTimes(4)
  })

  it("does not retry non-recoverable tool failures", async () => {
    ;(client as any).retryDelaysMs = [0, 0, 0]

    const callTool = jest.fn().mockRejectedValue(new Error("invalid params"))

    ;(client as any).ensureConnected = jest.fn().mockResolvedValue({ callTool })
    ;(client as any).disconnectCurrent = jest.fn().mockResolvedValue(undefined)

    await expect(
      (client as any).callToolRaw("bm_search", { query: "x" }),
    ).rejects.toThrow("invalid params")

    expect((client as any).ensureConnected).toHaveBeenCalledTimes(1)
    expect((client as any).disconnectCurrent).toHaveBeenCalledTimes(0)
  })
})
