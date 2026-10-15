// UI-15: the pure rules of the bottom dock: minimums, the initial height, the divider keys, the remembered height
// and the tab list.
import test from "node:test";
import assert from "node:assert/strict";

import {
  DOCK_MIN_PX, GRID_MIN_ROWS, STORAGE_KEY, doubleClickTarget, dragHeight, formatStored, fractionOf, initialHeight,
  keyStep, limits, nextTab, parseStored, shouldStartMaximized, tabsAfterClose, valueNow,
} from "../../src/dmslog/web/js/dock-model.js";
import { ROW_HEIGHT } from "../../src/dmslog/web/js/grid-model.js";

const HEAD = 42; // grid header 22 px plus the viewport chrome

test("the grid keeps 8 rows plus its header: the dock gets what is left, never less than 160 px", () => {
  const l = limits({ available: 600, rowHeight: ROW_HEIGHT, gridHead: HEAD });
  assert.equal(l.min, DOCK_MIN_PX);
  assert.equal(l.max, 600 - (HEAD + GRID_MIN_ROWS * ROW_HEIGHT));
  assert.equal(l.maximizedOnly, false);
  const tight = limits({ available: 340, rowHeight: ROW_HEIGHT, gridHead: HEAD }); // 340 - 202 = 138 < 160
  assert.equal(tight.maximizedOnly, true);
  assert.ok(tight.max >= tight.min, "the clamp range is never inverted");
  assert.equal(limits({ available: 362, rowHeight: ROW_HEIGHT, gridHead: HEAD }).maximizedOnly, false); // exactly 160 fits
  assert.equal(limits({ available: 361, rowHeight: ROW_HEIGHT, gridHead: HEAD }).maximizedOnly, true);
});

test("1366x768: a dock of the largest allowed height still leaves 8 grid rows", () => {
  const available = 768 - 30 - 22 - 185 - 79 - 22 - 24; // window - bar - status - timeline - filter - header - misc
  const l = limits({ available, rowHeight: ROW_HEIGHT, gridHead: HEAD });
  assert.ok(!l.maximizedOnly);
  const gridRows = Math.floor((available - l.max - HEAD) / ROW_HEIGHT);
  assert.ok(gridRows >= GRID_MIN_ROWS, gridRows + " rows");
});

test("the initial height follows the content: clamp(160, content, 40%); a remembered fraction wins", () => {
  const bounds = { min: 160, max: 400, maximizedOnly: false };
  const base = { available: 800, limits: bounds };
  assert.equal(initialHeight({ ...base, storedFraction: null, contentHeight: 90 }), 160); // short content: the minimum
  assert.equal(initialHeight({ ...base, storedFraction: null, contentHeight: 250 }), 250);
  assert.equal(initialHeight({ ...base, storedFraction: null, contentHeight: 900 }), 320); // 40% of 800
  assert.equal(initialHeight({ ...base, storedFraction: 0.3, contentHeight: 900 }), 240);
  assert.equal(initialHeight({ ...base, storedFraction: 0.9, contentHeight: 900 }), 400); // limited to the maximum
  assert.equal(initialHeight({ ...base, storedFraction: 0.1, contentHeight: 900 }), 160); // and to the minimum
  assert.equal(initialHeight({ available: 300, limits: { min: 160, max: 200, maximizedOnly: false }, storedFraction: null, contentHeight: 500 }), 160);
});

test("divider keys: arrows move 5% of the space, Home is the smallest, End the largest, all inside the limits", () => {
  const ctx = { available: 800, limits: { min: 160, max: 400, maximizedOnly: false } };
  assert.equal(keyStep(240, "ArrowUp", ctx), 280); // 5% of 800 = 40
  assert.equal(keyStep(240, "ArrowDown", ctx), 200);
  assert.equal(keyStep(390, "ArrowUp", ctx), 400);
  assert.equal(keyStep(170, "ArrowDown", ctx), 160);
  assert.equal(keyStep(240, "Home", ctx), 160);
  assert.equal(keyStep(240, "End", ctx), 400);
  assert.equal(keyStep(240, "x", ctx), 240);
});

