// Aggregate panel: pure model (choices, request, table, chart data) and the request flow (fake api).
import test from "node:test";
import assert from "node:assert/strict";

import {
  ERRORS_ONLY, EXAMPLES, MAX_LIMIT, MAX_MEASURES, applicableLookups, buildRequest, buildView, chartData,
  createAggregateFlow, csvFileName, dimLabel, dimOptions, drillFilter, emptyForm, exampleForm, groupText,
  measureLabel, measureOptions, parseLimit, tableText,
} from "../../src/dmslog/web/js/aggregate-model.js";
import { S } from "../../src/dmslog/web/js/strings.js";

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

test("choices: dimensions and measures follow the rows; bad key and field names are dropped", () => {
  const dims = dimOptions("entries", ["msgid", "bad type", "msgid"], ["op", "1bad"]).map((o) => o.value);
  assert.deepEqual(dims, [
    "tpl", "source", "collection", "family", "sev", "level", "tid", "pid",
    "bucket:1m", "bucket:5m", "bucket:15m", "bucket:1h", "bucket:1d", "key.msgid", "fields.op",
  ]);
  const evDims = dimOptions("events", ["msgid"], ["op"]).map((o) => o.value);
  assert.ok(evDims.includes("name") && evDims.includes("bucket:1h") && !evDims.includes("key.msgid"));
  const measures = measureOptions("events", [], []).map((o) => o.value);
  assert.equal(measures[0], "count");
  assert.ok(measures.includes("distinct:name") && measures.includes("p90:duration") && measures.includes("sum:entries"));
  assert.ok(!measures.some((m) => m.startsWith("distinct:bucket")));
  const entryMeasures = measureOptions("entries", ["msgid"], ["n"]).map((o) => o.value);
  assert.ok(entryMeasures.includes("distinct:key.msgid") && entryMeasures.includes("p99:fields.n"));
  assert.ok(!entryMeasures.some((m) => m.endsWith(":duration")));
});

test("labels: readable names for dimensions, measures and buckets", () => {
  assert.equal(dimLabel("tpl"), "Message type");
  assert.equal(dimLabel("bucket:1h"), "Time (1 hour)");
  assert.equal(dimLabel("key.msgid"), "Key msgid");
  assert.equal(measureLabel("p90:duration"), "p90 duration (ms)");
  assert.equal(measureLabel("distinct:source"), "Distinct Source");
  assert.equal(measureLabel("avg:fields.n"), "Average n");
  assert.equal(measureLabel("count"), S.aggCount);
});

test("request: groups and measures deduplicated, limit checked, event lists, order, lookups, only errors", () => {
  const form = {
    ...emptyForm(), rows: "events", groupBy: ["name", "name", "bucket:1h"], measures: ["count", "p90:duration", "", "count"],
    orderBy: "p90:duration", orderDir: "desc", limit: "50", eventKinds: " operation , ", eventNames: "",
    eventStatuses: "fail,timeout", lookups: ["operation.direction", "error.reason"], onlyErrors: true,
  };
  const { request, error } = buildRequest(form, { source: [1] });
  assert.equal(error, undefined);
  assert.deepEqual(request, {
    rows: "events", filter: { and: [{ source: [1] }, ERRORS_ONLY] }, group_by: ["name", "bucket:1h"],
    measures: ["count", "p90:duration"], limit: 50, events: { kind: ["operation"], status: ["fail", "timeout"] },
    order: { by: "p90:duration", dir: "desc" }, lookups: ["operation.direction"],
  });
  assert.equal(buildRequest({ ...emptyForm(), measures: ["", "", "", ""] }, null).error, S.aggNeedMeasure);
  assert.equal(buildRequest({ ...emptyForm(), groupBy: ["bucket:1h", "bucket:1d", ""] }, null).error, S.aggOneBucket);
  for (const limit of ["0", "1001", "2.5", "x"]) {
    assert.match(buildRequest({ ...emptyForm(), limit }, null).error, /Rows must be/);
  }
  assert.equal(parseLimit(String(MAX_LIMIT)), MAX_LIMIT);
  const plain = buildRequest({ ...emptyForm(), orderBy: "sev" }, null).request;
  assert.deepEqual(plain, { rows: "entries", filter: null, group_by: ["tpl"], measures: ["count"], limit: 100 });
  const long = buildRequest({ ...emptyForm(), rows: "events", eventNames: "x".repeat(201) }, null);
  assert.equal(long.error, S.aggBadEventList);
  assert.deepEqual(applicableLookups({ ...emptyForm(), groupBy: ["key.errcode", "", ""] }), ["error.reason", "error.severity"]);
});

