import { createHash } from "node:crypto"
import { setTimeout as delay } from "node:timers/promises"
import { Client } from "@modelcontextprotocol/sdk/client"
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js"
import { log } from "./logger.ts"

const DEFAULT_RETRY_DELAYS_MS = [500, 1000, 2000]

// Thrown for calls to the plugin's project after BM refused to create it.
// Deliberately not a BmToolError: note methods read a BmToolError's refusal
// payload as their own, and would report this ALREADY_EXISTS as an existing
// note.
export class ProjectUnavailableError extends Error {
  readonly project: string
  readonly reason: string
  constructor(project: string, reason: string) {
    super(`Basic Memory project "${project}" is unavailable: ${reason}`)
    this.name = "ProjectUnavailableError"
    this.project = project
    this.reason = reason
  }
}

export class NoteAlreadyExistsError extends Error {
  readonly permalink: string
  constructor(title: string, permalink: string, message?: string) {
    super(message ?? `Note already exists: "${title}" (${permalink})`)
    this.name = "NoteAlreadyExistsError"
    this.permalink = permalink
  }
}

/**
 * bm_write refused a conditional overwrite: the note is no longer the
 * revision named by expected_checksum. Nothing was written; currentChecksum is
 * available revision when supplied by a server. A null value means unknown;
 * 1.0 puts revision details in the refusal message, not a checksum field.
 */
export class NoteRevisionConflictError extends Error {
  readonly permalink: string | null
  readonly currentChecksum: string | null
  constructor(
    title: string,
    permalink: string | null,
    currentChecksum: string | null,
    message?: string,
  ) {
    super(
      message ?? `Note revision conflict: "${title}" changed since it was read`,
    )
    this.name = "NoteRevisionConflictError"
    this.permalink = permalink
    this.currentChecksum = currentChecksum
  }
}

/** A BM MCP tool returned isError; toolMessage is the tool's own error text. */
export class BmToolError extends Error {
  readonly toolName: string
  readonly toolMessage: string
  constructor(toolName: string, toolMessage: string) {
    super(
      `BM MCP tool ${toolName} failed${toolMessage ? `: ${toolMessage}` : ""}`,
    )
    this.name = "BmToolError"
    this.toolName = toolName
    this.toolMessage = toolMessage
  }
}

const REQUIRED_TOOLS = [
  "bm_search",
  "bm_read",
  "bm_write",
  "bm_edit",
  "bm_context",
  "bm_project",
  "bm_delete",
  "bm_move",
  "bm_schema",
]

export interface SearchResult {
  title: string
  permalink: string
  content: string
  score?: number
  file_path: string
  // The chunk of the note that matched, when the server ranked by chunk.
  matched_chunk?: string | null
}

/**
 * The text to preview for a search hit. `content` is the start of the note,
 * so for a long note (a day of captured conversation) the matching part can
 * sit far below any preview cut-off; the matched chunk is the part that hit.
 */
export function searchResultText(result: SearchResult): string {
  return result.matched_chunk?.trim() || result.content || ""
}

export interface NoteResult {
  title: string
  permalink: string
  content: string
  file_path: string
  frontmatter?: Record<string, unknown> | null
  checksum?: string | null
  action?: "created" | "updated"
}

export interface EditNoteResult {
  title: string
  permalink: string
  file_path: string
  operation: EditOperation
  checksum?: string | null
}

interface ReadNoteOptions {
  includeFrontmatter?: boolean
  /** Project external_id; routes to that exact project instead of by name. */
  projectId?: string
}

interface WriteNoteOptions {
  metadata?: Record<string, unknown>
  tags?: string[] | string
  noteType?: string
  /** Project external_id; routes to that exact project instead of by name. */
  projectId?: string
  /** Checksum of the revision read; overwrite only while it is still current. */
  expectedChecksum?: string
}

// bm_edit commands the plugin exposes. set_metadata is left out: edit_note
// already merges `metadata` alongside any other operation.
export type EditOperation =
  | "append"
  | "prepend"
  | "find_replace"
  | "replace_section"
  | "insert_before_section"
  | "insert_after_section"

interface EditNoteOptions {
  find_text?: string
  section?: string
  expected_replacements?: number
  metadata?: Record<string, unknown>
}

export interface ContextResult {
  results: Array<{
    primary_result: NoteResult
    observations: Array<{
      category: string
      content: string
    }>
    related_results: Array<{
      type: "relation" | "entity"
      title?: string
      permalink: string
      relation_type?: string
      from_entity?: string
      // Null for a forward reference whose target note does not exist yet;
      // to_name then carries the link text.
      to_entity?: string | null
      to_name?: string | null
    }>
  }>
}

