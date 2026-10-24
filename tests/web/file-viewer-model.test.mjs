// R3c and R4c: the pure parts. The reduced-source notes and the "Large files" choice (case-files-model.js), the group of
// files that were not indexed, the errors of the viewer and its load/reveal state (file-viewer-model.js). Synthetic data.
import assert from "node:assert/strict";
import { test } from "node:test";
import { installGlobalDom } from "./fixtures/fake-dom.mjs";

installGlobalDom();
const files = await import("../../src/dmslog/web/js/case-files-model.js");
const model = await import("../../src/dmslog/web/js/file-viewer-model.js");

const MB = 1024 * 1024;
const REDUCED = { cap: 256 * MB, head_end: MB + 40, tail_start: 7000 * MB, skipped_bytes: 6700 * MB, skipped_lines: 25000000, skipped_from_ts: Date.UTC(2026, 0, 2, 3, 4, 5), skipped_to_ts: Date.UTC(2026, 0, 14, 1, 2, 3) };
const skipped = (id, member, error, extra = {}) => ({ id, name: member.split("/").pop(), member, status: "skipped", error, size: 1000, records: 0, ...extra });

test("extensions: lowercase with the dot, the whole tail of a db3 side file, none for a bare name", () => {
  assert.equal(model.extOf("a/b/Config.XML"), ".xml");
  assert.equal(model.extOf("x\\y\\q.db3-wal"), ".db3-wal");
  assert.equal(model.extOf("README"), "");
  assert.equal(model.extOf(".hidden"), "");
  assert.equal(model.extOf(null), "");
});

test("only a skipped, not-indexable file with a text extension can be viewed", () => {
  assert.equal(model.isViewable(skipped(1, "A/app.config", "not_indexable")), true);
  assert.equal(model.isViewable(skipped(2, "A/x.blg", "not_indexable")), false);
  assert.equal(model.isViewable(skipped(3, "A/app.config", "out_of_scope")), false);
  assert.equal(model.isViewable({ id: 4, name: "a.xml", member: "a.xml", status: "ok", error: null }), false);
  assert.equal(model.isViewable(null), false);
});

test("subgroups: other logs, text files, binary files, over the limit; the type of a binary is named", () => {
  const sources = [
    skipped(1, "L/install.log", "out_of_scope"), skipped(2, "L/a.config", "not_indexable"), skipped(3, "L/b.config", "not_indexable"),
    skipped(4, "L/c.xml", "not_indexable"), skipped(5, "L/perf.blg", "not_indexable"), skipped(6, "L/q.db3-wal", "not_indexable"),
    skipped(7, "L/huge.log", "too_large"), { id: 8, name: "ok.log", member: "ok.log", status: "ok" },
  ];
  const root = model.buildNotIndexed(sources);
  assert.equal(root.name, "Not indexed (7)");
  assert.deepEqual(root.children.map((c) => c.name), ["Other logs (not selected) (1)", "Text files (3)", "Binary files (2)", "Over the limit (1)"]);
  const text = root.children[1];
  assert.deepEqual(text.children.map((c) => c.name), [".config (2)", ".xml (1)"], "the biggest extension first");
  assert.equal(root.children[2].children.some((c) => c.name === "Performance counter log (.blg) (1)"), true);
  assert.equal(root.children[2].children.some((c) => c.name === "SQLite database (.db3) (1)"), true);
  assert.deepEqual(root.ids, [], "nothing under it can be checked");
  assert.deepEqual(root.allIds.sort(), [1, 2, 3, 4, 5, 6, 7]);
  const leaf = text.children[0].children[0];
  assert.equal(leaf.viewable, true);
  assert.equal(root.children[2].children[0].children[0].viewable, false);
  assert.equal(model.buildNotIndexed([{ id: 1, status: "ok" }]), null);
});

