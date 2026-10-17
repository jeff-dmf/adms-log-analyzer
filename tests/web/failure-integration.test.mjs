// UI-25, third review: the real grid, the real view controller and the real failure center wired the way main.js wires
// them (only the page is faked). Each test is a scenario the reviewer reproduced.
import test from "node:test";
import assert from "node:assert/strict";

import { createFailureCenter } from "../../src/dmslog/web/js/status-bar-model.js";
import { createViewController } from "../../src/dmslog/web/js/grid-model.js";

function fakeElement() {
  const attrs = {};
  return {
    style: {}, dataset: {}, children: [], hidden: false, clientHeight: 66, scrollTop: 0, className: "", attrs,
    classList: { toggle() {}, add() {}, remove() {} },
    addEventListener() {}, appendChild(c) { this.children.push(c); return c; },
    setAttribute(k, v) { attrs[k] = String(v); }, removeAttribute(k) { delete attrs[k]; }, focus() {},
    set textContent(v) { this.children = []; this._t = v; }, get textContent() { return this._t || ""; },
  };
}
globalThis.document = { createElement: fakeElement, createTextNode: (t) => ({ textContent: String(t) }) };
const { Grid } = await import("../../src/dmslog/web/js/grid.js");
const { api, ApiError, isRetryable } = await import("../../src/dmslog/web/js/api.js");

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}
const flush = async () => { for (let i = 0; i < 10; i += 1) await Promise.resolve(); };
const rows = (from, n) => Array.from({ length: n }, (_, i) => ({ id: from + i, ts: 1000 + from + i, sev: 1, level: 3, msg: "m" }));
const net = () => new ApiError(0, "network", "down");

function rig() {
  const pages = [];
  const counts = [];
  api.page = (caseId, filter, cursor, dir, limit, signal) => { const d = deferred(); pages.push({ caseId, filter, cursor, dir, signal, d }); return d.promise; };
  api.count = (caseId, filter, signal) => { const d = deferred(); counts.push({ caseId, filter, signal, d }); return d.promise; };
  const state = { live: "A", ids: [1], groups: ["g1"] };
  const log = { derived: 0, confirmed: 0, offline: 0, online: 0, timeline: [], fallbacks: 0, errors: [] };
  const messages = []; // {text, action, dismissed}
  const status = {
    message(text, opts = {}) {
      const m = { text, action: opts.action || null, dismissed: false };
      messages.push(m);
      return { dismiss() { m.dismissed = true; } };
    },
  };
  const live = () => messages.filter((m) => !m.dismissed);
  const timelineGate = { current: null };
  let grid = null;
  let flow = null;
  const timeline = {
    refreshNow: () => { log.timeline.push("refresh"); timelineGate.current = deferred(); return timelineGate.current.promise; },
    invalidate: () => log.timeline.push("invalidate"),
  };
  const failures = createFailureCenter({
    status, retryLabel: "Retry now", genericMessage: "Request failed",
    goOffline: () => { log.offline += 1; },
    health: async () => {},
    setOnline: () => { log.online += 1; },
    openingPending: () => false,
    reloadCases: async () => {},
    fallback: async () => { log.fallbacks += 1; },
    onError: (e) => log.errors.push(e),
    retryView: () => retryView(),
    hasViewRetry: () => flow.retryTarget() !== null,
    dropViewRetry: () => flow.dropRetry(),
    retryGrid: () => grid.retry(),
    retryCount: () => grid.retryCount(),
    refreshTimeline: () => timeline.refreshNow(),
    resumeStream: () => true,
    cancelRequests: () => { flow.caseChanged(); grid.closeCase(); timeline.invalidate(); },
  });
  grid = new Grid({
    viewport: fakeElement(), rowsLayer: fakeElement(), spacer: fakeElement(), header: fakeElement(),
    onSelect() {}, onCount() {}, onPosition() {}, onError: (e) => log.errors.push(e),
    onFailure: (err, info) => failures.gridFailure(err, info),
    onRecovered: (scope) => failures.gridRecovered(scope),
    onAbandon: () => flow.abandon(),
  });
  flow = createViewController({
    getCase: () => "C",
    captureLive: () => state.live,
    restoreLive: (l) => { state.live = l; },
    getSelection: () => ({ ids: [...state.ids], groups: [...state.groups] }),
    setSelection: (sel) => { state.ids = [...sel.ids]; state.groups = [...sel.groups]; },
    getStack: () => null,
    refreshDerived: () => { log.derived += 1; },
    afterRollback: () => {},
    onConfirm: () => { log.confirmed += 1; },
  });
  const apply = (liveState, ids, groups) => {
    Object.assign(state, { live: liveState, ids, groups });
    const { attempt, token } = flow.begin();
    attempt.filter = { f: liveState };
    failures.viewApplied();
    const loaded = grid.setView({ caseId: "C", filter: attempt.filter, range: null });
    flow.track(token, loaded, () => (grid.failure ? grid.failure.kind : "unknown"), isRetryable);
    return loaded;
  };
  const retryView = () => (flow.retryState("C") === null ? Promise.resolve() : apply(state.live, state.ids, state.groups));
  const rig = { grid, pages, counts, state, log, messages, live, failures, flow, apply, timeline, timelineGate };
  return rig;
}

