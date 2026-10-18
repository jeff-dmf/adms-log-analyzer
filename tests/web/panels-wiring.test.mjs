// The Panels panel as the app wires it: the real dock, app-actions.js, the real API client over a held fetch and a fake
// EventSource. Loaded on demand, shown and hidden with its tab, reset by a case change, and its requests (api.panels.*).
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";

import { installStrictDom } from "./fixtures/strict-dom.mjs";
import { flush, panel, runAnswer, widget } from "./fixtures/panels-world.mjs";

const doc = installStrictDom();
const { createDock } = await import("../../src/dmslog/web/js/dock.js");
const { registerAppPanels } = await import("../../src/dmslog/web/js/app-actions.js");
const { api } = await import("../../src/dmslog/web/js/api.js");
const { S } = await import("../../src/dmslog/web/js/strings.js");

let held = [];
globalThis.fetch = (url, options = {}) => new Promise((resolve, reject) => {
  const request = { url: String(url), method: options.method, raw: options.body, body: options.body ? JSON.parse(options.body) : undefined, resolve, reject, aborted: false };
  held.push(request);
  options.signal?.addEventListener?.("abort", () => {
    request.aborted = true;
    reject(Object.assign(new Error("aborted"), { name: "AbortError" }));
  });
});
const answer = (request, status, data) => request.resolve({
  ok: status < 400, status, json: async () => data, text: async () => JSON.stringify(data), headers: { get: () => null },
});

class FakeEventSource {
  static all = [];

  constructor(url) {
    this.url = url;
    this.listeners = {};
    this.readyState = 1;
    this.closed = false;
    FakeEventSource.all.push(this);
  }

  addEventListener(type, fn) { (this.listeners[type] ??= []).push(fn); }
  close() { this.closed = true; this.readyState = 2; }
  emit(type, data) { for (const fn of this.listeners[type] ?? []) fn({ type, data }); }
}
globalThis.EventSource = FakeEventSource;

const cls = (node, name) => node.className.split(/\s+/).includes(name);
const byClass = (root, name) => root.all().filter((n) => cls(n, name));
const P = panel("aaaaaaaaaaaa", "Alpha", [widget("w1"), widget("w2")]);
const LIMITS = { panels: 20, widgets: 16, rows: 4000, title_chars: 80, name_chars: 80 };

async function until(check, what) {
  for (let i = 0; i < 400; i += 1) {
    if (check()) return;
    await new Promise((resolve) => setTimeout(resolve, 2));
  }
  throw new Error("timed out waiting for " + what);
}

function world() {
  held = [];
  FakeEventSource.all = [];
  const root = doc.createElement("section");
  doc.body.appendChild(root);
  const dock = createDock({ root, storage: null, onFocusOut() {}, doc });
  const state = { caseId: "A" };
  const ctx = Object.freeze({
    caseId: () => state.caseId, filter: () => null, userFilter: () => null, selectedRow: () => null, entry: () => null,
    setUserFilter: async () => {}, reveal: async () => {}, dock, status: { message: () => ({ dismiss() {} }) },
  });
  dock.bind(ctx);
  registerAppPanels(dock, {
    api, derivedApi: api, highlightStore: {}, bookmarkStore: {}, fieldNames: () => ["op"], hasCollections: () => false,
    sourceName: () => "", typeLabel: () => "", showKeyInGrid() {}, onError() {},
  });
  dock.register({ id: "other", titleKey: "actConfig", mount: async (host) => { host.textContent = "other"; return {}; } });
  const hostOf = () => root.all().find((n) => n.id === "panels-host");
  const find = (url, method) => held.filter((r) => r.url.startsWith(url) && (method === undefined || r.method === method));
  return { dock, state, root, hostOf, find };
}

async function opened() {
  const w = world();
  const opening = w.dock.open("panels");
  await until(() => w.find("/api/panels", "GET").length === 1, "the list request");
  answer(w.find("/api/panels", "GET")[0], 200, { panels: [P], invalid: false, problems: [], limits: LIMITS });
  await until(() => w.find("/api/panels/run").length === 1, "the first run");
  answer(w.find("/api/panels/run")[0], 200, runAnswer(P));
  await opening;
  await flush();
  return w;
}

