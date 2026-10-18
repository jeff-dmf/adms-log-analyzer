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

// ---- cursors and the scrollbar live on the ADJUSTED axis (review of PR 227) ------------------------------------------

const gm = await import("../../src/dmslog/web/js/grid-model.js");
const filters = await import("../../src/dmslog/web/js/filters.js");
const highlight = await import("../../src/dmslog/web/js/highlight-model.js");
const OFFSET = 10_000;
const adjustedRow = (id, ts) => ({ id, ts, ts_adjusted: ts + OFFSET, sev: 4, msg: "m" + id, nlines: 1, source_id: 1 });

test("makeCursor names the adjusted time when the row carries one, the log time otherwise, and null for no time", () => {
  assert.equal(gm.makeCursor({ id: 98, ts: 2000, ts_adjusted: 12000 }), "12000:98");
  assert.equal(gm.makeCursor({ id: 98, ts: 2000 }), "2000:98");
  assert.equal(gm.makeCursor({ id: 5, ts: null, ts_adjusted: null }), "null:5");
  assert.equal(gm.makeCursor({ id: 5, ts: null }), "null:5");
  assert.equal(gm.makeCursor({ id: 5, ts: 0, ts_adjusted: 0 }), "0:5");
});

test("the buffer's trimming rebuilds its cursors on the adjusted axis (forward and backward paging)", () => {
  const rows = (from, to) => Array.from({ length: to - from }, (_, i) => adjustedRow(from + i, 1000 + (from + i) * 10));
  const win = new gm.RowWindow(4);
  win.load(rows(1, 5), "p0", "n0", 3);
  win.append(rows(5, 7), "n1"); // 6 rows > 4: the head is cut, prevCursor is rebuilt from the new first row
  assert.equal(win.rows[0].id, 3);
  assert.equal(win.prevCursor, `${1000 + 3 * 10 + OFFSET}:3`);
  const back = new gm.RowWindow(4);
  back.load(rows(10, 14), "p0", "n0", 0);
  back.prepend(rows(8, 10), "p1"); // the tail is cut, nextCursor is rebuilt from the new last row
  assert.equal(back.rows.at(-1).id, 11);
  assert.equal(back.nextCursor, `${1000 + 11 * 10 + OFFSET}:11`);
});

test("revealRow with offsets: a record that only has its log time gets its adjusted time first, so the jump lands on it", async () => {
  const { grid } = makeGrid();
  await load(grid, [adjustedRow(1, T)]);
  grid.setAdjustedColumn(true);
  const cursors = [];
  const entries = [];
  api.page = async (caseId, filter, cursor) => { cursors.push(cursor); return { rows: [adjustedRow(98, 2000)], prev: null, next: null }; };
  api.entry = async (caseId, id) => { entries.push(id); return { id, ts: 2000, ts_adjusted: 2000 + OFFSET, clock_offset_ms: OFFSET }; };
  await grid.revealRow({ id: 98, ts: 2000 });
  assert.deepEqual(entries, [98]);
  assert.equal(cursors.at(-1), `${2000 + OFFSET}:97`, "12000:97, not 2000:97");
  assert.equal(grid.selectedRow().id, 98);
  // a record that already carries its adjusted time needs no read; a record without time never asks
  entries.length = 0;
  await grid.revealRow(adjustedRow(98, 2000));
  assert.equal(cursors.at(-1), `${2000 + OFFSET}:97`);
  await grid.revealRow({ id: 98, ts: null });
  assert.equal(cursors.at(-1), "null:97");
  assert.deepEqual(entries, []);
});

test("revealRow without offsets keeps the log time and reads nothing", async () => {
  const { grid } = makeGrid();
  await load(grid, [{ id: 1, ts: T, sev: 4, msg: "a", nlines: 1, source_id: 1 }]);
  const cursors = [];
  let read = 0;
  api.page = async (caseId, filter, cursor) => { cursors.push(cursor); return { rows: [], prev: null, next: null }; };
  api.entry = async () => { read += 1; return {}; };
  await grid.revealRow({ id: 98, ts: 2000 });
  assert.equal(cursors.at(-1), "2000:97");
  assert.equal(read, 0);
});

