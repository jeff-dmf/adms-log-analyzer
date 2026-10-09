// Links flow and side-by-side flow with a fake api: no DOM, no real requests.
import test from "node:test";
import assert from "node:assert/strict";

import { createLinksFlow, createSideBySideFlow } from "../../src/dmslog/web/js/links-model.js";

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

const link = (entry_id) => ({ entry_id, kind: "normal_sensitive", confidence: 3, source: { member: "m", sensitive: true }, ts: 1 });

function setupLinks() {
  const calls = [];
  const states = [];
  const current = { caseId: "A" };
  const api = {
    entryLinks(caseId, id, signal) {
      const d = deferred();
      calls.push({ kind: "links", caseId, id, signal, d });
      return d.promise;
    },
    entryHead(caseId, id, lines, signal) {
      const d = deferred();
      calls.push({ kind: "head", caseId, id, lines, signal, d });
      return d.promise;
    },
  };
  const flow = createLinksFlow({ api, getCase: () => current.caseId, onState: (s) => states.push(JSON.parse(JSON.stringify(s))) });
  return { flow, calls, states, current };
}

test("links load for an entry and become rows", async () => {
  const { flow, calls } = setupLinks();
  const loading = flow.load(10);
  assert.equal(flow.state.status, "loading");
  assert.equal(calls[0].caseId, "A");
  assert.equal(calls[0].id, 10);
  calls[0].d.resolve({ links: [link(11)] });
  await loading;
  assert.equal(flow.state.status, "ready");
  assert.deepEqual(flow.state.rows.map((r) => r.entryId), [11]);
});

test("a stale links answer is dropped when another entry was selected", async () => {
  const { flow, calls } = setupLinks();
  const first = flow.load(1);
  const second = flow.load(2);
  assert.equal(calls[0].signal.aborted, true);
  calls[0].d.resolve({ links: [link(99)] });
  await first;
  assert.deepEqual(flow.state.rows, []);
  assert.equal(flow.state.status, "loading");
  calls[1].d.resolve({ links: [link(3)] });
  await second;
  assert.deepEqual(flow.state.rows.map((r) => r.entryId), [3]);
});

test("reset drops a pending answer; no case means nothing is requested", async () => {
  const { flow, calls, current } = setupLinks();
  const pending = flow.load(1);
  flow.reset();
  calls[0].d.resolve({ links: [link(5)] });
  await pending;
  assert.equal(flow.state.status, "idle");
  assert.deepEqual(flow.state.rows, []);
  current.caseId = null;
  await flow.load(2);
  assert.equal(calls.length, 1);
  assert.equal(flow.state.status, "idle");
});

test("an error is reported, an aborted request stays silent", async () => {
  const { flow, calls } = setupLinks();
  const failing = flow.load(1);
  calls[0].d.reject(Object.assign(new Error("boom"), { status: 500 }));
  await failing;
  assert.equal(flow.state.status, "error");
  assert.equal(flow.state.error, "boom");
  const aborted = flow.load(2);
  flow.reset();
  calls[1].d.reject(Object.assign(new Error("aborted"), { name: "AbortError" }));
  await aborted;
  assert.equal(flow.state.status, "idle");
});

test("preview loads the first 40 lines lazily and only for a listed counterpart", async () => {
  const { flow, calls } = setupLinks();
  const loading = flow.load(1);
  calls[0].d.resolve({ links: [link(2)] });
  await loading;
  await flow.preview(777); // not a counterpart of this entry
  assert.equal(calls.length, 1);
  const preview = flow.preview(2);
  assert.equal(flow.state.preview.status, "loading");
  assert.equal(calls[1].kind, "head");
  assert.equal(calls[1].lines, 40);
  calls[1].d.resolve({ text: "a\nb", cont_truncated: true });
  await preview;
  assert.deepEqual(
    { s: flow.state.preview.status, t: flow.state.preview.text, c: flow.state.preview.truncated },
    { s: "ready", t: "a\nb", c: true },
  );
  flow.closePreview();
  assert.equal(flow.state.preview.status, "none");
});

test("a preview answer is dropped after the entry changed", async () => {
  const { flow, calls } = setupLinks();
  const loading = flow.load(1);
  calls[0].d.resolve({ links: [link(2)] });
  await loading;
  const preview = flow.preview(2);
  flow.load(3);
  calls[1].d.resolve({ text: "old", cont_truncated: false });
  await preview;
  assert.equal(flow.state.preview.status, "none");
  assert.equal(flow.state.preview.text, "");
});

function setupSide() {
  const calls = [];
  const current = { caseId: "A" };
  const api = {
    sideBySide(caseId, id, signal) {
      const d = deferred();
      calls.push({ caseId, id, signal, d });
      return d.promise;
    },
  };
  const flow = createSideBySideFlow({ api, getCase: () => current.caseId, onState: () => {} });
  return { flow, calls, current };
}

test("side by side: newest request wins, close drops the answer", async () => {
  const { flow, calls } = setupSide();
  const first = flow.open(1);
  const second = flow.open(2);
  assert.equal(calls[0].signal.aborted, true);
  calls[0].d.resolve({ a: { id: 1 }, b: { id: 9 } });
  await first;
  assert.equal(flow.state.data, null);
  calls[1].d.resolve({ a: { id: 2 }, b: { id: 8 } });
  await second;
  assert.equal(flow.state.status, "ready");
  assert.equal(flow.state.data.a.id, 2);
  const third = flow.open(3);
  flow.close();
  calls[2].d.resolve({ a: { id: 3 }, b: { id: 7 } });
  await third;
  assert.equal(flow.state.status, "idle");
  assert.equal(flow.state.data, null);
});

test("side by side: 404 no_counterpart is its own state, no case is reported", async () => {
  const { flow, calls, current } = setupSide();
  const none = flow.open(1);
  calls[0].d.reject(Object.assign(new Error("none"), { status: 404, code: "no_counterpart" }));
  await none;
  assert.equal(flow.state.status, "none");
  const failing = flow.open(2);
  calls[1].d.reject(Object.assign(new Error("down"), { status: 500, code: "http_500" }));
  await failing;
  assert.equal(flow.state.status, "error");
  current.caseId = null;
  await flow.open(3);
  assert.equal(flow.state.status, "nocase");
  assert.equal(calls.length, 2);
});
