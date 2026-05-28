import { setTimeout as delay } from "node:timers/promises"
import { Client } from "@modelcontextprotocol/sdk/client"
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js"
import { log } from "./logger.ts"

const DEFAULT_RETRY_DELAYS_MS = [500, 1000, 2000]

export class NoteAlreadyExistsError extends Error {
  readonly permalink: string
  constructor(title: string, permalink: string) {
    super(`Note already exists: "${title}" (${permalink})`)
    this.name = "NoteAlreadyExistsError"
    this.permalink = permalink
  }
}

const REQUIRED_TOOLS = [
  "search_notes",
  "read_note",
  "write_note",
  "edit_note",
  "build_context",
  "recent_activity",
  "list_memory_projects",
  "list_workspaces",
  "create_memory_project",
  "delete_note",
  "move_note",
  "schema_validate",
  "schema_infer",
  "schema_diff",
]

export interface SearchResult {
  title: string
  permalink: string
  content: string
  score?: number
  file_path: string
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
  operation: "append" | "prepend" | "find_replace" | "replace_section"
  checksum?: string | null
}

interface ReadNoteOptions {
  includeFrontmatter?: boolean
}

interface EditNoteOptions {
  find_text?: string
  section?: string
  expected_replacements?: number
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
      to_entity?: string
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
  workspace_type: string
  role: string
  organization_id?: string | null
  has_active_subscription: boolean
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

function isNoteNotFoundError(err: unknown): boolean {
  const msg = getErrorMessage(err).toLowerCase()
  return (
    msg.includes("entity not found") ||
    msg.includes("note not found") ||
    msg.includes("resource not found") ||
    msg.includes("could not find note matching") ||
    msg.includes("404")
  )
}

function asString(value: unknown): string | null {
  return typeof value === "string" ? value : null
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

export class BmClient {
  private bmPath: string
  private project: string
  private cwd?: string
  private env?: Record<string, string>
  private shouldRun = false

  private client: Client | null = null
  private transport: StdioClientTransport | null = null
  private connectPromise: Promise<void> | null = null
  private retryDelaysMs = [...DEFAULT_RETRY_DELAYS_MS]

  constructor(bmPath: string, project: string) {
    this.bmPath = bmPath
    this.project = project
  }

  async start(options?: {
    cwd?: string
    env?: Record<string, string>
  }): Promise<void> {
    this.shouldRun = true
    if (options?.cwd) {
      this.cwd = options.cwd
    }
    if (options?.env) {
      this.env = options.env
    }

    await this.connectWithRetries()
  }

  async stop(): Promise<void> {
    this.shouldRun = false
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
          const message = extractTextFromContent(result.content)
          throw new Error(
            `BM MCP tool ${name} failed${message ? `: ${message}` : ""}`,
          )
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

  private routedProject(project?: string): string {
    return project ?? this.project
  }

  async ensureProject(projectPath: string): Promise<void> {
    const payload = await this.callTool("create_memory_project", {
      project_name: this.project,
      project_path: projectPath,
      set_default: true,
      output_format: "json",
    })

    if (!isRecord(payload)) {
      throw new Error("invalid create_memory_project response")
    }
  }

  async listWorkspaces(): Promise<WorkspaceResult[]> {
    const payload = await this.callTool("list_workspaces", {
      output_format: "json",
    })

    if (isRecord(payload) && Array.isArray(payload.workspaces)) {
      return payload.workspaces as WorkspaceResult[]
    }

    throw new Error("invalid list_workspaces response")
  }

  async listProjects(workspace?: string): Promise<ProjectListResult[]> {
    const payload = await this.callTool("list_memory_projects", {
      output_format: "json",
    })

    if (isRecord(payload) && Array.isArray(payload.projects)) {
      const projects = payload.projects as ProjectListResult[]
      if (workspace) {
        return projects.filter((project) =>
          projectMatchesWorkspace(project, workspace),
        )
      }
      return projects
    }

    throw new Error("invalid list_memory_projects response")
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
    if (metadata?.filters) args.metadata_filters = metadata.filters
    if (metadata?.tags) args.tags = metadata.tags
    if (metadata?.status) args.status = metadata.status
    if (metadata?.note_types) args.note_types = metadata.note_types
    if (metadata?.entity_types) args.entity_types = metadata.entity_types

    const payload = await this.callTool("search_notes", args)

    if (!isRecord(payload) || !Array.isArray(payload.results)) {
      throw new Error("invalid search_notes response")
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
      project: this.routedProject(project),
    }

    const payload = await this.callTool("read_note", args)

    if (!isRecord(payload)) {
      throw new Error("invalid read_note response")
    }

    const title = asString(payload.title)
    const permalink = asString(payload.permalink)
    const content = asString(payload.content)
    const filePath = asString(payload.file_path)

    if (!title || !permalink || content === null || !filePath) {
      throw new Error("invalid read_note payload")
    }

    return {
      title,
      permalink,
      content,
      file_path: filePath,
      frontmatter: isRecord(payload.frontmatter) ? payload.frontmatter : null,
    }
  }

  async writeNote(
    title: string,
    content: string,
    folder: string,
    project?: string,
    overwrite?: boolean,
  ): Promise<NoteResult> {
    const args: Record<string, unknown> = {
      title,
      content,
      directory: folder,
      output_format: "json",
      project: this.routedProject(project),
    }
    if (overwrite !== undefined) args.overwrite = overwrite

    const payload = await this.callTool("write_note", args)

    if (!isRecord(payload)) {
      throw new Error("invalid write_note response")
    }

    if (payload.error === "NOTE_ALREADY_EXISTS") {
      throw new NoteAlreadyExistsError(
        asString(payload.title) ?? title,
        asString(payload.permalink) ?? "",
      )
    }

    const resultTitle = asString(payload.title)
    const permalink = asString(payload.permalink)
    const filePath = asString(payload.file_path)

    if (!resultTitle || !permalink || !filePath) {
      throw new Error("invalid write_note payload")
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
      url,
      depth,
      output_format: "json",
      project: this.routedProject(project),
    }

    const payload = await this.callTool("build_context", args)

    if (!isRecord(payload) || !Array.isArray(payload.results)) {
      throw new Error("invalid build_context response")
    }

    return payload as unknown as ContextResult
  }

  async recentActivity(
    timeframe = "24h",
    project?: string,
  ): Promise<RecentResult[]> {
    const args: Record<string, unknown> = {
      timeframe,
      output_format: "json",
      project: this.routedProject(project),
    }

    const payload = await this.callTool("recent_activity", args)

    if (Array.isArray(payload)) {
      return payload as RecentResult[]
    }

    throw new Error("invalid recent_activity response")
  }

  async editNote(
    identifier: string,
    operation: "append" | "prepend" | "find_replace" | "replace_section",
    content: string,
    options: EditNoteOptions = {},
    project?: string,
  ): Promise<EditNoteResult> {
    const args: Record<string, unknown> = {
      identifier,
      operation,
      content,
      output_format: "json",
      project: this.routedProject(project),
    }
    if (options.find_text) args.find_text = options.find_text
    if (options.section) args.section = options.section
    if (options.expected_replacements != null)
      args.expected_replacements = options.expected_replacements

    const payload = await this.callTool("edit_note", args)

    if (!isRecord(payload)) {
      throw new Error("invalid edit_note response")
    }

    const title = asString(payload.title)
    const permalink = asString(payload.permalink)
    const filePath = asString(payload.file_path)

    if (!title || !permalink || !filePath) {
      throw new Error("invalid edit_note payload")
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

    const payload = await this.callTool("delete_note", args)

    if (!isRecord(payload)) {
      throw new Error("invalid delete_note response")
    }

    if (payload.deleted !== true) {
      throw new Error(`delete_note did not delete "${identifier}"`)
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
      destination_folder: newFolder,
      output_format: "json",
      project: this.routedProject(project),
    }

    const payload = await this.callTool("move_note", args)

    if (!isRecord(payload)) {
      throw new Error("invalid move_note response")
    }

    if (payload.moved !== true) {
      throw new Error(
        asString(payload.error) ??
          `move_note did not move "${identifier}" to "${newFolder}"`,
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
      output_format: "json",
      project: this.routedProject(project),
    }
    if (noteType) args.note_type = noteType
    if (identifier) args.identifier = identifier

    const payload = await this.callTool("schema_validate", args)

    if (!isRecord(payload)) {
      throw new Error("invalid schema_validate response")
    }

    return payload as unknown as SchemaValidationResult
  }

  async schemaInfer(
    noteType: string,
    threshold = 0.25,
    project?: string,
  ): Promise<SchemaInferResult> {
    const args: Record<string, unknown> = {
      note_type: noteType,
      threshold,
      output_format: "json",
      project: this.routedProject(project),
    }

    const payload = await this.callTool("schema_infer", args)

    if (!isRecord(payload)) {
      throw new Error("invalid schema_infer response")
    }

    return payload as unknown as SchemaInferResult
  }

  async schemaDiff(
    noteType: string,
    project?: string,
  ): Promise<SchemaDiffResult> {
    const args: Record<string, unknown> = {
      note_type: noteType,
      output_format: "json",
      project: this.routedProject(project),
    }

    const payload = await this.callTool("schema_diff", args)

    if (!isRecord(payload)) {
      throw new Error("invalid schema_diff response")
    }

    return payload as unknown as SchemaDiffResult
  }

  async indexConversation(
    userMessage: string,
    assistantResponse: string,
  ): Promise<void> {
    const now = new Date()
    const dateStr = now.toISOString().split("T")[0]
    const timeStr = now.toTimeString().slice(0, 5)
    const title = `conversations-${dateStr}`

    const entry = [
      `### ${timeStr}`,
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
      return
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

    try {
      await this.writeNote(title, content, "conversations")
      log.debug(`created conversation note: ${title}`)
    } catch (err) {
      log.error("conversation index failed", err)
    }
  }

  getProject(): string {
    return this.project
  }
}
