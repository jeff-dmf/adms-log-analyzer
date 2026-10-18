// Follow tail with a hidden tab: the stream stays open, but nothing is counted and /sources is not asked;
// ONE update runs when the tab is back. Fake document, EventSource, fetch and timers.
import test from "node:test";
import assert from "node:assert/strict";

import { installGlobalDom } from "./fixtures/fake-dom.mjs";

function fakeTimers() {
  let now = 0;
  let nextId = 1;
  const queue = new Map();
  return {
    now: () => now,
    setTimeout(fn, ms) { const id = nextId++; queue.set(id, { at: now + ms, fn }); return id; },
    clearTimeout(id) { queue.delete(id); },
    size: () => queue.size,
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

async function setup({ atEnd = false } = {}) {
  const doc = installGlobalDom();
  doc.hidden = false;
  const streams = [];
  class FakeEventSource {
    constructor(url) { this.url = url; this.handlers = {}; this.closed = false; streams.push(this); }
    addEventListener(name, fn) { (this.handlers[name] ??= []).push(fn); }
    close() { this.closed = true; }
    emit(name, data) { for (const fn of this.handlers[name] || []) fn(data); }
  }
  globalThis.EventSource = FakeEventSource;
  const urls = [];
  let total = 1000;
  globalThis.fetch = async (url) => {
    urls.push(String(url));
    return { status: 200, ok: true, json: async () => ({ count: total, exact: true }) };
  };
  const { initFollowTail } = await import("../../src/dmslog/web/js/follow-tail.js");
  const timers = fakeTimers();
  const toggle = doc.createElement("input");
  toggle.type = "checkbox";
  const newButton = doc.createElement("button");
  const changed = [];
  const gridCalls = { followTail: 0, shown: [] };
  const info = atEnd ? { atEnd: true, top: 180, visible: 20, length: 200 } : { atEnd: true, top: 100, visible: 20, length: 200 };
  const grid = {
    total: 1000,
    tailInfo: () => info,
    followTail: async () => { gridCalls.followTail += 1; },
    showTotal(n, exact) { grid.total = n; gridCalls.shown.push({ n, exact }); return true; },
  };
  const follow = initFollowTail({
    toggle, newButton, grid, getCase: () => "C", getFilter: () => null, doc, timers,
    onChanged: (arg) => changed.push(arg), onError: (err) => { throw err; },
  });
  follow.onCase();
  toggle.checked = true;
  toggle.dispatch("change");
  await Promise.resolve();
  const sent = (data) => streams.at(-1).emit("watch", { data: JSON.stringify(data) });
  const hide = () => { doc.hidden = true; (doc.listeners.visibilitychange || []).forEach((fn) => fn({})); };
  const show = () => { doc.hidden = false; (doc.listeners.visibilitychange || []).forEach((fn) => fn({})); };
  const settle = async () => { for (let i = 0; i < 10; i += 1) await Promise.resolve(); };
  return { doc, streams, urls, timers, follow, grid, gridCalls, changed, sent, hide, show, settle, setTotal: (n) => { total = n; }, newButton, toggle };
}

const cleanup = () => { delete globalThis.EventSource; delete globalThis.fetch; };

test("turning Follow tail on goes to the end even when the view was scrolled up", async () => {
  const t = await setup();
  assert.equal(t.gridCalls.followTail, 1);
  assert.equal(t.streams.length, 1);
  cleanup();
});

test("hidden tab: events open no /count and no /sources; the stream stays open; one update when back", async () => {
  const t = await setup();
  t.hide();
  t.urls.length = 0;
  for (let i = 0; i < 20; i += 1) {
    t.sent({ records: 5, sources: i === 3 ? [{ action: "new" }] : [{ action: "append" }] });
    t.timers.advance(3000);
  }
  await t.settle();
  assert.deepEqual(t.urls, []);
  assert.deepEqual(t.changed, []);
  assert.equal(t.streams[0].closed, false);
  assert.equal(t.timers.size(), 0); // no timer left running
  t.setTotal(1500);
  t.show();
  await t.settle();
  assert.equal(t.urls.filter((u) => u.endsWith("/api/entries/count")).length, 1);
  assert.deepEqual(t.changed, [{ sources: true }]);
  assert.equal(t.grid.total, 1500);
  t.show(); // a second visibilitychange without a gap changes nothing
  await t.settle();
  assert.equal(t.changed.length, 1);
  cleanup();
});

test("hiding with a debounce pending cancels it and updates once when back", async () => {
  const t = await setup();
  t.sent({ records: 1, sources: [] });
  assert.ok(t.timers.size() > 0);
  t.hide();
  assert.equal(t.timers.size(), 0);
  t.timers.advance(10000);
  await t.settle();
  assert.deepEqual(t.urls, []);
  t.show();
  await t.settle();
  assert.equal(t.changed.length, 1);
  cleanup();
});

test("visible: a burst of events makes one count and one derived update; /sources only for a new source", async () => {
  const t = await setup();
  t.setTotal(1200);
  for (let i = 0; i < 5; i += 1) {
    t.sent({ records: 5, sources: [{ action: "append" }] });
    t.timers.advance(100);
  }
  t.timers.advance(10000);
  await t.settle();
  assert.equal(t.urls.filter((u) => u.endsWith("/api/entries/count")).length, 1);
  assert.deepEqual(t.changed, [{ sources: false }]);
  assert.equal(t.grid.total, 1200);
  assert.equal(t.newButton.textContent, "200 new records");
  t.sent({ records: 5, sources: [{ action: "new" }] });
  t.timers.advance(10000);
  await t.settle();
  assert.deepEqual(t.changed.at(-1), { sources: true });
  cleanup();
});

test("a count in flight when the tab hides is dropped and does not write the total", async () => {
  const t = await setup();
  let release;
  globalThis.fetch = (url, options) => new Promise((resolve, reject) => {
    t.urls.push(String(url));
    release = () => resolve({ status: 200, ok: true, json: async () => ({ count: 9999, exact: true }) });
    options.signal.addEventListener("abort", () => reject(Object.assign(new Error("aborted"), { name: "AbortError" })));
  });
  t.sent({ records: 1, sources: [] });
  t.timers.advance(1000);
  await t.settle();
  assert.equal(t.urls.length, 1);
  t.hide();
  release();
  await t.settle();
  assert.equal(t.grid.total, 1000);
  assert.deepEqual(t.gridCalls.shown, []);
  cleanup();
});

test("destroy removes the listener and the stream; a later visibility change does nothing", async () => {
  const t = await setup();
  t.follow.destroy();
  assert.equal(t.streams[0].closed, true);
  assert.equal((t.doc.listeners.visibilitychange || []).length, 0);
  assert.equal(t.timers.size(), 0);
  cleanup();
});

test("destroy removes the toggle and button listeners and the instance is inert", async () => {
  const t = await setup();
  t.follow.destroy();
  const streamsBefore = t.streams.length;
  const callsBefore = t.gridCalls.followTail;
  t.toggle.checked = false;
  t.toggle.dispatch("change");
  t.toggle.checked = true;
  t.toggle.dispatch("change");
  t.newButton.dispatch("click");
  t.follow.onCase();
  assert.equal(t.follow.resume(), false);
  await t.settle();
  assert.equal(t.streams.length, streamsBefore); // no stream was opened again
  assert.equal(t.gridCalls.followTail, callsBefore);
  assert.equal((t.toggle.listeners?.change || []).length, 0);
  assert.equal((t.newButton.listeners?.click || []).length, 0);
  cleanup();
});
