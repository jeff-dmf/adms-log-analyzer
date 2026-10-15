// UI-16: grid columns in ch, the middle ellipsis of the Source column, the "S" badge, the short time text and the
// rule that nothing is measured while rendering rows.
import test from "node:test";
import assert from "node:assert/strict";

function fakeElement() {
  const attrs = {};
  const el = {
    style: {}, dataset: {}, children: [], hidden: false, clientHeight: 66, scrollTop: 0, className: "", attrs, title: "",
    classList: { toggle() {}, add() {}, remove() {} },
    addEventListener() {}, appendChild(c) { this.children.push(c); return c; },
    setAttribute(k, v) { attrs[k] = String(v); }, focus() {},
    set textContent(v) { this.children = []; this._t = v; }, get textContent() { return this._t || ""; },
  };
  return el;
}
globalThis.document = { createElement: fakeElement, createTextNode: (text) => ({ textContent: String(text) }) };

const model = await import("../../src/dmslog/web/js/grid-model.js");
const { Grid } = await import("../../src/dmslog/web/js/grid.js");
const { api } = await import("../../src/dmslog/web/js/api.js");
const { S } = await import("../../src/dmslog/web/js/strings.js");
const { middleEllipsis, isMultiDay, shortTs, isSensitiveName, SourceLabels } = model;

test("middleEllipsis: short and exact names are untouched", () => {
  assert.equal(middleEllipsis("a.log", 20), "a.log");
  assert.equal(middleEllipsis("AVLAdapter_1022.log", 19), "AVLAdapter_1022.log");
  assert.equal(middleEllipsis("", 5), "");
  assert.equal(middleEllipsis(null, 5), "");
});

test("middleEllipsis: keeps .sensitive, .log and the rotation index, and the exact length", () => {
  const name = "AVLAdapter_1022.sensitive.log.0";
  const cut = middleEllipsis(name, 24);
  assert.equal(cut.length, 24);
  assert.ok(cut.endsWith(".sensitive.log.0"), cut);
  assert.ok(cut.startsWith("AVLAdap"), cut);
  assert.ok(cut.includes("\u2026"));
  assert.ok(middleEllipsis("AVLAdapter_1022.log.12", 14).endsWith(".log.12"));
  assert.ok(middleEllipsis("AVLAdapter_1022.log", 12).endsWith(".log"));
});

test("middleEllipsis: a 120-character name", () => {
  const name = "X".repeat(100) + ".sensitive.log.3";
  assert.equal(name.length, 116);
  const long = "Y".repeat(104) + name.slice(100);
  assert.equal(long.length, 120);
  const cut = middleEllipsis(long, 20);
  assert.equal(cut.length, 20);
  assert.ok(cut.endsWith(".sensitive.log.3"));
  assert.equal(cut[0], "Y");
  assert.equal(middleEllipsis(long, 120), long);
});

test("middleEllipsis: tiny and invalid widths", () => {
  assert.equal(middleEllipsis("abcdef", 1), "\u2026");
  assert.equal(middleEllipsis("abcdef", 2), "a\u2026");
  assert.equal(middleEllipsis("abcdef", 0), "");
  assert.equal(middleEllipsis("abcdef", -4), "");
  assert.equal(middleEllipsis("abcdef", NaN), "abcdef"); // width not measured: no cut
  assert.equal(middleEllipsis("abcdef", Infinity), "abcdef");
  // a tail longer than the room is itself cut from the left, the end stays
  const cut = middleEllipsis("A.sensitive.log.0", 8);
  assert.equal(cut.length, 8);
  assert.ok(cut.endsWith(".log.0"), cut);
  // names without a log suffix keep a short tail
  assert.equal(middleEllipsis("abcdefghijklmnop", 9).length, 9);
});

test("isSensitiveName follows the adapter naming", () => {
  assert.equal(isSensitiveName("AVLAdapter_1022.sensitive.log"), true);
  assert.equal(isSensitiveName("AVLAdapter_1022.sensitive.log.2"), true);
  assert.equal(isSensitiveName("AVLAdapter_1022.log"), false);
  assert.equal(isSensitiveName("sensitive_notes.txt"), false);
});

