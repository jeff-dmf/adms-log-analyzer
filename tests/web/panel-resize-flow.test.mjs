import assert from "node:assert/strict";
import { test } from "node:test";
import { createPanelResize } from "../../src/dmslog/web/js/panel-resize.js";
import { STORAGE_KEY } from "../../src/dmslog/web/js/panel-resize-model.js";

class Target {
  constructor() { this.listeners = new Map(); this.attrs = {}; this.classes = new Set(); this.style = { props: new Map(), setProperty(n, v) { this.props.set(n, v); }, removeProperty(n) { this.props.delete(n); } }; }
  addEventListener(type, fn) { this.listeners.set(type, [...(this.listeners.get(type) || []), fn]); }
  removeEventListener(type, fn) { this.listeners.set(type, (this.listeners.get(type) || []).filter((f) => f !== fn)); }
  fire(type, event = {}) { for (const fn of this.listeners.get(type) || []) { fn({ preventDefault() {}, ...event }); } }
  setAttribute(n, v) { this.attrs[n] = v; }
  get classList() { return { add: (c) => this.classes.add(c), remove: (c) => this.classes.delete(c) }; }
  setPointerCapture() {}
}

function setup(stored, { observers = false } = {}) {
  const mem = new Map(stored ? [[STORAGE_KEY, JSON.stringify(stored)]] : []);
  const storage = { getItem: (k) => mem.get(k) ?? null, setItem: (k, v) => mem.set(k, v) };
  const panels = Object.assign(new Target(), { clientWidth: 2000, getBoundingClientRect: () => ({ left: 0, right: 2000 }) });
  const els = { panels, "side-resize": new Target(), "detail-resize": new Target(), "sources-panel": Object.assign(new Target(), { offsetWidth: 248 }), "detail-panel": Object.assign(new Target(), { offsetWidth: 380 }) };
  const doc = { getElementById: (id) => els[id] || null };
  const win = new Target();
  const observed = [];
  if (observers) {
    win.ResizeObserver = class { constructor(callback) { observed.push(callback); } observe() {} disconnect() {} };
  }
  const resize = createPanelResize({ doc, win, storage });
  return { els, mem, win, resize, observed };
}

test("the dividers are separators with a name and the limits", () => {
  const { els } = setup();
  assert.equal(els["side-resize"].attrs.role, "separator");
  assert.equal(els["detail-resize"].attrs["aria-label"], "Resize the record detail panel");
  assert.equal(els["detail-resize"].attrs["aria-valuenow"], "380");
  assert.equal(els["detail-resize"].attrs["aria-valuemin"], "280");
});

test("dragging the detail edge sets its width and remembers it on release", () => {
  const { els, mem } = setup();
  const handle = els["detail-resize"];
  handle.fire("pointerdown", { button: 0, pointerId: 1 });
  assert.ok(handle.classes.has("dragging"));
  handle.fire("pointermove", { clientX: 1500 });
  assert.equal(els.panels.style.props.get("--detail-w"), "500px");
  assert.equal(els.panels.style.props.get("--detail-w-rail"), "500px", "the rail layout uses it too");
  assert.equal(mem.has(STORAGE_KEY), false, "not written while dragging");
  handle.fire("pointerup", {});
  assert.deepEqual(JSON.parse(mem.get(STORAGE_KEY)), { detail: 500 });
  handle.fire("pointermove", { clientX: 100 });
  assert.equal(els.panels.style.props.get("--detail-w"), "500px", "the drag is over");
});

test("the sources edge follows the pointer from the left and respects the limits", () => {
  const { els } = setup();
  const handle = els["side-resize"];
  handle.fire("pointerdown", { button: 0, pointerId: 1 });
  handle.fire("pointermove", { clientX: 320 });
  assert.equal(els.panels.style.props.get("--side-w"), "320px");
  handle.fire("pointermove", { clientX: 10 });
  assert.equal(els.panels.style.props.get("--side-w"), "180px");
});

test("keys resize, Home and a double click go back to the default", () => {
  const { els, mem } = setup();
  const handle = els["detail-resize"];
  handle.fire("keydown", { key: "ArrowLeft" });
  assert.equal(els.panels.style.props.get("--detail-w"), "396px");
  assert.deepEqual(JSON.parse(mem.get(STORAGE_KEY)), { detail: 396 });
  handle.fire("keydown", { key: "Home" });
  assert.equal(els.panels.style.props.has("--detail-w"), false);
  handle.fire("keydown", { key: "ArrowLeft" });
  handle.fire("dblclick", {});
  assert.equal(els.panels.style.props.has("--detail-w"), false);
  assert.deepEqual(JSON.parse(mem.get(STORAGE_KEY)), {});
});

test("a narrow window holds the widths back without losing the choice", () => {
  const { els, win, mem, resize } = setup({ side: 480, detail: 800 });
  els.panels.clientWidth = 700;
  win.fire("resize", {});
  assert.equal(els.panels.style.props.get("--detail-w"), "280px", "held back to the minimum");
  els.panels.clientWidth = 2400;
  win.fire("resize", {});
  assert.equal(els.panels.style.props.get("--side-w"), "480px", "maximized: the choice is back");
  assert.equal(els.panels.style.props.get("--detail-w"), "800px");
  els.panels.clientWidth = 700;
  win.fire("resize", {});
  assert.deepEqual(JSON.parse(mem.get(STORAGE_KEY)), { side: 480, detail: 800 }, "narrowing the window writes nothing");
  assert.deepEqual(resize.widths(), { side: 480, detail: 800 }, "the choice is what is kept");
});

test("expanding a collapsed panel leaves the records their room", () => {
  const { els, observed } = setup({ side: 480, detail: 800 }, { observers: true });
  els.panels.clientWidth = 1600;
  els["sources-panel"].offsetWidth = 0; // collapsed
  els["detail-panel"].offsetWidth = 800;
  observed.forEach((callback) => callback());
  assert.equal(els.panels.style.props.get("--detail-w"), "800px");
  els["sources-panel"].offsetWidth = 480; // expanded again: the detail gives way
  observed.forEach((callback) => callback());
  assert.equal(els.panels.style.props.get("--detail-w"), String(1600 - 480 - 360) + "px");
});

test("remembered widths are applied at start and held back when the window gets narrow", () => {
  const { els, win } = setup({ side: 300, detail: 700 });
  assert.equal(els.panels.style.props.get("--side-w"), "300px");
  assert.equal(els.panels.style.props.get("--detail-w"), "700px");
  els.panels.clientWidth = 1200;
  win.fire("resize", {});
  assert.equal(els.panels.style.props.get("--detail-w"), String(1200 - 248 - 360) + "px");
});
