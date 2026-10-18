// 9.7 UI: "Compare views" rules without DOM: the request built from the form (every mode, every refusal), the answer
// shaped for the panel, error texts, keyboard movement and the static texts.
import test from "node:test";
import assert from "node:assert/strict";

import {
  CURRENT, DEFAULT_MAX_SIDE, MAX_SIDE_CHOICES, MODES, PAGE_LIMIT, buildRequest, buildResult, classifyError, describeSide, errorText,
  moveCell, nextDifference, nextMaxSide, noteTexts, operationOptions, parseTime, processOptions, rowView, templateRows, viewOptions,
} from "../../src/dmslog/web/js/compare-views-model.js";
import { S } from "../../src/dmslog/web/js/strings.js";
import { formatTs } from "../../src/dmslog/web/js/time.js";

const T1 = parseTime("2026-01-01 10:00:00");
const T2 = parseTime("2026-01-01 11:00:00");
const FILTER = { sev: { le: 3 } };
const VIEWS = [{ id: 7, name: "Errors", filter: { sev: { le: 2 } } }, { id: 8, name: "Empty", filter: null }];

test("filter mode: the sides carry their own filters (the current view or a saved one) and there is no common filter", () => {
  const built = buildRequest({ mode: "filter", a: { source: CURRENT }, b: { source: "7" }, common: FILTER, views: VIEWS });
  assert.deepEqual(built, {
    ok: true,
    body: {
      mode: "filter", filter: null, a: { filter: FILTER }, b: { filter: { sev: { le: 2 } } }, max_side: DEFAULT_MAX_SIDE, offset: 0,
      limit: PAGE_LIMIT, only_diff: false,
    },
  });
  assert.deepEqual(buildRequest({ mode: "filter", a: { source: "8" }, b: { source: "" }, common: null, views: VIEWS }).body.a, { filter: null });
  const gone = buildRequest({ mode: "filter", a: { source: "99" }, b: { source: CURRENT }, common: null, views: VIEWS });
  assert.deepEqual(gone, { ok: false, errors: [{ side: "a", key: "noView" }] });
});

test("window mode: the current view is the common filter; both times are required and from < to", () => {
  const ok = buildRequest({
    mode: "window", a: { from: "2026-01-01 10:00:00", to: "2026-01-01 11:00:00" }, b: { from: "2026-01-02 10:00", to: "2026-01-02 11:00" },
    common: FILTER, maxSide: 20000, onlyDiff: true,
  });
  assert.equal(ok.ok, true);
  assert.deepEqual(ok.body.filter, FILTER);
  assert.deepEqual(ok.body.a, { from: T1, to: T2 });
  assert.equal(ok.body.max_side, 20000);
  assert.equal(ok.body.only_diff, true);
  const bad = buildRequest({ mode: "window", a: { from: "", to: "2026-01-01 11:00" }, b: { from: "2026-01-01 11:00", to: "2026-01-01 10:00" } });
  assert.deepEqual(bad.errors, [{ side: "a", key: "badFrom" }, { side: "b", key: "windowOrder" }]);
  const noTo = buildRequest({ mode: "window", a: { from: "2026-01-01 10:00", to: "nope" }, b: { from: T1, to: T2 } });
  assert.equal(noTo.errors[0].key, "badTo");
  const equal = buildRequest({ mode: "window", a: { from: "2026-01-01 10:00", to: "2026-01-01 10:00" }, b: { from: "2026-01-01 10:00", to: "2026-01-01 10:01" } });
  assert.deepEqual(equal.errors, [{ side: "a", key: "windowOrder" }]);
});

