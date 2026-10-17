// UI-24: the "same key" panel on the fake DOM: the columns of the grid, one line per record, no full path and no
// template code, "Show in grid" and the case guard. The "server" is a held request the test answers.
import test from "node:test";
import assert from "node:assert/strict";

import { El, installGlobalDom, tick } from "./fixtures/fake-dom.mjs";

const doc = installGlobalDom();
globalThis.Element = El;
const { initKeyPanel } = await import("../../src/dmslog/web/js/key-panel.js");
const { S } = await import("../../src/dmslog/web/js/strings.js");
const { createDock } = await import("../../src/dmslog/web/js/dock.js");
const { registerAppPanels } = await import("../../src/dmslog/web/js/app-actions.js");
const { api } = await import("../../src/dmslog/web/js/api.js");

let held = [];
globalThis.fetch = (url, options = {}) => new Promise((resolve, reject) => {
  held.push({ url: String(url), resolve, reject });
  options.signal?.addEventListener?.("abort", () => reject(Object.assign(new Error("aborted"), { name: "AbortError" })));
});
const answer = (request, status, data) => request.resolve({
  ok: status < 400, status, json: async () => data, text: async () => JSON.stringify(data), headers: { get: () => null },
});

const cls = (node, name) => node.className.split(/\s+/).includes(name);
const find = (root, name) => root.all().find((n) => cls(n, name));
const findAll = (root, name) => root.all().filter((n) => cls(n, name));

const rec = (id, extra = {}) => ({
  id, source_id: id % 2 ? 1 : 2, member: "C:/ERS/Log/Adapter_1" + (id % 2 ? "" : ".sensitive") + ".log", sensitive: id % 2 === 0,
  ts: id * 1000, tid: 1, level: 3, sev: 4, tpl_code: "WFM-001", tpl_id: 9, msg: "message " + id, ...extra,
});

function world(overrides = {}) {
  const state = { caseId: "A" };
  const host = doc.createElement("div");
  doc.body.appendChild(host);
  const calls = { select: [], show: [] };
  const panel = initKeyPanel(host, {
    getCase: () => state.caseId,
    onSelect: (row) => calls.select.push(row),
    onShowInGrid: (arg) => calls.show.push(arg),
    getSourceName: (id) => (id === 1 ? "C:\\ERS\\Log\\Adapter_1.log" : "C:\\ERS\\Log\\Adapter_1.sensitive.log"),
    getTypeLabel: (id) => (id === 9 ? "Operation started" : ""),
    ...overrides,
  });
  return { state, host, calls, panel, button: find(host, "key-show") };
}

async function load(w, records, next = null) {
  held = [];
  const opening = w.panel.open("msgid", "A/B");
  assert.equal(held.length, 1);
  answer(held[0], 200, { records, next });
  await opening;
  await tick();
}

test("the columns are those of the grid and every record is one row", async () => {
  const w = world();
  await load(w, [rec(1), rec(2), rec(3)]);
  const headers = w.host.all().filter((n) => n.tagName === "TH").map((n) => n.textContent);
  assert.deepEqual(headers, ["Time", "Source", "Level", "Message type", "Message"]);
  const rows = findAll(w.host, "key-row");
  assert.equal(rows.length, 3);
  for (const row of rows) {
    assert.equal(row.children.length, 5);
  }
  const first = rows[0].children.map((c) => c.textContent);
  assert.deepEqual(first, ["1970-01-01 00:00:01.000", "Adapter_1.log", "ERROR", "Operation started", "message 1"]);
});

test("no full path and no template code anywhere in the panel; the sensitive source carries the S badge", async () => {
  const w = world();
  await load(w, [rec(1), rec(2)]);
  const text = w.host.all().map((n) => n.textContent + " " + n.title + " " + (n.attrs["aria-label"] || "")).join("\n");
  assert.equal(text.includes("ERS"), false, "no directory");
  assert.equal(text.includes("C:"), false);
  assert.equal(text.includes("Log/") || text.includes("\\"), false, "no path separator of a source");
  assert.equal(text.includes("WFM-001"), false, "no template code");
  const rows = findAll(w.host, "key-row");
  assert.equal(findAll(rows[0], "ui-badge").length, 0);
  const badge = find(rows[1], "ui-badge");
  assert.equal(badge.textContent, S.gridSensitiveBadge);
  // the accessible name is on the CELL (full name + "sensitive"), like the grid; the badge itself is decorative
  assert.equal(rows[1].children[1].getAttribute("aria-label"), "Adapter_1.sensitive.log (sensitive)");
  assert.equal(badge.getAttribute("aria-hidden"), "true");
  assert.equal(badge.getAttribute("aria-label"), null, "a generic span cannot carry a name");
  assert.equal(rows[0].children[1].getAttribute("aria-label"), null, "a normal source needs no extra name");
  assert.equal(rows[1].children[1].textContent, "SAdapter_1.log");
  assert.equal(rows[1].children[1].title, "Adapter_1.sensitive.log (sensitive)");
});

