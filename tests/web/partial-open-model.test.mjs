// M9 9.9: the pure model of "Open only part first" and of the pending work. Synthetic data, no DOM, no timers.
import test from "node:test";
import assert from "node:assert/strict";

import {
  MAX_FIRST_SOURCES, MAX_PLAN_SOURCES, buildFirst, canChoose, captureTs, chainKey, chainMembers, chosenSources, defaultChecked, mergePeriods,
  normalizePending, normalizePlan, parseInstant, parseWindow, partialLeft, pendingTotals, periodOf, safeCount,
  unmatchedNames, windowTouchesPeriod,
} from "../../src/dmslog/web/js/partial-open-model.js";

const T = (text) => Date.parse(text + "Z");
const src = (member, extra = {}) => ({ member, name: member, selected: true, indexable: true, ...extra });

test("a window field: empty, a valid time, a bad time, and years that Date.UTC would move", () => {
  assert.deepEqual(parseInstant(""), { state: "empty", ms: null });
  assert.deepEqual(parseInstant("   "), { state: "empty", ms: null });
  assert.deepEqual(parseInstant(null), { state: "empty", ms: null });
  assert.deepEqual(parseInstant("2026-10-09 14:30:00"), { state: "ok", ms: T("2026-10-09T14:30:00") });
  assert.deepEqual(parseInstant("2026-10-09T14:30"), { state: "ok", ms: T("2026-10-09T14:30:00") });
  assert.deepEqual(parseInstant("2026-10-09"), { state: "ok", ms: T("2026-10-09T00:00:00") });
  for (const bad of ["soon", "2026-13-01 00:00:00", "2026-02-30 00:00:00", "2026-10-09 24:00:00", "0099-01-01 00:00:00", "0000-01-01", "10/09/2026"]) {
    assert.equal(parseInstant(bad).state, "bad", bad);
  }
  assert.equal(parseInstant("1970-01-01 00:00:00").ms, 0, "the epoch itself is a valid instant (0 is not 'missing')");
});

test("the window: both empty is fine, one end is fine, the end must come after the start", () => {
  assert.deepEqual(parseWindow("", ""), { from: null, to: null, errors: {}, valid: true, empty: true });
  const onlyFrom = parseWindow("2026-10-09 10:00:00", "");
  assert.equal(onlyFrom.valid, true);
  assert.equal(onlyFrom.empty, false);
  assert.equal(onlyFrom.to, null);
  assert.equal(parseWindow("", "2026-10-09 10:00:00").from, null);
  assert.deepEqual(parseWindow("2026-10-09 10:00:00", "2026-10-09 10:00:00").errors, { to: "order" }, "from == to is empty");
  assert.deepEqual(parseWindow("2026-10-09 11:00:00", "2026-10-09 10:00:00").errors, { to: "order" });
  assert.deepEqual(parseWindow("nope", "2026-10-09 10:00:00").errors, { from: "bad" });
  assert.deepEqual(parseWindow("2026-10-09 10:00:00", "nope").errors, { to: "bad" });
  assert.deepEqual(parseWindow("x", "y").errors, { from: "bad", to: "bad" });
  assert.equal(parseWindow("x", "y").valid, false);
  const ok = parseWindow("2026-10-09 10:00:00", "2026-10-09 10:00:00.001");
  assert.equal(ok.valid, true, "one millisecond is a window");
});

test("the plan is normalized: no member, repeats, NaN and non-integer times, sizes and flags", () => {
  const data = {
    sources: [
      src("a.log", { name: "A", family: "Wfm", pid: 0, size: 10, first_ts: 5, last_ts: 9 }),
      src("a.log", { name: "again" }),
      { name: "no member", selected: true },
      { member: "", selected: true },
      null,
      "text",
      src("b.log", { name: "", size: -4, first_ts: NaN, last_ts: 1.5, selected: false }),
      src("c.log", { first_ts: "5", last_ts: 2 ** 60, indexable: false, sensitive: true, size: "12" }),
      src("d.log", { size: null, family: 7 }),
    ],
  };
  const { sources, truncated } = normalizePlan(data);
  assert.equal(truncated, false);
  assert.deepEqual(sources.map((s) => s.member), ["a.log", "b.log", "c.log", "d.log"]);
  const [a, b, c, d] = sources;
  assert.equal(a.name, "A");
  assert.equal(a.pid, 0, "pid 0 is a real value");
  assert.equal(a.firstTs, 5);
  assert.equal(b.name, "b.log", "a missing name falls back to the member");
  assert.equal(b.size, 0, "a negative size is 0");
  assert.equal(b.firstTs, null);
  assert.equal(b.lastTs, null, "1.5 is not an instant");
  assert.equal(c.firstTs, null, "text is not an instant");
  assert.equal(c.lastTs, null, "2^60 is not a safe integer");
  assert.equal(c.indexable, false);
  assert.equal(c.sensitive, true);
  assert.equal(c.size, 12);
  assert.equal(d.size, null);
  assert.equal(d.family, "");
  for (const garbage of [null, undefined, {}, { sources: "x" }, 5]) {
    assert.deepEqual(normalizePlan(garbage), { sources: [], truncated: false });
  }
  const many = { sources: Array.from({ length: MAX_PLAN_SOURCES + 5 }, (_, i) => src("m" + i)) };
  const capped = normalizePlan(many);
  assert.equal(capped.sources.length, MAX_PLAN_SOURCES);
  assert.equal(capped.truncated, true);
});

