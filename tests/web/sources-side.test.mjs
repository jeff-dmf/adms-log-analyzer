// UI-21: the sources list of the side column (rows reconciled by id, keyboard, unreadable marker, texts of the meta line
// and of the progress), the fixed case header and its layout. Data is synthetic. No wall-clock waits.
import test from "node:test";
import assert from "node:assert/strict";

import { installGlobalDom, tick } from "./fixtures/fake-dom.mjs";

const doc = installGlobalDom();
const model = await import("../../src/dmslog/web/js/sources.js");
const { SourcesPanel } = model;
const { api } = await import("../../src/dmslog/web/js/api.js");
const { S } = await import("../../src/dmslog/web/js/strings.js");

const make = (tag, init = {}) => Object.assign(doc.createElement(tag), init);

function src(id, extra = {}) {
  return { id, name: "adapter" + id + ".log", family: "dms-core", pid: 1000 + id, records: 100 * id, status: "ok", error: null, sensitive: false, collection: 1, ...extra };
}

function setup({ header = false } = {}) {
  const refs = {
    caseSelect: make("select"), newCaseForm: make("form"), newCaseName: make("input"), openForm: make("form"),
    pathInput: make("input"), scopeSelect: make("select", { value: "default" }), sourceList: make("ul"),
    filterInput: make("input"),
  };
  if (header) {
    Object.assign(refs, {
      caseHeader: make("div"), caseHeaderName: make("span"), caseRow: make("div"), caseAddFiles: make("button"),
      caseMenu: make("button"), addFilesHost: make("div"),
    });
    refs.addFilesHost.appendChild(make("button"));
    for (const node of Object.values(refs)) {
      doc.body.appendChild(node);
    }
  } else {
    doc.body.appendChild(refs.sourceList);
    doc.body.appendChild(refs.filterInput);
  }
  const calls = { selection: [], messages: [], progress: [], cases: [] };
  const panel = new SourcesPanel(refs, {
    onCase: (id) => calls.cases.push(id), onSources() {}, onSelection: (ids) => calls.selection.push(ids), onDone() {},
    onMessage: (t, e) => calls.messages.push([t, e]), onProgress: (p) => calls.progress.push(p),
  });
  return { refs, panel, calls };
}

const rowsOf = (refs) => refs.sourceList.children;
const boxOf = (li) => li.children[0].children[0];
const show = (panel, list) => { panel.sources = list; panel.renderSources(); };
// A click on a checkbox in a real page flips it and fires change.
function wireClick(box) {
  box.addEventListener("click", () => { box.checked = !box.checked; box.dispatch("change"); });
}

// ---- pure helpers --------------------------------------------------------------------------------------------

test("meta line: a missing family or pid leaves its segment out; pid 0 is a real pid", () => {
  assert.equal(model.sourceMeta({ family: "dms-core", pid: 4120, records: 1200 }), "dms-core - pid 4120 - 1,200 records");
  assert.equal(model.sourceMeta({ family: "dms-core", pid: null, records: 1 }), "dms-core - 1 record");
  assert.equal(model.sourceMeta({ family: "", pid: 77, records: 2 }), "pid 77 - 2 records");
  assert.equal(model.sourceMeta({ family: null, pid: undefined, records: 5 }), "5 records");
  assert.equal(model.sourceMeta({ family: "x", pid: 0, records: 5 }), "x - pid 0 - 5 records");
  assert.equal(model.sourceMeta({ family: "x", pid: Number.NaN, records: 5 }), "x - 5 records");
  assert.ok(!/(^| )- -|pid -|- $/.test(model.sourceMeta({ family: null, pid: null, records: 0 })));
});

test("measures that are not counts become 0 and never print NaN", () => {
  for (const bad of [null, undefined, Number.NaN, -3, "x", Infinity]) {
    assert.equal(model.safeCount(bad), 0);
    assert.equal(model.sourceMeta({ family: "f", pid: 1, records: bad }), "f - pid 1 - 0 records");
  }
  assert.equal(model.safeCount("1200"), 1200);
  assert.equal(model.safeCount(2.9), 2);
});