test("examples: each ready-made question gives a valid request", () => {
  assert.ok(EXAMPLES.length >= 3 && EXAMPLES.length <= 6);
  for (const example of EXAMPLES) {
    const form = exampleForm(example.id);
    assert.equal(form.measures.length, MAX_MEASURES);
    const { request, error } = buildRequest(form, null);
    assert.equal(error, undefined, example.id);
    assert.ok(request.measures.length >= 1 && typeof example.title() === "string" && example.title().length > 5);
  }
  assert.equal(exampleForm("nope"), null);
  assert.deepEqual(buildRequest(exampleForm("op_duration"), null).request.events, { kind: ["operation"] });
});

const ANSWER = {
  rows_kind: "entries",
  columns: [
    { id: "tpl", role: "group" }, { id: "bucket:1h", role: "group" }, { id: "count", role: "measure" },
    { id: "avg:fields.n", role: "measure" }, { id: "error.reason", role: "lookup" },
  ],
  rows: [
    { values: [7, 3_600_000, 1234, 2.5, "Bad\tthing"], labels: ["Request <b>sent</b>", null], drill: { and: [{ tpl: 7 }, { ts: { ge: 3_600_000, lt: 7_200_000 } }] } },
    { values: [0, 0, 5, null, null], labels: [null, null], drill: null },
    { values: [7, 0, 9, 1, null], labels: ["Request <b>sent</b>", null], drill: { tpl: 7 } },
  ],
  groups: 10, truncated: true, total: 1300, exact: false, method: "sample", notes: ["lookups_without_pack"],
};

test("view: headers, cell texts, drill with the panel's extra condition, notices and summary", () => {
  const view = buildView(ANSWER, false, ERRORS_ONLY);
  assert.deepEqual(view.headers, ["Message type", "Time (1 hour)", "Count", "Average n", "Error reason"]);
  assert.deepEqual(view.numeric, [false, false, true, true, false]);
  assert.deepEqual(view.rows[0].cells, ["Request <b>sent</b>", "1970-01-01 01:00:00.000", "1,234", "2.5", "Bad\tthing"]);
  assert.deepEqual(view.rows[1].cells.slice(0, 4), [S.dstUnclassified, "1970-01-01 00:00:00.000", "5", ""]);
  assert.deepEqual(view.rows[0].drill, { and: [ERRORS_ONLY, { tpl: 7 }, { ts: { ge: 3_600_000, lt: 7_200_000 } }] });
  assert.equal(view.rows[1].drill, null);
  assert.deepEqual(view.notices, [S.aggSampleWarning, S.aggPackMissing]);
  assert.equal(view.summary, "Showing 3 of 10 groups. Total about 1,300.");
  assert.equal(view.canComputeExactly, true);
  assert.equal(buildView({ ...ANSWER, exact: false }, true).canComputeExactly, false);
  assert.ok(buildView({ ...ANSWER, exact: false }, true).notices.includes(S.aggExactTooSlow));
  const text = tableText(view);
  assert.equal(text.split("\n")[1], "Request <b>sent</b>\t1970-01-01 01:00:00.000\t1,234\t2.5\tBad thing");
  assert.equal(groupText("sev", 2, null), "Error");
  assert.equal(groupText("tid", 255, null), "0xff");
  assert.equal(groupText("name", null, null), S.aggEmptyValue);
});

test("chart: time series per other group (largest first), bars without a bucket, none without groups", () => {
  const time = chartData(ANSWER);
  assert.equal(time.kind, "time");
  assert.deepEqual(time.xs, [0, 3600]);
  assert.deepEqual(time.series.map((s) => s.label), ["Request <b>sent</b>", S.dstUnclassified]);
  assert.deepEqual(time.series[0].values, [9, 1234]);
  assert.deepEqual(time.series[1].values, [5, null]);
  const bars = chartData({
    columns: [{ id: "sev", role: "group" }, { id: "count", role: "measure" }],
    rows: [{ values: [2, 10], labels: [null] }, { values: [4, 3], labels: [null] }],
  });
  assert.deepEqual(bars, { kind: "bars", measure: "Count", labels: ["Error", "Info"], series: [{ label: "Count", values: [10, 3] }], truncated: false });
  assert.equal(chartData({ columns: [{ id: "count", role: "measure" }], rows: [{ values: [3] }] }), null);
  assert.equal(chartData(null), null);
  const many = chartData({
    columns: [{ id: "name", role: "group" }, { id: "bucket:1m", role: "group" }, { id: "count", role: "measure" }],
    rows: Array.from({ length: 12 }, (_v, k) => ({ values: ["op" + k, 60_000, k], labels: [null, null] })),
  });
  assert.equal(many.series.length, 8);
  assert.equal(many.series[0].label, "op11");
  assert.equal(many.truncated, true);
});

