// 9.12 UI: the repeat model (window, filter node, preferences, marks) and its store/controller, with a fake api whose
// answers the test releases by hand (no waiting on the wall clock).
import test from "node:test";
import assert from "node:assert/strict";

import {
  DEFAULT_WINDOW_MS, MARK_BATCH, MAX_WINDOW_MS, MAX_KNOWN, createPrefs, createRepeatStore, createRepeats, earlierLabel, findHide, hideNode,
  hideSpec, markLabel, normalizeWindow, parseMark, parsePrefs, repeatSpec, spanText, windowChoices, withHide, withoutHide,
} from "../../src/dmslog/web/js/repeat-model.js";

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}
const tick = () => new Promise((resolve) => setImmediate(resolve));
const abortError = () => Object.assign(new Error("aborted"), { name: "AbortError" });

// ---- the window --------------------------------------------------------------------------------------------------

test("normalizeWindow: 0 is a window; NaN, null, text, fractions, negatives and values above the maximum are not", () => {
  assert.equal(normalizeWindow(0), 0);
  assert.equal(normalizeWindow(1), 1);
  assert.equal(normalizeWindow(MAX_WINDOW_MS), MAX_WINDOW_MS);
  for (const bad of [NaN, Infinity, -1, 1.5, MAX_WINDOW_MS + 1, 2 ** 53, null, undefined, "1000", true, {}, []]) {
    assert.equal(normalizeWindow(bad), null, String(bad));
  }
});

test("windowChoices adds a window that is not a preset, in order, and ignores an invalid one", () => {
  assert.deepEqual(windowChoices(1000), [100, 500, 1000, 5000, 30000, 60000]);
  assert.deepEqual(windowChoices(2500), [100, 500, 1000, 2500, 5000, 30000, 60000]);
  assert.deepEqual(windowChoices(0), [0, 100, 500, 1000, 5000, 30000, 60000]);
  assert.deepEqual(windowChoices(NaN), [100, 500, 1000, 5000, 30000, 60000]);
});

test("spanText says the window in words and nothing for an invalid one", () => {
  assert.equal(spanText(0), "0 ms");
  assert.equal(spanText(250), "250 ms");
  assert.equal(spanText(1000), "1 s");
  assert.equal(spanText(60000), "1 min");
  assert.equal(spanText(90000), "1 min 30 s");
  assert.equal(spanText(120000), "2 min");
  assert.equal(spanText(3600000), "1 h");
  assert.equal(spanText(NaN), "");
  assert.equal(spanText(-5), "");
});

// ---- the filter node ---------------------------------------------------------------------------------------------

test("hideNode and the readers agree; a leaf with fields, tid or a bad window is not ours", () => {
  assert.deepEqual(hideNode(500), { not: { repeat: { window_ms: 500 } } });
  assert.deepEqual(hideNode(NaN), { not: { repeat: { window_ms: DEFAULT_WINDOW_MS } } });
  assert.deepEqual(hideSpec(hideNode(500)), { ms: 500 });
  assert.deepEqual(hideSpec({ not: { repeat: {} } }), { ms: DEFAULT_WINDOW_MS });
  assert.deepEqual(hideSpec({ not: { repeat: { window_ms: 0 } } }), { ms: 0 });
  assert.equal(hideSpec({ not: { repeat: { window_ms: 5, fields: ["a"] } } }), null);
  assert.equal(hideSpec({ not: { repeat: { tid: true } } }), null);
  assert.equal(hideSpec({ not: { repeat: { window_ms: -1 } } }), null);
  assert.equal(hideSpec({ not: { repeat: { window_ms: "5" } } }), null);
  assert.equal(hideSpec({ not: { sev: { le: 3 } } }), null);
  assert.equal(hideSpec({ repeat: {} }), null);
  assert.equal(hideSpec(null), null);
  assert.deepEqual(repeatSpec({ repeat: { window_ms: 2000 } }), { ms: 2000 });
  assert.equal(repeatSpec({ repeat: [] }), null);
});