async function loadA(r, { countFails = false } = {}) {
  const loaded = r.apply("A", [1], ["g1"]);
  if (countFails) {
    r.counts.at(-1).d.reject(new ApiError(408, "query_timeout", "Count timed out"));
  } else {
    r.counts.at(-1).d.resolve({ count: 100, exact: true });
  }
  r.pages.at(-1).d.resolve({ rows: rows(1, 20), prev: null, next: null });
  assert.equal(await loaded, "ok");
  await flush();
}

test("#1 a case switch cancels rows, count and histogram AT ONCE: late answers paint nothing and register nothing", async () => {
  const r = rig();
  await loadA(r);
  const changing = r.apply("B", [2], []);
  const pageB = r.pages.at(-1);
  const countB = r.counts.at(-1);
  r.failures.caseChanged(); // main's onCase, before the new sources arrive
  assert.equal(pageB.signal.aborted, true);
  assert.equal(countB.signal.aborted, true);
  assert.equal(r.grid.window.rows.length, 0); // the old case's rows are gone
  assert.ok(r.log.timeline.includes("invalidate"));
  pageB.d.resolve({ rows: rows(500, 4), prev: null, next: null }); // late success of the old case
  assert.equal(await changing, "superseded");
  assert.equal(r.grid.window.rows.length, 0);
  // late failures of the old case do not recreate a task
  const again = r.apply("C1", [1], []);
  const oldPage = r.pages.at(-1);
  r.failures.caseChanged();
  oldPage.d.reject(new ApiError(0, "network", "late"));
  await again;
  assert.equal(r.failures.recovery.size, 0);
  assert.equal(r.live().length, 0);
});

test("#2 filter B fails, End in A fails: Retry repeats End in A, not B", async () => {
  const r = rig();
  await loadA(r);
  const b = r.apply("B", [2], []);
  r.pages.at(-1).d.reject(net());
  await b;
  assert.equal(r.state.live, "A"); // rolled back
  const end = r.grid.goEnd();
  r.pages.at(-1).d.reject(new ApiError(500, "boom", "Server error"));
  assert.equal(await end, "failed");
  const before = r.pages.length;
  const retrying = r.failures.retry();
  await flush();
  assert.equal(r.pages.length, before + 1);
  const sent = r.pages.at(-1);
  assert.equal(sent.dir, "back"); // End
  assert.deepEqual(sent.filter, { f: "A" }); // on the confirmed view
  assert.equal(r.state.live, "A");
  sent.d.resolve({ rows: rows(80, 20), prev: "p", next: null });
  await retrying;
  assert.equal(r.failures.recovery.size, 0);
});

