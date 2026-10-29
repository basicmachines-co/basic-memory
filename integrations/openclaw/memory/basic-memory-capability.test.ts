import { beforeEach, describe, expect, it, jest } from "bun:test"
import { type BmClient, BmToolError } from "../bm-client.ts"
import {
  buildBasicMemoryCapability,
  MEMORY_PROVIDER_ID,
  openBasicMemoryReader,
} from "./basic-memory-capability.ts"

type Context = Parameters<typeof openBasicMemoryReader>[1]

function makeContext(overrides?: Partial<Context>): Context {
  return {
    authority: { kind: "host", operation: "test" },
    assertCurrent: jest.fn(),
    ...overrides,
  } as Context
}

const notFound = () =>
  new BmToolError(
    "bm_read",
    JSON.stringify({ error: "NOT_FOUND", message: "Missing note" }),
  )

describe("Basic Memory memory capability", () => {
  let client: {
    search: jest.Mock
    readNote: jest.Mock
    listProjects: jest.Mock
    isServiceRunning: jest.Mock
    stop: jest.Mock
  }

  beforeEach(() => {
    client = {
      search: jest.fn().mockResolvedValue([
        {
          title: "Quillfeather",
          permalink: "pets/quillfeather",
          content: "My test cat is named Quillfeather.",
          file_path: "pets/quillfeather.md",
          score: 1.18,
        },
        {
          title: "Weak match",
          permalink: "misc/weak",
          content: "Barely related.",
          file_path: "misc/weak.md",
          score: 0.2,
        },
      ]),
      readNote: jest.fn().mockResolvedValue({
        title: "Quillfeather",
        permalink: "pets/quillfeather",
        content: "line 1\nline 2\nline 3\nline 4",
        file_path: "pets/quillfeather.md",
      }),
      listProjects: jest.fn().mockResolvedValue([]),
      isServiceRunning: jest.fn().mockReturnValue(true),
      stop: jest.fn().mockResolvedValue(undefined),
    }
  })

  const reader = (context = makeContext()) =>
    openBasicMemoryReader(client as unknown as BmClient, context)

  it("declares memory-only search without pagination or candidates", () => {
    expect(reader().capabilities).toEqual({
      sources: ["memory"],
      pagination: false,
      candidates: [],
      projectFilter: false,
    })
  })

  it("maps BM results to provider hits with capped scores", async () => {
    const page = await reader().search({ query: "test cat", maxResults: 3 })

    expect(client.search).toHaveBeenCalledWith("test cat", 3)
    expect(page.hits[0]).toEqual({
      reference: { providerId: MEMORY_PROVIDER_ID, id: "pets/quillfeather" },
      excerpt: "My test cat is named Quillfeather.",
      source: "pets/quillfeather.md",
      score: 1,
    })
  })

  it("enforces minScore and drops unscored hits when a floor is set", async () => {
    client.search.mockResolvedValue([
      ...(await client.search()),
      {
        title: "Unscored",
        permalink: "misc/unscored",
        content: "x",
        file_path: "misc/unscored.md",
      },
    ])

    const page = await reader().search({ query: "test cat", minScore: 0.5 })

    expect(page.hits.map((hit) => hit.reference.id)).toEqual([
      "pets/quillfeather",
    ])
  })

  it("reads a note by reference and applies the host line window", async () => {
    const result = await reader().get({
      reference: { providerId: MEMORY_PROVIDER_ID, id: "pets/quillfeather" },
      from: 2,
      lines: 2,
    })

    expect(client.readNote).toHaveBeenCalledWith("pets/quillfeather")
    expect(result).toEqual({
      status: "ok",
      reference: { providerId: MEMORY_PROVIDER_ID, id: "pets/quillfeather" },
      text: "line 2\nline 3",
      from: 2,
      lines: 2,
      truncated: true,
      nextFrom: 4,
    })
  })

  it("returns the whole note when no line window is requested", async () => {
    const result = await reader().get({
      reference: { providerId: MEMORY_PROVIDER_ID, id: "pets/quillfeather" },
    })

    expect(result).toEqual({
      status: "ok",
      reference: { providerId: MEMORY_PROVIDER_ID, id: "pets/quillfeather" },
      text: "line 1\nline 2\nline 3\nline 4",
    })
  })

  it("reports a missing note as not_found but surfaces other failures", async () => {
    client.readNote.mockRejectedValueOnce(notFound())
    await expect(
      reader().get({ reference: { providerId: MEMORY_PROVIDER_ID, id: "x" } }),
    ).resolves.toEqual({ status: "not_found" })

    client.readNote.mockRejectedValueOnce(new Error("BM MCP unavailable"))
    await expect(
      reader().get({ reference: { providerId: MEMORY_PROVIDER_ID, id: "x" } }),
    ).rejects.toThrow("BM MCP unavailable")
  })

  it("refuses a caller whose authority was revoked", async () => {
    const context = makeContext({
      assertCurrent: jest.fn(() => {
        throw new Error("caller no longer current")
      }),
    })

    await expect(reader(context).search({ query: "cat" })).rejects.toThrow(
      "caller no longer current",
    )
    expect(client.search).not.toHaveBeenCalled()
  })

  it("reports health from the BM session without reading notes", async () => {
    await expect(reader().health()).resolves.toEqual({ status: "ready" })
    expect(client.search).not.toHaveBeenCalled()
    expect(client.readNote).not.toHaveBeenCalled()

    client.listProjects.mockRejectedValue(new Error("connection closed"))
    await expect(reader().health()).resolves.toEqual({
      status: "unavailable",
      message: "Basic Memory is not reachable",
    })
  })

  it("leaves the shared gateway session open when a reader closes", async () => {
    await reader().close()

    expect(client.stop).not.toHaveBeenCalled()
  })

  // Regression: `openclaw memory status` loads the plugin without starting its
  // service, and the lazily spawned `bm mcp` child kept the CLI from exiting.
  it("stops a connection it opened outside the plugin service", async () => {
    client.isServiceRunning.mockReturnValue(false)
    const handle = reader()

    await expect(handle.health()).resolves.toEqual({ status: "ready" })
    await handle.close()

    expect(client.stop).toHaveBeenCalledTimes(1)
  })

  it("registers recall tools, a tools-arm flush, and Basic Memory prompt guidance", async () => {
    const capability = buildBasicMemoryCapability(client as unknown as BmClient)

    expect(capability.recallToolNames).toEqual([
      "search_notes",
      "read_note",
      "build_context",
    ])

    const plan = capability.providerFlushPlanResolver?.({})
    expect(plan).toMatchObject({
      persistenceToolNames: ["write_note", "edit_note"],
      lookupToolNames: ["search_notes", "read_note"],
    })
    expect(plan && "relativePath" in plan).toBe(false)

    expect(
      capability.promptBuilder?.({ availableTools: new Set(["search_notes"]) }),
    ).toContain("## Basic Memory")
    expect(
      capability.promptBuilder?.({
        availableTools: new Set(["memory_search"]),
      }),
    ).toEqual([])

    const opened = await capability.providerRuntime?.open({
      cfg: {},
      agentId: "main",
      context: makeContext(),
    } as Parameters<NonNullable<typeof capability.providerRuntime>["open"]>[0])
    expect(opened?.provider?.capabilities.sources).toEqual(["memory"])
  })
})