export interface RecentResult {
  title: string
  permalink: string
  file_path: string
  created_at: string
}

export interface ProjectListResult {
  name: string
  path: string
  // Set only on the row attached to a local project; null on cloud-only rows.
  local_path?: string | null
  external_id?: string | null
  source?: string | null
  qualified_name?: string | null
  display_name?: string | null
  is_private?: boolean
  is_default?: boolean
  isDefault?: boolean
  workspace_name?: string | null
  workspace_slug?: string | null
  workspace_type?: string | null
  workspace_tenant_id?: string | null
}

export interface WorkspaceResult {
  tenant_id: string
  name: string
  workspace_type: string | null
}

export interface SchemaValidationResult {
  entity_type: string | null
  total_notes: number
  total_entities: number
  valid_count: number
  warning_count: number
  error_count: number
  results: Array<{
    identifier: string
    valid: boolean
    warnings: string[]
    errors: string[]
  }>
}

export interface SchemaInferResult {
  entity_type: string
  notes_analyzed: number
  field_frequencies: Array<{
    name: string
    percentage: number
    count: number
    total: number
    source: string
    sample_values?: string[]
    is_array?: boolean
    target_type?: string | null
  }>
  suggested_schema: Record<string, unknown>
  suggested_required: string[]
  suggested_optional: string[]
  excluded: string[]
}

export interface SchemaDiffResult {
  entity_type: string
  schema_found: boolean
  new_fields: Array<{
    name: string
    source: string
    count: number
    total: number
    percentage: number
  }>
  dropped_fields: Array<{ name: string; source: string; declared_in?: string }>
  cardinality_changes: string[]
}

function getErrorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err)
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value)
}

function extractTextFromContent(content: unknown): string {
  if (!Array.isArray(content)) return ""

  const textBlocks = content
    .filter(
      (block): block is { type: "text"; text: string } =>
        isRecord(block) &&
        block.type === "text" &&
        typeof block.text === "string",
    )
    .map((block) => block.text)

  return textBlocks.join("\n").trim()
}

function isRecoverableConnectionError(err: unknown): boolean {
  const msg = getErrorMessage(err).toLowerCase()
  return (
    msg.includes("connection closed") ||
    msg.includes("not connected") ||
    msg.includes("transport") ||
    msg.includes("broken pipe") ||
    msg.includes("econnreset") ||
    msg.includes("epipe") ||
    msg.includes("failed to start bm mcp stdio") ||
    msg.includes("client is closed")
  )
}

export function isNoteNotFoundError(err: unknown): boolean {
  return (
    err instanceof BmToolError &&
    parseRefusalPayload(err.toolMessage)?.error === "NOT_FOUND"
  )
}

export type ConversationCaptureStatus = "committed" | "duplicate"

function conversationDate(date: Date): string {
  return date.toISOString().split("T")[0]
}

// Host advancement keys are opaque and may contain characters that would end
// an HTML comment, so the note stores a fixed-length digest instead.
function conversationTurnMarker(turnKey: string): string {
  const digest = createHash("sha256").update(turnKey).digest("hex").slice(0, 32)
  return `<!-- openclaw-turn:${digest} -->`
}

function asString(value: unknown): string | null {
  return typeof value === "string" ? value : null
}

// In JSON mode BM reports a refused write as a tool error whose text is the
// refusal payload itself ({"error": "ALREADY_EXISTS", ...}). Any other
// error text (plain-text failures, older servers) is not a refusal payload.
function parseRefusalPayload(text: string): Record<string, unknown> | null {
  let parsed: unknown
  try {
    parsed = JSON.parse(text)
  } catch {
    return null
  }
  return isRecord(parsed) && typeof parsed.error === "string" ? parsed : null
}

/**
 * The server's own explanation of a failed tool call, for showing to an agent.
 * A refusal payload becomes "CODE: message"; other tool text passes through.
 */
export function describeToolError(err: unknown): string {
  if (!(err instanceof BmToolError)) return getErrorMessage(err)
  const payload = parseRefusalPayload(err.toolMessage)
  if (!payload) return err.toolMessage || err.message
  const message = asString(payload.message)
  return message ? `${payload.error}: ${message}` : String(payload.error)
}

