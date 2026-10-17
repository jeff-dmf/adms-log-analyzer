// 9.12 UI: the actions of the View menu for repeated records, through the real registry and app-actions.js, with a real
// controller and a fake api. The module arrives late (after the first view): the actions wait for it.
import test from "node:test";
import assert from "node:assert/strict";

import { createActionRegistry } from "../../src/dmslog/web/js/actions.js";
import { registerAppActions } from "../../src/dmslog/web/js/app-actions.js";
import { createRepeats, hideNode } from "../../src/dmslog/web/js/repeat-model.js";

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

function world({ ready = Promise.resolve(), caseId = "A", withController = true } = {}) {
  const state = { caseId, userFilter: null, errors: [], redraws: 0 };
  const repeats = withController
    ? createRepeats({
      api: { repeatMarks: () => new Promise(() => {}) },
      getCase: () => state.caseId,
      getUserFilter: () => state.userFilter,
      setUserFilter: async (filter) => { state.userFilter = filter; },
      storage: null,
      onChange: () => { state.redraws += 1; },
      schedule: () => 1,
    })
    : null;
  const actions = createActionRegistry({ onError: (err, id) => state.errors.push([id, err.message]) });
  registerAppActions(actions, {
    ready: { alerts: Promise.resolve(), followTail: Promise.resolve(), repeats: ready },
    repeats: () => (state.loaded ? repeats : null),
    theme: () => "system",
    cases: () => [],
    drawerOpen: () => false,
    followTailOn: () => false,
  });
  const ctx = { caseId: () => state.caseId };
  return { actions, ctx, state, repeats, load: () => { state.loaded = true; } };
}

test("the three actions live in the View menu and carry their strings", () => {
  const w = world();
  for (const [id, key] of [["view.markRepeats", "actMarkRepeats"], ["view.hideRepeats", "actHideRepeats"], ["view.repeatWindow", "actRepeatWindow"]]) {
    const def = w.actions.get(id);
    assert.equal(def.group, "view", id);
    assert.equal(def.labelKey, key);
  }
  const labels = w.actions.list({ group: "view" }).map((d) => d.id);
  assert.ok(labels.indexOf("view.followTail") < labels.indexOf("view.markRepeats"));
  assert.ok(labels.indexOf("view.repeatWindow") < labels.indexOf("view.theme"));
});

test("Hide repeated records adds the chip, shows as pressed, and a second use takes it out", async () => {
  const w = world();
  w.load();
  assert.equal(w.actions.state("view.hideRepeats", w.ctx).pressed, false);
  assert.deepEqual(await w.actions.invoke("view.hideRepeats", w.ctx), { ok: true });
  assert.deepEqual(w.state.userFilter, { and: [hideNode(1000)] });
  assert.equal(w.actions.state("view.hideRepeats", w.ctx).pressed, true);
  await w.actions.invoke("view.hideRepeats", w.ctx);
  assert.equal(w.state.userFilter, null);
  assert.equal(w.actions.state("view.hideRepeats", w.ctx).pressed, false);
});

test("the chip removed from the strip (the filter changed elsewhere) is the state: the item is no longer pressed", async () => {
  const w = world();
  w.load();
  await w.actions.invoke("view.hideRepeats", w.ctx);
  w.state.userFilter = null; // the user removed the chip
  assert.equal(w.actions.state("view.hideRepeats", w.ctx).pressed, false);
  await w.actions.invoke("view.hideRepeats", w.ctx);
  assert.deepEqual(w.state.userFilter, { and: [hideNode(1000)] });
});

test("the window menu lists the presets with the one in force checked; choosing one rewrites the chip", async () => {
  const w = world();
  w.load();
  const def = w.actions.get("view.repeatWindow");
  const children = def.children(w.ctx);
  assert.deepEqual(children.map((c) => [c.label, c.args, c.checked]), [
    ["100 ms", 100, false], ["500 ms", 500, false], ["1 s", 1000, true], ["5 s", 5000, false], ["30 s", 30000, false], ["1 min", 60000, false],
  ]);
  await w.actions.invoke("view.hideRepeats", w.ctx);
  await w.actions.invoke("view.repeatWindow", w.ctx, 5000);
  assert.deepEqual(w.state.userFilter, { and: [hideNode(5000)] });
  assert.equal(def.children(w.ctx).find((c) => c.checked).args, 5000);
  await w.actions.invoke("view.repeatWindow", w.ctx, "oops");
  await w.actions.invoke("view.repeatWindow", w.ctx, NaN);
  assert.deepEqual(w.state.userFilter, { and: [hideNode(5000)] }, "an invalid window changes nothing");
});

test("Mark repeated records is pressed by default, toggles, and asks the page to draw again", async () => {
  const w = world();
  w.load();
  assert.equal(w.actions.state("view.markRepeats", w.ctx).pressed, true);
  await w.actions.invoke("view.markRepeats", w.ctx);
  assert.equal(w.actions.state("view.markRepeats", w.ctx).pressed, false);
  assert.equal(w.state.redraws, 1);
  await w.actions.invoke("view.markRepeats", w.ctx);
  assert.equal(w.actions.state("view.markRepeats", w.ctx).pressed, true);
});

test("before the module arrives the items are not pressed and the window menu is empty; using one waits for the module", async () => {
  const late = deferred();
  const w = world({ ready: late.promise });
  assert.equal(w.actions.state("view.hideRepeats", w.ctx).pressed, false);
  assert.equal(w.actions.state("view.markRepeats", w.ctx).pressed, false);
  assert.deepEqual(w.actions.get("view.repeatWindow").children(w.ctx), []);
  const invoking = w.actions.invoke("view.hideRepeats", w.ctx);
  assert.equal(w.state.userFilter, null, "nothing happens before the module is in");
  w.load();
  late.resolve();
  assert.deepEqual(await invoking, { ok: true });
  assert.deepEqual(w.state.userFilter, { and: [hideNode(1000)] });
});

test("a module that failed to load disables the items with a reason; the others of the menu are not affected", async () => {
  const w = world({ ready: Promise.reject(new Error("offline")), withController: false });
  const result = await w.actions.invoke("view.hideRepeats", w.ctx);
  assert.deepEqual(result, { ok: false, reason: "loadFailed" });
  await w.actions.invoke("view.markRepeats", w.ctx); // each item learns of the failure when it tries to load
  const state = w.actions.state("view.markRepeats", w.ctx);
  assert.equal(state.enabled, false);
  assert.equal(state.reasonKey, "actionUnavailable");
  assert.equal(w.actions.state("view.theme", w.ctx).enabled, true);
});

test("without a case the items are disabled and nothing is changed", async () => {
  const w = world({ caseId: null });
  w.load();
  assert.equal(w.actions.state("view.hideRepeats", w.ctx).enabled, false);
  assert.deepEqual(await w.actions.invoke("view.hideRepeats", w.ctx), { ok: false, reason: "disabled" });
  assert.equal(w.state.userFilter, null);
});
