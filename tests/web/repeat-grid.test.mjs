// 9.12 UI: the grid shows a repeated record with a data attribute, the words as the row tooltip and in its aria-label,
// asks for the marks of the rows on screen through onVisible, and never reads the layout to do it.
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";

function fakeElement() {
  const attrs = {};
  return {
    style: {}, dataset: {}, children: [], hidden: false, clientHeight: 66, scrollTop: 0, className: "", attrs, title: "",
    classList: { toggle() {} },
    addEventListener() {}, appendChild(c) { this.children.push(c); return c; },
    setAttribute(k, v) { attrs[k] = String(v); }, removeAttribute(k) { delete attrs[k]; if (k === "title") this.title = ""; }, focus() {},
    set textContent(v) { this.children = []; this._t = v; }, get textContent() { return this._t || ""; },
  };
}
globalThis.document = { createElement: fakeElement, createTextNode: (text) => ({ textContent: String(text) }) };

const { Grid } = await import("../../src/dmslog/web/js/grid.js");
const { api } = await import("../../src/dmslog/web/js/api.js");

test("repeated rows get data-rp, the tooltip and the label; other rows and rows whose mark went away lose them", async () => {
  api.count = async () => ({ count: 2, exact: true });
  api.page = async () => ({
    rows: [{ id: 1, ts: 10, sev: 4, msg: "a", nlines: 1, source_id: 1 }, { id: 2, ts: 11, sev: 4, msg: "a", nlines: 1, source_id: 1 }],
    prev: null, next: null,
  });
  let marks = { 2: { label: "Repeat of #1, +1 ms" } };
  const visible = [];
  const rowsLayer = fakeElement();
  const grid = new Grid({
    viewport: fakeElement(), rowsLayer, spacer: fakeElement(), header: fakeElement(),
    onSelect() {}, onError(err) { throw err; },
    getRepeat: (id) => marks[id] || null,
    onVisible: (ids) => visible.push(ids),
  });
  await grid.setView({ caseId: "A", filter: null, range: null });
  const [first, second] = rowsLayer.children;
  assert.equal(first.dataset.rp, undefined);
  assert.equal(first.title, "");
  assert.equal(second.dataset.rp, "1");
  assert.equal(second.title, "Repeat of #1, +1 ms");
  assert.match(second.attrs["aria-label"], /, Repeat of #1, \+1 ms$/);
  assert.deepEqual(visible.at(-1), [1, 2]);
  marks = {};
  grid.render();
  assert.equal(second.dataset.rp, undefined);
  assert.equal(second.title, "");
  assert.doesNotMatch(second.attrs["aria-label"], /Repeat/);
});

test("without the repeats module (it loads after the first view) the grid is as it was", async () => {
  api.count = async () => ({ count: 1, exact: true });
  api.page = async () => ({ rows: [{ id: 1, ts: 10, sev: 4, msg: "a", nlines: 1, source_id: 1 }], prev: null, next: null });
  const rowsLayer = fakeElement();
  const grid = new Grid({
    viewport: fakeElement(), rowsLayer, spacer: fakeElement(), header: fakeElement(), onSelect() {}, onError(err) { throw err; },
  });
  await grid.setView({ caseId: "A", filter: null, range: null });
  assert.equal(rowsLayer.children[0].dataset.rp, undefined);
});

test("the mark costs the scroll no layout read: the modules touch no geometry and use only attributes", () => {
  for (const file of ["repeat-model.js", "repeat-detail.js"]) {
    const source = fs.readFileSync(new URL("../../src/dmslog/web/js/" + file, import.meta.url), "utf8");
    assert.ok(!/offsetWidth|offsetHeight|clientWidth|clientHeight|getBoundingClientRect|scrollTop|scrollHeight/.test(source), file);
  }
  const grid = fs.readFileSync(new URL("../../src/dmslog/web/js/grid.js", import.meta.url), "utf8");
  const block = grid.slice(grid.indexOf("const repeat = this.getRepeat"), grid.indexOf("shown.push(row.id)"));
  assert.ok(block.length > 100 && !/offset|client|getBounding|scroll/.test(block));
});