/** describeToolError, but saying plainly when the note does not exist. */
export function describeNoteError(err: unknown): string {
  const reason = describeToolError(err)
  return isNoteNotFoundError(err) ? `it does not exist. ${reason}` : reason
}

function projectMatchesWorkspace(
  project: ProjectListResult,
  workspace: string,
): boolean {
  const requested = workspace.trim().toLowerCase()
  if (!requested) return true

  return [
    project.workspace_name,
    project.workspace_slug,
    project.workspace_tenant_id,
  ].some(
    (value) => typeof value === "string" && value.toLowerCase() === requested,
  )
}

function parseProjectList(payload: unknown): ProjectListResult[] {
  if (isRecord(payload) && Array.isArray(payload.projects)) {
    return payload.projects as ProjectListResult[]
  }
  throw new Error("invalid bm_project response")
}

export class BmClient {
  private bmPath: string
  private project: string
  private cwd?: string
  private env?: Record<string, string>
  private shouldRun = false
  // True only between start() and stop(), i.e. while the plugin service owns
  // the BM MCP session. A lazy connection opened by a tool or memory call in a
  // one-shot CLI process leaves this false.
  private serviceRunning = false
  // Connect plus project creation, started by start(). Tool calls wait for
  // it so none can reach a project that does not exist yet. Null when the
  // client was never started (tests, one-shot CLI readers).
  private startup: Promise<void> | null = null
  private startupRetry: Promise<void> | null = null
  private projectPath?: string
  private adoptExistingProject = false
  // BM's refusal to create the plugin's project, kept so calls to that
  // project report it while listing and other projects still work.
  private projectSetupError: ProjectUnavailableError | null = null
  private prepare?: () => Promise<void>

  private client: Client | null = null
  private transport: StdioClientTransport | null = null
  private connectPromise: Promise<void> | null = null
  private retryDelaysMs = [...DEFAULT_RETRY_DELAYS_MS]
  // Conversation captures run one at a time so a turn-key duplicate check
  // and the append that follows it cannot interleave with another capture.
  private conversationWrites: Promise<unknown> = Promise.resolve()

  constructor(bmPath: string, project: string) {
    this.bmPath = bmPath
    this.project = project
  }

  /**
   * Connect to BM and create the plugin's project.
   *
   * Returns the startup promise; callers that must not block (the plugin
   * service, which OpenClaw gives only a few seconds) can leave it running.
   * Tool calls made meanwhile wait for it. If it fails, the next tool call
   * runs it again, so a transient failure does not leave the plugin dead.
   */
  start(options?: {
    cwd?: string
    env?: Record<string, string>
    projectPath?: string
    // True when projectPath is only the default: an existing project of the
    // plugin's name is then used at its stored folder.
    adoptExistingProject?: boolean
    // Runs before each connect attempt, e.g. installing the bm CLI.
    prepare?: () => Promise<void>
  }): Promise<void> {
    this.shouldRun = true
    this.serviceRunning = true
    if (options?.cwd) {
      this.cwd = options.cwd
    }
    if (options?.env) {
      this.env = options.env
    }
    if (options?.projectPath) {
      this.projectPath = options.projectPath
    }
    if (options?.adoptExistingProject !== undefined) {
      this.adoptExistingProject = options.adoptExistingProject
    }
    if (options?.prepare) {
      this.prepare = options.prepare
    }

    this.startup = this.runStartup()
    return this.startup
  }

  private async runStartup(): Promise<void> {
    await this.prepare?.()
    await this.connectWithRetries()
    if (!this.projectPath) return

    this.projectSetupError = null
    const projectPath = await this.startupProjectPath(this.projectPath)
    try {
      await this.createProject(projectPath)
    } catch (err) {
      // Trigger: BM refused the project, e.g. a configured projectPath that
      //   differs from where a project of this name is stored (#1846).
      // Why: a refusal is deterministic, so retrying startup cannot fix it,
      //   and failing startup would also fail project listing, the call a
      //   user needs to see the stored path. Other failures still fail
      //   startup so the next tool call retries them.
      // Outcome: calls to the plugin's project throw this refusal; listing
      //   and calls naming another project still work.
      if (
        !(err instanceof BmToolError) ||
        parseRefusalPayload(err.toolMessage) === null
      ) {
        throw err
      }
      this.projectSetupError = new ProjectUnavailableError(
        this.project,
        describeToolError(err),
      )
      log.error(this.projectSetupError.message)
    }
  }