test("only a selected, indexable source can be chosen; those start checked", () => {
  const list = normalizePlan({ sources: [src("a"), src("b", { selected: false }), src("c", { indexable: false }), src("d")] }).sources;
  assert.deepEqual(list.map(canChoose), [true, false, false, true]);
  assert.deepEqual([...defaultChecked(list)], ["a", "d"]);
});

test("the capture time of an ERS name, and the period of the previewed sources", () => {
  const name = "ERS_CL_0A1B2C3D-0000-1111-2222-333344445555_20261009143000_Enterprise.zip";
  assert.equal(captureTs(name), T("2026-10-09T14:30:00"));
  assert.equal(captureTs("plain.zip"), null);
  assert.equal(captureTs("x_20261309143000_y"), null, "month 13");
  assert.equal(captureTs("x_20260230143000_y"), null, "Feb 30");
  const list = normalizePlan({ sources: [
    src("a", { first_ts: T("2026-10-09T10:00:00"), last_ts: T("2026-10-09T12:00:00") }),
    src("b", { first_ts: T("2026-10-09T09:00:00"), last_ts: T("2026-10-09T11:00:00") }),
    src("out", { selected: false, first_ts: 0, last_ts: T("2030-01-01T00:00:00") }),
  ] }).sources;
  assert.deepEqual(periodOf(list, name), { from: T("2026-10-09T09:00:00"), to: T("2026-10-09T12:00:00"), approx: false, end: "read" });
  const noLast = normalizePlan({ sources: [src("a", { first_ts: T("2026-10-09T10:00:00") }), src("b", { first_ts: T("2026-10-09T10:30:00") })] }).sources;
  assert.deepEqual(periodOf(noLast, name), { from: T("2026-10-09T10:00:00"), to: T("2026-10-09T14:30:00"), approx: true, end: "capture" }, "the capture time ends it");
  assert.deepEqual(periodOf(noLast, "plain.zip"), { from: T("2026-10-09T10:00:00"), to: T("2026-10-09T10:30:00"), approx: true, end: "latest" });
  const early = periodOf(noLast, "x_20200101000000_y.zip");
  assert.equal(early.to, T("2026-10-09T10:30:00"), "a capture time before the first record is not an end");
  assert.equal(periodOf([], name), null);
  assert.equal(periodOf(normalizePlan({ sources: [src("a")] }).sources, name), null, "no time at all");
  assert.deepEqual(mergePeriods([null, { from: 5, to: 9, approx: false, end: "read" }, { from: 2, to: 7, approx: true, end: "latest" }]), { from: 2, to: 9, approx: true, end: "latest" });
  assert.equal(mergePeriods([null, null]), null);
});

test("what the checked boxes mean", () => {
  const list = normalizePlan({ sources: [src("a"), src("b"), src("c", { selected: false }), src("d")] }).sources;
  assert.deepEqual(chosenSources(list, new Set(["a", "b", "d"])), { names: null, none: false, tooMany: false, total: 3, count: 3 }, "all = no restriction");
  assert.deepEqual(chosenSources(list, new Set(["a", "d", "c"])), { names: ["a", "d"], none: false, tooMany: false, total: 3, count: 2 }, "c is not choosable");
  assert.equal(chosenSources(list, new Set()).none, true);
  assert.equal(chosenSources(list, new Set(["c", "zzz"])).none, true, "unknown or unchoosable members do not count");
  assert.equal(chosenSources([], new Set()).names, null, "nothing to choose from: no restriction, no error");
  const big = normalizePlan({ sources: Array.from({ length: MAX_FIRST_SOURCES + 2 }, (_, i) => src("s" + i)) }).sources;
  const some = new Set(big.slice(0, MAX_FIRST_SOURCES + 1).map((s) => s.member));
  assert.equal(chosenSources(big, some).tooMany, true);
  assert.equal(chosenSources(big, new Set(big.slice(0, MAX_FIRST_SOURCES).map((s) => s.member))).tooMany, false, "500 is allowed");
});

