// UI-23: every dock panel in the common frame (.ui-panel: h2 title, Close, body, foot), closing through the dock
// (onRequestClose, Escape, a late answer never reopens a closed tab), the export and "Copy as" dialogs, and the
// compare header. Real registry, real dock and real panel modules on the fake DOM; the "server" is a set of
// held requests released by the test (no waiting on the wall clock).
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { installGlobalDom, tick, until } from "./fixtures/fake-dom.mjs";

const doc = installGlobalDom();
const { api } = await import("../../src/dmslog/web/js/api.js");
const { createDock } = await import("../../src/dmslog/web/js/dock.js");
const { registerAppPanels } = await import("../../src/dmslog/web/js/app-actions.js");
const { createBookmarkStore } = await import("../../src/dmslog/web/js/bookmarks-model.js");
const { createHighlightStore } = await import("../../src/dmslog/web/js/highlight-model.js");
const { createPanelShell, createDialogShell } = await import("../../src/dmslog/web/js/panel-shell.js");
const { S, format } = await import("../../src/dmslog/web/js/strings.js");

const WEB = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../src/dmslog/web");
const read = (file) => fs.readFileSync(path.join(WEB, file), "utf8").replace(/\r\n/g, "\n");

// ---- a fake server: every request is held until the test answers it
let held = [];
globalThis.fetch = (url, options = {}) => new Promise((resolve, reject) => {
  held.push({ url: String(url), resolve, reject });
  options.signal?.addEventListener?.("abort", () => reject(Object.assign(new Error("aborted"), { name: "AbortError" })));
});
const answer = (request, status, data) => request.resolve({
  ok: status < 400, status, json: async () => data, text: async () => JSON.stringify(data), headers: { get: () => null },
});
// Answers every held request with a failure: each panel then writes its error state (the late answer under test).
const failAll = () => {
  const pending = held;
  held = [];
  for (const request of pending) {
    answer(request, 500, { error: "boom", message: "late" });
  }
};

const cls = (node, name) => node.className.split(/\s+/).includes(name);
const find = (root, name) => root.all().find((n) => cls(n, name));

// Starts dock.open() and waits (on the loading of the module, not on the clock) until the tab is open. Returns the
// pending opening, which `settle` finishes by answering whatever the panel asks.
async function startOpen(w, id, args) {
  const opening = w.dock.open(id, args);
  await until(() => w.dock.isOpen(id), id + " is open");
  await tick();
  return { opening }; // wrapped: returning the promise itself would make the caller wait for the opening
}

async function settle({ opening }) {
  let done = false;
  opening.then(() => { done = true; }, () => { done = true; });
  for (let round = 0; round < 50 && !done; round += 1) {
    failAll();
    await tick();
  }
  assert.equal(done, true, "the opening finished");
  return opening;
}

function world() {
  const state = { caseId: "A" };
  const root = doc.createElement("section");
  doc.body.appendChild(root);
  const opener = doc.createElement("button");
  doc.body.appendChild(opener);
  const focused = [];
  const dock = createDock({ root, storage: null, onFocusOut: () => focused.push("grid"), doc });
  const ctx = Object.freeze({
    caseId: () => state.caseId, filter: () => null, userFilter: () => null, selectedRow: () => null, entry: () => null,
    setUserFilter: async () => {}, reveal: async () => {}, dock,
    status: { message: () => ({ dismiss() {} }) },
  });
  dock.bind(ctx);
  const bookmarkStore = createBookmarkStore({ api, getCase: () => state.caseId, onChange: () => {} });
  const highlightStore = createHighlightStore({ api, getCase: () => state.caseId, onChange: () => {} });
  const alertsHolder = {
    flow: { state: { rules: [], history: [], problems: [], invalid: false, error: "" }, refresh: async () => {}, recordsFilterFor: () => null },
    notes: { permission: () => "unsupported", wanted: () => false, setWanted() {}, request: async () => {} },
    showRecords() {},
    on() { return () => {}; },
    clearUnseen() {},
  };
  registerAppPanels(dock, {
    api, derivedApi: api, highlightStore, bookmarkStore,
    fieldNames: () => [], hasCollections: () => false, sourceName: (id) => String(id),
    showEntry() {}, showEventInGrid() {},
    exportContext: () => ({ caseId: state.caseId, filter: null, recordId: 1, eventId: null }),
    filterTreeOptions: () => ({}), onFilterTree() {}, onHighlightPanel() {}, onBookmarksPanel() {},
    alerts: () => alertsHolder, onError() {},
  });
  return { state, root, dock, opener, focused, host: (id) => root.all().find((n) => n.id === id + "-host") };
}

