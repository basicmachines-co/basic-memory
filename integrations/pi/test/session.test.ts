import assert from "node:assert/strict";
import test from "node:test";

import { buildCaptureDraft, extractSessionTurns } from "../extensions/session.ts";

test("extractSessionTurns reads Pi-style message entries", () => {
  const turns = extractSessionTurns([
    { type: "header" },
    { type: "message", message: { role: "user", content: [{ type: "text", text: "hello" }] } },
    { type: "message", message: { role: "assistant", content: "hi" } },
    { type: "message", message: { role: "toolResult", content: "ignored" } },
  ]);

  assert.deepEqual(turns, [
    { role: "user", text: "hello" },
    { role: "assistant", text: "hi" },
  ]);
});

test("buildCaptureDraft creates a frontmatter-backed pi_session note", () => {
  const draft = buildCaptureDraft({
    turns: [
      { role: "user", text: "Decide to support CLI and MCP" },
      { role: "assistant", text: "We will keep the integration thin." },
    ],
    cwd: "/repo",
    sessionFile: "/tmp/session.jsonl",
    sessionId: "s1",
    model: "openai/gpt-6-astra",
    title: "Pi handoff",
  });

  assert.ok(draft);
  assert.equal(draft.title, "Pi handoff");
  assert.match(draft.content, /^---\ntitle: "Pi handoff"\ntype: "pi_session"/);
  assert.match(draft.content, /pi_session_id: "s1"/);
  assert.match(draft.content, /- \[next_step\] Review this checkpoint/);
});

test("buildCaptureDraft reuses a stable session title when no title is provided", () => {
  const base = {
    turns: [{ role: "user" as const, text: "Continue the Pi memory package" }],
    cwd: "/repo",
    sessionId: "pi-session-123",
    branchId: "branch-a",
  };

  const first = buildCaptureDraft(base);
  const second = buildCaptureDraft(base);
  const otherBranch = buildCaptureDraft({ ...base, branchId: "branch-b" });

  assert.ok(first);
  assert.ok(second);
  assert.ok(otherBranch);
  assert.equal(
    first.title,
    "Pi session pi-session-123/branch-a — Continue the Pi memory package",
  );
  assert.equal(second.title, first.title);
  assert.notEqual(otherBranch.title, first.title);
});

test("buildCaptureDraft keeps the full thread in overwritten checkpoints", () => {
  const turns = Array.from({ length: 10 }, (_, index) => ({
    role: (index % 2 === 0 ? "user" : "assistant") as "user" | "assistant",
    text: `turn ${index}`,
  }));

  const draft = buildCaptureDraft({ turns, cwd: "/repo", sessionId: "s1", branchId: "b1" });

  assert.ok(draft);
  assert.match(draft.content, /turn 0/);
  assert.match(draft.content, /turn 9/);
});


test("buildCaptureDraft keeps branch identity when session IDs are long", () => {
  const common = {
    turns: [{ role: "user" as const, text: "Investigate forked approaches" }],
    cwd: "/repo",
    sessionId: "123e4567-e89b-12d3-a456-426614174000",
  };

  const branchA = buildCaptureDraft({ ...common, branchId: "branch-alpha" });
  const branchB = buildCaptureDraft({ ...common, branchId: "branch-beta" });

  assert.ok(branchA);
  assert.ok(branchB);
  assert.match(branchA.title, /branch-alpha/);
  assert.match(branchB.title, /branch-beta/);
  assert.notEqual(branchA.title, branchB.title);
});
