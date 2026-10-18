// 9.7 UI: the comparison as ONE validated operation (compare-views-model.js): overlapping runs, late answers, case and
// view changes, cancelling, paging with the digest, the pickers' lists. No DOM: the API is a set of held promises.
import test from "node:test";
import assert from "node:assert/strict";

import { createCompareFlow, createListsFlow } from "../../src/dmslog/web/js/compare-views-model.js";
import { tick } from "./fixtures/fake-dom.mjs";

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}
const abortError = () => Object.assign(new Error("aborted"), { name: "AbortError" });

const answer = (extra = {}) => ({
  mode: "operation",
  a: { count: 2, truncated: false, first_ts: 1, last_ts: 2 },
  b: { count: 2, truncated: false, first_ts: 1, last_ts: 2 },
  stats: { same: 2, only_a: 0, only_b: 0, changed_a: 0, changed_b: 0, similarity: 1 },
  templates: [], templates_total: 0, runs_total: 1, total_rows: 2, offset: 0, next_offset: null, digest: "d1", notes: [],
  rows: [
    { op: "same", a_pos: 0, b_pos: 0, a: { id: 1, ts: 1, label: "T", msg: "m" }, b: { id: 11, ts: 1, label: "T", msg: "m" } },
    { op: "same", a_pos: 1, b_pos: 1, a: { id: 2, ts: 2, label: "T", msg: "m" }, b: { id: 12, ts: 2, label: "T", msg: "m" } },
  ],
  ...extra,
});

const SPEC = { mode: "operation", a: { event: "5" }, b: { event: "6" }, common: { sev: { le: 3 } }, maxSide: 5000, onlyDiff: false };

function world() {
  const calls = [];
  const current = { caseId: "A" };
  const states = [];
  const api = {
    compareViews: {
      run(caseId, body, signal) {
        const d = deferred();
        calls.push({ caseId, body, signal, d });
        signal?.addEventListener?.("abort", () => d.reject(abortError()));
        return d.promise;
      },
    },
  };
  const flow = createCompareFlow({ api, getCase: () => current.caseId, onState: (s) => states.push(s.status + (s.loadingMore ? "+more" : "")) });
  return { flow, calls, current, states, state: flow.state };
}

async function done(w, extra = {}) {
  const running = w.flow.run(SPEC, null);
  w.calls.at(-1).d.resolve(answer(extra));
  await running;
  await tick();
}

test("a run goes loading -> ok and sends the frozen request with the case", async () => {
  const w = world();
  const running = w.flow.run(SPEC, null);
  assert.equal(w.state.status, "loading");
  assert.equal(w.calls.length, 1);
  assert.equal(w.calls[0].caseId, "A");
  assert.deepEqual(w.calls[0].body, {
    mode: "operation", filter: { sev: { le: 3 } }, a: { event: 5 }, b: { event: 6 }, max_side: 5000, offset: 0, limit: 200, only_diff: false,
  });
  w.calls[0].d.resolve(answer());
  await running;
  assert.equal(w.state.status, "ok");
  assert.equal(w.state.rows.length, 2);
  assert.equal(w.state.result.stats.similarity, 100);
  assert.equal(w.state.nextOffset, null);
  assert.equal(w.state.caseId, "A");
});

test("two overlapping runs: the newer one wins whatever the order of the answers, and the older is aborted", async () => {
  const w = world();
  const first = w.flow.run(SPEC, null);
  const second = w.flow.run({ ...SPEC, a: { event: "7" } }, null);
  assert.equal(w.calls[0].signal.aborted, true, "the older request is cancelled");
  w.calls[1].d.resolve(answer({ digest: "second" }));
  await second;
  w.calls[0].d.resolve(answer({ digest: "first" })); // too late; its promise was rejected by the abort, a late success must not matter either
  await first;
  await tick();
  assert.equal(w.state.status, "ok");
  assert.equal(w.state.result.digest, "second");
  assert.equal(w.state.body.a.event, 7);
});

