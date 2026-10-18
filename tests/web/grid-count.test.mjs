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

test("showTotal shares the count order: an older count answer cannot overwrite a newer total", async () => {
  const { grid, counts } = setup();
  await grid.setView({ caseId: "A", filter: null, range: null });
  counts[0].d.resolve({ count: 2000, exact: true });
  await Promise.resolve();
  await Promise.resolve();
  const older = grid.refreshCount(); // requested before the total below
  assert.equal(grid.showTotal(2300, true), true);
  counts[1].d.resolve({ count: 2100, exact: true });
  await older;
  assert.equal(grid.total, 2300);
});

test("followTail stops before counting when its operation became obsolete", async () => {
  const { grid, counts } = setup();
  await grid.setView({ caseId: "A", filter: null, range: null });
  counts[0].d.resolve({ count: 10, exact: true });
  await Promise.resolve();
  let alive = true;
  const page = deferred();
  api.page = () => page.promise;
  const running = grid.followTail({ isCurrent: () => alive });
  alive = false; // the tab hid while the end was loading
  page.resolve({ rows: [], prev: null, next: null });
  assert.equal(await running, false);
  assert.equal(counts.length, 1); // no /count was requested
});

test("followTail drops the count answer when the operation became obsolete while it was in flight", async () => {
  const { grid, counts } = setup();
  await grid.setView({ caseId: "A", filter: null, range: null });
  counts[0].d.resolve({ count: 10, exact: true });
  await Promise.resolve();
  api.page = async () => ({ rows: [], prev: null, next: null });
  let alive = true;
  const running = grid.followTail({ isCurrent: () => alive });
  for (let i = 0; i < 10 && counts.length < 2; i += 1) await Promise.resolve();
  assert.equal(counts.length, 2);
  alive = false;
  counts[1].d.resolve({ count: 999, exact: true });
  assert.equal(await running, false);
  assert.equal(grid.total, 10);
});

test("a move by the user while followTail loads the end prevails: no rows replaced, no count", async () => {
  const { grid, counts } = setup();
  await grid.setView({ caseId: "A", filter: null, range: null });
  counts[0].d.resolve({ count: 10, exact: true });
  await Promise.resolve();
  const page = deferred();
  let loaded = 0;
  api.page = () => { loaded += 1; return page.promise; };
  const original = grid.window.load.bind(grid.window);
  let replaced = 0;
  grid.window.load = (...args) => { replaced += 1; return original(...args); };
  const running = grid.followTail();
  grid.moveRows(-3); // the user scrolls during the load
  page.resolve({ rows: [], prev: null, next: null });
  assert.equal(await running, false);
  assert.ok(loaded >= 1);
  assert.equal(replaced, 0);
  assert.equal(counts.length, 1);
});

test("a normal followTail (no user move) completes and counts", async () => {
  const { grid, counts } = setup();
  await grid.setView({ caseId: "A", filter: null, range: null });
  counts[0].d.resolve({ count: 10, exact: true });
  await Promise.resolve();
  api.page = async () => ({ rows: [], prev: null, next: null });
  const running = grid.followTail();
  for (let i = 0; i < 10 && counts.length < 2; i += 1) await Promise.resolve();
  counts[1].d.resolve({ count: 12, exact: true });
  assert.equal(await running, true);
  assert.equal(grid.total, 12);
});

function tailSetup() {
  const t = setup();
  return t;
}

test("an obsolete operation does not apply the end page (window.load) nor show its failure", async () => {
  const { grid, counts } = tailSetup();
  await grid.setView({ caseId: "A", filter: null, range: null });
  counts[0].d.resolve({ count: 10, exact: true });
  await Promise.resolve();
  const page = deferred();
  api.page = () => page.promise;
  let replaced = 0;
  const original = grid.window.load.bind(grid.window);
  grid.window.load = (...args) => { replaced += 1; return original(...args); };
  let alive = true;
  const running = grid.followTail({ isCurrent: () => alive });
  alive = false;
  page.resolve({ rows: [{ id: 1, ts: 1 }], prev: null, next: null });
  assert.equal(await running, false);
  assert.equal(replaced, 0);
  // the same for a failure: nothing is shown for an obsolete operation
  const failing = deferred();
  api.page = () => failing.promise;
  let alive2 = true;
  const running2 = grid.followTail({ isCurrent: () => alive2 });
  alive2 = false;
  failing.promise.catch(() => {});
  failing.resolve(Promise.reject(new Error("503")));
  assert.equal(await running2, false);
  assert.equal(grid.failure, null);
});

test("an older load that ends does not clear the protection of the newer one", async () => {
  const { grid, counts } = tailSetup();
  await grid.setView({ caseId: "A", filter: null, range: null });
  counts[0].d.resolve({ count: 10, exact: true });
  await Promise.resolve();
  const pages = [];
  api.page = (c, f, cur, dir, limit, signal) => { const d = deferred(); pages.push({ d, signal }); return d.promise; };
  let replaced = 0;
  const original = grid.window.load.bind(grid.window);
  grid.window.load = (...args) => { replaced += 1; return original(...args); };
  const first = grid.followTail({ isCurrent: () => true });
  const second = grid.followTail({ isCurrent: () => true }); // overlaps: the first one is superseded
  pages[0].d.resolve({ rows: [], prev: null, next: null });
  assert.equal(await first, false);
  assert.ok(grid.tailOp !== null); // the second still owns it
  grid.moveRows(-2); // the user scrolls
  assert.equal(pages[1].signal.aborted, true);
  pages[1].d.resolve({ rows: [], prev: null, next: null });
  assert.equal(await second, false);
  assert.equal(replaced, 0);
});

test("a late count failure of an obsolete operation does not erase the count nor register a failure", async () => {
  const { grid, counts } = tailSetup();
  await grid.setView({ caseId: "A", filter: null, range: null });
  counts[0].d.resolve({ count: 10, exact: true });
  await Promise.resolve();
  await Promise.resolve();
  api.page = async () => ({ rows: [], prev: null, next: null });
  let alive = true;
  const running = grid.followTail({ isCurrent: () => alive });
  for (let i = 0; i < 10 && counts.length < 2; i += 1) await Promise.resolve();
  assert.equal(counts.length, 2);
  alive = false;
  counts[1].d.reject(new Error("503"));
  assert.equal(await running, false);
  assert.equal(grid.countFailure, null);
  assert.equal(grid.total, 10);
  assert.equal(grid.showTotal(11, true), true); // not blocked by a failure that never was
});

test("a count requested by Follow tail before a reload cannot overwrite the reload's newer total", async () => {
  const { grid, counts } = tailSetup();
  await grid.setView({ caseId: "A", filter: null, range: null });
  counts[0].d.resolve({ count: 1000, exact: true });
  await Promise.resolve();
  await Promise.resolve();
  const id = grid.beginCount(); // the flow asks /count (answer still in flight)
  const reload = grid.refreshCount(); // a reload (same case, same filter) counts after it
  counts[1].d.resolve({ count: 1200, exact: true });
  await reload;
  assert.equal(grid.total, 1200);
  assert.equal(grid.showTotal(1100, true, id), false); // the old answer arrives late
  assert.equal(grid.total, 1200);
  assert.equal(grid.showTotal(1300, true, grid.beginCount()), true);
  assert.equal(grid.total, 1300);
});