test("the count uses the right plural and a message keeps its text literal", async () => {
  const w = world();
  await load(w, [rec(1, { msg: "<b>x</b>" })]);
  assert.equal(find(w.host, "key-status").textContent, "1 record");
  assert.equal(find(w.host, "key-text").textContent, "<b>x</b>");
  assert.equal(find(w.host, "key-text").title, "<b>x</b>", "the cut text stays readable in the title");
  await load(w, [rec(1), rec(2)], "2000:2");
  assert.equal(find(w.host, "key-status").textContent, "2 records so far (more available)");
});

test("Show in grid: disabled until a key is open, then hands the key filter to the host once per click", async () => {
  const w = world();
  assert.equal(w.button.disabled, true);
  w.button.click();
  assert.equal(w.calls.show.length, 0);
  await load(w, [rec(1)]);
  assert.equal(w.button.disabled, false);
  assert.equal(w.button.textContent, S.keyShowInGrid);
  w.button.click();
  assert.deepEqual(w.calls.show, [{ type: "msgid", value: "A/B", filter: { key: { type: "msgid", eq: "A/B" } } }]);
  w.panel.close();
  assert.equal(w.button.disabled, true, "closed panel: nothing to show");
  w.button.click();
  assert.equal(w.calls.show.length, 1);
});

test("Show in grid does nothing when the case changed after the key was read", async () => {
  const w = world();
  await load(w, [rec(1)]);
  w.state.caseId = "B";
  w.button.dispatch("click");
  assert.equal(w.calls.show.length, 0);
  w.state.caseId = null;
  w.button.dispatch("click");
  assert.equal(w.calls.show.length, 0);
});

test("Show in grid is disabled with no case and without a host callback it is inert", async () => {
  const w = world({ onShowInGrid: undefined });
  await load(w, [rec(1)]);
  w.button.dispatch("click"); // no callback: nothing happens, nothing throws
  const none = world();
  none.state.caseId = null;
  held = [];
  await none.panel.open("msgid", "x");
  assert.equal(none.button.disabled, true);
});

test("a row selects its record in the grid", async () => {
  const w = world();
  await load(w, [rec(1), rec(2)]);
  const open = findAll(w.host, "key-open")[1];
  open.dispatch("click");
  assert.deepEqual(w.calls.select, [{ id: 2, ts: 2000 }]);
});

test("the dock panel is wired to the host: sources, type titles and Show in grid", async () => {
  held = [];
  const root = doc.createElement("section");
  doc.body.appendChild(root);
  const dock = createDock({ root, storage: null, onFocusOut() {}, doc });
  const shown = [];
  const ctx = Object.freeze({
    caseId: () => "A", filter: () => null, userFilter: () => null, selectedRow: () => null, entry: () => null,
    setUserFilter: async () => {}, reveal: async () => {}, dock, status: { message: () => ({ dismiss() {} }) },
  });
  dock.bind(ctx);
  registerAppPanels(dock, {
    api, derivedApi: api, highlightStore: {}, bookmarkStore: {}, fieldNames: () => [], hasCollections: () => false,
    sourceName: (id) => "D:\\Logs\\Src_" + id + ".log", typeLabel: () => "Readable title", showKeyInGrid: (arg) => shown.push(arg),
    onError() {},
  });
  const opening = dock.open("sameKey", { type: "gid", value: "7" });
  for (let i = 0; i < 200 && held.length === 0; i += 1) {
    await tick();
  }
  answer(held[0], 200, { records: [rec(1)], next: null });
  await opening;
  await tick();
  const host = root.all().find((n) => n.id === "sameKey-host");
  const row = find(host, "key-row");
  assert.deepEqual(row.children.map((c) => c.textContent).slice(1, 4), ["Src_1.log", "ERROR", "Readable title"]);
  find(host, "key-show").click();
  assert.deepEqual(shown.map((a) => a.type + "=" + a.value), ["gid=7"]);
});

test("a type without a title shows the neutral label, never its template code", async () => {
  const w = world({ getTypeLabel: () => "" });
  await load(w, [rec(1), rec(2, { tpl_id: null, tpl_code: null })]);
  const types = findAll(w.host, "key-type").map((n) => n.textContent);
  assert.deepEqual(types, [S.keyTypeUnclassified, S.keyTypeUnclassified]);
  assert.equal(w.host.all().some((n) => n.textContent.includes("WFM-001") || n.title.includes("WFM-001")), false);
});