test("a late success of the older run (no abort support) is dropped", async () => {
  const calls = [];
  const flow = createCompareFlow({
    api: { compareViews: { run: (caseId, body) => { const d = deferred(); calls.push(d); return d.promise; } } },
    getCase: () => "A",
    onState: () => {},
  });
  const first = flow.run(SPEC, null);
  const second = flow.run({ ...SPEC, a: { event: "7" } }, null);
  calls[1].resolve(answer({ digest: "second" }));
  await second;
  calls[0].resolve(answer({ digest: "first" }));
  await first;
  assert.equal(flow.state.result.digest, "second");
});

test("a failure of the older run never shows over the newer one", async () => {
  const w = world();
  const first = w.flow.run(SPEC, null);
  const second = w.flow.run(SPEC, null);
  w.calls[0].d.reject(Object.assign(new Error("x"), { status: 500, code: "boom" }));
  await first;
  assert.equal(w.state.status, "loading");
  w.calls[1].d.resolve(answer());
  await second;
  assert.equal(w.state.status, "ok");
  assert.equal(w.state.error, null);
});

test("the case changing during the run drops the answer (success or failure)", async () => {
  for (const outcome of ["resolve", "reject"]) {
    const w = world();
    const running = w.flow.run(SPEC, null);
    w.current.caseId = "B";
    if (outcome === "resolve") {
      w.calls[0].d.resolve(answer());
    } else {
      w.calls[0].d.reject(Object.assign(new Error("x"), { status: 500, code: "boom" }));
    }
    await running;
    assert.equal(w.state.status, "loading", outcome + ": nothing published");
    assert.equal(w.state.result, null);
    assert.equal(w.state.error, null);
  }
});

test("without a case nothing is asked", async () => {
  const w = world();
  w.current.caseId = null;
  await w.flow.run(SPEC, null);
  assert.equal(w.calls.length, 0);
  assert.equal(w.state.status, "idle");
});

test("an invalid form sends nothing and touches neither the pending run nor the result shown", async () => {
  const w = world();
  await done(w);
  assert.equal(w.state.status, "ok");
  await w.flow.run({ ...SPEC, a: { event: "" } }, null);
  assert.equal(w.calls.length, 1);
  assert.equal(w.state.status, "ok");
  assert.ok(w.state.result);
  assert.deepEqual(w.state.errors, [{ side: "a", key: "noOperation" }]);
  const running = w.flow.run(SPEC, null); // a good run clears the refusal
  assert.deepEqual(w.state.errors, []);
  await w.flow.run({ ...SPEC, b: { event: "x" } }, null);
  assert.equal(w.state.status, "loading", "the run in progress was not cancelled by a refused form");
  assert.equal(w.calls[1].signal.aborted, false);
  w.calls[1].d.resolve(answer());
  await running;
  assert.equal(w.state.status, "ok");
});

test("a failed run says why with a static key and keeps no half result", async () => {
  const w = world();
  const running = w.flow.run(SPEC, null);
  w.calls[0].d.reject(Object.assign(new Error("event 5 does not exist"), { status: 404, code: "event_not_found" }));
  await running;
  assert.equal(w.state.status, "error");
  assert.equal(w.state.error.key, "event_not_found");
  assert.equal(w.state.result, null);
  assert.deepEqual(w.state.rows, []);
});

test("a side cut at the limit is too_large, and the rerun uses the frozen request, not the form", async () => {
  const w = world();
  const running = w.flow.run(SPEC, { a: "x", b: "y" });
  w.calls[0].d.resolve(answer({ a: { count: 1000, truncated: true, first_ts: 1, last_ts: 2 }, notes: ["truncated_a"] }));
  await running;
  assert.equal(w.state.status, "too_large");
  const rerun = w.flow.rerun({ maxSide: 20000 });
  assert.equal(w.calls[1].body.max_side, 20000);
  assert.deepEqual(w.calls[1].body.a, { event: 5 }, "the sides are the ones of the result, whatever the form says now");
  assert.equal(w.calls[1].body.offset, 0);
  w.calls[1].d.resolve(answer());
  await rerun;
  assert.equal(w.state.status, "ok");
  const only = w.flow.rerun({ onlyDiff: true });
  assert.equal(w.calls[2].body.only_diff, true);
  assert.equal(w.calls[2].body.max_side, 20000);
  w.calls[2].d.resolve(answer());
  await only;
  const odd = w.flow.rerun({ maxSide: 12345 });
  assert.equal(w.calls[3].body.max_side, 20000, "a limit that is not a choice is ignored");
  w.calls[3].d.resolve(answer());
  await odd;
});

