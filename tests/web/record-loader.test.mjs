// UI-29: one way to show a record for the grid selection, the record menu and the palette (late-init.js createRecordLoader), on the
// REAL Detail: a record that is shown is held against a late answer for another one, a request for the same record is shared,
// and watchGesture() watches a pending menu request from the first moment.
import test from "node:test";
import assert from "node:assert/strict";

import { installGlobalDom, makeDoc } from "./fixtures/fake-dom.mjs";

installGlobalDom();

const { Detail } = await import("../../src/dmslog/web/js/detail.js");
const { api } = await import("../../src/dmslog/web/js/api.js");
const { createMenuRequests, createRecordLoader, watchGesture } = await import("../../src/dmslog/web/js/late-init.js");

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

function setup() {
  const el = () => document.createElement("div");
  const refs = {
    empty: el(), meta: el(), text: el(), actions: el(), contextButton: el(), contextClose: el(), contextSame: el(),
    contextBox: el(), contextList: el(), copyButton: el(), copyStatus: el(),
  };
  const detail = new Detail(refs, { getCase: () => "c1", getSourceName: String, onError: (err) => { throw err; } });
  const asked = [];
  const answers = new Map();
  api.entry = (caseId, id) => {
    asked.push(id);
    const d = deferred();
    answers.set(id, d);
    return d.promise;
  };
  const loader = createRecordLoader({ getEntry: () => detail.entry, show: (id) => detail.show(id), hold: (id) => detail.hold(id) });
  const answer = (id) => answers.get(id).resolve({ id, ts: 1, source_id: 1, level: 3, sev: 3, msg: "m" + id, tpl_id: 1 });
  return { detail, loader, asked, answer };
}
const flush = () => new Promise((resolve) => setTimeout(resolve, 2));

test("a record that is shown is held: the late answer of another record's request does not replace it", async () => {
  const t = setup();
  const first = t.loader.ensure({ id: 1 });
  t.answer(1);
  assert.equal(await first, true);
  assert.equal(t.detail.entry.id, 1);
  const selecting = t.loader.ensure({ id: 2 }, { refresh: true }); // the grid's selection of record 2 is still loading
  assert.equal(await t.loader.ensure({ id: 1 }), true, "the menu was opened for record 1, which is shown");
  t.answer(2); // the answer arrives late
  await selecting;
  await flush();
  assert.equal(t.detail.entry.id, 1, "record 1 stays");
});

test("without the hold the same sequence WOULD replace the detail (the repro of the review)", async () => {
  const t = setup();
  const first = t.detail.show(1);
  t.answer(1);
  await first;
  const selecting = t.detail.show(2);
  t.answer(2);
  await selecting;
  assert.equal(t.detail.entry.id, 2);
});

test("a request already running for the record is shared by the selection and the menu", async () => {
  const t = setup();
  const selecting = t.loader.ensure({ id: 5 }, { refresh: true });
  const menu = t.loader.ensure({ id: 5 });
  assert.equal(menu, selecting);
  assert.deepEqual(t.asked, [5], "one request");
  t.answer(5);
  assert.deepEqual([await selecting, await menu], [true, true]);
});

test("another record's request replaces the running one; the loser reports false", async () => {
  const t = setup();
  const a = t.loader.ensure({ id: 1 });
  const b = t.loader.ensure({ id: 2 });
  t.answer(1);
  t.answer(2);
  assert.deepEqual([await a, await b], [false, true]);
  assert.equal(t.detail.entry.id, 2);
});

test("refresh loads the shown record again; a failed request reports false and is forgotten", async () => {
  const t = setup();
  const one = t.loader.ensure({ id: 1 });
  t.answer(1);
  await one;
  const again = t.loader.ensure({ id: 1 }, { refresh: true });
  assert.deepEqual(t.asked, [1, 1]);
  t.answer(1);
  assert.equal(await again, true);
  const failing = createRecordLoader({ getEntry: () => null, show: () => Promise.reject(new Error("x")), hold: () => false });
  assert.equal(await failing.ensure({ id: 9 }), false);
  assert.equal(await failing.ensure({ id: 9 }), false, "not stuck on the failed request");
});

test("Detail.hold: true only for the shown record, and it aborts the request for another one", async () => {
  const t = setup();
  const first = t.detail.show(1);
  t.answer(1);
  await first;
  assert.equal(t.detail.hold(7), false);
  assert.equal(t.detail.hold("1"), true);
});

test("watchGesture: installed at once, cancels on scroll, wheel, a click, another control taking the focus, blur and resize; stop removes all", () => {
  const doc = makeDoc();
  const win = { listeners: {}, addEventListener(t, fn) { (this.listeners[t] ??= []).push(fn); }, removeEventListener(t, fn) { this.listeners[t] = (this.listeners[t] ?? []).filter((f) => f !== fn); } };
  const keep = doc.createElement("div");
  const other = doc.createElement("button");
  let hits = 0;
  const stop = watchGesture({ doc, win, keep, cancel: () => { hits += 1; } });
  const fire = (target, type, event) => { for (const fn of target.listeners[type] ?? []) fn(event); };
  for (const type of ["scroll", "wheel", "pointerdown"]) fire(doc, type, { type, target: other });
  fire(doc, "focusin", { type: "focusin", target: other });
  assert.equal(hits, 4);
  fire(doc, "focusin", { type: "focusin", target: keep });
  assert.equal(hits, 4, "the grid taking the focus is not a cancel");
  fire(win, "blur", {});
  fire(win, "resize", {});
  assert.equal(hits, 6);
  stop();
  stop();
  for (const type of ["scroll", "wheel", "pointerdown", "focusin"]) assert.deepEqual(doc.listeners[type] ?? [], [], type);
  assert.deepEqual([win.listeners.blur, win.listeners.resize], [[], []]);
});

