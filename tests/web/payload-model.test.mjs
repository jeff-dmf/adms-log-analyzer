// Pure helpers of the Payload section and the payload expected/absent notice.
import test from "node:test";
import assert from "node:assert/strict";

import {
  announcementTarget, buildChips, buildItems, carrierTarget, confidenceText, coveringWindow, displayView, groupDigits,
  payloadNotice, reasonText, splitChunks, windowRank,
} from "../../src/dmslog/web/js/payload-model.js";

const meta = (over = {}) => ({
  id: 7, entry_id: 20, event_id: 3, event_conf: 3, ref_entry_id: 10, pair_id: 8, role: "request", direction: "in",
  peer: "WFM", operation: "Op", fmt: "xml", root: "ns0:Root", well_formed: 1, truncated: 0, reason: null,
  char_len: 1234567, ts: 5000, sensitive: true, member: "m", ...over,
});

test("size, confidence and reasons are written as text", () => {
  assert.equal(groupDigits(1234567), "1,234,567");
  assert.equal(confidenceText(3), "key");
  assert.equal(confidenceText(2), "tid within operation");
  assert.equal(confidenceText(1), "time window");
  assert.equal(confidenceText(9), "unknown");
  assert.equal(reasonText("malformed"), "the payload is malformed");
  assert.equal(reasonText("weird_reason"), "weird_reason");
  assert.equal(reasonText(null), "");
});

test("chips skip empty values", () => {
  const chips = buildChips(meta({ peer: null, root: "" }));
  assert.deepEqual(chips.map((c) => c.field), ["fmt", "role", "direction", "operation"]);
  assert.equal(chips[0].label, "format: xml");
});

test("items mark payloads announced by the entry", () => {
  const items = buildItems({ payloads: [meta({ entry_id: 20, ref_entry_id: 10 }), meta({ id: 9, entry_id: 10, ref_entry_id: null })] }, 10);
  assert.equal(items[0].announced, true);
  assert.equal(items[1].announced, false);
  assert.deepEqual(buildItems(null, 1), []);
});

test("display view: formatted only when the reformat succeeded", () => {
  const ok = { payload: meta(), text: "<a><b/></a>", formatted: { text: "<a>\n  <b/>\n</a>", ok: true, truncated_display: false } };
  assert.equal(displayView(ok, "formatted").text, "<a>\n  <b/>\n</a>");
  assert.equal(displayView(ok, "raw").text, "<a><b/></a>");
  const bad = { payload: meta({ well_formed: 0 }), text: "<a>", formatted: { text: "<a>", ok: false, reason: "malformed" } };
  const view = displayView(bad, "formatted");
  assert.equal(view.shown, "raw");
  assert.equal(view.formatted, null);
  assert.ok(view.statusParts.some((p) => p.includes("malformed")));
  assert.ok(view.statusParts.includes("not well-formed"));
  assert.equal(displayView(null, "raw"), null);
});

test("large text is split into chunks that keep every character", () => {
  assert.equal(splitChunks("small").length, 1);
  const line = "x".repeat(99) + "\n";
  const big = line.repeat(5000); // 500k chars
  const chunks = splitChunks(big);
  assert.ok(chunks.length > 5);
  assert.equal(chunks.join(""), big);
  assert.ok(chunks.slice(0, -1).every((c) => c.endsWith("\n") && c.length <= 50000));
  const noBreaks = "y".repeat(450000);
  assert.equal(splitChunks(noBreaks).join(""), noBreaks);
});

test("navigation targets", () => {
  assert.deepEqual(announcementTarget(meta(), 20), { id: 10, ts: 3000 });
  assert.equal(announcementTarget(meta(), 10), null);
  assert.equal(announcementTarget(meta({ ref_entry_id: null }), 20), null);
  assert.deepEqual(carrierTarget(meta(), 10), { id: 20, ts: 5000 });
  assert.equal(carrierTarget(meta(), 20), null);
});

const win = (start, end, name, num, stream = null) => ({ ts_start: start, ts_end: end, stream_id: stream, detail: { level_name: name, level_num: num } });
const op = { id: 1, kind: "operation", ts_start: 1000, ts_end: 2000, stream_id: null };

test("level window ranks by printed name, then by number", () => {
  assert.equal(windowRank(win(0, 1, "Info", 99)), 5);
  assert.equal(windowRank(win(0, 1, "Verbose", 6)), 6);
  assert.equal(windowRank(win(0, 1, "Warning", 4)), 4);
  assert.equal(windowRank(win(0, 1, "Odd", 7)), 7);
  assert.equal(windowRank({ detail: null }), null);
});

test("covering window: start before, end after (or open), same stream", () => {
  assert.equal(coveringWindow(op, [win(1500, null, "Info", 5)]), null);
  assert.equal(coveringWindow(op, [win(0, 1500, "Info", 5)]), null);
  const covering = win(0, 3000, "Info", 5);
  assert.equal(coveringWindow(op, [win(0, 500, "Info", 5), covering]), covering);
  assert.equal(coveringWindow(op, [win(0, null, "Info", 5)]).ts_start, 0);
  assert.equal(coveringWindow({ ...op, stream_id: 1 }, [win(0, null, "Info", 5, 2)]), null);
  assert.equal(coveringWindow({ ...op, ts_start: null }, [covering]), null);
});

test("notice: expected-but-absent, not expected, unknown, or a payload exists", () => {
  const info = payloadNotice({ operation: op, payloadCount: 0, windows: [win(0, null, "Info", 5)] });
  assert.equal(info.kind, "absent");
  assert.match(info.text, /Payload expected but absent/);
  const warn = payloadNotice({ operation: op, payloadCount: 0, windows: [win(0, null, "Warning", 4)] });
  assert.equal(warn.kind, "not_expected");
  assert.match(warn.text, /below MessageLogLevel/);
  assert.equal(payloadNotice({ operation: op, payloadCount: 0, windows: [] }), null);
  assert.equal(payloadNotice({ operation: op, payloadCount: 1, windows: [win(0, null, "Info", 5)] }), null);
  assert.equal(payloadNotice({ operation: op, payloadCount: 0, windows: [win(0, null, "Verbose", 6)] }).kind, "absent");
});