test("isMultiDay: one day, several days and missing bounds", () => {
  const day = 86400000;
  assert.equal(isMultiDay({ min: 5 * day + 10, max: 5 * day + day - 1 }), false);
  assert.equal(isMultiDay({ min: 5 * day + 10, max: 6 * day }), true);
  assert.equal(isMultiDay(null), false);
  assert.equal(isMultiDay({ min: null, max: 10 * day }), false);
  assert.equal(isMultiDay({ min: NaN, max: 10 * day }), false);
  assert.equal(isMultiDay({ min: 0, max: 0 }), false);
});

test("shortTs: clock only, or month-day and clock", () => {
  assert.equal(shortTs("2026-03-09 14:05:06.789", false), "14:05:06.789");
  assert.equal(shortTs("2026-03-09 14:05:06.789", true), "03-09 14:05:06.789");
  assert.equal(shortTs("", false), "");
  assert.equal(shortTs(null, true), "");
});

test("SourceLabels computes once per (source, width) and refreshes when the width or the names change", () => {
  const labels = new SourceLabels();
  labels.setMaxChars(12);
  const a = labels.get(1, "AVLAdapter_1022.sensitive.log.0");
  for (let i = 0; i < 100; i += 1) {
    labels.get(1, "AVLAdapter_1022.sensitive.log.0");
  }
  assert.equal(labels.computed, 1);
  assert.equal(a.sensitive, true);
  assert.equal(a.text.length, 12 - model.BADGE_CHARS); // the badge takes characters of the cell
  labels.setMaxChars(12);
  assert.equal(labels.computed, 1); // same width: cache kept
  labels.setMaxChars(30);
  labels.get(1, "AVLAdapter_1022.sensitive.log.0");
  assert.equal(labels.computed, 2);
  labels.setNames(new Map());
  labels.get(1, "AVLAdapter_1022.sensitive.log.0");
  assert.equal(labels.computed, 3);
  assert.equal(labels.get(7, undefined).text, "7"); // unknown source: its id
  labels.setMaxChars(NaN);
  assert.equal(labels.maxChars, Infinity);
});

function makeGrid(charPx = 8) {
  const rowsLayer = fakeElement();
  const grid = new Grid({
    viewport: fakeElement(), rowsLayer, spacer: fakeElement(), header: fakeElement(),
    onSelect() {}, onError(err) { throw err; },
  });
  let probes = 0;
  grid.resizer.measureProbe = () => { probes += 1; return charPx; };
  grid.resizer.measure();
  grid.resizer.apply();
  return { grid, rowsLayer, probes: () => probes };
}

const T = Date.UTC(2026, 2, 9, 14, 5, 6, 789);

async function loaded(grid, range, names) {
  api.count = async () => ({ count: 2, exact: true });
  api.page = async () => ({
    rows: [
      { id: 1, ts: T, sev: 4, msg: "a", nlines: 1, source_id: 1 },
      { id: 2, ts: T + 1, sev: 2, msg: "b", nlines: 1, source_id: 2 },
    ],
    prev: null, next: null,
  });
  grid.setSourceNames(names);
  await grid.setView({ caseId: "A", filter: null, range });
}

test("grid: time cell shows the clock, the title has the full date; a multi-day case adds month-day", async () => {
  const { grid, rowsLayer } = makeGrid();
  await loaded(grid, { min: T, max: T + 1000 }, new Map());
  const [first] = rowsLayer.children;
  assert.equal(first.children[0].textContent, "14:05:06.789");
  assert.equal(first.children[0].title, "2026-03-09 14:05:06.789");
  assert.equal(grid.resizer.model.get("ts"), 12 * 8 + 9);
  const wide = makeGrid();
  await loaded(wide.grid, { min: T, max: T + 2 * 86400000 }, new Map());
  assert.equal(wide.rowsLayer.children[0].children[0].textContent, "03-09 14:05:06.789");
  assert.equal(wide.grid.resizer.model.get("ts"), 18 * 8 + 9);
  // the decision is kept for the same case when the selection narrows, and re-made for another case
  await wide.grid.setView({ caseId: "A", filter: null, range: { min: T, max: T + 1000 } });
  assert.equal(wide.grid.multiDay, true);
  await wide.grid.setView({ caseId: "B", filter: null, range: { min: T, max: T + 1000 } });
  assert.equal(wide.grid.multiDay, false);
});