test("#3 the selection comes back with the derived panels (rollback and Retry), not only the grid", async () => {
  const r = rig();
  await loadA(r);
  const derivedBefore = r.log.derived;
  const b = r.apply("B", [2], ["g2"]);
  r.pages.at(-1).d.reject(new ApiError(500, "boom", "x"));
  await b;
  assert.deepEqual(r.state.groups, ["g1"]);
  assert.equal(r.log.derived, derivedBefore + 1); // Operations / Dashboard / clock follow G1 again
  const retrying = r.failures.retry();
  await flush();
  assert.deepEqual(r.state.groups, ["g2"]);
  assert.equal(r.log.derived, derivedBefore + 2); // and follow the retried G2
  r.pages.at(-1).d.resolve({ rows: rows(1, 3), prev: null, next: null });
  r.counts.at(-1).d.resolve({ count: 3, exact: true });
  await retrying;
});

test("#4 an isolated histogram timeout offers a visible Retry, also on the first load", async () => {
  const r = rig();
  r.failures.timelineFailure(new ApiError(408, "query_timeout", "Timeline timed out"));
  const notice = r.live().at(-1);
  assert.ok(notice);
  assert.equal(notice.text, "Timeline timed out");
  assert.equal(notice.action.label, "Retry now");
  notice.action.run();
  await flush();
  assert.deepEqual(r.log.timeline, ["refresh"]);
  r.timelineGate.current.resolve();
  r.failures.timelineRecovered();
  await flush();
  assert.equal(r.live().length, 0);
});

test("#5 the count failed, a navigation failed and the next one worked: the Retry of the count is still offered", async () => {
  const r = rig();
  await loadA(r, { countFails: true });
  assert.equal(r.live().length, 1);
  assert.equal(r.live()[0].text, "Count timed out");
  const end = r.grid.goEnd();
  r.pages.at(-1).d.reject(new ApiError(500, "boom", "Server error"));
  await end;
  assert.equal(r.live().at(-1).text, "Server error"); // the latest failure speaks
  const home = r.grid.goHome();
  r.pages.at(-1).d.resolve({ rows: rows(1, 20), prev: null, next: null });
  await home;
  await flush();
  const notice = r.live().at(-1);
  assert.ok(notice, "a notice is still there");
  assert.equal(notice.text, "Count timed out");
  assert.equal(notice.action.label, "Retry now");
  const pagesBefore = r.pages.length;
  const countsBefore = r.counts.length;
  notice.action.run();
  await flush();
  assert.equal(r.counts.length, countsBefore + 1); // only the count
  assert.equal(r.pages.length, pagesBefore);
  r.counts.at(-1).d.resolve({ count: 100, exact: true });
  await flush();
  assert.equal(r.live().length, 0);
});

test("#6 the recovery lasts until the histogram request is over", async () => {
  const r = rig();
  r.failures.timelineFailure(net());
  const done = r.failures.retry();
  await flush();
  assert.equal(r.failures.recovery.busy, true);
  r.timelineGate.current.resolve();
  await done;
  assert.equal(r.failures.recovery.busy, false);
});

test("#7 a histogram that timed out and then failed with a 400 is no longer repeatable", () => {
  const r = rig();
  r.failures.timelineFailure(new ApiError(408, "query_timeout", "slow"));
  assert.equal(r.failures.recovery.has("timeline"), true);
  r.failures.timelineFailure(new ApiError(400, "bad_filter", "bad"));
  assert.equal(r.failures.recovery.has("timeline"), false);
  assert.equal(r.live().length, 0); // the notice of the 408 went with its task
});