test("operation and process modes: ids are plain positive integers; a process window is all or nothing", () => {
  assert.deepEqual(buildRequest({ mode: "operation", a: { event: "12" }, b: { event: " 13 " }, common: FILTER }).body.a, { event: 12 });
  assert.deepEqual(buildRequest({ mode: "operation", a: { event: "12" }, b: { event: " 13 " }, common: FILTER }).body.b, { event: 13 });
  for (const wrong of ["", "0", "-1", "1.5", "1e3", "0x10", "abc", "12345678901234567890", "+4"]) {
    const built = buildRequest({ mode: "operation", a: { event: wrong }, b: { event: "1" } });
    assert.deepEqual(built.errors, [{ side: "a", key: "noOperation" }], JSON.stringify(wrong));
  }
  const proc = buildRequest({ mode: "process", a: { stream: "3", from: "", to: "" }, b: { stream: "4", from: "2026-01-01 10:00", to: "2026-01-01 11:00" } });
  assert.deepEqual(proc.body.a, { stream: 3 });
  assert.deepEqual(proc.body.b, { stream: 4, from: T1, to: T2 });
  const half = buildRequest({ mode: "process", a: { stream: "3", from: "2026-01-01 10:00", to: "" }, b: { stream: "" } });
  assert.deepEqual(half.errors, [{ side: "a", key: "badTo" }, { side: "b", key: "noProcess" }]);
});

test("an unknown mode or limit never reaches the server as typed", () => {
  assert.deepEqual(buildRequest({ mode: "template", a: {}, b: {} }).errors, [{ side: "a", key: "badMode" }]);
  assert.equal(buildRequest({ mode: "operation", a: { event: "1" }, b: { event: "2" }, maxSide: 999999 }).body.max_side, DEFAULT_MAX_SIDE);
  assert.deepEqual(MODES, ["filter", "window", "operation", "process"]);
  assert.equal(nextMaxSide(1000), 5000);
  assert.equal(nextMaxSide(5000), 20000);
  assert.equal(nextMaxSide(20000), null);
  assert.ok(MAX_SIDE_CHOICES.every((n) => n >= 1 && n <= 20000));
});

test("errors map to static texts: the server's own message is never used", () => {
  const known = classifyError({ status: 404, code: "event_not_found", message: "event 5 does not exist <b>x</b>" });
  assert.equal(known.key, "event_not_found");
  assert.equal(errorText(known), S.cvErrors.event_not_found);
  assert.equal(errorText(classifyError({ status: 408, code: "query_timeout" })), S.cvErrors.query_timeout);
  assert.equal(classifyError({ status: 408, code: "weird" }).key, "timeout");
  assert.equal(classifyError({ status: 0, code: "network" }).key, "network");
  assert.equal(classifyError({ status: 500, code: "internal" }).key, "server");
  assert.equal(classifyError({ status: 400, code: "bad_window", path: "$.a.to" }).key, "bad_window");
  assert.equal(classifyError({ status: 400, code: "new_code_nobody_knows" }).key, "other");
  assert.equal(classifyError(null).key, "other");
  assert.equal(classifyError({ status: 400, code: "hasOwnProperty" }).key, "other", "no prototype keys");
  assert.equal(classifyError({ status: 400, code: "constructor" }).key, "other");
});

const sample = () => ({
  mode: "operation",
  a: { count: 3, truncated: false, first_ts: T1, last_ts: T2, event: { id: 5, name: "GetIncidents", ts_start: T1 } },
  b: { count: 1234, truncated: true, first_ts: T1, last_ts: T2, event: { id: 6, name: "<img src=x onerror=alert(1)>", ts_start: T2 } },
  stats: { same: 2, only_a: 1, only_b: 1200, changed_a: 0, changed_b: 3, similarity: 0.0832 },
  optimal: true, templates_total: 250,
  templates: [
    { code: "WFM-S010", label: "Request received", unknown: false, a: 1, b: 5 },
    { code: null, label: "boom <b>", unknown: true, a: 0, b: 1 },
    { code: "WFM-S011", label: "Done", unknown: false, a: 2, b: 2 },
    { code: "WFM-S012", label: "Gone", unknown: false, a: 4, b: 1 },
  ],
  runs_total: 3, total_rows: 1207, digest: "abc", notes: ["truncated_b", "mixed_clocks", "surprise"],
});