test("progress text: with a source it names it, without one it never starts with a colon", () => {
  assert.equal(model.indexingText({ source: "core.log", records: 1200 }), "Indexing core.log: 1,200 records");
  assert.equal(model.indexingText({ source: "core.log", records: 1 }), "Indexing core.log: 1 record");
  for (const source of [null, undefined, "", "   "]) {
    const text = model.indexingText({ source, records: 235230 });
    assert.equal(text, "Indexing: 235,230 records");
    assert.ok(!text.startsWith(":") && !/Indexing :/.test(text));
  }
  assert.equal(model.indexingText({ source: "a", records: Number.NaN }), "Indexing a: 0 records");
  assert.equal(model.indexingText(null), "Indexing: 0 records");
  assert.equal(model.doneText(235230), "Indexed 235,230 records.");
  assert.equal(model.doneText(1), "Indexed 1 record.");
  assert.equal(model.doneText(undefined), "Indexed 0 records.");
});

test("unreadable: counts per collection and the header line in both plural forms", () => {
  const list = [src(1, { status: "error", collection: 1 }), src(2, { collection: 1 }), src(3, { status: "error", collection: 2 })];
  assert.equal(model.unreadableCount(list), 2);
  assert.equal(model.unreadableCount(list, 2), 1);
  assert.equal(model.unreadableCount(list, 9), 0);
  assert.equal(model.unreadableCount(null), 0);
  assert.equal(model.unreadableLine(1), "1 file unreadable");
  assert.equal(model.unreadableLine(3), "3 files unreadable");
  assert.equal(model.unreadableLine(0), "");
  assert.equal(model.unreadableLine(Number.NaN), "");
  assert.match(model.unreadableTitle({ error: "ZIP entry is truncated" }), /could not be read: ZIP entry is truncated/);
  assert.match(model.unreadableTitle({ error: null }), /no reason was reported/);
});

test("diffIds, neighborAfterRemoval and listKeyTarget", () => {
  assert.deepEqual(model.diffIds([1, 2, 3], [1, 2, 3]), { removed: [], added: [], sameOrder: true, identical: true });
  const d = model.diffIds([1, 2, 3], [2, 3, 4]);
  assert.deepEqual([d.removed, d.added, d.identical], [[1], [4], false]);
  assert.equal(model.diffIds([1, 2], [2, 1]).sameOrder, false);
  assert.equal(model.neighborAfterRemoval([1, 2, 3], 2, new Set([1, 3])), 3);
  assert.equal(model.neighborAfterRemoval([1, 2, 3], 3, new Set([1, 2])), 2);
  assert.equal(model.neighborAfterRemoval([1], 1, new Set()), null);
  assert.equal(model.neighborAfterRemoval([1], 9, new Set()), null);
  assert.equal(model.listKeyTarget([5, 6, 7], 6, "ArrowDown"), 7);
  assert.equal(model.listKeyTarget([5, 6, 7], 7, "ArrowDown"), 7);
  assert.equal(model.listKeyTarget([5, 6, 7], 5, "ArrowUp"), 5);
  assert.equal(model.listKeyTarget([5, 6, 7], 6, "Home"), 5);
  assert.equal(model.listKeyTarget([5, 6, 7], 6, "End"), 7);
  assert.equal(model.listKeyTarget([5, 6, 7], 6, "a"), null);
  assert.equal(model.listKeyTarget([], 6, "Home"), null);
});

// ---- the list ------------------------------------------------------------------------------------------------

test("the focus stays on the same checkbox after three updates", () => {
  const { refs, panel } = setup();
  show(panel, [src(1), src(2), src(3)]);
  const box = boxOf(rowsOf(refs)[1]);
  box.focus();
  show(panel, [src(1), src(2, { records: 250 }), src(3)]);
  show(panel, [src(1), src(2, { records: 260 }), src(3), src(4)]);
  show(panel, [src(1, { records: 5 }), src(2, { records: 270 }), src(3)]);
  assert.equal(doc.activeElement, box);
  assert.equal(boxOf(rowsOf(refs)[1]), box); // the very same node, not an equal one
});