test("#8 the count failure of A survives a view change B that fails, and a B that the user abandons", async () => {
  const r = rig();
  await loadA(r, { countFails: true });
  assert.equal(r.failures.recovery.has("count"), true);
  const b = r.apply("B", [2], []);
  assert.equal(r.failures.recovery.has("count"), false); // the attempt owns the count now
  r.pages.at(-1).d.reject(net());
  await b;
  assert.equal(r.failures.recovery.has("count"), true); // back with A
  // abandoned by navigation
  const c = r.apply("C1", [3], []);
  assert.equal(r.failures.recovery.has("count"), false);
  const end = r.grid.goEnd();
  assert.equal(r.failures.recovery.has("count"), true);
  r.pages.at(-2).d.resolve({ rows: rows(9, 2), prev: null, next: null });
  await c;
  r.pages.at(-1).d.resolve({ rows: rows(1, 5), prev: null, next: null });
  await end;
  // and when B succeeds the old failure is simply gone
  const d = r.apply("D", [4], []);
  r.pages.at(-1).d.resolve({ rows: rows(1, 5), prev: null, next: null });
  r.counts.at(-1).d.resolve({ count: 5, exact: true });
  assert.equal(await d, "ok");
  await flush();
  assert.equal(r.failures.recovery.has("count"), false);
});

test("double fault: Retry of a view whose retry fails again stays repeatable; navigating after it drops the old target", async () => {
  const r = rig();
  await loadA(r);
  const b = r.apply("B", [2], []);
  r.pages.at(-1).d.reject(net());
  await b;
  const retrying = r.failures.retry();
  await flush();
  r.pages.at(-1).d.reject(net()); // B fails again
  await retrying;
  await flush();
  assert.equal(r.failures.recovery.has("grid"), true);
  assert.notEqual(r.flow.retryTarget(), null);
  const home = r.grid.goHome(); // navigation after the failure succeeds
  r.pages.at(-1).d.resolve({ rows: rows(1, 4), prev: null, next: null });
  await home;
  await flush();
  assert.equal(r.flow.retryTarget(), null);
  assert.equal(r.failures.recovery.has("grid"), false);
});

test("while a recovery runs the notice is gone (its tasks are in flight); a task that fails again brings it back", async () => {
  const r = rig();
  r.failures.timelineFailure(new ApiError(408, "query_timeout", "slow"));
  assert.equal(r.live().length, 1);
  const retrying = r.failures.retry();
  await flush();
  assert.equal(r.live().length, 0);
  r.failures.timelineFailure(new ApiError(408, "query_timeout", "slow again"));
  r.timelineGate.current.resolve();
  await retrying;
  assert.equal(r.live().at(-1).text, "slow again");
});

test("the real timeline: a case switch drops its request in flight; a late failure registers nothing; refreshNow resolves at the end", async () => {
  const { installGlobalDom } = await import("./fixtures/fake-dom.mjs");
  const doc = installGlobalDom();
  globalThis.getComputedStyle = () => ({ getPropertyValue: () => "" });
  const { initTimeline } = await import("../../src/dmslog/web/js/timeline.js");
  class U {
    static paths = { bars: () => ({}), stepped: () => ({}) };
    static tzDate(d) { return d; }
    constructor(o, c, host) { this.over = new (doc.body.constructor)(doc, "div"); this.select = {}; host.appendChild(this.over); }
    setSize() {} setSelect() {} valToPos(v) { return v; } posToVal(v) { return v; } destroy() {}
  }
  const calls = [];
  const events = [];
  const api2 = { histogram: (...args) => { const d = deferred(); calls.push(d); return d.promise; } };
  const host = doc.createElement("div");
  doc.body.appendChild(host);
  const timeline = initTimeline(host, {
    api: api2, uplot: U, getFilter: () => ({ caseId: "C", filter: null }), onRangeSelected() {},
    onFailure: (e) => events.push("fail"), onRecovered: () => events.push("ok"),
  });
  const running = timeline.refreshNow();
  assert.equal(calls.length, 1);
  timeline.invalidate(); // another case
  calls[0].reject(new ApiError(0, "network", "late"));
  await running;
  assert.deepEqual(events, []);
  assert.equal(timeline.hasFailure(), false);
  let done = false;
  const again = timeline.refreshNow().then(() => { done = true; });
  await flush();
  assert.equal(done, false); // waits for the request
  calls[1].reject(new ApiError(408, "query_timeout", "slow"));
  await again;
  assert.equal(done, true);
  assert.deepEqual(events, ["fail"]);
  timeline.destroy();
});
