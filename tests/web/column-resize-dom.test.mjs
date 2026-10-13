// DOM-side behaviour of the resizer with fake elements: the pool keeps every slot in sync and
// "fit" measures the intrinsic width.
import test from "node:test";
import assert from "node:assert/strict";

import { ColumnResizer, intrinsicWidths } from "../../src/dmslog/web/js/column-resize.js";
import { FIT_MAX_WIDTH, FIT_SLACK, MIN_WIDTH } from "../../src/dmslog/web/js/column-resize-model.js";

// A cell whose scrollWidth follows the layout rule: the applied width unless "auto", then the content.
function cell(content) {
  const el = { style: { width: "", flex: "" }, content };
  Object.defineProperty(el, "scrollWidth", {
    configurable: true,
    get() {
      return el.style.width === "auto" || el.style.width === "" ? el.content : Math.max(el.content, parseFloat(el.style.width));
    },
  });
  el.offsetWidth = 0;
  return el;
}

function setup(pool, head) {
  const header = { style: {}, scrollLeft: 0 };
  const rowsLayer = { style: {} };
  const viewport = { addEventListener() {}, scrollLeft: 0 };
  const resizer = new ColumnResizer({
    viewport, rowsLayer, header, storage: null,
    getCells: () => pool.map((s) => s.cell),
    getVisibleCells: () => pool.filter((s) => !s.hidden).map((s) => s.cell),
    getKeys: () => ["msg"],
  });
  resizer.headerCells.set("msg", head);
  return resizer;
}

test("changing a width updates hidden pool slots too", () => {
  const pool = [{ hidden: false, cell: cell(50) }, { hidden: true, cell: cell(50) }];
  const resizer = setup(pool, cell(30));
  resizer.setWidth("msg", 500, false);
  assert.equal(pool[1].cell.style.width, "500px");
  resizer.reset();
  assert.equal(pool[1].cell.style.width, "300px");
  assert.equal(pool[0].cell.style.width, "300px");
});

test("fit shrinks a wide column to its short content and restores the styles", () => {
  const pool = [{ hidden: false, cell: cell(60) }, { hidden: false, cell: cell(80) }, { hidden: true, cell: cell(900) }];
  const resizer = setup(pool, cell(40));
  resizer.setWidth("msg", 700, false);
  resizer.fit("msg");
  assert.equal(resizer.model.get("msg"), 80 + FIT_SLACK); // hidden rows are not measured
  assert.equal(pool[0].cell.style.width, "84px");
  assert.equal(pool[0].cell.style.flex, "");
});

test("fit does not grow a column through its own width, and caps at the maximum", () => {
  const pool = [{ hidden: false, cell: cell(20) }];
  const resizer = setup(pool, cell(10));
  resizer.setWidth("msg", 100, false);
  resizer.fit("msg");
  assert.equal(resizer.model.get("msg"), MIN_WIDTH); // 20 + slack is below the minimum
  pool[0].cell.content = 5000;
  resizer.fit("msg");
  assert.equal(resizer.model.get("msg"), FIT_MAX_WIDTH);
});

test("intrinsicWidths restores width and flex", () => {
  const c = cell(70);
  c.style.width = "300px";
  c.style.flex = "1 0 auto";
  assert.deepEqual(intrinsicWidths([c]), [70]);
  assert.equal(c.style.width, "300px");
  assert.equal(c.style.flex, "1 0 auto");
});

test("intrinsicWidths restores the styles when a measurement throws", () => {
  const a = cell(70);
  a.style.width = "300px";
  a.style.flex = "1 0 auto";
  const b = cell(10);
  b.style.width = "120px";
  Object.defineProperty(b, "scrollWidth", { get() { throw new Error("layout failed"); } });
  assert.throws(() => intrinsicWidths([a, b]), /layout failed/);
  assert.equal(a.style.width, "300px");
  assert.equal(a.style.flex, "1 0 auto");
  assert.equal(b.style.width, "120px");
  assert.equal(b.style.flex, "");
});
