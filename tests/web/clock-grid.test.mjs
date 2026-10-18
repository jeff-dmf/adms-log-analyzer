// 9.15: the grid's Adjusted time column. Time keeps the ORIGINAL time (always visible, even when the user hid the column);
// the adjusted column sits right after it while some collection has an offset. Fake elements of grid-columns; synthetic data.
import test from "node:test";
import assert from "node:assert/strict";

function fakeElement() {
  const attrs = {};
  return {
    style: {}, dataset: {}, children: [], hidden: false, clientHeight: 66, scrollTop: 0, className: "", attrs, title: "",
    classList: { toggle() {}, add() {}, remove() {} },
    addEventListener() {}, appendChild(c) { this.children.push(c); return c; },
    setAttribute(k, v) { attrs[k] = String(v); }, removeAttribute(k) { delete attrs[k]; }, focus() {},
    set textContent(v) { this.children = []; this._t = v; }, get textContent() { return this._t || ""; },
  };
}
globalThis.document = { createElement: fakeElement, createTextNode: (text) => ({ textContent: String(text) }) };

const { Grid, COLUMNS } = await import("../../src/dmslog/web/js/grid.js");
const { api } = await import("../../src/dmslog/web/js/api.js");
const { S } = await import("../../src/dmslog/web/js/strings.js");
const { DEFAULT_CH } = await import("../../src/dmslog/web/js/column-resize-model.js");

const T = Date.UTC(2026, 2, 9, 14, 5, 6, 789);

function makeGrid() {
  const rowsLayer = fakeElement();
  const header = fakeElement();
  const grid = new Grid({ viewport: fakeElement(), rowsLayer, spacer: fakeElement(), header, onSelect() {}, onError(err) { throw err; } });
  grid.resizer.measureProbe = () => 8;
  grid.resizer.measure();
  grid.resizer.apply();
  return { grid, rowsLayer, header };
}

async function load(grid, rows, range = { min: T, max: T + 1000 }) {
  const calls = { page: 0 };
  api.count = async () => ({ count: rows.length, exact: true });
  api.page = async () => { calls.page += 1; return { rows, prev: null, next: null }; };
  await grid.setView({ caseId: "A", filter: null, range });
  return calls;
}

const cellOf = (row, key) => row.children.find((c) => c.className.split(" ").includes("col-" + key));
const headerKeys = (header) => header.children.map((c) => c.className.replace("grid-cell ", "").trim());

const ROWS = [
  { id: 1, ts: T, ts_adjusted: T + 2000, sev: 4, msg: "a", nlines: 1, source_id: 1 },
  { id: 2, ts: T + 1, ts_adjusted: T + 1, sev: 4, msg: "b", nlines: 1, source_id: 2 },
];

test("without offsets the grid has no Adjusted time column", async () => {
  const { grid, header, rowsLayer } = makeGrid();
  await load(grid, ROWS);
  assert.equal(headerKeys(header).includes("col-adjusted"), false);
  assert.equal(cellOf(rowsLayer.children[0], "adjusted"), undefined);
  assert.equal(COLUMNS.some((c) => c.key === "adjusted"), false, "not part of the Columns menu");
});

test("with offsets: Time (original) then Adjusted time; each row shows both, the titles carry the full dates", async () => {
  const { grid, header, rowsLayer } = makeGrid();
  const calls = await load(grid, ROWS);
  grid.setAdjustedColumn(true);
  const keys = headerKeys(header);
  assert.deepEqual(keys.slice(0, 2), ["col-ts", "col-adjusted"]);
  assert.equal(header.children[1].textContent, S.ckColumnAdjusted);
  const [first, second] = rowsLayer.children;
  assert.equal(cellOf(first, "ts").textContent, "14:05:06.789", "the original time");
  assert.equal(cellOf(first, "adjusted").textContent, "14:05:08.789");
  assert.equal(cellOf(first, "adjusted").title, "2026-03-09 14:05:08.789");
  assert.equal(cellOf(second, "adjusted").textContent, "14:05:06.790");
  assert.equal(calls.page, 1, "the rows already carry ts_adjusted: no reload");
  grid.setAdjustedColumn(false);
  assert.equal(headerKeys(header).includes("col-adjusted"), false);
  assert.equal(cellOf(rowsLayer.children[0], "adjusted"), undefined);
});

test("a row without ts_adjusted shows its own time; a row without time shows nothing", async () => {
  const { grid, rowsLayer } = makeGrid();
  await load(grid, [
    { id: 1, ts: T, sev: 4, msg: "a", nlines: 1, source_id: 1 },
    { id: 2, ts: null, ts_adjusted: null, sev: 4, msg: "b", nlines: 1, source_id: 1 },
  ]);
  grid.setAdjustedColumn(true);
  assert.equal(cellOf(rowsLayer.children[0], "adjusted").textContent, "14:05:06.789");
  assert.equal(cellOf(rowsLayer.children[1], "adjusted").textContent, "");
  assert.equal(cellOf(rowsLayer.children[1], "ts").textContent, "");
});

test("the original time is never hidden while the adjusted one shows, even if the user hid the Time column", async () => {
  const { grid, header } = makeGrid();
  await load(grid, ROWS);
  grid.setColumns(["msg", "source"]);
  assert.equal(headerKeys(header).includes("col-ts"), false);
  grid.setAdjustedColumn(true);
  assert.deepEqual(headerKeys(header).slice(0, 2), ["col-ts", "col-adjusted"]);
  grid.setColumns(["msg"]);
  assert.deepEqual(headerKeys(header).slice(0, 2), ["col-ts", "col-adjusted"]);
  grid.setAdjustedColumn(false);
  assert.equal(headerKeys(header).includes("col-ts"), false, "the user's choice applies again");
});

test("a multi-day case widens the adjusted column like the time column, and the month-day shows", async () => {
  const { grid, rowsLayer } = makeGrid();
  await load(grid, ROWS, { min: T, max: T + 2 * 86_400_000 });
  grid.setAdjustedColumn(true);
  assert.equal(cellOf(rowsLayer.children[0], "adjusted").textContent, "03-09 14:05:08.789");
  assert.equal(grid.resizer.model.get("adjusted"), 18 * 8 + 9);
  assert.equal(DEFAULT_CH.adjusted, DEFAULT_CH.ts);
});

test("setting the same state twice draws nothing again", async () => {
  const { grid, header } = makeGrid();
  await load(grid, ROWS);
  grid.setAdjustedColumn(true);
  const cell = header.children[1];
  grid.setAdjustedColumn(true);
  assert.equal(header.children[1], cell);
});