test("the answer: counts as words, similarity as a percent, notes explained, unknown notes dropped", () => {
  const result = buildResult(sample(), { mode: "operation", max_side: 1000 }, null);
  assert.equal(result.a.countText, "3 records");
  assert.equal(result.b.countText, "1,234 records");
  assert.deepEqual(result.truncatedSides, ["b"]);
  assert.equal(result.stats.similarity, 8.3);
  assert.equal(result.stats.onlyB, 1200);
  assert.deepEqual(result.notes, [
    "Side B has more than 1,000 records: only the first 1,000 in order were compared.",
    S.cvNotes.mixed_clocks,
  ]);
  assert.equal(result.templatesTotal, 250);
  assert.equal(result.digest, "abc");
  assert.equal(result.a.description, "GetIncidents at " + formatTs(T1));
  assert.equal(result.b.description, "<img src=x onerror=alert(1)> at " + formatTs(T2), "kept as text for textContent");
  assert.deepEqual(noteTexts(["approximate", "incomplete_index"], 5000), [S.cvNotes.approximate, S.cvNotes.incomplete_index]);
});

test("message types: unknown ones keep their signature as the label and a change in words and sign", () => {
  const rows = templateRows(sample().templates);
  assert.deepEqual(rows.map((r) => [r.label, r.a, r.b, r.deltaText, r.unknown]), [
    ["Request received", 1, 5, "+4", false],
    ["boom <b>", 0, 1, "+1", true],
    ["Done", 2, 2, S.cvDeltaSame, false],
    ["Gone", 4, 1, "-3", false],
  ]);
  assert.deepEqual(templateRows(undefined), []);
});

test("describing a side: windows, processes and saved view names", () => {
  assert.equal(describeSide({ from: T1, to: T2 }, "window"), "from " + formatTs(T1) + " to " + formatTs(T2));
  assert.equal(describeSide({ stream: { family: "Wfm", process: "Wfm", pid: 77 } }, "process"), "Wfm, PID 77");
  assert.equal(describeSide({ stream: { family: "Wfm", process: "Wfm2", pid: null }, from: T1, to: T2 }, "process"),
    "Wfm / Wfm2, PID ? from " + formatTs(T1) + " to " + formatTs(T2));
  assert.equal(describeSide({}, "filter", "Errors"), "Errors");
  assert.equal(describeSide({}, "filter", ""), S.cvCurrentView);
  assert.equal(describeSide(null, "window"), "");
});

test("rows: a missing side is null, the text is trimmed to one short line, an unknown op reads as same", () => {
  const long = "x".repeat(500);
  const row = rowView({ op: "changed", a: { id: 1, ts: T1, label: "Type A", msg: "line one\n   line   two" }, b: { id: 2, ts: null, tpl: "WFM-1", msg: long } }, 4);
  assert.equal(row.key, 4);
  assert.equal(row.opLabel, S.cvOps.changed);
  assert.equal(row.a.text, "line one line two");
  assert.equal(row.a.type, "Type A");
  assert.equal(row.b.type, "WFM-1");
  assert.equal(row.b.time, "");
  assert.equal(Array.from(row.b.text).length, 161);
  const odd = rowView({ op: "hasOwnProperty", a: null, b: { id: "7" } }, 0);
  assert.equal(odd.op, "same");
  assert.equal(odd.a, null);
  assert.equal(odd.b, null, "an id that is not an integer is no record to open");
  assert.equal(rowView({ op: "only_a", a: { id: 3, ts: NaN, msg: null } }, 1).a.type, S.cvUnknownType);
});

test("pickers: views start with the current one, operations and processes list each id once", () => {
  assert.deepEqual(viewOptions(VIEWS), [
    { value: CURRENT, label: S.cvCurrentView }, { value: "7", label: "Errors" }, { value: "8", label: "Empty" },
  ]);
  assert.deepEqual(viewOptions(null), [{ value: CURRENT, label: S.cvCurrentView }]);
  const events = [
    { id: 5, name: "Op", ts_start: T1, status: "ok", stream_id: 3, family: "Wfm", process: "Wfm" },
    { id: 6, name: null, ts_start: T2, status: null, stream_id: 3, family: "Wfm", process: "Wfm" },
    { id: "x", stream_id: 4, family: "Smn", process: "Smn2" },
  ];
  assert.deepEqual(operationOptions(events).map((o) => o.label), ["Op at " + formatTs(T1) + " ok", "(no name) at " + formatTs(T2)]);
  assert.deepEqual(processOptions(events), [{ value: "3", label: "Wfm" }, { value: "4", label: "Smn / Smn2" }]);
});

