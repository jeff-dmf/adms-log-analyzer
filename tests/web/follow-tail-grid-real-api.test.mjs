// Follow tail on the grid with the REAL api layer (only fetch is faked): an obsolete operation's late failures
// never reach the global handlers (Disconnected / session), and a wheel gesture ends the end-of-data load.
import test from "node:test";
import assert from "node:assert/strict";

function fakeElement() {
  const listeners = {};
  return {
    style: {}, dataset: {}, children: [], hidden: false, clientHeight: 440, scrollTop: 0, className: "",
    classList: { toggle() {} }, listeners,
    addEventListener(type, fn) { (listeners[type] ??= []).push(fn); }, appendChild(c) { this.children.push(c); return c; },
    setAttribute() {}, removeAttribute() {}, focus() {},
    set textContent(v) { this.children = []; this._t = v; }, get textContent() { return this._t || ""; },
  };
}
globalThis.document = { createElement: fakeElement };

const { Grid } = await import("../../src/dmslog/web/js/grid.js");
const { onForbidden, onNetworkError } = await import("../../src/dmslog/web/js/api.js");

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

const ok = (data) => ({ status: 200, ok: true, json: async () => data });

async function setup() {
  const pending = []; // page requests held by the test
  globalThis.fetch = (url) => {
    if (String(url).includes("count")) {
      return Promise.resolve(ok({ count: 10, exact: true }));
    }
    const d = deferred();
    pending.push(d);
    return d.promise;
  };
  const handlers = { forbidden: 0, network: 0 };
  onForbidden(() => { handlers.forbidden += 1; });
  onNetworkError(() => { handlers.network += 1; });
  const frames = [];
  const viewport = fakeElement();
  const grid = new Grid({
    viewport, rowsLayer: fakeElement(), spacer: fakeElement(), header: fakeElement(),
    onSelect() {}, onCount() {}, onPosition() {}, onError() {}, onFailure() {},
    requestFrame: (fn) => { frames.push(fn); return frames.length; }, cancelFrame() {},
  });
  const first = grid.setView({ caseId: "A", filter: null, range: null });
  for (let i = 0; i < 10; i += 1) await Promise.resolve();
  pending.shift().resolve(ok({ rows: [], prev: null, next: null }));
  await first;
  return { grid, pending, handlers, viewport, frames };
}

test("late HTTP 403 of a cancelled operation does not raise the session notice", async () => {
  const t = await setup();
  let alive = true;
  const running = t.grid.followTail({ isCurrent: () => alive });
  await Promise.resolve();
  alive = false; // the tab hid
  t.pending.shift().resolve({ status: 403, ok: false, json: async () => ({ error: "forbidden" }) });
  assert.equal(await running, false);
  assert.equal(t.handlers.forbidden, 0);
  assert.equal(t.grid.failure, null);
});

test("late network failure of a cancelled operation does not raise Disconnected; a current one does", async () => {
  const t = await setup();
  let alive = true;
  const running = t.grid.followTail({ isCurrent: () => alive });
  await Promise.resolve();
  alive = false;
  t.pending.shift().reject(new TypeError("fetch failed"));
  assert.equal(await running, false);
  assert.equal(t.handlers.network, 0);
  assert.equal(t.grid.failure, null);
  // control: the same failure for a current operation is reported
  const current = t.grid.followTail({ isCurrent: () => true });
  await Promise.resolve();
  t.pending.shift().reject(new TypeError("fetch failed"));
  await current;
  assert.equal(t.handlers.network, 1);
});

test("a vertical wheel gesture ends the end-of-data load when it is received, not when its frame runs", async () => {
  const t = await setup();
  let replaced = 0;
  const original = t.grid.window.load.bind(t.grid.window);
  t.grid.window.load = (...args) => { replaced += 1; return original(...args); };
  const running = t.grid.followTail({ isCurrent: () => true });
  await Promise.resolve();
  for (const fn of t.viewport.listeners.wheel) fn({ deltaY: -300, deltaX: 0, deltaMode: 0, preventDefault() {} });
  assert.equal(t.frames.length, 1); // the move itself waits for the frame
  t.pending.shift().resolve(ok({ rows: [{ id: 1, ts: 1 }], prev: null, next: null }));
  assert.equal(await running, false);
  assert.equal(replaced, 0);
  assert.notEqual(t.grid.wheelFrame, null); // the frame is not cancelled by the page
});
