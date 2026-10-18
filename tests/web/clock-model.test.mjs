// 9.15 (clock offset between collections): the pure model of the Align clock dialog and the pieces the collection row,
// the grid and the detail read from it. Synthetic data only.
import test from "node:test";
import assert from "node:assert/strict";

const model = await import("../../src/dmslog/web/js/clock-model.js");
const { formatOffset } = await import("../../src/dmslog/web/js/time.js");
const { clockLine, clockTitle, clocksMode } = await import("../../src/dmslog/web/js/collections-model.js");
const { S } = await import("../../src/dmslog/web/js/strings.js");

const T = Date.UTC(2026, 2, 9, 14, 5, 6, 789);

test("formatOffset: signed seconds with milliseconds, never anything for a non-integer", () => {
  assert.equal(formatOffset(1234), "+1.234s");
  assert.equal(formatOffset(-500), "-0.500s");
  assert.equal(formatOffset(0), "0.000s");
  assert.equal(formatOffset(2_678_400_000), "+2678400.000s");
  assert.equal(formatOffset(-1), "-0.001s");
  for (const bad of [1.5, NaN, Infinity, null, undefined, "5", true]) {
    assert.equal(formatOffset(bad), "", String(bad));
  }
});

test("formatSpan: seconds, minutes, hours and days", () => {
  assert.equal(model.formatSpan(500), "0.500 s");
  assert.equal(model.formatSpan(-12_345), "12.345 s");
  assert.equal(model.formatSpan(125_000), "2 min 05.000 s");
  assert.equal(model.formatSpan(3_723_500), "1 h 02 min 03.500 s");
  assert.equal(model.formatSpan(90_061_001), "1 d 01 h 01 min 01.001 s");
  assert.equal(model.formatSpan(NaN), "0.000 s");
});

test("parseOffset: whole milliseconds within 31 days, nothing else", () => {
  assert.deepEqual(model.parseOffset("1234"), { ok: true, ms: 1234 });
  assert.deepEqual(model.parseOffset(" -250 "), { ok: true, ms: -250 });
  assert.deepEqual(model.parseOffset("+7"), { ok: true, ms: 7 });
  assert.deepEqual(model.parseOffset("-0"), { ok: true, ms: 0 });
  assert.equal(Object.is(model.parseOffset("-0").ms, 0), true, "no negative zero");
  assert.deepEqual(model.parseOffset("2678400000"), { ok: true, ms: 2_678_400_000 });
  assert.deepEqual(model.parseOffset("-2678400000"), { ok: true, ms: -2_678_400_000 });
  for (const bad of ["2678400001", "-2678400001", "", "  ", "1.5", "1e3", "0x10", "12 34", "--1", "abc", "99999999999", null, undefined]) {
    assert.deepEqual(model.parseOffset(bad), { ok: false }, String(bad));
  }
});

test("recordRef keeps ids and times, needs a time and a known collection", () => {
  const groupOfSource = (id) => ({ 1: 1, 2: 2 })[id] ?? null;
  const made = model.recordRef({ id: 7, ts: T, source_id: 2, ts_adjusted: T + 5 }, { caseId: "A", groupOfSource });
  assert.deepEqual(made, { ok: true, ref: { caseId: "A", id: 7, ts: T, tsAdjusted: T + 5, group: 2 } });
  assert.ok(Object.isFrozen(made.ref));
  assert.equal(model.recordRef({ id: 7, ts: T, source_id: 1 }, { caseId: "A", groupOfSource }).ref.tsAdjusted, T, "no offsets: adjusted = original");
  assert.deepEqual(model.recordRef({ id: 7, ts: null, source_id: 1 }, { caseId: "A", groupOfSource }), { ok: false, reason: "no_time" });
  assert.deepEqual(model.recordRef({ id: 7, ts: T, source_id: 99 }, { caseId: "A", groupOfSource }), { ok: false, reason: "no_collection" });
  assert.deepEqual(model.recordRef({ id: 0, ts: T, source_id: 1 }, { caseId: "A", groupOfSource }), { ok: false, reason: "record" });
  assert.deepEqual(model.recordRef(null, { caseId: "A", groupOfSource }), { ok: false, reason: "record" });
  assert.deepEqual(model.recordRef({ id: 7, ts: T, source_id: 1 }, { caseId: null, groupOfSource }), { ok: false, reason: "record" });
  assert.equal(model.refReasonText("no_time"), S.ckRefNoTime);
  assert.equal(model.refReasonText("no_collection"), S.ckRefNoCollection);
  assert.equal(model.refReasonText("record"), S.ckErrFailed);
});