  // The folder startup creates the project at. Since #1846, BM refuses a
  // create whose path differs from the stored one instead of reusing the
  // project by name.
  private async startupProjectPath(configured: string): Promise<string> {
    // Trigger: projectPath was not configured.
    // Why: its default has changed between plugin releases ("." and
    //   ~/.basic-memory/openclaw/), and users create projects by hand, so an
    //   existing project may sit somewhere other than the default.
    // Outcome: create at the stored folder, which BM treats as idempotent.
    if (!this.adoptExistingProject) return configured

    // With cloud credentials the listing holds one row per workspace for a
    // name, and only the row attached to the local project carries
    // local_path; a cloud-only row's path is tenant-relative. BM compares a
    // local create against the local project, so only local_path qualifies.
    const name = this.project.toLowerCase()
    const localPath = parseProjectList(
      await this.callStructured("bm_project", {
        command: "list",
        output_format: "json",
      }),
    ).find(
      (project) => project.name.toLowerCase() === name && project.local_path,
    )?.local_path
    if (!localPath) return configured

    if (localPath !== configured) {
      log.info(
        `using existing project "${this.project}" at ${localPath}; set projectPath to use another folder`,
      )
    }
    return localPath
  }

  // The refusal from the last project setup, or null when the project is
  // usable. Status checks report it, since listing projects still succeeds.
  projectUnavailableReason(): string | null {
    return this.projectSetupError?.reason ?? null
  }

  private async waitForStartup(): Promise<void> {
    if (!this.startup) return
    try {
      await this.startup
    } catch (err) {
      // Trigger: the startup this call was waiting on failed.
      // Why: the service no longer fails on a slow or failed start, so the
      //   first tool call afterwards is the place to recover.
      // Outcome: one fresh attempt, shared by concurrent callers; its error
      //   reaches the tool if it fails too.
      if (!this.serviceRunning) throw err
      if (this.startupRetry === null) {
        this.startupRetry = this.runStartup().finally(() => {
          this.startupRetry = null
        })
        this.startup = this.startupRetry
      }
      await this.startupRetry
    }
  }

  isServiceRunning(): boolean {
    return this.serviceRunning
  }

  async stop(): Promise<void> {
    this.shouldRun = false
    this.serviceRunning = false
    // Let an in-flight startup settle first, so it cannot reconnect after
    // the disconnect below.
    await this.startup?.catch(() => undefined)
    this.startup = null
    await this.disconnectCurrent(this.client, this.transport)
    this.client = null
    this.transport = null
  }

  private async connectWithRetries(): Promise<void> {
    let lastErr: unknown

    for (let attempt = 0; attempt <= this.retryDelaysMs.length; attempt++) {
      try {
        await this.ensureConnected()
        return
      } catch (err) {
        lastErr = err
        await this.disconnectCurrent(this.client, this.transport)
        this.client = null
        this.transport = null

        if (attempt === this.retryDelaysMs.length) {
          break
        }

        const waitMs = this.retryDelaysMs[attempt]
        log.warn(
          `BM MCP connect failed (attempt ${attempt + 1}/${this.retryDelaysMs.length + 1}): ${getErrorMessage(err)}; retrying in ${waitMs}ms`,
        )
        await delay(waitMs)
      }
    }

    throw new Error(`BM MCP unavailable: ${getErrorMessage(lastErr)}`)
  }

  private async ensureConnected(): Promise<Client> {
    if (!this.shouldRun) {
      this.shouldRun = true
    }

    if (this.client && this.transport) {
      return this.client
    }

    if (!this.connectPromise) {
      this.connectPromise = this.connectFresh()
    }

    try {
      await this.connectPromise
    } finally {
      this.connectPromise = null
    }

    if (!this.client) {
      throw new Error("BM MCP client was not initialized")
    }

    return this.client
  }

  private async connectFresh(): Promise<void> {
    const transport = new StdioClientTransport({
      command: this.bmPath,
      args: ["mcp", "--transport", "stdio"],
      cwd: this.cwd,
      env: this.env,
      stderr: "pipe",
    })

    const client = new Client(
      {
        name: "openclaw-basic-memory",
        version: "0.1.0",
      },
      { capabilities: {} },
    )

    const stderr = transport.stderr
    if (stderr) {
      stderr.on("data", (data: Buffer) => {
        const msg = data.toString().trim()
        if (msg.length > 0) {
          log.debug(`[bm mcp] ${msg}`)
        }
      })
    }

    transport.onclose = () => {
      if (this.transport !== transport) return
      log.warn("BM MCP stdio session closed")
      this.client = null
      this.transport = null
    }

    transport.onerror = (err: unknown) => {
      if (this.transport !== transport) return
      log.warn(`BM MCP transport error: ${getErrorMessage(err)}`)
    }

    this.client = client
    this.transport = transport

    try {
      await client.connect(transport)
      const tools = await client.listTools()
      this.assertRequiredTools(tools.tools.map((tool) => tool.name))

      log.info(
        `connected to BM MCP stdio (project=${this.project}, pid=${transport.pid ?? "unknown"})`,
      )
    } catch (err) {
      await this.disconnectCurrent(client, transport)
      if (this.client === client) {
        this.client = null
      }
      if (this.transport === transport) {
        this.transport = null
      }

      throw new Error(`failed to start BM MCP stdio: ${getErrorMessage(err)}`)
    }
  }