// The panels that use the frame (filterTree is a different widget; export became a dialog).
const FRAMED = [
  { id: "unknownTypes" }, { id: "config" }, { id: "operations" }, { id: "payloads" }, { id: "dashboard" },
  { id: "sideBySide", args: { entryId: 1 } }, { id: "sameKey", args: { type: "msgid", value: "v" } },
  { id: "bookmarks" }, { id: "compare", args: { a: 1, b: 2 } }, { id: "alerts" }, { id: "highlights" },
  { id: "distinct" }, { id: "groupByType" }, { id: "aggregate" },
];

for (const panel of FRAMED) {
  test(`${panel.id}: the frame has an h2 title, a Close button, a scrolling body, and Close closes the tab`, async () => {
    held = [];
    const w = world();
    const opening = await startOpen(w, panel.id, panel.args);
    const host = w.host(panel.id);
    const shell = find(host, "ui-panel");
    assert.ok(shell, "inside .ui-panel");
    const title = shell.all().find((n) => n.tagName === "H2" && cls(n, "ui-panel-title"));
    assert.ok(title && title.textContent.length > 0, "an h2 title");
    assert.ok(find(shell, "ui-panel-body"), "a body that scrolls");
    const close = find(shell, "panel-close");
    assert.ok(close, "a Close button");
    assert.equal(close.getAttribute("aria-label"), format(S.panelCloseLabel, { title: title.textContent }));
    // the head comes first, the body after it (the head and the foot do not scroll away)
    const order = shell.children.map((c) => c.className.split(" ").find((n) => n.startsWith("ui-panel-")));
    assert.deepEqual(order, ["ui-panel-head", "ui-panel-tools", "ui-panel-body", "ui-panel-foot"]);
    await settle(opening);
    assert.equal(w.dock.isOpen(panel.id), true);
    close.click();
    assert.equal(w.dock.isOpen(panel.id), false, "Close closed the tab");
    assert.equal(w.dock.active(), null);
  });

  test(`${panel.id}: Escape inside the panel closes the tab and gives the focus back`, async () => {
    held = [];
    const w = world();
    w.opener.focus();
    const opening = await startOpen(w, panel.id, panel.args);
    await settle(opening);
    assert.equal(w.dock.isOpen(panel.id), true);
    const inside = find(w.host(panel.id), "panel-close");
    inside.focus();
    inside.dispatch("keydown", { key: "Escape" });
    assert.equal(w.dock.isOpen(panel.id), false);
    assert.equal(doc.activeElement, w.opener, "the focus returned to the element that opened the panel");
  });
}

// The regression this task fixes: a panel module showed its own host when an answer arrived, so a slow answer reopened a
// tab the person had closed.
for (const panel of FRAMED.filter((p) => ["payloads", "sideBySide", "sameKey", "bookmarks", "compare", "alerts"].includes(p.id))) {
  test(`${panel.id}: an answer that arrives after the tab was closed does not reopen it`, async () => {
    held = [];
    const w = world();
    const opening = await startOpen(w, panel.id, panel.args);
    w.dock.close(panel.id);
    await settle(opening);
    assert.equal(w.host(panel.id).hidden, true, "the host stays hidden");
    assert.equal(w.dock.isOpen(panel.id), false);
    assert.equal(w.dock.active(), null);
    assert.equal(w.root.hidden, true, "the dock stays closed");
  });
}

test("the panel modules never show their own host and the alerts panel has no Escape handler of its own", () => {
  for (const file of ["compare", "side-by-side", "key-panel", "payload-list", "bookmarks-panel", "alerts-panel"]) {
    const source = read("js/" + file + ".js");
    assert.doesNotMatch(source, /host\.hidden\s*=\s*false/, file + " shows its host");
    assert.doesNotMatch(source, /payload-list-closed/, file);
  }
  const alerts = read("js/alerts-panel.js");
  assert.doesNotMatch(alerts, /"Escape"/);
  assert.doesNotMatch(alerts, /onClose\b/);
  assert.doesNotMatch(read("js/copy-export.js"), /document\.addEventListener/, "Copy as listens to the document");
});

// ---- the frame itself --------------------------------------------------------------------------------------

