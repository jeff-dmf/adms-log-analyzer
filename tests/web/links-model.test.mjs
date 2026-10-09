// Pure helpers of the Links section and the side-by-side view.
import test from "node:test";
import assert from "node:assert/strict";

import {
  bestRow, buildKeyChips, buildLinkRows, buildPane, buildSideView, confidenceLabel, confidenceTone, revealLabel,
} from "../../src/dmslog/web/js/links-model.js";
import { S } from "../../src/dmslog/web/js/strings.js";

const link = (entry_id, kind, confidence, sensitive, extra = {}) => ({
  entry_id, kind, confidence, direction: "out", source: { member: "m" + entry_id, sensitive },
  ts: 1000 * entry_id, tid: 255, tpl_code: "T-1", msg: "text " + entry_id, ...extra,
});

test("confidence is always a text label, never only a colour", () => {
  assert.equal(confidenceLabel(3), "key");
  assert.equal(confidenceLabel(2), "ts+TID");
  assert.equal(confidenceLabel(1), "second");
  assert.equal(confidenceLabel(0), "window (heuristic)");
  assert.equal(confidenceLabel(9), S.lnkConfidenceUnknown);
  assert.deepEqual([3, 2, 1, 0].map(confidenceTone), ["high", "good", "fair", "low"]);
});

test("rows are ordered: normal/sensitive pair first, then confidence, then time", () => {
  const rows = buildLinkRows([
    link(5, "same_key", 3, false),
    link(4, "normal_sensitive", 1, true),
    link(3, "normal_sensitive", 3, true),
    link(2, "retry", 2, false),
    link(1, "normal_sensitive", 3, true, { ts: null }),
  ]);
  assert.deepEqual(rows.map((r) => r.entryId), [1, 3, 4, 2, 5]);
  assert.equal(bestRow(rows).entryId, 1);
  assert.equal(bestRow([]), null);
});

test("row fields: side, time, tid in hex, nulls tolerated", () => {
  const [row] = buildLinkRows([link(7, "normal_sensitive", 2, true)]);
  assert.equal(row.sideLabel, "sensitive");
  assert.equal(row.confidenceLabel, "ts+TID");
  assert.equal(row.tid, "TID 0xff");
  assert.equal(row.time, "1970-01-01 00:00:07.000");
  assert.equal(row.code, "T-1");
  const [odd] = buildLinkRows([{ entry_id: 1, kind: "weird", confidence: 1, source: null, ts: null, tid: null, msg: null }]);
  assert.equal(odd.sensitive, null);
  assert.equal(odd.sideLabel, S.lnkSideUnknown);
  assert.equal(odd.kindLabel, "weird");
  assert.equal(odd.tid, "");
  assert.equal(odd.text, "");
  assert.deepEqual(buildLinkRows(undefined), []);
});

test("reveal button label names the counterpart side", () => {
  assert.equal(revealLabel({ sensitive: true }), "Show in sensitive");
  assert.equal(revealLabel({ sensitive: false }), "Show in normal");
  assert.equal(revealLabel({ sensitive: null }), S.lnkShowLinked);
});

test("key chips are deduplicated, kept literal and capped", () => {
  const keys = [
    { key_type: "msgid", value: "A<b>&" }, { key_type: "msgid", value: "A<b>&" },
    { key_type: "gid", value: "g" }, null, { key_type: "x" },
  ];
  assert.deepEqual(buildKeyChips({ keys }), [
    { type: "msgid", value: "A<b>&", label: "msgid: A<b>&" },
    { type: "gid", value: "g", label: "gid: g" },
  ]);
  const many = Array.from({ length: 100 }, (_, i) => ({ key_type: "k", value: String(i) }));
  assert.equal(buildKeyChips({ keys: many }).length, 40);
  assert.deepEqual(buildKeyChips(null), []);
});

test("side-by-side view keeps normal left, aligned headers and the full text", () => {
  const a = { id: 1, sensitive: false, member: "n.log", ts: 0, tid: 16, tpl: "T-9", text: "msg\ncont", cont_truncated: false };
  const b = { id: 2, sensitive: true, member: "s.log", ts: 0, tid: 16, tpl: null, text: "payload", cont_truncated: true };
  const view = buildSideView({ a, b, kind: "normal_sensitive", confidence: 3 });
  assert.equal(view.left.title, "Normal");
  assert.equal(view.right.title, "Sensitive");
  assert.deepEqual(view.left.headers.map((h) => h.label), view.right.headers.map((h) => h.label));
  assert.deepEqual(view.left.headers.map((h) => h.text), ["n.log", "1970-01-01 00:00:00.000", "0x10", "T-9"]);
  assert.equal(view.right.headers[3].text, S.sbsNoValue);
  assert.equal(view.left.text, "msg\ncont");
  assert.equal(view.right.truncated, true);
  assert.equal(view.kindText, "Link: normal / sensitive, confidence key");
  assert.equal(buildSideView({ a }), null);
  assert.equal(buildPane(null), null);
});
