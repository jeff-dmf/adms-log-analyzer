// The grid's count has its own generation: navigation must not discard it, a reload must reset the baseline.
import test from "node:test";
import assert from "node:assert/strict";

function fakeElement() {
  return {
    style: {}, dataset: {}, children: [], hidden: false, clientHeight: 440, scrollTop: 0, className: "",
    classList: { toggle() {} },
    addEventListener() {}, appendChild(c) { this.children.push(c); return c; },
    setAttribute() {}, removeAttribute() {}, focus() {},
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
  const counts2 = [];
  const positions = [];
  const grid = new Grid({
    viewport: fakeElement(), rowsLayer: fakeElement(), spacer: fakeElement(), header: fakeElement(),
    onSelect() {}, onCount: (v, o) => counts2.push([v, o]), onPosition: (ts) => positions.push(ts), onError(err) { throw err; },
  });
  return { grid, counts, emitted: counts2, positions };
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

test("the grid reports the count and the position through onCount and onPosition", async () => {
  const { grid, counts, emitted, positions } = setup();
  await grid.setView({ caseId: "A", filter: null, range: null });
  assert.deepEqual(emitted[0], ["loading", undefined]); // the reload dims the old number
  counts[0].d.resolve({ count: 321, exact: true });
  await Promise.resolve();
  await Promise.resolve();
  assert.deepEqual(emitted.at(-1), [{ n: 321, exact: true }, { announce: true }]); // came from a reload: announced
  grid.refreshCount(); // a later refresh (follow tail) is silent
  counts[1].d.resolve({ count: 400, exact: false });
  await Promise.resolve();
  await Promise.resolve();
  assert.deepEqual(emitted.at(-1), [{ n: 400, exact: false }, { announce: false }]);
  grid.window.load([{ id: 1, ts: 5000, sev: 1, nlines: 1 }], null, null, 0);
  grid.updateStatus();
  assert.equal(positions.at(-1), 5000);
  await grid.setView({ caseId: null, filter: null, range: null });
  assert.equal(emitted.at(-1)[0], null);
  assert.equal(positions.at(-1), null);
});

test("showTotal (Follow tail) shows the newest total without announcing; not without a case or during a view change", async () => {
  const { grid, counts, emitted } = setup();
  assert.equal(grid.showTotal(5, true), false); // no case yet
  await grid.setView({ caseId: "A", filter: null, range: null });
  counts[0].d.resolve({ count: 100, exact: true });
  await Promise.resolve();
  await Promise.resolve();
  const before = emitted.length;
  assert.equal(grid.showTotal(130, true), true);
  assert.equal(grid.total, 130);
  assert.deepEqual(emitted.at(-1)[0], { n: 130, exact: true });
  assert.equal(emitted.at(-1)[1], undefined); // no { announce }
  assert.equal(emitted.length, before + 1);
  assert.equal(grid.showTotal("x", true), false);
  assert.equal(grid.total, 130);
  grid.pending = { caseId: "A" }; // a view change is waiting for its page: it owns the count
  assert.equal(grid.showTotal(200, true), false);
  assert.equal(grid.total, 130);
});
