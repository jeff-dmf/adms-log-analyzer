// The adapter dashboard flow with a fake api: no DOM, no real requests.
import test from "node:test";
import assert from "node:assert/strict";

import { createDashboardFlow } from "../../src/dmslog/web/js/dashboard-model.js";

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

const tick = () => new Promise((resolve) => setImmediate(resolve));

function setup() {
  const calls = [];
  const current = { caseId: "A" };
  const make = (name) => (caseId, params, signal) => {
    const d = deferred();
    calls.push({ name, caseId, params, signal, d });
    // Like fetch: an aborted request rejects.
    signal.addEventListener("abort", () => d.reject(Object.assign(new Error("aborted"), { name: "AbortError" })));
    return d.promise;
  };
  const api = {
    eventStats: make("stats"),
    eventHistogram: make("histogram"),
    statsMethods: make("methods"),
    statsSeries: make("series"),
    events: make("events"),
  };
  const flow = createDashboardFlow({ api, getCase: () => current.caseId, onState: () => {} });
  const find = (name, pred = () => true) => calls.filter((c) => c.name === name && pred(c));
  return { flow, calls, current, find };
}

async function resolveAll(find, caseId, extra = {}) {
  find("stats").forEach((c) => c.d.resolve(extra.stats || { groups: [], totals: null }));
  find("histogram").forEach((c) => c.d.resolve({ buckets: [] }));
  find("methods").forEach((c) => c.d.resolve(extra.methods || { methods: [] }));
  find("events").forEach((c) => c.d.resolve({ events: [], next: null }));
  await tick();
}

test("refresh sends the case everywhere and fills every section", async () => {
  const { flow, calls, find } = setup();
  const loading = flow.refresh();
  assert.equal(flow.state.status, "loading");
  assert.ok(calls.every((c) => c.caseId === "A"));
  assert.deepEqual(find("stats")[0].params, { kind: "operation" });
  assert.deepEqual(find("events").map((c) => c.params.kind).sort(), ["exception", "gap", "level_window", "restart", "startup"]);
  await resolveAll(find, "A", { methods: { methods: [{ stream_id: 7, family: "Wfm", side: "in", method: "M", calls: 3, last_ts: 5 }] } });
  assert.deepEqual(find("series")[0].params, { stream: 7, side: "in", method: "M" });
  find("series")[0].d.resolve({ samples: [{ ts: 1, calls: 1, d_calls: null }] });
  await loading;
  assert.equal(flow.state.status, "ready");
  assert.equal(flow.state.stats.status, "ready");
  assert.equal(flow.state.series.status, "ready");
  assert.equal(flow.state.series.data.length, 1);
  assert.equal(flow.state.lifecycle.data.length, 3);
});

test("one failing section does not blank the others", async () => {
  const { flow, find } = setup();
  const loading = flow.refresh();
  find("stats")[0].d.reject(new Error("boom"));
  find("histogram")[0].d.resolve({ buckets: [] });
  find("methods")[0].d.resolve({ methods: [] });
  find("events").forEach((c) => c.d.resolve({ events: [{ id: 1, kind: c.params.kind, ts_start: 1 }], next: "more" }));
  await loading;
  assert.equal(flow.state.stats.status, "error");
  assert.equal(flow.state.stats.error, "boom");
  assert.equal(flow.state.exceptions.status, "ready");
  assert.equal(flow.state.exceptions.more, true);
  assert.equal(flow.state.series.status, "none");
});

test("changing the case clears the sections and drops answers of the old case", async () => {
  const { flow, current, find, calls } = setup();
  const first = flow.refresh();
  await tick();
  current.caseId = "B";
  const second = flow.refresh();
  assert.equal(flow.state.caseId, "B");
  assert.equal(flow.state.stats.status, "loading");
  assert.equal(flow.state.stats.data, null);
  assert.equal(find("stats")[0].signal.aborted, true);
  find("stats")[0].d.resolve({ groups: [{ name: "old" }], totals: null }); // late answer of A
  find("events", (c) => c.caseId === "A").forEach((c) => c.d.resolve({ events: [{ id: 1 }], next: null }));
  await first;
  assert.equal(flow.state.stats.data, null);
  assert.equal(flow.state.exceptions.data, null);
  await resolveAll((name, pred) => find(name, (c) => c.caseId === "B" && (pred ? pred(c) : true)), "B");
  await second;
  assert.equal(flow.state.stats.data.groups.length, 0);
  assert.ok(calls.some((c) => c.caseId === "B"));
});

