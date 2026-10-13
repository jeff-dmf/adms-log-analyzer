// Pure model of the chained views: composition, back/forward, names, limits, persistence, keys.
import test from "node:test";
import assert from "node:assert/strict";
import { composeFilter } from "../../src/dmslog/web/js/filters.js";
import {
  MAX_DEPTH, createViewsStack, describeFilter, emptyState, isValidFilter, loadStack, saveStack, viewsKey,
} from "../../src/dmslog/web/js/views-stack-model.js";

const ownFilter = (s) => composeFilter({ sourceIds: [], text: s.searchText, ...s.formState }, s.userFilter, s.toggles);

function make() {
  let live = emptyState();
  const stack = createViewsStack({ ownFilter, getLive: () => live });
  return {
    stack,
    set: (patch) => { live = { ...live, ...patch }; },
    setLive: (state) => { live = state; },
    get live() { return live; },
  };
}

test("names come from the last filter", () => {
  assert.equal(describeFilter(null), null);
  assert.equal(describeFilter({ and: [{ sev: { le: 3 } }, { tid: [12] }] }), "tid = 12");
  assert.equal(describeFilter({ tid: [1, 2] }), "tid in 1, 2");
  assert.equal(describeFilter({ key: { type: "gid", eq: "ab" } }), "key gid = ab");
  assert.equal(describeFilter({ field: { name: "op", eq: "Read" } }), "op = Read");
  assert.equal(describeFilter({ not: { tpl: [5] } }), "not template = 5");
  assert.equal(describeFilter({ ts: { ge: 1 } }), "time range");
  assert.equal(describeFilter({ key: { type: "gid", eq: "x".repeat(60) } }).length < 40, true);
});

test("narrow needs a filter, then parent AND own composes", () => {
  const m = make();
  assert.deepEqual(m.stack.narrow(), { ok: false, reason: "empty" });
  m.set({ userFilter: { tid: [12] } });
  assert.equal(m.stack.name(0), "tid = 12");
  const res = m.stack.narrow();
  assert.equal(res.ok, true);
  m.setLive(res.state);
  assert.equal(m.stack.depth, 1);
  assert.deepEqual(m.stack.parentsFilter(), { and: [{ tid: [12] }] });
  assert.equal(m.stack.name(1), "View 2");
  m.set({ userFilter: { key: { type: "gid", eq: "g1" } } });
  assert.equal(m.stack.name(1), "key gid = g1");
  const second = m.stack.narrow();
  m.setLive(second.state);
  assert.deepEqual(m.stack.parentsFilter(), { and: [{ tid: [12] }, { key: { type: "gid", eq: "g1" } }] });
});

test("back, forward and breadcrumbs keep the children until a new narrow", () => {
  const m = make();
  m.set({ searchText: "alpha" });
  m.setLive(m.stack.narrow().state);
  m.set({ userFilter: { tid: [3] } });
  m.setLive(m.stack.narrow().state);
  assert.equal(m.stack.index, 2);
  assert.deepEqual(m.stack.crumbs().map((c) => c.active), [false, false, true]);
  m.setLive(m.stack.goTo(0)); // click the root crumb
  assert.equal(m.live.searchText, "alpha");
  assert.equal(m.stack.canForward(), true);
  assert.deepEqual(m.stack.crumbs().map((c) => c.future), [false, true, true]);
  m.setLive(m.stack.goTo(1));
  assert.equal(m.live.searchText, "");
  assert.deepEqual(m.live.userFilter, { tid: [3] }); // own state restored
  assert.equal(m.stack.goTo(1), null); // same view: nothing to do
  assert.equal(m.stack.goTo(9), null);
  // A new narrow from view 1 drops view 2.
  m.setLive(m.stack.goTo(0));
  m.setLive(m.stack.goTo(1));
  m.set({ searchText: "beta" });
  m.setLive(m.stack.narrow().state);
  assert.equal(m.stack.size, 3);
  m.setLive(m.stack.goTo(0));
  m.set({ userFilter: { pid: [1] } });
  m.setLive(m.stack.narrow().state); // from root: truncates both children
  assert.equal(m.stack.size, 2);
});

test("depth is limited", () => {
  const m = make();
  for (let i = 1; i < MAX_DEPTH; i += 1) {
    m.set({ userFilter: { tid: [i] } });
    const res = m.stack.narrow();
    assert.equal(res.ok, true);
    m.setLive(res.state);
  }
  m.set({ userFilter: { tid: [99] } });
  assert.deepEqual(m.stack.narrow(), { ok: false, reason: "full" });
  assert.equal(m.stack.size, MAX_DEPTH);
});