  private assertRequiredTools(toolNames: string[]): void {
    const available = new Set(toolNames)
    const missing = REQUIRED_TOOLS.filter((name) => !available.has(name))
    if (missing.length > 0) {
      throw new Error(
        `BM MCP server missing required tools: ${missing.join(", ")}`,
      )
    }
  }

  private async disconnectCurrent(
    client: Client | null,
    transport: StdioClientTransport | null,
  ): Promise<void> {
    if (client) {
      try {
        await client.close()
      } catch {
        // ignore shutdown errors
      }
    }

    if (transport) {
      try {
        await transport.close()
      } catch {
        // ignore shutdown errors
      }
    }
  }

  private async callToolRaw(
    name: string,
    args: Record<string, unknown>,
  ): Promise<unknown> {
    let lastErr: unknown

    for (let attempt = 0; attempt <= this.retryDelaysMs.length; attempt++) {
      try {
        const client = await this.ensureConnected()
        const result = await client.callTool({
          name,
          arguments: args,
        })

        if (isRecord(result) && result.isError === true) {
          throw new BmToolError(name, extractTextFromContent(result.content))
        }

        return result
      } catch (err) {
        if (!isRecoverableConnectionError(err)) {
          throw err
        }

        lastErr = err
        await this.disconnectCurrent(this.client, this.transport)
        this.client = null
        this.transport = null

        if (attempt === this.retryDelaysMs.length) {
          break
        }

        const waitMs = this.retryDelaysMs[attempt]
        log.warn(
          `BM MCP call ${name} failed (attempt ${attempt + 1}/${this.retryDelaysMs.length + 1}): ${getErrorMessage(err)}; retrying in ${waitMs}ms`,
        )
        await delay(waitMs)
      }
    }

    throw new Error(`BM MCP unavailable: ${getErrorMessage(lastErr)}`)
  }

  private async callTool(
    name: string,
    args: Record<string, unknown>,
  ): Promise<unknown> {
    await this.waitForStartup()
    // Trigger: BM refused the plugin's project at startup.
    // Why: the refusal concerns that project only; a call that names another
    //   project by name or id must still reach BM.
    // Outcome: only calls routed to the plugin's project report the refusal.
    if (this.projectSetupError && this.targetsPluginProject(args)) {
      throw this.projectSetupError
    }
    return this.callStructured(name, args)
  }

  // Ungated: startup itself calls this to create the project.
  private async callStructured(
    name: string,
    args: Record<string, unknown>,
  ): Promise<unknown> {
    const result = await this.callToolRaw(name, args)

    if (!isRecord(result) || result.structuredContent === undefined) {
      throw new Error(`BM MCP tool ${name} returned no structured payload`)
    }

    const structuredPayload = result.structuredContent
    if (isRecord(structuredPayload) && structuredPayload.result !== undefined) {
      return structuredPayload.result
    }

    return structuredPayload
  }

  // Project names match case-insensitively in BM, so "Test-Project" routes
  // to the plugin's "test-project" too.
  private targetsPluginProject(args: Record<string, unknown>): boolean {
    return (
      typeof args.project === "string" &&
      args.project.toLowerCase() === this.project.toLowerCase()
    )
  }

  private routedProject(project?: string): string {
    return project ?? this.project
  }

  // The 1.0 project parameter accepts either a name or an exact project UUID.
  private routing(project?: string, projectId?: string): { project: string } {
    if (projectId !== undefined) return { project: projectId }
    return { project: this.routedProject(project) }
  }

  async ensureProject(projectPath: string): Promise<void> {
    await this.waitForStartup()
    await this.createProject(projectPath)
  }

