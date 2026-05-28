import { afterAll, beforeAll, describe, expect, it } from "bun:test"
import { execFile } from "node:child_process"
import { mkdir, mkdtemp, rm, stat } from "node:fs/promises"
import { homedir, tmpdir } from "node:os"
import { join, resolve } from "node:path"
import { setTimeout as delay } from "node:timers/promises"
import { promisify } from "node:util"
import { BmClient } from "../bm-client.ts"

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
      await stat(createdPath)

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
      await stat(movedPath)

      const movedIdentifier = moved.file_path.replace(/\.md$/i, "")
      await client.deleteNote(movedIdentifier)

      await expect(stat(movedPath)).rejects.toBeDefined()
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
