import { afterAll, beforeAll, describe, expect, it } from "bun:test"
import { execFile } from "node:child_process"
import { mkdir, mkdtemp, rm, stat } from "node:fs/promises"
import { homedir, tmpdir } from "node:os"
import { join, resolve } from "node:path"
import { setTimeout as delay } from "node:timers/promises"
import { promisify } from "node:util"
import type { OpenClawPluginApi } from "openclaw/plugin-sdk/plugin-entry"
import { BmClient } from "../bm-client.ts"
import { registerProjectListTool } from "../tools/list-memory-projects.ts"
import { registerReadTool } from "../tools/read-note.ts"
import { registerWriteTool } from "../tools/write-note.ts"

const RUN_INTEGRATION = process.env.BM_INTEGRATION === "1"
const BM_BIN = resolveCommand(process.env.BM_BIN ?? "./scripts/bm-local.sh")
const execFileAsync = promisify(execFile)

type Harness = {
  client: BmClient
  rootDir: string
  workspaceDir: string
  projectPath: string
  projectName: string
}

let harness: Harness | null = null

function getHarness(): Harness {
  if (!harness) {
    throw new Error("integration harness is not initialized")
  }
  return harness
}

function resolveCommand(command: string): string {
  if (command.startsWith("~/")) {
    return join(homedir(), command.slice(2))
  }
  if (command.includes("/")) {
    return resolve(command)
  }
  return command
}

function isMissingJsonOutputSupportError(err: unknown): boolean {
  const message =
    err instanceof Error ? err.message.toLowerCase() : String(err).toLowerCase()
  return (
    message.includes("output_format") &&
    message.includes("unexpected keyword argument")
  )
}

async function waitForSearchResult(
  client: BmClient,
  query: string,
  title: string,
): Promise<boolean> {
  const deadline = Date.now() + 10_000
  while (Date.now() < deadline) {
    const results = await client.search(query, 10)
    if (results.some((item) => item.title === title)) {
      return true
    }
    await delay(250)
  }
  return false
}

async function waitForFile(path: string, exists: boolean): Promise<void> {
  const deadline = Date.now() + 10_000
  while (Date.now() < deadline) {
    let present = true
    try {
      await stat(path)
    } catch (error) {
      if (
        !(error instanceof Error) ||
        !("code" in error) ||
        error.code !== "ENOENT"
      ) {
        throw error
      }
      present = false
    }
    if (present === exists) return
    await delay(100)
  }
  throw new Error(
    `File materialization did not converge: ${path}, exists=${exists}`,
  )
}

async function bootstrapProject(
  bmPath: string,
  projectName: string,
  projectPath: string,
  env: Record<string, string>,
): Promise<void> {
  try {
    await execFileAsync(
      bmPath,
      ["project", "add", projectName, projectPath, "--default"],
      {
        env,
        timeout: 30_000,
      },
    )
  } catch (err) {
    const msg =
      err instanceof Error
        ? err.message.toLowerCase()
        : String(err).toLowerCase()
    if (msg.includes("already exists")) {
      return
    }
    throw err
  }
}

async function assertJsonModeSupport(client: BmClient): Promise<void> {
  try {
    await client.listProjects()
  } catch (err) {
    if (isMissingJsonOutputSupportError(err)) {
      throw new Error(
        `BM binary "${BM_BIN}" does not support MCP JSON output mode. Set BASIC_MEMORY_REPO to a newer basic-memory checkout or point BM_BIN to an updated bm binary.`,
      )
    }
    throw err
  }
}

