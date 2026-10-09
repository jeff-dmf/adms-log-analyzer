// Pure helpers of the adapter dashboard.
import test from "node:test";
import assert from "node:assert/strict";

import {
  buildStatsRows, buildStatsTotals, groupExceptions, histogramToChart, inferBucketMs, levelRows, lifecycleRows,
  methodOptions, seriesToChart,
} from "../../src/dmslog/web/js/dashboard-model.js";

const GROUPS = {
  groups: [
    { family: "Wfm", name: "B", n: 5, ok: 3, business_error: 1, fail: 1, timeout: 0, open: 0, truncated: 0, p50_ms: 10, p90_ms: 2000, p99_ms: 5000, max_ms: 61000, avg_ms: 100 },
    { family: "Ont", name: "A", n: 9, ok: 9, business_error: 0, fail: 0, timeout: 0, open: 1, truncated: 2, p50_ms: 5, p90_ms: 6, p99_ms: 7, max_ms: 8, avg_ms: 6 },
  ],
  totals: { n: 14, ok: 12, business_error: 1, fail: 1, timeout: 0, open: 1, truncated: 2, p50_ms: 6, p90_ms: 7, p99_ms: 8, max_ms: 61000 },
};

test("stats rows are sorted by count, durations formatted, open and truncated merged", () => {
  const rows = buildStatsRows(GROUPS);
  assert.deepEqual(rows.map((r) => r.name), ["A", "B"]);
  assert.equal(rows[0].open, 3);
  assert.equal(rows[1].p90, "2 s");
  assert.equal(rows[1].max, "1 min 1 s");
  assert.deepEqual(buildStatsRows(null), []);
  const totals = buildStatsTotals(GROUPS);
  assert.equal(totals.n, 14);
  assert.equal(totals.rawName, "");
  assert.equal(buildStatsTotals({}), null);
});

test("histogram: seconds on x, empty series dropped, bucket width inferred", () => {
  const response = {
    bucket: "minute",
    bucket_ms: 60000,
    truncated: false,
    buckets: [
      { t: 120000, total: 3, counts: { ok: 1, fail: 2 } },
      { t: 0, total: 4, counts: { ok: 4 } },
      { t: 60000, total: 4, counts: { ok: 3, fail: 1 } },
    ],
  };
  const chart = histogramToChart(response);
  assert.deepEqual(chart.xs, [0, 60, 120]);
  assert.deepEqual(chart.series.map((s) => s.key), ["ok", "fail"]);
  assert.deepEqual(chart.series[0].values, [4, 3, 1]);
  assert.equal(chart.bucketMs, 60000);
  assert.deepEqual(chart.windowMs, { from: 0, to: 180000 });
  assert.equal(chart.total, 11);
  // timeout and unknown have no series of their own: they are summed into "other"
  const other = histogramToChart({ buckets: [{ t: 0, total: 5, counts: { ok: 1, timeout: 2, unknown: 2 } }] });
  assert.deepEqual(other.series.map((s) => [s.key, s.values[0]]), [["ok", 1], ["other", 4]]);
  assert.equal(histogramToChart({ buckets: [] }).windowMs, null);
  assert.equal(histogramToChart(null).series.length, 0);
  assert.equal(inferBucketMs([{ t: 5 }]), 60000);
});

test("method options and series: cumulative versus delta, gaps kept as null", () => {
  assert.equal(methodOptions([{ side: "in", method: "M", calls: 7 }])[0].label, "in: M (7)");
  const samples = [
    { ts: 2000, calls: 10, d_calls: 4, req_bytes: 100, d_req_bytes: 40, resp_bytes: 9, d_resp_bytes: 3 },
    { ts: 1000, calls: 6, d_calls: null, req_bytes: 60, d_req_bytes: null, resp_bytes: 6, d_resp_bytes: null },
    { ts: 2000, calls: 11, d_calls: 5, req_bytes: 101, d_req_bytes: 41, resp_bytes: 10, d_resp_bytes: 4 },
  ];
  const cumulative = seriesToChart(samples, "calls", "cumulative");
  assert.deepEqual(cumulative.xs, [1, 2]);
  assert.deepEqual(cumulative.ys, [6, 11]); // a repeated instant keeps the last sample
  const delta = seriesToChart(samples, "calls", "delta");
  assert.deepEqual(delta.ys, [null, 5]);
  assert.equal(delta.hasData, true);
  assert.deepEqual(seriesToChart(samples, "req", "delta").ys, [null, 41]);
  assert.deepEqual(seriesToChart(samples, "resp", "cumulative").ys, [6, 10]);
  assert.equal(seriesToChart([], "calls", "delta").hasData, false);
  assert.equal(seriesToChart([{ ts: 1, d_calls: null }], "calls", "delta").hasData, false);
});

test("exceptions are grouped by key with count, first and last occurrence", () => {
  const events = [
    { id: 1, kind: "exception", key: "sig-a", name: "TimeoutException", ts_start: 3000 },
    { id: 2, kind: "exception", key: "sig-b", name: "IOException", ts_start: 1000 },
    { id: 3, kind: "exception", key: "sig-a", name: "TimeoutException", ts_start: 1000 },
    { id: 4, kind: "exception", key: "sig-a", name: "TimeoutException", ts_start: 9000 },
    { id: 5, kind: "exception", key: "", name: "", ts_start: null },
  ];
  const groups = groupExceptions(events);
  assert.deepEqual(groups.map((g) => [g.signature, g.count]), [["sig-a", 3], ["(no key)", 1], ["sig-b", 1]]);
  assert.equal(groups[0].first.id, 3);
  assert.equal(groups[0].last.id, 4);
  assert.equal(groups[0].firstTime, "1970-01-01 00:00:01.000");
  assert.equal(groups[1].first, null);
  assert.deepEqual(groupExceptions(null), []);
});

test("level windows and lifecycle events are listed in time order", () => {
  const levels = levelRows([
    { id: 2, kind: "level_window", name: "Verbose", ts_start: 5000, ts_end: 9000, duration_ms: 4000, family: "Wfm", process: "WfmAdapter" },
    { id: 1, kind: "level_window", name: "Info", ts_start: 1000, ts_end: null, duration_ms: null },
  ]);
  assert.deepEqual(levels.map((r) => r.level), ["Info", "Verbose"]);
  assert.equal(levels[0].to, "");
  assert.equal(levels[1].duration, "4 s");

  const rows = lifecycleRows([
    [{ id: 1, kind: "startup", name: "start", ts_start: 3000 }],
    [{ id: 2, kind: "restart", name: "process_dead", ts_start: 1000 }],
    [{ id: 3, kind: "gap", name: "gap", ts_start: 2000, duration_ms: 3600000, detail: { possible_clock_jump: true } }],
  ]);
  assert.deepEqual(rows.map((r) => r.kindLabel), ["Restart", "Gap", "Startup"]);
  assert.equal(rows[1].suspect, true);
  assert.equal(rows[1].what, "gap, 1 h 0 min");
  assert.equal(rows[0].suspect, false);
});
