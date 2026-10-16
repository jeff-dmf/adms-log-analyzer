// UI-17: the drawers module loads on demand and recovers from a failed import(); "New case" and "Add files" open the
// sources drawer first (below 1600 px the form would be revealed inside a hidden panel).
import test from "node:test";
import assert from "node:assert/strict";

import { afterDrawerOpen, openWhenLoaded, retryable } from "../../src/dmslog/web/js/late-init.js";

function loader(failures) {
  const calls = { loads: 0, opens: [] };
  const drawers = { open: (which) => calls.opens.push(which) };
  let left = failures;
  const load = retryable(() => {
    calls.loads += 1;
    if (left > 0) {
      left -= 1;
      return Promise.reject(new Error("chunk failed"));
    }
    state.drawers = drawers;
    return Promise.resolve(drawers);
  });
  const state = { drawers: null };
  return { calls, load, state, current: () => state.drawers };
}

test("a failed import is reported and the next use of the control tries again", async () => {
  const t = loader(1);
  const errors = [];
  await openWhenLoaded({ current: t.current, load: t.load, which: "side", onError: (e) => errors.push(e.message) });
  assert.deepEqual(errors, ["chunk failed"]);
  assert.deepEqual(t.calls.opens, []);
  await openWhenLoaded({ current: t.current, load: t.load, which: "side", onError: (e) => errors.push(e.message) });
  assert.equal(t.calls.loads, 2, "retried, not stuck on the first rejection");
  assert.deepEqual(t.calls.opens, ["side"]);
  assert.equal(errors.length, 1);
  await openWhenLoaded({ current: t.current, load: t.load, which: "side", onError: (e) => errors.push(e.message) });
  assert.equal(t.calls.loads, 2, "once it is in the module's own handler works; nothing loads again");
  assert.deepEqual(t.calls.opens, ["side"]);
});

test("concurrent first uses share one load", async () => {
  const t = loader(0);
  await Promise.all([1, 2, 3].map(() => openWhenLoaded({ current: t.current, load: t.load, which: "detail", onError: () => {} })));
  assert.equal(t.calls.loads, 1);
});

test("New case / Add files: the drawer opens first, then the action runs", async () => {
  const t = loader(0);
  const order = [];
  const action = () => { order.push("action"); return "done"; };
  const first = afterDrawerOpen({ current: t.current, load: t.load, which: "side", action, onError: () => {} });
  assert.ok(first instanceof Promise, "the module is not in yet");
  assert.equal(await first, "done");
  assert.deepEqual(t.calls.opens, ["side"]);
  assert.deepEqual(order, ["action"]);
  // module in: synchronous, the open happens before the action in the same turn
  t.calls.opens.length = 0;
  const log = [];
  const sync = afterDrawerOpen({ current: t.current, load: t.load, which: "side", action: () => { log.push(t.calls.opens.length); return 7; }, onError: () => {} });
  assert.equal(sync, 7);
  assert.deepEqual(log, [1], "already open when the action focuses its field");
});

test("a failed load never loses the action", async () => {
  const t = loader(1);
  const errors = [];
  let ran = 0;
  await afterDrawerOpen({ current: t.current, load: t.load, which: "side", action: () => { ran += 1; }, onError: (e) => errors.push(e.message) });
  assert.equal(ran, 1);
  assert.deepEqual(errors, ["chunk failed"]);
});