test("a case change while the adjusted time is being read cancels the reveal", async () => {
  const { grid } = makeGrid();
  await load(grid, [adjustedRow(1, T)]);
  grid.setAdjustedColumn(true);
  let release;
  let pages = 0;
  api.entry = () => new Promise((resolve) => { release = resolve; });
  const reveal = grid.revealRow({ id: 98, ts: 2000 });
  api.count = async () => ({ count: 0, exact: true });
  api.page = async () => { pages += 1; return { rows: [], prev: null, next: null }; };
  await grid.setView({ caseId: "B", filter: null, range: null });
  const before = pages;
  release({ id: 98, ts: 2000, ts_adjusted: 12000 });
  await reveal;
  assert.equal(pages, before, "no jump for a case that is not open any more");
});

test("the scrollbar position uses the adjusted time of the top row, on the adjusted range", async () => {
  const { grid } = makeGrid();
  const range = { min: T + OFFSET, max: T + OFFSET + 1000 }; // the adjusted span (main.js shifts each source by its offset)
  await load(grid, [adjustedRow(1, T + 400), adjustedRow(2, T + 800)], range);
  grid.window.top = 0;
  grid.syncScroll();
  assert.equal(grid.viewport.scrollTop, gm.tsToScroll(T + 400 + OFFSET, range));
  assert.notEqual(grid.viewport.scrollTop, gm.tsToScroll(T + 400, range), "the raw time would be far outside the range");
  grid.window.top = 1;
  grid.syncScroll();
  assert.ok(grid.viewport.scrollTop > gm.tsToScroll(T + 400 + OFFSET, range), "moving down never moves the bar back");
});

test("the keyboard navigation and the highlight keys start from the adjusted time too", () => {
  const row = { id: 7, ts: 2000, ts_adjusted: 12000 };
  assert.equal(filters.navRequest(null, null, row, "fwd").cursor, "12000:7");
  assert.equal(filters.navRequest(null, null, { id: 7, ts: 2000 }, "fwd").cursor, "2000:7");
  assert.equal(filters.navRequest(null, null, { id: 7, ts: null }, "fwd").cursor, "null:7");
  assert.equal(highlight.cursorOf(row), "12000:7");
  assert.equal(highlight.cursorOf({ id: 7, ts: 2000 }), "2000:7");
  assert.equal(highlight.cursorOf({ id: 7, ts: null }), "null:7");
});

test("timeSpan shifts each source by its collection's offset; the whole case then spans several days when an offset says so", async () => {
  const day = 86_400_000;
  const sources = [
    { id: 1, collection: 1, ts_min: T, ts_max: T + 1000 },
    { id: 2, collection: 2, ts_min: T, ts_max: T + 1000 },
  ];
  const offsetOf = (source) => ({ 1: 0, 2: 2 * day })[source.collection] || 0;
  assert.deepEqual(filters.timeSpan(sources, [], null, null), { min: T, max: T + 1000 }, "raw: unchanged");
  assert.deepEqual(filters.timeSpan(sources, [], null, null, offsetOf), { min: T, max: T + 2 * day + 1000 });
  assert.deepEqual(filters.timeSpan(sources, [2], null, null, offsetOf), { min: T + 2 * day, max: T + 2 * day + 1000 });
  assert.deepEqual(filters.timeSpan(sources, [], T + day, null, offsetOf), { min: T + day, max: T + 2 * day + 1000 }, "from/to are adjusted already");
  assert.equal(gm.isMultiDay(filters.timeSpan(sources, [], null, null)), false);
  assert.equal(gm.isMultiDay(filters.timeSpan(sources, [], null, null, offsetOf)), true);
  const { grid } = makeGrid();
  const caseRange = filters.timeSpan(sources, [], null, null, offsetOf);
  api.count = async () => ({ count: 1, exact: true });
  api.page = async () => ({ rows: [adjustedRow(1, T)], prev: null, next: null });
  await grid.setView({ caseId: "A", filter: null, range: { min: T, max: T + 1000 }, caseRange });
  assert.equal(grid.multiDay, true, "the offset made the case span days: Time and Adjusted time show the date");
});

