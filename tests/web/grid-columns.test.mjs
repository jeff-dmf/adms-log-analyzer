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
    setAttribute(k, v) { attrs[k] = String(v); }, removeAttribute(k) { delete attrs[k]; }, focus() {},
    set textContent(v) { this.children = []; this._t = v; }, get textContent() { return this._t || ""; },
  };
  return el;
}
globalThis.document = { createElement: fakeElement, createTextNode: (text) => ({ textContent: String(text) }) };

const { probeCharWidth } = await import("../../src/dmslog/web/js/column-resize.js");
const colModel = await import("../../src/dmslog/web/js/column-resize-model.js");
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

test("grid: a sensitive Source cell has an accessible name with the full name and the sensitive state, and loses it when reused", async () => {
  const { grid, rowsLayer } = makeGrid(8);
  const names = new Map([[1, "/x/AVLAdapter_1022.sensitive.log.0"], [2, "C:\\logs\\Short.log"]]);
  await loaded(grid, null, names);
  const cell = (row) => row.children.find((c) => c.className.includes("col-source"));
  const [first, second] = rowsLayer.children;
  assert.equal(cell(first).attrs["aria-label"], "AVLAdapter_1022.sensitive.log.0 (" + S.sourceSensitive + ")");
  assert.equal(cell(second).attrs["aria-label"], undefined);
  // the pooled cell is reused for a normal source: the label must not stay
  grid.setSourceNames(new Map([[1, "/x/Other.log"], [2, "C:\\logs\\Short.log"]]));
  grid.render();
  assert.equal(cell(rowsLayer.children[0]).attrs["aria-label"], undefined);
  assert.equal(cell(rowsLayer.children[0]).dataset.badge, undefined);
});

test("grid: the date of a multi-day case comes from the whole case, not from the filtered range", async () => {
  const { grid } = makeGrid();
  const filtered = { min: T, max: T + 1000 };
  const whole = { min: T, max: T + 3 * 86400000 };
  await loaded(grid, filtered, new Map());
  await grid.setView({ caseId: "M", filter: null, range: filtered, caseRange: whole });
  assert.equal(grid.multiDay, true);
  assert.equal(grid.resizer.model.get("ts"), 18 * 8 + 9);
  // widening the filter later changes nothing
  await grid.setView({ caseId: "M", filter: null, range: whole, caseRange: whole });
  assert.equal(grid.resizer.model.get("ts"), 18 * 8 + 9);
  // a one-day case with a one-day filter stays one day
  const other = makeGrid();
  await loaded(other.grid, filtered, new Map());
  await other.grid.setView({ caseId: "N", filter: null, range: filtered, caseRange: filtered });
  assert.equal(other.grid.multiDay, false);
  // the multi-day Source default is narrower, so Message keeps its room
  assert.equal(grid.resizer.model.get("source"), 16 * 8 + 9);
  assert.equal(other.grid.resizer.model.get("source"), 20 * 8 + 9);
});

test("Message keeps at least 60 characters at 1600 px with the Collection column, also for a multi-day case", () => {
  const keys = ["ts", "pid", "tid", "level", "source", "collection", "msg"];
  for (const multiDay of [false, true]) {
    const chars = colModel.messageChars(1600, keys, { charPx: 7.2, multiDay });
    assert.ok(chars >= 60, `multiDay=${multiDay}: ${chars}`);
  }
  assert.equal(colModel.messageChars(0, keys), 0);
  assert.equal(colModel.messageChars(NaN, keys), 0);
  assert.equal(colModel.messageChars(300, keys), 0);
  // with the nominal character for a failed measurement (0, NaN)
  assert.equal(colModel.messageChars(1600, keys, { charPx: 0, multiDay: true }), colModel.messageChars(1600, keys, { multiDay: true }));
  // the multi-day source default is what pays for the date: without it the target is missed
  const without = colModel.messageChars(1600, keys.filter((k) => k !== "source"), { multiDay: true });
  assert.ok(without > 60);
});

function layoutNode() {
  const node = { children: [], attrs: {}, className: "", _t: "", removed: false };
  node.setAttribute = (k, v) => { node.attrs[k] = v; };
  node.appendChild = (c) => { node.children.push(c); return c; };
  node.remove = () => { node.removed = true; };
  node.getBoundingClientRect = () => ({ width: node.width });
  return node;
}

test("probeCharWidth: width of 40 characters divided by 40, null without layout, and the probe row is removed", () => {
  const saved = globalThis.document;
  try {
    for (const [width, expected] of [[320, 8], [0, null], [NaN, null], [-5, null], [undefined, null]]) {
      const made = [];
      globalThis.document = { createElement: () => { const n = layoutNode(); n.width = width; made.push(n); return n; } };
      const layer = layoutNode();
      assert.equal(probeCharWidth(layer), expected, String(width));
      assert.equal(made[0].removed, true, "the hidden row is taken out of the grid");
      assert.equal(made[1].textContent, "0".repeat(40));
    }
    assert.equal(probeCharWidth(null), null);
    assert.equal(probeCharWidth({}), null);
    globalThis.document = { createElement: fakeElement }; // no layout engine (no getBoundingClientRect)
    assert.equal(probeCharWidth(layoutNode()), null);
  } finally {
    globalThis.document = saved;
  }
});

test("grid: a font that finishes loading measures the character again and re-applies the widths", () => {
  const saved = globalThis.document.fonts;
  const listeners = {};
  globalThis.document.fonts = { addEventListener(type, fn) { listeners[type] = fn; } };
  try {
    const { grid } = makeGrid(8);
    assert.equal(typeof listeners.loadingdone, "function", "the grid listens for loadingdone");
    grid.resizer.measureProbe = () => 9;
    listeners.loadingdone();
    assert.equal(grid.resizer.model.get("pid"), 6 * 9 + 9);
    grid.resizer.measureProbe = () => null; // a failed re-measure changes nothing
    listeners.loadingdone();
    assert.equal(grid.resizer.model.get("pid"), 6 * 9 + 9);
  } finally {
    globalThis.document.fonts = saved;
  }
});
