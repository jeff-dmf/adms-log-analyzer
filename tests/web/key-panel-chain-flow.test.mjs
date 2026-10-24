// The "Operations and handoffs" section of the same-key panel on the fake DOM: hidden without operations, an error of
// the chain does not touch the records, the buttons reach the host, text only through textContent, tab order. The
// "server" is held requests the test answers; names are synthetic.
import test from "node:test";
import assert from "node:assert/strict";

import { El, installGlobalDom, tick } from "./fixtures/fake-dom.mjs";

const doc = installGlobalDom();
globalThis.Element = El;
const { initKeyPanel } = await import("../../src/dmslog/web/js/key-panel.js");
const { api } = await import("../../src/dmslog/web/js/api.js");

let held = [];
globalThis.fetch = (url, options = {}) => new Promise((resolve, reject) => {
  held.push({ url: String(url), resolve, reject });
  options.signal?.addEventListener?.("abort", () => reject(Object.assign(new Error("aborted"), { name: "AbortError" })));
});
// api.keyChain is added with the route (api.js, main session); until then the same request is made here.
if (typeof api.keyChain !== "function") {
  api.keyChain = (caseId, type, value, params, signal) =>
    fetch("/api/keys/" + encodeURIComponent(type) + "/" + encodeURIComponent(value) + "/chain?case=" + caseId, { signal })
      .then(async (response) => {
        const data = await response.json();
        if (!response.ok) {
          throw new Error(data && data.message ? data.message : "failed");
        }
        return data;
      });
}
const answer = (request, status, data) => request.resolve({
  ok: status < 400, status, json: async () => data, text: async () => JSON.stringify(data), headers: { get: () => null },
});
const isChain = (request) => request.url.includes("/chain");

const cls = (node, name) => node.className.split(/\s+/).includes(name);
const find = (root, name) => root.all().find((n) => cls(n, name));
const findAll = (root, name) => root.all().filter((n) => cls(n, name));

const rec = (id) => ({
  id, source_id: 1, member: "Adapter_1.log", sensitive: false, ts: id * 1000, tid: 1, level: 3, sev: 4, tpl_id: 9, msg: "message " + id,
});
const op = (id, name, ts, extra = {}) => ({
  id, kind: "operation", name, key: "100001", status: "ok", ts_start: ts, ts_end: ts + 100, duration_ms: 100, n_entries: 3,
  start_entry_id: id * 10, family: "DMSJOB", process: "Proc_A", pool: null, instance: null, collection: 1, ...extra,
});
const end = (entryId, ts, process, extra = {}) => ({
  entry_id: entryId, ts, stream_id: 1, event_id: null, family: "DMSJOB", process, pool: null, instance: null, tpl_code: "X-1", collection: 1, ...extra,
});
const chainData = (extra = {}) => ({
  key: { type: "job_id", value: "100001" },
  operations: [op(1, "SchedulerJob", 1000), op(2, "LauncherJob", 1500, { status: "fail" })],
  handoffs: [{ kind: "handoff", confidence: 3, dt_ms: 4, from: end(11, 1500, "Proc_A"), to: end(92, 1504, "DMS_FunctionHost_7", { pool: "DMS_FunctionHost", instance: 7 }) }],
  scanned: 16,
  truncated: false,
  ...extra,
});

function world(overrides = {}) {
  const state = { caseId: "A" };
  const host = doc.createElement("div");
  doc.body.appendChild(host);
  const calls = { select: [], grid: [], open: [] };
  const panel = initKeyPanel(host, {
    getCase: () => state.caseId,
    onSelect: (row) => calls.select.push(row),
    onShowEventInGrid: (target) => calls.grid.push(target),
    onOpenOperation: (id) => calls.open.push(id),
    getSourceName: () => "Adapter_1.log",
    getTypeLabel: () => "Title",
    ...overrides,
  });
  return { state, host, calls, panel, section: find(host, "key-chain") };
}

async function open(w, records, chain) {
  held = [];
  const opening = w.panel.open("job_id", "100001");
  assert.equal(held.length, 2);
  const [recordsRequest, chainRequest] = [held.find((r) => !isChain(r)), held.find(isChain)];
  answer(recordsRequest, 200, { records, next: null });
  if (chain instanceof Error) {
    chainRequest.resolve({ ok: false, status: 500, json: async () => ({ error: "x", message: chain.message }), text: async () => chain.message, headers: { get: () => null } });
  } else if (chain !== undefined) {
    answer(chainRequest, 200, chain);
  }
  await opening;
  await tick();
}

test("the section lists operations and handoffs in time order, with the summary and the failure", async () => {
  const w = world();
  await open(w, [rec(1)], chainData());
  assert.equal(w.section.hidden, false);
  assert.equal(find(w.section, "key-chain-title").tagName, "H3");
  assert.equal(w.section.getAttribute("aria-labelledby"), find(w.section, "key-chain-title").id);
  const rows = findAll(w.section, "key-chain-row");
  assert.deepEqual(rows.map((r) => r.children[2].textContent), ["SchedulerJob", "Handoff", "LauncherJob"]);
  assert.equal(rows[1].children[1].textContent, "Proc_A → DMS_FunctionHost #7");
  assert.equal(find(w.section, "key-chain-summary").textContent.startsWith("2 operations, 1 handoff"), true);
  assert.equal(find(w.section, "key-chain-summary").textContent.endsWith("Failed at LauncherJob (Proc_A)"), true);
  assert.equal(findAll(w.host, "key-row").length, 1); // the records table is untouched
});

test("a key without operation or handoff hides the whole section", async () => {
  const w = world();
  await open(w, [rec(1)], { operations: [], handoffs: [], scanned: 1, truncated: false });
  assert.equal(w.section.hidden, true);
  assert.equal(findAll(w.section, "key-chain-row").length, 0);
});

