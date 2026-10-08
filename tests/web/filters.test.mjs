import test from "node:test";
import assert from "node:assert/strict";
import { buildFilter, stateFromForm, timeSpan } from "../../src/dmslog/web/js/filters.js";

test("no criteria gives no filter", () => {
  assert.equal(buildFilter({ sourceIds: [], sevMax: null, levelMax: null, from: null, to: null, text: "  " }), null);
});

test("all criteria build an AND tree in the API shape", () => {
  const tree = buildFilter({ sourceIds: [3, 4], sevMax: 2, levelMax: 3, from: 10, to: 20, text: " timeout " });
  assert.deepEqual(tree, {
    and: [
      { source: [3, 4] },
      { sev: { le: 2 } },
      { level: { le: 3 } },
      { ts: { ge: 10, le: 20 } },
      { text: { fts: "timeout" } },
    ],
  });
});

test("one-sided time windows and zero values are kept", () => {
  assert.deepEqual(buildFilter({ sourceIds: [], from: 0, to: null }), { and: [{ ts: { ge: 0 } }] });
  assert.deepEqual(buildFilter({ sourceIds: [], to: 5 }), { and: [{ ts: { le: 5 } }] });
});

test("search text is passed as data, never interpreted", () => {
  const nasty = "x\"; DROP TABLE entry; -- <script>";
  assert.deepEqual(buildFilter({ sourceIds: [], text: nasty }), { and: [{ text: { fts: nasty } }] });
});

test("stateFromForm parses numbers and log times, reporting bad fields", () => {
  const ok = stateFromForm({ sevMax: "2", levelMax: "", from: "2026-10-08 12:00:00", to: "", text: "a" });
  assert.deepEqual(ok.errors, []);
  assert.equal(ok.state.sevMax, 2);
  assert.equal(ok.state.levelMax, null);
  assert.equal(ok.state.from, Date.UTC(2026, 9, 8, 12));
  const bad = stateFromForm({ from: "yesterday", to: "2026-10-08" });
  assert.deepEqual(bad.errors, ["from"]);
});

test("timeSpan unions the selected sources and clips to the window", () => {
  const sources = [
    { id: 1, ts_min: 100, ts_max: 200 },
    { id: 2, ts_min: 150, ts_max: 400 },
    { id: 3, ts_min: null, ts_max: null },
  ];
  assert.deepEqual(timeSpan(sources, [], null, null), { min: 100, max: 400 });
  assert.deepEqual(timeSpan(sources, [1], null, null), { min: 100, max: 200 });
  assert.deepEqual(timeSpan(sources, [], 120, 300), { min: 120, max: 300 });
  assert.equal(timeSpan(sources, [3], null, null), null);
  assert.equal(timeSpan(sources, [1], 500, null), null);
});
