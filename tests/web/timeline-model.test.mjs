import test from "node:test";
import assert from "node:assert/strict";
import {
  ERRORS_ONLY_HIDDEN,
  NORMAL_LINE_FRACTION,
  NORMAL_SERIES_KEY,
  alarmDrawOrder,
  alarmLayers,
  alarmReadoutAt,
  alarmTotals,
  axisLabel,
  isErrorsOnly,
  parsePx,
  stripHeight,
  BUCKET_LADDER_MS,
  MAX_BUCKETS,
  TimelineLoader,
  chooseBucketMs,
  colorIndex,
  compactCount,
  formatBucketStart,
  rangeToBox,
  readoutAt,
  requestKey,
  selectionToRange,
  splitTimeFilter,
  stackSeries,
  toChartData,
  widthParts,
} from "../../src/dmslog/web/js/timeline-model.js";

test("chooseBucketMs picks the smallest ladder width that fits the pixels", () => {
  assert.equal(chooseBucketMs(60 * 1000, 800), 1000); // 60 s over 200 bars
  assert.equal(chooseBucketMs(24 * 3600 * 1000, 800, 60000), 600000); // 144 bars of 10 min
  assert.equal(chooseBucketMs(24 * 3600 * 1000, 800, 1000), 600000);
  assert.equal(chooseBucketMs(3600 * 1000, 4000, 60000), 60000); // never below the minimum
  assert.equal(chooseBucketMs(1e15, 800), BUCKET_LADDER_MS[BUCKET_LADDER_MS.length - 1]);
  assert.equal(chooseBucketMs(0, 0), 1000);
  for (const span of [1e3, 1e5, 1e7, 1e9]) {
    const width = chooseBucketMs(span, 1600);
    assert.ok(BUCKET_LADDER_MS.includes(width));
    assert.ok(span / width <= Math.min(MAX_BUCKETS, 400) || width === BUCKET_LADDER_MS.at(-1));
  }
});

test("splitTimeFilter removes top-level ts conditions and reads the range", () => {
  assert.deepEqual(splitTimeFilter(null), { filter: null, range: null });
  assert.deepEqual(splitTimeFilter({}), { filter: {}, range: null });
  const text = { text: { fts: "x" } };
  assert.deepEqual(splitTimeFilter({ and: [{ ts: { ge: 100, le: 200 } }, text] }), {
    filter: text,
    range: { from: 100, to: 200 },
  });
  assert.deepEqual(splitTimeFilter({ ts: { gt: 9, lt: 20 } }), { filter: null, range: { from: 10, to: 19 } });
  const nested = { and: [{ sev: { le: 2 } }, text, { ts: { ge: 5 } }] };
  assert.deepEqual(splitTimeFilter(nested), {
    filter: { and: [{ sev: { le: 2 } }, text] },
    range: { from: 5, to: null },
  });
  // A ts under OR/NOT is part of the logic and stays.
  const or = { or: [{ ts: { ge: 1 } }, text] };
  assert.deepEqual(splitTimeFilter(or), { filter: or, range: null });
  const input = { and: [{ ts: { ge: 1 } }, text] };
  splitTimeFilter(input);
  assert.deepEqual(input, { and: [{ ts: { ge: 1 } }, text] }); // input untouched
});

test("requestKey changes with case, filter or split", () => {
  const a = requestKey("c", { sev: 1 }, "sev");
  assert.equal(a, requestKey("c", { sev: 1 }, "sev"));
  assert.notEqual(a, requestKey("d", { sev: 1 }, "sev"));
  assert.notEqual(a, requestKey("c", { sev: 2 }, "sev"));
  assert.notEqual(a, requestKey("c", { sev: 1 }, "source"));
  assert.equal(requestKey("c", undefined, "sev"), requestKey("c", null, "sev"));
});

const RESPONSE = {
  bucket_ms: 60000,
  start: 1760000000000,
  n_buckets: 3,
  split: "sev",
  total: 9,
  series: [
    { key: 1, counts: [1, 0, 2] },
    { key: 4, counts: [0, 5, 1] },
  ],
};