// ---- createMenuRequests: the request is watched from the gesture and the watching ends the moment it is cancelled ----------

function menuSetup({ selected = { id: 7 } } = {}) {
  const doc = makeDoc();
  const win = { listeners: {}, addEventListener(t, fn) { (this.listeners[t] ??= []).push(fn); }, removeEventListener(t, fn) { this.listeners[t] = (this.listeners[t] ?? []).filter((f) => f !== fn); } };
  const keep = doc.createElement("div");
  const other = doc.createElement("button");
  const st = { caseId: "C1", selected, shown: [], held: 0, opened: [], closed: 0, importDone: null };
  const gates = [];
  const menu = { open: async (req) => { st.opened.push(req.row.id); return true; }, close: () => { st.closed += 1; } };
  const requests = createMenuRequests({
    doc, win, keep: () => keep, getCase: () => st.caseId, selectedRow: () => st.selected,
    holdSelection: () => { st.held += 1; }, show: (row) => { st.shown.push(row.id); },
    load: () => { const d = deferred(); gates.push(d); return d.promise; }, getMenu: () => menu,
  });
  const count = () => ({
    doc: ["scroll", "wheel", "pointerdown", "focusin"].reduce((n, t) => n + (doc.listeners[t] ?? []).length, 0),
    win: (win.listeners.blur ?? []).length + (win.listeners.resize ?? []).length,
  });
  const fire = (type, target = other) => { for (const fn of [...(doc.listeners[type] ?? [])]) fn({ type, target }); };
  const req = (id = 7) => ({ row: { id }, x: 1, y: 2, keyboard: false });
  return { st, requests, gates, menu, count, fire, req, keep, other };
}

test("a request is watched at once; cancelling it removes every listener even though the import has not ended", async () => {
  const t = menuSetup();
  const pending = t.requests.request(t.req());
  assert.deepEqual(t.count(), { doc: 4, win: 2 }, "watching starts before the module is in");
  assert.deepEqual([t.st.held, t.st.shown], [1, [7]]);
  t.fire("scroll"); // the user scrolled: cancelled
  assert.deepEqual(t.count(), { doc: 0, win: 0 }, "removed at the cancel, the import is still pending");
  t.gates[0].resolve(t.menu);
  assert.equal(await pending, false);
  assert.deepEqual(t.st.opened, []);
});

test("new attempts while an import is pending do not accumulate observers: the older request is stopped when replaced", async () => {
  const t = menuSetup();
  const first = t.requests.request(t.req());
  const second = t.requests.request(t.req());
  const third = t.requests.request(t.req());
  assert.deepEqual(t.count(), { doc: 4, win: 2 }, "only the latest request is watched");
  t.fire("wheel");
  assert.deepEqual(t.count(), { doc: 0, win: 0 });
  for (const gate of t.gates) gate.resolve(t.menu);
  assert.deepEqual([await first, await second, await third], [false, false, false]);
});

test("close() (a selection, a view or a case change) stops the watching and closes the menu at once", async () => {
  const t = menuSetup();
  const pending = t.requests.request(t.req());
  t.requests.close();
  assert.deepEqual(t.count(), { doc: 0, win: 0 });
  assert.equal(t.st.closed, 1);
  assert.equal(t.requests.targetId(), null);
  t.gates[0].resolve(t.menu);
  assert.equal(await pending, false);
});

test("the focus moving to another control cancels; the grid taking it does not", async () => {
  const t = menuSetup();
  const pending = t.requests.request(t.req());
  t.fire("focusin", t.keep);
  assert.deepEqual(t.count(), { doc: 4, win: 2 });
  t.fire("focusin", t.other);
  assert.deepEqual(t.count(), { doc: 0, win: 0 });
  t.gates[0].resolve(t.menu);
  assert.equal(await pending, false);
});

test("an untouched request hands the menu its request and stops watching; the target is known meanwhile", async () => {
  const t = menuSetup();
  const pending = t.requests.request(t.req());
  assert.equal(t.requests.targetId(), 7);
  t.gates[0].resolve(t.menu);
  assert.equal(await pending, true);
  assert.deepEqual(t.st.opened, [7]);
  assert.deepEqual(t.count(), { doc: 0, win: 0 });
});

test("the module arriving after the case or the selection changed opens nothing; a failing import leaves no listener", async () => {
  for (const change of [(st) => { st.caseId = "C2"; }, (st) => { st.selected = { id: 8 }; }, (st) => { st.selected = null; }]) {
    const t = menuSetup();
    const pending = t.requests.request(t.req());
    change(t.st);
    t.gates[0].resolve(t.menu);
    assert.equal(await pending, false);
    assert.deepEqual(t.st.opened, []);
    assert.deepEqual(t.count(), { doc: 0, win: 0 });
  }
  const t = menuSetup();
  const failing = t.requests.request(t.req());
  assert.deepEqual(t.count(), { doc: 4, win: 2 });
  t.gates[0].reject(new Error("import failed"));
  await assert.rejects(failing, /import failed/);
  assert.deepEqual(t.count(), { doc: 0, win: 0 }, "the finally stops the watching");
});
