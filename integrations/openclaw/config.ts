import { homedir, hostname } from "node:os"
import { isAbsolute, resolve } from "node:path"

export type BasicMemoryConfig = {
  project: string
  bmPath: string
  memoryDir: string
  memoryFile: string
  projectPath: string
  autoCapture: boolean
  captureMinChars: number
  autoRecall: boolean
  recallPrompt: string
  debug: boolean
}

const ALLOWED_KEYS = [
  "project",
  "bmPath",
  "memoryDir",
  "memory_dir",
  "memoryFile",
  "memory_file",
  "projectPath",
  "autoCapture",
  "captureMinChars",
  "capture_min_chars",
  "autoRecall",
  "auto_recall",
  "recallPrompt",
  "recall_prompt",
  "debug",
]

function assertAllowedKeys(
  value: Record<string, unknown>,
  allowed: string[],
  label: string,
): void {
  const unknown = Object.keys(value).filter((k) => !allowed.includes(k))
  if (unknown.length > 0) {
    throw new Error(`${label} has unknown keys: ${unknown.join(", ")}`)
  }
}

function defaultProject(): string {
  return `openclaw-${hostname()
    .replace(/[^a-zA-Z0-9-]/g, "-")
    .toLowerCase()}`
}

function expandUserPath(path: string): string {
  if (path === "~") return homedir()
  if (path.startsWith("~/")) return `${homedir()}/${path.slice(2)}`
  return path
}

export function resolveProjectPath(
  projectPath: string,
  workspaceDir: string,
): string {
  const expanded = expandUserPath(projectPath)
  if (isAbsolute(expanded)) return expanded
  return resolve(workspaceDir, expanded)
}

export function parseConfig(raw: unknown): BasicMemoryConfig {
  const cfg =
    raw && typeof raw === "object" && !Array.isArray(raw)
      ? (raw as Record<string, unknown>)
      : {}

  if (Object.keys(cfg).length > 0) {
    assertAllowedKeys(cfg, ALLOWED_KEYS, "basic-memory config")
  }

  // Support both camelCase and snake_case for memory_dir / memory_file
  const memoryDir =
    typeof cfg.memoryDir === "string" && cfg.memoryDir.length > 0
      ? cfg.memoryDir
      : typeof cfg.memory_dir === "string" &&
          (cfg.memory_dir as string).length > 0
        ? (cfg.memory_dir as string)
        : "memory/"

  const memoryFile =
    typeof cfg.memoryFile === "string" && cfg.memoryFile.length > 0
      ? cfg.memoryFile
      : typeof cfg.memory_file === "string" &&
          (cfg.memory_file as string).length > 0
        ? (cfg.memory_file as string)
        : "MEMORY.md"

  return {
    project:
      typeof cfg.project === "string" && cfg.project.length > 0
        ? cfg.project
        : defaultProject(),
    projectPath:
      typeof cfg.projectPath === "string" && cfg.projectPath.length > 0
        ? cfg.projectPath
        : ".",
    bmPath:
      typeof cfg.bmPath === "string" && cfg.bmPath.length > 0
        ? cfg.bmPath
        : "bm",
    memoryDir,
    memoryFile,
    autoCapture: typeof cfg.autoCapture === "boolean" ? cfg.autoCapture : true,
    captureMinChars:
      typeof cfg.captureMinChars === "number" && cfg.captureMinChars >= 0
        ? cfg.captureMinChars
        : typeof cfg.capture_min_chars === "number" &&
            (cfg.capture_min_chars as number) >= 0
          ? (cfg.capture_min_chars as number)
          : 10,
    autoRecall:
      typeof cfg.autoRecall === "boolean"
        ? cfg.autoRecall
        : typeof cfg.auto_recall === "boolean"
          ? (cfg.auto_recall as boolean)
          : true,
    recallPrompt:
      typeof cfg.recallPrompt === "string" && cfg.recallPrompt.length > 0
        ? cfg.recallPrompt
        : typeof cfg.recall_prompt === "string" &&
            (cfg.recall_prompt as string).length > 0
          ? (cfg.recall_prompt as string)
          : "Check for active tasks and recent activity. Summarize anything relevant to the current session.",
    debug: typeof cfg.debug === "boolean" ? cfg.debug : false,
  }
}

export const basicMemoryConfigSchema = {
  parse: parseConfig,
}