test("createPanelShell: Refresh sits left of Close in the head, extra head buttons before both, the foot only shows with content", () => {
  const host = doc.createElement("div");
  let refreshed = 0;
  const shell = createPanelShell(host, { title: "Things", onRefresh: () => { refreshed += 1; } });
  const extra = doc.createElement("button");
  shell.addActions(extra);
  assert.deepEqual(shell.actions.children.map((c) => c.className.split(" ").pop()), [extra.className.split(" ").pop(), "panel-refresh", "panel-close"]);
  assert.equal(shell.foot.hidden, true);
  assert.equal(shell.tools.hidden, true);
  shell.addFoot(doc.createElement("button"));
  shell.addTools(doc.createElement("input"));
  assert.equal(shell.foot.hidden, false);
  assert.equal(shell.tools.hidden, false);
  shell.refreshButton.click();
  assert.equal(refreshed, 1);
});

test("createPanelShell: Close calls onRequestClose and leaves the host alone; without it the host is hidden", () => {
  const host = doc.createElement("div");
  const calls = [];
  const shell = createPanelShell(host, { title: "T", onRequestClose: () => calls.push("close") });
  shell.closeButton.click();
  assert.deepEqual(calls, ["close"]);
  assert.equal(host.hidden, false);
  const other = doc.createElement("div");
  createPanelShell(other, { title: "T" }).closeButton.click();
  assert.equal(other.hidden, true);
});

test("createPanelShell: a failing close handler does not break the click", () => {
  const host = doc.createElement("div");
  const shell = createPanelShell(host, { title: "T", onRequestClose: () => { throw new Error("x"); } });
  assert.doesNotThrow(() => shell.closeButton.click());
});

test("each module that closes through the dock calls onRequestClose from its Close button", async () => {
  held = [];
  const seen = [];
  const mk = () => doc.createElement("div");
  const { initCompare } = await import("../../src/dmslog/web/js/compare.js");
  const { initSideBySide } = await import("../../src/dmslog/web/js/side-by-side.js");
  const { initKeyPanel } = await import("../../src/dmslog/web/js/key-panel.js");
  const { initPayloadList } = await import("../../src/dmslog/web/js/payload-list.js");
  const { initBookmarksPanel } = await import("../../src/dmslog/web/js/bookmarks-panel.js");
  const onRequestClose = () => seen.push("closed");
  const store = createBookmarkStore({ api, getCase: () => "A", onChange: () => {} });
  const hosts = [mk(), mk(), mk(), mk(), mk()];
  initCompare(hosts[0], { getCase: () => "A", onRequestClose });
  initSideBySide(hosts[1], { getCase: () => "A", onRequestClose });
  initKeyPanel(hosts[2], { getCase: () => "A", onSelect() {}, onRequestClose });
  initPayloadList(hosts[3], { api, getCase: () => "A", onOpen() {}, onRequestClose });
  initBookmarksPanel(hosts[4], { getCase: () => "A", store, onReveal() {}, onError() {}, onRequestClose });
  for (const host of hosts) {
    find(host, "panel-close").click();
    assert.equal(host.hidden, false, "the module leaves the host to the dock");
  }
  assert.equal(seen.length, 5);
});

// ---- dialogs -----------------------------------------------------------------------------------------------

test("createDialogShell: opens, returns the focus to the opener on Close and on Escape, and fires onClose once", () => {
  const opener = doc.createElement("button");
  doc.body.appendChild(opener);
  opener.focus();
  let closed = 0;
  const shell = createDialogShell(doc, { title: "Dlg", onClose: () => { closed += 1; } });
  doc.body.appendChild(shell.dialog);
  shell.open(opener);
  assert.equal(shell.isOpen(), true);
  assert.equal(shell.dialog.getAttribute("open"), "");
  assert.equal(shell.dialog.getAttribute("aria-labelledby"), shell.titleNode.id);
  assert.equal(shell.titleNode.tagName, "H2");
  find(shell.dialog, "dialog-close").click();
  assert.equal(shell.isOpen(), false);
  assert.equal(doc.activeElement, opener);
  shell.open(opener);
  shell.dialog.dispatch("keydown", { key: "Escape" });
  assert.equal(shell.isOpen(), false, "Escape closed it");
  assert.equal(closed, 2);
  shell.close();
  assert.equal(closed, 2, "closing twice is a no-op");
});

test("createDialogShell: a field that already handled Escape keeps the dialog open", () => {
  const shell = createDialogShell(doc, { title: "Dlg" });
  doc.body.appendChild(shell.dialog);
  shell.open(null);
  shell.dialog.dispatch("keydown", { key: "Escape", defaultPrevented: true });
  assert.equal(shell.isOpen(), true);
  shell.close();
});

