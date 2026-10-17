// UI-26: the grid is readable by a screen reader. The viewport is the grid (rowcount/colcount, the header owned by it, the
// selected row as aria-activedescendant); rows have no aria-label (it would replace their cells); bookmark and highlight
// are hidden text in the first cell. Attributes only: nothing here reads layout.
import test from "node:test";
import assert from "node:assert/strict";
import { El, makeDoc } from "./fixtures/fake-dom.mjs";

const doc = makeDoc();
doc.createTextNode = (text) => Object.assign(new El(doc, "#text"), { _text: String(text) });
globalThis.document = doc;

const { Grid } = await import("../../src/dmslog/web/js/grid.js");
const { api } = await import("../../src/dmslog/web/js/api.js");

function node(id) {
  const el = new El(doc, "div");
  el.id = id;
  Object.defineProperty(el, "clientHeight", { value: 5 * 22, configurable: true }); // a few rows visible
  return el;
}

function makeRows(from, to) {
  return Array.from({ length: to - from + 1 }, (_, i) => ({ id: from + i, ts: 1000 + from + i, sev: 1, level: 3, pid: 1, tid: 1, source_id: 1, msg: "m" + (from + i), nlines: 1 }));
}

function setup({ marks = {}, highlights = {}, repeats = {}, count = { count: 500, exact: true } } = {}) {
  api.count = async () => count;
  api.page = async () => ({ rows: makeRows(1, 200), prev: null, next: "n" });
  const header = node("grid-header");
  const viewport = node("grid-viewport");
  const rowsLayer = node("grid-rows");
  const grid = new Grid({
    viewport, rowsLayer, spacer: node("s"), header, onSelect() {}, onCount() {}, onPosition() {}, onError(err) { throw err; },
    getMark: (id) => marks[id] || null, getHighlight: (id) => highlights[id] || null, getRepeat: (id) => repeats[id] || null,
  });
  grid.pump = () => {};
  return { grid, viewport, header, rowsLayer };
}

const shown = (rowsLayer) => rowsLayer.children.filter((r) => !r.hidden);

test("the viewport is the grid: owned header, rowgroup of rows, counts", async () => {
  const { grid, viewport, header, rowsLayer } = setup();
  await grid.setView({ caseId: "A", filter: null, range: null });
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(viewport.attrs["aria-owns"], "grid-header");
  assert.equal(rowsLayer.attrs.role, "rowgroup");
  assert.equal(viewport.attrs["aria-rowcount"], "501", "total plus the header row");
  assert.equal(viewport.attrs["aria-colcount"], String(grid.columnDefs().length));
  assert.equal(header.attrs["aria-rowindex"], "1");
  for (const row of shown(rowsLayer)) {
    assert.equal(row.attrs["aria-label"], undefined);
    assert.equal(row.attrs["aria-rowindex"], String(Number(row.dataset.index) + 2));
  }
});

test("an estimated or smaller-than-loaded count gives -1", async () => {
  const a = setup({ count: { count: 500, exact: false } });
  await a.grid.setView({ caseId: "A", filter: null, range: null });
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(a.viewport.attrs["aria-rowcount"], "-1");
  const b = setup({ count: { count: 50, exact: true } }); // fewer than the 200 loaded: stale, so unknown
  await b.grid.setView({ caseId: "A", filter: null, range: null });
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(b.viewport.attrs["aria-rowcount"], "-1");
  b.grid.shownCount = { n: 500, exact: true };
  b.grid.emitCount(b.grid.shownCount);
  assert.equal(b.viewport.attrs["aria-rowcount"], "501", "the count arriving after the rows updates the grid");
});

test("rowindex only while the window starts at the first record", async () => {
  const { grid, viewport, header, rowsLayer } = setup();
  await grid.setView({ caseId: "A", filter: null, range: null });
  await new Promise((r) => setTimeout(r, 0));
  grid.window.atStart = false; // the head was trimmed: positions are unknown
  grid.window.top = 50;
  grid.render();
  assert.equal(header.attrs["aria-rowindex"], undefined);
  for (const row of shown(rowsLayer)) {
    assert.equal(row.attrs["aria-rowindex"], undefined);
  }
  assert.equal(viewport.attrs["aria-rowcount"], "501");
  grid.window.atStart = true;
  grid.window.top = 3;
  grid.render();
  assert.equal(header.attrs["aria-rowindex"], "1");
  assert.ok(shown(rowsLayer).every((r) => r.attrs["aria-rowindex"] === String(Number(r.dataset.index) + 2)));
});