test("picks belong to a case: another case never sees them, clear forgets both", () => {
  const picks = model.createPicks();
  const a = { caseId: "A", id: 1, group: 1 };
  const b = { caseId: "A", id: 2, group: 2 };
  picks.setAnchor(a);
  picks.setTarget(b);
  assert.equal(picks.anchor("A"), a);
  assert.equal(picks.target("A"), b);
  assert.equal(picks.anchor("B"), null);
  assert.equal(picks.target(null), null);
  picks.clear();
  assert.equal(picks.anchor("A"), null);
  assert.equal(picks.target("A"), null);
});

test("pairOf: the anchor must be in another collection and the target in the aligned one", () => {
  const a1 = { id: 1, group: 1 };
  const t2 = { id: 2, group: 2 };
  assert.deepEqual(model.pairOf(2, a1, t2), { anchor: a1, target: t2, problem: null });
  assert.deepEqual(model.pairOf(2, null, null), { anchor: null, target: null, problem: null });
  assert.deepEqual(model.pairOf(1, a1, t2), { anchor: null, target: null, problem: "anchor_same" });
  assert.deepEqual(model.pairOf(3, a1, t2), { anchor: a1, target: null, problem: "target_elsewhere" });
  assert.deepEqual(model.pairOf(3, null, t2), { anchor: null, target: null, problem: "target_elsewhere" });
});

test("pairBody carries ids only; dry_run only when asked", () => {
  assert.deepEqual(model.pairBody({ id: 1, ts: 5 }, { id: 2, ts: 6 }, true), { anchor: 1, target: 2, dry_run: true });
  assert.deepEqual(model.pairBody({ id: 1 }, { id: 2 }, false), { anchor: 1, target: 2 });
});

const op = { group: 2, anchor: { id: 1 }, target: { id: 2 } };
const preview = () => ({
  group: 2, previous_ms: 0, clock_offset_ms: 1500, clock_source: { method: "pair" }, applied: false, skew_ms: -1500,
  anchor: { entry_id: 1, ts: 100, ts_adjusted: 100 }, target: { entry_id: 2, ts: 50, ts_adjusted: 1550 },
});

test("acceptPreview: only an answer for this operation, with every number a number", () => {
  assert.equal(model.acceptPreview(preview(), op), true);
  const bad = (change) => model.acceptPreview({ ...preview(), ...change }, op);
  assert.equal(bad({ group: 3 }), false);
  assert.equal(bad({ applied: true }), false);
  assert.equal(bad({ clock_offset_ms: "1500" }), false);
  assert.equal(bad({ clock_offset_ms: 2_678_400_001 }), false);
  assert.equal(bad({ previous_ms: null }), false);
  assert.equal(bad({ skew_ms: 1.5 }), false);
  assert.equal(bad({ anchor: { entry_id: 9, ts: 1, ts_adjusted: 1 } }), false, "another anchor");
  assert.equal(bad({ target: { entry_id: 9, ts: 1, ts_adjusted: 1 } }), false, "another target");
  assert.equal(bad({ target: { entry_id: 2, ts: 1 } }), false, "no adjusted time");
  assert.equal(bad({ anchor: null }), false);
  assert.equal(model.acceptPreview(null, op), false);
  assert.equal(model.acceptPreview("x", op), false);
});

test("acceptApplied: the PUT/DELETE answer names the group and says it was applied", () => {
  const ok = { group: 2, applied: true, clock_offset_ms: 0, previous_ms: 5 };
  assert.equal(model.acceptApplied(ok, 2), true);
  assert.equal(model.acceptApplied(ok, 3), false);
  assert.equal(model.acceptApplied({ ...ok, applied: false }, 2), false);
  assert.equal(model.acceptApplied({ ...ok, previous_ms: undefined }, 2), false);
  assert.equal(model.acceptApplied(null, 2), false);
});

