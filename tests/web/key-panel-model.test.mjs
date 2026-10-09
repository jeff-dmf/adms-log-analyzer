// Pure helpers of the "same key" panel.
import test from "node:test";
import assert from "node:assert/strict";

import { buildKeyRow, keyHeading } from "../../src/dmslog/web/js/key-panel-model.js";

test("a record row keeps the text literal and tolerates nulls", () => {
  const row = buildKeyRow({
    id: 5, source_id: 2, member: "Log/s.log", sensitive: true, ts: 0, tid: 31, tpl_code: "T-1", sev: 4,
    msg: "<img src=x onerror=alert(1)>",
  });
  assert.deepEqual(
    { id: row.id, source: row.source, sensitive: row.sensitive, tid: row.tid, code: row.code, text: row.text },
    { id: 5, source: "Log/s.log", sensitive: true, tid: "0x1f", code: "T-1", text: "<img src=x onerror=alert(1)>" },
  );
  assert.equal(row.time, "1970-01-01 00:00:00.000");
  const odd = buildKeyRow({ id: 6, source_id: null, member: null, sensitive: null, ts: null, tid: null, tpl_code: null, msg: null });
  assert.deepEqual(
    { time: odd.time, source: odd.source, sensitive: odd.sensitive, tid: odd.tid, code: odd.code, text: odd.text },
    { time: "", source: "", sensitive: false, tid: "", code: "", text: "" },
  );
});

test("heading shows the key type and value", () => {
  assert.equal(keyHeading("msgid", "a b"), "msgid = a b");
});
