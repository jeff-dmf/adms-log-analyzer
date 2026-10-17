// UI-25 review fixes: one idempotent recovery, the whole view (selection included) goes back, navigation drops a
// pending view change, the count is recoverable alone, the first timeline failure is recoverable, discarded answers
// raise no global notice, a stopped stream is forgotten with its case.
import test from "node:test";
import assert from "node:assert/strict";

import { createRecovery } from "../../src/dmslog/web/js/status-bar-model.js";
import { createViewFlow } from "../../src/dmslog/web/js/grid-model.js";
import { installGlobalDom, until } from "./fixtures/fake-dom.mjs";

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}
const flush = async () => { for (let i = 0; i < 8; i += 1) await Promise.resolve(); };

// ---- recovery (#4) ------------------------------------------------------------------------------------------------

function recoveryRig(over = {}) {
  const log = [];
  const healthGate = deferred();
  const rig = {
    log, healthGate, healthCalls: 0,
    recovery: createRecovery({
      health: () => { rig.healthCalls += 1; return over.health ? over.health() : healthGate.promise; },
      setOnline: () => log.push("online"),
      openingPending: () => false,
      reloadCases: async () => log.push("cases"),
      fallback: async () => log.push("fallback"),
      onError: (e) => log.push("error:" + e.message),
    }),
  };
  return rig;
}

test("two clicks on Retry while /health waits are ONE recovery: health once, each task once", async () => {
  const rig = recoveryRig();
  rig.recovery.fail("grid", async () => rig.log.push("grid"));
  rig.recovery.fail("timeline", async () => rig.log.push("timeline"));
  const first = rig.recovery.retry();
  const second = rig.recovery.retry(); // the bar button and the message button, or a double click
  assert.strictEqual(first, second);
  assert.equal(rig.recovery.busy, true);
  rig.healthGate.resolve();
  await first;
  assert.equal(rig.healthCalls, 1);
  assert.deepEqual(rig.log, ["online", "grid", "timeline"]);
  assert.equal(rig.recovery.busy, false);
});

test("nothing registered and the connection never lost: Retry reloads NOTHING (no fallback to applyView)", async () => {
  const rig = recoveryRig({ health: async () => {} });
  await rig.recovery.retry();
  assert.deepEqual(rig.log, ["online"]);
});

test("the connection was lost and nothing is registered: the view is applied again, once", async () => {
  const rig = recoveryRig({ health: async () => {} });
  rig.recovery.markOffline();
  await Promise.all([rig.recovery.retry(), rig.recovery.retry()]);
  assert.deepEqual(rig.log, ["online", "fallback"]);
});

test("health still failing keeps every task registered and touches nothing", async () => {
  const rig = recoveryRig({ health: async () => { throw new Error("down"); } });
  rig.recovery.fail("grid", async () => rig.log.push("grid"));
  await rig.recovery.retry();
  assert.deepEqual(rig.log, []);
  assert.equal(rig.recovery.has("grid"), true);
});

test("a task that fails again registers itself again; clear and clearAll forget tasks; a rejected task is reported", async () => {
  const rig = recoveryRig({ health: async () => {} });
  let calls = 0;
  const task = async () => { calls += 1; rig.recovery.fail("grid", task); };
  rig.recovery.fail("grid", task);
  rig.recovery.fail("count", async () => { throw new Error("boom"); });
  await rig.recovery.retry();
  assert.equal(calls, 1);
  assert.equal(rig.recovery.has("grid"), true); // failed again
  assert.ok(rig.log.includes("error:boom"));
  rig.recovery.clear("grid");
  assert.equal(rig.recovery.size, 0);
  rig.recovery.fail("a", () => {});
  rig.recovery.fail("b", () => {});
  rig.recovery.clearAll();
  assert.equal(rig.recovery.size, 0);
  rig.recovery.fail("x", "not a function");
  assert.equal(rig.recovery.size, 0);
});

// ---- the view as a whole (#1) and navigation (#2) ----------------------------------------------------------------