test("rerun without a result, or after the case changed, asks for nothing", async () => {
  const w = world();
  await w.flow.rerun({ onlyDiff: true });
  assert.equal(w.calls.length, 0);
  await done(w);
  w.current.caseId = "B";
  await w.flow.rerun({ onlyDiff: true });
  assert.equal(w.calls.length, 1);
});

test("paging: the next page is appended when it belongs to the same comparison", async () => {
  const w = world();
  await done(w, { next_offset: 2, total_rows: 4 });
  assert.equal(w.state.nextOffset, 2);
  const more = w.flow.more();
  assert.equal(w.state.loadingMore, true);
  assert.equal(w.calls[1].body.offset, 2);
  assert.equal(w.calls[1].body.limit, 200);
  assert.deepEqual(w.calls[1].body.a, { event: 5 });
  const again = w.flow.more();
  await again;
  assert.equal(w.calls.length, 2, "a second click while loading asks for nothing");
  w.calls[1].d.resolve(answer({ offset: 2, next_offset: null, rows: [{ op: "only_a", a: { id: 3, ts: 3 }, b: null }] }));
  await more;
  assert.equal(w.state.rows.length, 3);
  assert.equal(w.state.rows[2].key, 2);
  assert.equal(w.state.rows[2].op, "only_a");
  assert.equal(w.state.nextOffset, null);
  assert.equal(w.state.loadingMore, false);
  assert.equal(w.state.status, "ok");
});

test("paging: another digest or offset means another comparison; its rows are never mixed in", async () => {
  for (const wrong of [{ digest: "other" }, { offset: 5 }]) {
    const w = world();
    await done(w, { next_offset: 2, total_rows: 4 });
    const more = w.flow.more();
    w.calls[1].d.resolve(answer({ offset: 2, next_offset: 4, ...wrong }));
    await more;
    assert.equal(w.state.changed, true, JSON.stringify(wrong));
    assert.equal(w.state.rows.length, 2);
    assert.equal(w.state.nextOffset, null);
    await w.flow.more();
    assert.equal(w.calls.length, 2, "nothing more is asked of a result that changed");
  }
});

test("paging: a failure keeps the rows and the page can be asked again", async () => {
  const w = world();
  await done(w, { next_offset: 2, total_rows: 4 });
  const more = w.flow.more();
  w.calls[1].d.reject(Object.assign(new Error("x"), { status: 408, code: "query_timeout" }));
  await more;
  assert.equal(w.state.moreError.key, "query_timeout");
  assert.equal(w.state.rows.length, 2);
  assert.equal(w.state.status, "ok");
  const retry = w.flow.more();
  assert.equal(w.state.moreError, null);
  w.calls[2].d.resolve(answer({ offset: 2, next_offset: null, rows: [] }));
  await retry;
  assert.equal(w.state.nextOffset, null);
});

test("paging: a late page of a result that was replaced, reset or left for another case is dropped", async () => {
  // replaced by a new run
  let w = world();
  await done(w, { next_offset: 2 });
  let more = w.flow.more();
  const run = w.flow.run(SPEC, null);
  w.calls[1].d.resolve(answer({ offset: 2, next_offset: null, rows: [{ op: "only_a", a: { id: 3 }, b: null }] }));
  await more;
  assert.equal(w.state.rows.length, 0, "the new run cleared the old rows and the late page did not come back");
  w.calls[2].d.resolve(answer({ digest: "new" }));
  await run;
  assert.equal(w.state.rows.length, 2);
  // reset
  w = world();
  await done(w, { next_offset: 2 });
  more = w.flow.more();
  w.flow.reset();
  w.calls[1].d.resolve(answer({ offset: 2, next_offset: null }));
  await more;
  assert.equal(w.state.status, "idle");
  assert.equal(w.state.result, null);
  // another case
  w = world();
  await done(w, { next_offset: 2 });
  more = w.flow.more();
  w.current.caseId = "B";
  w.calls[1].d.resolve(answer({ offset: 2, next_offset: null }));
  await more;
  assert.equal(w.state.rows.length, 2);
  assert.equal(w.state.loadingMore, true, "nothing of the late page was published");
  await w.flow.more();
  assert.equal(w.calls.length, 2, "and the old case's page is not asked again from another case");
});

