// UI-15: the Grid owns "the selected row stays visible": its ResizeObserver (a dock opening, dragging or maximizing
// all arrive as a size change of the viewport) moves the top row through topAfterResize, and a height of 0 (the dock
// is maximized and hides the grid) recalculates nothing.
import test from "node:test";
import assert from "node:assert/strict";

function fakeElement() {
  return {
    style: {}, dataset: {}, children: [], hidden: false, clientHeight: 440, scrollTop: 0, className: "",
    classList: { toggle() {}, add() {}, remove() {} },
    addEventListener() {}, appendChild(c) { this.children.push(c); return c; },
    setAttribute() {}, focus() {}, removeAttribute() {},
    set textContent(v) { this.children = []; this._t = v; }, get textContent() { return this._t || ""; },
  };
}
globalThis.document = { createElement: fakeElement, createTextNode: (t) => ({ textContent: t }) };
const observers = [];
globalThis.ResizeObserver = class {
  constructor(callback) { this.callback = callback; observers.push(this); }
  observe() {}
};

const { Grid } = await import("../../src/dmslog/web/js/grid.js");

function setup() {
  const viewport = fakeElement();
  const grid = new Grid({
    viewport, rowsLayer: fakeElement(), spacer: fakeElement(), header: fakeElement(),
    onSelect() {}, onCount() {}, onPosition() {}, onError(err) { throw err; },
  });
  const rows = Array.from({ length: 300 }, (_, i) => ({ id: i + 1, ts: 1000 + i, sev: 1, level: 3, pid: 1, tid: 1, source_id: 1, message: "m" + i }));
  grid.window.load(rows, null, null, 100);
  grid.window.atStart = true;
  grid.window.atEnd = true; // nothing to fetch
  grid.pump = () => {};
  grid.render();
  const resize = (height) => {
    viewport.clientHeight = height;
    observers.at(-1).callback([{ contentRect: { height } }]);
  };
  return { grid, viewport, resize };
}

test("a selected row that is still inside the smaller grid keeps its distance from the top", () => {
  const { grid, resize } = setup();
  resize(440); // 22 rows
  grid.window.selected = 103;
  resize(200); // 10 rows: row 103 is the 4th
  assert.equal(grid.window.top, 100);
});

test("a selected row that would be covered by the dock moves to a third of the new height", () => {
  const { grid, resize } = setup();
  resize(440);
  grid.window.selected = 118; // visible in 22 rows (100..121), not in 10
  resize(200);
  assert.equal(grid.window.top, 118 - 3); // floor(10 / 3)
  assert.ok(grid.window.selected >= grid.window.top && grid.window.selected < grid.window.top + 10);
});

test("maximizing the dock (height 0) leaves the top alone and restoring the same height changes nothing", () => {
  const { grid, resize } = setup();
  resize(440);
  grid.window.selected = 105;
  resize(0);
  assert.equal(grid.window.top, 100);
  resize(440);
  assert.equal(grid.window.top, 100);
  resize(200); // the known height was 440 before the 0: the comparison still works
  assert.equal(grid.window.top, 100);
});

test("without a selection the top stays whatever the height", () => {
  const { grid, resize } = setup();
  resize(440);
  resize(160);
  assert.equal(grid.window.top, 100);
});

test("ensureSelectedVisible is public and idempotent: calling it again with the same height changes nothing", () => {
  const { grid, resize } = setup();
  resize(440);
  grid.window.selected = 118;
  resize(200);
  const top = grid.window.top;
  grid.ensureSelectedVisible(200);
  grid.ensureSelectedVisible(200);
  assert.equal(grid.window.top, top);
});