test("findHide looks at the top-level conditions only", () => {
  const sev = { sev: { le: 3 } };
  assert.equal(findHide(null), null);
  assert.equal(findHide(sev), null);
  assert.deepEqual(findHide(hideNode(500)), { index: 0, ms: 500 });
  assert.deepEqual(findHide({ and: [sev, hideNode(500)] }), { index: 1, ms: 500 });
  assert.equal(findHide({ and: [{ or: [hideNode(500), sev] }] }), null, "inside an or: not ours");
});

test("withHide adds the one node, replaces another and keeps the rest; withoutHide takes every one out; neither mutates", () => {
  const sev = { sev: { le: 3 } };
  const base = { and: [sev] };
  const snapshot = JSON.stringify(base);
  assert.deepEqual(withHide(null, 1000), { and: [hideNode(1000)] });
  assert.deepEqual(withHide(sev, 1000), { and: [sev, hideNode(1000)] });
  assert.deepEqual(withHide({ and: [hideNode(100), sev, hideNode(500)] }, 5000), { and: [sev, hideNode(5000)] });
  assert.deepEqual(withoutHide({ and: [hideNode(100), sev, hideNode(500)] }), { and: [sev] });
  assert.equal(withoutHide(hideNode(100)), null);
  assert.equal(withoutHide(null), null);
  withHide(base, 10);
  withoutHide(base);
  assert.equal(JSON.stringify(base), snapshot);
  // a spec with fields is somebody else's: it stays when ours goes
  const mine = { not: { repeat: { fields: ["id"] } } };
  assert.deepEqual(withoutHide({ and: [mine, hideNode(1000)] }), { and: [mine] });
});

// ---- the preferences ---------------------------------------------------------------------------------------------

test("parsePrefs keeps what is valid and falls back for the rest", () => {
  assert.deepEqual(parsePrefs(null), { mark: true, windowMs: 1000 });
  assert.deepEqual(parsePrefs("not json"), { mark: true, windowMs: 1000 });
  assert.deepEqual(parsePrefs("[1]"), { mark: true, windowMs: 1000 });
  assert.deepEqual(parsePrefs(JSON.stringify({ mark: false, windowMs: 500 })), { mark: false, windowMs: 500 });
  assert.deepEqual(parsePrefs(JSON.stringify({ mark: "no", windowMs: "5" })), { mark: true, windowMs: 1000 });
  assert.deepEqual(parsePrefs(JSON.stringify({ windowMs: 0 })), { mark: true, windowMs: 0 });
  assert.deepEqual(parsePrefs(JSON.stringify({ windowMs: MAX_WINDOW_MS + 1 })), { mark: true, windowMs: 1000 });
});

test("createPrefs survives a storage that throws on read and on write, and remembers in session", () => {
  const throwing = { getItem() { throw new Error("blocked"); }, setItem() { throw new Error("blocked"); } };
  const prefs = createPrefs(throwing);
  assert.equal(prefs.mark(), true);
  prefs.setMark(false);
  prefs.setWindow(250);
  assert.equal(prefs.mark(), false);
  assert.equal(prefs.windowMs(), 250);
  prefs.setWindow(NaN);
  prefs.setWindow(-3);
  assert.equal(prefs.windowMs(), 250, "an invalid window changes nothing");
  assert.equal(createPrefs(null).mark(), true);
  const saved = {};
  const ok = createPrefs({ getItem: (k) => saved[k] ?? null, setItem: (k, v) => { saved[k] = v; } });
  ok.setWindow(5000);
  assert.equal(createPrefs({ getItem: (k) => saved[k] ?? null, setItem() {} }).windowMs(), 5000);
});

// ---- marks -------------------------------------------------------------------------------------------------------