test("cancel: the run in progress is aborted, the panel is idle and says it was cancelled; closing the tab is quiet", async () => {
  let w = world();
  const running = w.flow.run(SPEC, null);
  w.flow.cancel();
  assert.equal(w.calls[0].signal.aborted, true);
  assert.equal(w.state.status, "idle");
  assert.equal(w.state.cancelled, true);
  w.calls[0].d.resolve(answer());
  await running;
  assert.equal(w.state.result, null);
  w = world();
  const quiet = w.flow.run(SPEC, null);
  w.flow.cancel({ quiet: true });
  assert.equal(w.state.cancelled, false);
  assert.equal(w.state.status, "idle");
  await quiet;
  // nothing in progress: nothing to cancel, and a shown result stays
  w = world();
  await done(w);
  w.flow.cancel();
  assert.equal(w.state.status, "ok");
});

test("cancelling a page keeps the rows already read", async () => {
  const w = world();
  await done(w, { next_offset: 2 });
  const more = w.flow.more();
  w.flow.cancel();
  assert.equal(w.calls[1].signal.aborted, true);
  assert.equal(w.state.status, "ok");
  assert.equal(w.state.rows.length, 2);
  assert.equal(w.state.loadingMore, false);
  await more;
});

test("the view changing: a run in progress is cancelled, a result is marked out of date, a page in flight is dropped", async () => {
  let w = world();
  let running = w.flow.run(SPEC, null);
  w.flow.viewChanged();
  assert.equal(w.calls[0].signal.aborted, true);
  assert.equal(w.state.status, "idle");
  assert.equal(w.state.stale, true);
  w.calls[0].d.resolve(answer());
  await running;
  assert.equal(w.state.result, null);
  w = world();
  w.flow.viewChanged();
  assert.equal(w.state.stale, false, "nothing was compared: nothing is out of date");
  await done(w, { next_offset: 2 });
  w.flow.viewChanged();
  assert.equal(w.state.stale, true);
  assert.equal(w.state.status, "ok");
  assert.equal(w.state.rows.length, 2, "the result stays readable");
  w.flow.rerun({ onlyDiff: true });
  assert.equal(w.state.stale, false, "a new run is for the new state");
  w.calls.at(-1).d.resolve(answer());
  await tick();
  // a page in flight
  w = world();
  await done(w, { next_offset: 2 });
  const more = w.flow.more();
  w.flow.viewChanged();
  assert.equal(w.state.loadingMore, false);
  w.calls[1].d.resolve(answer({ offset: 2, next_offset: null, rows: [{ op: "only_b", a: null, b: { id: 9 } }] }));
  await more;
  assert.equal(w.state.rows.length, 2);
  assert.equal(w.state.stale, true);
});

test("reset forgets everything of the case", async () => {
  const w = world();
  await done(w, { next_offset: 2 });
  w.flow.reset();
  assert.equal(w.state.status, "idle");
  assert.equal(w.state.result, null);
  assert.deepEqual(w.state.rows, []);
  assert.equal(w.state.caseId, null);
  assert.equal(w.state.body, null);
  assert.equal(w.state.nextOffset, null);
});

// ---- the lists of the pickers

function listsWorld() {
  const calls = [];
  const current = { caseId: "A" };
  const hold = (name) => (caseId, ...args) => {
    const d = deferred();
    const signal = args.at(-1);
    calls.push({ name, caseId, args, d });
    signal?.addEventListener?.("abort", () => d.reject(abortError()));
    return d.promise;
  };
  const api = { views: { list: hold("views") }, events: hold("events") };
  const changes = [];
  const flow = createListsFlow({ api, getCase: () => current.caseId, onChange: (name) => changes.push(name) });
  return { flow, calls, current, changes, lists: flow.lists };
}