test("grid: Source cell is cut in the middle by its width, with the S badge and the full name in the title", async () => {
  const { grid, rowsLayer } = makeGrid(8);
  const names = new Map([[1, "/x/AVLAdapter_1022.sensitive.log.0"], [2, "C:\\logs\\Short.log"]]);
  await loaded(grid, null, names);
  grid.resizer.setWidth("source", 20 * 8 + 9, false); // 20 characters at 8px
  const [first, second] = rowsLayer.children;
  const cell = (row) => row.children.find((c) => c.className.includes("col-source"));
  const sensitive = cell(first);
  assert.equal(sensitive.textContent.length, 20 - model.BADGE_CHARS);
  assert.ok(sensitive.textContent.endsWith(".log.0"), sensitive.textContent);
  assert.ok(!sensitive.textContent.includes("sensitive"), "the S badge replaces the word");
  assert.equal(sensitive.dataset.badge, S.gridSensitiveBadge);
  assert.equal(sensitive.title, "AVLAdapter_1022.sensitive.log.0 (" + S.sourceSensitive + ")");
  assert.equal(cell(second).textContent, "Short.log");
  assert.equal(cell(second).dataset.badge, undefined);
  assert.equal(cell(second).title, "Short.log");
  grid.resizer.setWidth("source", 400, false);
  assert.equal(sensitive.textContent, "AVLAdapter_1022.log.0");
});

test("grid: rendering rows never measures text (the character width is read on column changes only)", async () => {
  const { grid, probes } = makeGrid();
  await loaded(grid, null, new Map([[1, "A.log"], [2, "B.log"]]));
  const after = probes();
  assert.ok(after >= 1);
  for (let i = 0; i < 50; i += 1) {
    grid.render();
    grid.window.top = 0;
  }
  assert.equal(probes(), after);
  grid.setColumns(["ts", "msg"]);
  assert.equal(probes(), after + 1); // a column change measures once
});

test("grid: a failed measurement keeps the nominal widths; a user width is preserved after the font changes", async () => {
  const { grid } = makeGrid(0);
  grid.resizer.measure();
  assert.equal(grid.resizer.model.get("source"), Math.ceil(20 * 7.2) + 9);
  assert.equal(grid.sourceLabels.maxChars, 20);
  grid.resizer.setWidth("level", 130, true);
  grid.resizer.measureProbe = () => 9;
  assert.equal(grid.resizer.measure(), true);
  grid.resizer.apply();
  assert.equal(grid.resizer.model.get("level"), 130);
  assert.equal(grid.resizer.model.get("pid"), 6 * 9 + 9);
});

test("grid: fit on Source measures the whole names and then cuts them again", async () => {
  const { grid, rowsLayer } = makeGrid(8);
  await loaded(grid, null, new Map([[1, "AVLAdapter_1022.sensitive.log.0"], [2, "B.log"]]));
  const seen = [];
  const original = grid.resizer.getVisibleCells;
  grid.resizer.getVisibleCells = (key) => {
    const cells = [...original(key)];
    seen.push(cells.map((c) => c.textContent));
    return cells;
  };
  grid.resizer.fit("source");
  assert.ok(seen[0].includes("AVLAdapter_1022.log.0"), "measured uncut");
  assert.equal(grid.fitting, false);
  const width = grid.resizer.model.get("source"); // the fake cells have no layout: the width is the minimum
  assert.equal(grid.sourceLabels.maxChars, Math.floor((width - 8) / 8)); // cut again by the new width
  const cut = rowsLayer.children[0].children.find((c) => c.className.includes("col-source")).textContent;
  assert.ok(cut.includes("\u2026"));
  assert.ok(rowsLayer.children.length > 0);
});
