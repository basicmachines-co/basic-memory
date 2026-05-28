import { beforeEach, describe, expect, it, jest } from "bun:test"
import { BmClient } from "./bm-client.ts"

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

  it("readNote calls read_note with JSON output and no frontmatter by default", async () => {
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
      name: "read_note",
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
      name: "read_note",
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

  it("writeNote calls write_note with JSON output", async () => {
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
      name: "write_note",
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
      name: "write_note",
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

  it("writeNote throws NoteAlreadyExistsError on conflict response", async () => {
    const callTool = jest.fn().mockResolvedValue(
      mcpResult({
        title: "Existing",
        permalink: "notes/existing",
        file_path: null,
        checksum: null,
        action: "conflict",
        error: "NOTE_ALREADY_EXISTS",
      }),
    )
    setConnected(client, callTool)

    await expect(
      client.writeNote("Existing", "content", "notes"),
    ).rejects.toThrow("Note already exists")
  })

  it("editNote calls edit_note with MCP argument names", async () => {
    const callTool = jest.fn().mockResolvedValue(
      mcpResult({
        title: "t",
        permalink: "p",
        file_path: "notes/t.md",
        operation: "find_replace",
        checksum: "abc",
      }),
    )
    setConnected(client, callTool)

    const result = await client.editNote("t", "find_replace", "new", {
      find_text: "old",
      expected_replacements: 2,
    })

    expect(callTool).toHaveBeenCalledWith({
      name: "edit_note",
      arguments: {
        identifier: "t",
        operation: "find_replace",
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

  it("search calls search_notes with paging params", async () => {
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
      name: "search_notes",
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

  it("search passes metadata_filters, tags, and status to search_notes", async () => {
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
      name: "search_notes",
      arguments: {
        query: "oauth",
        page: 1,
        page_size: 5,
        output_format: "json",
        project: "research",
        metadata_filters: { type: "spec", confidence: { $gt: 0.7 } },
        tags: ["security"],
        status: "in-progress",
      },
    })
    expect(results).toHaveLength(1)
    expect(results[0].title).toBe("Auth Design")
  })

  it("search omits metadata args when not provided", async () => {
    const callTool = jest.fn().mockResolvedValue(mcpResult({ results: [] }))
    setConnected(client, callTool)

    await client.search("test", 10)

    expect(callTool).toHaveBeenCalledWith({
      name: "search_notes",
      arguments: {
        query: "test",
        page: 1,
        page_size: 10,
        output_format: "json",
        project: DEFAULT_PROJECT,
      },
    })
  })

  it("buildContext calls build_context using output_format=json", async () => {
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
      name: "build_context",
      arguments: {
        url: "memory://notes/x",
        depth: 2,
        output_format: "json",
        project: DEFAULT_PROJECT,
      },
    })
    expect(ctx.results).toHaveLength(1)
  })

  it("recentActivity calls recent_activity with JSON output", async () => {
    const callTool = jest.fn().mockResolvedValue(
      mcpResult([
        {
          title: "x",
          permalink: "x",
          file_path: "notes/x.md",
          created_at: "2026-01-01T00:00:00Z",
        },
      ]),
    )
    setConnected(client, callTool)

    const recent = await client.recentActivity("7d")

    expect(callTool).toHaveBeenCalledWith({
      name: "recent_activity",
      arguments: {
        timeframe: "7d",
        output_format: "json",
        project: DEFAULT_PROJECT,
      },
    })
    expect(recent).toHaveLength(1)
  })

  it("listProjects calls list_memory_projects with JSON output", async () => {
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
      name: "list_memory_projects",
      arguments: {
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
      name: "list_memory_projects",
      arguments: {
        output_format: "json",
      },
    })
    expect(projects.map((project) => project.name)).toEqual(["alpha"])
  })

  it("ensureProject calls create_memory_project in idempotent JSON mode", async () => {
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
      name: "create_memory_project",
      arguments: {
        project_name: "test-project",
        project_path: "/tmp/memory",
        set_default: true,
        output_format: "json",
      },
    })
  })

  it("deleteNote calls delete_note with JSON output", async () => {
    const callTool = jest.fn().mockResolvedValue(
      mcpResult({
        deleted: true,
        title: "old-note",
        permalink: "notes/old-note",
        file_path: "notes/old-note.md",
      }),
    )
    setConnected(client, callTool)

    const result = await client.deleteNote("notes/old-note")

    expect(callTool).toHaveBeenCalledWith({
      name: "delete_note",
      arguments: {
        identifier: "notes/old-note",
        output_format: "json",
        project: DEFAULT_PROJECT,
      },
    })
    expect(result.file_path).toBe("notes/old-note.md")
  })

  it("schemaValidate calls schema_validate with JSON output", async () => {
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
      name: "schema_validate",
      arguments: {
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
      name: "schema_validate",
      arguments: {
        identifier: "notes/my-note",
        output_format: "json",
        project: DEFAULT_PROJECT,
      },
    })
  })

  it("schemaInfer calls schema_infer with threshold", async () => {
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
      name: "schema_infer",
      arguments: {
        note_type: "task",
        threshold: 0.5,
        output_format: "json",
        project: DEFAULT_PROJECT,
      },
    })
    expect(result.notes_analyzed).toBe(10)
  })

  it("schemaDiff calls schema_diff with JSON output", async () => {
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
      name: "schema_diff",
      arguments: {
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
      name: "search_notes",
      arguments: {
        page: 1,
        page_size: 10,
        note_types: ["task"],
        status: "active",
        output_format: "json",
        project: DEFAULT_PROJECT,
      },
    })
    expect(result).toHaveLength(1)
    expect(result[0].title).toBe("Task 1")
  })

  it("moveNote calls move_note with destination_folder in a single MCP call", async () => {
    const callTool = jest.fn().mockResolvedValue(
      mcpResult({
        moved: true,
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
      name: "move_note",
      arguments: {
        identifier: "notes/my-note",
        destination_folder: "archive",
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
      .mockRejectedValue(new Error("Entity not found"))
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
      (client as any).callToolRaw("search_notes", { query: "x" }),
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
      (client as any).callToolRaw("search_notes", { query: "x" }),
    ).rejects.toThrow("invalid params")

    expect((client as any).ensureConnected).toHaveBeenCalledTimes(1)
    expect((client as any).disconnectCurrent).toHaveBeenCalledTimes(0)
  })
})