test("rename wins over the automatic name and is sanitised", () => {
  const m = make();
  m.set({ userFilter: { tid: [1] } });
  m.stack.rename(0, "  my   view  " + "x".repeat(100));
  assert.equal(m.stack.name(0).startsWith("my view x"), true);
  assert.equal(m.stack.name(0).length <= 40, true);
  m.stack.rename(0, "");
  assert.equal(m.stack.name(0), "tid = 1");
});

test("the root without filter is named", () => {
  assert.equal(make().stack.name(0), "All records");
});

test("persistence round-trips and rejects garbage", () => {
  const m = make();
  m.set({ userFilter: { tid: [1] } });
  m.setLive(m.stack.narrow().state);
  m.stack.rename(1, "child");
  const store = new Map();
  const storage = { getItem: (k) => store.get(k) ?? null, setItem: (k, v) => store.set(k, v), removeItem: (k) => store.delete(k) };
  saveStack(storage, "C1", m.stack);
  const other = make();
  const restored = loadStack(storage, "C1", other.stack);
  assert.deepEqual(restored, emptyState());
  assert.equal(other.stack.size, 2);
  assert.equal(other.stack.name(1), "child");
  assert.deepEqual(other.stack.parentsFilter(), { and: [{ tid: [1] }] });
  assert.equal(loadStack(storage, "none", other.stack), null);
  store.set("dmslog.viewsStack.C1", "{not json");
  assert.equal(loadStack(storage, "C1", other.stack), null);
  assert.equal(other.stack.load({ v: 1, index: 5, nodes: [{}] }), null);
  assert.equal(other.stack.load({ v: 1, index: 0, nodes: new Array(MAX_DEPTH + 1).fill({}) }), null);
  const throwing = { getItem() { throw new Error("blocked"); }, setItem() { throw new Error("blocked"); }, removeItem() { throw new Error("x"); } };
  saveStack(throwing, "C1", m.stack); // must not throw
  assert.equal(loadStack(throwing, "C1", other.stack), null);
  // A root-only stack removes the saved entry.
  const fresh = make();
  store.set("dmslog.viewsStack.C2", "{}");
  saveStack(storage, "C2", fresh.stack);
  assert.equal(store.has("dmslog.viewsStack.C2"), false);
});

test("Alt+Left / Alt+Right only", () => {
  assert.equal(viewsKey({ key: "ArrowLeft", altKey: true }), "back");
  assert.equal(viewsKey({ key: "ArrowRight", altKey: true }), "forward");
  assert.equal(viewsKey({ key: "ArrowLeft" }), null);
  assert.equal(viewsKey({ key: "ArrowLeft", altKey: true, ctrlKey: true }), null);
  assert.equal(viewsKey({ key: "n", altKey: true }), null);
});

test("malformed stored filters are discarded, valid ones kept", () => {
  assert.equal(isValidFilter(null), true);
  assert.equal(isValidFilter({ and: [{ tid: [1] }, { not: { key: { type: "g", eq: "x" } } }] }), true);
  assert.equal(isValidFilter({ and: "invalid" }), false);
  assert.equal(isValidFilter({ and: [1] }), false);
  assert.equal(isValidFilter({ tid: [1], pid: [2] }), false);
  assert.equal(isValidFilter({ tid: Array.from({ length: 1001 }, (_, k) => k) }), false);
  assert.equal(isValidFilter({ text: { fts: "x".repeat(513) } }), false);
  assert.equal(isValidFilter({ and: Array.from({ length: 201 }, () => ({ tid: [1] })) }), false);
  let deep = { tid: [1] };
  for (let i = 0; i < 100000; i += 1) {
    deep = { not: deep };
  }
  assert.equal(isValidFilter(deep), false); // no RangeError either
  let nested = [1];
  for (let i = 0; i < 50000; i += 1) {
    nested = [nested];
  }
  assert.equal(isValidFilter({ tid: nested }), false);

  const m = make();
  const bad = (userFilter) => ({
    v: 1, index: 1, nodes: [{ name: "", state: { ...emptyState(), userFilter } }, { name: "", state: emptyState() }],
  });
  assert.deepEqual(m.stack.load(bad({ and: "invalid" })), emptyState());
  assert.equal(m.stack.parentsFilter(), null); // the invalid filter was dropped
  m.stack.load(bad(deep));
  assert.equal(m.stack.crumbs().length, 2); // renders names without throwing
  m.stack.load(bad({ and: [{ tid: [4] }] }));
  assert.deepEqual(m.stack.parentsFilter(), { and: [{ tid: [4] }] });
});
