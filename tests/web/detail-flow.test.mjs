// Drives Detail.loadContext()/closeContext() with deferred API answers and a DOM-less fake.
import test from "node:test";
import assert from "node:assert/strict";

// Minimal document: only what detail.js touches while rendering the context list.
globalThis.document = {
  createElement: () => ({
    className: "", textContent: "", tabIndex: 0, children: [],
    classList: { add() {} },
    appendChild(child) { this.children.push(child); },
    addEventListener() {},
    scrollIntoView() {},
  }),
};

const { Detail } = await import("../../src/dmslog/web/js/detail.js");
const { api } = await import("../../src/dmslog/web/js/api.js");

function fakeRefs() {
  const el = () => ({ hidden: false, textContent: "", value: "source", listeners: {}, addEventListener(n, f) { this.listeners[n] = f; } });
  const list = { textContent: "", children: [], appendChild(c) { this.children.push(c); } };
  Object.defineProperty(list, "textContent", {
    get() { return ""; },
    set() { this.children.length = 0; },
  });
  return {
    meta: el(), text: el(), actions: el(), contextButton: el(), contextClose: el(), contextSame: el(),
    contextBox: el(), contextList: list, copyButton: el(), copyStatus: el(),
  };
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

function setup() {
  const refs = fakeRefs();
  const state = { caseId: "c1", errors: [] };
  const detail = new Detail(refs, {
    getCase: () => state.caseId,
    getSourceName: (id) => String(id),
    onError: (err) => state.errors.push(err),
  });
  detail.entry = { id: 5, ts: 1, source_id: 1 };
  detail.entryCase = "c1";
  return { refs, state, detail };
}

const answer = { rows: [{ id: 4, ts: 1, msg: "a" }, { id: 5, ts: 2, msg: "b" }] };

test("context answer renders when nothing changed", async () => {
  const { refs, detail } = setup();
  const d = deferred();
  api.context = () => d.promise;
  const pending = detail.loadContext();
  d.resolve(answer);
  await pending;
  assert.equal(refs.contextBox.hidden, false);
  assert.equal(refs.contextList.children.length, 2);
});

test("closing the context while a request is pending keeps the panel closed", async () => {
  const { refs, detail } = setup();
  const d = deferred();
  api.context = () => d.promise;
  const pending = detail.loadContext();
  detail.closeContext();
  d.resolve(answer);
  await pending;
  assert.equal(refs.contextBox.hidden, true);
  assert.equal(refs.contextList.children.length, 0);
});

test("clear() while a request is pending discards the answer", async () => {
  const { refs, detail } = setup();
  const d = deferred();
  api.context = () => d.promise;
  const pending = detail.loadContext();
  detail.clear();
  d.resolve(answer);
  await pending;
  assert.equal(refs.contextBox.hidden, true);
  assert.equal(refs.contextList.children.length, 0);
});

test("a case switch while a request is pending discards the answer", async () => {
  const { refs, state, detail } = setup();
  const d = deferred();
  api.context = () => d.promise;
  const pending = detail.loadContext();
  state.caseId = "c2";
  d.resolve(answer);
  await pending;
  assert.equal(refs.contextBox.hidden, true);
});

test("a stale failure is not reported", async () => {
  const { state, detail } = setup();
  const d = deferred();
  api.context = () => d.promise;
  const pending = detail.loadContext();
  detail.closeContext();
  d.reject(new Error("late"));
  await pending;
  assert.equal(state.errors.length, 0);
});

test("the context is not requested for an entry fetched in another case", async () => {
  const { state, detail } = setup();
  let calls = 0;
  api.context = () => { calls += 1; return Promise.resolve(answer); };
  state.caseId = "c2"; // case switched, /sources not yet answered, old entry still held
  await detail.loadContext();
  assert.equal(calls, 0);
});
