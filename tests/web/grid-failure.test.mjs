// UI-25: a failed request never empties the grid. The old rows, filter, count and selection stay; the failure is
// reported with a Retry that repeats only what failed; late answers of replaced requests change nothing.
import test from "node:test";
import assert from "node:assert/strict";

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
globalThis.document = { createElement: fakeElement, createTextNode: (text) => ({ textContent: String(text) }) };

const { Grid } = await import("../../src/dmslog/web/js/grid.js");
const { api, ApiError, errorKind, isRetryable } = await import("../../src/dmslog/web/js/api.js");

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}
const flush = async () => { for (let i = 0; i < 6; i += 1) await Promise.resolve(); };
const rows = (from, n) => Array.from({ length: n }, (_, i) => ({ id: from + i, ts: 1000 + from + i, sev: 1, level: 3, msg: "m" + (from + i) }));

function setup() {
  const pages = [];
  const counts = [];
  api.page = (caseId, filter, cursor, dir, limit, signal) => {
    const d = deferred();
    pages.push({ caseId, filter, cursor, dir, signal, d });
    return d.promise;
  };
  api.count = (caseId, filter, signal) => {
    const d = deferred();
    counts.push({ caseId, filter, signal, d });
    return d.promise;
  };
  const log = { counts: [], failures: [], recovered: 0, errors: [] };
  const viewport = fakeElement();
  const grid = new Grid({
    viewport, rowsLayer: fakeElement(), spacer: fakeElement(), header: fakeElement(),
    onSelect() {}, onCount: (v, o) => log.counts.push([v, o]), onPosition() {},
    onError: (err) => log.errors.push(err),
    onFailure: (err, info) => log.failures.push({ err, kind: info.kind, retry: info.retry }),
    onRecovered: () => { log.recovered += 1; },
  });
  return { grid, pages, counts, log, viewport };
}

const FA = { and: [{ field: "a" }] };
const FB = { and: [{ field: "b" }] };

async function loaded(ctx, filter = FA, n = 30) {
  const loading = ctx.grid.setView({ caseId: "C", filter, range: null });
  ctx.counts.at(-1).d.resolve({ count: 500, exact: true });
  ctx.pages.at(-1).d.resolve({ rows: rows(1, n), prev: null, next: null });
  assert.equal(await loading, "ok");
  await flush();
}

test("errorKind: network, forbidden, timeout, server and query errors are told apart", () => {
  assert.equal(errorKind(new ApiError(0, "network", "x")), "network");
  assert.equal(errorKind(new ApiError(403, "forbidden", "x")), "forbidden");
  assert.equal(errorKind(new ApiError(408, "query_timeout", "x")), "timeout");
  assert.equal(errorKind(new ApiError(408, "http_408", "x")), "timeout"); // by status alone
  assert.equal(errorKind(new ApiError(504, "aggregate_timeout", "x")), "timeout");
  assert.equal(errorKind(new ApiError(500, "boom", "x")), "server");
  assert.equal(errorKind(new ApiError(400, "bad_filter", "x")), "query");
  assert.equal(errorKind(new ApiError(404, "no_case", "x")), "query");
  assert.equal(errorKind(null), "unknown");
  assert.equal(errorKind(new Error("plain")), "unknown");
  assert.equal(new ApiError(0, "network", "x").kind, "network");
  assert.equal(isRetryable("query"), false);
  assert.equal(isRetryable("forbidden"), false);
  for (const kind of ["network", "timeout", "server", "unknown"]) assert.equal(isRetryable(kind), true);
});

