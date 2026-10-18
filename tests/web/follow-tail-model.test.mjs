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

import { createWatchGate, watchInfo } from "../../src/dmslog/web/js/follow-tail-model.js";

test("watch info: only a new, resumed or rewritten source asks for the sources list", () => {
  assert.equal(watchInfo({ sources: [{ source: "a", records: 3, action: "append" }] }).sourcesChanged, false);
  assert.equal(watchInfo({ sources: [{ action: "append" }, { action: "new" }] }).sourcesChanged, true);
  assert.equal(watchInfo({ sources: [{ action: "reindex" }] }).sourcesChanged, true);
  assert.equal(watchInfo({ sources: [{ action: "resume" }] }).sourcesChanged, true);
  assert.equal(watchInfo({}).sourcesChanged, false);
  assert.equal(watchInfo(null).sourcesChanged, false);
  assert.equal(watchInfo({ sources: [null] }).sourcesChanged, false);
});

test("watch gate: visible events schedule; hidden ones are remembered and give ONE update when back", () => {
  const gate = createWatchGate(false);
  assert.equal(gate.note({ sourcesChanged: false }), true);
  gate.hide(false);
  assert.equal(gate.note({ sourcesChanged: false }), false);
  assert.equal(gate.note({ sourcesChanged: false }), false);
  assert.deepEqual(gate.show(), { refresh: true, sources: true });
  assert.equal(gate.show(), null); // nothing missed since
});

test("watch gate: a refresh pending when the tab hides runs again when it is back; none missed means no update", () => {
  const gate = createWatchGate(false);
  gate.hide(true);
  assert.deepEqual(gate.show(), { refresh: true, sources: true });
  gate.hide(false);
  assert.equal(gate.show(), null);
});

test("watch gate: the sources flag accumulates over a burst and is taken once", () => {
  const gate = createWatchGate(false);
  gate.note({ sourcesChanged: false });
  gate.note({ sourcesChanged: true });
  gate.note({ sourcesChanged: false });
  assert.equal(gate.takeSources(), true);
  assert.equal(gate.takeSources(), false);
});

test("watch gate: starts hidden when the page opens in a hidden tab", () => {
  const gate = createWatchGate(true);
  assert.equal(gate.note({}), false);
  assert.notEqual(gate.show(), null);
});