if (!RUN_INTEGRATION) {
  describe("BmClient integration", () => {
    it("skipped (set BM_INTEGRATION=1 to run real BM integration tests)", () => {
      expect(true).toBe(true)
    })
  })
} else {
  describe("BmClient integration", () => {
    beforeAll(async () => {
      const rootDir = await mkdtemp(join(tmpdir(), "openclaw-bm-int-"))
      const workspaceDir = join(rootDir, "workspace")
      const projectPath = join(workspaceDir, "memory")
      const homeDir = join(rootDir, "home")
      const projectName = `openclaw-int-${Date.now().toString(36)}`

      await mkdir(projectPath, { recursive: true })
      await mkdir(homeDir, { recursive: true })

      const childEnv: Record<string, string> = {}
      for (const [key, value] of Object.entries(process.env)) {
        if (typeof value === "string") {
          childEnv[key] = value
        }
      }
      childEnv.HOME = homeDir
      childEnv.BASIC_MEMORY_HOME = join(homeDir, ".basic-memory")
      // BM resolves its config from BASIC_MEMORY_CONFIG_DIR, then
      // XDG_CONFIG_HOME, before HOME; pin it so the run never registers the
      // temp project in the developer's real Basic Memory config.
      childEnv.BASIC_MEMORY_CONFIG_DIR = join(
        homeDir,
        ".config",
        "basic-memory",
      )
      childEnv.BASIC_MEMORY_CLOUD_MODE = "false"
      delete childEnv.BASIC_MEMORY_PROJECTS

      await bootstrapProject(BM_BIN, projectName, projectPath, childEnv)

      const client = new BmClient(BM_BIN, projectName)
      await client.start({ cwd: workspaceDir, env: childEnv })
      await assertJsonModeSupport(client)

      harness = {
        client,
        rootDir,
        workspaceDir,
        projectPath,
        projectName,
      }
    })

    afterAll(async () => {
      if (!harness) return

      await harness.client.stop()
      await rm(harness.rootDir, { recursive: true, force: true })
    })

    it("runs note lifecycle end-to-end against a real BM MCP session", async () => {
      const { client, projectPath } = getHarness()

      const created = await client.writeNote(
        "Integration Lifecycle Note",
        "# Integration Lifecycle Note\n\nInitial content.",
        "integration",
      )
      expect(created.title).toBe("Integration Lifecycle Note")
      expect(created.file_path).toContain("integration/")

      const createdPath = resolve(projectPath, created.file_path)
      await waitForFile(createdPath, true)

      const readBefore = await client.readNote(
        "integration/integration-lifecycle-note",
      )
      expect(readBefore.content).toContain("Initial content.")

      await client.editNote(
        "integration/integration-lifecycle-note",
        "append",
        "\n\nAppended from integration test.",
      )

      const readAfter = await client.readNote(
        "integration/integration-lifecycle-note",
      )
      expect(readAfter.content).toContain("Appended from integration test.")

      const foundBySearch = await waitForSearchResult(
        client,
        "Integration Lifecycle Note",
        "Integration Lifecycle Note",
      )
      expect(foundBySearch).toBe(true)

      const context = await client.buildContext("integration/*", 1)
      expect(context.results.length).toBeGreaterThan(0)

      const recent = await client.recentActivity("7d")
      expect(
        recent.some((item) => item.title === "Integration Lifecycle Note"),
      ).toBe(true)

      const moved = await client.moveNote(
        "integration/integration-lifecycle-note",
        "archive",
      )
      expect(moved.file_path.startsWith("archive/")).toBe(true)

      const movedPath = resolve(projectPath, moved.file_path)
      await waitForFile(movedPath, true)

      const movedIdentifier = moved.file_path.replace(/\.md$/i, "")
      await client.deleteNote(movedIdentifier)

      await waitForFile(movedPath, false)
    })

    it("round-trips Memory Quest fields through the OpenClaw tools", async () => {
      const { client, projectName } = getHarness()
      type ToolResult = {
        content: Array<{ type: "text"; text: string }>
        details: Record<string, any>
      }
      const tools = new Map<
        string,
        (id: string, params: Record<string, unknown>) => Promise<ToolResult>
      >()
      const api = {
        registerTool: (tool: { name: string; execute: any }) => {
          tools.set(tool.name, tool.execute)
        },
      } as unknown as OpenClawPluginApi
      registerProjectListTool(api, client)
      registerWriteTool(api, client)
      registerReadTool(api, client)
      const call = (name: string, params: Record<string, unknown>) => {
        const execute = tools.get(name)
        if (!execute) throw new Error(`tool ${name} was not registered`)
        return execute("integration", params)
      }

      // list_memory_projects keeps the routing fields the skill selects on.
      const listed = await call("list_memory_projects", {})
      const project = listed.details.projects.find(
        (item: { name: string }) => item.name === projectName,
      )
      expect(project.source).toBe("local")
      expect(project.external_id).toMatch(/^[0-9a-f-]{36}$/)
      expect(listed.content[0].text).toContain(`ID: ${project.external_id}`)
      expect(listed.content[0].text).toContain("Source: local")

      const writeArgs = {
        title: "Memory Master",
        content: "Memory Master body.",
        folder: "memory-quest",
        note_type: "memory_master",
        project_id: project.external_id,
        metadata: {
          memory_quest_code: "MQ-TEST-0000-0001",
          memory_quest_guidance: "active",
        },
        overwrite: false,
      }

      // Files materialize asynchronously; the accepted note (what read_note
      // returns) is the canonical state these assertions check.
      const readBack = () =>
        call("read_note", {
          identifier: "memory-quest/memory-master",
          project_id: project.external_id,
          include_frontmatter: true,
        })

      const created = await call("write_note", writeArgs)
      expect(created.details.error).toBeUndefined()

      // overwrite=false refuses a second write instead of replacing the note.
      const refused = await call("write_note", writeArgs)
      expect(refused.details.error).toBe("note_already_exists")
      expect(refused.content[0].text).toContain("Note already exists")

      // read_note by project_id surfaces the metadata as frontmatter and the
      // checksum that guards a replacement.
      const read = await readBack()
      expect(read.details.frontmatter).toMatchObject({
        type: "memory_master",
        memory_quest_code: "MQ-TEST-0000-0001",
        memory_quest_guidance: "active",
      })
      expect(read.content[0].text).toContain("type: memory_master")
      expect(read.content[0].text).toContain(
        "memory_quest_code: MQ-TEST-0000-0001",
      )
      const checksum = read.details.checksum
      expect(typeof checksum).toBe("string")
      expect(read.content[0].text).toContain(`Checksum: ${checksum}`)

      // A stale checksum is refused and nothing is replaced.
      const stale = await call("write_note", {
        ...writeArgs,
        content: "Stale replacement.",
        overwrite: true,
        expected_checksum: "0".repeat(64),
      })
      expect(stale.details.error).toBe("note_revision_conflict")
      expect(stale.details.current_checksum).toBeNull()
      expect((await readBack()).content[0].text).toContain(
        "Memory Master body.",
      )

      // The checksum that was read guards a successful replacement.
      const replaced = await call("write_note", {
        ...writeArgs,
        content: "Replaced body.",
        metadata: {
          memory_quest_code: "MQ-TEST-0000-0002",
          memory_quest_guidance: "active",
        },
        overwrite: true,
        expected_checksum: checksum,
      })
      expect(replaced.details.error).toBeUndefined()
      const after = await readBack()
      expect(after.content[0].text).toContain("Replaced body.")
      expect(after.details.frontmatter.memory_quest_code).toBe(
        "MQ-TEST-0000-0002",
      )
      expect(after.details.checksum).not.toBe(checksum)
    })

    it("lists the integration project through real BM MCP", async () => {
      const { client, projectName } = getHarness()
      const projects = await client.listProjects()
      expect(projects.some((project) => project.name === projectName)).toBe(
        true,
      )
    })
  })
}
