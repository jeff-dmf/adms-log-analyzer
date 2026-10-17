// 9.12 UI: the "Repeat of #N" line of the detail on the fake DOM: tickets (a later record or case wins), the button that
// goes to the record repeated, no rebuild when the marks changed but the line did not.
import test from "node:test";
import assert from "node:assert/strict";

import { El, installGlobalDom, tick } from "./fixtures/fake-dom.mjs";

const doc = installGlobalDom();
globalThis.Element = El;
const { initRepeatDetail } = await import("../../src/dmslog/web/js/repeat-detail.js");
const { S } = await import("../../src/dmslog/web/js/strings.js");

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}
const abortError = () => Object.assign(new Error("aborted"), { name: "AbortError" });
const cls = (node, name) => node.className.split(/\s+/).includes(name);
const byClass = (root, name) => root.all().filter((n) => cls(n, name));
const mark = (prev, dtMs = 12, earlier = 0) => ({ prev, dtMs, earlier, label: `Repeat of #${prev}, +${dtMs} ms` });

function world() {
  const state = { caseId: "A", window: 1000 };
  const lookups = [];
  const entries = [];
  const reveals = [];
  const errors = [];
  const repeats = {
    window: () => state.window,
    lookup(id, signal) {
      const d = deferred();
      lookups.push({ id, d, signal });
      signal?.addEventListener?.("abort", () => d.reject(abortError()));
      return d.promise;
    },
  };
  const api = {
    entry(caseId, id, signal) {
      const d = deferred();
      entries.push({ caseId, id, d, signal });
      signal?.addEventListener?.("abort", () => d.reject(abortError()));
      return d.promise;
    },
  };
  const host = doc.createElement("section");
  host.hidden = true;
  doc.body.appendChild(host);
  const detail = initRepeatDetail(host, {
    repeats, api, getCase: () => state.caseId, onReveal: async (row) => { reveals.push(row); }, onError: (err) => errors.push(err),
  });
  return { host, detail, state, lookups, entries, reveals, errors };
}