test("toChartData converts the response to uPlot columns (x in seconds)", () => {
  const data = toChartData(RESPONSE, (k) => "sev" + k);
  assert.deepEqual(data.xs, [1760000000, 1760000060, 1760000120]);
  assert.deepEqual(data.window, { from: 1760000000000, to: 1760000180000 });
  assert.deepEqual(data.series.map((s) => [s.key, s.label, s.values]), [
    [1, "sev1", [1, 0, 2]],
    [4, "sev4", [0, 5, 1]],
  ]);
  assert.equal(data.total, 9);
  const empty = toChartData({ bucket_ms: 60000, start: null, n_buckets: 0, series: [], total: 0 }, String);
  assert.deepEqual(empty.xs, []);
  assert.equal(empty.window, null);
});

test("stackSeries gives cumulative tops, top layer first, honouring hidden series", () => {
  const data = toChartData(RESPONSE, String);
  const all = stackSeries(data.series, new Set(), 3);
  assert.deepEqual(all.draw.map((d) => d.index), [1, 0]);
  assert.deepEqual(all.draw[0].tops, [1, 5, 3]);
  assert.deepEqual(all.draw[1].tops, [1, 0, 2]);
  assert.equal(all.max, 5);
  const hideFirst = stackSeries(data.series, new Set([1]), 3);
  assert.deepEqual(hideFirst.draw[0].tops, [0, 5, 1]);
  assert.deepEqual(hideFirst.draw[1].tops, [0, 0, 0]);
  assert.equal(hideFirst.max, 5);
  assert.equal(stackSeries([], new Set(), 0).max, 0);
});

test("readoutAt lists the visible non-zero series of one bucket", () => {
  const data = toChartData(RESPONSE, (k) => "s" + k);
  assert.deepEqual(readoutAt(data.series, new Set(), 2), [
    { key: 1, label: "s1", count: 2 },
    { key: 4, label: "s4", count: 1 },
  ]);
  assert.deepEqual(readoutAt(data.series, new Set([4]), 1), []);
  assert.deepEqual(readoutAt(data.series, new Set(), 99), []);
});

test("selectionToRange converts and clamps a drag to the data window", () => {
  const win = { from: 1000000, to: 1180000 };
  assert.deepEqual(selectionToRange(1060, 1120, win, 800), { from: 1060000, to: 1120000 });
  assert.deepEqual(selectionToRange(1120, 1060, win, 800), { from: 1060000, to: 1120000 }); // right-to-left
  assert.deepEqual(selectionToRange(900, 5000, win, 800), { from: 1000000, to: 1179999 }); // clamped
  assert.equal(selectionToRange(1060, 1060.2, win, 800), null); // a click, too narrow
  assert.equal(selectionToRange(2000, 3000, win, 800), null); // outside
  assert.equal(selectionToRange(NaN, 1, win, 800), null);
  assert.equal(selectionToRange(1, 2, null, 800), null);
  assert.deepEqual(selectionToRange(1000.5, 1001.25, win, 0), { from: 1000500, to: 1001250 });
});

test("rangeToBox maps the active time range to a pixel box inside the plot", () => {
  const win = { from: 0, to: 100000 };
  const pos = (sec) => sec * 8; // 800 px for 100 s
  assert.deepEqual(rangeToBox({ from: 10000, to: 19999 }, win, pos, 800), { left: 80, width: 80 });
  assert.deepEqual(rangeToBox({ from: null, to: 9999 }, win, pos, 800), { left: 0, width: 80 });
  assert.deepEqual(rangeToBox({ from: 90000, to: null }, win, pos, 800), { left: 720, width: 80 });
  assert.equal(rangeToBox({ from: 200000, to: 300000 }, win, pos, 800), null);
  assert.equal(rangeToBox(null, win, pos, 800), null);
});

