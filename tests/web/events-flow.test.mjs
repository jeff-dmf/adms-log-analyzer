// The Operations panel flow and the clock-jump flow with a fake api: no DOM, no real requests.
import test from "node:test";
import assert from "node:assert/strict";

import { createClockJumpFlow, createEventsFlow } from "../../src/dmslog/web/js/events-model.js";

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

const tick = () => new Promise((resolve) => setImmediate(resolve));
const ev = (id, extra = {}) => ({ id, kind: "operation", name: "Op" + id, ts_start: id * 1000, ...extra });

function setup() {
  const calls = [];
  const states = [];
  const current = { caseId: "A" };
  const api = {
    events(caseId, params, signal) {
      const d = deferred();
      calls.push({ kind: "events", caseId, params, signal, d });
      return d.promise;
    },
    event(caseId, id, signal) {
      const d = deferred();
      calls.push({ kind: "event", caseId, id, signal, d });
      return d.promise;
    },
  };
  const flow = createEventsFlow({
    api, getCase: () => current.caseId, onState: (s) => states.push(JSON.parse(JSON.stringify(s))), limit: 2,
  });
  return { flow, calls, states, current };
}

test("first page, keyset load more and duplicate ids", async () => {
  const { flow, calls } = setup();
  const loading = flow.refresh();
  assert.equal(flow.state.status, "loading");
  assert.deepEqual(calls[0].params, { kind: "operation", limit: 2 });
  assert.equal(calls[0].caseId, "A");
  calls[0].d.resolve({ events: [ev(1), ev(2)], next: "tok-2" });
  await loading;
  assert.equal(flow.state.status, "ready");
  assert.equal(flow.state.next, "tok-2");

  const more = flow.loadMore();
  assert.equal(flow.state.loadingMore, true);
  assert.equal(calls[1].params.after, "tok-2");
  flow.loadMore(); // a second click while loading does nothing
  assert.equal(calls.length, 2);
  calls[1].d.resolve({ events: [ev(2), ev(3)], next: null });
  await more;
  assert.deepEqual(flow.state.events.map((e) => e.id), [1, 2, 3]);
  assert.equal(flow.state.next, null);
  assert.equal(flow.state.loadingMore, false);
  await flow.loadMore(); // nothing left
  assert.equal(calls.length, 2);
});

test("changing a filter restarts from the first page and drops the old answer", async () => {
  const { flow, calls } = setup();
  const first = flow.refresh();
  const second = flow.setFilters({ status: "fail", name: "Op1" });
  assert.equal(calls[0].signal.aborted, true);
  assert.equal(calls[1].params.status, "fail");
  assert.equal(calls[1].params.name, "Op1");
  calls[0].d.resolve({ events: [ev(1)], next: null }); // stale
  await first;
  assert.deepEqual(flow.state.events, []);
  assert.equal(flow.state.status, "loading");
  calls[1].d.resolve({ events: [ev(9)], next: null });
  await second;
  assert.deepEqual(flow.state.events.map((e) => e.id), [9]);
});

test("a stale page of load more is dropped after a refresh", async () => {
  const { flow, calls } = setup();
  const first = flow.refresh();
  calls[0].d.resolve({ events: [ev(1)], next: "t" });
  await first;
  const more = flow.loadMore();
  const again = flow.refresh();
  calls[1].d.resolve({ events: [ev(50)], next: null }); // answer of the old load more
  await more;
  assert.deepEqual(flow.state.events, []);
  calls[2].d.resolve({ events: [ev(2)], next: null });
  await again;
  assert.deepEqual(flow.state.events.map((e) => e.id), [2]);
  assert.equal(flow.state.loadingMore, false);
});

test("switching the case clears list and detail before the new request", async () => {
  const { flow, calls, current } = setup();
  const first = flow.refresh();
  calls[0].d.resolve({ events: [ev(1)], next: null });
  await first;
  const picked = flow.select(1);
  calls[1].d.resolve({ event: ev(1), entries: [], entries_total: 0, related: [] });
  await picked;
  assert.equal(flow.state.detail.status, "ready");

  current.caseId = "B";
  const second = flow.refresh();
  assert.deepEqual(flow.state.events, []);
  assert.equal(flow.state.selectedId, null);
  assert.equal(flow.state.detail.status, "none");
  assert.equal(flow.state.caseId, "B");
  assert.equal(calls[2].caseId, "B");
  calls[2].d.resolve({ events: [], next: null });
  await second;
  assert.equal(flow.state.status, "ready");
});

