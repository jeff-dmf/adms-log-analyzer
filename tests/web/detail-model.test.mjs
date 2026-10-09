import test from "node:test";
import assert from "node:assert/strict";
import { RequestGuard, canLoadContext } from "../../src/dmslog/web/js/detail-model.js";

test("a newer request invalidates the older one (out-of-order responses)", () => {
  const guard = new RequestGuard();
  const a = guard.begin("caseA", 1);
  const b = guard.begin("caseB", 7);
  assert.equal(guard.isCurrent(a, "caseB", 1), false);
  assert.equal(guard.isCurrent(b, "caseB", 7), true);
});

test("invalidate (clear / case switch) discards pending responses", () => {
  const guard = new RequestGuard();
  const a = guard.begin("caseA", 1);
  guard.invalidate();
  assert.equal(guard.isCurrent(a, "caseA", 1), false);
});

test("case or entry mismatch is rejected even with the same generation", () => {
  const guard = new RequestGuard();
  const a = guard.begin("caseA", 1);
  assert.equal(guard.isCurrent(a, "caseB", 1), false);
  assert.equal(guard.isCurrent(a, "caseA", 2), false);
  assert.equal(guard.isCurrent(a, "caseA"), true);
});

test("a new request aborts the previous signal", () => {
  const guard = new RequestGuard();
  const a = guard.begin("c", 1);
  guard.begin("c", 2);
  assert.equal(a.signal.aborted, true);
});

test("late responses simulated out of order: only the latest renders", async () => {
  const guard = new RequestGuard();
  const rendered = [];
  let current = { caseId: "caseA", entryId: 1 };
  const run = async (caseId, entryId, delay) => {
    const t = guard.begin(caseId, entryId);
    await new Promise((r) => setTimeout(r, delay));
    if (guard.isCurrent(t, current.caseId, entryId)) rendered.push(caseId + ":" + entryId);
  };
  const first = run("caseA", 1, 30);
  current = { caseId: "caseB", entryId: 7 };
  const second = run("caseB", 7, 5);
  await Promise.all([first, second]);
  assert.deepEqual(rendered, ["caseB:7"]);
});

test("context is refused for an entry that belongs to another case (switch window)", () => {
  const entry = { id: 7 };
  assert.equal(canLoadContext(entry, "A", "A"), true);
  assert.equal(canLoadContext(entry, "A", "B"), false);
  assert.equal(canLoadContext(null, "A", "A"), false);
  assert.equal(canLoadContext(entry, "A", null), false);
});

test("closing the context invalidates the pending request (it cannot reopen the panel)", () => {
  const guard = new RequestGuard();
  const ticket = guard.begin("A", 7);
  assert.equal(guard.isCurrent(ticket, "A", 7), true);
  guard.invalidate(); // what closeContext() now does
  assert.equal(guard.isCurrent(ticket, "A", 7), false);
});
