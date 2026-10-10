// The grid's count has its own generation: navigation must not discard it, a reload must reset the baseline.
import test from "node:test";
import assert from "node:assert/strict";

function fakeElement() {
  return {
    style: {}, dataset: {}, children: [], hidden: false, clientHeight: 440, scrollTop: 0, className: "",
    classList: { toggle() {} },
    addEventListener() {}, appendChild(c) { this.children.push(c); return c; },
    setAttribute() {}, focus() {},
    set textContent(v) { this.children = []; this._t = v; }, get textContent() { return this._t || ""; },
  };
}
globalThis.document = { createElement: fakeElement };

const { Grid } = await import("../../src/dmslog/web/js/grid.js");
const { api } = await import("../../src/dmslog/web/js/api.js");

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

function setup() {
  const counts = [];
  api.count = (caseId, filter, signal) => {
    const d = deferred();
    counts.push({ caseId, signal, d });
    return d.promise;
  };
  api.page = async () => ({ rows: [], prev: null, next: null });
  const statuses = [];
  const grid = new Grid({
    viewport: fakeElement(), rowsLayer: fakeElement(), spacer: fakeElement(), header: fakeElement(),
    onSelect() {}, onStatus: (t) => statuses.push(t), onError(err) { throw err; },
  });
  return { grid, counts, statuses };
}

test("the count answer survives the navigation that follows a reload", async () => {
  const { grid, counts } = setup();
  await grid.setView({ caseId: "A", filter: null, range: null }); // reload: count first, then jump
  assert.equal(counts.length, 1);
  counts[0].d.resolve({ count: 321, exact: true });
  await Promise.resolve();
  await Promise.resolve();
  assert.equal(grid.total, 321);
});

test("a case switch resets the baseline and drops the old answer", async () => {
  const { grid, counts } = setup();
  await grid.setView({ caseId: "A", filter: null, range: null });
  counts[0].d.resolve({ count: 100, exact: true });
  await Promise.resolve();
  await Promise.resolve();
  assert.equal(grid.total, 100);
  await grid.setView({ caseId: "B", filter: null, range: null });
  assert.equal(grid.total, null);
  assert.equal(counts[0].signal.aborted, false); // already answered
  counts[1].d.resolve({ count: 7, exact: true });
  await Promise.resolve();
  await Promise.resolve();
  assert.equal(grid.total, 7);
});

test("a late answer of the previous filter is dropped", async () => {
  const { grid, counts } = setup();
  await grid.setView({ caseId: "A", filter: null, range: null });
  await grid.setView({ caseId: "A", filter: { and: [] }, range: null });
  assert.equal(counts[0].signal.aborted, true);
  counts[0].d.resolve({ count: 999, exact: true });
  counts[1].d.resolve({ count: 5, exact: true });
  await Promise.resolve();
  await Promise.resolve();
  assert.equal(grid.total, 5);
});
