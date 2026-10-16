// Pure model of the resizable grid columns: limits, keyboard, fit and persistence.
import test from "node:test";
import assert from "node:assert/strict";

import {
  BIG_STEP, ColumnWidths, FIT_MAX_WIDTH, MAX_WIDTH, MIN_WIDTH, STEP, STORAGE_KEY, clampWidth, defaultWidth,
  fitWidth, keyAction,
} from "../../src/dmslog/web/js/column-resize-model.js";

function fakeStorage(initial = {}) {
  const data = new Map(Object.entries(initial));
  return {
    data,
    getItem: (k) => (data.has(k) ? data.get(k) : null),
    setItem: (k, v) => data.set(k, String(v)),
    removeItem: (k) => data.delete(k),
  };
}

const brokenStorage = {
  getItem() { throw new Error("blocked"); },
  setItem() { throw new Error("blocked"); },
  removeItem() { throw new Error("blocked"); },
};

test("clamp keeps widths inside the limits and survives junk", () => {
  assert.equal(clampWidth(5), MIN_WIDTH);
  assert.equal(clampWidth(99999), MAX_WIDTH);
  assert.equal(clampWidth(120.4), 120);
  assert.equal(clampWidth("abc"), MIN_WIDTH);
  assert.equal(clampWidth(NaN), MIN_WIDTH);
});

test("defaults: fixed columns, msg and dynamic fields", () => {
  assert.equal(defaultWidth("ts"), 96); // ceil(12ch of the nominal 7.2px) + 8px of padding + 1px
  assert.equal(defaultWidth("fields.Foo"), 140);
  assert.equal(defaultWidth("constructor"), 140); // not an inherited property
});

test("fit takes the widest content, adds slack and caps", () => {
  assert.equal(fitWidth([50, 120, 80]), 124);
  assert.equal(fitWidth([]), MIN_WIDTH);
  assert.equal(fitWidth([5000]), FIT_MAX_WIDTH);
  assert.equal(fitWidth([NaN, undefined, 100.2]), 105);
});

test("keyboard: arrows, shift, enter, home and end", () => {
  assert.deepEqual(keyAction("ArrowRight", false), { delta: STEP });
  assert.deepEqual(keyAction("ArrowLeft", false), { delta: -STEP });
  assert.deepEqual(keyAction("ArrowRight", true), { delta: BIG_STEP });
  assert.deepEqual(keyAction("ArrowLeft", true), { delta: -BIG_STEP });
  assert.deepEqual(keyAction("Enter", false), { fit: true });
  assert.deepEqual(keyAction("Home", false), { to: "min" });
  assert.deepEqual(keyAction("End", false), { to: "max" });
  assert.equal(keyAction("a", false), null);
});

test("widths persist per column name and come back on a new instance", () => {
  const storage = fakeStorage();
  const a = new ColumnWidths(storage);
  assert.equal(a.get("msg"), 300);
  a.set("msg", 640);
  a.adjust("ts", STEP);
  a.set("fields.Foo", 5); // clamped
  const b = new ColumnWidths(storage);
  assert.equal(b.get("msg"), 640);
  assert.equal(b.get("ts"), 96 + STEP);
  assert.equal(b.get("fields.Foo"), MIN_WIDTH);
  assert.equal(b.get("source"), 153); // 20ch + padding
});

test("dragging does not write until persisted", () => {
  const storage = fakeStorage();
  const a = new ColumnWidths(storage);
  a.set("msg", 500, false);
  assert.equal(storage.data.has(STORAGE_KEY), false);
  a.save();
  assert.equal(JSON.parse(storage.data.get(STORAGE_KEY)).msg, 500);
});

test("corrupt or hostile stored data is ignored or sanitized", () => {
  for (const raw of ["not json", "[1,2]", "null", '{"msg":"wide"}']) {
    const w = new ColumnWidths(fakeStorage({ [STORAGE_KEY]: raw }));
    assert.equal(w.get("msg"), 300);
  }
  const w = new ColumnWidths(fakeStorage({ [STORAGE_KEY]: '{"ts":99999,"__proto__":7,"pid":1e999}' }));
  assert.equal(w.get("ts"), MAX_WIDTH);
  assert.equal(w.get("pid"), 53);
});

test("unavailable storage: works for the session, never throws", () => {
  for (const storage of [brokenStorage, null]) {
    const w = new ColumnWidths(storage);
    assert.equal(w.get("ts"), 96);
    w.set("ts", 250);
    assert.equal(w.get("ts"), 250);
    w.reset();
    assert.equal(w.get("ts"), 96);
  }
});

test("reset forgets every width and clears the storage", () => {
  const storage = fakeStorage();
  const w = new ColumnWidths(storage);
  w.set("msg", 700);
  w.reset();
  assert.equal(w.get("msg"), 300);
  assert.equal(storage.data.has(STORAGE_KEY), false);
});

test("the stored set is capped, dropping the oldest", () => {
  const storage = fakeStorage();
  const w = new ColumnWidths(storage);
  for (let i = 0; i < 250; i += 1) {
    w.set("fields.f" + i, 100 + (i % 50), false);
  }
  w.save();
  const stored = JSON.parse(storage.data.get(STORAGE_KEY));
  assert.equal(Object.keys(stored).length, 200);
  assert.equal("fields.f0" in stored, false);
  assert.equal("fields.f249" in stored, true);
});

test("total sums the visible columns", () => {
  const w = new ColumnWidths(null);
  w.set("ts", 100, false);
  assert.equal(w.total(["ts", "pid", "msg"]), 100 + 53 + 300);
});

test("UI-16: default widths are in ch of the measured character; a dragged px width wins and survives a new measure", () => {
  const storage = fakeStorage();
  const w = new ColumnWidths(storage);
  assert.equal(w.setCharPx(9), true);
  assert.equal(w.get("ts"), 117);
  assert.equal(w.get("pid"), 63);
  assert.equal(w.get("level"), 90);
  assert.equal(w.get("source"), 189);
  assert.equal(w.get("collection"), 54);
  w.set("source", 333); // dragged: stored in px
  w.setCharPx(10);
  assert.equal(w.get("source"), 333);
  assert.equal(new ColumnWidths(storage).get("source"), 333);
  assert.equal(w.get("ts"), 129);
});

test("UI-16: a failed character measurement (0, NaN, null, negative) keeps the previous value", () => {
  const w = new ColumnWidths(null);
  w.setCharPx(8);
  for (const bad of [0, NaN, null, undefined, -3, Infinity, "8"]) {
    assert.equal(w.setCharPx(bad), false);
    assert.equal(w.charPx, 8);
  }
  assert.equal(defaultWidth("ts", 0), 96);
  assert.equal(defaultWidth("ts", NaN), 96);
});

test("UI-16: the time column of a multi-day case is 18ch, and back to 12ch when the override is removed", () => {
  const w = new ColumnWidths(null);
  w.setCharPx(8);
  w.setDefaultCh("ts", 18);
  assert.equal(w.get("ts"), 153);
  w.set("ts", 150);
  assert.equal(w.get("ts"), 150); // the user's width still wins
  w.reset();
  w.setDefaultCh("ts", null);
  assert.equal(w.get("ts"), 105);
});