function flowRig() {
  const state = { live: "A", ids: [1], groups: ["g1"], stack: "s0", filter: "FA" };
  const log = [];
  const flow = createViewFlow({
    capture: () => ({ caseId: "C", live: state.live, selection: { ids: [...state.ids], groups: [...state.groups] }, stack: state.stack, filter: null }),
    restoreState: (to) => { state.live = to.live; state.ids = [...to.selection.ids]; state.groups = [...to.selection.groups]; state.stack = to.stack; log.push("restore:" + to.live); },
    afterRollback: (to) => { state.filter = to.filter; log.push("after:" + to.live); },
    onConfirm: () => log.push("confirm"),
  });
  const apply = (live, ids, groups, stack, filter) => {
    Object.assign(state, { live, ids, groups, stack });
    const { attempt, token } = flow.begin();
    attempt.filter = filter;
    return token;
  };
  return { state, log, flow, apply };
}

test("a failed change puts back the toolbar, the SOURCES and COLLECTIONS and the trail; the detail pane is not cleared", () => {
  const r = flowRig();
  r.flow.settle(r.apply("A", [1], ["g1"], "s0", "FA"), "ok");
  assert.deepEqual(r.log, ["confirm"]); // the detail goes only now
  r.log.length = 0;
  const token = r.apply("B", [2], [], "s1", "FB"); // the user selected source 2 and narrowed
  assert.equal(r.log.length, 0); // beginning a change clears nothing
  r.flow.settle(token, "failed", true);
  assert.deepEqual(r.log, ["restore:A", "after:A"]);
  assert.deepEqual(r.state.ids, [1]);
  assert.deepEqual(r.state.groups, ["g1"]);
  assert.equal(r.state.stack, "s0");
  assert.equal(r.state.filter, "FA");
});

test("Retry restores the attempt that failed, selection included, and nothing else", () => {
  const r = flowRig();
  r.flow.settle(r.apply("A", [1], ["g1"], "s0", "FA"), "ok");
  r.flow.settle(r.apply("B", [2], [], "s1", "FB"), "failed", true);
  const failed = r.flow.retryState("C");
  assert.equal(failed.live, "B");
  assert.deepEqual(r.state.ids, [2]);
  assert.equal(r.state.stack, "s1");
  assert.equal(r.flow.retryState("OTHER"), null);
  r.apply("C2", [3], [], "s2", "FC"); // a newer change replaces the failed one
  assert.equal(r.flow.retryTarget(), null);
});

test("navigation during a pending change restores the confirmed view; the dropped change is stale afterwards", () => {
  const r = flowRig();
  r.flow.settle(r.apply("A", [1], ["g1"], "s0", "FA"), "ok");
  const token = r.apply("B", [2], [], "s1", "FB");
  r.log.length = 0;
  const to = r.flow.abandon();
  assert.equal(to.live, "A");
  assert.deepEqual(r.log, ["restore:A", "after:A"]);
  assert.deepEqual(r.state.ids, [1]);
  assert.equal(r.flow.settle(token, "ok").action, "stale"); // B finishing late confirms nothing
  assert.equal(r.flow.settle(token, "failed").action, "stale");
  assert.equal(r.flow.tracker.confirmed.live, "A");
  // nothing confirmed yet: nothing to go back to
  const fresh = flowRig();
  fresh.apply("A", [1], [], "s0", "FA");
  assert.equal(fresh.flow.abandon(), null);
  assert.deepEqual(fresh.log, []);
});

// ---- the grid: navigation (#2) and the count (#3) -----------------------------------------------------------------

function fakeElement() {
  const attrs = {};
  const classes = new Set();
  return {
    style: {}, dataset: {}, children: [], hidden: false, clientHeight: 66, scrollTop: 0, className: "", attrs, classes,
    classList: { toggle(name, on) { if (on) classes.add(name); else classes.delete(name); }, add() {}, remove() {} },
    addEventListener() {}, appendChild(c) { this.children.push(c); return c; },
    setAttribute(k, v) { attrs[k] = String(v); }, removeAttribute(k) { delete attrs[k]; }, focus() {},
    set textContent(v) { this.children = []; this._t = v; }, get textContent() { return this._t || ""; },
  };
}
globalThis.document = globalThis.document || { createElement: fakeElement, createTextNode: (t) => ({ textContent: String(t) }) };
const { Grid } = await import("../../src/dmslog/web/js/grid.js");
const { api, ApiError } = await import("../../src/dmslog/web/js/api.js");
const rows = (from, n) => Array.from({ length: n }, (_, i) => ({ id: from + i, ts: 1000 + from + i, sev: 1, level: 3, msg: "m" }));
const FA = { and: [{ field: "a" }] };
const FB = { and: [{ field: "b" }] };

