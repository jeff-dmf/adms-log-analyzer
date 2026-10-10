// Follow tail flow with a fake api and a fake grid view: no DOM, no real requests.
import test from "node:test";
import assert from "node:assert/strict";

import { createDebounce, createFollowFlow } from "../../src/dmslog/web/js/follow-tail-model.js";

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

function setup(info = { atEnd: true, top: 180, visible: 20, length: 200 }) {
  const counts = [];
  const states = [];
  const calls = { followTail: 0 };
  const current = { caseId: "A", info: { ...info }, total: 1000 };
  const api = {
    count(caseId, filter, signal) {
      const d = deferred();
      counts.push({ caseId, filter, signal, d });
      return d.promise;
    },
  };
  const flow = createFollowFlow({
    api,
    getCase: () => current.caseId,
    getFilter: () => ({ and: [] }),
    view: {
      tailInfo: () => current.info,
      followTail: async () => { calls.followTail += 1; },
      total: () => current.total,
    },
    onState: (s) => states.push(s),
  });
  return { flow, counts, states, calls, current };
}

test("nothing happens while it is off", async () => {
  const { flow, calls, counts } = setup();
  await flow.refresh();
  assert.equal(calls.followTail, 0);
  assert.equal(counts.length, 0);
});

test("at the bottom the tail is reloaded and the counter stays at zero", async () => {
  const { flow, calls, counts, states } = setup();
  flow.setOn(true);
  await flow.refresh();
  assert.equal(calls.followTail, 1);
  assert.equal(counts.length, 0);
  assert.equal(states.at(-1).pending, 0);
});

test("scrolled up the view is left alone and the new records are counted", async () => {
  const { flow, calls, counts, states, current } = setup({ atEnd: true, top: 100, visible: 20, length: 200 });
  flow.setOn(true);
  const refreshing = flow.refresh();
  assert.equal(counts.length, 1);
  assert.equal(counts[0].caseId, "A");
  counts[0].d.resolve({ count: 1042, exact: true });
  await refreshing;
  assert.equal(calls.followTail, 0);
  assert.equal(flow.state.pending, 42);
  assert.equal(states.at(-1).pending, 42);
  // Back at the end of the OLD buffer: the tail is loaded first, only then does the counter clear.
  current.info = { atEnd: true, top: 180, visible: 20, length: 200 };
  flow.viewChanged();
  assert.equal(calls.followTail, 1);
  assert.equal(flow.state.pending, 42);
  await Promise.resolve();
  await Promise.resolve();
  assert.equal(flow.state.pending, 0);
});

test("returning to the bottom with nothing pending does not reload", () => {
  const { flow, calls, current } = setup({ atEnd: true, top: 100, visible: 20, length: 200 });
  flow.setOn(true);
  current.info = { atEnd: true, top: 180, visible: 20, length: 200 };
  flow.viewChanged();
  assert.equal(calls.followTail, 0);
});

test("a stale count answer is dropped after a newer refresh", async () => {
  const { flow, counts } = setup({ atEnd: false, top: 0, visible: 20, length: 200 });
  flow.setOn(true);
  const first = flow.refresh();
  const second = flow.refresh();
  assert.equal(counts[0].signal.aborted, true);
  counts[0].d.resolve({ count: 5000 });
  await first;
  assert.equal(flow.state.pending, 0);
  counts[1].d.resolve({ count: 1010 });
  await second;
  assert.equal(flow.state.pending, 10);
});

test("an answer for another case or after switching off is dropped", async () => {
  const { flow, counts, current } = setup({ atEnd: true, top: 0, visible: 20, length: 200 });
  flow.setOn(true);
  const first = flow.refresh();
  current.caseId = "B";
  counts[0].d.resolve({ count: 1500 });
  await first;
  assert.equal(flow.state.pending, 0);
  current.caseId = "A";
  const second = flow.refresh();
  flow.setOn(false);
  counts[1].d.resolve({ count: 1500 });
  await second;
  assert.equal(flow.state.pending, 0);
});

test("a failed count keeps the previous number", async () => {
  const { flow, counts } = setup({ atEnd: false, top: 0, visible: 20, length: 200 });
  flow.setOn(true);
  const refreshing = flow.refresh();
  counts[0].d.reject(new Error("network"));
  await refreshing;
  assert.equal(flow.state.pending, 0);
});

test("changing case resets the counter", () => {
  const { flow, states } = setup({ atEnd: false, top: 0, visible: 20, length: 200 });
  flow.setOn(true);
  flow.state.pending = 7;
  flow.reset();
  assert.equal(states.at(-1).pending, 0);
});

function fakeTimers() {
  let now = 0;
  let nextId = 1;
  const queue = new Map();
  return {
    now: () => now,
    setTimeout(fn, ms) {
      const id = nextId++;
      queue.set(id, { at: now + ms, fn });
      return id;
    },
    clearTimeout(id) { queue.delete(id); },
    advance(ms) {
      const end = now + ms;
      for (;;) {
        const due = [...queue.entries()].filter(([, t]) => t.at <= end).sort((a, b) => a[1].at - b[1].at)[0];
        if (!due) break;
        queue.delete(due[0]);
        now = due[1].at;
        due[1].fn();
      }
      now = end;
    },
  };
}

test("a debounce fires after the quiet period", () => {
  const timers = fakeTimers();
  let runs = 0;
  const d = createDebounce({ run: () => { runs += 1; }, wait: 400, maxWait: 2000, timers });
  d.trigger();
  timers.advance(399);
  assert.equal(runs, 0);
  timers.advance(1);
  assert.equal(runs, 1);
  assert.equal(d.pending(), false);
});

test("continuous events every 200 ms still fire within the maximum wait", () => {
  const timers = fakeTimers();
  const firedAt = [];
  const d = createDebounce({ run: () => firedAt.push(timers.now()), wait: 400, maxWait: 2000, timers });
  for (let t = 0; t < 7000; t += 200) {
    d.trigger();
    timers.advance(200);
  }
  assert.ok(firedAt.length >= 3, String(firedAt));
  assert.ok(firedAt[0] <= 2000);
  assert.ok(firedAt.every((at, i) => i === 0 || at - firedAt[i - 1] <= 2200));
});

test("the slower derived debounce is bounded by its own maximum wait", () => {
  const timers = fakeTimers();
  let runs = 0;
  const d = createDebounce({ run: () => { runs += 1; }, wait: 1500, maxWait: 6000, timers });
  for (let t = 0; t < 6000; t += 1000) {
    d.trigger();
    timers.advance(1000);
  }
  assert.equal(runs, 1);
});

test("cancel drops a pending run", () => {
  const timers = fakeTimers();
  let runs = 0;
  const d = createDebounce({ run: () => { runs += 1; }, wait: 100, maxWait: 500, timers });
  d.trigger();
  d.cancel();
  timers.advance(1000);
  assert.equal(runs, 0);
});