test("the panel is registered for on-demand loading only: app-actions imports it with import(), never statically", () => {
  const source = fs.readFileSync(new URL("../../src/dmslog/web/js/app-actions.js", import.meta.url), "utf8");
  assert.equal([...source.matchAll(/import\("\.\/panels-panel\.js"\)/g)].length, 1, "the mount");
  assert.match(source, /analyze\("analyze\.panels", "actPanels", "panels", "\.\/panels-panel\.js"\)/, "the menu action loads it on hover and focus");
  assert.equal(/^import .*panels-(panel|model)/m.test(source), false);
  const defs = [];
  registerAppPanels({ register: (def) => defs.push(def) }, { api: {}, derivedApi: {} });
  const def = defs.find((d) => d.id === "panels");
  assert.equal(def.titleKey, "actPanels");
  assert.deepEqual(def.listens, ["case"]);
});

test("api.panels: methods, paths and bodies", async () => {
  held = [];
  api.panels.list();
  api.panels.create({ name: "N", widgets: [] });
  api.panels.update("aaaaaaaaaaaa", { name: "N", widgets: [] }, 3);
  api.panels.update("aaaaaaaaaaaa", { name: "N", widgets: [] });
  api.panels.remove("aaaaaaaaaaaa");
  api.panels.run("case 1", "aaaaaaaaaaaa");
  api.panels.run("c", "aaaaaaaaaaaa", ["w1"]);
  assert.deepEqual(held.map((r) => [r.method, r.url, r.body]), [
    ["GET", "/api/panels", undefined],
    ["POST", "/api/panels", { name: "N", widgets: [] }],
    ["PUT", "/api/panels", { id: "aaaaaaaaaaaa", panel: { name: "N", widgets: [] }, if_rev: 3 }],
    ["PUT", "/api/panels", { id: "aaaaaaaaaaaa", panel: { name: "N", widgets: [] } }],
    ["DELETE", "/api/panels?id=aaaaaaaaaaaa", undefined],
    ["POST", "/api/panels/run", { case: "case 1", id: "aaaaaaaaaaaa" }],
    ["POST", "/api/panels/run", { case: "c", id: "aaaaaaaaaaaa", widgets: ["w1"] }],
  ]);
});

test("opening the tab loads the module, reads the list, runs the first panel and listens to the case's stream", async () => {
  const w = await opened();
  assert.equal(w.dock.isOpen("panels"), true);
  assert.deepEqual(w.find("/api/panels/run")[0].body, { case: "A", id: P.id });
  assert.equal(byClass(w.hostOf(), "pn-widget").length, 2);
  assert.equal(FakeEventSource.all.length, 1);
  assert.equal(FakeEventSource.all[0].url, "/api/cases/A/progress");
  assert.ok(byClass(w.hostOf(), "pn-run")[0]);
});

test("a panels event runs the whole panel again; a repeated or foreign event does not pile up requests", async () => {
  const w = await opened();
  await new Promise((resolve) => setTimeout(resolve, 520)); // the smallest interval a notice can ask for is 0.5 s
  const stream = FakeEventSource.all[0];
  stream.emit("panels", JSON.stringify({ cycle: 1, seq: 5, to_id: 9, throttled: false, retry_after_ms: null, min_interval_ms: 500 }));
  await until(() => w.find("/api/panels/run").length === 2, "the second run");
  stream.emit("panels", JSON.stringify({ cycle: 2, seq: 6, throttled: false, min_interval_ms: 500 }));
  stream.emit("panels", "garbage");
  assert.equal(w.find("/api/panels/run").length, 2, "one run at a time; the notice during it is kept for later");
  answer(w.find("/api/panels/run")[1], 200, runAnswer(P));
  await flush();
  w.dock.close("panels"); // the pending run (if any) must not survive the tab
  await flush();
});

test("another tab in front pauses the panel (no stream, no run); coming back runs it again", async () => {
  const w = await opened();
  await w.dock.open("other");
  assert.equal(FakeEventSource.all[0].closed, true);
  await w.dock.open("panels");
  await until(() => w.find("/api/panels/run").length === 2, "the run after coming back");
  assert.equal(FakeEventSource.all.length, 2);
  assert.equal(FakeEventSource.all[1].closed, false);
  answer(w.find("/api/panels/run")[1], 200, runAnswer(P));
  await flush();
});

