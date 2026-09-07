import { spawn } from "node:child_process";

import type { BasicMemoryPiConfig } from "./config.ts";

export interface BmCommandResult {
  stdout: string;
  stderr: string;
  code: number | null;
}

export class BmCommandError extends Error {
  readonly result: BmCommandResult;

  constructor(message: string, result: BmCommandResult) {
    super(message);
    this.name = "BmCommandError";
    this.result = result;
  }
}

export function projectArgs(cfg: BasicMemoryPiConfig): string[] {
  const args: string[] = [];
  if (cfg.projectId) args.push("--project-id", cfg.projectId);
  else if (cfg.project) args.push("--project", cfg.project);
  return args;
}

export function bmCommandParts(cfg: BasicMemoryPiConfig): { command: string; argsPrefix: string[] } {
  const command = cfg.bmCommand ?? [cfg.bmPath];
  return { command: command[0], argsPrefix: command.slice(1) };
}

export async function runBm(
  cfg: BasicMemoryPiConfig,
  args: string[],
  options: { stdin?: string; signal?: AbortSignal; timeoutMs?: number } = {},
): Promise<BmCommandResult> {
  const controller = new AbortController();
  const signals = [controller.signal, options.signal].filter(Boolean) as AbortSignal[];
  const signal = signals.length === 1 ? signals[0] : AbortSignal.any(signals);
  const timeout = setTimeout(() => controller.abort(), options.timeoutMs ?? 30_000);
  timeout.unref?.();

  return await new Promise<BmCommandResult>((resolve, reject) => {
    const bm = bmCommandParts(cfg);
    const child = spawn(bm.command, [...bm.argsPrefix, ...args], {
      stdio: ["pipe", "pipe", "pipe"],
      signal,
    });
    const stdout: Buffer[] = [];
    const stderr: Buffer[] = [];

    child.stdout.on("data", (chunk: Buffer) => stdout.push(chunk));
    child.stderr.on("data", (chunk: Buffer) => stderr.push(chunk));
    child.on("error", reject);
    child.on("close", (code) => {
      clearTimeout(timeout);
      resolve({
        stdout: Buffer.concat(stdout).toString("utf8"),
        stderr: Buffer.concat(stderr).toString("utf8"),
        code,
      });
    });

    child.stdin.end(options.stdin ?? "");
  });
}

export async function runBmJson<T>(
  cfg: BasicMemoryPiConfig,
  args: string[],
  options: { stdin?: string; signal?: AbortSignal; timeoutMs?: number } = {},
): Promise<T> {
  const result = await runBm(cfg, args, options);
  if (result.code !== 0) {
    throw new BmCommandError(result.stderr.trim() || result.stdout.trim() || "bm command failed", result);
  }
  try {
    return JSON.parse(result.stdout) as T;
  } catch (error) {
    throw new BmCommandError(
      `bm command returned invalid JSON: ${error instanceof Error ? error.message : String(error)}`,
      result,
    );
  }
}