  private async createProject(projectPath: string): Promise<void> {
    const payload = await this.callStructured("bm_project", {
      command: "create",
      name: this.project,
      path: projectPath,
      set_default: true,
      output_format: "json",
    })

    if (!isRecord(payload)) {
      throw new Error("invalid bm_project response")
    }
  }

  async listWorkspaces(): Promise<WorkspaceResult[]> {
    const projects = await this.listProjects()
    const workspaces = new Map<string, WorkspaceResult>()
    for (const project of projects) {
      if (project.workspace_tenant_id && project.workspace_name) {
        workspaces.set(project.workspace_tenant_id, {
          tenant_id: project.workspace_tenant_id,
          name: project.workspace_name,
          workspace_type: project.workspace_type ?? null,
        })
      }
    }
    return [...workspaces.values()]
  }

  async listProjects(workspace?: string): Promise<ProjectListResult[]> {
    // Waits for startup but not for the plugin's project: when BM refused
    // it, the listing is how the user finds where it is actually stored.
    await this.waitForStartup()
    const projects = parseProjectList(
      await this.callStructured("bm_project", {
        command: "list",
        output_format: "json",
      }),
    )
    if (workspace) {
      return projects.filter((project) =>
        projectMatchesWorkspace(project, workspace),
      )
    }
    return projects
  }

  async search(
    query?: string,
    limit = 10,
    project?: string,
    metadata?: {
      filters?: Record<string, unknown>
      tags?: string[]
      status?: string
      note_types?: string[]
      entity_types?: string[]
    },
  ): Promise<SearchResult[]> {
    const args: Record<string, unknown> = {
      page: 1,
      page_size: limit,
      output_format: "json",
      project: this.routedProject(project),
    }
    if (query) args.query = query
    const filters = { ...metadata?.filters }
    // Explicit predicates take precedence over convenience shortcuts.
    if (metadata?.tags?.length && !("tags" in filters))
      filters.tags = { $contains: metadata.tags }
    if (metadata?.status && !("status" in filters))
      filters.status = metadata.status
    if (Object.keys(filters).length) args.filters = filters
    if (metadata?.note_types) args.note_types = metadata.note_types
    if (metadata?.entity_types)
      args.items = metadata.entity_types.map((item) =>
        item === "entity" ? "note" : item,
      )

    const payload = await this.callTool("bm_search", args)

    if (!isRecord(payload) || !Array.isArray(payload.results)) {
      throw new Error("invalid bm_search response")
    }

    return payload.results as SearchResult[]
  }

  async readNote(
    identifier: string,
    options: ReadNoteOptions = {},
    project?: string,
  ): Promise<NoteResult> {
    const args: Record<string, unknown> = {
      identifier,
      include_frontmatter: options.includeFrontmatter === true,
      output_format: "json",
      ...this.routing(project, options.projectId),
    }

    const payload = await this.callTool("bm_read", args)

    if (!isRecord(payload)) {
      throw new Error("invalid bm_read response")
    }

    const title = asString(payload.title)
    const permalink = asString(payload.permalink)
    const content = asString(payload.content)
    const filePath = asString(payload.file_path)

    if (!title || !permalink || content === null || !filePath) {
      throw new Error("invalid bm_read payload")
    }

    return {
      title,
      permalink,
      content,
      file_path: filePath,
      frontmatter: isRecord(payload.frontmatter) ? payload.frontmatter : null,
      checksum: asString(payload.checksum),
    }
  }

  async writeNote(
    title: string,
    content: string,
    folder: string,
    project?: string,
    overwrite?: boolean,
    options: WriteNoteOptions = {},
  ): Promise<NoteResult> {
    const args: Record<string, unknown> = {
      title,
      content,
      directory: folder,
      output_format: "json",
      ...this.routing(project, options.projectId),
    }
    if (overwrite !== undefined) args.overwrite = overwrite
    if (options.metadata !== undefined) args.metadata = options.metadata
    if (options.tags !== undefined) args.tags = options.tags
    if (options.noteType !== undefined) args.note_type = options.noteType
    if (options.expectedChecksum !== undefined)
      args.expected_checksum = options.expectedChecksum

    let payload: unknown
    try {
      payload = await this.callTool("bm_write", args)
    } catch (err) {
      const refusal =
        err instanceof BmToolError ? parseRefusalPayload(err.toolMessage) : null
      if (refusal?.error === "ALREADY_EXISTS") {
        throw new NoteAlreadyExistsError(
          asString(refusal.title) ?? title,
          asString(refusal.permalink) ?? "",
          asString(refusal.message) ?? undefined,
        )
      }
      if (refusal?.error === "REVISION_CONFLICT") {
        throw new NoteRevisionConflictError(
          asString(refusal.title) ?? title,
          asString(refusal.permalink),
          asString(refusal.currentChecksum),
          asString(refusal.message) ?? undefined,
        )
      }
      throw err
    }

    if (!isRecord(payload)) {
      throw new Error("invalid bm_write response")
    }

    const resultTitle = asString(payload.title)
    const permalink = asString(payload.permalink)
    const filePath = asString(payload.file_path)

    if (!resultTitle || !permalink || !filePath) {
      throw new Error("invalid bm_write payload")
    }

    return {
      title: resultTitle,
      permalink,
      content,
      file_path: filePath,
      checksum: asString(payload.checksum),
      action:
        payload.action === "created" || payload.action === "updated"
          ? payload.action
          : undefined,
    }
  }