test("Export report: a dialog with the build action fixed in the foot, Escape closes it, the focus goes back", async () => {
  held = [];
  const { initExportPanel } = await import("../../src/dmslog/web/js/export-panel.js");
  const host = doc.createElement("div");
  doc.body.appendChild(host);
  const opener = doc.createElement("button");
  doc.body.appendChild(opener);
  opener.focus();
  const exporting = initExportPanel(host, { getContext: () => ({ caseId: "A", filter: null, recordId: 1, eventId: null }), onOpenRedaction() {} });
  exporting.open(opener);
  const dialog = find(host, "export-dialog");
  assert.ok(cls(dialog, "ui-dialog"));
  const foot = find(dialog, "ui-dialog-foot");
  const build = find(dialog, "export-build");
  assert.ok(foot.contains(build), "Build is in the foot");
  assert.equal(build.getAttribute("form"), find(dialog, "export-form").id, "and submits the form in the body");
  assert.ok(!find(dialog, "ui-dialog-body").contains(build));
  assert.equal(dialog.all().filter((n) => n.tagName === "H2").length, 1);
  dialog.dispatch("keydown", { key: "Escape" });
  assert.equal(exporting.isOpen(), false);
  assert.equal(doc.activeElement, opener);
});

test("Export report: reset closes the dialog (a new case never shows the old report)", async () => {
  const { initExportPanel } = await import("../../src/dmslog/web/js/export-panel.js");
  const host = doc.createElement("div");
  doc.body.appendChild(host);
  const exporting = initExportPanel(host, { getContext: () => ({ caseId: "A", filter: null, recordId: 1, eventId: null }), onOpenRedaction() {} });
  exporting.open(null);
  exporting.reset();
  assert.equal(exporting.isOpen(), false);
});

test("Export dialogs: Redaction settings opens on top and gives the focus back to its button; Save is in its foot", async () => {
  held = [];
  const { mountExportDialogs } = await import("../../src/dmslog/web/js/export-dialogs.js");
  const host = doc.createElement("div");
  doc.body.appendChild(host);
  const dialogs = mountExportDialogs(host, { getContext: () => ({ caseId: "A", filter: null, recordId: 1, eventId: null }) });
  dialogs.open(null);
  const settings = find(host, "export-settings");
  settings.focus();
  settings.click();
  const redaction = find(host, "redact-dialog");
  assert.ok(redaction.getAttribute("open") !== null);
  assert.ok(find(redaction, "ui-dialog-foot").contains(find(redaction, "redact-save")));
  redaction.dispatch("keydown", { key: "Escape" });
  assert.equal(redaction.getAttribute("open"), null);
  assert.equal(doc.activeElement, settings);
  assert.equal(find(host, "export-dialog").getAttribute("open"), "", "the export dialog stays open under it");
  dialogs.reset();
  assert.equal(dialogs.isOpen(), false);
  failAll();
});

test("Copy as: a dialog with Copy and Download in the foot; no document-level Escape listener", async () => {
  held = [];
  const { initCopyExport } = await import("../../src/dmslog/web/js/copy-export.js");
  const before = (doc.listeners.keydown || []).length;
  const bar = doc.createElement("div");
  doc.body.appendChild(bar);
  const anchor = doc.createElement("button");
  bar.appendChild(anchor);
  anchor.focus();
  const copy = initCopyExport(anchor, { getContext: () => ({ caseId: "A", filter: null, columns: [], fieldColumns: [], entry: null, recordId: 1 }) });
  assert.equal((doc.listeners.keydown || []).length, before, "no keydown listener on the document");
  copy.open();
  const dialog = find(bar, "cx-dialog");
  const foot = find(dialog, "ui-dialog-foot");
  assert.ok(foot.contains(find(dialog, "cx-copy")) && foot.contains(find(dialog, "cx-download")));
  assert.equal(copy.isOpen(), true);
  dialog.dispatch("keydown", { key: "Escape" });
  assert.equal(copy.isOpen(), false);
  assert.equal(doc.activeElement, anchor, "the focus is back on the Export menu trigger");
  failAll();
});

// ---- primary actions stay in the foot -------------------------------------------------------------------

test("the main action of Aggregate, Alerts and Highlights is in the foot, outside the scrolling body", async () => {
  held = [];
  const w = world();
  const checks = [["aggregate", "agg-run"], ["alerts", "al-create"], ["highlights", "hl-add"], ["payloads", null], ["bookmarks", null]];
  for (const [id, action] of checks) {
    await settle(await startOpen(w, id));
    const shell = find(w.host(id), "ui-panel");
    if (action !== null) {
      const button = find(shell, action);
      assert.ok(find(shell, "ui-panel-foot").contains(button), id + ": " + action + " is in the foot");
      assert.ok(!find(shell, "ui-panel-body").contains(button));
      assert.equal(find(shell, "ui-panel-foot").hidden, false);
    }
  }
});

