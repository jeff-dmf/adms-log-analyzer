// UI-13: the pure part of the status bar: counts (exact and estimated), message expiry by kind, stale handles.
import test from "node:test";
import assert from "node:assert/strict";

import { S } from "../../src/dmslog/web/js/strings.js";
import {
  DEFAULT_TIMEOUT_MS, compactNumber, createMessageState, formatCount, messageTimeout,
} from "../../src/dmslog/web/js/status-bar-model.js";

test("compactNumber gives the short form of an estimate", () => {
  assert.equal(compactNumber(0), "0");
  assert.equal(compactNumber(999), "999");
  assert.equal(compactNumber(1234), "1,234");
  assert.equal(compactNumber(12345), "12.3K");
  assert.equal(compactNumber(285093), "285K");
  assert.equal(compactNumber(1_200_000), "1.2M");
  assert.equal(compactNumber(1_000_000), "1M");
  assert.equal(compactNumber(3_450_000_000), "3.5B");
});

test("formatCount: exact is the full number, an estimate is 'about' and compact; plural by pairs", () => {
  assert.equal(formatCount({ n: 285093, exact: true }, S), "285,093 records");
  assert.equal(formatCount({ n: 1, exact: true }, S), "1 record");
  assert.equal(formatCount({ n: 0, exact: true }, S), "0 records");
  assert.equal(formatCount({ n: 1_200_000, exact: false }, S), "about 1.2M records");
  assert.equal(formatCount({ n: 1, exact: false }, S), "about 1 record");
});

test("the timeout depends on the kind: errors stay, the rest go away after 8 s", () => {
  assert.equal(messageTimeout("error"), 0);
  for (const kind of ["info", "success", "warning"]) {
    assert.equal(messageTimeout(kind), DEFAULT_TIMEOUT_MS);
  }
  assert.equal(DEFAULT_TIMEOUT_MS, 8000);
});

test("a message expires at its time, an error never, and the last message replaces the previous", () => {
  const state = createMessageState();
  state.show("done", { kind: "success" }, 1000);
  assert.equal(state.expire(8999), false);
  assert.equal(state.current.text, "done");
  assert.equal(state.expire(9000), true);
  assert.equal(state.current, null);

  state.show("boom", { kind: "error" }, 0);
  assert.equal(state.expire(10 ** 9), false);
  assert.equal(state.current.kind, "error");

  state.show("later", { kind: "info" }, 5);
  assert.equal(state.current.text, "later");
  state.show("", {}, 6);
  assert.equal(state.current, null);
});

test("an explicit timeoutMs wins, and a stale dismiss handle does not clear a newer message", () => {
  const state = createMessageState();
  const first = state.show("a", { kind: "error", timeoutMs: 100 }, 0);
  assert.equal(first.expiresAt, 100);
  const second = state.show("b", { kind: "info" }, 0);
  assert.equal(state.dismiss(first.id), false);
  assert.equal(state.current.text, "b");
  assert.equal(state.dismiss(second.id), true);
  assert.equal(state.current, null);
  assert.equal(state.show("x", { kind: "weird" }, 0).kind, "info");
});