test("drill filter and CSV file name", () => {
  assert.deepEqual(drillFilter({ sev: 1 }, { tpl: 2 }), { and: [{ sev: 1 }, { tpl: 2 }] });
  assert.deepEqual(drillFilter({ sev: 1 }, null), { sev: 1 });
  assert.equal(csvFileName("my case/1", new Date(Date.UTC(2026, 9, 13, 8, 5, 6))), "dmslog-my_case_1-aggregate-20261013-080506.csv");
});

test("flow: one request at a time, stale answers dropped, exact re-run, CSV of the last request", async () => {
  const pending = [];
  const calls = [];
  const api = {
    aggregate: (caseId, body, signal) => {
      calls.push([caseId, body]);
      const d = deferred();
      pending.push(d);
      signal.addEventListener("abort", () => d.reject(Object.assign(new Error("aborted"), { name: "AbortError" })));
      return d.promise;
    },
    aggregateCsv: async (caseId, body) => ({ data: "csv:" + caseId + ":" + JSON.stringify(body) }),
  };
  const states = [];
  let caseId = "C1";
  const flow = createAggregateFlow({ api, getCase: () => caseId, onState: (s) => states.push(s.status) });
  await assert.rejects(flow.csv(), /Run the aggregation first/);
  const first = flow.run({ group_by: ["tpl"] });
  const second = flow.run({ group_by: ["sev"] });
  pending[1].resolve({ columns: [], rows: [], groups: 0, exact: true });
  await second;
  await first;
  assert.equal(flow.state.status, "ready");
  assert.deepEqual(flow.state.request, { group_by: ["sev"] });
  flow.markStale();
  assert.equal(flow.state.stale, true);
  const exact = flow.run({ group_by: ["sev"] }, { exact: true });
  assert.deepEqual(calls.at(-1), ["C1", { group_by: ["sev"], exact: true }]);
  pending[2].reject(Object.assign(new Error("The aggregation did not finish in time"), { status: 408 }));
  await exact;
  assert.equal(flow.state.status, "error");
  assert.match(flow.state.error, /did not finish in time/);
  assert.equal(await flow.csv(), 'csv:C1:{"group_by":["sev"],"exact":true}');
  caseId = null;
  await flow.run({ group_by: ["tpl"] });
  assert.equal(flow.state.status, "idle");
  assert.equal(flow.state.error, S.aggNoCase);
  flow.cancel();
  assert.equal(flow.state.request, null);
});

test("chart: series are told apart by group values, not by their label", () => {
  const time = chartData({
    columns: [{ id: "source", role: "group" }, { id: "bucket:1m", role: "group" }, { id: "count", role: "measure" }],
    rows: [
      { values: [1, 60_000, 10], labels: ["a.log", null] },
      { values: [2, 60_000, 20], labels: ["a.log", null] },
    ],
  });
  assert.equal(time.series.length, 2);
  assert.deepEqual(time.series.map((s) => [s.label, s.values]), [["a.log", [20]], ["a.log", [10]]]);
});

test("flow: a view or case change drops the pending answer; CSV and drill stay bound to the result's case", async () => {
  const pending = [];
  const csvCalls = [];
  const api = {
    aggregate: (caseId, body, signal) => {
      const d = deferred();
      pending.push({ caseId, d });
      signal.addEventListener("abort", () => d.reject(Object.assign(new Error("aborted"), { name: "AbortError" })));
      return d.promise;
    },
    aggregateCsv: async (caseId, body) => { csvCalls.push([caseId, body]); return { data: "csv" }; },
  };
  let caseId = "A";
  const flow = createAggregateFlow({ api, getCase: () => caseId, onState: () => {} });
  const running = flow.run({ group_by: ["tpl"] });
  caseId = "B";
  flow.markStale(); // A -> B while A's request is pending
  pending[0].d.resolve({ columns: [], rows: [], groups: 0, exact: true });
  await running;
  assert.equal(flow.state.status, "idle");
  assert.equal(flow.state.data, null);
  assert.equal(flow.state.caseId, null);
  await assert.rejects(flow.csv(), /Run the aggregation first/);
  // a ready result of A, then the case becomes B: the result is cleared
  caseId = "A";
  const again = flow.run({ group_by: ["sev"] });
  pending[1].d.resolve({ columns: [], rows: [], groups: 0, exact: true });
  await again;
  assert.equal(flow.state.caseId, "A");
  flow.markStale(); // same case, other view: kept, marked stale, CSV still of case A
  assert.equal(flow.state.stale, true);
  caseId = "B";
  assert.equal(await flow.csv(), "csv");
  assert.deepEqual(csvCalls.at(-1), ["A", { group_by: ["sev"] }]);
  flow.markStale();
  assert.equal(flow.state.status, "idle");
  assert.equal(flow.state.data, null);
});