test("labels and formatting helpers", () => {
  assert.equal(colorIndex("sev", 1, 0), 0);
  assert.equal(colorIndex("sev", 5, 3), 4);
  assert.equal(colorIndex("source", 77, 0), 5);
  assert.equal(colorIndex("tpl", 3, 9), 6);
  assert.equal(colorIndex("tpl", null, 2), 13);
  assert.deepEqual([0, 999, 1000, 1250, 3000000, 2e9].map(compactCount), ["0", "999", "1k", "1.3k", "3M", "2G"]);
  assert.equal(formatBucketStart(Date.UTC(2026, 2, 5, 7, 8, 9, 456)), "2026-03-05 07:08:09");
  assert.deepEqual(widthParts(1000), { key: "widthSeconds", n: 1 });
  assert.deepEqual(widthParts(1500), { key: "widthSeconds", n: 1.5 });
  assert.deepEqual(widthParts(300000), { key: "widthMinutes", n: 5 });
  assert.deepEqual(widthParts(7200000), { key: "widthHours", n: 2 });
  assert.deepEqual(widthParts(172800000), { key: "widthDays", n: 2 });
});

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

test("TimelineLoader discards a response that was overtaken by a newer request", async () => {
  const loader = new TimelineLoader();
  const first = deferred();
  const second = deferred();
  const a = loader.load("k1", () => first.promise);
  const b = loader.load("k2", () => second.promise);
  second.resolve("new");
  first.resolve("old"); // arrives late
  assert.deepEqual(await b, { stale: false, data: "new" });
  assert.deepEqual(await a, { stale: true });
});

test("TimelineLoader aborts the previous request and treats AbortError as stale", async () => {
  const loader = new TimelineLoader();
  let firstSignal;
  const a = loader.load("k1", (signal) => {
    firstSignal = signal;
    return new Promise((_res, rej) => {
      signal.addEventListener("abort", () => rej(Object.assign(new Error("aborted"), { name: "AbortError" })));
    });
  });
  const b = loader.load("k2", async () => "fresh");
  assert.equal(firstSignal.aborted, true);
  assert.deepEqual(await a, { stale: true });
  assert.deepEqual(await b, { stale: false, data: "fresh" });
});

test("TimelineLoader rethrows real errors of the current request but not of stale ones", async () => {
  const loader = new TimelineLoader();
  await assert.rejects(loader.load("k", async () => { throw new Error("boom"); }), /boom/);
  const slow = deferred();
  const stale = loader.load("old", () => slow.promise);
  const current = loader.load("new", async () => 1);
  slow.reject(new Error("late failure"));
  assert.deepEqual(await stale, { stale: true });
  assert.deepEqual(await current, { stale: false, data: 1 });
});

test("TimelineLoader.invalidate (case switch / destroy) makes the pending response stale", async () => {
  const loader = new TimelineLoader();
  const pending = deferred();
  const run = loader.load("k", () => pending.promise);
  loader.invalidate();
  pending.resolve("x");
  assert.deepEqual(await run, { stale: true });
});

// Synthetic case of the issue: 160 thousand Info records, 20.6 thousand Errors with one sharp peak.
function peakCase() {
  const n = 10;
  const info = new Array(n).fill(16000);
  const error = [100, 100, 100, 100, 18000, 100, 100, 1000, 500, 500];
  return [
    { key: 2, label: "Error", values: error },
    { key: 4, label: "Info", values: info },
  ];
}

test("alarmLayers: the error peak fills the plot whatever the volume of Info", () => {
  const layers = alarmLayers(peakCase(), new Set(), 10);
  assert.equal(layers.peak, 18000);
  assert.equal(layers.bars.length, 1);
  assert.equal(layers.bars[0].tops[4], 1); // the peak bar is 100% of the strip (acceptance: >= 50%)
  assert.ok(layers.bars[0].tops[4] >= 0.5);
  assert.ok(Math.abs(layers.bars[0].tops[0] - 100 / 18000) < 1e-12);
  // Info (a flat 16000 per bucket) is a line on its own scale, never above 40% of the height.
  assert.equal(layers.normalPeak, 16000);
  assert.ok(layers.line.every((v) => v <= NORMAL_LINE_FRACTION + 1e-12));
  assert.equal(Math.max(...layers.line), NORMAL_LINE_FRACTION);
});