test("choosing an operation reloads only the histogram; a stale answer is dropped", async () => {
  const { flow, find } = setup();
  const loading = flow.refresh();
  await resolveAll(find, "A");
  await loading;
  const before = find("histogram").length;
  const a = flow.setHistogramName("Op1");
  const b = flow.setHistogramName("Op2");
  assert.equal(find("histogram").length, before + 2);
  assert.deepEqual(find("histogram")[before].params, { kind: "operation", name: "Op1" });
  assert.equal(find("histogram")[before].signal.aborted, true);
  find("histogram")[before].d.resolve({ bucket: "minute", bucket_ms: 60000, buckets: [{ t: 1, total: 0, counts: {} }], truncated: false }); // stale
  await a;
  assert.equal(flow.state.histogram.status, "loading");
  find("histogram")[before + 1].d.resolve({ bucket: "minute", bucket_ms: 60000, buckets: [{ t: 2, total: 0, counts: {} }], truncated: false });
  await b;
  assert.equal(flow.state.histName, "Op2");
  assert.equal(flow.state.histogram.data.buckets[0].t, 2);
  const c = flow.setHistogramName("");
  assert.deepEqual(find("histogram")[before + 2].params, { kind: "operation" });
  find("histogram")[before + 2].d.resolve({ buckets: [] });
  await c;
});

test("choosing another method loads its samples; an older answer is dropped", async () => {
  const { flow, find } = setup();
  const loading = flow.refresh();
  await resolveAll(find, "A", { methods: { methods: [{ stream_id: 3, family: "Wfm", side: "in", method: "M1", calls: 1, last_ts: 1 }, { stream_id: 9, family: "Ont", side: "out", method: "M2", calls: 2, last_ts: 2 }] } });
  find("series")[0].d.resolve({ samples: [{ ts: 1 }] });
  await loading;
  const a = flow.selectMethod(1);
  const b = flow.selectMethod(0);
  assert.deepEqual(find("series")[1].params, { stream: 9, side: "out", method: "M2" });
  find("series")[1].d.resolve({ samples: [{ ts: 99 }] }); // stale
  await a;
  assert.equal(flow.state.series.status, "loading");
  find("series")[2].d.resolve({ samples: [{ ts: 5 }] });
  await b;
  assert.equal(flow.state.series.data[0].ts, 5);
  assert.equal(flow.state.methodIndex, 0);
});

test("selecting a method of another stream requests that stream", async () => {
  const { flow, find } = setup();
  const loading = flow.refresh();
  await resolveAll(find, "A", {
    methods: {
      methods: [
        { stream_id: 1, family: "Wfm", side: "in", method: "Same", calls: 100, last_ts: 1 },
        { stream_id: 2, family: "Wfm", side: "in", method: "Same", calls: 10, last_ts: 1 },
      ],
    },
  });
  find("series")[0].d.resolve({ samples: [] });
  await loading;
  assert.equal(find("series")[0].params.stream, 1);
  const pending = flow.selectMethod(1);
  assert.deepEqual(find("series")[1].params, { stream: 2, side: "in", method: "Same" });
  find("series")[1].d.resolve({ samples: [] });
  await pending;
});

test("without a case nothing is requested", async () => {
  const { flow, current, calls } = setup();
  current.caseId = null;
  await flow.refresh();
  assert.equal(flow.state.status, "nocase");
  assert.equal(calls.length, 0);
  await flow.selectMethod(0);
  await flow.setHistogramName("x");
  assert.equal(calls.length, 0);
});
