// The "same key" flow with a fake api: keyset load more, stale answers, case changes.
import test from "node:test";
import assert from "node:assert/strict";

import { createKeyFlow } from "../../src/dmslog/web/js/key-panel-model.js";

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

const rec = (id) => ({ id, source_id: 1, member: "m", sensitive: false, ts: id * 1000, tid: 1, sev: 4, tpl_code: null, msg: "r" + id });

function setup() {
  const calls = [];
  const current = { caseId: "A" };
  const api = {
    keyRecords(caseId, type, value, params, signal) {
      const d = deferred();
      calls.push({ caseId, type, value, params, signal, d });
      return d.promise;
    },
  };
  const flow = createKeyFlow({ api, getCase: () => current.caseId, onState: () => {}, limit: 2 });
  return { flow, calls, current };
}

test("first page, keyset load more and duplicate ids", async () => {
  const { flow, calls } = setup();
  const opening = flow.open("msgid", "A/B 100%");
  assert.equal(flow.state.status, "loading");
  assert.deepEqual(calls[0].params, { limit: 2 });
  assert.equal(calls[0].value, "A/B 100%"); // raw: api.js does the encoding
  calls[0].d.resolve({ records: [rec(1), rec(2)], next: "2000:2" });
  await opening;
  assert.equal(flow.state.status, "ready");
  const more = flow.loadMore();
  assert.equal(flow.state.loadingMore, true);
  assert.deepEqual(calls[1].params, { limit: 2, after: "2000:2" });
  flow.loadMore(); // a second click while loading does nothing
  assert.equal(calls.length, 2);
  calls[1].d.resolve({ records: [rec(2), rec(3)], next: null });
  await more;
  assert.deepEqual(flow.state.records.map((r) => r.id), [1, 2, 3]);
  assert.equal(flow.state.next, null);
  await flow.loadMore();
  assert.equal(calls.length, 2);
});

test("opening another key drops the old answer", async () => {
  const { flow, calls } = setup();
  const first = flow.open("msgid", "A");
  const second = flow.open("gid", "B");
  assert.equal(calls[0].signal.aborted, true);
  calls[0].d.resolve({ records: [rec(1)], next: null });
  await first;
  assert.deepEqual(flow.state.records, []);
  assert.equal(flow.state.type, "gid");
  calls[1].d.resolve({ records: [rec(9)], next: null });
  await second;
  assert.deepEqual(flow.state.records.map((r) => r.id), [9]);
});

test("a stale load-more page is dropped after close or reopen", async () => {
  const { flow, calls } = setup();
  const opening = flow.open("msgid", "A");
  calls[0].d.resolve({ records: [rec(1), rec(2)], next: "t" });
  await opening;
  const more = flow.loadMore();
  flow.close();
  calls[1].d.resolve({ records: [rec(3)], next: null });
  await more;
  assert.deepEqual(flow.state.records, []);
  assert.equal(flow.state.status, "idle");
});

test("no case, errors and aborted requests", async () => {
  const { flow, calls, current } = setup();
  current.caseId = null;
  await flow.open("msgid", "A");
  assert.equal(flow.state.status, "nocase");
  assert.equal(calls.length, 0);
  current.caseId = "A";
  const failing = flow.open("msgid", "A");
  calls[0].d.reject(Object.assign(new Error("bad key"), { status: 400 }));
  await failing;
  assert.equal(flow.state.status, "error");
  assert.equal(flow.state.error, "bad key");
  const aborted = flow.open("msgid", "B");
  flow.close();
  calls[1].d.reject(Object.assign(new Error("x"), { name: "AbortError" }));
  await aborted;
  assert.equal(flow.state.status, "idle");
});