test("the same set of sources only updates texts: no node is created or replaced", () => {
  const { refs, panel } = setup();
  show(panel, [src(1), src(2)]);
  const before = [...rowsOf(refs)];
  const create = doc.createElement;
  let created = 0;
  doc.createElement = (tag) => { created += 1; return create(tag); };
  try {
    show(panel, [src(1, { records: 7 }), src(2, { records: 9 })]);
  } finally {
    doc.createElement = create;
  }
  assert.equal(created, 0);
  assert.deepEqual([...rowsOf(refs)], before);
  assert.equal(rowsOf(refs)[0].textContent.includes("7"), true);
});

test("a changed set inserts and removes only the rows concerned and keeps the other checkboxes", () => {
  const { refs, panel } = setup();
  show(panel, [src(1), src(2), src(3)]);
  panel.selected.add(3);
  const [one, two, three] = [...rowsOf(refs)];
  show(panel, [src(1), src(3), src(4), src(0)].sort((a, b) => (a.id === 0 ? -1 : b.id === 0 ? 1 : a.id - b.id)));
  const now = [...rowsOf(refs)];
  assert.equal(now.length, 4);
  assert.equal(now[1], one);
  assert.equal(now[2], three);
  assert.ok(!now.includes(two));
  assert.equal(boxOf(three).checked, true); // selection kept
  assert.deepEqual(panel.order, [0, 1, 3, 4]);
});

test("the focused row is removed: the focus goes to the next row, else the previous, else the filter box", () => {
  const { refs, panel } = setup();
  show(panel, [src(1), src(2), src(3)]);
  boxOf(rowsOf(refs)[1]).focus();
  show(panel, [src(1), src(3)]);
  assert.equal(doc.activeElement, boxOf(rowsOf(refs)[1]));
  assert.equal(doc.activeElement.dataset.sourceId, "3");
  boxOf(rowsOf(refs)[1]).focus();
  show(panel, [src(1)]);
  assert.equal(doc.activeElement.dataset.sourceId, "1");
  boxOf(rowsOf(refs)[0]).focus();
  show(panel, []);
  assert.equal(doc.activeElement, refs.filterInput);
});

test("a moved row keeps the focus", () => {
  const { refs, panel } = setup();
  show(panel, [src(1), src(2), src(3)]);
  const box = boxOf(rowsOf(refs)[2]);
  box.focus();
  show(panel, [src(3), src(1), src(2)]);
  assert.deepEqual(panel.order, [3, 1, 2]);
  assert.equal(boxOf(rowsOf(refs)[0]), box);
  assert.equal(doc.activeElement, box);
});

test("a case change never reuses the rows of the old case (source ids repeat between cases)", async () => {
  const { refs, panel } = setup();
  api.sources = async (caseId) => ({ sources: [src(1, { name: caseId + ".log" })] });
  await panel.selectCase("A");
  const rowA = rowsOf(refs)[0];
  boxOf(rowA).focus();
  await panel.selectCase("B");
  const rowB = rowsOf(refs)[0];
  assert.notEqual(rowB, rowA);
  assert.match(rowB.textContent, /B\.log/);
  assert.equal(panel.tabStop, null); // the old tab stop belonged to the old case
  assert.equal(rowsOf(refs).length, 1);
});

test("one listener per kind on the list, none on the rows; a change updates the selection", () => {
  const { refs, panel, calls } = setup();
  show(panel, [src(1), src(2)]);
  assert.deepEqual(Object.keys(refs.sourceList.listeners).sort(), ["change", "focusin", "keydown"]);
  for (const li of rowsOf(refs)) {
    for (const node of li.all()) {
      assert.deepEqual(Object.keys(node.listeners), []);
    }
  }
  const box = boxOf(rowsOf(refs)[1]);
  box.checked = true;
  box.dispatch("change");
  assert.deepEqual(calls.selection.at(-1), [2]);
  box.checked = false;
  box.dispatch("change");
  assert.deepEqual(calls.selection.at(-1), []);
});