test("flat-list rows follow the open folders; a filter opens them all", () => {
  const root = model.buildNotIndexed([skipped(1, "L/a.config", "not_indexable"), skipped(2, "L/b.xml", "not_indexable")]);
  assert.deepEqual(model.niRows(root, new Set(), false).map((r) => r.node.name), ["Not indexed (2)"]);
  const open = new Set([root.key, root.children[0].key]);
  assert.deepEqual(model.niRows(root, open, false).map((r) => [r.level, r.node.name]), [[1, "Not indexed (2)"], [2, "Text files (2)"], [3, ".config (1)"], [3, ".xml (1)"]]);
  assert.equal(model.niRows(root, new Set(), true).length, 1 + 1 + 2 + 2);
  assert.deepEqual(model.niRows(null, new Set(), true), []);
});

test("the Reduced title says what was indexed, what was skipped, when and the limit", () => {
  const title = files.reducedTitle({ size: 7669 * MB, reduced: REDUCED });
  assert.match(title, /^Indexed the first 1\.0 MB and the last 669 MB of 7\.5 GB\./);
  assert.match(title, /Skipped 6\.5 GB in between \(25,000,000 lines, 2026-01-02 03:04:05\.000 to 2026-01-14 01:02:03\.000\)/);
  assert.match(title, /Per-file limit: 256 MB\.$/);
  assert.equal(files.reducedTitle({ size: 1, reduced: null }), "");
  assert.equal(files.isReduced({ reduced: null }), false);
  assert.equal(files.isReduced({ reduced: REDUCED }), true);
  assert.equal(files.badgeText(), "Reduced");
});

test("the Reduced title survives unknown times and sizes (never NaN or undefined)", () => {
  const title = files.reducedTitle({ size: null, reduced: { ...REDUCED, skipped_from_ts: null, skipped_to_ts: null, tail_start: null } });
  assert.match(title, /of \?\./);
  assert.match(title, /unknown time to unknown time/);
  assert.doesNotMatch(title, /NaN|undefined|null/);
});

test("the record after a skipped stretch carries a note; any other record does not", () => {
  assert.equal(files.isAfterSkip({ flags: 128 }), true);
  assert.equal(files.isAfterSkip({ flags: 128 | 32 | 2 }), true);
  assert.equal(files.isAfterSkip({ flags: 127 }), false);
  assert.equal(files.isAfterSkip({}), false);
  assert.equal(files.afterSkipNote({ flags: 0 }, { reduced: REDUCED }), "");
  const note = files.afterSkipNote({ flags: 128 }, { reduced: REDUCED });
  assert.match(note, /^Lines before this record were skipped: 25,000,000 lines \(6\.5 GB\), 2026-01-02 03:04:05\.000 to 2026-01-14 01:02:03\.000\.$/);
  assert.equal(files.afterSkipNote({ flags: 128 }, {}), "Lines before this record were skipped.");
});

test("Large files: 256 MB is the default, then 1 GB and the whole file; anything else is the default", () => {
  assert.deepEqual(files.capChoices().map(([value]) => value), ["256", "1024", "0"]);
  assert.equal(files.capChoices()[0][1], "First 1 MB and last 256 MB (default)");
  assert.equal(files.capMbOf("1024"), 1024);
  assert.equal(files.capMbOf("0"), 0);
  assert.equal(files.capMbOf("junk"), 256);
  assert.equal(files.capMbOf(undefined), 256);
});

test("the preview note names the limit, or is empty for the whole file", () => {
  assert.equal(files.previewCappedText("256"), "Over the 256 MB per-file limit: only the first 1 MB and the last 256 MB will be indexed.");
  assert.match(files.previewCappedText("1024"), /Over the 1 GB per-file limit: only the first 1 MB and the last 1 GB/);
  assert.equal(files.previewCappedText("0"), "");
});

test("a failed answer maps to one text by code", () => {
  const code = (status, code, extra = {}) => model.viewerError({ status, code, ...extra });
  assert.equal(code(404, "source_not_found").code, "not_found");
  assert.equal(code(409, "not_viewable").code, "not_viewable");
  assert.equal(code(422, "binary_content").text, "This is not a text file.");
  assert.equal(code(410, "container_missing").text, "The collection file is no longer available.");
  assert.equal(code(413, "file_too_large", { limit: 4 * MB }).text, "This file is larger than 4.0 MB and is not shown.");
  assert.match(code(413, "file_too_large").text, /larger than 4\.0 MB/, "no limit in the answer: the documented one");
  assert.equal(code(500, "boom", { message: "bad" }).code, "failed");
  assert.equal(model.viewerError({ name: "AbortError" }).code, "aborted");
});

