// R3c: the detail of the first record after a stretch that a reduced source skipped (flags & 128) says so; the others do not.
import assert from "node:assert/strict";
import { test } from "node:test";
import { installGlobalDom } from "./fixtures/fake-dom.mjs";

installGlobalDom();
const { Detail } = await import("../../src/dmslog/web/js/detail.js");

const el = () => document.createElement("div");
const refs = () => ({ meta: el(), text: el(), actions: el(), contextButton: el(), contextClose: el(), contextSame: el(), contextBox: el(), contextList: el(), copyButton: el(), copyStatus: el() });
const REDUCED = { skipped_bytes: 3 * 1024 * 1024, skipped_lines: 12345, skipped_from_ts: Date.UTC(2026, 3, 1, 10, 0, 0), skipped_to_ts: Date.UTC(2026, 3, 2, 11, 30, 0) };
const noteOf = (r) => r.meta.all().find((n) => n.className.includes("dt-skip-note"));

function render(entry, info) {
  const r = refs();
  const detail = new Detail(r, { getCase: () => "c1", getSourceName: () => "svc.log", getSourceInfo: () => info, onError() {} });
  detail.renderHeader(entry);
  return r;
}

test("the record after the skipped stretch carries the note with lines, size and times", () => {
  const r = render({ id: 9, ts: 1, source_id: 1, level: 2, flags: 128 | 2 }, { name: "svc.log", reduced: REDUCED });
  const note = noteOf(r);
  assert.ok(note);
  assert.equal(note.attrs.role, "note");
  assert.equal(note.textContent, "Lines before this record were skipped: 12,345 lines (3.0 MB), 2026-04-01 10:00:00.000 to 2026-04-02 11:30:00.000.");
});

test("without the numbers of the source the note still says that lines were skipped", () => {
  const r = render({ id: 9, ts: 1, source_id: 1, flags: 128 }, { name: "svc.log" });
  assert.equal(noteOf(r).textContent, "Lines before this record were skipped.");
});

test("any other record has no note", () => {
  for (const flags of [0, 2, 32, 127, undefined]) {
    assert.equal(noteOf(render({ id: 9, ts: 1, source_id: 1, flags }, { name: "svc.log", reduced: REDUCED })), undefined);
  }
});