function gridRig() {
  const pages = [];
  const counts = [];
  api.page = (caseId, filter, cursor, dir, limit, signal) => { const d = deferred(); pages.push({ filter, cursor, dir, signal, d }); return d.promise; };
  api.count = (caseId, filter, signal) => { const d = deferred(); counts.push({ filter, signal, d }); return d.promise; };
  const log = { counts: [], failures: [], recovered: [], abandoned: [], errors: [] };
  const grid = new Grid({
    viewport: fakeElement(), rowsLayer: fakeElement(), spacer: fakeElement(), header: fakeElement(),
    onSelect() {}, onCount: (v) => log.counts.push(v), onPosition() {}, onError: (e) => log.errors.push(e),
    onFailure: (err, info) => log.failures.push({ err, ...info }),
    onRecovered: (scope) => log.recovered.push(scope),
    onAbandon: (v) => log.abandoned.push(v),
  });
  return { grid, pages, counts, log };
}
async function first(ctx) {
  const loading = ctx.grid.setView({ caseId: "C", filter: FA, range: null });
  ctx.counts.at(-1).d.resolve({ count: 500, exact: true });
  ctx.pages.at(-1).d.resolve({ rows: rows(1, 20), prev: null, next: null });
  assert.equal(await loading, "ok");
  await flush();
}

test("End during a pending filter change drops it: the controls are told, the page is of the confirmed filter, B's count is gone", async () => {
  const ctx = gridRig();
  await first(ctx);
  const changing = ctx.grid.setView({ caseId: "C", filter: FB, range: null });
  const countB = ctx.counts.at(-1);
  const ending = ctx.grid.goEnd();
  assert.equal(ctx.log.abandoned.length, 1);
  assert.equal(ctx.log.abandoned[0].filter, FB);
  assert.equal(ctx.grid.pending, null);
  assert.equal(ctx.pages.at(-1).filter, FA); // navigation runs on the filter the rows belong to
  assert.equal(countB.signal.aborted, true);
  assert.deepEqual(ctx.log.counts.at(-1), { n: 500, exact: true }); // "loading" is over: the old count is back
  ctx.pages.at(-2).d.resolve({ rows: rows(900, 3), prev: null, next: null }); // B's page, late
  assert.equal(await changing, "superseded");
  ctx.pages.at(-1).d.resolve({ rows: rows(30, 5), prev: "p", next: null });
  assert.equal(await ending, "ok");
  assert.equal(ctx.grid.filter, FA);
  assert.equal(ctx.grid.window.rows.length, 5);
});

test("a column change (reload of the current view) also drops a pending filter change", async () => {
  const ctx = gridRig();
  await first(ctx);
  const changing = ctx.grid.setView({ caseId: "C", filter: FB, range: null });
  const reloading = ctx.grid.setFieldColumns(["x"]);
  assert.equal(ctx.log.abandoned.length, 1);
  assert.equal(ctx.pages.at(-1).filter, FA);
  ctx.pages.at(-2).d.resolve({ rows: rows(900, 3), prev: null, next: null });
  assert.equal(await changing, "superseded");
  ctx.pages.at(-1).d.resolve({ rows: rows(1, 3), prev: null, next: null });
  await reloading;
  assert.equal(ctx.grid.filter, FA);
});

test("navigation with nothing pending, or after the change committed, abandons nothing", async () => {
  const ctx = gridRig();
  await first(ctx);
  const going = ctx.grid.goEnd();
  ctx.pages.at(-1).d.resolve({ rows: rows(30, 5), prev: null, next: null });
  await going;
  assert.equal(ctx.log.abandoned.length, 0);
});

