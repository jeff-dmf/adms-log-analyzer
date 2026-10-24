// 9.12 UI: the loading flow and the shaping of the "Missing steps" panel, with a fake api held by the test.
import test from "node:test";
import assert from "node:assert/strict";

import {
  CAUSES, buildItemRow, buildListParams, buildSummaryParams, countText, createMissingFlow, summaryRows, summaryTotal,
} from "../../src/dmslog/web/js/missing-model.js";

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}
const abortError = () => Object.assign(new Error("aborted"), { name: "AbortError" });

const item = (id, extra = {}) => ({
  event: { id, kind: "operation", name: "Op" + id, family: "Wfm", process: "Wfm", ts_start: id * 1000, ts_end: null, status: "open", start_entry_id: id * 10 },
  steps: ["start", "end"],
  missing: ["end"],
  cause: "end_of_log",
  proven: false,
  ...extra,
});

function setup(options = {}) {
  const calls = [];
  const states = [];
  const current = { caseId: "A" };
  const hold = (name) => (caseId, params, signal) => {
    const d = deferred();
    calls.push({ name, caseId, params, signal, d });
    if (options.honorAbort !== false) {
      signal?.addEventListener?.("abort", () => d.reject(abortError()));
    }
    return d.promise;
  };
  const api = { missingEvents: hold("list"), missingSummary: hold("summary") };
  const flow = createMissingFlow({ api, getCase: () => current.caseId, onState: (s) => states.push(s.status), limit: 2, ...options, honorAbort: undefined });
  const of = (name) => calls.filter((c) => c.name === name);
  return { flow, calls, current, of, states };
}

// ---- shaping ---------------------------------------------------------------------------------------------------------

test("the list query never carries the cause (the server has no such filter) and leaves empty values out", () => {
  assert.deepEqual(buildListParams({ kind: "operation", status: "", cause: "timeout", name: " X " }, null, 200),
    { limit: 200, kind: "operation", name: "X" });
  assert.deepEqual(buildListParams({}, "tok", 5), { limit: 5, after: "tok" });
  assert.deepEqual(buildListParams(null, "", 5), { limit: 5 });
  assert.deepEqual(buildSummaryParams({ kind: "startup", status: "open", name: "X", cause: "timeout" }), { kind: "startup", status: "open" });
});

test("buildItemRow shapes one item as text and skips what is malformed", () => {
  const row = buildItemRow(item(7, { cause: "superseded", proven: true, missing: ["end"] }));
  assert.equal(row.id, 7);
  assert.equal(row.missing, "End");
  assert.equal(row.causeLabel, "Superseded by a later record");
  assert.equal(row.proven, true);
  assert.deepEqual(row.target, { from: 7000, to: 7000, entryId: 70 });
  for (const bad of [null, {}, { event: null }, { event: {} }, { event: { id: "x" } }, { event: { id: 1.5 } }]) {
    assert.equal(buildItemRow(bad), null);
  }
  const weird = buildItemRow({ event: { id: 3, kind: "x", name: "<b>n</b>", ts_start: null }, missing: "end", cause: 5, proven: "yes" });
  assert.equal(weird.name, "<b>n</b>", "log text is kept literally (the panel writes it with textContent)");
  assert.equal(weird.target, null, "an event without a time cannot open in the grid");
  assert.equal(weird.missing, "");
  assert.equal(weird.proven, false);
});

test("summaryRows: every cause listed, a cause filter keeps its count and hides empty groups; bad numbers count as none", () => {
  const groups = [
    { kind: "operation", family: "Wfm", name: "A", missing: "end", n: 5, causes: { superseded: 2, end_of_log: 3 } },
    { kind: "startup", family: "Wfm", name: null, missing: "hot", n: 1, causes: { timeout: 1 } },
    { kind: "operation", family: "Wfm", name: "B,C", missing: "end", n: NaN, causes: { superseded: -1, end_of_log: "x" } },
    null,
  ];
  const all = summaryRows(groups, "");
  assert.deepEqual(all.map((r) => r.count), [5, 1]);
  assert.equal(all[0].causes, "Superseded by a later record: 2, End of log: 3");
  assert.equal(all[0].canFilter, true);
  assert.equal(all[1].canFilter, false, "no name: nothing to filter by");
  assert.equal(all[1].name, "(no name)");
  const timed = summaryRows(groups, "timeout");
  assert.deepEqual(timed.map((r) => [r.kind, r.count]), [["startup", 1]]);
  assert.equal(summaryTotal(all), 6);
  assert.equal(summaryRows([{ kind: "operation", name: "B,C", n: 2, causes: {} }], "")[0].canFilter, false, "a comma would split the name list");
  assert.deepEqual(summaryRows(null, ""), []);
  assert.deepEqual(CAUSES.length, 5);
});