  async buildContext(
    url: string,
    depth = 1,
    project?: string,
  ): Promise<ContextResult> {
    const args: Record<string, unknown> = {
      identifier: url,
      depth,
      output_format: "json",
      project: this.routedProject(project),
    }

    const payload = await this.callTool("bm_context", args)

    if (!isRecord(payload) || !Array.isArray(payload.results)) {
      throw new Error("invalid bm_context response")
    }

    return payload as unknown as ContextResult
  }

  async recentActivity(
    timeframe = "24h",
    project?: string,
  ): Promise<RecentResult[]> {
    const args: Record<string, unknown> = {
      command: "recent",
      since: timeframe,
      output_format: "json",
      project: this.routedProject(project),
    }

    const payload = await this.callTool("bm_project", args)

    if (isRecord(payload) && Array.isArray(payload.results)) {
      return payload.results as RecentResult[]
    }

    throw new Error("invalid bm_project response")
  }

  async editNote(
    identifier: string,
    operation: EditOperation,
    content: string,
    options: EditNoteOptions = {},
    project?: string,
  ): Promise<EditNoteResult> {
    const args: Record<string, unknown> = {
      identifier,
      command: operation,
      content,
      output_format: "json",
      project: this.routedProject(project),
    }
    if (options.find_text) args.find_text = options.find_text
    if (options.section) args.section = options.section
    if (options.expected_replacements != null)
      args.expected_replacements = options.expected_replacements
    if (options.metadata !== undefined) args.metadata = options.metadata

    const payload = await this.callTool("bm_edit", args)

    if (!isRecord(payload)) {
      throw new Error("invalid bm_edit response")
    }

    const title = asString(payload.title)
    const permalink = asString(payload.permalink)
    const filePath = asString(payload.file_path)

    if (!title || !permalink || !filePath) {
      throw new Error("invalid bm_edit payload")
    }

    return {
      title,
      permalink,
      file_path: filePath,
      operation,
      checksum: asString(payload.checksum),
    }
  }

  async deleteNote(
    identifier: string,
    project?: string,
  ): Promise<{ title: string; permalink: string; file_path: string }> {
    const args: Record<string, unknown> = {
      identifier,
      output_format: "json",
      project: this.routedProject(project),
    }

    const payload = await this.callTool("bm_delete", args)

    if (!isRecord(payload)) {
      throw new Error("invalid bm_delete response")
    }

    if (payload.action !== "deleted") {
      throw new Error(`bm_delete did not delete "${identifier}"`)
    }

    return {
      title: asString(payload.title) ?? identifier,
      permalink: asString(payload.permalink) ?? identifier,
      file_path: asString(payload.file_path) ?? identifier,
    }
  }

  async moveNote(
    identifier: string,
    newFolder: string,
    project?: string,
  ): Promise<NoteResult> {
    const args: Record<string, unknown> = {
      identifier,
      destination: `${newFolder.replace(/\/+$/, "")}/`,
      output_format: "json",
      project: this.routedProject(project),
    }

    const payload = await this.callTool("bm_move", args)

    if (!isRecord(payload)) {
      throw new Error("invalid bm_move response")
    }

    if (payload.action !== "moved") {
      throw new Error(
        asString(payload.error) ??
          `bm_move did not move "${identifier}" to "${newFolder}"`,
      )
    }

    return {
      title: asString(payload.title) ?? identifier,
      permalink: asString(payload.permalink) ?? identifier,
      content: "",
      file_path: asString(payload.file_path) ?? "",
    }
  }

