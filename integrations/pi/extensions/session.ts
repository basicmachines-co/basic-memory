export interface SessionTurn {
  role: "user" | "assistant";
  text: string;
}

export interface CaptureDraft {
  title: string;
  content: string;
  metadata: Record<string, unknown>;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function textFromContent(content: unknown): string {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  return content
    .map((block) => {
      if (!isRecord(block)) return "";
      if ((block.type === "text" || block.type === "input_text" || block.type === "output_text")
        && typeof block.text === "string") {
        return block.text;
      }
      return "";
    })
    .filter(Boolean)
    .join("\n");
}

function entryMessage(entry: unknown): Record<string, unknown> | undefined {
  if (!isRecord(entry)) return undefined;
  if (isRecord(entry.message)) return entry.message;
  if (typeof entry.role === "string") return entry;
  return undefined;
}

export function extractSessionTurns(entries: unknown[]): SessionTurn[] {
  const turns: SessionTurn[] = [];
  for (const entry of entries) {
    const message = entryMessage(entry);
    if (!message) continue;
    const role = message.role;
    if (role !== "user" && role !== "assistant") continue;
    const text = textFromContent(message.content).trim();
    if (text) turns.push({ role: role as "user" | "assistant", text });
  }
  return turns;
}

function clip(value: string, limit: number): string {
  const compact = value.replace(/\s+/g, " ").trim();
  return compact.length <= limit ? compact : `${compact.slice(0, Math.max(0, limit - 1)).trim()}…`;
}

function yamlScalar(value: unknown): string {
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(String(value));
}

function frontmatter(metadata: Record<string, unknown>): string {
  const lines = ["---"];
  for (const [key, value] of Object.entries(metadata)) {
    if (Array.isArray(value)) {
      lines.push(`${key}: [${value.map(yamlScalar).join(", ")}]`);
      continue;
    }
    lines.push(`${key}: ${yamlScalar(value)}`);
  }
  lines.push("---", "");
  return lines.join("\n");
}

export function buildCaptureDraft(params: {
  turns: SessionTurn[];
  cwd: string;
  sessionFile?: string;
  sessionId?: string;
  branchId?: string;
  model?: string;
  title?: string;
}): CaptureDraft | null {
  const userTurns = params.turns.filter((turn) => turn.role === "user");
  if (userTurns.length === 0) return null;

  const now = new Date();
  const openedWith = userTurns[0]?.text ?? "Pi session";
  const thread = params.turns;
  const stableThreadId = [
    params.sessionId ? clip(params.sessionId, 18) : undefined,
    params.branchId ? clip(params.branchId, 18) : undefined,
  ].filter(Boolean).join("/");
  const stableSessionTitle = stableThreadId
    ? `Pi session ${stableThreadId} — ${clip(openedWith, 48)}`
    : `Pi session ${now.toISOString().slice(0, 19).replace("T", " ")} — ${clip(openedWith, 48)}`;
  const title = params.title?.trim() || stableSessionTitle;

  const metadata: Record<string, unknown> = {
    title,
    type: "pi_session",
    tags: ["pi", "session", "checkpoint"],
    status: "open",
    started: now.toISOString(),
    ended: now.toISOString(),
    cwd: params.cwd,
    capture: "extractive",
    source: "pi",
  };
  if (params.sessionFile) metadata.pi_session_file = params.sessionFile;
  if (params.sessionId) metadata.pi_session_id = params.sessionId;
  if (params.branchId) metadata.pi_branch_id = params.branchId;
  if (params.model) metadata.model = params.model;

  const content = [
    frontmatter(metadata),
    `# ${title}`,
    "",
    "_Pi working-thread checkpoint. This is durable context for continuing later; the full transcript remains in Pi's session history._",
    "",
    "## Summary",
    `Working in \`${params.cwd}\`.`,
    `- Opening request: ${clip(openedWith, 300)}`,
    "",
    "## Thread to date",
    ...thread.map((turn) => `- **${turn.role}:** ${turn.text}`),
    "",
    "## Observations",
    `- [context] Pi session opened with: ${clip(openedWith, 200)}`,
    "- [next_step] Review this checkpoint and continue where the thread left off",
  ].join("\n");

  return { title, content, metadata };
}