test("countText pluralizes and says when more is available", () => {
  assert.equal(countText(1, false), "1 event shown");
  assert.equal(countText(2, true), "2 events shown, more available");
  assert.equal(countText(NaN, false), "0 events shown");
});

// ---- the flow --------------------------------------------------------------------------------------------------------

test("refresh asks for the list and the summary of the case; the first page, then keyset pages without repeats", async () => {
  const { flow, of } = setup();
  const loading = flow.refresh();
  assert.equal(flow.state.status, "loading");
  assert.equal(of("list")[0].caseId, "A");
  assert.deepEqual(of("list")[0].params, { limit: 2 });
  assert.equal(of("summary")[0].caseId, "A");
  of("list")[0].d.resolve({ items: [item(1), item(2)], next: "t2" });
  of("summary")[0].d.resolve({ groups: [{ kind: "operation", family: "Wfm", name: "A", missing: "end", n: 3, causes: { end_of_log: 3 } }], total: 3 });
  await loading;
  assert.equal(flow.state.status, "ready");
  assert.equal(flow.state.next, "t2");
  assert.equal(flow.state.summary.total, 3);
  const more = flow.loadMore();
  assert.equal(flow.state.loadingMore, true);
  assert.equal(of("list")[1].params.after, "t2");
  flow.loadMore(); // a second click while loading does nothing
  assert.equal(of("list").length, 2);
  of("list")[1].d.resolve({ items: [item(2), item(3)], next: null }); // item 2 comes back: the page is not shown twice
  await more;
  assert.deepEqual(flow.state.items.map((r) => r.id), [1, 2, 3]);
  assert.equal(flow.state.next, null);
  await flow.loadMore();
  assert.equal(of("list").length, 2, "nothing left to ask");
});

test("an empty answer is a ready state with no items; a failure is an error with the message and Refresh tries again", async () => {
  const { flow, of } = setup();
  let loading = flow.refresh();
  of("list")[0].d.resolve({ items: [], next: null });
  of("summary")[0].d.resolve({ groups: [], total: 0 });
  await loading;
  assert.equal(flow.state.status, "ready");
  assert.equal(flow.state.items.length, 0);
  loading = flow.refresh();
  of("list")[1].d.reject(new Error("boom"));
  of("summary")[1].d.reject(new Error("sum boom"));
  await loading;
  assert.equal(flow.state.status, "error");
  assert.equal(flow.state.error, "boom");
  assert.equal(flow.state.summary.status, "error");
  assert.equal(flow.state.summary.error, "sum boom");
  loading = flow.refresh();
  assert.equal(flow.state.status, "loading");
  of("list")[2].d.resolve({ items: [item(1)], next: null });
  of("summary")[2].d.resolve({ groups: [], total: 0 });
  await loading;
  assert.equal(flow.state.status, "ready");
  assert.equal(flow.state.error, null);
});

test("a failed Load more keeps the list and the button, with its own message; the next try goes on", async () => {
  const { flow, of } = setup();
  const loading = flow.refresh();
  of("list")[0].d.resolve({ items: [item(1), item(2)], next: "t2" });
  of("summary")[0].d.resolve({ groups: [], total: 0 });
  await loading;
  const failing = flow.loadMore();
  of("list")[1].d.reject(new Error("net"));
  await failing;
  assert.equal(flow.state.status, "ready");
  assert.equal(flow.state.items.length, 2);
  assert.equal(flow.state.next, "t2");
  assert.equal(flow.state.moreError, "net");
  assert.equal(flow.state.loadingMore, false);
  const again = flow.loadMore();
  assert.equal(flow.state.moreError, null);
  of("list")[2].d.resolve({ items: [item(3)], next: null });
  await again;
  assert.equal(flow.state.items.length, 3);
});

