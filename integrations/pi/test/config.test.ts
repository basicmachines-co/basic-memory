import { mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import assert from "node:assert/strict";
import test from "node:test";

import { bmCommandParts } from "../extensions/bm-cli.ts";
import { parseConfig, resolveConfigPath } from "../extensions/config.ts";

test("parseConfig applies safe defaults", () => {
  assert.deepEqual(parseConfig(), {
    transport: "cli",
    bmPath: "bm",
    bmCommand: undefined,
    project: undefined,
    projectId: undefined,
    captureFolder: "pi/sessions",
    recallTimeframe: "7d",
    autoRecall: true,
    autoCapture: true,
    captureMinChars: 80,
    mcpServerName: "basic-memory",
    useHookFlow: true,
    debug: false,
  });
});

test("parseConfig accepts snake_case aliases and strict unknown keys", () => {
  const cfg = parseConfig({
    transport: "mcp",
    bm_path: "/tmp/bm",
    bm_command: ["uv", "run", "basic-memory"],
    project_id: "123",
    capture_folder: "sessions",
    recall_timeframe: "3d",
    auto_recall: true,
    auto_capture: true,
    capture_min_chars: 12,
    mcp_server_name: "memory",
    use_hook_flow: false,
  });

  assert.equal(cfg.transport, "mcp");
  assert.equal(cfg.bmPath, "/tmp/bm");
  assert.deepEqual(cfg.bmCommand, ["uv", "run", "basic-memory"]);
  assert.equal(cfg.projectId, "123");
  assert.equal(cfg.captureFolder, "sessions");
  assert.equal(cfg.recallTimeframe, "3d");
  assert.equal(cfg.autoRecall, true);
  assert.equal(cfg.autoCapture, true);
  assert.equal(cfg.captureMinChars, 12);
  assert.equal(cfg.mcpServerName, "memory");
  assert.equal(cfg.useHookFlow, false);
  assert.throws(() => parseConfig({ nope: true }), /unknown keys: nope/);
});

test("resolveConfigPath finds the nearest ancestor workspace config", () => {
  const root = join(tmpdir(), `bm-pi-config-${process.pid}-${Date.now()}`);
  const child = join(root, "packages", "cli");
  const config = join(root, ".pi", "basic-memory.json");
  mkdirSync(join(root, ".pi"), { recursive: true });
  mkdirSync(child, { recursive: true });
  writeFileSync(config, "{}\n");

  assert.equal(resolveConfigPath(child), config);
  assert.equal(resolveConfigPath(child, "custom.json"), join(child, "custom.json"));
});

test("bmCommand overrides bmPath when invoking the CLI", () => {
  assert.deepEqual(bmCommandParts(parseConfig({ bmPath: "bm" })), {
    command: "bm",
    argsPrefix: [],
  });
  assert.deepEqual(
    bmCommandParts(parseConfig({ bmPath: "bm", bmCommand: ["uv", "run", "basic-memory"] })),
    { command: "uv", argsPrefix: ["run", "basic-memory"] },
  );
});

test("parseConfig rejects invalid known values", () => {
  assert.throws(
    () => parseConfig({ transport: "mpc" }),
    /transport must be "cli" or "mcp"/,
  );
  assert.throws(() => parseConfig({ project: 123 }), /project must be a non-empty string/);
  assert.throws(() => parseConfig({ bmCommand: [] }), /bmCommand must be a non-empty string array/);
  assert.throws(() => parseConfig({ autoRecall: "yes" }), /autoRecall must be a boolean/);
  assert.throws(() => parseConfig({ useHookFlow: "no" }), /useHookFlow must be a boolean/);
  assert.throws(
    () => parseConfig({ captureMinChars: -1 }),
    /captureMinChars must be a non-negative number/,
  );
  assert.throws(() => parseConfig([]), /must be a JSON object/);
});