test("a repeated record: the line, the earlier occurrences, the window and a button to the record repeated", async () => {
  const w = world();
  assert.equal(w.host.hidden, true);
  const showing = w.detail.show({ id: 9 });
  w.lookups[0].d.resolve(mark(5, 340, 2));
  await showing;
  assert.equal(w.host.hidden, false);
  const text = w.host.textContent;
  assert.match(text, /Repeat of #5, \+340 ms/);
  assert.match(text, /2 earlier occurrences in the window/);
  assert.match(text, /window 1 s/);
  assert.equal(byClass(w.host, "rp-go")[0].textContent, "Go to #5");
});

test("no mark, no line: the section stays hidden for a record that repeats nothing, and for a failed lookup", async () => {
  const w = world();
  let showing = w.detail.show({ id: 9 });
  w.lookups[0].d.resolve(null);
  await showing;
  assert.equal(w.host.hidden, true);
  showing = w.detail.show({ id: 10 });
  w.lookups[1].d.reject(new Error("boom"));
  await showing;
  assert.equal(w.host.hidden, true);
  assert.equal(w.errors.length, 0, "a hint: silent");
  await w.detail.show(null);
  await w.detail.show({ id: "x" });
  assert.equal(w.host.hidden, true);
});

test("a late answer for an earlier record never draws over the record shown now", async () => {
  const w = world();
  const first = w.detail.show({ id: 1 });
  const second = w.detail.show({ id: 2 });
  assert.equal(w.lookups[0].signal.aborted, true);
  w.lookups[0].d.resolve(mark(111)); // late: the first record's answer
  w.lookups[1].d.resolve(mark(222));
  await Promise.all([first, second]);
  assert.match(w.host.textContent, /#222/);
  assert.doesNotMatch(w.host.textContent, /#111/);
  // the record was cleared (a case switch) while its answer was on the way
  const third = w.detail.show({ id: 3 });
  await w.detail.show(null);
  w.lookups[2].d.resolve(mark(333));
  await third;
  assert.equal(w.host.hidden, true);
  assert.equal(w.host.textContent, "");
});

test("an answer that arrives after the case changed is dropped", async () => {
  const w = world();
  const showing = w.detail.show({ id: 1 });
  w.state.caseId = "B";
  w.lookups[0].d.resolve(mark(5));
  await showing;
  assert.equal(w.host.hidden, true);
  assert.equal(w.host.textContent, "");
});

test("without a case nothing is looked up", async () => {
  const w = world();
  w.state.caseId = null;
  await w.detail.show({ id: 1 });
  assert.equal(w.lookups.length, 0);
});

test("Go to: reads the record repeated, then asks the grid for it with its time; the button stays focusable while it works", async () => {
  const w = world();
  const showing = w.detail.show({ id: 9 });
  w.lookups[0].d.resolve(mark(5));
  await showing;
  const go = byClass(w.host, "rp-go")[0];
  go.focus();
  go.click();
  assert.deepEqual([w.entries[0].caseId, w.entries[0].id], ["A", 5]);
  assert.equal(go.getAttribute("aria-disabled"), "true");
  assert.equal(go.disabled, false);
  go.click();
  assert.equal(w.entries.length, 1, "a second click while it works does nothing");
  w.entries[0].d.resolve({ id: 5, ts: 777 });
  await tick();
  assert.deepEqual(w.reveals, [{ id: 5, ts: 777 }]);
  assert.equal(go.getAttribute("aria-disabled"), null);
  assert.equal(doc.activeElement, go);
});

test("Go to: a failure is reported, a wrong record is not followed, and a record shown meanwhile cancels it", async () => {
  const w = world();
  let showing = w.detail.show({ id: 9 });
  w.lookups[0].d.resolve(mark(5));
  await showing;
  let go = byClass(w.host, "rp-go")[0];
  go.click();
  w.entries[0].d.reject(new Error("net"));
  await tick();
  assert.equal(w.errors.length, 1);
  assert.equal(go.getAttribute("aria-disabled"), null);
  go.click();
  w.entries[1].d.resolve({ id: 6, ts: 1 }); // not the record asked for
  await tick();
  assert.equal(w.reveals.length, 0);
  go.click();
  const next = w.detail.show({ id: 10 });
  assert.equal(w.entries[2].signal.aborted, true);
  w.entries[2].d.resolve({ id: 5, ts: 9 });
  w.lookups[1].d.resolve(null);
  await next;
  await tick();
  assert.equal(w.reveals.length, 0, "the record shown changed: the old click is dropped");
  assert.equal(w.errors.length, 1, "an abort is not an error");
});

test("Go to after the case changed does nothing", async () => {
  const w = world();
  const showing = w.detail.show({ id: 9 });
  w.lookups[0].d.resolve(mark(5));
  await showing;
  w.state.caseId = "B";
  byClass(w.host, "rp-go")[0].click();
  assert.equal(w.entries.length, 0);
});

test("refresh redraws only when the line changed (a focused button is not rebuilt), hides it when the mark is gone", async () => {
  const w = world();
  const showing = w.detail.show({ id: 9 });
  w.lookups[0].d.resolve(mark(5));
  await showing;
  const go = byClass(w.host, "rp-go")[0];
  go.focus();
  let refreshing = w.detail.refresh();
  w.lookups[1].d.resolve(mark(5));
  await refreshing;
  assert.equal(byClass(w.host, "rp-go")[0], go, "same node");
  assert.equal(doc.activeElement, go);
  refreshing = w.detail.refresh();
  w.lookups[2].d.resolve(mark(5, 99));
  await refreshing;
  assert.match(w.host.textContent, /\+99 ms/);
  refreshing = w.detail.refresh();
  const second = w.detail.refresh(); // one is in flight: no second request
  assert.equal(w.lookups.length, 4);
  w.lookups[3].d.resolve(null);
  await Promise.all([refreshing, second]);
  assert.equal(w.host.hidden, true);
  w.detail.close();
  await w.detail.refresh();
  assert.equal(w.lookups.length, 4, "a closed detail asks for nothing");
});

test("a new window redraws the line even when the mark is the same number", async () => {
  const w = world();
  const showing = w.detail.show({ id: 9 });
  w.lookups[0].d.resolve(mark(5));
  await showing;
  w.state.window = 5000;
  const refreshing = w.detail.refresh();
  w.lookups[1].d.resolve(mark(5));
  await refreshing;
  assert.match(w.host.textContent, /window 5 s/);
  assert.ok(S.repeatTitle);
});