test("keyboard: one tab stop, arrows and Home/End move, Enter toggles, Escape goes to the filter box", () => {
  const { refs, panel } = setup();
  show(panel, [src(1), src(2), src(3)]);
  const boxes = [...rowsOf(refs)].map(boxOf);
  boxes.forEach(wireClick);
  const stops = () => boxes.filter((b) => b.getAttribute("tabindex") === "0");
  assert.deepEqual(stops(), [boxes[0]]);
  boxes[0].focus();
  boxes[0].dispatch("keydown", { key: "ArrowDown" });
  assert.equal(doc.activeElement, boxes[1]);
  assert.deepEqual(stops(), [boxes[1]]); // roving: the stop follows the focus
  boxes[1].dispatch("keydown", { key: "End" });
  assert.equal(doc.activeElement, boxes[2]);
  boxes[2].dispatch("keydown", { key: "Home" });
  assert.equal(doc.activeElement, boxes[0]);
  boxes[0].dispatch("keydown", { key: "ArrowUp" });
  assert.equal(doc.activeElement, boxes[0]);
  const enter = boxes[0].dispatch("keydown", { key: "Enter" });
  assert.equal(enter.prevented, true);
  assert.equal(boxes[0].checked, true);
  boxes[0].dispatch("keydown", { key: "Escape" });
  assert.equal(doc.activeElement, refs.filterInput);
  panel.focusList();
  assert.equal(doc.activeElement, boxes[0]); // back to the tab stop
  assert.equal(stops().length, 1);
});

test("the tab stop survives the removal of its row", () => {
  const { refs, panel } = setup();
  show(panel, [src(1), src(2)]);
  boxOf(rowsOf(refs)[1]).focus();
  show(panel, [src(1)]);
  const boxes = [...rowsOf(refs)].map(boxOf);
  assert.equal(boxes.filter((b) => b.getAttribute("tabindex") === "0").length, 1);
});

test("an unreadable source gets a ! marker before the name with the reason in its title, and loses it when readable", () => {
  const { refs, panel } = setup();
  show(panel, [src(1, { status: "error", error: "ZIP entry is truncated" }), src(2)]);
  const label = rowsOf(refs)[0].children[0];
  const mark = label.children[1];
  assert.equal(mark.className, "source-unreadable");
  assert.equal(mark.textContent, "!");
  assert.equal(mark.getAttribute("aria-label"), "Unreadable");
  assert.match(mark.title, /ZIP entry is truncated/);
  assert.equal(label.children[2].className, "source-text"); // the marker comes before the name
  assert.equal(rowsOf(refs)[0].className, "is-unreadable");
  assert.equal(rowsOf(refs)[1].children[0].children.filter((c) => c.className === "source-unreadable").length, 0);
  show(panel, [src(1), src(2)]);
  assert.equal(rowsOf(refs)[0].children[0].children.filter((c) => c.className === "source-unreadable").length, 0);
  assert.equal(rowsOf(refs)[0].className, "");
});

test("a reason that looks like markup stays text", () => {
  const { refs, panel } = setup();
  show(panel, [src(1, { status: "error", error: "<img src=x onerror=alert(1)>", name: "<b>x.log" })]);
  const li = rowsOf(refs)[0];
  assert.equal(li.all().some((n) => n.tagName === "IMG" || n.tagName === "B" || n.tagName === "IMG"), false);
  assert.match(li.textContent, /<b>x\.log/);
});

test("the one-line row: name, count, full meta for screen readers and as the tooltip; the sensitive badge comes and goes", () => {
  const { refs, panel } = setup();
  show(panel, [src(1, { records: 45083, sensitive: true, pid: null })]);
  const label = rowsOf(refs)[0].children[0];
  const text = label.children.find((c) => c.className === "source-text");
  const [name, count, meta] = text.children;
  assert.equal(name.textContent, "adapter1.log");
  assert.equal(count.textContent, "45,083");
  assert.equal(count.getAttribute("aria-hidden"), "true");
  assert.match(meta.className, /visually-hidden/);
  assert.equal(meta.textContent, "dms-core - 45,083 records");
  assert.equal(label.title, "dms-core - 45,083 records");
  assert.equal(label.children.at(-1).textContent, "sensitive");
  show(panel, [src(1, { records: 45083, sensitive: false })]);
  assert.equal(label.children.some((c) => c.className === "badge"), false);
});

