// Follow tail: every obsolete operation is dropped right before its effect (one operation token, UI-30 review).
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import { createFollowFlow } from "../../src/dmslog/web/js/follow-tail-model.js";

function deferred() {
  let resolve;
  const promise = new Promise((res) => { resolve = res; });
  return { promise, resolve };
}

function setup(info = { atEnd: true, top: 100, visible: 20, length: 200 }, { showResult = true } = {}) {
  const counts = [];
  const states = [];
  const shown = [];
  const tails = [];
  const current = { info: { ...info }, total: 1000, showResult, nextId: 0, ids: [] };
  const api = {
    count(caseId, filter, signal) {
      const d = deferred();
      counts.push({ signal, d });
      return d.promise;
    },
  };
  const filter = {};
  const flow = createFollowFlow({
    api,
    getCase: () => "A",
    getFilter: () => filter,
    view: {
      tailInfo: () => current.info,
      followTail: (op) => { const d = deferred(); tails.push({ isCurrent: () => op.isCurrent(), op, d }); return d.promise; },
      beginCount: () => { current.nextId += 1; return current.nextId; },
      total: () => current.total,
      showTotal(n, exact, id) { shown.push({ n, exact, id }); if (current.showResult) { current.total = n; } return current.showResult; },
    },
    onState: (s) => states.push(s),
  });
  return { flow, counts, states, shown, tails, current };
}

const tick = async () => { for (let i = 0; i < 6; i += 1) await Promise.resolve(); };

test("cancel (tab hidden) makes the grid's isCurrent false and a late tail result changes nothing", async () => {
  const t = setup({ atEnd: true, top: 180, visible: 20, length: 200 });
  t.flow.setOn(true);
  const engaging = t.flow.engage();
  assert.equal(t.tails[0].isCurrent(), true);
  const before = t.states.length;
  t.flow.cancel();
  assert.equal(t.tails[0].isCurrent(), false);
  t.tails[0].d.resolve(true);
  await engaging;
  assert.equal(t.states.length, before); // no forget(), no emit from the obsolete operation
});

test("the model never publishes a total the grid refused (status and button agree)", async () => {
  const t = setup(undefined, { showResult: false });
  t.flow.setOn(true);
  const refreshing = t.flow.refresh();
  t.counts[0].d.resolve({ count: 1100, exact: true });
  await refreshing;
  assert.equal(t.shown.length, 1);
  assert.equal(t.flow.state.pending, 0);
  assert.equal(t.flow.state.total, null);
  assert.equal(t.states.at(-1).pending ?? 0, 0);
  // The grid accepts the next one: both adopt it together.
  t.current.showResult = true;
  const next = t.flow.refresh();
  t.counts[1].d.resolve({ count: 1100, exact: true });
  await next;
  assert.equal(t.flow.state.pending, 100);
  assert.equal(t.current.total, 1100);
});

test("returning to the bottom with nothing pending drops the count in flight", async () => {
  const t = setup();
  t.flow.setOn(true);
  const refreshing = t.flow.refresh();
  assert.equal(t.counts.length, 1);
  t.current.info = { atEnd: true, top: 180, visible: 20, length: 200 };
  t.flow.viewChanged();
  assert.equal(t.counts[0].signal.aborted, true);
  t.counts[0].d.resolve({ count: 1100, exact: true });
  await refreshing;
  assert.equal(t.flow.state.pending, 0);
  assert.equal(t.shown.length, 0);
  assert.equal(t.states.at(-1).position, "bottom");
});

test("a load of the tail is not cancelled by the view reaching the bottom (it is what brings it there)", async () => {
  const t = setup();
  t.flow.setOn(true);
  const engaging = t.flow.engage();
  t.current.info = { atEnd: true, top: 180, visible: 20, length: 200 };
  t.flow.viewChanged();
  assert.equal(t.tails[0].isCurrent(), true);
  t.tails[0].d.resolve(true);
  await engaging;
  assert.equal(t.states.at(-1).position, "bottom");
});

test("a tail load the grid dropped (the user scrolled) does not forget the baseline nor claim the bottom", async () => {
  const t = setup();
  t.flow.setOn(true);
  const first = t.flow.refresh();
  t.counts[0].d.resolve({ count: 1050, exact: true });
  await first;
  assert.equal(t.flow.state.pending, 50);
  const engaging = t.flow.engage();
  t.tails[0].d.resolve(false); // the user scrolled during the load
  await engaging;
  assert.equal(t.flow.state.pending, 50);
  assert.equal(t.states.at(-1).position, "scrolled");
  await tick();
});

test("an old count after switching off is dropped", async () => {
  const t = setup();
  t.flow.setOn(true);
  const refreshing = t.flow.refresh();
  t.flow.setOn(false);
  t.counts[0].d.resolve({ count: 5000, exact: true });
  await refreshing;
  assert.equal(t.shown.length, 0);
});

test("main.js: a watch update without a source change reloads the collections but not /api/sources", () => {
  const text = readFileSync(new URL("../../src/dmslog/web/js/main.js", import.meta.url), "utf8");
  const block = text.slice(text.indexOf("onChanged: ({ sources = false }"), text.indexOf("onError: showError,", text.indexOf("onChanged: ({ sources = false }")));
  assert.match(block, /if \(sources\) \{\s*panel\.refreshSources\(true\)/);
  assert.match(block, /else if \(collectionsPanel\) \{\s*collectionsPanel\.refresh\(\)/);
});

test("the count request takes its id from the grid before the request and hands it back with the answer", async () => {
  const t = setup();
  t.flow.setOn(true);
  const refreshing = t.flow.refresh();
  t.counts[0].d.resolve({ count: 1100, exact: true });
  await refreshing;
  assert.equal(t.shown[0].id, 1);
});

test("an operation object has an id and a kind; only the newest is current", async () => {
  const t = setup({ atEnd: true, top: 180, visible: 20, length: 200 });
  t.flow.setOn(true);
  t.flow.engage();
  t.flow.engage();
  assert.equal(t.tails[0].op.kind, "load");
  assert.ok(t.tails[1].op.id > t.tails[0].op.id);
  assert.equal(t.tails[0].op.isCurrent(), false);
  assert.equal(t.tails[1].op.isCurrent(), true);
  assert.equal(t.flow.busy(), true);
  t.tails[1].d.resolve(true);
  await tick();
  assert.equal(t.flow.busy(), false);
});
