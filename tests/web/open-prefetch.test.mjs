// UI-14: the opening requests started by boot.js (prefetch.js) and taken once by SourcesPanel and main.js.
import test from "node:test";
import assert from "node:assert/strict";

import { installGlobalDom, tick } from "./fixtures/fake-dom.mjs";

const doc = installGlobalDom();
const { api } = await import("../../src/dmslog/web/js/api.js");
const prefetch = await import("../../src/dmslog/web/js/prefetch.js");
const { SourcesPanel } = await import("../../src/dmslog/web/js/sources.js");

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

// A client whose answers the test controls; every call is logged in order.
function fakeClient(log, { cases = { cases: [{ id: 7, name: "A" }, { id: 8, name: "B" }] }, gate = null } = {}) {
  const answer = (name, value) => (...args) => {
    log.push([name, ...args.filter((a) => typeof a !== "object" || a === null)]);
    return gate && gate[name] ? gate[name].promise : Promise.resolve(value);
  };
  return {
    cases: answer("cases", cases),
    sources: answer("sources", { sources: [{ id: 1, name: "s.log" }] }),
    collections: answer("collections", { collections: [], ordering: "exact" }),
  };
}

test.beforeEach(() => prefetch.resetPrefetch());

test("sources and collections start together as soon as the cases answer, for the first case", async () => {
  const log = [];
  const gate = { cases: deferred() };
  prefetch.startPrefetch(fakeClient(log, { gate }));
  assert.deepEqual(log, [["cases"]]);
  gate.cases.resolve({ cases: [{ id: 7, name: "A" }, { id: 8, name: "B" }] });
  await tick();
  assert.deepEqual(log, [["cases"], ["sources", 7], ["collections", 7]]);
});

test("each answer is taken once; another case or a repeated take falls back to the API", async () => {
  prefetch.startPrefetch(fakeClient([]));
  await tick();
  assert.equal(prefetch.takeSources(8, () => "fallback"), null, "a case other than the first one");
  assert.deepEqual(await prefetch.takeSources(7, () => "fallback"), { sources: [{ id: 1, name: "s.log" }] });
  assert.equal(prefetch.takeSources(7, () => "fallback"), null);
  assert.deepEqual(await prefetch.takeCollections("7", () => "fallback"), { collections: [], ordering: "exact" });
  assert.equal(prefetch.takeCollections(7, () => "fallback"), null);
  assert.deepEqual((await prefetch.takeCases(() => "fallback")).cases.length, 2);
  assert.equal(prefetch.takeCases(() => "fallback"), null);
});

test("nothing is handed over when boot.js did not run, or when there is no case", async () => {
  assert.equal(prefetch.takeCases(() => null), null);
  assert.equal(prefetch.takeSources(7, () => null), null);
  const log = [];
  prefetch.startPrefetch(fakeClient(log, { cases: { cases: [] } }));
  await tick();
  assert.deepEqual(log, [["cases"]]);
  assert.equal(prefetch.takeSources(7, () => null), null);
});

test("a failed prefetch is asked again by the taker, so the connection hooks see the failure", async () => {
  const log = [];
  const gate = { cases: deferred() };
  prefetch.startPrefetch(fakeClient(log, { gate }));
  gate.cases.reject(new Error("offline"));
  const data = await prefetch.takeCases(() => Promise.resolve({ cases: ["retried"] }));
  assert.deepEqual(data, { cases: ["retried"] });
});

test("startPrefetch runs once", () => {
  const log = [];
  prefetch.startPrefetch(fakeClient(log));
  prefetch.startPrefetch(fakeClient(log));
  assert.equal(log.filter(([name]) => name === "cases").length, 1);
});

test("the opening of SourcesPanel makes no request of its own for what boot.js already asked", async () => {
  const log = [];
  const real = { cases: api.cases, sources: api.sources, collections: api.collections };
  Object.assign(api, fakeClient(log));
  try {
    prefetch.startPrefetch();
    await tick();
    const refs = {
      caseSelect: doc.createElement("select"), newCaseForm: doc.createElement("form"), newCaseName: doc.createElement("input"),
      openForm: doc.createElement("form"), pathInput: doc.createElement("input"), scopeSelect: doc.createElement("select"),
      sourceList: doc.createElement("ul"),
    };
    const seen = [];
    const panel = new SourcesPanel(refs, {
      onCase: (id) => seen.push(["case", id]), onSources: (list) => seen.push(["sources", list.length]),
      onSelection() {}, onDone() {}, onMessage() {},
    });
    await panel.loadCases(null);
    assert.deepEqual(seen, [["case", 7], ["sources", 1]]);
    assert.deepEqual(log.map(([name]) => name), ["cases", "sources", "collections"], "no second cases or sources call");
    // Later calls are ordinary requests.
    await panel.refreshSources(true);
    assert.equal(log.filter(([name]) => name === "sources").length, 2);
    // The collections answer is still waiting for the Collections panel (main.js takes it).
    assert.ok(prefetch.takeCollections(7, () => null));
  } finally {
    Object.assign(api, real);
  }
});