test("filtering highlights with <mark> and clearing the filter restores the plain name, on the same rows", () => {
  const { refs, panel } = setup();
  show(panel, [src(1, { name: "alpha.log" }), src(2, { name: "beta.log" })]);
  const first = rowsOf(refs)[0];
  panel.setQuery("alp");
  assert.equal(rowsOf(refs).length, 1);
  assert.equal(rowsOf(refs)[0], first);
  const name = first.all().find((n) => n.className === "source-name");
  assert.equal(name.children.some((c) => c.tagName === "MARK" && c.textContent === "alp"), true);
  panel.setQuery("");
  assert.equal(rowsOf(refs).length, 2);
  assert.equal(name.children.some((c) => c.tagName === "MARK"), false);
  assert.equal(name.textContent, "alpha.log");
});

test("empty states come and go: no sources, no match, rows", () => {
  const { refs, panel } = setup();
  const seen = [];
  panel.onRendered = (v, t) => seen.push([v, t]);
  show(panel, []);
  assert.equal(rowsOf(refs).length, 1);
  assert.equal(rowsOf(refs)[0].textContent, S.sourcesEmpty);
  show(panel, [src(1)]);
  assert.equal(rowsOf(refs).length, 1);
  assert.equal(rowsOf(refs)[0].className, "");
  panel.setQuery("zzz");
  assert.equal(rowsOf(refs).length, 1);
  assert.equal(rowsOf(refs)[0].textContent, S.sourceFilterNone);
  panel.setQuery("");
  assert.equal(rowsOf(refs).length, 1);
  assert.equal(rowsOf(refs)[0].className, "");
  assert.deepEqual(seen.at(-1), [1, 1]);
  assert.deepEqual(seen[0], [0, 0]);
});

test("a source that vanishes leaves the selection; duplicate ids make one row", () => {
  const { refs, panel } = setup();
  show(panel, [src(1), src(2), src(2)]);
  assert.equal(rowsOf(refs).length, 2);
  panel.selected = new Set([1, 2]);
  panel.sources = [src(1)];
  panel.selected = new Set([...panel.selected].filter((id) => panel.sources.some((s) => s.id === id)));
  panel.renderSources();
  assert.deepEqual(panel.selectedIds(), [1]);
});

test("clearSelection and toggleFirst work on the reconciled rows", () => {
  const { refs, panel, calls } = setup();
  show(panel, [src(1), src(2)]);
  const boxes = [...rowsOf(refs)].map(boxOf);
  boxes.forEach(wireClick);
  panel.toggleFirst();
  assert.deepEqual(calls.selection.at(-1), [1]);
  panel.clearSelection();
  assert.equal(boxes[0].checked, false);
  assert.deepEqual(calls.selection.at(-1), []);
});

// ---- progress ------------------------------------------------------------------------------------------------

class FakeEventSource {
  static instances = [];
  constructor() { this.listeners = {}; this.readyState = 1; FakeEventSource.instances.push(this); }
  addEventListener(type, fn) { this.listeners[type] = fn; }
  close() { this.closed = true; }
  emit(type, data) { return this.listeners[type]({ data: JSON.stringify(data) }); }
}

