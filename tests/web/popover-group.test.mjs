// popover-group.js: one popover open at a time across components.
import test from "node:test";
import assert from "node:assert/strict";

import { createPopoverGroup } from "../../src/dmslog/web/js/popover-group.js";

test("opening a member closes the one that was open; the closed one's callback does not close the new one", () => {
  const group = createPopoverGroup();
  const log = [];
  let a;
  let b;
  a = group.join("a", () => { log.push("close a"); a.closed(); });
  b = group.join("b", () => { log.push("close b"); b.closed(); });
  a.opened();
  b.opened();
  assert.deepEqual(log, ["close a"]);
  a.opened();
  assert.deepEqual(log, ["close a", "close b"]);
});

test("re-opening the same member or closing a stale one closes nothing", () => {
  const group = createPopoverGroup();
  const log = [];
  const a = group.join("a", () => log.push("a"));
  const b = group.join("b", () => log.push("b"));
  a.opened();
  a.opened();
  b.closed(); // b was never open
  b.opened();
  assert.deepEqual(log, ["a"]);
  a.closed(); // stale: b is the current one
  a.opened();
  assert.deepEqual(log, ["a", "b"]);
});
