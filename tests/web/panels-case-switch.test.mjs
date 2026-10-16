// UI-12: through the REAL registry (registerAppPanels), the real dock and the real panel modules on a fake DOM:
// content produced in case A must not survive "close, switch to case B, reopen", not even while B's request is
// still pending, and an opening that was waiting for the mount must not finish in the new case.
import test from "node:test";
import assert from "node:assert/strict";

import { installGlobalDom, tick, until } from "./fixtures/fake-dom.mjs";

const doc = installGlobalDom();
const { api } = await import("../../src/dmslog/web/js/api.js");
const { createDock } = await import("../../src/dmslog/web/js/dock.js");
const { registerAppPanels } = await import("../../src/dmslog/web/js/app-actions.js");
const { createBookmarkStore } = await import("../../src/dmslog/web/js/bookmarks-model.js");
const { createHighlightStore } = await import("../../src/dmslog/web/js/highlight-model.js");

const MARK = "ALPHA-CONTENT";

// ---- a fake server: case A answers with the marker, case B never answers (until released)
let pendingB = [];
const calls = [];
globalThis.fetch = (url, options = {}) => {
  const text = String(url);
  let caseId = new URL(text, "http://x").searchParams.get("case");
  if (options.body) {
    try { caseId = JSON.parse(options.body).case ?? caseId; } catch { /* not JSON */ }
  }
  calls.push([caseId, text]);
  if (caseId === "B") {
    return new Promise((resolve, reject) => {
      pendingB.push(resolve);
      options.signal?.addEventListener?.("abort", () => reject(Object.assign(new Error("aborted"), { name: "AbortError" })));
    });
  }
  const body = answerForA(text);
  return Promise.resolve({
    ok: body.status === undefined, status: body.status ?? 200,
    json: async () => body.data,
    text: async () => JSON.stringify(body.data),
    headers: { get: () => null },
  });
};

// Error answers carry the marker in their message: every panel shows its error text.
const failure = () => ({ status: 500, data: { error: "boom", message: MARK } });
function answerForA(url) {
  return ROUTES.find(([re]) => re.test(url))?.[1]() ?? failure();
}
const ROUTES = [];

function world({ stripReset = null } = {}) {
  const state = { caseId: "A" };
  const root = doc.createElement("section");
  doc.body.appendChild(root);
  const dock = createDock({ root, storage: null, onFocusOut() {}, doc });
  const messages = [];
  const ctx = Object.freeze({
    caseId: () => state.caseId, filter: () => null, userFilter: () => null, selectedRow: () => null, entry: () => null,
    setUserFilter: async () => {}, reveal: async () => {}, dock,
    status: { message: (text) => { messages.push(text); return { dismiss() {} }; } },
  });
  dock.bind(ctx);
  const holders = {};
  const bookmarkStore = createBookmarkStore({ api, getCase: () => state.caseId, onChange: () => {} });
  const highlightStore = createHighlightStore({ api, getCase: () => state.caseId, onChange: () => holders.highlights?.storeChanged() });
  const gates = new Map(); // id -> release function of a held mount
  const holdingDock = {
    register: (def) => {
      const mount = def.mount;
      dock.register({
        ...def,
        mount: async (host, c) => {
          if (gates.has(def.id)) {
            await new Promise((resolve) => { gates.get(def.id).release = resolve; }); // a held mount
          }
          const instance = await mount(host, c);
          if (stripReset === def.id) {
            const { reset, ...without } = instance; // mutation: this panel forgets to implement reset()
            return without;
          }
          return instance;
        },
      });
    },
  };
  registerAppPanels(holdingDock, {
    api, derivedApi: api, highlightStore, bookmarkStore,
    fieldNames: () => [], hasCollections: () => false, sourceName: (id) => String(id),
    showEntry() {}, showEventInGrid() {},
    exportContext: () => ({ caseId: state.caseId, filter: null, recordId: 1, eventId: null }),
    filterTreeOptions: () => ({}), onFilterTree() {}, onHighlightPanel: (p) => { holders.highlights = p; },
    onBookmarksPanel: (p) => { holders.bookmarks = p; }, alerts: () => null, onError() {},
  });
  // What main.js does when the case changes (steps 2 and 5 of onCase).
  const switchTo = (id) => {
    state.caseId = id;
    bookmarkStore.reset();
    dock.caseChanged();
    dock.notify("case");
  };
  return { state, dock, root, switchTo, gates, calls, messages };
}