test("revealRow: a GET started in A does not move the selection after A -> B -> A", async () => {
  const { grid } = makeGrid();
  await load(grid, [adjustedRow(1, T)]);
  grid.setAdjustedColumn(true);
  let release;
  api.entry = () => new Promise((resolve) => { release = resolve; });
  const reveal = grid.revealRow({ id: 98, ts: 2000 });
  api.count = async () => ({ count: 1, exact: true });
  const cursors = [];
  api.page = async (caseId, filter, cursor) => { cursors.push(cursor); return { rows: [adjustedRow(1, T)], prev: null, next: null }; };
  await grid.setView({ caseId: "B", filter: null, range: null });
  await grid.setView({ caseId: "A", filter: null, range: null }); // the same case id, another opening of the view
  const before = cursors.length;
  release({ id: 98, ts: 2000, ts_adjusted: 12000 });
  await reveal;
  assert.equal(cursors.length, before, "no jump in the view that was opened later");
  assert.equal(grid.selectedRow(), null);
});

test("revealRow: a jump cancelled by a case change selects nothing in the new case, even for the same record id", async () => {
  const { grid } = makeGrid();
  await load(grid, [adjustedRow(1, T)]);
  let releaseA;
  api.count = async () => ({ count: 1, exact: true });
  api.page = (caseId) => (caseId === "A"
    ? new Promise((resolve) => { releaseA = () => resolve({ rows: [adjustedRow(98, 2000)], prev: null, next: null }); })
    : Promise.resolve({ rows: [adjustedRow(98, 2000)], prev: null, next: null }));
  const reveal = grid.revealRow(adjustedRow(98, 2000)); // its jump waits for the page of A
  await Promise.resolve();
  await grid.setView({ caseId: "B", filter: null, range: null }); // B has a record 98 too
  grid.window.selected = -1;
  releaseA();
  await reveal;
  assert.equal(grid.selectedRow(), null, "record 98 of B is not selected by a reveal made for A");
});

test("setView: the dates show when the ORIGINAL span crosses a day, even if the adjusted span does not", async () => {
  const day = 86_400_000;
  const { grid } = makeGrid();
  api.count = async () => ({ count: 1, exact: true });
  api.page = async () => ({ rows: [adjustedRow(1, T)], prev: null, next: null });
  const adjusted = { min: T, max: T + 1000 };
  const original = { min: T, max: T + 2 * day };
  await grid.setView({ caseId: "A", filter: null, range: adjusted, caseRange: [adjusted, original] });
  assert.equal(grid.multiDay, true);
  const quiet = makeGrid().grid;
  await quiet.setView({ caseId: "A", filter: null, range: adjusted, caseRange: [adjusted, adjusted] });
  assert.equal(quiet.multiDay, false);
});

test("revealRow: a jump superseded by the user's own newer jump in the same view selects nothing", async () => {
  const { grid } = makeGrid();
  await load(grid, [adjustedRow(1, T)]);
  let calls = 0;
  api.page = (caseId, filter, cursor, dir, limit, signal) => {
    calls += 1;
    if (calls > 1) {
      return Promise.resolve({ rows: [adjustedRow(5, 3000), adjustedRow(98, 2000)], prev: null, next: null });
    }
    return new Promise((resolve, reject) => signal.addEventListener("abort", () => setTimeout(() => reject(Object.assign(new Error("aborted"), { name: "AbortError" })), 5))); // the old request ends after the new rows are in
  };
  const reveal = grid.revealRow(adjustedRow(98, 2000));
  await new Promise((r) => setTimeout(r, 0)); // the reveal's page request is in flight
  assert.equal(await grid.jump(null, "fwd"), "ok"); // the newer jump supersedes the reveal's
  grid.window.selected = -1;
  await reveal;
  assert.equal(grid.selectedRow(), null, "record 98 of the newer window is not selected by the old reveal");
});
