// Pure parts of tools/ui_perf.mjs: no browser, no server.
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import {
  parseArgs, validateBudgets, compareBudgets, deltaPercent, hasFailures, formatTable, analyzeTrace,
  interactionDuration, buildMeasures, browserCandidates,
} from "../../tools/ui_perf.mjs";

const budgets = JSON.parse(fs.readFileSync(new URL("../../tools/ui_perf_budgets.json", import.meta.url), "utf-8"));

test("the budget file is valid and covers every measure the tool produces", () => {
  assert.deepEqual(validateBudgets(budgets), []);
  const run = { firstRowMs: 1, requests: 2, staticRequests: 1, notModified: 0, jsBytes: 1024, jsFiles: 1, cssFiles: 1,
    cssLateInjections: 0, apiMaxMs: 1, cls: 0 };
  const raw = { open: run, reload: run, inp: { click: 0, keys: 0, filter: 0, search: 0 },
    toRows: { filter: 0, search: 0 }, scroll: { forced_layouts: 0, work_per_frame_ms: 0, long_tasks: 0 } };
  const measures = buildMeasures(raw);
  assert.deepEqual(Object.keys(measures).sort(), Object.keys(budgets.budgets).sort());
});

test("validateBudgets reports malformed entries", () => {
  assert.ok(validateBudgets(null).length);
  assert.ok(validateBudgets({ schema: 2, reference: {}, budgets: {} }).some((p) => p.includes("schema")));
  const bad = { schema: 1, reference: {}, budgets: { a: { max: "x", unit: "ms", label: "A" }, b: { max: 1, unit: "", label: "B", warn: 2 } } };
  const problems = validateBudgets(bad);
  assert.ok(problems.some((p) => p.startsWith("a:")));
  assert.ok(problems.some((p) => p.startsWith("b:")));
});

test("compareBudgets classifies pass, warn, fail and missing values", () => {
  const b = { budgets: { x: { label: "X", unit: "ms", max: 100, warn: 50 }, y: { label: "Y", unit: "n", max: 0 }, z: { label: "Z", unit: "n", max: 1 } } };
  const rows = compareBudgets({ x: 60, y: 1 }, b);
  assert.deepEqual(rows.map((r) => r.status), ["warn", "fail", "n/a"]);
  assert.equal(compareBudgets({ x: 50, y: 0, z: 1 }, b).every((r) => r.status === "pass" || r.status === "n/a"), true);
  assert.equal(hasFailures(rows), true);
  assert.equal(hasFailures(compareBudgets({ x: 1, y: 0, z: 0 }, b)), false);
});

test("deltaPercent and formatTable", () => {
  assert.equal(deltaPercent(120, 100), 20);
  assert.equal(deltaPercent(1, 0), null);
  const table = formatTable(compareBudgets({ x: 150 }, { budgets: { x: { label: "X", unit: "ms", max: 100, baseline: 100 } } }));
  assert.match(table, /X \(ms\)/);
  assert.match(table, /\+50%/);
  assert.match(table, /FAIL/);
});

test("analyzeTrace separates forced layouts from frame layouts", () => {
  const meta = { ph: "M", name: "thread_name", pid: 1, tid: 2, args: { name: "CrRendererMain" } };
  const ev = (name, ts, dur) => ({ ph: "X", name, pid: 1, tid: 2, ts, dur });
  const events = [
    meta,
    ev("EventDispatch", 0, 2000), ev("Layout", 500, 400), // forced: inside a script
    ev("Layout", 5000, 300), ev("UpdateLayoutTree", 4800, 100), // frame lifecycle: not forced
    ev("RunTask", 10_000, 60_000), // long task
    { ph: "X", name: "Layout", pid: 9, tid: 9, ts: 1, dur: 1 }, // other thread: ignored
  ];
  const r = analyzeTrace(events, 2);
  assert.equal(r.forced_layouts, 1);
  assert.equal(r.layouts, 2);
  assert.equal(r.long_tasks, 1);
  assert.ok(Math.abs(r.work_per_frame_ms - (2 + 0.7 + 0.1) / 2) < 1e-9);
});

test("interactionDuration only counts events with an interaction id", () => {
  assert.equal(interactionDuration([{ interactionId: 0, duration: 99 }, { interactionId: 3, duration: 24 }, { interactionId: 4, duration: 16 }]), 24);
  assert.equal(interactionDuration([]), 0);
});

test("parseArgs accepts the documented flags and rejects bad ones", () => {
  const o = parseArgs(["--large", "--cpu", "4", "--json", "x.json", "--report-only"]);
  assert.equal(o.large, true);
  assert.equal(o.cpu, 4);
  assert.equal(o.reportOnly, true);
  assert.throws(() => parseArgs(["--cpu", "0"]));
  assert.throws(() => parseArgs(["--nope"]));
  assert.throws(() => parseArgs(["--size-mb"]));
});

test("browserCandidates covers Windows Edge and Chrome without assuming POSIX", () => {
  const list = browserCandidates("win32", { PROGRAMFILES: "C:\\Program Files", LOCALAPPDATA: "C:\\Users\\u\\AppData\\Local" });
  assert.ok(list.some((p) => p.endsWith("msedge.exe")));
  assert.ok(list.some((p) => p.endsWith("chrome.exe")));
});
