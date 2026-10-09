// Payload flows with a fake api: selection, stale answers, pair, notice cache, list paging.
import test from "node:test";
import assert from "node:assert/strict";

import { createPayloadFlow, createPayloadNoticeFlow } from "../../src/dmslog/web/js/payload-model.js";
import { buildPayloadParams, buildPayloadRow, createPayloadListFlow } from "../../src/dmslog/web/js/payload-list-model.js";

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

const meta = (id, over = {}) => ({ id, entry_id: 20, event_id: 3, event_conf: 2, ref_entry_id: 10, pair_id: null, role: "request", direction: "in", char_len: 5, ts: 1000, sensitive: true, ...over });

function setup() {
  const calls = [];
  const current = { caseId: "A" };
  const api = {
    entryPayloads(caseId, id, signal) { const d = deferred(); calls.push({ name: "list", caseId, id, signal, d }); return d.promise; },
    payload(caseId, id, fmt, signal) { const d = deferred(); calls.push({ name: "payload", caseId, id, fmt, signal, d }); return d.promise; },
  };
  const flow = createPayloadFlow({ api, getCase: () => current.caseId, onState: () => {} });
  return { flow, calls, current };
}

test("loads the list, then the text of the first own payload (pretty)", async () => {
  const { flow, calls } = setup();
  const loading = flow.load(20);
  assert.equal(flow.state.status, "loading");
  calls[0].d.resolve({ payloads: [meta(5, { entry_id: 99, ref_entry_id: 20 }), meta(6)] });
  await new Promise((r) => setImmediate(r));
  assert.equal(calls[1].name, "payload");
  assert.equal(calls[1].id, 6);
  assert.equal(calls[1].fmt, "pretty");
  calls[1].d.resolve({ payload: meta(6), text: "<a/>", formatted: null, pair: null });
  await loading;
  assert.equal(flow.state.detail.status, "ready");
  assert.equal(flow.state.selectedId, 6);
});

test("a new entry drops the old answers", async () => {
  const { flow, calls } = setup();
  const first = flow.load(1);
  const second = flow.load(2);
  assert.equal(calls[0].signal.aborted, true);
  calls[0].d.resolve({ payloads: [meta(1)] });
  await first;
  assert.deepEqual(flow.state.items, []);
  calls[1].d.resolve({ payloads: [] });
  await second;
  assert.equal(flow.state.status, "ready");
  assert.equal(calls.length, 2); // nothing to select
});

test("a case change makes select ignore the request", async () => {
  const { flow, calls, current } = setup();
  const loading = flow.load(20);
  calls[0].d.resolve({ payloads: [meta(6)] });
  await new Promise((r) => setImmediate(r));
  current.caseId = "B";
  await flow.select(6);
  assert.equal(calls.filter((c) => c.name === "payload").length, 1);
  flow.reset();
  calls[1].d.resolve({ payload: meta(6), text: "x" });
  await loading;
  assert.equal(flow.state.detail.status, "none");
});

test("pair is fetched on demand and closed again", async () => {
  const { flow, calls } = setup();
  const loading = flow.load(20);
  calls[0].d.resolve({ payloads: [meta(6, { pair_id: 7 })] });
  await new Promise((r) => setImmediate(r));
  calls[1].d.resolve({ payload: meta(6, { pair_id: 7 }), text: "req", formatted: null, pair: meta(7, { role: "response" }) });
  await loading;
  const opening = flow.togglePair();
  assert.equal(flow.state.pair.open, true);
  assert.equal(calls[2].id, 7);
  calls[2].d.resolve({ payload: meta(7), text: "resp", formatted: null, pair: null });
  await opening;
  assert.equal(flow.state.pair.data.text, "resp");
  await flow.togglePair();
  assert.equal(flow.state.pair.open, false);
});

test("mode only accepts known values", () => {
  const { flow } = setup();
  flow.setMode("bogus");
  assert.equal(flow.state.mode, "formatted");
  flow.setMode("raw");
  assert.equal(flow.state.mode, "raw");
});