test("a failed filter change keeps the rows, the filter, the selection and the old count", async () => {
  const ctx = setup();
  await loaded(ctx);
  ctx.grid.window.selected = 4;
  ctx.grid.window.top = 3;
  const before = ctx.grid.window.rows.slice();
  const outcome = ctx.grid.setView({ caseId: "C", filter: FB, range: { from: 1, to: 2 } });
  // While the new page is pending: old rows still there, nothing adopted, busy and muted.
  assert.equal(ctx.grid.window.rows.length, 30);
  assert.equal(ctx.grid.filter, FA);
  assert.equal(ctx.viewport.attrs["aria-busy"], "true");
  assert.ok(ctx.viewport.classes.has("is-reloading"));
  ctx.counts.at(-1).d.resolve({ count: 7, exact: true }); // the new count arrives first: held, not shown
  await flush();
  assert.equal(ctx.log.counts.some(([v]) => v && v.n === 7), false);
  ctx.pages.at(-1).d.reject(new ApiError(0, "network", "down"));
  assert.equal(await outcome, "failed");
  assert.deepEqual(ctx.grid.window.rows, before);
  assert.equal(ctx.grid.window.selected, 4);
  assert.equal(ctx.grid.window.top, 3);
  assert.equal(ctx.grid.filter, FA);
  assert.equal(ctx.grid.range, null);
  assert.equal(ctx.viewport.attrs["aria-busy"], "false");
  assert.equal(ctx.viewport.classes.has("is-reloading"), false);
  assert.equal(ctx.log.failures.length, 1);
  assert.equal(ctx.log.failures[0].kind, "network");
  assert.deepEqual(ctx.log.counts.at(-1)[0], { n: 500, exact: true }); // the old count is back, not "loading"
});

test("the view change is confirmed with its page: filter, count and baseline arrive together", async () => {
  const ctx = setup();
  await loaded(ctx);
  const outcome = ctx.grid.setView({ caseId: "C", filter: FB, range: null });
  ctx.pages.at(-1).d.resolve({ rows: rows(100, 5), prev: null, next: null });
  assert.equal(await outcome, "ok");
  await flush();
  assert.equal(ctx.grid.filter, FB);
  assert.equal(ctx.grid.window.rows.length, 5);
  assert.equal(ctx.grid.total, null); // the count of FB has not answered yet; the old total is no baseline
  ctx.counts.at(-1).d.resolve({ count: 5, exact: true });
  await flush();
  assert.equal(ctx.grid.total, 5);
  assert.deepEqual(ctx.log.counts.at(-1)[0], { n: 5, exact: true });
});

test("a 4xx and a timeout are not network errors: the notice names the kind and Retry only for the repeatable", async () => {
  const ctx = setup();
  await loaded(ctx);
  let outcome = ctx.grid.setView({ caseId: "C", filter: FB, range: null });
  ctx.pages.at(-1).d.reject(new ApiError(400, "bad_filter", "Filter is not valid"));
  await outcome;
  assert.equal(ctx.log.failures.at(-1).kind, "query");
  outcome = ctx.grid.setView({ caseId: "C", filter: FB, range: null });
  ctx.pages.at(-1).d.reject(new ApiError(408, "query_timeout", "Too slow"));
  await outcome;
  assert.equal(ctx.log.failures.at(-1).kind, "timeout");
  assert.equal(ctx.grid.window.rows.length, 30);
});

test("retry repeats only the failed view change, once, and recovery is announced", async () => {
  const ctx = setup();
  await loaded(ctx);
  const outcome = ctx.grid.setView({ caseId: "C", filter: FB, range: null });
  ctx.pages.at(-1).d.reject(new ApiError(0, "network", "down"));
  await outcome;
  const pagesBefore = ctx.pages.length;
  const first = ctx.grid.retry();
  const second = ctx.grid.retry(); // a double click: nothing more is sent
  assert.equal(await second, "noop");
  assert.equal(ctx.pages.length, pagesBefore + 1);
  assert.equal(ctx.pages.at(-1).filter, FB);
  ctx.counts.at(-1).d.resolve({ count: 3, exact: true });
  ctx.pages.at(-1).d.resolve({ rows: rows(200, 3), prev: null, next: null });
  assert.equal(await first, "ok");
  assert.equal(ctx.grid.filter, FB);
  assert.equal(ctx.log.recovered, 1);
  assert.equal(ctx.grid.hasFailure(), false);
  assert.equal(await ctx.grid.retry(), "noop"); // nothing left to repeat
});