test("alarmLayers: severities stack bottom-up and Info + Noise are summed into one line", () => {
  const series = [
    { key: 3, values: [1, 0, 0] },
    { key: 1, values: [0, 0, 2] },
    { key: 2, values: [1, 0, 1] },
    { key: 4, values: [10, 0, 5] },
    { key: 5, values: [10, 0, 0] },
  ];
  const layers = alarmLayers(series, new Set(), 3);
  assert.deepEqual(layers.bars.map((b) => b.key), [1, 2, 3]); // Critical at the bottom
  assert.equal(layers.peak, 3);
  assert.deepEqual(layers.bars[2].tops, [2 / 3, 0, 1]);
  assert.deepEqual(layers.line, [0.4, 0, 0.1]);
  assert.deepEqual(alarmDrawOrder(layers.bars).map((b) => b.key), [3, 2, 1]);
  // Gaps stay visible: a bucket without records is 0 in the bars and in the line.
  assert.equal(layers.bars[2].tops[1], 0);
  assert.equal(layers.line[1], 0);
});

test("alarmLayers honours hidden series and an empty case", () => {
  const series = peakCase();
  const noNormal = alarmLayers(series, new Set([NORMAL_SERIES_KEY]), 10);
  assert.equal(noNormal.line, null);
  const noError = alarmLayers(series, new Set([2]), 10);
  assert.equal(noError.peak, 0);
  assert.ok(noError.bars[0].tops.every((v) => v === 0));
  const empty = alarmLayers([], new Set(), 0);
  assert.deepEqual(empty, { bars: [], line: null, peak: 0, normalPeak: 0 });
  const onlyInfo = alarmLayers([{ key: 4, values: [3, 6] }], new Set(), 2);
  assert.deepEqual(onlyInfo.line, [0.2, 0.4]);
});

test("alarm legend totals, readout and the Errors only view", () => {
  const series = peakCase();
  const totals = alarmTotals(series, (k) => "s" + k, "Info + Noise");
  assert.deepEqual(totals.map((t) => [t.key, t.label, t.total]), [
    [1, "s1", 0],
    [2, "s2", 20600],
    [3, "s3", 0],
    [NORMAL_SERIES_KEY, "Info + Noise", 160000],
  ]);
  assert.deepEqual(alarmReadoutAt(series, new Set(), 4, "Info + Noise"), [
    { key: 2, label: "Error", count: 18000 },
    { key: NORMAL_SERIES_KEY, label: "Info + Noise", count: 16000 },
  ]);
  assert.deepEqual(alarmReadoutAt(series, new Set([NORMAL_SERIES_KEY]), 4, "n"), [
    { key: 2, label: "Error", count: 18000 },
  ]);
  assert.equal(isErrorsOnly(new Set()), false);
  assert.equal(isErrorsOnly(new Set(ERRORS_ONLY_HIDDEN)), true);
  assert.equal(isErrorsOnly(new Set([...ERRORS_ONLY_HIDDEN, 2])), false);
});

test("strip height, token parsing and ISO axis labels", () => {
  assert.equal(stripHeight(false), 40);
  assert.equal(stripHeight(true), 120);
  assert.equal(stripHeight(true, 40, 150), 150);
  assert.equal(parsePx("40px", 1), 40);
  assert.equal(parsePx("", 40), 40);
  assert.equal(parsePx("auto", 120), 120);
  const jan15 = Date.UTC(2030, 0, 15, 7, 8) / 1000;
  assert.equal(axisLabel(jan15, 5 * 86400), "2030-01-15");
  assert.equal(axisLabel(jan15, 3600), "2030-01-15 07:08");
});