test("first: only the keys that are set, nothing when nothing narrows, instants at the edges", () => {
  assert.equal(buildFirst({}), null);
  assert.equal(buildFirst({ from: null, to: null, sources: [] }), null, "an empty list is no list");
  assert.deepEqual(buildFirst({ from: 5 }), { from: 5 });
  assert.deepEqual(buildFirst({ to: 9, sources: ["a"] }), { to: 9, sources: ["a"] });
  assert.deepEqual(buildFirst({ from: 0 }), { from: 0 }, "0 is an instant");
  assert.equal(buildFirst({ from: NaN, to: 2 ** 60, sources: null }), null);
  assert.equal(buildFirst({ from: "5" }), null, "text is not an instant");
  const names = ["a"];
  const first = buildFirst({ sources: names });
  names.push("b");
  assert.deepEqual(first.sources, ["a"], "the list is copied");
});

test("does the window touch the period", () => {
  const period = { from: 100, to: 200, approx: false };
  assert.equal(windowTouchesPeriod({ from: 150, to: 160 }, period), true);
  assert.equal(windowTouchesPeriod({ from: 200, to: 300 }, period), true, "the last instant is inside");
  assert.equal(windowTouchesPeriod({ from: 201, to: 300 }, period), false);
  assert.equal(windowTouchesPeriod({ from: 0, to: 100 }, period), false, "the end is not included");
  assert.equal(windowTouchesPeriod({ from: null, to: 101 }, period), true);
  assert.equal(windowTouchesPeriod({ from: 500, to: null }, period), false);
  assert.equal(windowTouchesPeriod({ from: 1, to: 2 }, null), true, "no known period: no warning");
});

test("pending work: garbage in, safe numbers out", () => {
  assert.deepEqual(normalizePending(null), { running: false, items: [] });
  assert.deepEqual(normalizePending({ pending: "x" }), { running: false, items: [] });
  const { running, items } = normalizePending({ running: true, pending: [null, 5, { group: 2, partial: 1, pending: "3", indexing: -1 }, { partial: NaN }] });
  assert.equal(running, true);
  assert.deepEqual(items, [
    { group: 2, partial: 1, pending: 3, indexing: 0 },
    { group: null, partial: 0, pending: 0, indexing: 0 },
  ]);
  assert.deepEqual(pendingTotals(items), { collections: 2, sources: 4 });
  assert.equal(normalizePending({ running: "yes", pending: [] }).running, false, "only true counts");
  assert.equal(safeCount(2 ** 53), 2 ** 53);
  assert.equal(safeCount(undefined), 0);
  assert.deepEqual(unmatchedNames({ first_unmatched: ["a", "", 5, null, "b"] }), ["a", "b"]);
  assert.deepEqual(unmatchedNames({}), []);
  assert.equal(partialLeft({ partial_left: "3" }), 3);
  assert.equal(partialLeft({ partial_left: -2 }), 0);
  assert.equal(partialLeft(null), 0);
});

test("rotation chains: same stream and name without .N; choosing one source chooses the chain", () => {
  const list = normalizePlan({ sources: [
    src("a", { name: "core.log", family: "Wfm", pid: 7 }),
    src("b", { name: "core.log.1", family: "Wfm", pid: 7 }),
    src("c", { name: "core.log.12", family: "Wfm", pid: 7 }),
    src("d", { name: "core.log", family: "Wfm", pid: 8 }),
    src("e", { name: "core.log", family: "Wfm", pid: 7, sensitive: true }),
    src("f", { name: "core.log.1", family: "Wfm", pid: 7, selected: false }),
  ] }).sources;
  assert.equal(chainKey(list[0]), chainKey(list[1]));
  assert.equal(chainKey(list[0]), chainKey(list[2]));
  assert.notEqual(chainKey(list[0]), chainKey(list[3]), "another pid is another stream");
  assert.notEqual(chainKey(list[0]), chainKey(list[4]), "sensitive is another stream");
  assert.deepEqual(chainMembers(list, "b"), ["a", "b", "c"], "the member outside the scope is not part of the choice");
  assert.deepEqual(chainMembers(list, "f"), [], "not choosable");
  assert.deepEqual(chainMembers(list, "zzz"), []);
  const one = chosenSources(list, new Set(["b"]));
  assert.deepEqual(one.names, ["a", "b", "c"]);
  assert.equal(one.count, 3);
  assert.equal(one.total, 5);
  assert.equal(chosenSources(list, new Set(["a", "d", "e"])).names, null, "all chains: no restriction");
});