test("a late answer of a replaced view change never overwrites the new one, a late failure is silent", async () => {
  const ctx = setup();
  await loaded(ctx);
  const first = ctx.grid.setView({ caseId: "C", filter: FB, range: null });
  const second = ctx.grid.setView({ caseId: "C", filter: { and: [] }, range: null });
  assert.equal(ctx.pages[1].signal.aborted, true);
  ctx.pages[2].d.resolve({ rows: rows(300, 2), prev: null, next: null });
  assert.equal(await second, "ok");
  ctx.pages[1].d.reject(new ApiError(0, "network", "late")); // the old one fails after being replaced (no abort in the fake)
  assert.equal(await first, "superseded");
  assert.equal(ctx.log.failures.length, 0);
  assert.equal(ctx.grid.window.rows.length, 2);
  assert.deepEqual(ctx.grid.filter, { and: [] });
  const stale = ctx.grid.setView({ caseId: "C", filter: FA, range: null });
  const newest = ctx.grid.setView({ caseId: "C", filter: FB, range: null });
  ctx.pages.at(-1).d.resolve({ rows: rows(400, 4), prev: null, next: null });
  ctx.pages.at(-2).d.resolve({ rows: rows(500, 9), prev: null, next: null }); // late success of the replaced one
  assert.equal(await newest, "ok");
  assert.equal(await stale, "superseded");
  assert.equal(ctx.grid.window.rows.length, 4);
  assert.equal(ctx.grid.filter, FB);
});

test("another case is adopted at once: its failure leaves an empty grid, never the old case's rows", async () => {
  const ctx = setup();
  await loaded(ctx);
  const outcome = ctx.grid.setView({ caseId: "D", filter: null, range: null });
  assert.equal(ctx.grid.caseId, "D");
  assert.equal(ctx.grid.window.rows.length, 0);
  ctx.pages.at(-1).d.reject(new ApiError(0, "network", "down"));
  assert.equal(await outcome, "failed");
  assert.equal(ctx.grid.window.rows.length, 0);
  assert.equal(ctx.grid.hasFailure(), true);
});

test("a case switch while a view change is pending drops the old answer", async () => {
  const ctx = setup();
  await loaded(ctx);
  const old = ctx.grid.setView({ caseId: "C", filter: FB, range: null });
  const next = ctx.grid.setView({ caseId: "D", filter: null, range: null });
  ctx.pages[1].d.resolve({ rows: rows(900, 6), prev: null, next: null });
  ctx.pages[2].d.resolve({ rows: rows(10, 2), prev: null, next: null });
  assert.equal(await next, "ok");
  assert.equal(await old, "superseded");
  assert.equal(ctx.grid.caseId, "D");
  assert.equal(ctx.grid.window.rows.length, 2);
});

test("a failed jump (Home, End, time) leaves the rows, the selection and the scroll where they were", async () => {
  const ctx = setup();
  await loaded(ctx);
  ctx.grid.window.selected = 2;
  ctx.viewport.scrollTop = 123;
  const before = ctx.grid.window.rows.slice();
  const jumping = ctx.grid.goEnd();
  ctx.pages.at(-1).d.reject(new ApiError(500, "boom", "server error"));
  assert.equal(await jumping, "failed");
  assert.deepEqual(ctx.grid.window.rows, before);
  assert.equal(ctx.grid.window.selected, 2);
  assert.equal(ctx.log.failures.at(-1).kind, "server");
  assert.equal(ctx.viewport.attrs["aria-busy"], "false");
  const again = ctx.grid.retry();
  assert.equal(ctx.pages.at(-1).dir, "back");
  ctx.pages.at(-1).d.resolve({ rows: rows(480, 20), prev: "p", next: null });
  assert.equal(await again, "ok");
  assert.equal(ctx.log.recovered, 1);
});

