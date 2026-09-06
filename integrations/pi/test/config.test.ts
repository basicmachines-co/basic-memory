import assert from "node:assert/strict";
import test from "node:test";

import { parseConfig } from "../extensions/config.ts";

test("parseConfig applies safe defaults", () => {
  assert.deepEqual(parseConfig(), {
    transport: "cli",
    bmPath: "bm",
    project: undefined,
    projectId: undefined,
    captureFolder: "pi/sessions",
    recallTimeframe: "7d",
    autoRecall: false,
    autoCapture: false,
    captureMinChars: 80,
    mcpServerName: "basic-memory",
    debug: false,
  });
});

test("parseConfig accepts snake_case aliases and strict unknown keys", () => {
  const cfg = parseConfig({
    transport: "mcp",
    bm_path: "/tmp/bm",
    project_id: "123",
    capture_folder: "sessions",
    recall_timeframe: "3d",
    auto_recall: true,
    auto_capture: true,
    capture_min_chars: 12,
    mcp_server_name: "memory",
  });

  assert.equal(cfg.transport, "mcp");
  assert.equal(cfg.bmPath, "/tmp/bm");
  assert.equal(cfg.projectId, "123");
  assert.equal(cfg.captureFolder, "sessions");
  assert.equal(cfg.recallTimeframe, "3d");
  assert.equal(cfg.autoRecall, true);
  assert.equal(cfg.autoCapture, true);
  assert.equal(cfg.captureMinChars, 12);
  assert.equal(cfg.mcpServerName, "memory");
  assert.throws(() => parseConfig({ nope: true }), /unknown keys: nope/);
});

test("parseConfig rejects invalid known values", () => {
  assert.throws(
    () => parseConfig({ transport: "mpc" }),
    /transport must be "cli" or "mcp"/,
  );
  assert.throws(() => parseConfig({ project: 123 }), /project must be a non-empty string/);
  assert.throws(() => parseConfig({ autoRecall: "yes" }), /autoRecall must be a boolean/);
  assert.throws(
    () => parseConfig({ captureMinChars: -1 }),
    /captureMinChars must be a non-negative number/,
  );
  assert.throws(() => parseConfig([]), /must be a JSON object/);
});