test("no case: nothing is requested", async () => {
  const { flow, calls, current } = setup();
  current.caseId = null;
  await flow.refresh();
  assert.equal(flow.state.status, "nocase");
  assert.equal(calls.length, 0);
  await flow.select(1);
  assert.equal(calls.length, 0);
});

test("detail: a newer selection wins, a late older answer is ignored", async () => {
  const { flow, calls } = setup();
  const first = flow.refresh();
  calls[0].d.resolve({ events: [ev(1), ev(2)], next: null });
  await first;
  const a = flow.select(1);
  const b = flow.select(2);
  assert.equal(calls[1].signal.aborted, true);
  calls[1].d.resolve({ event: ev(1), entries: [], entries_total: 0, related: [] });
  await a;
  assert.equal(flow.state.detail.status, "loading");
  calls[2].d.resolve({ event: ev(2), entries: [], entries_total: 0, related: [] });
  await b;
  assert.equal(flow.state.selectedId, 2);
  assert.equal(flow.state.detail.data.event.id, 2);
});

test("a refresh drops the detail answer still in flight", async () => {
  const { flow, calls } = setup();
  const first = flow.refresh();
  calls[0].d.resolve({ events: [ev(1)], next: null });
  await first;
  const pending = flow.select(1);
  const again = flow.refresh();
  calls[1].d.resolve({ event: ev(1), entries: [], entries_total: 0, related: [] });
  await pending;
  assert.equal(flow.state.detail.status, "none");
  calls[2].d.resolve({ events: [], next: null });
  await again;
});

test("errors are reported and aborts are not", async () => {
  const { flow, calls } = setup();
  const first = flow.refresh();
  calls[0].d.reject(new Error("boom"));
  await first;
  assert.equal(flow.state.status, "error");
  assert.equal(flow.state.error, "boom");
  const second = flow.refresh();
  const third = flow.refresh();
  calls[1].d.reject(Object.assign(new Error("aborted"), { name: "AbortError" }));
  await second;
  assert.equal(flow.state.status, "loading");
  calls[2].d.resolve({ events: [], next: null });
  await third;
});

function clockSetup(answers) {
  const calls = [];
  const current = { caseId: "A" };
  const api = {
    async events(caseId, params) {
      calls.push({ caseId, params });
      await tick();
      return answers[params.kind] || { events: [], next: null };
    },
  };
  const flow = createClockJumpFlow({ api, getCase: () => current.caseId, onState: () => {} });
  return { flow, calls, current };
}

test("clock flow: one small request when a clock_jump exists", async () => {
  const { flow, calls } = clockSetup({ clock_jump: { events: [ev(1, { kind: "clock_jump", ts_start: 5 })], next: null } });
  await flow.refresh();
  assert.equal(calls.length, 1);
  assert.equal(calls[0].params.kind, "clock_jump");
  assert.equal(calls[0].caseId, "A");
  assert.equal(flow.state.summary.kind, "jump");
  assert.equal(flow.state.summary.first, 5);
});

test("clock flow: falls back to gaps flagged as a possible clock change", async () => {
  const gap = ev(3, { kind: "gap", ts_start: 9, detail: { possible_clock_jump: true } });
  const { flow, calls } = clockSetup({ gap: { events: [gap, ev(4, { kind: "gap", detail: {} })], next: null } });
  await flow.refresh();
  assert.deepEqual(calls.map((c) => c.params.kind), ["clock_jump", "gap"]);
  assert.equal(flow.state.summary.kind, "possible");
});

test("clock flow: nothing to warn about, and no request without a case", async () => {
  const { flow, calls, current } = clockSetup({});
  await flow.refresh();
  assert.equal(flow.state.summary, null);
  current.caseId = null;
  const before = calls.length;
  await flow.refresh();
  assert.equal(calls.length, before);
  assert.equal(flow.state.summary, null);
});

test("clock flow: an answer of the previous case never shows up", async () => {
  const calls = [];
  const current = { caseId: "A" };
  const api = {
    events(caseId, params) {
      const d = deferred();
      calls.push({ caseId, params, d });
      return d.promise;
    },
  };
  const flow = createClockJumpFlow({ api, getCase: () => current.caseId, onState: () => {} });
  const first = flow.refresh();
  current.caseId = "B";
  const second = flow.refresh();
  calls[0].d.resolve({ events: [ev(1, { kind: "clock_jump", ts_start: 1 })], next: null });
  await first;
  assert.equal(flow.state.summary, null);
  assert.equal(flow.state.caseId, "B");
  calls[1].d.resolve({ events: [], next: null });
  await tick(); // the gap scan of case B starts after the clock_jump answer
  calls[2].d.resolve({ events: [], next: null });
  await second;
  assert.equal(flow.state.summary, null);
});
