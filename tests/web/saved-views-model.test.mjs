// Saved filters: what the Save button does for a new name, an existing name and a rename.
import test from "node:test";
import assert from "node:assert/strict";

import { MAX_NAME, MAX_VIEWS } from "../../src/dmslog/web/js/filter-tree-model.js";
import { planSave } from "../../src/dmslog/web/js/saved-views-model.js";

const views = [{ id: 1, name: "Errors" }, { id: 2, name: "Slow calls" }];

test("a new name creates", () => {
  assert.deepEqual(planSave({ raw: "  New  view ", views }), { action: "create", name: "New view" });
});

test("an existing name asks first, then overwrites (letter case ignored)", () => {
  const ask = planSave({ raw: "errors", views });
  assert.deepEqual(ask, { action: "confirmOverwrite", name: "errors", id: 1 });
  assert.deepEqual(planSave({ raw: "ERRORS", views, confirmedName: "errors" }), { action: "overwrite", name: "ERRORS", id: 1 });
  // a confirmation for another name does not carry over
  assert.equal(planSave({ raw: "Slow calls", views, confirmedName: "errors" }).action, "confirmOverwrite");
});

test("rename keeps other names unique and may change only the letter case", () => {
  assert.deepEqual(planSave({ raw: "Fatal", views, renamingId: 1 }), { action: "rename", name: "Fatal", id: 1 });
  assert.equal(planSave({ raw: "errors", views, renamingId: 1 }).action, "rename");
  assert.deepEqual(planSave({ raw: "slow CALLS", views, renamingId: 1 }), { action: "error", name: "slow CALLS", error: "duplicate" });
});

test("name problems", () => {
  assert.equal(planSave({ raw: "  \t ", views }).error, "empty");
  assert.equal(planSave({ raw: "x".repeat(MAX_NAME + 1), views }).error, "tooLong");
  assert.equal(planSave({ raw: "x".repeat(MAX_NAME), views }).action, "create");
  const many = Array.from({ length: MAX_VIEWS }, (_, i) => ({ id: i, name: "v" + i }));
  assert.equal(planSave({ raw: "fresh", views: many }).error, "tooMany");
  assert.equal(planSave({ raw: "v1", views: many }).action, "confirmOverwrite"); // overwriting is fine at the limit
});

import { createViewsGuard } from "../../src/dmslog/web/js/saved-views-model.js";

test("views guard: answers of another case or of an older list are stale", async () => {
  const guard = createViewsGuard();
  let currentCase = "A";
  let views = [{ id: 1, name: "from B" }];
  const slow = new Promise((resolve) => setTimeout(() => resolve({ id: 1, name: "from A" }), 5));
  const ticket = guard.begin(currentCase);
  currentCase = "B"; // user switches case while the request is in flight
  guard.bump();
  const updated = await slow;
  if (guard.isCurrent(ticket, currentCase)) {
    views = views.map((v) => (v.id === updated.id ? updated : v));
  }
  assert.equal(views[0].name, "from B");
  const fresh = guard.begin(currentCase);
  assert.equal(guard.isCurrent(fresh, currentCase), true);
  assert.equal(guard.isCurrent(fresh, "A"), false);
  guard.bump();
  assert.equal(guard.isCurrent(fresh, currentCase), false);
});

test("views guard run: stale successes and stale failures are dropped, current failures are thrown", async () => {
  const guard = createViewsGuard();
  let currentCase = "A";
  const late = (fn) => new Promise((resolve, reject) => setTimeout(() => fn(resolve, reject), 2));
  const failing = guard.run("A", () => currentCase, () => late((_ok, bad) => bad(new Error("old"))));
  const succeeding = guard.run("A", () => currentCase, () => late((ok) => ok("v")));
  currentCase = "B";
  guard.bump();
  assert.deepEqual(await failing, { stale: true });
  assert.deepEqual(await succeeding, { stale: true });
  assert.deepEqual(await guard.run("B", () => currentCase, () => Promise.resolve(7)), { stale: false, value: 7 });
  await assert.rejects(guard.run("B", () => currentCase, () => Promise.reject(new Error("now"))), /now/);
});
