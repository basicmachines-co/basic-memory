import { homedir } from "node:os";
import { isAbsolute, join, resolve } from "node:path";

export type BasicMemoryTransport = "cli" | "mcp";

export interface BasicMemoryPiConfig {
  transport: BasicMemoryTransport;
  bmPath: string;
  project?: string;
  projectId?: string;
  captureFolder: string;
  recallTimeframe: string;
  autoRecall: boolean;
  autoCapture: boolean;
  captureMinChars: number;
  mcpServerName: string;
  debug: boolean;
}

const DEFAULT_CONFIG: BasicMemoryPiConfig = {
  transport: "cli",
  bmPath: "bm",
  captureFolder: "pi/sessions",
  recallTimeframe: "7d",
  autoRecall: false,
  autoCapture: false,
  captureMinChars: 80,
  mcpServerName: "basic-memory",
  debug: false,
};

const ALLOWED_KEYS = new Set([
  "transport",
  "bmPath",
  "bm_path",
  "project",
  "projectId",
  "project_id",
  "captureFolder",
  "capture_folder",
  "recallTimeframe",
  "recall_timeframe",
  "autoRecall",
  "auto_recall",
  "autoCapture",
  "auto_capture",
  "captureMinChars",
  "capture_min_chars",
  "mcpServerName",
  "mcp_server_name",
  "debug",
]);

function expandUserPath(path: string): string {
  if (path === "~") return homedir();
  if (path.startsWith("~/")) return join(homedir(), path.slice(2));
  return path;
}

export function resolveConfigPath(cwd: string, explicitPath?: string): string {
  const candidate = explicitPath?.trim() || ".pi/basic-memory.json";
  const expanded = expandUserPath(candidate);
  return isAbsolute(expanded) ? expanded : resolve(cwd, expanded);
}

function configValue(data: Record<string, unknown>, primary: string, alias?: string): unknown {
  if (primary in data) return data[primary];
  return alias && alias in data ? data[alias] : undefined;
}

function requiredStringValue(
  data: Record<string, unknown>,
  primary: string,
  fallback: string,
  alias?: string,
): string {
  const value = configValue(data, primary, alias);
  if (value === undefined) return fallback;
  if (typeof value === "string" && value.trim().length > 0) return value.trim();
  throw new Error(`basic-memory Pi config ${primary} must be a non-empty string`);
}

function optionalStringConfigValue(
  data: Record<string, unknown>,
  primary: string,
  alias?: string,
): string | undefined {
  const value = configValue(data, primary, alias);
  if (value === undefined) return undefined;
  if (typeof value === "string" && value.trim().length > 0) return value.trim();
  throw new Error(`basic-memory Pi config ${primary} must be a non-empty string`);
}

function booleanConfigValue(
  data: Record<string, unknown>,
  primary: string,
  fallback: boolean,
  alias?: string,
): boolean {
  const value = configValue(data, primary, alias);
  if (value === undefined) return fallback;
  if (typeof value === "boolean") return value;
  throw new Error(`basic-memory Pi config ${primary} must be a boolean`);
}

function numberConfigValue(
  data: Record<string, unknown>,
  primary: string,
  fallback: number,
  alias?: string,
): number {
  const value = configValue(data, primary, alias);
  if (value === undefined) return fallback;
  if (typeof value === "number" && Number.isFinite(value) && value >= 0) return value;
  throw new Error(`basic-memory Pi config ${primary} must be a non-negative number`);
}

export function parseConfig(raw: unknown = {}): BasicMemoryPiConfig {
  if (raw === null || typeof raw !== "object" || Array.isArray(raw)) {
    throw new Error("basic-memory Pi config must be a JSON object");
  }
  const data = raw as Record<string, unknown>;

  const unknown = Object.keys(data).filter((key) => !ALLOWED_KEYS.has(key));
  if (unknown.length > 0) {
    throw new Error(`basic-memory Pi config has unknown keys: ${unknown.join(", ")}`);
  }

  const rawTransport = data.transport ?? DEFAULT_CONFIG.transport;
  if (rawTransport !== "cli" && rawTransport !== "mcp") {
    throw new Error('basic-memory Pi config transport must be "cli" or "mcp"');
  }

  return {
    transport: rawTransport,
    bmPath: requiredStringValue(data, "bmPath", DEFAULT_CONFIG.bmPath, "bm_path"),
    project: optionalStringConfigValue(data, "project"),
    projectId: optionalStringConfigValue(data, "projectId", "project_id"),
    captureFolder: requiredStringValue(
      data,
      "captureFolder",
      DEFAULT_CONFIG.captureFolder,
      "capture_folder",
    ),
    recallTimeframe: requiredStringValue(
      data,
      "recallTimeframe",
      DEFAULT_CONFIG.recallTimeframe,
      "recall_timeframe",
    ),
    autoRecall: booleanConfigValue(data, "autoRecall", DEFAULT_CONFIG.autoRecall, "auto_recall"),
    autoCapture: booleanConfigValue(
      data,
      "autoCapture",
      DEFAULT_CONFIG.autoCapture,
      "auto_capture",
    ),
    captureMinChars: numberConfigValue(
      data,
      "captureMinChars",
      DEFAULT_CONFIG.captureMinChars,
      "capture_min_chars",
    ),
    mcpServerName: requiredStringValue(
      data,
      "mcpServerName",
      DEFAULT_CONFIG.mcpServerName,
      "mcp_server_name",
    ),
    debug: booleanConfigValue(data, "debug", DEFAULT_CONFIG.debug),
  };
}
