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
    this.children.push(child);
    return child;
  }

  append(...nodes) {
    for (const node of nodes) {
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

  focus() { document.activeElement = this; }
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
    host, note, api: fakeApi, getCase: () => "case1",
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
  byClass(host, "col-remove")[1].fire("click");
  assert.equal(calls.removed.length, 0); // first step only asks
  const confirm = byClass(host, "col-confirm")[0];
  assert.equal(confirm.attrs.role, "alertdialog");
  assert.match(confirm.textContent, /C2/);
  assert.match(confirm.textContent, /300 records|200 records/);
  assert.match(confirm.textContent, /bookmarks/);
  byClass(host, "col-cancel")[0].fire("click");
  assert.equal(byClass(host, "col-confirm").length, 0);
  assert.equal(calls.removed.length, 0);
  byClass(host, "col-remove")[1].fire("click");
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
    byClass(host, "col-remove")[0].fire("click");
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
    progressBar: new FakeNode("progress"), progressText: new FakeNode("div"), scopeSelect: new FakeNode("select"),
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
    host, note: new FakeNode("div"), api: fakeApi, getCase: () => caseId,
    onMessage: (t, e) => calls.messages.push([t, e]), onRemoved: async () => { calls.removedCb += 1; },
  });
  await panel.refresh();
  byClass(host, "col-remove")[0].fire("click");
  byClass(host, "col-yes")[0].fire("click"); // DELETE in case A starts
  assert.equal(calls.removed, 1);
  caseId = "B";
  panel.setCase();
  await panel.refresh();
  byClass(host, "col-remove")[0].fire("click");
  byClass(host, "col-yes")[0].fire("click"); // DELETE in case B starts
  assert.equal(calls.removed, 2);
  pending[0].resolve({ sources: 1, records: 1, bookmarks: 0 }); // the old answer arrives
  await tick();
  await tick();
  assert.notEqual(panel.removing, null); // still locked by B's removal
  assert.equal(calls.removedCb, 0);
  assert.equal(calls.messages.some(([t]) => /Removed/.test(t)), false);
  byClass(host, "col-remove")[0].fire("click"); // disabled while B's removal runs
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
  byClass(host, "col-remove")[1].fire("click"); // asks: focus goes to Cancel of C2
  assert.equal(document.activeElement.dataset.act, "cancel");
  assert.equal(document.activeElement.dataset.group, "9");
  await panel.refresh(); // an update during indexing re-renders the list
  assert.equal(document.activeElement.dataset.act, "cancel");
  assert.equal(document.activeElement.dataset.group, "9");
  byClass(host, "col-confirm")[0].fire("keydown", { key: "Escape" }); // Esc still cancels after the refresh
  assert.equal(byClass(host, "col-confirm").length, 0);
  assert.equal(document.activeElement.dataset.act, "remove");
  assert.equal(document.activeElement.dataset.group, "9");
  byClass(host, "col-expand")[0].fire("click");
  assert.equal(document.activeElement.dataset.act, "expand");
  assert.equal(document.activeElement.dataset.group, "4");
  byClass(host, "col-remove")[1].fire("click");
  byClass(host, "col-yes")[0].fire("click");
  await tick();
  await tick();
  assert.equal(document.activeElement.dataset.act, "remove"); // C2 is gone: the next row (C3) gets it
  assert.equal(document.activeElement.dataset.group, "12");
});
