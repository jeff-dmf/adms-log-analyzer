// The grid paints highlights through getHighlight: data-hl on the row and the rule name in its label.
import test from "node:test";
import assert from "node:assert/strict";

function fakeElement() {
  const attrs = {};
  return {
    style: {}, dataset: {}, children: [], hidden: false, clientHeight: 66, scrollTop: 0, className: "", attrs,
    classList: { toggle() {} },
    addEventListener() {}, appendChild(c) { this.children.push(c); return c; },
    setAttribute(k, v) { attrs[k] = String(v); }, removeAttribute(k) { delete attrs[k]; }, focus() {},
    set textContent(v) { this.children = []; this._t = v; }, get textContent() { return this._t || ""; },
  };
}
globalThis.document = { createElement: fakeElement, createTextNode: (text) => ({ textContent: String(text) }) };

const { Grid } = await import("../../src/dmslog/web/js/grid.js");
const { api } = await import("../../src/dmslog/web/js/api.js");

test("highlighted rows get data-hl and a label; others lose it", async () => {
  api.count = async () => ({ count: 2, exact: true });
  api.page = async () => ({
    rows: [
      { id: 1, ts: 10, sev: 4, msg: "a", nlines: 1, source_id: 1 },
      { id: 2, ts: 11, sev: 2, msg: "b", nlines: 1, source_id: 1 },
    ],
    prev: null,
    next: null,
  });
  let colors = { 2: { color: "pink", label: "highlighted by <b>x</b>" } };
  const visible = [];
  const rowsLayer = fakeElement();
  const grid = new Grid({
    viewport: fakeElement(), rowsLayer, spacer: fakeElement(), header: fakeElement(),
    onSelect() {}, onStatus() {}, onError(err) { throw err; },
    getHighlight: (id) => colors[id] || null,
    onVisible: (ids) => visible.push(ids),
  });
  await grid.setView({ caseId: "A", filter: null, range: null });
  const [first, second] = rowsLayer.children;
  assert.equal(first.dataset.hl, undefined);
  assert.equal(second.dataset.hl, "pink");
  assert.equal(second.attrs["aria-label"], undefined, "UI-26: no aria-label on the row, it would replace the cells");
  assert.equal(second.children[0].children.at(-1).textContent, "highlighted by <b>x</b>", "the rule name is hidden text in the first cell");
  assert.deepEqual(visible.at(-1), [1, 2]);
  colors = {};
  grid.render();
  assert.equal(second.dataset.hl, undefined);
  assert.doesNotMatch(second.children[0].textContent, /highlighted/);
});
