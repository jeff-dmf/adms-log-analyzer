// Compare two records: the two-step selection, the diff rows and the loading flow.
import test from "node:test";
import assert from "node:assert/strict";

import {
  CONTEXT_LINES, buildDiffRows, buildSideHeader, compareButtonLabel, createCompareFlow, createCompareSelection,
  statsText,
} from "../../src/dmslog/web/js/compare-model.js";

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

test("selection: mark, compare, cancel, ignore bad ids", () => {
  const sel = createCompareSelection();
  assert.deepEqual(sel.pick(5), { action: "mark", a: 5 });
  assert.equal(sel.first, 5);
  assert.deepEqual(sel.pick(5), { action: "cancel" });
  assert.equal(sel.first, null);
  sel.pick(5);
  assert.deepEqual(sel.pick(8), { action: "compare", a: 5, b: 8 });
  assert.equal(sel.first, null);
  assert.deepEqual(sel.pick("x"), { action: "none" });
  sel.pick(1);
  sel.reset();
  assert.equal(sel.first, null);
});

test("button label names the first record only while choosing the second", () => {
  assert.match(compareButtonLabel(null, 3), /\.\.\./);
  assert.match(compareButtonLabel(5, 3), /5/);
  assert.match(compareButtonLabel(5, 5), /\.\.\./);
});

const op = (tag, a, b, aStart, bStart) => ({ tag, a, b, a_start: aStart, b_start: bStart });

test("diff rows: change, remove, add and line numbers", () => {
  const rows = buildDiffRows([
    op("equal", ["h"], ["h"], 1, 1),
    op("replace", ["x", "y"], ["X"], 2, 2),
    op("delete", ["z"], [], 4, 3),
    op("insert", [], ["n"], 5, 3),
  ], false);
  assert.deepEqual(rows.map((r) => r.kind), ["equal", "change", "remove", "remove", "add"]);
  assert.deepEqual(rows[1], { kind: "change", a: "x", b: "X", aLine: 2, bLine: 2 });
  assert.deepEqual(rows[2], { kind: "remove", a: "y", b: null, aLine: 3, bLine: null });
  assert.deepEqual(rows[4], { kind: "add", a: null, b: "n", aLine: null, bLine: 3 });
});

test("hiding unchanged runs keeps context around changes only", () => {
  const long = Array.from({ length: 30 }, (_, i) => "l" + i);
  const ops = [op("equal", long, long, 1, 1), op("replace", ["a"], ["b"], 31, 31), op("equal", long, long, 32, 32)];
  const rows = buildDiffRows(ops, true);
  const kinds = rows.map((r) => r.kind);
  assert.equal(kinds.filter((k) => k === "skip").length, 2);
  // first run: no context before it (starts the text), CONTEXT_LINES after; last run mirrors it
  assert.equal(rows.filter((r) => r.kind === "equal").length, 2 * CONTEXT_LINES);
  assert.equal(rows[0].kind, "skip");
  assert.equal(rows[0].count, 30 - CONTEXT_LINES);
  assert.equal(kinds.at(-1), "skip");
  assert.equal(buildDiffRows(ops, false).length, 61);
  assert.deepEqual(buildDiffRows(null, true), []);
});

test("side header and stats text", () => {
  const header = buildSideHeader({ id: 4, ts: null, member: "Log/a.log", tid: 255, tpl: null });
  assert.match(header.title, /4/);
  assert.equal(header.items.find((i) => i.text === "0xff") !== undefined, true);
  assert.equal(header.items.filter((i) => i.text === "-").length >= 1, true);
  assert.match(statsText({ changed: 1, removed: 2, added: 3, equal: 4 }), /1.*2.*3.*4/);
});

function flowSetup() {
  const calls = [];
  const states = [];
  const current = { caseId: "A" };
  const api = {
    compare(caseId, a, b, signal) {
      const d = deferred();
      calls.push({ caseId, a, b, signal, d });
      return d.promise;
    },
  };
  const flow = createCompareFlow({ api, getCase: () => current.caseId, onState: (s) => states.push(s.status) });
  return { flow, calls, states, current };
}

test("flow: ready, error, stale answers and no case", async () => {
  const { flow, calls, current } = flowSetup();
  const first = flow.open(1, 2);
  const second = flow.open(3, 4);
  assert.equal(calls[0].signal.aborted, true);
  calls[0].d.resolve({ a: { id: 1 }, b: { id: 2 }, diff: {} });
  await first;
  assert.equal(flow.state.data, null);
  calls[1].d.resolve({ a: { id: 3 }, b: { id: 4 }, diff: { ops: [] } });
  await second;
  assert.equal(flow.state.status, "ready");
  assert.equal(flow.state.data.a.id, 3);
  const failing = flow.open(5, 6);
  calls[2].d.reject(new Error("gone"));
  await failing;
  assert.equal(flow.state.status, "error");
  assert.equal(flow.state.error, "gone");
  flow.close();
  assert.equal(flow.state.status, "idle");
  current.caseId = null;
  await flow.open(1, 2);
  assert.equal(flow.state.status, "nocase");
});