test("a LATE count failure of a view whose page failed raises nothing", async () => {
  const ctx = gridRig();
  await first(ctx);
  const changing = ctx.grid.setView({ caseId: "C", filter: FB, range: null });
  ctx.pages.at(-1).d.reject(new ApiError(0, "network", "down"));
  assert.equal(await changing, "failed");
  assert.equal(ctx.counts.at(-1).signal.aborted, true); // the discarded count is cancelled, not just ignored
  const failuresBefore = ctx.log.failures.length;
  ctx.counts.at(-1).d.reject(new ApiError(500, "boom", "count failed late"));
  await flush();
  assert.equal(ctx.log.failures.length, failuresBefore);
  assert.equal(ctx.log.errors.length, 0);
  assert.equal(ctx.grid.countFailure, null);
});

test("a count that times out is a failure of its own: Retry repeats ONLY the count, once, and announces recovery", async () => {
  const ctx = gridRig();
  const loading = ctx.grid.setView({ caseId: "C", filter: FA, range: null });
  ctx.counts.at(-1).d.reject(new ApiError(408, "query_timeout", "slow"));
  ctx.pages.at(-1).d.resolve({ rows: rows(1, 5), prev: null, next: null });
  assert.equal(await loading, "ok");
  await flush();
  const fail = ctx.log.failures.at(-1);
  assert.equal(fail.scope, "count");
  assert.equal(fail.kind, "timeout");
  assert.equal(ctx.grid.hasFailure(), false); // the rows are fine
  const pagesBefore = ctx.pages.length;
  const countsBefore = ctx.counts.length;
  const a = fail.retry();
  const b = fail.retry(); // a second click while it runs
  assert.equal(await b, "noop");
  assert.equal(ctx.counts.length, countsBefore + 1);
  assert.equal(ctx.pages.length, pagesBefore); // no page was asked for
  ctx.counts.at(-1).d.resolve({ count: 5, exact: true });
  assert.equal(await a, "ok");
  assert.deepEqual(ctx.log.recovered, ["count"]);
  assert.equal(ctx.grid.total, 5);
  assert.equal(await fail.retry(), "noop");
});

test("a count that fails again after Retry stays repeatable", async () => {
  const ctx = gridRig();
  const loading = ctx.grid.setView({ caseId: "C", filter: FA, range: null });
  ctx.counts.at(-1).d.reject(new ApiError(500, "boom", "x"));
  ctx.pages.at(-1).d.resolve({ rows: rows(1, 5), prev: null, next: null });
  await loading;
  await flush();
  const retried = ctx.grid.retryCount();
  ctx.counts.at(-1).d.reject(new ApiError(500, "boom", "again"));
  assert.equal(await retried, "failed");
  assert.equal(ctx.log.failures.length, 2);
  assert.notEqual(ctx.grid.countFailure, null);
});

// ---- the first timeline failure (#5) -------------------------------------------------------------------------------

test("a histogram that fails with no chart yet is a failure too: it is reported, repeatable, and recovers", async () => {
  const doc = installGlobalDom();
  globalThis.getComputedStyle = () => ({ getPropertyValue: () => "" });
  const { initTimeline } = await import("../../src/dmslog/web/js/timeline.js");
  class U {
    static paths = { bars: () => ({}), stepped: () => ({}) };
    static tzDate(d) { return d; }
    constructor(o, c, host) { this.over = new (doc.body.constructor)(doc, "div"); this.select = {}; host.appendChild(this.over); }
    setSize() {} setSelect() {} valToPos(v) { return v; } posToVal(v) { return v; } destroy() {}
  }
  const good = { bucket_ms: 60000, start: 1760000000000, n_buckets: 1, split: "sev", total: 1, exact: true, series: [{ key: 2, counts: [1] }] };
  let mode = "fail";
  const events = [];
  const api2 = { histogram: async () => { if (mode === "fail") throw new ApiError(0, "network", "down"); return good; } };
  const host = doc.createElement("div");
  doc.body.appendChild(host);
  const timeline = initTimeline(host, {
    api: api2, uplot: U, getFilter: () => ({ caseId: "C", filter: null }), onRangeSelected() {},
    onFailure: (err) => events.push("fail:" + err.kind), onRecovered: () => events.push("ok"),
  });
  await until(() => timeline.hasFailure(), "the first failure");
  assert.equal(timeline.isStale(), false); // no chart: nothing "old" is shown
  assert.deepEqual(events, ["fail:network"]);
  mode = "ok";
  timeline.refresh();
  await until(() => !timeline.hasFailure(), "recovered");
  assert.deepEqual(events, ["fail:network", "ok"]);
  timeline.destroy();
});