test("closing the tab aborts the run in flight and closes the stream", async () => {
  const w = await opened();
  byClass(w.hostOf(), "pn-run")[0].click();
  await until(() => w.find("/api/panels/run").length === 2, "a manual run");
  w.dock.close("panels");
  assert.equal(w.find("/api/panels/run")[1].aborted, true);
  assert.equal(FakeEventSource.all[0].closed, true);
  await flush();
});

test("a case change clears the results; the dock's refresh runs the panel in the new case on a new stream", async () => {
  const w = await opened();
  w.state.caseId = "B";
  w.dock.caseChanged();
  assert.equal(FakeEventSource.all[0].closed, true);
  assert.deepEqual(byClass(w.hostOf(), "pn-widget").map((c) => c.children[1].textContent), [S.pnWidgetNotRun, S.pnWidgetNotRun]);
  w.dock.notify("case");
  await until(() => w.find("/api/panels/run").length === 2, "the run in the new case");
  assert.deepEqual(w.find("/api/panels/run")[1].body, { case: "B", id: P.id });
  assert.equal(FakeEventSource.all.at(-1).url, "/api/cases/B/progress");
  answer(w.find("/api/panels/run")[1], 200, runAnswer(P));
  await flush();
});

test("Escape in the name field is the panel's: the tab stays open", async () => {
  const w = await opened();
  byClass(w.hostOf(), "pn-new")[0].click();
  const input = byClass(w.hostOf(), "pn-name-input")[0];
  input.dispatch("keydown", { key: "Escape" });
  assert.equal(w.dock.isOpen("panels"), true);
  assert.equal(byClass(w.hostOf(), "pn-name-form")[0].hidden, true);
  w.dock.close("panels");
});

test("a panel that fails to load shows the dock's message, and the next open tries again", async () => {
  const w = world();
  const opening = w.dock.open("panels");
  await until(() => w.find("/api/panels", "GET").length === 1, "the list request");
  w.find("/api/panels", "GET")[0].reject(new Error("offline"));
  await opening;
  await flush();
  assert.equal(w.dock.isOpen("panels"), true);
  assert.match(byClass(w.hostOf(), "pn-empty")[0].textContent, /request failed|could not be reached/i);
  w.dock.close("panels");
  void w.dock.open("panels");
  await until(() => w.find("/api/panels", "GET").length === 2, "a second list request");
  w.dock.close("panels");
});

test("a 64-bit revision survives the trip: read as digits, written back as the same number", async () => {
  held = [];
  const text = '{"panels":[{"id":"aaaaaaaaaaaa","rev":9223372036854775807,"name":"A","widgets":[]}],"problems":[],"invalid":false}';
  const listing = api.panels.list();
  held[0].resolve({ ok: true, status: 200, json: async () => JSON.parse(text), text: async () => text, headers: { get: () => null } });
  const data = await listing;
  assert.equal(data.panels[0].rev, "9223372036854775807", "not 9223372036854776000");
  const { cleanPanel } = await import("../../src/dmslog/web/js/panels-model.js");
  const rev = cleanPanel(data.panels[0]).rev;
  const saving = api.panels.update("aaaaaaaaaaaa", { name: "A", widgets: [] }, rev);
  assert.equal(held[1].raw, '{"id":"aaaaaaaaaaaa","panel":{"name":"A","widgets":[]},"if_rev":9223372036854775807}');
  held[1].resolve({ ok: true, status: 200, json: async () => ({}), text: async () => "{}", headers: { get: () => null } });
  await saving;
  const small = api.panels.update("aaaaaaaaaaaa", { name: "A", widgets: [] }, 3);
  assert.match(held[2].raw, /"if_rev":3\}$/);
  held[2].resolve({ ok: true, status: 200, json: async () => ({}), text: async () => "{}", headers: { get: () => null } });
  await small;
  const hostile = '{"panels":[{"id":"aaaaaaaaaaaa","rev":5,"name":"\\"rev\\":1234567890123456789","widgets":[]}]}';
  const again = api.panels.list();
  held[3].resolve({ ok: true, status: 200, json: async () => JSON.parse(hostile), text: async () => hostile, headers: { get: () => null } });
  assert.equal((await again).panels[0].name, '"rev":1234567890123456789', "text inside a string is never rewritten");
});