test("parseMark takes a usable mark and drops the rest; dt that is not a measure is left out, 0 is kept", () => {
  assert.deepEqual(parseMark({ prev: 7, dt_ms: 12, earlier: 3 }), { prev: 7, dtMs: 12, earlier: 3 });
  assert.deepEqual(parseMark({ prev: 0, dt_ms: 0, earlier: 1 }), { prev: 0, dtMs: 0, earlier: 1 });
  assert.deepEqual(parseMark({ prev: 7, dt_ms: NaN, earlier: 0 }), { prev: 7, dtMs: null, earlier: 0 });
  assert.deepEqual(parseMark({ prev: 7, dt_ms: -4, earlier: -1 }), { prev: 7, dtMs: null, earlier: 0 });
  assert.deepEqual(parseMark({ prev: 7, dt_ms: "5" }), { prev: 7, dtMs: null, earlier: 0 });
  for (const bad of [null, 5, [], {}, { prev: "7" }, { prev: -1 }, { prev: 1.5 }, { prev: 2 ** 60 }, { prev: NaN }]) {
    assert.equal(parseMark(bad), null, JSON.stringify(bad));
  }
});

test("labels: the words of the row tooltip and of the detail", () => {
  assert.equal(markLabel({ prev: 12, dtMs: 340, earlier: 0 }), "Repeat of #12, +340 ms");
  assert.equal(markLabel({ prev: 12, dtMs: 0, earlier: 0 }), "Repeat of #12, +0 ms");
  assert.equal(markLabel({ prev: 12, dtMs: 1234567, earlier: 0 }), "Repeat of #12, +1,234,567 ms");
  assert.equal(markLabel({ prev: 12, dtMs: null, earlier: 0 }), "Repeat of #12");
  assert.equal(earlierLabel({ earlier: 0 }), "");
  assert.equal(earlierLabel({ earlier: 1 }), "1 earlier occurrence in the window");
  assert.equal(earlierLabel({ earlier: 2 }), "2 earlier occurrences in the window");
});

// ---- the store ---------------------------------------------------------------------------------------------------

function storeWorld(extra = {}) {
  const calls = [];
  const state = { caseId: "A", windowMs: 1000, on: true, changes: 0 };
  const api = {
    repeatMarks(caseId, ids, spec, signal) {
      const d = deferred();
      const call = { caseId, ids, spec, signal, d };
      if (!extra.ignoreAbort) {
        signal?.addEventListener?.("abort", () => d.reject(abortError()));
      }
      calls.push(call);
      return d.promise;
    },
  };
  const timers = [];
  const store = createRepeatStore({
    api,
    getCase: () => state.caseId,
    getWindow: () => state.windowMs,
    isOn: () => state.on,
    onChange: () => { state.changes += 1; },
    schedule: (fn) => { timers.push(fn); return timers.length; },
    ...{ ...extra, ignoreAbort: undefined },
  });
  const fire = async () => { const fn = timers.shift(); await fn(); };
  return { store, calls, state, timers, fire };
}

const marks = (map) => ({ marks: Object.fromEntries(Object.entries(map).map(([id, v]) => [id, v])) });

test("ensure merges a burst into one request, sends the window, and a mark shows up through get()", async () => {
  const { store, calls, state, timers, fire } = storeWorld();
  store.ensure([1, 2, 3]);
  store.ensure([1, 2, 3]);
  assert.equal(timers.length, 1, "one timer for the burst");
  const flush = fire();
  assert.equal(calls.length, 1);
  assert.deepEqual(calls[0].ids, [1, 2, 3]);
  assert.deepEqual(calls[0].spec, { window_ms: 1000 });
  assert.equal(calls[0].caseId, "A");
  calls[0].d.resolve(marks({ 2: { prev: 1, dt_ms: 5, earlier: 1 } }));
  await flush;
  assert.equal(store.get(1), null);
  assert.deepEqual(store.get(2), { prev: 1, dtMs: 5, earlier: 1, label: "Repeat of #1, +5 ms" });
  assert.equal(store.known(1), true, "an id that is not a repeat is answered too");
  assert.equal(state.changes, 1);
  store.ensure([1, 2, 3]);
  assert.equal(timers.length, 0, "nothing is asked twice");
});

test("a page of more than 1000 ids goes in requests of at most 1000, in order", async () => {
  const { store, calls, timers, fire } = storeWorld();
  const ids = Array.from({ length: 2500 }, (_, i) => i + 1);
  store.ensure(ids);
  const first = fire();
  assert.equal(calls[0].ids.length, MARK_BATCH);
  calls[0].d.resolve(marks({}));
  await first;
  assert.equal(timers.length, 1, "the rest is scheduled");
  const second = fire();
  assert.equal(calls[1].ids.length, MARK_BATCH);
  assert.equal(calls[1].ids[0], 1001);
  calls[1].d.resolve(marks({}));
  await second;
  const third = fire();
  assert.equal(calls[2].ids.length, 500);
  calls[2].d.resolve(marks({}));
  await third;
  assert.equal(timers.length, 0);
});