test("Aggregate: Run submits the form it sits outside of, once per click", async () => {
  held = [];
  const w = world();
  await settle(await startOpen(w, "aggregate"));
  const run = find(w.host("aggregate"), "agg-run");
  assert.equal(run.getAttribute("type") ?? run.type, "submit");
  assert.equal(run.getAttribute("form"), find(w.host("aggregate"), "agg-form").id);
  const before = held.length;
  const event = run.click();
  assert.equal(event.prevented, true, "the click does not also submit the form (that would run twice)");
  await tick();
  assert.ok(held.length > before, "the run asked the server");
  failAll();
});

// ---- compare ------------------------------------------------------------------------------------------------

test("Compare: A and B are one line each with time, source file name and message type; the diff comes before any detail", async () => {
  held = [];
  const w = world();
  const opening = await startOpen(w, "compare", { a: 14941, b: 14942 });
  const side = (id, member) => ({
    id, ts: Date.UTC(2026, 0, 2, 10, 20, 30, 456), member, file_name: member, tid: 255, tpl: "ADP-001", tpl_title: "Request received",
  });
  answer(held.pop(), 200, {
    a: side(14941, "Log/Adapters/online/adapter.log"),
    b: side(14942, "Log\\Adapters\\online\\adapter.log.0"),
    diff: { identical: false, truncated: false, max_lines: 200, stats: { changed: 1, removed: 0, added: 0, equal: 0 }, ops: [{ tag: "replace", a: ["x"], b: ["y"], a_start: 1, b_start: 1 }] },
  });
  await tick();
  await opening.opening;
  const host = w.host("compare");
  const lines = host.all().filter((n) => cls(n, "cmp-side"));
  assert.equal(lines.length, 2);
  const text = lines.map((l) => l.textContent).join("\n");
  assert.match(text, /A.*2026-01-02 10:20:30\.456.*adapter\.log.*Request received/);
  assert.match(text, /B.*adapter\.log\.0/);
  assert.doesNotMatch(text, /14941|Adapters|ADP-001/, "no record number, path or template code");
  const body = find(host, "ui-panel-body");
  const kids = body.children.map((c) => c.className.split(" ")[0]);
  assert.ok(kids.indexOf("cmp-wrap") >= 0 && kids.indexOf("cmp-sides") < kids.indexOf("cmp-wrap"), "the identification is right above the diff");
  assert.equal(host.all().filter((n) => n.tagName === "DL").length, 0, "no long metadata block before the diff");
});

// ---- operations: the label belongs to the field, the filters are one row ----------------------------------

test("Operations: every filter has its label bound to the field and the filters share one row", async () => {
  held = [];
  const w = world();
  await settle(await startOpen(w, "operations"));
  const host = w.host("operations");
  const tools = find(host, "ui-panel-tools");
  const labels = tools.all().filter((n) => n.tagName === "LABEL");
  assert.equal(labels.length, 4);
  for (const label of labels) {
    const field = tools.all().find((n) => n.id !== "" && n.id === label.htmlFor);
    assert.ok(field, "label '" + label.textContent + "' is bound to a field");
  }
  assert.equal(tools.children.length, 1, "one row (a single toolbar)");
  assert.ok(cls(tools.children[0], "ev-toolbar"));
});

// ---- the CSS the frame relies on --------------------------------------------------------------------------

test("CSS: the frame keeps head and foot in place, the body scrolls, filters stay on one row, no letter-by-letter names", () => {
  const dockCss = read("css/dock.css");
  assert.match(dockCss, /\.dock-panel\.is-active:has\(> \.ui-panel\)\s*\{[^}]*height:\s*100%/);
  assert.match(dockCss, /\.dock-panel \.ui-panel-tools\s*\{[^}]*flex-wrap:\s*nowrap/);
  const components = read("css/components.css");
  assert.match(components, /\.ui-panel-body\s*\{[^}]*overflow:\s*auto/);
  assert.match(components, /\.ui-panel-foot\s*\{[^}]*flex:\s*none/);
  const events = read("css/events.css");
  assert.doesNotMatch(events, /\.ev-mono\s*\{[^}]*overflow-wrap:\s*anywhere/, "Name must not break letter by letter");
  assert.match(events, /\.ev-name-cell\s*\{[^}]*min-width:\s*\d+ch/);
  for (const file of ["distinct", "aggregate", "key-panel", "payload-list", "events", "config-panel", "unknown-panel"]) {
    assert.doesNotMatch(read("css/" + file + ".css"), /max-height:\s*(45|50)vh/, file + " still caps its own height");
  }
});
