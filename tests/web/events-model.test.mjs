// Pure helpers of the Operations panel and the clock-jump banner.
import test from "node:test";
import assert from "node:assert/strict";

import {
  bannerText, buildDetailView, buildEventParams, buildEventRow, formatDuration, gridTarget, statusLabel, statusTone,
  summarizeClockJumps,
} from "../../src/dmslog/web/js/events-model.js";

const EVENT = {
  id: 7, stream_id: 2, family: "Wfm", process: "WfmAdapter", sensitive: false, kind: "operation", name: "Op1",
  key: "k-1", tid: 12, start_entry_id: 100, end_entry_id: 140, ts_start: Date.UTC(2026, 0, 2, 3, 4, 5, 6),
  ts_end: Date.UTC(2026, 0, 2, 3, 4, 7, 6), duration_ms: 2000, status: "fail", sev: 2, n_entries: 5, detail: {},
};

test("formatDuration picks a readable unit and tolerates missing values", () => {
  assert.equal(formatDuration(null), "");
  assert.equal(formatDuration(undefined), "");
  assert.equal(formatDuration(-1), "");
  assert.equal(formatDuration(15.6), "15.6 ms");
  assert.equal(formatDuration(2500), "2.5 s");
  assert.equal(formatDuration(184000), "3 min 4 s");
  assert.equal(formatDuration(7500000), "2 h 5 min");
});

test("status labels and tones are text first, with a neutral fallback", () => {
  assert.equal(statusLabel("business_error"), "Business error");
  assert.equal(statusLabel(null), "-");
  assert.equal(statusLabel("weird"), "weird");
  assert.equal(statusTone("fail"), "bad");
  assert.equal(statusTone("business_error"), "warn");
  assert.equal(statusTone("ok"), "ok");
  assert.equal(statusTone("weird"), "neutral");
});

test("buildEventParams defaults to operations, trims and drops empty values", () => {
  assert.deepEqual(buildEventParams({}, null), { kind: "operation", limit: 200 });
  assert.deepEqual(
    buildEventParams({ kind: "exception", name: " X ", status: "", q: "abc", from: null }, "tok", 50),
    { kind: "exception", limit: 50, name: "X", q: "abc", after: "tok" },
  );
});

test("buildEventRow formats time, family, duration and status", () => {
  const row = buildEventRow(EVENT);
  assert.equal(row.time, "2026-01-02 03:04:05.006");
  assert.equal(row.process, "Wfm / WfmAdapter");
  assert.equal(row.durationText, "2 s");
  assert.equal(row.statusLabel, "Failed");
  assert.equal(row.tone, "bad");
  assert.equal(row.entries, "5");
  assert.equal(buildEventRow({ ...EVENT, name: null }).name, "(no name)");
  assert.equal(buildEventRow({ ...EVENT, family: "Osr", process: "Osr" }).process, "Osr");
});

test("gridTarget uses the event window and the start entry", () => {
  assert.deepEqual(gridTarget(EVENT), { from: EVENT.ts_start, to: EVENT.ts_end, entryId: 100 });
  assert.deepEqual(gridTarget({ ...EVENT, ts_end: null, start_entry_id: null }), {
    from: EVENT.ts_start, to: EVENT.ts_start, entryId: null,
  });
  assert.equal(gridTarget({ ...EVENT, ts_start: null }), null);
});

test("buildDetailView shapes fields, entries and related events", () => {
  const view = buildDetailView({
    event: { ...EVENT, detail: { retries: 3, nested: { a: 1 } } },
    entries: [{ id: 1, ts: EVENT.ts_start, tid: 12, level: 5, sev: 2, tpl_code: "WFM-1", msg: "<b>text</b>" }],
    entries_total: 5,
    related: [{ ...EVENT, id: 8, kind: "retry_seq" }],
  });
  assert.equal(view.title, "Operation: Op1");
  assert.equal(view.entriesPartial, true);
  assert.equal(view.entries[0].text, "<b>text</b>"); // untrusted text stays literal
  assert.equal(view.related[0].kindLabel, "Retry sequence");
  const labels = view.fields.map((f) => f.label);
  assert.ok(labels.includes("Detail: retries"));
  assert.equal(view.fields.find((f) => f.label === "Detail: nested").value, '{"a":1}');
  assert.equal(view.fields.find((f) => f.label === "Sensitive log").value, "No");
  assert.equal(buildDetailView({}), null);
});

test("summarizeClockJumps prefers real jumps, then flagged gaps, else nothing", () => {
  const jump = (id, ts) => ({ ...EVENT, id, kind: "clock_jump", ts_start: ts, ts_end: ts });
  const gap = (id, ts, flag) => ({ ...EVENT, id, kind: "gap", ts_start: ts, detail: flag ? { possible_clock_jump: true } : {} });
  const jumps = summarizeClockJumps([jump(1, 900), jump(2, 500)], [gap(3, 100, true)], false);
  assert.equal(jumps.kind, "jump");
  assert.equal(jumps.count, 2);
  assert.equal(jumps.first, 500);
  assert.equal(jumps.target.from, 500);
  const possible = summarizeClockJumps([], [gap(3, 700, true), gap(4, 100, false), gap(5, 300, true)], false);
  assert.equal(possible.kind, "possible");
  assert.equal(possible.count, 2);
  assert.equal(possible.first, 300);
  assert.equal(summarizeClockJumps([], [gap(4, 100, false)], false), null);
  assert.equal(summarizeClockJumps([], [], false), null);
});

test("bannerText says one jump, many jumps or 'more than'", () => {
  const base = { kind: "jump", count: 1, more: false, first: Date.UTC(2026, 9, 25, 0, 30), target: null };
  assert.match(bannerText(base), /^Clock jump detected at 2026-10-25 00:30:00\.000\./);
  assert.match(bannerText({ ...base, count: 3 }), /^3 clock jumps detected, first at 2026-10-25/);
  assert.match(bannerText({ ...base, count: 50, more: true }), /^More than 50 clock jumps/);
  assert.match(bannerText({ ...base, kind: "possible" }), /^Possible clock change/);
  assert.equal(bannerText(null), "");
});
