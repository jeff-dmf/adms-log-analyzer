import assert from "node:assert/strict";
import { test } from "node:test";
import { clampStripHeight, parseStoredStrip, stripKeyHeight } from "../../src/dmslog/web/js/timeline-model.js";

test("the strip stays between the collapsed height and about half of the window", () => {
  assert.equal(clampStripHeight(10, 1000), 40);
  assert.equal(clampStripHeight(300, 1000), 300);
  assert.equal(clampStripHeight(900, 1000), 480);
  assert.equal(clampStripHeight(900, 600), 300, "half of a short window");
  assert.equal(clampStripHeight(900, 50), 40, "never below the minimum");
  assert.equal(clampStripHeight(Infinity, 1000), 480);
  assert.equal(clampStripHeight(0, 1000), 40);
  assert.equal(clampStripHeight(NaN, 1000), 40);
  assert.equal(clampStripHeight(200, undefined), 200, "no window height: only the absolute limits");
});

test("ArrowDown makes the strip taller, ArrowUp shorter, Home goes back to the default", () => {
  assert.equal(stripKeyHeight(120, "ArrowDown", 1000), 136);
  assert.equal(stripKeyHeight(120, "ArrowUp", 1000), 104);
  assert.equal(stripKeyHeight(40, "ArrowUp", 1000), 40);
  assert.equal(stripKeyHeight(120, "Home", 1000), null);
  assert.equal(stripKeyHeight(120, "x", 1000), undefined);
});

test("only a height the strip can have is remembered", () => {
  assert.equal(parseStoredStrip("200"), 200);
  assert.equal(parseStoredStrip(null), null);
  assert.equal(parseStoredStrip("20"), null);
  assert.equal(parseStoredStrip("9999"), null);
  assert.equal(parseStoredStrip("abc"), null);
  assert.equal(parseStoredStrip("12.5"), null);
});