test("dragging up makes the dock taller and stays inside the limits", () => {
  const bounds = { min: 160, max: 400, maximizedOnly: false };
  assert.equal(dragHeight({ startHeight: 200, startY: 500, y: 460, limits: bounds }), 240);
  assert.equal(dragHeight({ startHeight: 200, startY: 500, y: 0, limits: bounds }), 400);
  assert.equal(dragHeight({ startHeight: 200, startY: 500, y: 900, limits: bounds }), 160);
});

test("aria-valuenow is a percentage of the shared space", () => {
  assert.equal(valueNow(240, 800), 30);
  assert.equal(valueNow(1000, 800), 100);
  assert.equal(valueNow(10, 0), 0);
  assert.equal(fractionOf(240, 800), 0.3);
});

test("the remembered height is a fraction between 0.1 and 1; anything else is nothing", () => {
  assert.equal(STORAGE_KEY, "dmslog.dockHeight");
  assert.equal(parseStored("0.42"), 0.42);
  assert.equal(parseStored("1"), 1);
  assert.equal(parseStored("0.05"), null);
  assert.equal(parseStored("1.5"), null);
  assert.equal(parseStored("abc"), null);
  assert.equal(parseStored(""), null);
  assert.equal(parseStored(null), null);
  assert.equal(parseStored("0.4; DROP"), null);
  assert.equal(formatStored(0.123456), "0.123");
  assert.equal(parseStored(formatStored(0.4)), 0.4);
});

test("a short window opens the dock maximized", () => {
  assert.equal(shouldStartMaximized(639), true);
  assert.equal(shouldStartMaximized(640), false);
  assert.equal(shouldStartMaximized(900), false);
});

test("double click on the divider: maximized -> 40%, otherwise -> maximized", () => {
  const bounds = { min: 160, max: 400, maximizedOnly: false };
  assert.deepEqual(doubleClickTarget({ maximized: false, available: 800, limits: bounds }), { maximized: true, height: null });
  assert.deepEqual(doubleClickTarget({ maximized: true, available: 800, limits: bounds }), { maximized: false, height: 320 });
  assert.equal(doubleClickTarget({ maximized: true, available: 500, limits: bounds }).height, 200);
});

test("closing a tab activates its right neighbour, else the left one; another tab keeps the active one", () => {
  assert.deepEqual(tabsAfterClose(["a", "b", "c"], "b", "b"), { tabs: ["a", "c"], active: "c" });
  assert.deepEqual(tabsAfterClose(["a", "b", "c"], "c", "c"), { tabs: ["a", "b"], active: "b" });
  assert.deepEqual(tabsAfterClose(["a", "b", "c"], "a", "a"), { tabs: ["b", "c"], active: "b" });
  assert.deepEqual(tabsAfterClose(["a", "b", "c"], "a", "c"), { tabs: ["a", "b"], active: "a" });
  assert.deepEqual(tabsAfterClose(["a"], "a", "a"), { tabs: [], active: null });
  assert.deepEqual(tabsAfterClose(["a", "b"], "a", "zz"), { tabs: ["a", "b"], active: "a" });
});

test("roving tabindex: arrows wrap around, Home and End jump, other keys do nothing", () => {
  const tabs = ["a", "b", "c"];
  assert.equal(nextTab(tabs, "a", "ArrowRight"), "b");
  assert.equal(nextTab(tabs, "c", "ArrowRight"), "a");
  assert.equal(nextTab(tabs, "a", "ArrowLeft"), "c");
  assert.equal(nextTab(tabs, "b", "Home"), "a");
  assert.equal(nextTab(tabs, "b", "End"), "c");
  assert.equal(nextTab(tabs, "b", "x"), null);
  assert.equal(nextTab([], "a", "ArrowRight"), null);
});