test("aria-activedescendant follows the selection, also after scrolling and across slot reuse", async () => {
  const { grid, viewport, rowsLayer } = setup();
  await grid.setView({ caseId: "A", filter: null, range: null });
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(viewport.attrs["aria-activedescendant"], undefined, "nothing selected yet");
  const selectedEl = () => rowsLayer.children.find((r) => r.attrs["aria-selected"] === "true" && !r.hidden);
  const idsUnique = () => new Set(rowsLayer.children.map((r) => r.id)).size === rowsLayer.children.length;
  grid.window.selected = 2;
  grid.render();
  assert.equal(viewport.attrs["aria-activedescendant"], selectedEl().id);
  assert.ok(idsUnique());
  // Move down past the bottom edge: the window scrolls by one with the selection, so the selected row stays in the same slot.
  let previous = viewport.attrs["aria-activedescendant"];
  for (let i = 0; i < 12; i += 1) {
    grid.onKey({ key: "ArrowDown", preventDefault() {} });
    const now = viewport.attrs["aria-activedescendant"];
    assert.equal(now, selectedEl().id, "points at the selected row at step " + i);
    assert.notEqual(now, previous, "changes with every selected record (step " + i + ")");
    assert.ok(idsUnique());
    previous = now;
  }
  assert.equal(Number(selectedEl().dataset.index), grid.window.selected);
  // The selection scrolls out of the rendered rows: no dangling reference.
  grid.window.top = 100;
  grid.render();
  assert.equal(viewport.attrs["aria-activedescendant"], undefined);
  // And back: it points at the (re-rendered) row again.
  grid.window.top = Math.max(0, grid.window.selected - 1);
  grid.render();
  assert.equal(viewport.attrs["aria-activedescendant"], selectedEl().id);
});

test("a rebuilt pool (column change) keeps activedescendant valid", async () => {
  const { grid, viewport, rowsLayer } = setup();
  await grid.setView({ caseId: "A", filter: null, range: null });
  await new Promise((r) => setTimeout(r, 0));
  grid.window.selected = 1;
  grid.render();
  const before = viewport.attrs["aria-activedescendant"];
  grid.setColumns(["ts", "msg"]);
  assert.equal(viewport.attrs["aria-colcount"], "2");
  const after = viewport.attrs["aria-activedescendant"];
  assert.notEqual(after, before, "new elements get new ids");
  assert.ok(rowsLayer.children.some((r) => r.id === after && r.attrs["aria-selected"] === "true"));
});

test("bookmark and highlight are hidden text in the first cell; the cells stay readable", async () => {
  const { grid, rowsLayer } = setup({ marks: { 2: { color: "blue" } }, highlights: { 3: { color: "pink", label: "rule x" } } });
  await grid.setView({ caseId: "A", filter: null, range: null });
  await new Promise((r) => setTimeout(r, 0));
  const [r1, r2, r3] = shown(rowsLayer);
  const hidden = (row) => row.children[0].children.filter((c) => c.className === "visually-hidden").map((c) => c.textContent);
  assert.deepEqual(hidden(r1), []);
  assert.deepEqual(hidden(r2), ["bookmarked (Blue)"]);
  assert.deepEqual(hidden(r3), ["rule x"]);
  assert.equal(r2.attrs["aria-label"], undefined);
  assert.ok(r2.children.every((c) => c.attrs.role === "gridcell"));
  grid.getMark = () => null;
  grid.render();
  assert.deepEqual(hidden(r2), [], "removed when the mark goes");
});

test("hidden pool rows carry no rowindex", async () => {
  const { grid, rowsLayer } = setup();
  await grid.setView({ caseId: "A", filter: null, range: null });
  await new Promise((r) => setTimeout(r, 0));
  grid.window.load(makeRows(1, 2), null, null, 0);
  grid.render();
  const hiddenRows = rowsLayer.children.filter((r) => r.hidden);
  assert.ok(hiddenRows.length > 0);
  assert.ok(hiddenRows.every((r) => r.attrs["aria-rowindex"] === undefined));
});

