import assert from "node:assert/strict";
import test from "node:test";

import { parseConfig } from "../extensions/config.ts";
import { formatStatus } from "../extensions/index.ts";

test("unconfigured status never implies ambient default routing or enabled automation", () => {
  for (const trusted of [false, true]) {
    const status = formatStatus(parseConfig(), trusted);
    assert.match(status, /project: unconfigured/);
    assert.match(status, /auto recall: blocked \(no project mapping\)/);
    assert.match(status, /auto capture: blocked \(no project mapping\)/);
    assert.match(status, /\/skill:basic-memory-pi-setup/);
    assert.doesNotMatch(status, /project: default/);
  }
});

test("untrusted workspace names the trust step even with a configured project", () => {
  const status = formatStatus(parseConfig({ project: "research" }), false);
  assert.match(status, /project: research/);
  assert.match(status, /workspace trust: off/);
  assert.match(status, /auto recall: blocked \(workspace not trusted\)/);
  assert.match(status, /auto capture: blocked \(workspace not trusted\)/);
  assert.match(status, /BASIC_MEMORY_PI_TRUST_WORKSPACE=1/);
});

test("trusted mapping reports effective settings and respects project ID precedence", () => {
  const status = formatStatus(parseConfig({ project: "research", projectId: "project-id", autoCapture: false }), true);
  assert.match(status, /project: id:project-id/);
  assert.match(status, /auto recall: on/);
  assert.match(status, /auto capture: off \(configured\)/);
  assert.doesNotMatch(status, /blocked|To enable workspace automation/);
});

test("explicitly disabled automation stays off regardless of trust", () => {
  const status = formatStatus(parseConfig({ autoRecall: false, autoCapture: false, useHookFlow: false }), false);
  assert.match(status, /auto recall: off \(configured\)/);
  assert.match(status, /auto capture: off \(configured\)/);
  assert.match(status, /hook flow: off/);
});