  async schemaValidate(
    noteType?: string,
    identifier?: string,
    project?: string,
  ): Promise<SchemaValidationResult> {
    const args: Record<string, unknown> = {
      command: "validate",
      output_format: "json",
      project: this.routedProject(project),
    }
    if (noteType) args.note_type = noteType
    if (identifier) args.identifier = identifier

    const payload = await this.callTool("bm_schema", args)

    if (!isRecord(payload)) {
      throw new Error("invalid bm_schema response")
    }

    return payload as unknown as SchemaValidationResult
  }

  async schemaInfer(
    noteType: string,
    threshold = 0.25,
    project?: string,
  ): Promise<SchemaInferResult> {
    const args: Record<string, unknown> = {
      command: "infer",
      note_type: noteType,
      threshold,
      output_format: "json",
      project: this.routedProject(project),
    }

    const payload = await this.callTool("bm_schema", args)

    if (!isRecord(payload)) {
      throw new Error("invalid bm_schema response")
    }

    return payload as unknown as SchemaInferResult
  }

  async schemaDiff(
    noteType: string,
    project?: string,
  ): Promise<SchemaDiffResult> {
    const args: Record<string, unknown> = {
      command: "diff",
      note_type: noteType,
      output_format: "json",
      project: this.routedProject(project),
    }

    const payload = await this.callTool("bm_schema", args)

    if (!isRecord(payload)) {
      throw new Error("invalid bm_schema response")
    }

    return payload as unknown as SchemaDiffResult
  }

  /**
   * Append one user/assistant exchange to today's conversation note.
   *
   * With a turnKey the capture is idempotent: the entry carries a marker
   * derived from the key, and a key already present in today's or
   * yesterday's note is reported as a duplicate instead of written again.
   * Yesterday is checked because a host retry can cross midnight.
   */
  async indexConversation(
    userMessage: string,
    assistantResponse: string,
    options: { turnKey?: string } = {},
  ): Promise<ConversationCaptureStatus> {
    const write = this.conversationWrites.then(() =>
      this.writeConversationEntry(
        userMessage,
        assistantResponse,
        options.turnKey,
      ),
    )
    this.conversationWrites = write.catch(() => undefined)
    return write
  }

  private async writeConversationEntry(
    userMessage: string,
    assistantResponse: string,
    turnKey: string | undefined,
  ): Promise<ConversationCaptureStatus> {
    const now = new Date()
    const dateStr = conversationDate(now)
    const timeStr = now.toTimeString().slice(0, 5)
    const title = `conversations-${dateStr}`
    const marker = turnKey ? conversationTurnMarker(turnKey) : null

    // Trigger: the host supplied an advancement key for this turn.
    // Why: hosts retry commits after a crash, and a second append would
    //   duplicate the exchange in the note.
    // Outcome: an already-recorded key short-circuits before any write.
    if (marker) {
      const yesterday = new Date(now.getTime() - 24 * 60 * 60 * 1000)
      for (const candidate of [
        title,
        `conversations-${conversationDate(yesterday)}`,
      ]) {
        if (await this.noteContains(candidate, marker)) {
          log.debug(`conversation turn already recorded in: ${candidate}`)
          return "duplicate"
        }
      }
    }

    const entry = [
      `### ${timeStr}`,
      ...(marker ? [marker] : []),
      "",
      "**User:**",
      userMessage,
      "",
      "**Assistant:**",
      assistantResponse,
      "",
      "---",
    ].join("\n")

    try {
      await this.editNote(title, "append", entry)
      log.debug(`appended conversation to: ${title}`)
      return "committed"
    } catch (err) {
      if (!isNoteNotFoundError(err)) {
        log.error(`conversation append failed: ${getErrorMessage(err)}`, err)
        throw err
      }

      log.debug(
        `conversation note missing, will create: ${getErrorMessage(err)}`,
      )
    }

    // Create the note with frontmatter and first entry
    const content = [
      "---",
      `title: Conversations ${dateStr}`,
      "type: Conversation",
      `date: "${dateStr}"`,
      "---",
      "",
      `# Conversations ${dateStr}`,
      "",
      entry,
    ].join("\n")

    await this.writeNote(title, content, "conversations")
    log.debug(`created conversation note: ${title}`)
    return "committed"
  }

  private async noteContains(
    identifier: string,
    text: string,
  ): Promise<boolean> {
    try {
      const note = await this.readNote(identifier)
      return note.content.includes(text)
    } catch (err) {
      if (isNoteNotFoundError(err)) return false
      throw err
    }
  }

  getProject(): string {
    return this.project
  }
}
