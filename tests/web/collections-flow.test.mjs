// CollectionsPanel on a tiny fake DOM with a fake API, the Collection grid column, and the "add files
// to the open case" list. Data is synthetic.
import test from "node:test";
import assert from "node:assert/strict";

class FakeNode {
  constructor(tag) {
    this.tag = tag;
    this.children = [];
    this.attrs = {};
    this.listeners = {};
    this.dataset = {};
    this.className = "";
    this.hidden = false;
    this.disabled = false;
    this.checked = false;
    this.value = "";
    this._text = "";
    this.style = {};
    this.title = "";
  }

  get textContent() {
    return this._text + this.children.map((c) => c.textContent).join("");
  }

  set textContent(value) {
    this._text = String(value);
    this.children = [];
  }

  appendChild(child) {
    child.parent = this;
    this.children.push(child);
    return child;
  }

  append(...nodes) {
    for (const node of nodes) {
      node.parent = this;
      this.children.push(node);
    }
  }

  setAttribute(name, value) { this.attrs[name] = String(value); }
  removeAttribute(name) { delete this.attrs[name]; }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  querySelector(selector) {
    const pairs = [...selector.matchAll(/\[data-(\w+)="([^"]*)"\]/g)];
    const found = this.find((n) => n !== this && pairs.length > 0 && pairs.every(([, k, v]) => n.dataset[k] === v));
    return found.length > 0 ? found[0] : null;
  }

  // Like a browser: a node that is hidden, or inside a hidden one, cannot take the focus.
  focus() {
    for (let n = this; n; n = n.parent) {
      if (n.hidden) {
        return;
      }
    }
    document.activeElement = this;
  }
  get classList() {
    return { toggle() {}, add() {} };
  }

  fire(type, event = {}) {
    for (const fn of this.listeners[type] || []) {
      fn({ preventDefault() {}, stopPropagation() {}, key: "", ...event });
    }
  }

  find(predicate, out = []) {
    if (predicate(this)) {
      out.push(this);
    }
    for (const child of this.children) {
      child.find(predicate, out);
    }
    return out;
  }
}

globalThis.document = {
  activeElement: null,
  createElement: (tag) => new FakeNode(tag),
  createTextNode: (text) => Object.assign(new FakeNode("#text"), { _text: text }),
};

const { CollectionsPanel } = await import("../../src/dmslog/web/js/collections-panel.js");
const { CaseFiles } = await import("../../src/dmslog/web/js/case-files.js");
const { SourcesPanel } = await import("../../src/dmslog/web/js/sources.js");
const { Grid, COLUMNS } = await import("../../src/dmslog/web/js/grid.js");
const { api, ApiError } = await import("../../src/dmslog/web/js/api.js");

const byClass = (root, cls) => root.find((n) => n.className.split(" ").includes(cls));
const tick = () => new Promise((r) => setTimeout(r, 0));

// The row menus load ui-menu.js on demand; the tests give the panel a stand-in that records what it was given.
const menus = [];
const fakeMenuLoader = () => Promise.resolve({
  createMenu: (opts) => {
    const menu = { opts, opened: 0, where: undefined, open(where) { this.opened += 1; this.where = where; opts.getItems(); }, isOpen() { return false; }, close() {} };
    menus.push(menu);
    return menu;
  },
});

// Opens the "..." menu of row `index` and picks its one item ("Remove C<n>...").
async function removeVia(host, index) {
  byClass(host, "col-menu")[index].fire("click");
  await tick();
  menus.at(-1).opts.getItems()[0].run();
}

function group(id, number, extra = {}) {
  return {
    id, group: id, number, tag: "C" + number, kind: "ers", guid: "0A1B2C3D-0000-1111-2222-333344445555",
    capture: "20260101120000", server: "zone" + number, variants: ["enterprise", "sensitive"], missing: null,
    status: "ready", sources: 3, records: 100 * number, first_ts: 1000, last_ts: 2000,
    members: [{ id, name: "a.zip", variant: "enterprise", sources: 3, records: 100 * number, status: "ready" }],
    ...extra,
  };
}

function setup(lists) {
  const calls = { removed: [], selections: [], lists: [], messages: [], removedCb: 0 };
  let current = lists;
  let failure = null;
  const fakeApi = {
    collections: async () => ({ ordering: current.length > 1 ? "approximate" : "exact", collections: current }),
    removeCollection: async (caseId, g) => {
      if (failure) {
        throw failure;
      }
      calls.removed.push([caseId, g]);
      current = current.filter((c) => c.group !== g);
      return { group: g, sources: 3, records: 200, bookmarks: 1 };
    },
  };
  const host = new FakeNode("section");
  const note = new FakeNode("div");
  const panel = new CollectionsPanel({
    host, note, api: fakeApi, getCase: () => "case1", loadMenu: fakeMenuLoader,
    onSelection: (g) => calls.selections.push(g),
    onList: (list, ordering) => calls.lists.push([list.length, ordering]),
    onRemoved: async () => { calls.removedCb += 1; },
    onMessage: (text, isError) => calls.messages.push([text, isError]),
  });
  return {
    host, note, panel, calls, setFailure: (f) => { failure = f; },
    setList: (l) => { current = l; },
  };
}

test("one collection: the panel lists it, without the order warning or the All button", async () => {
  const { host, note, panel } = setup([group(4, 1)]);
  await panel.refresh();
  assert.equal(host.hidden, false);
  assert.equal(byClass(host, "col-item").length, 1);
  assert.equal(note.hidden, true);
  assert.equal(byClass(host, "col-all")[0].hidden, true);
  assert.match(host.textContent, /C1/);
  assert.match(host.textContent, /300|100 records in 3 sources/);
});

test("several collections: the approximate-order warning shows and the panel says it in plain words", async () => {
  const { host, note, panel, calls } = setup([group(4, 1), group(9, 2, { kind: "folder", members: [{ name: "Log<b>" }] })]);
  await panel.refresh();
  assert.equal(note.hidden, false);
  assert.equal(note.attrs["aria-live"], "polite");
  assert.match(note.textContent, /order across collections is approximate/);
  assert.match(host.textContent, /servers with different clocks/);
  assert.match(host.textContent, /Folder Log<b>/); // text, never markup
  assert.equal(byClass(host, "col-item").length, 2);
  assert.deepEqual(calls.lists.at(-1), [2, "approximate"]);
});

test("checking collections filters, All collections clears, a vanished one is dropped", async () => {
  const { host, panel, calls, setList } = setup([group(4, 1), group(9, 2)]);
  await panel.refresh();
  const boxes = () => host.find((n) => n.tag === "input" && n.type === "checkbox");
  boxes()[1].checked = true;
  boxes()[1].fire("change");
  assert.deepEqual(calls.selections.at(-1), [9]);
  assert.match(byClass(host, "col-status")[0].textContent, /Showing only C2/);
  boxes()[0].checked = true;
  boxes()[0].fire("change");
  assert.deepEqual(calls.selections.at(-1), [4, 9]);
  byClass(host, "col-all")[0].fire("click");
  assert.deepEqual(calls.selections.at(-1), []);
  assert.match(byClass(host, "col-status")[0].textContent, /all collections/);
  boxes()[1].checked = true;
  boxes()[1].fire("change");
  setList([group(4, 1)]);
  const changed = await panel.refresh();
  assert.equal(changed, true);
  assert.deepEqual(calls.selections.at(-1), []);
  assert.deepEqual(panel.selectedGroups(), []);
});

test("Remove takes two steps, names the collection, and reloads through onRemoved", async () => {
  const { host, panel, calls } = setup([group(4, 1), group(9, 2)]);
  await panel.refresh();
  await removeVia(host, 1);
  assert.equal(calls.removed.length, 0); // first step only asks
  const confirm = byClass(host, "col-confirm")[0];
  assert.equal(confirm.attrs.role, "alertdialog");
  assert.match(confirm.textContent, /C2/);
  assert.match(confirm.textContent, /300 records|200 records/);
  assert.match(confirm.textContent, /bookmarks/);
  byClass(host, "col-cancel")[0].fire("click");
  assert.equal(byClass(host, "col-confirm").length, 0);
  assert.equal(calls.removed.length, 0);
  await removeVia(host, 1);
  byClass(host, "col-yes")[0].fire("click");
  await tick();
  await tick();
  assert.deepEqual(calls.removed, [["case1", 9]]);
  assert.match(calls.messages.at(-1)[0], /Removed C2: 3 sources, 200 records and 1 bookmark\./);
  assert.equal(calls.removedCb, 1);
  assert.equal(byClass(host, "col-item").length, 1);
});

test("a removal refused with 409 explains why and changes nothing", async () => {
  const { host, panel, calls, setFailure } = setup([group(4, 1), group(9, 2)]);
  await panel.refresh();
  for (const [code, pattern] of [["job_running", /while the case is indexing/], ["collection_followed", /following this folder/]]) {
    setFailure(Object.assign(new ApiError(409, code, "server text"), {}));
    await removeVia(host, 0);
    byClass(host, "col-yes")[0].fire("click");
    await tick();
    await tick();
    const [text, isError] = calls.messages.at(-1);
    assert.match(text, pattern);
    assert.equal(isError, true);
  }
  assert.equal(calls.removedCb, 0);
  assert.equal(byClass(host, "col-item").length, 2);
});

test("a case change forgets the list and the selection", async () => {
  const { host, panel, calls } = setup([group(4, 1), group(9, 2)]);
  await panel.refresh();
  host.find((n) => n.tag === "input")[0].fire("change");
  panel.setCase();
  assert.equal(host.hidden, true);
  assert.deepEqual(panel.selectedGroups(), []);
  assert.deepEqual(calls.lists.at(-1), [0, "exact"]);
});

// -- Collection column of the grid ---------------------------------------------------------

function gridElement() {
  return {
    style: {}, dataset: {}, children: [], hidden: false, clientHeight: 440, scrollTop: 0, className: "",
    classList: { toggle() {} },
    addEventListener() {}, appendChild(c) { this.children.push(c); return c; },
    setAttribute() {}, removeAttribute() {}, focus() {},
    set textContent(v) { this.children = []; this._t = v; }, get textContent() { return this._t || ""; },
  };
}

test("the grid asks for the collection column only when it is shown, after Source", async () => {
  const requested = [];
  const savedCreate = document.createElement;
  document.createElement = () => gridElement();
  api.count = async () => ({ count: 0, exact: true });
  api.page = async (c, f, cursor, dir, limit, signal, columns) => {
    requested.push(columns);
    return { rows: [], prev: null, next: null };
  };
  const grid = new Grid({
    viewport: gridElement(), rowsLayer: gridElement(), spacer: gridElement(), header: gridElement(),
    onSelect() {}, onStatus() {}, onError(err) { throw err; },
  });
  document.createElement = savedCreate;
  await grid.setView({ caseId: "A", filter: null, range: null });
  assert.deepEqual(requested.at(-1), []);
  assert.deepEqual(grid.columnDefs().map((c) => c.key), COLUMNS.map((c) => c.key));
  await grid.setCollectionColumn(true);
  assert.deepEqual(requested.at(-1), ["collection"]);
  const keys = grid.columnDefs().map((c) => c.key);
  assert.equal(keys[keys.indexOf("source") + 1], "collection");
  const count = requested.length;
  await grid.setCollectionColumn(true); // no change: no reload
  assert.equal(requested.length, count);
  await grid.setCollectionColumn(false, false); // hidden without reloading (a full reload follows)
  assert.equal(requested.length, count);
  assert.equal(grid.columnDefs().some((c) => c.key === "collection"), false);
});

test("a grid row shows C<n> from the tag map and falls back to the group id", () => {
  const cells = { collection: gridElement() };
  const grid = Object.create(Grid.prototype);
  grid.collectionTags = new Map([[9, { tag: "C2", color: 2 }]]);
  grid.fieldColumns = [];
  grid.sourceNames = new Map();
  grid.fillCells({ cells }, { collection: 9 });
  assert.equal(cells.collection.textContent, "C2");
  assert.equal(cells.collection.dataset.tag, "2");
  grid.fillCells({ cells }, { collection: 77 });
  assert.equal(cells.collection.textContent, "77");
  assert.equal(cells.collection.dataset.tag, undefined);
});

// -- add files to the open case ------------------------------------------------------------

const GUID = "0A1B2C3D-0000-1111-2222-333344445555";
const ENT = `/data/ERS_CL_${GUID}_20260101120000_Enterprise.zip`;
const OTHER = "/data/ERS_CL_0A1B2C3D-0000-1111-2222-999999999999_20260202120000_Enterprise.zip";

test("add mode: its own title and button, no case creation, per-file results", async () => {
  const host = new FakeNode("div");
  const messages = [];
  let added = 0;
  const files = new CaseFiles({
    host, nameInput: null, fs: {}, storage: null, mode: "add", onAdd: () => { added += 1; },
    onMessage: (t, e) => messages.push([t, e]),
  });
  assert.match(host.textContent, /Add ERS files to this case/);
  const button = byClass(host, "cf-create")[0];
  assert.equal(button.type, "button"); // inside the open form: never submits it
  assert.equal(button.disabled, true);
  files.state.add([ENT, OTHER]);
  assert.match(button.textContent, /Add 2 files to case/);
  button.fire("click");
  assert.equal(added, 1);
  const opened = [];
  const result = await files.submitAdd({
    openPath: async (path) => {
      opened.push(path);
      return path === OTHER
        ? { status: "refused", code: "duplicate_collection", message: "already here" }
        : { status: "ok" };
    },
  });
  assert.deepEqual(opened, [ENT, OTHER]);
  assert.deepEqual([result.state, result.ok, result.total], ["partial", 1, 2]);
  assert.match(messages.at(-1)[0], /1 of 2 files added/);
  assert.equal(files.state.items.length, 2); // kept so the user can fix and add again
  const second = await files.submitAdd({ openPath: async () => ({ status: "ok" }) });
  assert.equal(second.state, "ok");
  assert.equal(files.state.items.length, 0);
  assert.match(messages.at(-1)[0], /2 files added to the case/);
});

test("add mode with nothing chosen asks for a file", async () => {
  const messages = [];
  const files = new CaseFiles({
    host: new FakeNode("div"), nameInput: null, fs: {}, storage: null, mode: "add",
    onMessage: (t, e) => messages.push([t, e]),
  });
  assert.equal(await files.submitAdd({ openPath: async () => ({ status: "ok" }) }), null);
  assert.deepEqual(messages.at(-1), ["Choose at least 1 file to add.", true]);
});

test("SourcesPanel.addFilesToCase opens every file in the open case and unblocks afterwards", async () => {
  const refs = {
    caseSelect: new FakeNode("select"), newCaseForm: new FakeNode("form"), openForm: new FakeNode("form"),
    sourceList: new FakeNode("ul"), progressBar: new FakeNode("progress"), progressText: new FakeNode("div"), scopeSelect: new FakeNode("select"),
  };
  const messages = [];
  const panel = new SourcesPanel(refs, { onMessage: (t, e) => messages.push([t, e]), onSources() {}, onDone() {} });
  const noFiles = new CaseFiles({ host: new FakeNode("div"), nameInput: null, fs: {}, storage: null, mode: "add", onMessage() {} });
  panel.addFiles = noFiles;
  noFiles.state.add([ENT]);
  await panel.addFilesToCase();
  assert.deepEqual(messages.at(-1), ["Create or select a case first.", true]); // no case open
  panel.caseId = "case1";
  panel.runCollection = async (path) => ({ status: "ok", path });
  await panel.addFilesToCase();
  assert.equal(panel.queueBusy, false);
  assert.equal(noFiles.state.items.length, 0);
});

// -- review fixes ----------------------------------------------------------------------------

const { collectionParam, scopeApi } = await import("../../src/dmslog/web/js/collections-model.js");

test("add: results belong to the case; trying again in another case opens every file", async () => {
  const files = new CaseFiles({ host: new FakeNode("div"), nameInput: null, fs: {}, storage: null, mode: "add", onMessage() {} });
  files.state.add([ENT, OTHER]);
  const opened = [];
  const open = (refuse) => async (path) => {
    opened.push(path);
    return path === OTHER && refuse ? { status: "failed", message: "boom" } : { status: "ok" };
  };
  const first = await files.submitAdd({ openPath: open(true) }, "A");
  assert.equal(first.state, "partial");
  assert.deepEqual(opened, [ENT, OTHER]);
  const second = await files.submitAdd({ openPath: open(false) }, "B"); // another case: both go in again
  assert.deepEqual(opened.slice(2), [ENT, OTHER]);
  assert.deepEqual([second.state, second.ok, second.total], ["ok", 2, 2]);
  // Same case again after a partial result keeps the ones that already went in.
  files.state.add([ENT, OTHER]);
  await files.submitAdd({ openPath: open(true) }, "C");
  const before = opened.length;
  await files.submitAdd({ openPath: open(false) }, "C");
  assert.deepEqual(opened.slice(before), [OTHER]);
  // Selecting a case resets the results of the list.
  files.state.resetResults();
  assert.equal(files.state.items.every((i) => i.status === "pending"), true);
});

test("panels fed by events carry the chosen collection (one at a time)", async () => {
  const seen = [];
  const base = {
    events: async (c, p) => seen.push(["events", p]),
    eventStats: async (c, p) => seen.push(["eventStats", p]),
    eventHistogram: async (c, p) => seen.push(["eventHistogram", p]),
    statsMethods: async (c, p) => seen.push(["statsMethods", p]),
    statsSeries: async (c, p) => seen.push(["statsSeries", p]),
    event: async () => null,
  };
  let groups = [];
  const scoped = scopeApi(base, () => groups);
  await scoped.events("c", { kind: "gap" });
  assert.deepEqual(seen.pop(), ["events", { kind: "gap" }]);
  groups = [9];
  for (const name of ["events", "eventStats", "eventHistogram", "statsMethods", "statsSeries"]) {
    await scoped[name]("c", { kind: "operation" });
    assert.deepEqual(seen.pop(), [name, { kind: "operation", collection: 9 }]);
  }
  await scoped.statsMethods("c", undefined);
  assert.deepEqual(seen.pop(), ["statsMethods", { collection: 9 }]);
  groups = [4, 9]; // the routes take one group: several chosen means no narrowing
  assert.deepEqual(collectionParam(groups, { a: 1 }), { a: 1 });
  assert.equal(scoped.event, base.event);
});

test("an old removal answer does not free the removal lock of the new case", async () => {
  const pending = [];
  const list = [group(4, 1), group(9, 2)];
  const calls = { removed: 0, messages: [], removedCb: 0 };
  let caseId = "A";
  const fakeApi = {
    collections: async () => ({ ordering: "approximate", collections: list }),
    removeCollection: () => new Promise((resolve, reject) => {
      calls.removed += 1;
      pending.push({ resolve, reject });
    }),
  };
  const host = new FakeNode("section");
  const panel = new CollectionsPanel({
    host, note: new FakeNode("div"), api: fakeApi, getCase: () => caseId, loadMenu: fakeMenuLoader,
    onMessage: (t, e) => calls.messages.push([t, e]), onRemoved: async () => { calls.removedCb += 1; },
  });
  await panel.refresh();
  await removeVia(host, 0);
  byClass(host, "col-yes")[0].fire("click"); // DELETE in case A starts
  assert.equal(calls.removed, 1);
  caseId = "B";
  panel.setCase();
  await panel.refresh();
  await removeVia(host, 0);
  byClass(host, "col-yes")[0].fire("click"); // DELETE in case B starts
  assert.equal(calls.removed, 2);
  pending[0].resolve({ sources: 1, records: 1, bookmarks: 0 }); // the old answer arrives
  await tick();
  await tick();
  assert.notEqual(panel.removing, null); // still locked by B's removal
  assert.equal(calls.removedCb, 0);
  assert.equal(calls.messages.some(([t]) => /Removed/.test(t)), false);
  await removeVia(host, 0); // disabled while B's removal runs
  assert.equal(byClass(host, "col-confirm").length, 0);
  pending[1].resolve({ sources: 1, records: 1, bookmarks: 0 });
  await tick();
  await tick();
  assert.equal(panel.removing, null);
  assert.equal(calls.removedCb, 1);
});

test("a refresh keeps the focus on the same control; removal moves it to the next row", async () => {
  const { host, panel } = setup([group(4, 1), group(9, 2), group(12, 3)]);
  await panel.refresh();
  await removeVia(host, 1); // asks: focus goes to Cancel of C2
  assert.equal(document.activeElement.dataset.act, "cancel");
  assert.equal(document.activeElement.dataset.group, "9");
  await panel.refresh(); // an update during indexing re-renders the list
  assert.equal(document.activeElement.dataset.act, "cancel");
  assert.equal(document.activeElement.dataset.group, "9");
  byClass(host, "col-confirm")[0].fire("keydown", { key: "Escape" }); // Esc still cancels after the refresh
  assert.equal(byClass(host, "col-confirm").length, 0);
  assert.equal(document.activeElement.dataset.act, "menu");
  assert.equal(document.activeElement.dataset.group, "9");
  byClass(host, "col-expand")[0].fire("click");
  assert.equal(document.activeElement.dataset.act, "expand");
  assert.equal(document.activeElement.dataset.group, "4");
  await removeVia(host, 1);
  byClass(host, "col-yes")[0].fire("click");
  await tick();
  await tick();
  assert.equal(document.activeElement.dataset.act, "menu"); // C2 is gone: the next row (C3) gets it
  assert.equal(document.activeElement.dataset.group, "12");
});

// -- UI-21: compact rows, folding list, unreadable warning, row menu ---------------------------------------

// Microtasks only: the stand-in menu loader and the fake api answer with resolved promises.
const flush = async () => { for (let i = 0; i < 10; i += 1) await Promise.resolve(); };

const unreadableSource = (id, g) => ({ id, collection: g, status: "error", error: "ZIP entry is truncated" });

test("UI-21: a row is one line (tag, identity, count); GUID, window and members open with +", async () => {
  const { host, panel } = setup([group(4, 1), group(9, 2)]);
  await panel.refresh();
  const row = byClass(host, "col-item")[0];
  assert.match(row.textContent, /C1/);
  assert.match(row.textContent, /ERS 2026-01-01 12:00:00 \(zone1\)/);
  assert.equal(byClass(row, "col-guid").length, 0);
  assert.equal(byClass(row, "col-members").length, 0);
  assert.equal(byClass(row, "col-counts")[0].textContent, "100 records in 3 sources");
  byClass(row, "col-expand")[0].fire("click");
  const opened = byClass(host, "col-item")[0];
  assert.equal(byClass(opened, "col-guid").length, 1);
  assert.equal(byClass(opened, "col-members").length, 1);
  assert.match(byClass(opened, "col-members")[0].textContent, /a\.zip/);
  assert.equal(byClass(host, "col-remove").length, 0); // removal lives in the row menu now
});

test("UI-21: the list is open with several collections, folded with one, and the user's choice wins", async () => {
  const { host, panel, setList } = setup([group(4, 1)]);
  await panel.refresh();
  const list = () => byClass(host, "col-list")[0];
  const fold = () => byClass(host, "col-fold")[0];
  assert.equal(list().hidden, true);
  assert.equal(fold().attrs["aria-expanded"], "false");
  assert.equal(fold().textContent, "Collections (1)");
  assert.equal(fold().attrs["aria-controls"], list().id);
  setList([group(4, 1), group(9, 2)]);
  await panel.refresh();
  assert.equal(list().hidden, false);
  assert.equal(fold().attrs["aria-expanded"], "true");
  assert.equal(fold().textContent, "Collections (2)");
  fold().fire("click"); // the user folds it
  assert.equal(list().hidden, true);
  assert.equal(document.activeElement, fold()); // the focus stays on the control that was used
  await panel.refresh();
  assert.equal(list().hidden, true); // a reload does not undo the choice
  fold().fire("click");
  assert.equal(list().hidden, false);
  fold().fire("click");
  panel.setCase(); // another case: back to the default
  setList([group(4, 1), group(9, 2)]);
  await panel.refresh();
  assert.equal(list().hidden, false);
});

test("UI-21: unreadable sources: '1 file unreadable' in the header and on the row of their collection", async () => {
  const { host, panel } = setup([group(4, 1), group(9, 2)]);
  await panel.refresh();
  const header = () => byClass(host, "col-unreadable")[0];
  assert.equal(header().hidden, true);
  panel.setSources([unreadableSource(1, 9), { id: 2, collection: 4, status: "ok" }]);
  assert.equal(header().hidden, false);
  assert.equal(header().textContent, "1 file unreadable");
  const rows = byClass(host, "col-item");
  assert.doesNotMatch(rows[0].textContent, /unreadable/);
  assert.match(rows[1].textContent, /1 file unreadable/);
  panel.setSources([unreadableSource(1, 9), unreadableSource(3, 9), unreadableSource(4, 4)]);
  assert.equal(header().textContent, "3 files unreadable");
  panel.setSources([]);
  assert.equal(header().hidden, true);
  assert.doesNotMatch(host.textContent, /unreadable/);
});

test("UI-21: the sources arrive before the collections of a new case and are counted once the list is there", async () => {
  const { host, panel } = setup([group(4, 1)]);
  panel.setSources([unreadableSource(1, 4)]); // main.js calls this before refresh()
  await panel.refresh();
  assert.equal(byClass(host, "col-unreadable")[0].textContent, "1 file unreadable");
  panel.setCase(); // another case: nothing of the old one stays
  assert.equal(byClass(host, "col-unreadable")[0].hidden, true);
});

test("UI-21: the row menu loads once, offers 'Remove C2…', and Remove still takes two steps with the focus on Cancel", async () => {
  menus.length = 0;
  const { host, panel, calls } = setup([group(4, 1), group(9, 2)]);
  await panel.refresh();
  const trigger = byClass(host, "col-menu")[1];
  assert.equal(trigger.attrs["aria-label"], "Actions for C2");
  trigger.fire("click", { detail: 0 });
  trigger.fire("click", { detail: 0 }); // second click before the load ends: the menu handles clicks once built
  await flush();
  assert.equal(menus.length, 1);
  assert.equal(menus[0].where, "first");
  assert.equal(menus[0].opts.trigger, trigger);
  const [item] = menus[0].opts.getItems();
  assert.deepEqual([item.id, item.label, item.disabled], ["col-remove", "Remove C2…", false]);
  item.run();
  assert.equal(calls.removed.length, 0); // the menu only asks
  assert.equal(document.activeElement.dataset.act, "cancel");
  assert.equal(document.activeElement.dataset.group, "9");
  byClass(host, "col-cancel")[0].fire("click");
  assert.equal(document.activeElement.dataset.act, "menu"); // the focus returns to the row's menu button
  assert.equal(document.activeElement.dataset.group, "9");
});

test("UI-21: a pointer click opens the menu without moving the focus; while a removal runs the item is disabled", async () => {
  menus.length = 0;
  const { host, panel } = setup([group(4, 1), group(9, 2)]);
  await panel.refresh();
  byClass(host, "col-menu")[0].fire("click", { detail: 1 });
  await flush();
  assert.equal(menus[0].where, null);
  panel.removing = 4;
  assert.equal(menus[0].opts.getItems()[0].disabled, true);
  panel.removing = null;
});

test("UI-21: a menu that fails to load is reported and tried again on the next click", async () => {
  const messages = [];
  let loads = 0;
  const host = new FakeNode("section");
  const panel = new CollectionsPanel({
    host, note: new FakeNode("div"), api: { collections: async () => ({ ordering: "exact", collections: [group(4, 1)] }) },
    getCase: () => "case1", onMessage: (t, e) => messages.push([t, e]),
    loadMenu: () => { loads += 1; return loads === 1 ? Promise.reject(new Error("no module")) : fakeMenuLoader(); },
  });
  await panel.refresh();
  const trigger = byClass(host, "col-menu")[0];
  trigger.fire("click");
  await flush();
  assert.deepEqual(messages.at(-1), ["no module", true]);
  menus.length = 0;
  trigger.fire("click");
  await flush();
  assert.equal(menus.length, 1);
});

test("UI-21: an identical reload draws nothing again; a changed list closes an open row menu", async () => {
  menus.length = 0;
  const { host, panel, setList } = setup([group(4, 1), group(9, 2)]);
  await panel.refresh();
  byClass(host, "col-menu")[0].fire("click");
  await flush();
  let closed = 0;
  menus[0].isOpen = () => true;
  menus[0].close = () => { closed += 1; };
  const rows = byClass(host, "col-item");
  await panel.refresh(); // same data (as during an indexing)
  assert.deepEqual(byClass(host, "col-item"), rows);
  assert.equal(closed, 0);
  setList([group(4, 1), group(9, 2, { records: 999 })]);
  await panel.refresh();
  assert.equal(closed, 1);
  assert.notEqual(byClass(host, "col-item")[0], rows[0]);
});

test("UI-21: a reload that drops the collection awaiting confirmation cancels the confirmation", async () => {
  const { host, panel, setList } = setup([group(4, 1), group(9, 2)]);
  await panel.refresh();
  panel.askRemove(9);
  assert.equal(byClass(host, "col-confirm").length, 1);
  setList([group(4, 1)]);
  await panel.refresh();
  assert.equal(byClass(host, "col-confirm").length, 0);
  assert.equal(panel.confirming, null);
});

// -- review fixes: focus never lands on a hidden node, pending menus, open menu while the list reloads ----------------

const visibleNode = (node) => {
  for (let n = node; n; n = n.parent) {
    if (n.hidden) {
      return false;
    }
  }
  return true;
};

test("UI-21 review: removing one of two collections folds the list and the focus goes to the fold button, visibly", async () => {
  const { host, panel } = setup([group(4, 1), group(9, 2)]);
  await panel.refresh();
  await removeVia(host, 1);
  byClass(host, "col-yes")[0].fire("click");
  await flush();
  await flush();
  assert.equal(byClass(host, "col-list")[0].hidden, true); // one collection left: the list folds by itself
  assert.equal(document.activeElement, byClass(host, "col-fold")[0]);
  assert.equal(visibleNode(document.activeElement), true);
});

test("UI-21 review: reloading the list while an item of a row menu has the focus gives the focus to the new menu button", async () => {
  menus.length = 0;
  const { host, panel, setList } = setup([group(4, 1), group(9, 2)]);
  await panel.refresh();
  byClass(host, "col-menu")[1].fire("click", { detail: 0 });
  await flush();
  const item = new FakeNode("div"); // an item of the ui-menu, outside the list and without data-act
  document.activeElement = item;
  menus[0].isOpen = () => true;
  menus[0].close = () => {};
  setList([group(4, 1), group(9, 2, { records: 5 })]);
  await panel.refresh();
  const trigger = byClass(host, "col-menu")[1];
  assert.equal(document.activeElement, trigger);
  assert.equal(visibleNode(trigger), true);
});

test("UI-21 review: a menu that finishes loading after its row was replaced or removed is not opened", async () => {
  menus.length = 0;
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  const host = new FakeNode("section");
  let list = [group(4, 1), group(9, 2)];
  const panel = new CollectionsPanel({
    host, note: new FakeNode("div"), api: { collections: async () => ({ ordering: "approximate", collections: list }) },
    getCase: () => "case1", loadMenu: () => gate.then(() => fakeMenuLoader()),
  });
  await panel.refresh();
  byClass(host, "col-menu")[0].fire("click", { detail: 0 });
  list = [group(9, 2)]; // C1 goes away
  await panel.refresh();
  release();
  await flush();
  assert.equal(menus.length, 0);
  // the same when the node is no longer in the page
  byClass(host, "col-menu")[0].fire("click", { detail: 0 });
  byClass(host, "col-menu")[0].isConnected = false;
  await flush();
  assert.equal(menus.length, 0);
});

test("UI-21 review: a wanted focus on a row of a folded list goes to the fold button (the node it found is hidden)", async () => {
  const { host, panel } = setup([group(4, 1)]); // one collection: folded
  await panel.refresh();
  assert.equal(byClass(host, "col-list")[0].hidden, true);
  document.activeElement = null; // what a browser has after the focused node is replaced
  panel.wanted = { group: 4, act: "menu" };
  panel.render();
  assert.equal(document.activeElement, byClass(host, "col-fold")[0]);
});

test("UI-21 review: in a folded list every row control that was focused hands the focus to the fold button", async () => {
  for (const act of ["pick", "expand", "menu", "remove", "yes", "cancel"]) {
    const { host, panel } = setup([group(4, 1)]); // one collection: the list is folded
    await panel.refresh();
    document.activeElement = null;
    panel.wanted = { group: 4, act };
    panel.render();
    assert.equal(document.activeElement, byClass(host, "col-fold")[0], act);
  }
});

test("UI-21 review: a refresh that folds the list while the checkbox has the focus moves it to the fold button", async () => {
  const { host, panel, setList } = setup([group(4, 1), group(9, 2)]);
  await panel.refresh();
  const box = host.find((n) => n.tag === "input" && n.dataset.act === "pick")[1];
  box.focus();
  setList([group(4, 1)]);
  await panel.refresh();
  assert.equal(document.activeElement, byClass(host, "col-fold")[0]);
});
