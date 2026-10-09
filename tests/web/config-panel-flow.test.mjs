// The configuration panel flow with a fake api: no DOM, no real requests.
import test from "node:test";
import assert from "node:assert/strict";

import { createConfigFlow } from "../../src/dmslog/web/js/config-panel-model.js";

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

function setup() {
  const calls = [];
  const states = [];
  const current = { caseId: "A" };
  const api = {
    configProducts(caseId) {
      const d = deferred();
      calls.push({ kind: "products", caseId, d });
      return d.promise;
    },
    config(caseId, product, group) {
      const d = deferred();
      calls.push({ kind: "config", caseId, product, group, d });
      return d.promise;
    },
  };
  const flow = createConfigFlow({
    api, getCase: () => current.caseId, onState: (s) => states.push(JSON.parse(JSON.stringify(s))),
  });
  return { flow, calls, states, current };
}

test("changing the case clears table and products before the new requests", async () => {
  const { flow, calls, current } = setup();
  const first = flow.refresh();
  calls[0].d.resolve({ products: [{ product: "P", group: 0 }] });
  await Promise.resolve();
  await Promise.resolve();
  calls[1].d.resolve({ product: "P", keys: [] });
  await first;
  assert.equal(flow.state.status, "ready");
  assert.notEqual(flow.state.config, null);

  current.caseId = "B";
  const second = flow.refresh();
  assert.equal(flow.state.config, null);
  assert.deepEqual(flow.state.products, []);
  assert.equal(flow.state.status, "loading");
  assert.equal(flow.state.caseId, "B");
  calls[2].d.resolve({ products: [] });
  await second;
  assert.equal(flow.state.status, "empty");
});

test("stale responses of the previous case are dropped", async () => {
  const { flow, calls, current } = setup();
  const first = flow.refresh(); // case A, products pending
  current.caseId = "B";
  const second = flow.refresh(); // case B supersedes it
  calls[1].d.resolve({ products: [{ product: "B1", group: 0 }] });
  await Promise.resolve();
  await Promise.resolve();
  calls[0].d.resolve({ products: [{ product: "A1", group: 0 }] }); // late answer of A
  await first;
  assert.deepEqual(flow.state.products.map((p) => p.product), ["B1"]);
  calls[2].d.resolve({ product: "B1", keys: [], marker: "B" });
  await second;
  assert.equal(flow.state.config.marker, "B");
});

test("a late config of the old case never replaces the new state", async () => {
  const { flow, calls, current } = setup();
  const first = flow.refresh();
  calls[0].d.resolve({ products: [{ product: "P", group: 0 }] });
  await Promise.resolve();
  await Promise.resolve();
  current.caseId = "B";
  const second = flow.refresh();
  calls[1].d.resolve({ product: "P", keys: [], marker: "A" }); // answer of A arrives late
  await first;
  assert.equal(flow.state.config, null);
  calls[2].d.resolve({ products: [] });
  await second;
  assert.equal(flow.state.config, null);
  assert.equal(flow.state.caseId, "B");
});

test("no case selected clears everything", async () => {
  const { flow, current } = setup();
  current.caseId = null;
  await flow.refresh();
  assert.equal(flow.state.status, "nocase");
  assert.equal(flow.state.config, null);
});