test("keyboard: arrows, page, home and end stay inside the rows; the next difference skips the same rows", () => {
  assert.deepEqual(moveCell({ row: 0, col: 0 }, "ArrowUp", 5), { row: 0, col: 0 });
  assert.deepEqual(moveCell({ row: 3, col: 0 }, "ArrowDown", 5), { row: 4, col: 0 });
  assert.deepEqual(moveCell({ row: 4, col: 0 }, "ArrowDown", 5), { row: 4, col: 0 });
  assert.deepEqual(moveCell({ row: 2, col: 0 }, "ArrowRight", 5), { row: 2, col: 1 });
  assert.deepEqual(moveCell({ row: 2, col: 1 }, "ArrowLeft", 5), { row: 2, col: 0 });
  assert.deepEqual(moveCell({ row: 2, col: 1 }, "PageDown", 50), { row: 12, col: 1 });
  assert.deepEqual(moveCell({ row: 2, col: 1 }, "PageUp", 50), { row: 0, col: 1 });
  assert.deepEqual(moveCell({ row: 2, col: 1 }, "Home", 5), { row: 0, col: 1 });
  assert.deepEqual(moveCell({ row: 2, col: 1 }, "End", 5), { row: 4, col: 1 });
  assert.equal(moveCell({ row: 0, col: 0 }, "a", 5), null);
  assert.equal(moveCell({ row: 0, col: 0 }, "ArrowDown", 0), null);
  const rows = ["same", "same", "only_a", "same", "changed"].map((op) => ({ op }));
  assert.equal(nextDifference(rows, 0, 1), 2);
  assert.equal(nextDifference(rows, 2, 1), 4);
  assert.equal(nextDifference(rows, 4, 1), -1);
  assert.equal(nextDifference(rows, 4, -1), 2);
  assert.equal(nextDifference(rows, 2, -1), -1);
});

function walk(value, visit, path) {
  if (value && typeof value === "object") {
    for (const [key, child] of Object.entries(value)) {
      walk(child, visit, path + "." + key);
    }
  } else {
    visit(value, path);
  }
}

test("texts: every cv string is static, non-empty and in the vocabulary of the app (record, message type)", () => {
  const seen = [];
  for (const [key, value] of Object.entries(S)) {
    if (key.startsWith("cv") || key === "actCompareViews") {
      walk(value, (text, path) => {
        assert.equal(typeof text, "string", key + path);
        assert.ok(text.length > 0, key + path);
        seen.push([key + path, text]);
      }, "");
    }
  }
  assert.ok(seen.length > 100);
  for (const [name, text] of seen) {
    assert.doesNotMatch(text, /template|entries|entry|\(s\)/i, name);
  }
  // every note and error code the backend can send has a text (ARQUITETURA 10.10)
  for (const note of ["truncated_a", "truncated_b", "approximate", "incomplete_index", "mixed_clocks"]) {
    assert.ok(S.cvNotes[note], note);
  }
  for (const code of ["bad_window", "event_not_found", "stream_not_found", "query_timeout", "substr_needs_scope"]) {
    assert.ok(S.cvErrors[code], code);
  }
  for (const kind of ["timeout", "network", "gone", "server", "other"]) {
    assert.ok(S.cvErrors[kind], kind);
  }
  for (const op of ["same", "only_a", "only_b", "changed"]) {
    assert.ok(S.cvOps[op], op);
  }
  for (const key of ["badMode", "badFrom", "badTo", "windowOrder", "noView", "noOperation", "noProcess"]) {
    assert.ok(S.cvInvalid[key], key);
  }
});