test("notice flow caches level windows per case and hides on errors", async () => {
  const results = [];
  let windowCalls = 0;
  let payloadCalls = 0;
  const api = {
    payloadPage: async (caseId, params) => { payloadCalls += 1; assert.deepEqual(params, { event: 4, limit: 1 }); return { payloads: [] }; },
    events: async (caseId, params) => {
      windowCalls += 1;
      assert.equal(params.kind, "level_window");
      return { events: [{ ts_start: 0, ts_end: null, stream_id: null, detail: { level_name: "Info", level_num: 5 } }], next: null };
    },
  };
  const flow = createPayloadNoticeFlow({ api, getCase: () => "A", onResult: (n) => results.push(n) });
  const operation = { id: 4, kind: "operation", ts_start: 10, ts_end: 20, stream_id: null };
  await flow.check(operation);
  await flow.check(operation);
  assert.equal(windowCalls, 1);
  assert.equal(payloadCalls, 2);
  assert.equal(results[1].kind, "absent");
  flow.clearCache();
  await flow.check(operation);
  assert.equal(windowCalls, 2);
  await flow.check({ id: 5, kind: "gap" });
  assert.equal(results[results.length - 1], null);
  assert.equal(payloadCalls, 3);
});

test("notice flow drops a stale answer", async () => {
  const results = [];
  const gate = deferred();
  const api = {
    payloadPage: () => gate.promise,
    events: async () => ({ events: [], next: null }),
  };
  const flow = createPayloadNoticeFlow({ api, getCase: () => "A", onResult: (n) => results.push(n) });
  const op = { id: 4, kind: "operation", ts_start: 10, ts_end: 20 };
  const pending = flow.check(op);
  flow.cancel();
  gate.resolve({ payloads: [] });
  await pending;
  assert.deepEqual(results, []);
});

test("list params: comma lists, orphan flag, keyset cursor", () => {
  assert.deepEqual(buildPayloadParams({ role: "request", peer: " WFM , CRS ,", operation: " Op ", q: "", orphan: true }, "5:1", 50),
    { limit: 50, role: "request", peer: "WFM,CRS", operation: "Op", orphan: 1, after: "5:1" });
  assert.deepEqual(buildPayloadParams({}, null, 10), { limit: 10 });
});

test("list rows show orphans with their reason", () => {
  const row = buildPayloadRow({ id: 1, entry_id: 2, event_id: null, reason: "malformed", role: "request", char_len: 12000, ts: 0, sensitive: false });
  assert.equal(row.orphan, true);
  assert.equal(row.note, "the payload is malformed");
  assert.equal(row.size, "12,000");
  assert.equal(buildPayloadRow({ id: 1, entry_id: 2, event_id: 5, reason: "x" }).note, "");
});

test("list flow: first page, load more, stale after close", async () => {
  const calls = [];
  const api = { payloadPage: (caseId, params, signal) => { const d = deferred(); calls.push({ params, signal, d }); return d.promise; } };
  const flow = createPayloadListFlow({ api, getCase: () => "A", onState: () => {}, limit: 2 });
  const first = flow.refresh();
  calls[0].d.resolve({ payloads: [meta(1), meta(2)], next: "c" });
  await first;
  const more = flow.loadMore();
  flow.loadMore();
  assert.equal(calls.length, 2);
  assert.equal(calls[1].params.after, "c");
  calls[1].d.resolve({ payloads: [meta(2), meta(3)], next: null });
  await more;
  assert.deepEqual(flow.state.payloads.map((p) => p.id), [1, 2, 3]);
  const again = flow.setFilters({ orphan: true });
  assert.equal(calls[2].params.orphan, 1);
  flow.close();
  calls[2].d.resolve({ payloads: [meta(9)], next: null });
  await again;
  assert.deepEqual(flow.state.payloads, []);
  assert.equal(flow.state.status, "idle");
});
