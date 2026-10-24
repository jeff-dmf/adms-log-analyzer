// The "Operations and handoffs" section of the same-key panel: rows, order, summary, failure, several collections,
// truncation text and the loading flow (stale answers dropped). Names are synthetic.
import test from "node:test";
import assert from "node:assert/strict";

import { buildChain, chainProcessLabel, chainSummary, chainTruncatedText, createChainFlow } from "../../src/dmslog/web/js/key-chain-model.js";

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

const op = (id, name, ts, extra = {}) => ({
  id, kind: "operation", name, key: "100001", status: "ok", ts_start: ts, ts_end: ts + 100, duration_ms: 100, n_entries: 3,
  start_entry_id: id * 10, family: "DMSJOB", process: "Proc_A", pool: null, instance: null, collection: 1, ...extra,
});
const side = (ts, process, extra = {}) => ({
  entry_id: 5, ts, stream_id: 1, event_id: null, family: "DMSJOB", process, pool: null, instance: null, tpl_code: "X-1", collection: 1, ...extra,
});
const link = (from, to, dt, confidence = 3) => ({ kind: "handoff", confidence, dt_ms: dt, from, to });

const three = () => ({
  key: { type: "job_id", value: "100001" },
  operations: [
    op(3, "FunctionHostJob", 2000, { process: "DMS_FunctionHost_7", pool: "DMS_FunctionHost", instance: 7, duration_ms: 1500, ts_end: 3500 }),
    op(1, "SchedulerJob", 1000),
    op(2, "LauncherJob", 1500),
  ],
  handoffs: [
    link(side(1500, "Proc_A", { entry_id: 11 }), side(1500, "Proc_B", { entry_id: 12 }), 0),
    link(side(2000, "Proc_B", { entry_id: 21 }), side(2004, "DMS_FunctionHost_7", { entry_id: 92, pool: "DMS_FunctionHost", instance: 7 }), 4, 2),
  ],
  scanned: 16,
  truncated: false,
});

test("rows are in time order and a handoff comes before the operation it starts", () => {
  const chain = buildChain(three());
  assert.deepEqual(chain.rows.map((r) => r.kind + ":" + r.step), [
    "operation:SchedulerJob", "handoff:Handoff", "operation:LauncherJob", "handoff:Handoff", "operation:FunctionHostJob",
  ]);
  const handoff = chain.rows[3];
  assert.equal(handoff.process, "Proc_B → DMS_FunctionHost #7");
  assert.equal(handoff.statusText, "ts+TID");
  assert.equal(handoff.duration, "4 ms");
  assert.equal(handoff.entryId, 92);
  assert.equal(handoff.entryTs, 2004);
  const fh = chain.rows[4];
  assert.equal(fh.process, "DMS_FunctionHost #7");
  assert.equal(fh.duration, "1.5 s");
  assert.equal(fh.records, "3");
  assert.deepEqual(fh.target, { from: 2000, to: 3500, entryId: 30 });
  assert.equal(fh.eventId, 3);
});

test("summary counts with plurals and gives the span from first to last", () => {
  const chain = buildChain(three());
  assert.equal(chain.summary, "3 operations, 2 handoffs, 2.5 s from first to last");
  const one = buildChain({ operations: [op(1, "SchedulerJob", 1000)], handoffs: [] });
  assert.equal(one.summary, "1 operation, 0 handoffs, 100 ms from first to last");
  const bare = buildChain({ operations: [op(1, "SchedulerJob", 1000, { ts_end: null, duration_ms: null })], handoffs: [] });
  assert.equal(bare.summary, "1 operation, 0 handoffs");
  assert.equal(chainSummary([], false), "");
});

test("a failed operation is named by its step and process, the first one in time order", () => {
  const data = three();
  data.operations[0].status = "fail";
  data.operations[2].status = "timeout";
  const chain = buildChain(data);
  assert.equal(chain.failure, "Failed at LauncherJob (Proc_A)");
  data.operations[2].status = "ok";
  assert.equal(buildChain(data).failure, "Failed at FunctionHostJob (DMS_FunctionHost #7)");
  data.operations[0].status = "ok";
  assert.equal(buildChain(data).failure, "");
  const failed = buildChain({ operations: [op(1, "X", 1, { status: "business_error" })], handoffs: [] });
  assert.equal(failed.rows[0].statusText.length > 0, true);
  assert.equal(failed.rows[0].tone, "warn");
});