test("a failed scroll page stops the pump (no hammering); Retry fetches that page again", async () => {
  const ctx = setup();
  const loading = ctx.grid.setView({ caseId: "C", filter: FA, range: null });
  ctx.counts.at(-1).d.resolve({ count: 500, exact: true });
  ctx.pages.at(-1).d.resolve({ rows: rows(1, 10), prev: null, next: "n1" });
  await loading;
  await flush();
  const pagesSoFar = ctx.pages.length; // the pump asked for the next page
  assert.ok(ctx.pages.at(-1).cursor === "n1");
  ctx.pages.at(-1).d.reject(new ApiError(0, "network", "down"));
  await flush();
  assert.equal(ctx.grid.hasFailure(), true);
  ctx.grid.scrollRows(1);
  ctx.grid.scrollRows(1);
  await flush();
  assert.equal(ctx.pages.length, pagesSoFar); // still the one that failed
  assert.equal(ctx.grid.window.rows.length, 10);
  const retried = ctx.grid.retry();
  assert.equal(ctx.pages.length, pagesSoFar + 1);
  ctx.pages.at(-1).d.resolve({ rows: rows(11, 10), prev: "p", next: null });
  await retried;
  await flush();
  assert.equal(ctx.grid.window.rows.length, 20);
  assert.equal(ctx.log.recovered, 1);
});

test("aria-busy is on while the rows are replaced and off after success, failure and cancel", async () => {
  const ctx = setup();
  const loading = ctx.grid.setView({ caseId: "C", filter: FA, range: null });
  assert.equal(ctx.viewport.attrs["aria-busy"], "true");
  ctx.pages.at(-1).d.resolve({ rows: rows(1, 3), prev: null, next: null });
  await loading;
  assert.equal(ctx.viewport.attrs["aria-busy"], "false");
  ctx.grid.setView({ caseId: "C", filter: FB, range: null });
  assert.equal(ctx.viewport.attrs["aria-busy"], "true");
  ctx.grid.cancel();
  assert.equal(ctx.viewport.attrs["aria-busy"], "false");
});

test("a count that fails while the page works shows no count (never the old one) and reports the error", async () => {
  const ctx = setup();
  await loaded(ctx);
  const outcome = ctx.grid.setView({ caseId: "C", filter: FB, range: null });
  ctx.counts.at(-1).d.reject(new ApiError(500, "boom", "count failed"));
  await flush();
  ctx.pages.at(-1).d.resolve({ rows: rows(50, 3), prev: null, next: null });
  assert.equal(await outcome, "ok");
  assert.equal(ctx.log.counts.at(-1)[0], null);
  assert.equal(ctx.log.errors.length, 1);
});

test("the count of a view change that failed, arriving late, is never shown", async () => {
  const ctx = setup();
  await loaded(ctx);
  const outcome = ctx.grid.setView({ caseId: "C", filter: FB, range: null });
  ctx.pages.at(-1).d.reject(new ApiError(0, "network", "down"));
  assert.equal(await outcome, "failed");
  const shown = ctx.log.counts.length;
  ctx.counts.at(-1).d.resolve({ count: 9, exact: true }); // the answer for FB arrives after its page failed
  await flush();
  assert.equal(ctx.log.counts.length, shown);
  assert.equal(ctx.grid.total, 500);
});

test("a new case keeps its count whichever answers first, the page or the count", async () => {
  const ctx = setup();
  const loading = ctx.grid.setView({ caseId: "C", filter: FA, range: null });
  ctx.pages.at(-1).d.resolve({ rows: rows(1, 3), prev: null, next: null });
  assert.equal(await loading, "ok");
  ctx.counts.at(-1).d.resolve({ count: 42, exact: true });
  await flush();
  assert.equal(ctx.grid.total, 42);
  assert.deepEqual(ctx.log.counts.at(-1)[0], { n: 42, exact: true });
});