const hostText = (w, id) => w.root.all().find((c) => c.id === id + "-host")?.textContent ?? "";

// Each panel: how it is opened, what extra the user does to produce content, and the text that shows it.
const PANELS = [
  { id: "compare", args: { a: 1, b: 2 } }, { id: "payloads" }, { id: "sideBySide", args: { entryId: 1 } },
  { id: "sameKey", args: { type: "msgid", value: "v" } }, { id: "distinct" }, { id: "aggregate", after: (host) => host.all().find((n) => n.className.split(" ").includes("agg-run"))?.click() },
  { id: "highlights" }, { id: "bookmarks" }, { id: "operations" }, { id: "dashboard" }, { id: "unknownTypes" }, { id: "config" },
];

// close -> switch to B -> reopen with B pending: returns what of A is still visible, if anything
async function scenario(panel, options) {
  const w = world(options);
  assert.equal(await w.dock.open(panel.id, panel.args), true);
  const host = w.root.all().find((c) => c.id === panel.id + "-host");
  if (panel.after) {
    panel.after(host);
  }
  await until(() => hostText(w, panel.id).includes(MARK), panel.id + " shows case A");
  w.dock.close(panel.id);
  w.switchTo("B");
  await tick();
  const leftHidden = host.hidden || !hostText(w, panel.id).includes(MARK);
  pendingB = [];
  w.dock.open(panel.id, panel.args); // B never answers: do not wait for it
  await tick();
  const leftReopened = !hostText(w, panel.id).includes(MARK);
  return { leftHidden, leftReopened, visible: !host.hidden };
}

// The panel stays open while the case changes: nothing of A may stay on screen (closeOnCase panels close).
async function stayingOpen(panel, options) {
  const w = world(options);
  assert.equal(await w.dock.open(panel.id, panel.args), true);
  const host = w.root.all().find((c) => c.id === panel.id + "-host");
  if (panel.after) {
    panel.after(host);
  }
  await until(() => hostText(w, panel.id).includes(MARK), panel.id + " shows case A");
  w.switchTo("B");
  await tick();
  return host.hidden || !hostText(w, panel.id).includes(MARK);
}

for (const panel of PANELS) {
  test(`${panel.id}: an open panel shows nothing of case A once the case changes`, async () => {
    assert.equal(await stayingOpen(panel), true);
  });

  test(`${panel.id}: content of case A is gone after close, switch to B and reopen (also while B is pending)`, async () => {
    const result = await scenario(panel);
    assert.equal(result.leftHidden, true, "nothing of A is visible after the case change");
    assert.equal(result.visible, true);
    assert.equal(result.leftReopened, true, "not shown while B's request is pending");
  });

  test(`${panel.id}: an opening that was still mounting when the case changed does not finish in the new case`, async () => {
    const w = world();
    w.gates.set(panel.id, {});
    const opening = w.dock.open(panel.id, panel.args); // the mount is held
    await tick();
    const before = calls.length;
    w.switchTo("B");
    w.gates.get(panel.id).release();
    assert.equal(await opening, false);
    await tick();
    const host = w.root.all().find((c) => c.id === panel.id + "-host");
    assert.equal(host.hidden, true);
    assert.equal(w.dock.isOpen(panel.id), false);
    assert.equal(calls.slice(before).filter(([id]) => id === "A").length, 0, "no request for the old case");
    assert.equal(hostText(w, panel.id).includes(MARK), false);
  });
}

// Mutation: without the real reset() of a panel the scenarios must notice. If these stop failing, the scenarios
// above have stopped looking at what they are meant to look at.
for (const id of ["distinct", "aggregate", "highlights", "operations", "dashboard"]) {
  test(`mutation: ${id} without reset() keeps case A's content while open`, async () => {
    assert.equal(await stayingOpen(PANELS.find((p) => p.id === id), { stripReset: id }), false);
  });
}
