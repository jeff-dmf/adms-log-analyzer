// UI-29: the one hook the context menu needs in the grid (js/grid.js, onContextMenu): which record a right click or the
// Menu key / Shift+F10 means, that the record is selected like a click does (without asking for its detail: the owner shows
// it), and that nothing else is touched (no layout read for a plain right click, the browser's own menu where there is no record).
import test from "node:test";
import assert from "node:assert/strict";

function fakeElement() {
  const el = {
    style: {}, dataset: {}, children: [], hidden: false, clientHeight: 440, scrollTop: 0, className: "", handlers: {}, focused: 0,
    classList: { toggle() {} },
    addEventListener(type, fn) { (el.handlers[type] ??= []).push(fn); },
    appendChild(c) { this.children.push(c); return c; },
    setAttribute() {}, focus() { el.focused += 1; },
    getBoundingClientRect: () => ({ left: 10, top: 20, right: 510, bottom: 460 }),
    set textContent(v) { this.children = []; this._t = v; }, get textContent() { return this._t || ""; },
  };
  return el;
}
globalThis.document = { createElement: fakeElement };

const { Grid } = await import("../../src/dmslog/web/js/grid.js");

function setup({ withHook = true } = {}) {
  const requests = [];
  const viewport = fakeElement();
  const grid = new Grid({
    viewport, rowsLayer: fakeElement(), spacer: fakeElement(), header: fakeElement(),
    onSelect() { throw new Error("the grid must not ask for the detail on a context menu"); },
    onError(err) { throw err; },
    onContextMenu: withHook ? (request) => requests.push(request) : undefined,
  });
  grid.window.rows = [{ id: 10 }, { id: 11 }, { id: 12 }];
  grid.window.selected = 0;
  let renders = 0;
  grid.render = () => { renders += 1; };
  const slot = (index, bottom) => ({
    el: { hidden: false, dataset: { index: String(index) }, getBoundingClientRect: () => ({ left: 100, top: bottom - 24, right: 600, bottom }) },
  });
  grid.pool = [slot(0, 44), slot(1, 68), slot(2, 92)];
  const fire = (event) => {
    const e = { button: 0, clientX: 0, clientY: 0, prevented: false, preventDefault() { this.prevented = true; }, ...event };
    for (const fn of viewport.handlers.contextmenu ?? []) fn(e);
    return e;
  };
  const onRow = (index) => ({ closest: (sel) => (sel === ".grid-row" ? { dataset: { index: String(index) } } : null) });
  const onViewport = () => ({ closest: () => null });
  return { grid, viewport, requests, fire, onRow, onViewport, renders: () => renders };
}

test("a right click on a record selects it and asks for its menu at the pointer", () => {
  const t = setup();
  const e = t.fire({ target: t.onRow(2), button: 2, clientX: 300, clientY: 150 });
  assert.equal(e.prevented, true, "the browser's menu is replaced");
  assert.deepEqual(t.requests, [{ row: { id: 12 }, x: 300, y: 150, keyboard: false }]);
  assert.equal(t.grid.window.selected, 2);
  assert.equal(t.renders(), 1);
  assert.equal(t.grid.selectTimer, null, "no detail request from the grid: the owner shows the record");
  assert.equal(t.viewport.focused, 1, "the grid keeps the focus, like after a click");
});

test("a right click on the record that is already selected does not redraw the rows", () => {
  const t = setup();
  t.fire({ target: t.onRow(0), button: 2, clientX: 5, clientY: 6 });
  assert.equal(t.renders(), 0);
  assert.equal(t.requests.length, 1);
});

test("the Menu key or Shift+F10 (the event targets the grid, it is not a right click) means the selected record, under its row", () => {
  const t = setup();
  t.grid.window.selected = 1;
  const e = t.fire({ target: t.onViewport(), button: 0 });
  assert.equal(e.prevented, true);
  assert.deepEqual(t.requests, [{ row: { id: 11 }, x: 124, y: 68, keyboard: true }]);
  assert.equal(t.renders(), 0);
});

test("the anchor falls back to the corner of the grid when the selected row is not in the pool", () => {
  const t = setup();
  t.grid.window.selected = 1;
  t.grid.pool = [];
  t.fire({ target: t.onViewport(), button: 0 });
  assert.deepEqual(t.requests, [{ row: { id: 11 }, x: 34, y: 20, keyboard: true }]);
  t.grid.pool = [{ el: { hidden: true, dataset: { index: "1" }, getBoundingClientRect: () => ({ left: 0, top: 0, right: 0, bottom: 0 }) } }];
  assert.deepEqual(t.grid.selectedAnchor(), { x: 34, y: 20 }, "a hidden pool row is not the row");
});

test("outside the records the browser's own menu stays: a right click on empty space, no selection, an unknown row, no hook", () => {
  const t = setup();
  assert.equal(t.fire({ target: t.onViewport(), button: 2 }).prevented, false);
  t.grid.window.selected = -1;
  assert.equal(t.fire({ target: t.onViewport(), button: 0 }).prevented, false);
  assert.equal(t.fire({ target: t.onRow(9), button: 2 }).prevented, false, "a row the window does not hold");
  assert.deepEqual(t.requests, []);
  const none = setup({ withHook: false });
  assert.equal(none.fire({ target: none.onRow(1), button: 2 }).prevented, false);
});

test("a target without closest() (a text node) is treated as the grid itself", () => {
  const t = setup();
  t.fire({ target: {}, button: 0 });
  assert.equal(t.requests.length, 1);
  assert.equal(t.requests[0].keyboard, true);
});

test("the menu takes the selection over: a pending 'show the selected record' is cancelled (pointer and keyboard)", () => {
  for (const [pick, button] of [[(t) => t.onRow(1), 2], [(t) => t.onViewport(), 0]]) {
    const t = setup();
    t.grid.selectTimer = setTimeout(() => { throw new Error("the pending selection fired"); }, 5);
    t.fire({ target: pick(t), button });
    assert.equal(t.grid.selectTimer, null);
    assert.equal(t.requests.length, 1);
  }
});

test("holdSelection() cancels the pending notification and is safe to call twice", async () => {
  const t = setup();
  let fired = false;
  t.grid.selectTimer = setTimeout(() => { fired = true; }, 1);
  t.grid.holdSelection();
  t.grid.holdSelection();
  await new Promise((resolve) => setTimeout(resolve, 10));
  assert.equal(fired, false);
});