test("progress events become a status-bar text without a stray colon; bad byte counts do not leak NaN", () => {
  globalThis.EventSource = FakeEventSource;
  FakeEventSource.instances = [];
  api.progressUrl = () => "/p";
  const { panel, calls } = setup();
  panel.refreshThrottled = () => {};
  panel.watchProgress("C");
  const stream = FakeEventSource.instances[0];
  stream.emit("progress", { source: "core.log", records: 1200, bytes_done: 50, bytes_total: 200 });
  assert.deepEqual(calls.progress.at(-1), { value: 50, max: 200, text: "Indexing core.log: 1,200 records" });
  stream.emit("progress", { source: null, records: 235230, bytes_done: 500, bytes_total: 200 });
  assert.deepEqual(calls.progress.at(-1), { value: 200, max: 200, text: "Indexing: 235,230 records" });
  stream.emit("progress", { source: "", records: 3, bytes_done: Number.NaN, bytes_total: 0 });
  assert.deepEqual(calls.progress.at(-1), { value: null, max: null, text: "Indexing: 3 records" });
  stream.emit("progress", { records: -5, bytes_done: "x", bytes_total: "y" });
  assert.deepEqual(calls.progress.at(-1), { value: null, max: null, text: "Indexing: 0 records" });
  for (const p of calls.progress.filter(Boolean)) {
    assert.ok(!/NaN|^:/.test(p.text));
  }
});

test("the end of an indexing says Indexed N records. and closes the progress", async () => {
  globalThis.EventSource = FakeEventSource;
  FakeEventSource.instances = [];
  api.progressUrl = () => "/p";
  api.sources = async () => ({ sources: [] });
  const { panel, calls } = setup();
  panel.caseId = "C";
  panel.refreshThrottled = () => {};
  panel.watchProgress("C");
  await FakeEventSource.instances[0].emit("done", { job_id: "j", status: "ok", records: 235230 });
  assert.deepEqual(calls.messages.at(-1), ["Indexed 235,230 records.", false]);
  assert.equal(calls.progress.at(-1), null);
  await tick();
});

// ---- case header and layout ------------------------------------------------------------------------------------

test("without a case the forms stay as they were and the header is hidden; with a case the header replaces them", async () => {
  const { refs, panel } = setup({ header: true });
  api.sources = async () => ({ sources: [] });
  assert.equal(refs.newCaseForm.hidden, true); // until the first case selection: the column does not jump
  assert.equal(refs.openForm.hidden, true);
  await panel.selectCase(null);
  assert.equal(refs.caseHeader.hidden, true);
  assert.equal(refs.newCaseForm.hidden, false);
  assert.equal(refs.openForm.hidden, false);
  panel.caseNames = new Map([["A", "incident"]]);
  await panel.selectCase("A");
  assert.equal(refs.caseHeader.hidden, false);
  assert.equal(refs.caseHeaderName.textContent, "incident");
  assert.equal(refs.caseHeaderName.title, "incident");
  assert.equal(refs.caseRow.hidden, true);
  assert.equal(refs.newCaseForm.hidden, true);
  assert.equal(refs.openForm.hidden, true);
  await panel.selectCase(null);
  assert.equal(refs.caseHeader.hidden, true);
  assert.equal(refs.openForm.hidden, false);
  assert.equal(refs.caseHeaderName.textContent, "");
});

test("Add files… opens the form with the focus inside it, and a second press closes it and keeps the focus on the button", async () => {
  const { refs, panel } = setup({ header: true });
  api.sources = async () => ({ sources: [] });
  await panel.selectCase("A");
  assert.equal(refs.caseAddFiles.getAttribute("aria-expanded"), "false");
  refs.caseAddFiles.dispatch("click");
  assert.equal(refs.openForm.hidden, false);
  assert.equal(refs.caseAddFiles.getAttribute("aria-expanded"), "true");
  assert.equal(doc.activeElement, refs.addFilesHost.children[0]);
  refs.caseAddFiles.dispatch("click");
  assert.equal(refs.openForm.hidden, true);
  assert.equal(refs.caseAddFiles.getAttribute("aria-expanded"), "false");
  assert.equal(doc.activeElement, refs.caseAddFiles);
});

test("revealNewCase and revealAddFiles(true) show the form and focus its field", async () => {
  const { refs, panel } = setup({ header: true });
  api.sources = async () => ({ sources: [] });
  await panel.selectCase("A");
  panel.revealNewCase();
  assert.equal(refs.newCaseForm.hidden, false);
  assert.equal(doc.activeElement, refs.newCaseName);
  panel.revealAddFiles(true);
  assert.equal(doc.activeElement, refs.pathInput);
});