test("the last ensure wins: ids of a page the reader already left are not asked for", async () => {
  const { store, calls, fire } = storeWorld();
  store.ensure([1, 2, 3]);
  store.ensure([4, 5]);
  const flush = fire();
  assert.deepEqual(calls[0].ids, [4, 5]);
  calls[0].d.resolve(marks({}));
  await flush;
});

test("a case change drops the answer in flight and the marks; the late answer never lands", async () => {
  const { store, calls, state, fire } = storeWorld();
  store.ensure([1, 2]);
  const flush = fire();
  state.caseId = "B";
  store.ensure([1, 2]); // the grid draws again in the new case: sync() sees the change
  calls[0].d.resolve(marks({ 1: { prev: 9, dt_ms: 1, earlier: 1 } }));
  await flush;
  assert.equal(store.get(1), null);
  assert.equal(store.known(1), false);
  assert.equal(calls[0].signal.aborted, true);
});

test("a new window drops what was looked up and asks again with the new window", async () => {
  const { store, calls, state, fire } = storeWorld();
  store.ensure([1]);
  let flush = fire();
  calls[0].d.resolve(marks({ 1: { prev: 0, dt_ms: 1, earlier: 1 } }));
  await flush;
  assert.notEqual(store.get(1), null);
  state.windowMs = 5000;
  assert.equal(store.get(1) !== null, true, "get() is cheap and does not look at the window");
  store.ensure([1]);
  assert.equal(store.get(1), null);
  flush = fire();
  assert.deepEqual(calls[1].spec, { window_ms: 5000 });
  calls[1].d.resolve(marks({}));
  await flush;
});

test("an invalid window or no case asks for nothing; marks off asks for nothing", async () => {
  const w = storeWorld();
  w.state.windowMs = NaN;
  w.store.ensure([1]);
  assert.equal(w.timers.length, 0);
  const none = storeWorld();
  none.state.caseId = null;
  none.store.ensure([1]);
  assert.equal(none.timers.length, 0);
  const off = storeWorld();
  off.state.on = false;
  off.store.ensure([1]);
  assert.equal(off.timers.length, 0);
});

test("ids that are not ids are never sent", async () => {
  const { store, calls, fire } = storeWorld();
  store.ensure([1, NaN, -1, 1.5, "7", null, undefined, 2 ** 60, 3]);
  const flush = fire();
  assert.deepEqual(calls[0].ids, [1, 3]);
  calls[0].d.resolve(marks({}));
  await flush;
});

test("a failed request is silent and not retried on every scroll; clear() asks again", async () => {
  const { store, calls, state, timers, fire } = storeWorld();
  store.ensure([1, 2]);
  const flush = fire();
  calls[0].d.reject(new Error("boom"));
  await flush;
  assert.equal(state.changes, 0);
  store.ensure([1, 2]);
  assert.equal(timers.length, 0, "not asked again");
  store.clear();
  store.ensure([1, 2]);
  assert.equal(timers.length, 1);
});

test("answers with a malformed mark or an unknown id are ignored", async () => {
  const { store, calls, fire } = storeWorld();
  store.ensure([1, 2]);
  const flush = fire();
  calls[0].d.resolve(marks({ 1: { prev: "x" }, 2: { prev: 1, dt_ms: NaN, earlier: 1 }, 99: { prev: 1, dt_ms: 1, earlier: 1 } }));
  await flush;
  assert.equal(store.get(1), null);
  assert.equal(store.get(2).label, "Repeat of #1");
  assert.equal(store.get(99), null);
  const odd = storeWorld();
  odd.store.ensure([1]);
  const f2 = odd.fire();
  odd.calls[0].d.resolve(null);
  await f2;
  assert.equal(odd.store.known(1), true);
});