test("facts and the count of hidden values", () => {
  const file = model.normalizeText({ source_id: 3, name: "a.config", member: "A/a.config", kind: "x", size: 2048, encoding: "utf-8", lines: 1200, hidden: 3, revealed: false, text: "t" });
  assert.equal(model.factsText(file), "2.0 KB - 1,200 lines - utf-8");
  assert.equal(model.hiddenText(file), "3 values hidden");
  assert.equal(model.hiddenText({ ...file, revealed: true }), "3 hidden values shown");
  assert.equal(model.hiddenText({ ...file, hidden: 0 }), "No values hidden");
  assert.equal(model.normalizeText({ text: 5 }), null);
  assert.equal(model.normalizeText(null), null);
});

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}
const answer = (reveal, extra = {}) => ({ source_id: 3, name: "a.config", member: "A/a.config", size: 10, encoding: "utf-8", lines: 2, hidden: 2, revealed: reveal, text: reveal ? "pw=secret" : "pw=\u2022\u2022\u2022\u2022", ...extra });

test("the viewer state loads masked, reveals on request and hides again without a new request", async () => {
  const calls = [];
  const state = new model.ViewerState(async (caseId, id, options) => { calls.push([caseId, id, options.reveal]); return answer(options.reveal); });
  await state.open("c1", { id: 3 });
  assert.equal(state.phase, "ready");
  assert.equal(state.file.text, "pw=\u2022\u2022\u2022\u2022");
  await state.toggleReveal();
  assert.equal(state.showing, true);
  assert.equal(state.file.text, "pw=secret");
  await state.toggleReveal();
  assert.equal(state.file.text, "pw=\u2022\u2022\u2022\u2022");
  await state.toggleReveal();
  assert.deepEqual(calls, [["c1", 3, false], ["c1", 3, true]], "the original is requested once");
  state.reset();
  assert.equal(state.revealedFile, null, "nothing of the original stays after reset");
  assert.equal(state.file, null);
});

test("with nothing hidden there is nothing to reveal", async () => {
  const calls = [];
  const state = new model.ViewerState(async (c, i, o) => { calls.push(o.reveal); return answer(false, { hidden: 0 }); });
  await state.open("c1", { id: 3 });
  await state.toggleReveal();
  assert.deepEqual(calls, [false]);
  assert.equal(state.showing, false);
});

test("an error ends in the error phase; a failed reveal keeps the masked text", async () => {
  const state = new model.ViewerState(async () => { throw Object.assign(new Error("x"), { status: 422, code: "binary_content" }); });
  await state.open("c1", { id: 3 });
  assert.equal(state.phase, "error");
  assert.equal(state.error.code, "binary");
  let fail = false;
  const second = new model.ViewerState(async (c, i, o) => { if (o.reveal && fail) throw Object.assign(new Error("gone"), { status: 410, code: "container_missing" }); return answer(o.reveal); });
  await second.open("c1", { id: 3 });
  fail = true;
  await second.toggleReveal();
  assert.equal(second.phase, "ready");
  assert.equal(second.showing, false);
  assert.equal(second.error.code, "missing");
});

test("a late answer of an older opening is dropped", async () => {
  const first = deferred();
  const second = deferred();
  const queue = [first, second];
  const state = new model.ViewerState(() => queue.shift().promise);
  const a = state.open("c1", { id: 1 });
  const b = state.open("c1", { id: 2 });
  second.resolve(answer(false, { name: "second" }));
  await b;
  first.resolve(answer(false, { name: "first" }));
  await a;
  assert.equal(state.file.name, "second");
  assert.equal(state.source.id, 2);
});