test("an open operation has no duration and still a status text", () => {
  const chain = buildChain({ operations: [op(1, "X", 1, { status: "open", duration_ms: null, ts_end: null })], handoffs: [] });
  assert.equal(chain.rows[0].duration, "");
  assert.notEqual(chain.rows[0].statusText, "");
});

test("several collections: C<n> prefix, grouped by collection, no aggregate span", () => {
  const data = {
    operations: [op(1, "SchedulerJob", 5000, { collection: 2 }), op(2, "SchedulerJob", 9000, { collection: 1 })],
    handoffs: [],
  };
  const chain = buildChain(data);
  assert.equal(chain.multi, true);
  assert.deepEqual(chain.rows.map((r) => r.process), ["C1 · Proc_A", "C2 · Proc_A"]);
  assert.equal(chain.summary, "2 operations, 0 handoffs");
  assert.equal(buildChain(three()).multi, false);
});

test("empty and malformed answers give no rows", () => {
  assert.deepEqual(buildChain({}).rows, []);
  assert.deepEqual(buildChain(null).rows, []);
  assert.deepEqual(buildChain({ operations: [null], handoffs: [{ from: null, to: null }, null] }).rows, []);
  assert.equal(buildChain({ operations: [], handoffs: [] }).summary, "");
});

test("an operation or process name from the log stays literal", () => {
  const evil = "<img src=x onerror=alert(1)> {name}";
  const chain = buildChain({ operations: [op(1, evil, 1, { process: evil })], handoffs: [] });
  assert.equal(chain.rows[0].step, evil);
  assert.equal(chain.rows[0].process, evil);
  assert.equal(chainProcessLabel({ process: "P", pool: "Pool", instance: 3 }), "Pool #3");
  assert.equal(chainProcessLabel({ process: "P", pool: "Pool", instance: null }), "P");
  assert.equal(chainProcessLabel(null), "");
});

test("the truncation notice names the number of records read", () => {
  assert.equal(chainTruncatedText(5000), "Only the first 5000 records with this key were read; the list may be incomplete.");
});

function setup() {
  const calls = [];
  const current = { caseId: "A" };
  const states = [];
  const api = {
    keyChain(caseId, type, value, params, signal) {
      const d = deferred();
      calls.push({ caseId, type, value, params, signal, d });
      return d.promise;
    },
  };
  const flow = createChainFlow({ api, getCase: () => current.caseId, onState: (s) => states.push(s.status) });
  return { flow, calls, current, states };
}

test("flow: loading then ready; the key goes raw to the api", async () => {
  const { flow, calls, states } = setup();
  const opening = flow.open("job_id", "A/B 1");
  assert.equal(flow.state.status, "loading");
  assert.equal(calls[0].value, "A/B 1");
  calls[0].d.resolve({ operations: [], handoffs: [] });
  await opening;
  assert.equal(flow.state.status, "ready");
  assert.deepEqual(states, ["idle", "loading", "ready"]);
});

test("flow: a newer key or a close drops the old answer and aborts it", async () => {
  const { flow, calls } = setup();
  const first = flow.open("job_id", "1");
  const second = flow.open("job_id", "2");
  assert.equal(calls[0].signal.aborted, true);
  calls[0].d.resolve({ operations: [op(1, "Old", 1)], handoffs: [] });
  await first;
  assert.equal(flow.state.data, null);
  calls[1].d.resolve({ operations: [op(2, "New", 1)], handoffs: [] });
  await second;
  assert.equal(flow.state.data.operations[0].name, "New");
  const third = flow.open("job_id", "3");
  flow.close();
  calls[2].d.resolve({ operations: [op(3, "Late", 1)], handoffs: [] });
  await third;
  assert.equal(flow.state.status, "idle");
  assert.equal(flow.state.data, null);
});

test("flow: an error is kept, an aborted request is not an error, no case does nothing", async () => {
  const { flow, calls, current } = setup();
  const failing = flow.open("job_id", "1");
  calls[0].d.reject(new Error("boom"));
  await failing;
  assert.equal(flow.state.status, "error");
  assert.equal(flow.state.error, "boom");
  const again = flow.open("job_id", "2");
  flow.close();
  calls[1].d.reject(Object.assign(new Error("aborted"), { name: "AbortError" }));
  await again;
  assert.equal(flow.state.status, "idle");
  current.caseId = null;
  await flow.open("job_id", "3");
  assert.equal(calls.length, 2);
  assert.equal(flow.state.status, "idle");
});