test("a case change drops every answer of the old case and the lists of the old case", async () => {
  const { flow, current, of } = setup();
  const first = flow.refresh();
  current.caseId = "B";
  const second = flow.reset();
  assert.equal(of("list")[0].signal.aborted, true);
  assert.equal(of("summary")[0].signal.aborted, true);
  of("list")[0].d.resolve({ items: [item(1)], next: "old" }); // late answer of A
  of("summary")[0].d.resolve({ groups: [{ kind: "operation", name: "old", n: 9, causes: {} }], total: 9 });
  await first;
  assert.equal(flow.state.items.length, 0);
  assert.equal(flow.state.summary.groups.length, 0);
  assert.equal(of("list")[1].caseId, "B");
  of("list")[1].d.resolve({ items: [item(5)], next: null });
  of("summary")[1].d.resolve({ groups: [], total: 0 });
  await second;
  assert.deepEqual(flow.state.items.map((r) => r.id), [5]);
  assert.equal(flow.state.next, null);
});

test("without a case nothing is asked", async () => {
  const { flow, current, of } = setup();
  current.caseId = null;
  await flow.refresh();
  assert.equal(of("list").length + of("summary").length, 0);
  assert.equal(flow.state.status, "nocase");
});

test("a filter change drops the page in flight and the late answer never lands; the summary follows kind and status only", async () => {
  const { flow, of } = setup();
  const first = flow.refresh();
  of("list")[0].d.resolve({ items: [item(1)], next: null });
  of("summary")[0].d.resolve({ groups: [], total: 0 });
  await first;
  const a = flow.setFilters({ status: "open" });
  const b = flow.setFilters({ status: "truncated" });
  assert.deepEqual(of("list")[1].params, { limit: 2, status: "open" });
  assert.equal(of("list")[1].signal.aborted, true);
  assert.deepEqual(of("list")[2].params, { limit: 2, status: "truncated" });
  assert.deepEqual(of("summary")[2].params, { status: "truncated" });
  of("list")[1].d.resolve({ items: [item(9)], next: null }); // the old filter's page, late
  of("list")[2].d.resolve({ items: [item(2)], next: null });
  of("summary")[1].d.resolve({ groups: [], total: 0 });
  of("summary")[2].d.resolve({ groups: [], total: 4 });
  await Promise.all([a, b]);
  assert.deepEqual(flow.state.items.map((r) => r.id), [2]);
  assert.equal(flow.state.summary.total, 4);
  const lists = of("list").length;
  const sums = of("summary").length;
  const named = flow.setFilters({ name: "X" });
  assert.equal(of("list").length, lists + 1);
  assert.equal(of("summary").length, sums, "the name does not narrow the summary");
  assert.equal(of("list").at(-1).params.name, "X");
  of("list").at(-1).d.resolve({ items: [], next: null });
  await named;
});

test("the cause filter works on what is loaded, with no request; the same value again does nothing", async () => {
  const { flow, of } = setup({ limit: 5 });
  const loading = flow.refresh();
  of("list")[0].d.resolve({ items: [item(1), item(2, { cause: "superseded", proven: true }), item(3)], next: null });
  of("summary")[0].d.resolve({ groups: [], total: 0 });
  await loading;
  const version = flow.state.version;
  await flow.setFilters({ cause: "superseded" });
  assert.deepEqual(flow.state.items.map((r) => r.id), [2]);
  assert.equal(flow.state.version > version, true, "the panel redraws the list");
  assert.equal(of("list").length, 1);
  await flow.setFilters({ cause: "superseded" });
  assert.equal(flow.state.version, version + 1);
  await flow.setFilters({ cause: "" });
  assert.deepEqual(flow.state.items.map((r) => r.id), [1, 2, 3]);
});

test("with a cause filter and nothing loaded that matches, further pages are read, a bounded number of them", async () => {
  const { flow, of } = setup({ limit: 1, maxPages: 3 });
  const loading = flow.refresh();
  of("list")[0].d.resolve({ items: [item(1)], next: "t1" });
  of("summary")[0].d.resolve({ groups: [], total: 0 });
  await loading;
  const filtering = flow.setFilters({ cause: "timeout" });
  assert.equal(flow.state.items.length, 0);
  assert.equal(of("list")[1].params.after, "t1");
  of("list")[1].d.resolve({ items: [item(2)], next: "t2" });
  await new Promise((r) => setImmediate(r));
  assert.equal(of("list")[2].params.after, "t2", "keeps reading while nothing matches");
  of("list")[2].d.resolve({ items: [item(3, { cause: "timeout", proven: true })], next: "t3" });
  await filtering;
  assert.deepEqual(flow.state.items.map((r) => r.id), [3]);
  assert.equal(of("list").length, 3, "stops once there is a match");
  // bounded: three pages without a match stop the step, and the button stays for the next one
  const more = flow.setFilters({ cause: "capacity" });
  await new Promise((r) => setImmediate(r));
  for (let i = 0; i < 3; i += 1) {
    of("list").at(-1).d.resolve({ items: [item(10 + i)], next: "n" + i });
    await new Promise((r) => setImmediate(r));
  }
  await more;
  assert.equal(of("list").length, 6);
  assert.equal(flow.state.next, "n2");
  assert.equal(flow.state.items.length, 0);
  assert.equal(flow.state.loadingMore, false);
});

