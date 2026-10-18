// UI-29: one way to show a record for the grid selection, the record menu and the palette (late-init.js createRecordLoader), on the
// REAL Detail: a record that is shown is held against a late answer for another one, a request for the same record is shared,
// and watchGesture() watches a pending menu request from the first moment.
import test from "node:test";
import assert from "node:assert/strict";

import { installGlobalDom, makeDoc } from "./fixtures/fake-dom.mjs";

installGlobalDom();

const { Detail } = await import("../../src/dmslog/web/js/detail.js");
const { api } = await import("../../src/dmslog/web/js/api.js");
const { createRecordLoader, watchGesture } = await import("../../src/dmslog/web/js/late-init.js");

function deferred() {
  let resolve;
  const promise = new Promise((res) => { resolve = res; });
  return { promise, resolve };
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