test("previewText says what applying does, in plain language", () => {
  assert.equal(model.previewText({ tag: "C2", offset: 1500, previous: 0 }), "C2 will be shifted by +1.500s.");
  assert.equal(model.previewText({ tag: "C2", offset: 1500, previous: 0, skew: -1500 }), "C2 will be shifted by +1.500s. Its clock runs 1.500 s behind the anchor's.");
  assert.ok(model.previewText({ tag: "C2", offset: -1500, previous: 0, skew: 1500 }).endsWith("runs 1.500 s ahead of the anchor's."));
  assert.equal(model.previewText({ tag: "C2", offset: 0, previous: 5 }), "C2 will have no offset.");
  assert.equal(model.previewText({ tag: "C2", offset: 5, previous: 5 }), S.ckNoChange);
  assert.equal(model.previewText({ tag: "C2", offset: 0, previous: 0 }), S.ckNoChange);
  assert.equal(model.currentText(0), "No offset is set.");
  assert.equal(model.currentText(-250), "Current offset: -0.250s.");
});

test("problemText names the collections; nothing without a problem", () => {
  assert.equal(model.problemText(null, { tag: "C2", other: "" }), "");
  assert.ok(model.problemText("anchor_same", { tag: "C2", other: "" }).includes("C2"));
  const text = model.problemText("target_elsewhere", { tag: "C2", other: "C3" });
  assert.ok(text.includes("C3") && text.includes("C2"));
  assert.equal(model.timeText(T), "2026-03-09 14:05:06.789");
  assert.equal(model.timeText(null), "");
});

test("errorText is static: chosen by the code, never by the server's message", () => {
  const err = { code: "no_time", message: "entry 91827 has no time (/secret/path)" };
  assert.equal(model.errorText(err), S.ckErrors.no_time);
  assert.ok(!model.errorText(err).includes("91827"));
  for (const code of Object.keys(S.ckErrors)) {
    assert.equal(model.errorText({ code }), S.ckErrors[code]);
  }
  assert.equal(model.errorText({ code: "weird" }), S.ckErrFailed);
  assert.equal(model.errorText({ code: "constructor" }), S.ckErrFailed, "no prototype lookups");
  assert.equal(model.errorText({ code: "__proto__" }), S.ckErrFailed);
  assert.equal(model.errorText(null), S.ckErrFailed);
  assert.equal(model.errorText(new Error("boom")), S.ckErrFailed);
  assert.equal(model.isAbort({ name: "AbortError" }), true);
  assert.equal(model.isAbort(new Error("x")), false);
  assert.equal(model.isAbort(null), false);
});

test("every error code the server can answer for a clock request has a sentence", () => {
  for (const code of ["bad_request", "bad_offset", "bad_entry", "entry_not_found", "collection_not_found", "no_time",
    "same_collection", "target_not_in_collection", "offset_too_large", "invalid_field", "database_error", "network"]) {
    assert.ok(typeof S.ckErrors[code] === "string" && S.ckErrors[code] !== "", code);
  }
});

test("clockLine: the badge of a collection row, empty without an offset", () => {
  assert.equal(clockLine({ clock_offset_ms: 1234, clock_source: { method: "pair" } }), "Clock +1.234s (from pair)");
  assert.equal(clockLine({ clock_offset_ms: -250, clock_source: { method: "manual", set_at: "2026-10-18T10:00:00Z" } }), "Clock -0.250s (manual)");
  assert.equal(clockLine({ clock_offset_ms: 5, clock_source: null }), "Clock +0.005s");
  assert.equal(clockLine({ clock_offset_ms: 5, clock_source: { method: "other" } }), "Clock +0.005s");
  for (const none of [{ clock_offset_ms: 0 }, {}, { clock_offset_ms: null }, { clock_offset_ms: "5" }, { clock_offset_ms: 1.5 }, null]) {
    assert.equal(clockLine(none), "", JSON.stringify(none));
  }
  assert.equal(clockTitle({ clock_source: { method: "pair", set_at: "2026-10-18T10:00:00Z" } }), S.ckBadgeTitle + " Set 2026-10-18T10:00:00Z.");
  assert.equal(clockTitle({ clock_source: null }), S.ckBadgeTitle);
  assert.equal(clocksMode("adjusted"), "adjusted");
  assert.equal(clocksMode("raw"), "raw");
  assert.equal(clocksMode(undefined), "raw");
});