test("the revealed forms close on another case, but not while a new case is being created", async () => {
  const { refs, panel } = setup({ header: true });
  api.sources = async () => ({ sources: [] });
  await panel.selectCase("A");
  panel.revealNewCase();
  panel.revealAddFiles();
  await panel.selectCase("B");
  assert.equal(refs.newCaseForm.hidden, true);
  assert.equal(refs.openForm.hidden, true);
  panel.revealNewCase();
  panel.setQueueBusy(true);
  await panel.selectCase("C"); // the queue creates the case and selects it: its results stay in view
  assert.equal(refs.newCaseForm.hidden, false);
});

test("the case menu loads ui-menu.js once, opens it, and a failed load is reported and retried", async () => {
  const { refs, panel, calls } = setup({ header: true });
  let loads = 0;
  const made = [];
  let failNext = true;
  panel.loadMenu = () => {
    loads += 1;
    if (failNext) {
      failNext = false;
      return Promise.reject(new Error("module failed"));
    }
    return Promise.resolve({ createMenu: (opts) => { const m = { opts, opened: [], open(where) { this.opened.push(where); } }; made.push(m); return m; } });
  };
  refs.caseMenu.dispatch("click", { detail: 0 });
  refs.caseMenu.dispatch("click", { detail: 0 }); // pending: ignored
  await tick();
  assert.equal(loads, 1);
  assert.deepEqual(calls.messages.at(-1), ["module failed", true]);
  refs.caseMenu.dispatch("click", { detail: 1 }); // retry, with the pointer
  await tick();
  assert.equal(loads, 2);
  assert.equal(made.length, 1);
  assert.deepEqual(made[0].opened, [null]);
  const items = made[0].opts.getItems();
  assert.deepEqual(items.map((i) => i.label), ["New case…", "Open a path…"]);
  assert.equal(made[0].opts.trigger, refs.caseMenu);
  refs.caseMenu.dispatch("click", { detail: 1 }); // built: the menu handles its own clicks
  await tick();
  assert.equal(loads, 2);
  items[1].run();
  assert.equal(doc.activeElement, refs.pathInput);
});

test("UI-21 review: creating the first case keeps its form in view while the queue runs and after", async () => {
  const { refs, panel } = setup({ header: true });
  api.cases = async () => ({ cases: [{ id: "N", name: "new" }] });
  api.sources = async () => ({ sources: [] });
  await panel.selectCase(null); // no case yet
  const seen = [];
  panel.caseFiles = {
    submit: async (deps) => {
      await deps.selectCase("N"); // the new case is selected while its files are still being opened
      seen.push(refs.newCaseForm.hidden);
    },
  };
  await panel.createCase();
  assert.deepEqual(seen, [false]);
  assert.equal(panel.caseId, "N");
  assert.equal(refs.newCaseForm.hidden, false); // results and Retry stay readable afterwards
});

test("UI-21 review: a refresh with nothing changed writes nothing to any row", () => {
  const { refs, panel } = setup();
  const list = [src(1, { status: "error", error: "x" }), src(2, { sensitive: true }), src(3)];
  show(panel, list);
  let writes = 0;
  for (const li of rowsOf(refs)) {
    for (const node of li.all()) {
      for (const key of ["className", "title", "checked"]) {
        let value = node[key];
        Object.defineProperty(node, key, { get: () => value, set: (v) => { writes += 1; value = v; }, configurable: true });
      }
      const set = node.setAttribute.bind(node);
      node.setAttribute = (k, v) => { writes += 1; set(k, v); };
      const text = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(node), "textContent");
      Object.defineProperty(node, "textContent", { get() { return text.get.call(this); }, set(v) { writes += 1; text.set.call(this, v); }, configurable: true });
    }
  }
  show(panel, list.map((s) => ({ ...s })));
  assert.equal(writes, 0);
  show(panel, [src(1, { status: "error", error: "x" }), src(2, { sensitive: true }), src(3, { records: 1 })]);
  assert.ok(writes > 0); // the guard is not blind: a real change is written
});