test("a cause filter that was set before a refresh still applies to the new pages and the dedup keeps its ids", async () => {
  const { flow, of } = setup({ limit: 5 });
  let loading = flow.refresh();
  of("list")[0].d.resolve({ items: [item(1)], next: null });
  of("summary")[0].d.resolve({ groups: [], total: 0 });
  await loading;
  await flow.setFilters({ cause: "timeout" });
  loading = flow.refresh();
  of("list")[1].d.resolve({ items: [item(1), item(2, { cause: "timeout", proven: true })], next: null });
  of("summary")[1].d.resolve({ groups: [], total: 0 });
  await loading;
  assert.deepEqual(flow.state.items.map((r) => r.id), [2]);
});

test("a malformed page is read as empty and an unusable next cursor ends the list", async () => {
  const { flow, of } = setup();
  const loading = flow.refresh();
  of("list")[0].d.resolve({ items: "no", next: "" });
  of("summary")[0].d.resolve("nonsense");
  await loading;
  assert.equal(flow.state.status, "ready");
  assert.equal(flow.state.items.length, 0);
  assert.equal(flow.state.next, null);
  assert.equal(flow.state.summary.status, "ready");
  assert.equal(flow.state.summary.total, 0);
});

test("reset clears the selectors (a new case starts with every filter at any)", async () => {
  const { flow, of } = setup();
  const a = flow.setFilters({ kind: "startup", cause: "timeout", name: "X" });
  of("list")[0].d.resolve({ items: [], next: null });
  of("summary")[0].d.resolve({ groups: [], total: 0 });
  await a;
  const b = flow.reset();
  assert.deepEqual(flow.state.filters, { kind: "", status: "", cause: "", name: "" });
  of("list")[1].d.resolve({ items: [], next: null });
  of("summary")[1].d.resolve({ groups: [], total: 0 });
  await b;
});

test("a server that answers after the abort cannot make an old page, an old summary or an old Load more land", async () => {
  const { flow, current, of } = setup({ honorAbort: false });
  const first = flow.refresh();
  of("list")[0].d.resolve({ items: [item(1)], next: "t1" });
  of("summary")[0].d.resolve({ groups: [], total: 0 });
  await first;
  const more = flow.loadMore();
  const filtering = flow.setFilters({ status: "open" }); // while the page of Load more is on the way
  of("list")[1].d.resolve({ items: [item(77)], next: "late" }); // the old Load more answers late
  of("list")[2].d.resolve({ items: [item(2)], next: null });
  of("summary")[1].d.resolve({ groups: [], total: 2 });
  await Promise.all([more, filtering]);
  assert.deepEqual(flow.state.items.map((r) => r.id), [2]);
  assert.equal(flow.state.next, null);
  assert.equal(flow.state.loadingMore, false);
  // the summary of an older request, answering after the newer one
  const a = flow.setFilters({ kind: "startup" });
  const b = flow.setFilters({ kind: "operation" });
  const sums = of("summary");
  sums.at(-1).d.resolve({ groups: [], total: 5 });
  sums.at(-2).d.resolve({ groups: [], total: 99 });
  of("list").at(-1).d.resolve({ items: [], next: null });
  of("list").at(-2).d.resolve({ items: [item(55)], next: null });
  await Promise.all([a, b]);
  assert.equal(flow.state.summary.total, 5);
  assert.deepEqual(flow.state.items, []);
  current.caseId = "B";
  const reset = flow.reset();
  of("list").at(-1).d.resolve({ items: [item(8)], next: null });
  of("summary").at(-1).d.resolve({ groups: [], total: 1 });
  await reset;
  assert.deepEqual(flow.state.items.map((r) => r.id), [8]);
});

test("the process column of a missing step uses the pool label", () => {
  const event = { id: 4, kind: "operation", name: "FunctionHostJob", family: "DMSFH", process: "DMS_FunctionHost_7", pool: "DMS_FunctionHost", instance: 7, ts_start: 1000 };
  assert.equal(buildItemRow({ event, missing: "end" }).process, "DMSFH / DMS_FunctionHost #7");
});
