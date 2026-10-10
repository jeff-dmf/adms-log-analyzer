// Pure helpers of "Follow tail": position, pending counter and the watch event parser.
import test from "node:test";
import assert from "node:assert/strict";

import { pendingCount, parseWatchEvent, tailPosition } from "../../src/dmslog/web/js/follow-tail-model.js";

test("tail position: bottom, scrolled and middle", () => {
  assert.equal(tailPosition({ atEnd: true, top: 180, visible: 20, length: 200 }), "bottom");
  assert.equal(tailPosition({ atEnd: true, top: 0, visible: 50, length: 30 }), "bottom"); // short buffer
  assert.equal(tailPosition({ atEnd: true, top: 100, visible: 20, length: 200 }), "scrolled");
  assert.equal(tailPosition({ atEnd: false, top: 180, visible: 20, length: 200 }), "middle");
  assert.equal(tailPosition(null), "middle");
});

test("pending count never goes negative and ignores unknown totals", () => {
  assert.equal(pendingCount(100, 142), 42);
  assert.equal(pendingCount(100, 90), 0);
  assert.equal(pendingCount(null, 10), 0);
  assert.equal(pendingCount(undefined, 10), 0);
  assert.equal(pendingCount(5, "x"), 0);
});

test("watch events parse to objects only", () => {
  assert.deepEqual(parseWatchEvent('{"records":3}'), { records: 3 });
  assert.equal(parseWatchEvent("nope"), null);
  assert.equal(parseWatchEvent("3"), null);
  assert.equal(parseWatchEvent("null"), null);
});
