import test from "node:test";
import assert from "node:assert/strict";
import {
  RowWindow, fetchPlan, jumpCursor, makeCursor, parseCursor, scrollToTs, tsToScroll,
} from "../../src/dmslog/web/js/grid-model.js";

const rows = (start, count) => Array.from({ length: count }, (_, i) => ({ id: start + i, ts: 1000 + start + i }));

test("cursors are <ts>:<id> and parse back", () => {
  assert.equal(makeCursor({ ts: 1790000000000, id: 42 }), "1790000000000:42");
  assert.deepEqual(parseCursor("1790000000000:42"), { ts: 1790000000000, id: 42 });
  assert.equal(jumpCursor(1790000000000.4), "1790000000000:-1");
  assert.deepEqual(parseCursor(jumpCursor(5)), { ts: 5, id: -1 });
  assert.equal(parseCursor("garbage"), null);
});

test("scroll position maps proportionally to the time range and back", () => {
  const range = { min: 1000, max: 2000 };
  assert.equal(tsToScroll(1000, range, 100), 0);
  assert.equal(tsToScroll(1500, range, 100), 50);
  assert.equal(tsToScroll(2000, range, 100), 100);
  assert.equal(tsToScroll(5000, range, 100), 100);
  assert.equal(scrollToTs(25, range, 100), 1250);
  assert.equal(scrollToTs(0, null, 100), null);
  assert.equal(tsToScroll(10, { min: 5, max: 5 }, 100), 0);
});

test("fetchPlan asks for pages near the buffer edges only", () => {
  assert.equal(fetchPlan(0, 30, 0, false, false, 60), null);
  assert.equal(fetchPlan(0, 30, 200, true, false, 60), null);
  assert.equal(fetchPlan(120, 30, 200, true, false, 60), "fwd");
  assert.equal(fetchPlan(120, 30, 200, true, true, 60), null);
  assert.equal(fetchPlan(10, 30, 200, false, true, 60), "back");
  assert.equal(fetchPlan(100, 30, 400, false, false, 60), null);
});

test("load sets the edge flags from the cursors", () => {
  const w = new RowWindow(1000);
  w.load(rows(0, 10), null, "next");
  assert.equal(w.atStart, true);
  assert.equal(w.atEnd, false);
  w.load(rows(0, 10), "prev", null);
  assert.equal(w.atStart, false);
  assert.equal(w.atEnd, true);
});

test("append trims the head and keeps the viewport on the same rows", () => {
  const w = new RowWindow(100);
  w.load(rows(0, 80), null, "n1");
  w.moveTop(70, 10);
  w.append(rows(80, 60), "n2");
  assert.equal(w.rows.length, 100);
  assert.equal(w.rows[w.top].id, 70);
  assert.equal(w.atStart, false);
  assert.equal(w.prevCursor, makeCursor(w.rows[0]));
  assert.equal(w.nextCursor, "n2");
});

test("prepend shifts top and selection and trims the tail", () => {
  const w = new RowWindow(100);
  w.load(rows(100, 80), "p1", "n1");
  w.moveSelection(3, 10);
  w.prepend(rows(60, 40), "p2");
  assert.equal(w.rows.length, 100);
  assert.equal(w.rows[w.selected].id, 103);
  assert.equal(w.rows[w.top].id, 100);
  assert.equal(w.atEnd, false);
  assert.equal(w.nextCursor, makeCursor(w.rows[99]));
  assert.equal(w.prevCursor, "p2");
});

test("append with an empty page marks the end", () => {
  const w = new RowWindow(100);
  w.load(rows(0, 5), null, "n1");
  w.append([], "n2");
  assert.equal(w.atEnd, true);
});

test("moveTop clamps and selection scrolls into view", () => {
  const w = new RowWindow(1000);
  w.load(rows(0, 50), null, null);
  assert.equal(w.moveTop(-5, 10), 0);
  assert.equal(w.moveTop(500, 10), 40);
  w.top = 0;
  w.moveSelection(15, 10);
  assert.equal(w.selected, 15);
  assert.equal(w.top, 6);
  w.moveSelection(-100, 10);
  assert.equal(w.selected, 0);
  assert.equal(w.top, 0);
  w.moveSelection(1000, 10);
  assert.equal(w.selected, 49);
  assert.equal(w.top, 40);
});
