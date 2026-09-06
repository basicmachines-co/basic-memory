import assert from "node:assert/strict";
import test from "node:test";

import { recallFenceFor } from "../extensions/index.ts";

test("recallFenceFor chooses a delimiter longer than recalled data", () => {
  assert.equal(recallFenceFor("plain text"), "```");
  assert.equal(recallFenceFor("note contains ``` and ````"), "`````");
});
