import assert from "node:assert/strict";
import { test } from "node:test";
import { GRID_MIN_PX, LIMITS, STORAGE_KEY, bounds, clampWidth, keyWidth, loadWidths, normalizeWidths, saveWidths } from "../../src/dmslog/web/js/panel-resize-model.js";

test("a column stays inside its limits and leaves the records their room", () => {
  const room = { total: 2000, other: 248 };
  assert.equal(clampWidth("detail", 10, room), LIMITS.detail.min);
  assert.equal(clampWidth("detail", 5000, room), LIMITS.detail.max);
  const narrow = { total: 1000, other: 248 };
  assert.equal(bounds("detail", narrow).max, 1000 - 248 - GRID_MIN_PX);
  assert.equal(clampWidth("detail", 700, narrow), 1000 - 248 - GRID_MIN_PX);
  assert.equal(bounds("side", { total: 300, other: 300 }).max, LIMITS.side.min, "never below the minimum");
});

test("arrow keys move the edge, Home goes back to the default, other keys are not ours", () => {
  const room = { total: 2000, other: 248 };
  assert.equal(keyWidth("side", 248, "ArrowRight", room), 264);
  assert.equal(keyWidth("side", 248, "ArrowLeft", room), 232);
  assert.equal(keyWidth("detail", 380, "ArrowLeft", room), 396, "the detail grows when its edge moves left");
  assert.equal(keyWidth("detail", 380, "ArrowRight", room), 364);
  assert.equal(keyWidth("detail", LIMITS.detail.min, "ArrowRight", room), LIMITS.detail.min);
  assert.equal(keyWidth("side", 300, "Home", room), null);
  assert.equal(keyWidth("side", 300, "a", room), undefined);
});

test("only whole numbers inside the limits are remembered", () => {
  assert.deepEqual(normalizeWidths({ side: 300, detail: 99999 }), { side: 300 });
  assert.deepEqual(normalizeWidths({ side: "300", detail: 3.5 }), {});
  assert.deepEqual(normalizeWidths(null), {});
});

test("storage that throws or holds garbage never breaks the page", () => {
  const broken = { getItem() { throw new Error("blocked"); }, setItem() { throw new Error("blocked"); } };
  assert.deepEqual(loadWidths(broken), {});
  assert.equal(saveWidths(broken, { side: 300 }), false);
  assert.equal(saveWidths(null, { side: 300 }), false);
  const mem = new Map([[STORAGE_KEY, "{not json"]]);
  const store = { getItem: (k) => mem.get(k) ?? null, setItem: (k, v) => mem.set(k, v) };
  assert.deepEqual(loadWidths(store), {});
  assert.equal(saveWidths(store, { side: 300, detail: 400 }), true);
  assert.deepEqual(loadWidths(store), { side: 300, detail: 400 });
});