// ---- discarded answers raise no global notice (#6) -----------------------------------------------------------------

test("a response the consumer discarded (aborted or no longer current) does not raise Disconnected or the session notice", async () => {
  const mod = await import("../../src/dmslog/web/js/api.js");
  const seen = [];
  mod.onNetworkError(() => seen.push("network"));
  mod.onForbidden(() => seen.push("forbidden"));
  const realFetch = globalThis.fetch;
  try {
    globalThis.fetch = async () => { throw new TypeError("Failed to fetch"); };
    // aborted before the failure surfaced
    const c1 = new AbortController();
    c1.abort();
    await assert.rejects(mod.api.entry("C", 1, c1.signal));
    assert.deepEqual(seen, []);
    // bound check says the request was replaced
    let current = false;
    const c2 = new AbortController();
    mod.bindCurrent(c2.signal, () => current);
    await assert.rejects(mod.api.entry("C", 1, c2.signal));
    assert.deepEqual(seen, []);
    // still the current one: the notice is raised
    current = true;
    await assert.rejects(mod.api.entry("C", 1, c2.signal));
    assert.deepEqual(seen, ["network"]);
    // no signal at all: always raised
    await assert.rejects(mod.api.health());
    assert.deepEqual(seen, ["network", "network"]);
    // the same rules for 403
    globalThis.fetch = async () => ({ status: 403, ok: false, json: async () => ({}) });
    const c3 = new AbortController();
    mod.bindCurrent(c3.signal, () => false);
    await assert.rejects(mod.api.entry("C", 1, c3.signal));
    assert.deepEqual(seen, ["network", "network"]);
    await assert.rejects(mod.api.entry("C", 1, new AbortController().signal));
    assert.deepEqual(seen, ["network", "network", "forbidden"]);
  } finally {
    globalThis.fetch = realFetch;
    mod.onNetworkError(null);
    mod.onForbidden(null);
  }
});

// ---- the stream guard and the case (#8) ----------------------------------------------------------------------------

test("closing the case forgets a stopped stream: nothing to resume, and resume() is true only when a stream opened", async () => {
  const doc = installGlobalDom();
  const sources = [];
  class FakeEventSource {
    constructor() { this.handlers = {}; this.closed = false; sources.push(this); }
    addEventListener(name, fn) { (this.handlers[name] ??= []).push(fn); }
    close() { this.closed = true; }
    emit(name) { for (const fn of this.handlers[name] || []) fn({}); }
  }
  globalThis.EventSource = FakeEventSource;
  const { initFollowTail } = await import("../../src/dmslog/web/js/follow-tail.js");
  const toggle = doc.createElement("input");
  toggle.type = "checkbox";
  let caseId = "C";
  const follow = initFollowTail({
    toggle, newButton: doc.createElement("button"),
    grid: { tailInfo: () => ({ atEnd: true, top: 0, visible: 1, length: 1 }), followTail: async () => {}, total: null },
    getCase: () => caseId, getFilter: () => null, onChanged() {}, onError() {}, onStreamStopped() {},
  });
  toggle.checked = true;
  toggle.dispatch("change");
  for (let i = 0; i < 3; i += 1) sources[0].emit("error");
  assert.equal(follow.stopped(), true);
  caseId = null; // the case was closed
  follow.onCase();
  assert.equal(follow.stopped(), false);
  assert.equal(follow.resume(), false);
  assert.equal(sources.length, 1); // nothing was opened
  // stopped again on a real case; with the case gone before Retry, resume cannot claim success
  caseId = "C";
  follow.onCase();
  for (let i = 0; i < 3; i += 1) sources.at(-1).emit("error");
  assert.equal(follow.stopped(), true);
  caseId = null;
  assert.equal(follow.resume(), false);
  delete globalThis.EventSource;
});
