// Pure parts of tools/ui_perf.mjs: no browser, no server.
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import {
  parseArgs, validateBudgets, compareBudgets, deltaPercent, hasFailures, formatTable, analyzeTrace,
  interactionDuration, buildMeasures, browserCandidates, classifyRequests, rowsUpdateState, withTimeout,
  terminateTree, Cdp, mergeIntervals, workByFrame,
} from "../../tools/ui_perf.mjs";

const budgets = JSON.parse(fs.readFileSync(new URL("../../tools/ui_perf_budgets.json", import.meta.url), "utf-8"));

test("the budget file is valid and covers every measure the tool produces", () => {
  assert.deepEqual(validateBudgets(budgets), []);
  const net = { static: 1, staticFull: 0, bytes: 2048 };
  const run = { firstRowMs: 1, net, jsBytes: 1024, jsFiles: 1, cssFiles: 1, cssLateInjections: 0, apiMaxMs: 1, cls: 0 };
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
  assert.equal(hasFailures(compareBudgets({ x: 1, y: 0 }, b)), true); // missing measure z fails the guard
  assert.equal(compareBudgets({ x: 50, y: 0, z: 1 }, b).every((r) => r.status === "pass" || r.status === "n/a"), true);
  assert.equal(hasFailures(rows), true);
  assert.equal(hasFailures(compareBudgets({ x: 1, y: 0, z: 0 }, b)), false);
  const info = compareBudgets({ k: 10 }, { budgets: { k: { label: "K", unit: "KB", info: true } } });
  assert.equal(info[0].status, "info");
  assert.equal(hasFailures(info), false);
});

test("deltaPercent and formatTable", () => {
  assert.equal(deltaPercent(120, 100), 20);
  assert.equal(deltaPercent(1, 0), null);
  const table = formatTable(compareBudgets({ x: 150 }, { budgets: { x: { label: "X", unit: "ms", max: 100, baseline: 100 } } }));
  assert.match(table, /X \(ms\)/);
  assert.match(table, /\+50%/);
  assert.match(table, /FAIL/);
});

test("analyzeTrace separates forced layouts and counts nested work once, per real frame", () => {
  const meta = { ph: "M", name: "thread_name", pid: 1, tid: 2, args: { name: "CrRendererMain" } };
  const ev = (name, ts, dur) => ({ ph: "X", name, pid: 1, tid: 2, ts, dur });
  const draw = (ts) => ({ ph: "I", name: "DrawFrame", pid: 1, tid: 3, ts });
  const events = [
    meta,
    ev("EventDispatch", 0, 3000), ev("Layout", 500, 2000), // forced layout of 2 ms inside a 3 ms script
    draw(4000),
    ev("Layout", 5000, 300), ev("UpdateLayoutTree", 4800, 100), // frame lifecycle, not forced
    draw(6000),
    ev("RunTask", 10_000, 60_000), // long task
    { ph: "X", name: "Layout", pid: 9, tid: 9, ts: 1, dur: 1 }, // other thread: ignored
  ];
  const r = analyzeTrace(events);
  assert.equal(r.forced_layouts, 1);
  assert.equal(r.layouts, 2);
  assert.equal(r.long_tasks, 1);
  assert.equal(r.frames, 2);
  assert.equal(r.work_ms, 3.4); // 3 (script containing the layout) + 0.3 + 0.1, not 5.4
  assert.ok(Math.abs(r.work_per_frame_ms - 1.7) < 1e-9);
  assert.ok(Math.abs(r.max_frame_work_ms - 3) < 1e-9);
  assert.equal(analyzeTrace([meta, ev("Layout", 0, 10)]).work_per_frame_ms, null); // no frames: no number
});

test("mergeIntervals and workByFrame", () => {
  assert.deepEqual(mergeIntervals([[0, 5], [2, 3], [4, 8], [10, 12]]), [[0, 8], [10, 12]]);
  assert.deepEqual(workByFrame([[0, 2], [3, 4], [20, 25]], [5, 10]), [3, 5]); // late work joins the last frame
  assert.deepEqual(workByFrame([[0, 2]], []), []);
});