test("lookup uses what the grid already knows, else asks once; a stale answer is undefined", async () => {
  const { store, calls, state, fire } = storeWorld();
  store.ensure([1]);
  const flush = fire();
  calls[0].d.resolve(marks({ 1: { prev: 0, dt_ms: 2, earlier: 1 } }));
  await flush;
  assert.equal((await store.lookup(1)).prev, 0);
  assert.equal(calls.length, 1, "no request for a row already looked up");
  const asking = store.lookup(7);
  assert.deepEqual(calls[1].ids, [7]);
  calls[1].d.resolve(marks({ 7: { prev: 5, dt_ms: 9, earlier: 2 } }));
  assert.equal((await asking).prev, 5);
  const stale = store.lookup(8);
  state.caseId = "B";
  store.ensure([]); // sync(): the case changed
  calls[2].d.resolve(marks({ 8: { prev: 1, dt_ms: 1, earlier: 1 } }));
  assert.equal(await stale, undefined);
  assert.equal(await store.lookup(NaN), null);
  assert.equal(await store.lookup(-1), null);
});

test("lookup rejects with the api error (the detail decides to stay silent)", async () => {
  const { store, calls } = storeWorld();
  const asking = store.lookup(3);
  calls[0].d.reject(new Error("boom"));
  await assert.rejects(asking, /boom/);
});

// ---- the controller ----------------------------------------------------------------------------------------------

function controllerWorld({ userFilter = null, caseId = "A", storage = null } = {}) {
  const w = { userFilter, caseId, changes: 0, sets: [] };
  const timers = [];
  const calls = [];
  w.repeats = createRepeats({
    api: { repeatMarks: (c, ids, spec, signal) => { const d = deferred(); calls.push({ ids, spec, signal, d }); return d.promise; } },
    getCase: () => w.caseId,
    getUserFilter: () => w.userFilter,
    setUserFilter: (filter) => { w.userFilter = filter; w.sets.push(filter); return Promise.resolve(); },
    storage,
    onChange: () => { w.changes += 1; },
    schedule: (fn) => { timers.push(fn); return 1; },
  });
  w.calls = calls;
  w.timers = timers;
  return w;
}

test("toggleHide adds the chip with the window in force and a second click takes it out; repeated clicks stay consistent", async () => {
  const w = controllerWorld({ userFilter: { sev: { le: 3 } } });
  assert.equal(w.repeats.hidden(), false);
  await w.repeats.toggleHide();
  assert.deepEqual(w.userFilter, { and: [{ sev: { le: 3 } }, hideNode(1000)] });
  assert.equal(w.repeats.hidden(), true);
  const a = w.repeats.toggleHide();
  const b = w.repeats.toggleHide(); // a second click before the view answered: it reads the filter already set
  await Promise.all([a, b]);
  assert.deepEqual(w.userFilter, { and: [{ sev: { le: 3 } }, hideNode(1000)] });
  assert.equal(w.sets.length, 3);
});

test("toggleHide without a case does nothing", async () => {
  const w = controllerWorld({ caseId: null });
  await w.repeats.toggleHide();
  assert.equal(w.sets.length, 0);
});

test("setWindow rewrites the hide chip, keeps it absent when absent, drops the marks, and ignores an invalid value", async () => {
  const w = controllerWorld({ userFilter: hideNode(1000) });
  await w.repeats.setWindow(5000);
  assert.deepEqual(w.userFilter, { and: [hideNode(5000)] });
  assert.equal(w.repeats.window(), 5000);
  assert.equal(w.changes, 1);
  await w.repeats.setWindow(5000);
  assert.equal(w.sets.length, 1, "the same window again changes nothing");
  for (const bad of [NaN, -1, "5", null, MAX_WINDOW_MS + 1]) {
    await w.repeats.setWindow(bad);
  }
  assert.equal(w.repeats.window(), 5000);
  assert.equal(w.sets.length, 1);
  const free = controllerWorld({ userFilter: null });
  await free.repeats.setWindow(100);
  assert.equal(free.userFilter, null, "no chip: the window is only a preference");
  assert.equal(free.sets.length, 0);
  assert.deepEqual(free.repeats.windows(), [100, 500, 1000, 5000, 30000, 60000]);
});