test("lists: each is loaded once per case (ensure), reloaded on demand, and asked with the right parameters", async () => {
  const w = listsWorld();
  const loading = w.flow.ensure("operation");
  assert.equal(w.lists.operation.status, "loading");
  assert.deepEqual(w.calls[0].args[0], { kind: "operation", limit: 200 });
  w.calls[0].d.resolve({ items: [{ id: 1 }] });
  await loading;
  assert.equal(w.lists.operation.status, "ready");
  assert.deepEqual(w.lists.operation.items, [{ id: 1 }]);
  await w.flow.ensure("operation");
  assert.equal(w.calls.length, 1, "ready: not asked again");
  const process = w.flow.ensure("process");
  assert.deepEqual(w.calls[1].args[0], { limit: 200 });
  w.calls[1].d.resolve({ items: [] });
  await process;
  const views = w.flow.load("views");
  w.calls[2].d.resolve({ views: [{ id: 7 }] });
  await views;
  assert.deepEqual(w.lists.views.items, [{ id: 7 }]);
});

test("lists: a failed list says so and ensure tries again", async () => {
  const w = listsWorld();
  const loading = w.flow.ensure("views");
  w.calls[0].d.reject(Object.assign(new Error("x"), { status: 500 }));
  await loading;
  assert.equal(w.lists.views.status, "error");
  const again = w.flow.ensure("views");
  assert.equal(w.calls.length, 2);
  w.calls[1].d.resolve({ views: [] });
  await again;
  assert.equal(w.lists.views.status, "ready");
});

test("lists: an answer for another case, an older load, or after clear() is dropped", async () => {
  let w = listsWorld();
  let loading = w.flow.load("operation");
  w.current.caseId = "B";
  w.calls[0].d.resolve({ items: [{ id: 1 }] });
  await loading;
  assert.equal(w.lists.operation.status, "loading", "nothing published for the case that was left");
  assert.deepEqual(w.lists.operation.items, []);
  const reloaded = w.flow.ensure("operation"); // the list belongs to another case now: loaded again
  assert.equal(w.calls.length, 2);
  assert.equal(w.calls[1].caseId, "B");
  w.calls[1].d.resolve({ items: [{ id: 3 }] });
  await reloaded;
  assert.deepEqual(w.lists.operation.items, [{ id: 3 }]);
  w = listsWorld();
  const first = w.flow.load("process");
  const second = w.flow.load("process");
  assert.equal(w.calls[0].args.at(-1).aborted, true);
  w.calls[1].d.resolve({ items: [{ id: 2 }] });
  await second;
  w.calls[0].d.resolve({ items: [{ id: 1 }] });
  await first;
  assert.deepEqual(w.lists.process.items, [{ id: 2 }]);
  w = listsWorld();
  loading = w.flow.load("views");
  w.flow.clear();
  w.calls[0].d.resolve({ views: [{ id: 1 }] });
  await loading;
  assert.equal(w.lists.views.status, "idle");
  assert.deepEqual(w.lists.views.items, []);
});

test("lists: closing the tab drops the lists still loading and keeps the loaded ones", async () => {
  const w = listsWorld();
  const ready = w.flow.load("views");
  w.calls[0].d.resolve({ views: [{ id: 1 }] });
  await ready;
  const pending = w.flow.load("operation");
  w.flow.cancelPending();
  assert.equal(w.calls[1].args.at(-1).aborted, true);
  assert.equal(w.lists.operation.status, "idle");
  assert.equal(w.lists.views.status, "ready");
  await pending;
  assert.equal(w.lists.operation.status, "idle");
});

test("lists: an older load that ignores the abort cannot overwrite the newer one", async () => {
  const calls = [];
  const flow = createListsFlow({
    api: { views: { list: () => { const d = deferred(); calls.push(d); return d.promise; } }, events: () => new Promise(() => {}) },
    getCase: () => "A",
    onChange: () => {},
  });
  const first = flow.load("views");
  const second = flow.load("views");
  calls[1].resolve({ views: [{ id: 2 }] });
  await second;
  calls[0].resolve({ views: [{ id: 1 }] });
  await first;
  assert.deepEqual(flow.lists.views.items, [{ id: 2 }]);
});