test("classifyRequests counts from network events: 304, cache and full downloads", () => {
  const origin = "http://127.0.0.1:1";
  const req = (id, path, type = "Script") => ({ method: "Network.requestWillBeSent", params: { requestId: id, type, request: { url: origin + path } } });
  const res = (id, status, extra = {}) => ({ method: "Network.responseReceived", params: { requestId: id, response: { status, ...extra } } });
  const fin = (id, n) => ({ method: "Network.loadingFinished", params: { requestId: id, encodedDataLength: n } });
  const events = [
    req("d", "/", "Document"), res("d", 304), fin("d", 200), // the document is revalidated too
    req("a", "/js/a.js"), res("a", 304), fin("a", 150),
    req("b", "/js/b.js"), res("b", 200, { fromDiskCache: true }), fin("b", 0),
    req("c", "/css/c.css", "Stylesheet"), res("c", 200), fin("c", 1000),
    req("x", "/api/cases", "Fetch"), res("x", 200), fin("x", 50),
    { method: "Network.requestServedFromCache", params: { requestId: "e" } }, // unknown id: ignored
  ];
  const r = classifyRequests(events);
  assert.equal(r.static, 4);
  assert.equal(r.api, 1);
  assert.equal(r.notModified, 2);
  assert.equal(r.fromCache, 1);
  assert.equal(r.staticFull, 1); // only the stylesheet was a full download
  assert.equal(r.bytes, 1400);
  // all static files revalidated: zero full downloads, and the count includes the document
  const all304 = classifyRequests([req("d", "/", "Document"), res("d", 304), req("a", "/js/a.js"), res("a", 304)]);
  assert.equal(all304.static, 2);
  assert.equal(all304.staticFull, 0);
});

test("rowsUpdateState never turns a missing update into success", () => {
  assert.equal(rowsUpdateState({ t0: 0, lastChange: null, now: 100 }), "pending");
  assert.equal(rowsUpdateState({ t0: 0, lastChange: null, now: 6000 }), "timeout");
  assert.equal(rowsUpdateState({ t0: 0, lastChange: 40, now: 100 }), "pending"); // still changing
  assert.equal(rowsUpdateState({ t0: 0, lastChange: 40, now: 200 }), "done");
});

test("withTimeout rejects a promise that never settles", async () => {
  await assert.rejects(withTimeout(new Promise(() => {}), 20, "nothing"), /timeout \(20 ms\) waiting for nothing/);
  assert.equal(await withTimeout(Promise.resolve(7), 20, "x"), 7);
});

test("terminateTree waits until the tree is gone and reports a stuck one", async () => {
  let alive = 3;
  const calls = [];
  assert.equal(await terminateTree({ kill: () => calls.push("kill"), isAlive: () => alive-- > 0, sleepMs: (ms) => calls.push(ms) }), true);
  assert.deepEqual(calls, ["kill", 50, 50, 50]);
  assert.equal(await terminateTree({ kill: () => { throw new Error("gone"); }, isAlive: () => true, sleepMs: () => {}, timeoutMs: 100 }), false);
});

function fakeSocketClass() {
  return class FakeSocket {
    constructor() { this.handlers = {}; this.sent = []; FakeSocket.last = this; }
    addEventListener(type, fn) { (this.handlers[type] ||= []).push(fn); }
    send(data) { this.sent.push(JSON.parse(data)); }
    close() {}
    emit(type, ev = {}) { (this.handlers[type] || []).forEach((fn) => fn(ev)); }
  };
}

test("Cdp rejects pending commands when the socket closes or errors", async () => {
  const WS = fakeSocketClass();
  const cdp = new Cdp("ws://x", { WS, timeoutMs: 5000 });
  const pending = cdp.send("Page.enable");
  WS.last.emit("close");
  await assert.rejects(pending, /DevTools connection closed/);
  await assert.rejects(cdp.send("Page.enable"), /connection closed/); // later commands fail at once
  const WS2 = fakeSocketClass();
  const cdp2 = new Cdp("ws://x", { WS: WS2 });
  const p2 = cdp2.send("A");
  WS2.last.emit("error");
  await assert.rejects(p2, /connection failed/);
});

test("Cdp commands time out and resolve on replies", async () => {
  const WS = fakeSocketClass();
  const cdp = new Cdp("ws://x", { WS, timeoutMs: 30 });
  await assert.rejects(cdp.send("Never"), /no response in 30 ms/);
  const ok = cdp.send("Echo", {}, undefined, 1000);
  WS.last.emit("message", { data: JSON.stringify({ id: WS.last.sent.at(-1).id, result: { fine: true } }) });
  assert.deepEqual(await ok, { fine: true });
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