test("the section shows the loading line while the chain is read, and the records do not wait for it", async () => {
  const w = world();
  held = [];
  const opening = w.panel.open("job_id", "100001");
  assert.equal(w.section.hidden, false);
  assert.notEqual(find(w.section, "key-chain-status").textContent, "");
  answer(held.find((r) => !isChain(r)), 200, { records: [rec(1)], next: null });
  await tick();
  assert.equal(findAll(w.host, "key-row").length, 1);
  answer(held.find(isChain), 200, chainData());
  await opening;
  assert.equal(find(w.section, "key-chain-status").textContent, "");
});

test("an error of the chain stays in the section and the records still show", async () => {
  const w = world();
  await open(w, [rec(1), rec(2)], new Error("chain failed"));
  assert.equal(w.section.hidden, false);
  const line = find(w.section, "key-chain-status");
  assert.equal(line.textContent.includes("chain failed"), true);
  assert.equal(cls(line, "is-error"), true);
  assert.equal(line.getAttribute("role"), "status");
  assert.equal(findAll(w.host, "key-row").length, 2);
});

test("a failing records request does not hide the chain", async () => {
  const w = world();
  held = [];
  const opening = w.panel.open("job_id", "100001");
  held.find((r) => !isChain(r)).resolve({ ok: false, status: 500, json: async () => ({ error: "x", message: "records failed" }), text: async () => "", headers: { get: () => null } });
  answer(held.find(isChain), 200, chainData());
  await opening;
  await tick();
  assert.equal(findAll(w.section, "key-chain-row").length, 3);
  assert.equal(find(w.host, "key-status").textContent.includes("records failed"), true);
});

test("a truncated chain says so", async () => {
  const w = world();
  await open(w, [rec(1)], chainData({ truncated: true, scanned: 5000 }));
  const note = find(w.section, "key-chain-note");
  assert.equal(note.hidden, false);
  assert.equal(note.textContent, "Only the first 5000 records with this key were read; the list may be incomplete.");
  const fine = world();
  await open(fine, [rec(1)], chainData());
  assert.equal(find(fine.section, "key-chain-note").hidden, true);
});

test("the buttons reach the host: operation in the grid, open operation, go to record", async () => {
  const w = world();
  await open(w, [rec(1)], chainData());
  const rows = findAll(w.section, "key-chain-row");
  const buttons = (row) => row.all().filter((n) => n.tagName === "BUTTON");
  const [grid, openOp] = buttons(rows[0]);
  assert.equal(grid.getAttribute("aria-label"), "Show operation SchedulerJob in grid");
  assert.equal(openOp.getAttribute("aria-label"), "Open operation SchedulerJob");
  grid.click();
  assert.deepEqual(w.calls.grid, [{ from: 1000, to: 1100, entryId: 10 }]);
  openOp.click();
  assert.deepEqual(w.calls.open, [1]);
  const [go] = buttons(rows[1]);
  go.click();
  assert.deepEqual(w.calls.select, [{ id: 92, ts: 1504 }]);
  assert.equal(buttons(rows[1]).length, 1);
});

test("buttons do nothing once the case changed, and a missing host callback leaves no button", async () => {
  const w = world();
  await open(w, [rec(1)], chainData());
  w.state.caseId = "B";
  find(w.section, "key-chain-action").click();
  assert.deepEqual(w.calls.grid, []);
  const bare = world({ onShowEventInGrid: null, onOpenOperation: null });
  await open(bare, [rec(1)], chainData());
  const operationRow = findAll(bare.section, "key-chain-row")[0];
  assert.equal(operationRow.all().filter((n) => n.tagName === "BUTTON").length, 0);
});

test("text from the log reaches the DOM only as text", async () => {
  const evil = "<script>alert(1)</script>";
  const w = world();
  await open(w, [rec(1)], chainData({ operations: [op(1, evil, 1000, { process: evil })], handoffs: [] }));
  const cell = findAll(w.section, "key-chain-row")[0].children[2];
  assert.equal(cell.textContent, evil);
  assert.equal(cell.children.length, 0);
  assert.equal(w.section.all().some((n) => n.tagName === "SCRIPT"), false);
});

test("tab order: summary, chain buttons, then the records table, then Load more", async () => {
  const w = world();
  await open(w, [rec(1)], chainData());
  const order = w.host.all().filter((n) => n.tagName === "BUTTON" && !n.disabled);
  const firstChain = order.findIndex((n) => cls(n, "key-chain-action"));
  const lastChain = order.length - 1 - [...order].reverse().findIndex((n) => cls(n, "key-chain-action"));
  const firstRecord = order.findIndex((n) => cls(n, "key-open"));
  assert.equal(firstChain >= 0 && firstChain < firstRecord, true);
  assert.equal(lastChain < firstRecord, true);
  for (const button of order) {
    assert.equal(button.type, "button");
  }
  const caption = find(w.section, "visually-hidden");
  assert.equal(caption.tagName, "CAPTION");
  assert.equal(w.section.all().filter((n) => n.tagName === "TH").every((n) => n.scope === "col"), true);
});

test("closing the panel hides the section and a late chain answer is dropped", async () => {
  const w = world();
  held = [];
  const opening = w.panel.open("job_id", "100001");
  w.panel.close();
  assert.equal(w.section.hidden, true);
  answer(held.find(isChain), 200, chainData());
  answer(held.find((r) => !isChain(r)), 200, { records: [rec(1)], next: null });
  await opening;
  await tick();
  assert.equal(w.section.hidden, true);
  assert.equal(findAll(w.section, "key-chain-row").length, 0);
});