test("toggleMarks turns the markers off (nothing asked, get() null) and on again", async () => {
  const w = controllerWorld();
  w.repeats.ensure([1]);
  assert.equal(w.timers.length, 1);
  w.timers.shift()();
  w.calls[0].d.resolve(marks({ 1: { prev: 0, dt_ms: 1, earlier: 1 } }));
  await tick();
  assert.notEqual(w.repeats.get(1), null);
  w.repeats.toggleMarks();
  assert.equal(w.repeats.marking(), false);
  assert.equal(w.repeats.get(1), null);
  w.repeats.ensure([1]);
  assert.equal(w.timers.length, 0);
  assert.equal(await w.repeats.lookup(1), null);
  w.repeats.toggleMarks();
  assert.equal(w.repeats.marking(), true);
  w.repeats.ensure([1]);
  assert.equal(w.timers.length, 1, "asked again after being turned on");
});

test("the choices are remembered through the storage", () => {
  const saved = {};
  const storage = { getItem: (k) => saved[k] ?? null, setItem: (k, v) => { saved[k] = v; } };
  const w = controllerWorld({ storage });
  w.repeats.toggleMarks();
  w.repeats.setWindow(500);
  const again = controllerWorld({ storage });
  assert.equal(again.repeats.marking(), false);
  assert.equal(again.repeats.window(), 500);
});

test("rows of another case than the open one are never asked for and carry no mark (ids repeat from case to case)", async () => {
  const { store, calls, state, timers, fire } = storeWorld();
  store.ensure([1], "A");
  const flush = fire();
  calls[0].d.resolve(marks({ 1: { prev: 0, dt_ms: 1, earlier: 1 } }));
  await flush;
  assert.notEqual(store.get(1, "A"), null);
  assert.equal(store.get(1, "B"), null, "the grid still shows the rows of B while A is open");
  store.ensure([2], "B");
  assert.equal(timers.length, 0, "nothing is asked for rows of another case");
  state.caseId = "B"; // the case changed; the grid still holds A's rows until its new view arrives
  store.ensure([1, 2], "A");
  assert.equal(timers.length, 0);
  assert.equal(store.get(1, "A"), null);
  store.ensure([1, 2], "B");
  assert.equal(timers.length, 1);
});

test("a long scroll does not grow the store without end: past the cap it starts over with the page on screen", async () => {
  const { store, calls, fire, timers } = storeWorld();
  let id = 1;
  for (let round = 0; round < 3; round += 1) {
    const ids = Array.from({ length: 10000 }, () => id++);
    store.ensure(ids);
    // one batch of 1000 is enough to fill `asked` with the whole page (ids are marked asked when taken)
    while (timers.length > 0) {
      const flush = fire();
      calls.at(-1).d.resolve(marks({}));
      await flush;
    }
  }
  assert.ok(id > MAX_KNOWN);
  store.ensure([5, 6, 7]);
  assert.equal(timers.length, 1, "an id seen long ago is asked again after the restart, and nothing broke");
  const flush = fire();
  assert.deepEqual(calls.at(-1).ids, [5, 6, 7]);
  calls.at(-1).d.resolve(marks({}));
  await flush;
});

test("a server that answers after the abort (it cannot be trusted to honour it): the token still drops the late answer", async () => {
  const { store, calls, state, fire } = storeWorld({ ignoreAbort: true });
  store.ensure([1, 2]);
  const flush = fire();
  state.caseId = "B";
  store.ensure([5], "B");
  calls[0].d.resolve(marks({ 1: { prev: 9, dt_ms: 1, earlier: 1 } }));
  await flush;
  assert.equal(store.get(1), null);
  assert.equal(store.get(1, "B"), null);
  assert.equal(store.known(1), false, "the ids of case A are not remembered as answered in case B");
  const lookup = store.lookup(7);
  state.windowMs = 500;
  store.ensure([], "B");
  calls.at(-1).d.resolve(marks({ 7: { prev: 1, dt_ms: 1, earlier: 1 } }));
  assert.equal(await lookup, undefined);
  assert.equal(store.get(7, "B"), null);
});