test("a repeat mark (9.12) is hidden text in the first cell, with data-rp and the tooltip, and no row label", async () => {
  const { grid, rowsLayer } = setup({ repeats: { 2: { label: "Repeat of #1, +1 ms" } }, marks: { 2: { color: "blue" } } });
  await grid.setView({ caseId: "A", filter: null, range: null });
  await new Promise((r) => setTimeout(r, 0));
  const [r1, r2] = shown(rowsLayer);
  const hidden = (row) => row.children[0].children.filter((c) => c.className === "visually-hidden").map((c) => c.textContent);
  assert.deepEqual(hidden(r1), []);
  assert.deepEqual(hidden(r2), ["bookmarked (Blue), Repeat of #1, +1 ms"]);
  assert.equal(r2.dataset.rp, "1");
  assert.equal(r2.title, "Repeat of #1, +1 ms");
  assert.equal(r2.attrs["aria-label"], undefined);
  grid.getRepeat = () => null;
  grid.render();
  assert.deepEqual(hidden(r2), ["bookmarked (Blue)"]);
  assert.equal(r2.dataset.rp, undefined);
});

const settle = () => new Promise((r) => setTimeout(r, 0));

test("dropping the pool for a reload clears activedescendant, also when the reload fails", async () => {
  const { grid, viewport } = setup();
  await grid.setView({ caseId: "A", filter: null, range: null });
  await settle();
  grid.window.selected = 1;
  grid.render();
  assert.ok(viewport.attrs["aria-activedescendant"]);
  let release;
  api.page = () => new Promise((_, reject) => { release = reject; });
  const done = grid.setFieldColumns(["f1"]);
  assert.equal(viewport.attrs["aria-activedescendant"], undefined, "no reference to a removed row while the request runs");
  release(new Error("boom"));
  await done.catch(() => {});
  await settle();
  assert.equal(viewport.attrs["aria-activedescendant"], undefined, "nor after the failure");
  // The collection column takes the same path.
  api.page = async () => ({ rows: makeRows(1, 200), prev: null, next: "n" });
  await grid.setView({ caseId: "A", filter: null, range: null });
  await settle();
  grid.window.selected = 1;
  grid.render();
  assert.ok(viewport.attrs["aria-activedescendant"]);
  api.page = () => new Promise(() => {});
  grid.setCollectionColumn(true);
  assert.equal(viewport.attrs["aria-activedescendant"], undefined);
});

test("marks stay in the accessible name when Source is the first column and has its own label", async () => {
  const { grid, rowsLayer } = setup({ marks: { 1: { color: "blue" } } });
  grid.setColumns(["source", "msg"]);
  grid.sourceNames.set(1, "a.sensitive.log");
  await grid.setView({ caseId: "A", filter: null, range: null });
  await settle();
  const [r1] = shown(rowsLayer);
  const [source, msg] = r1.children;
  assert.ok(source.attrs["aria-label"], "the sensitive source keeps its label");
  assert.doesNotMatch(source.attrs["aria-label"], /bookmarked/);
  assert.deepEqual(msg.children.filter((c) => c.className === "visually-hidden").map((c) => c.textContent), ["bookmarked (Blue)"]);
  // Source as the only column: the text goes into its label.
  grid.setColumns(["source"]);
  grid.render();
  const [only] = shown(rowsLayer)[0].children;
  assert.match(only.attrs["aria-label"], /, bookmarked \(Blue\)$/);
});

test("aria-rowcount does not carry the previous filter's count once the new rows are in", async () => {
  const { grid, viewport } = setup({ count: { count: 400, exact: true } });
  await grid.setView({ caseId: "A", filter: null, range: null });
  await settle();
  assert.equal(viewport.attrs["aria-rowcount"], "401");
  let answer;
  api.count = () => new Promise((resolve) => { answer = resolve; });
  await grid.setView({ caseId: "A", filter: { q: "x" }, range: null });
  await settle();
  assert.equal(viewport.attrs["aria-rowcount"], "-1", "the new view's count is still pending");
  answer({ count: 300, exact: true });
  await settle();
  assert.equal(viewport.attrs["aria-rowcount"], "301");
});
