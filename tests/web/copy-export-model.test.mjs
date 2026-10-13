// Pure helpers and the run flow of the "Copy as" menu with a fake api.
import test from "node:test";
import assert from "node:assert/strict";

import {
  actionsFor,
  buildRequest,
  createCopyExportFlow,
  fileName,
  kindAvailability,
  parseMaxRows,
  recordText,
  sourceChoices,
} from "../../src/dmslog/web/js/copy-export-model.js";

const ctx = (extra) => ({
  caseId: "demo",
  filter: { sev: { le: 2 } },
  columns: ["ts", "tid", "msg"],
  fieldColumns: ["op"],
  recordId: null,
  entry: null,
  ...extra,
});
const form = (extra) => ({
  kind: "values", source: "column:tid", separator: "comma", quote: "single", unique: true, bom: true,
  maxRows: "5000", events: { status: "fail", name: " SendWorks ", q: "" }, ...extra,
});

test("row limit is a whole number in range", () => {
  assert.equal(parseMaxRows("100000"), 100000);
  assert.equal(parseMaxRows(" 7 "), 7);
  for (const bad of ["", "0", "-1", "1.5", "1e3", "1000001", "abc", null, undefined]) {
    assert.equal(parseMaxRows(bad), null, String(bad));
  }
});

test("which kinds and actions are available", () => {
  assert.deepEqual(kindAvailability(null), { values: false, lines: false, record: false, view_csv: false, events_csv: false });
  assert.equal(kindAvailability(ctx()).record, false);
  assert.equal(kindAvailability(ctx({ recordId: 4 })).record, true);
  assert.deepEqual(actionsFor("values"), { copy: true, download: true });
  assert.deepEqual(actionsFor("view_csv"), { copy: false, download: true });
  assert.deepEqual(actionsFor("record"), { copy: true, download: false });
});

test("source choices: visible columns, fields and key types", () => {
  const choices = sourceChoices({ columns: ["ts", "msg"], fieldColumns: ["op"], keyTypes: ["gid"] });
  assert.deepEqual(choices.map((c) => c.value), ["column:ts", "column:msg", "column:fields.op", "key:gid"]);
  assert.ok(choices.every((c) => c.label.length > 0));
});

test("request of a value list", () => {
  assert.deepEqual(buildRequest(form(), ctx()), {
    kind: "values", filter: { sev: { le: 2 } }, max_rows: 5000, column: "tid",
    separator: "comma", quote: "single", unique: true,
  });
  assert.equal(buildRequest(form({ source: "key:gid" }), ctx()).key, "gid");
  assert.equal(buildRequest(form({ source: "key:gid" }), ctx()).column, undefined);
  assert.equal(buildRequest(form({ source: "column:fields.a:b" }), ctx()).column, "fields.a:b");
  assert.equal(buildRequest(form({ source: "nonsense" }), ctx()), null);
  assert.equal(buildRequest(form({ source: "other:x" }), ctx()), null);
  assert.equal(buildRequest(form({ quote: "weird", separator: "tab" }), ctx()).quote, "none");
  assert.equal(buildRequest(form({ separator: "tab" }), ctx()).separator, "newline");
});

test("request of the view CSV uses the visible columns and fields", () => {
  const body = buildRequest(form({ kind: "view_csv" }), ctx());
  assert.deepEqual(body.columns, ["ts", "tid", "msg", "fields.op"]);
  assert.equal(body.bom, true);
  assert.equal(body.column, undefined);
  assert.deepEqual(buildRequest(form({ kind: "view_csv" }), ctx({ columns: [], fieldColumns: [] })).columns,
    ["ts", "pid", "tid", "level", "template", "source", "msg"]);
});

test("request of the events CSV keeps only the filled filters", () => {
  const body = buildRequest(form({ kind: "events_csv", bom: false }), ctx());
  assert.deepEqual(body.events, { status: "fail", name: "SendWorks" });
  assert.equal(body.bom, false);
  assert.equal(body.filter.sev.le, 2);
});

test("no request without a case, with a bad limit or for the record kind", () => {
  assert.equal(buildRequest(form(), ctx({ caseId: null })), null);
  assert.equal(buildRequest(form({ maxRows: "0" }), ctx()), null);
  assert.equal(buildRequest(form({ kind: "record" }), ctx()), null);
  assert.equal(buildRequest(form({ kind: "nope" }), ctx()), null);
});

test("file names are safe and carry the extension", () => {
  const now = new Date(2030, 0, 2, 3, 4, 5);
  assert.equal(fileName("view_csv", "demo", now), "dmslog-demo-view-20300102-030405.csv");
  assert.equal(fileName("values", "a b/c:d", now), "dmslog-a_b_c_d-values-20300102-030405.txt");
  assert.equal(fileName("events_csv", "x", now), "dmslog-x-operations-20300102-030405.csv");
});

test("the selected record as text", () => {
  const entry = { ts: Date.UTC(2030, 0, 2, 3, 4, 5, 6), pid: 12, tid: 255, level: 3, text: "first\n  at frame()" };
  assert.equal(recordText(entry), "2030-01-02 03:04:05.006 [PID=12][TID=0xff] ERROR - first\n  at frame()\n");
  assert.equal(recordText({ ts: null, text: "plain" }), "plain\n");
  assert.equal(recordText(null), "");
});

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

function setup(overrides = {}) {
  const calls = [];
  const states = [];
  const saved = [];
  const copied = [];
  const current = { ctx: ctx() };
  const api = {
    exportView(caseId, body, as, signal) {
      const d = deferred();
      calls.push({ caseId, body, as, signal, ...d });
      return d.promise;
    },
  };
  const flow = createCopyExportFlow({
    api,
    getContext: () => current.ctx,
    onState: (state) => states.push(state),
    clipboard: overrides.clipboard,
    sink: { copy: (text) => { copied.push(text); return true; }, save: (data, name) => saved.push({ data, name }) },
  });
  return { flow, calls, states, saved, copied, current };
}

test("copy: the server text goes to the clipboard and truncation is warned", async () => {
  const clips = [];
  const s = setup({ clipboard: { writeText: async (t) => clips.push(t) } });
  const running = s.flow.run(form(), "copy");
  assert.equal(s.states.at(-1).status, "working");
  assert.equal(s.calls[0].as, "text");
  assert.equal(s.calls[0].caseId, "demo");
  s.calls[0].resolve({ data: "1\n2\n", rows: 2, truncated: true });
  await running;
  assert.deepEqual(clips, ["1\n2\n"]);
  const last = s.states.at(-1);
  assert.equal(last.status, "done");
  assert.match(last.message, /Copied 2 rows/);
  assert.match(last.message, /Only the first 2 rows/);
  assert.equal(last.truncated, true);
});

test("copy falls back to the legacy way and reports a blocked copy", async () => {
  const s = setup({ clipboard: { writeText: async () => { throw new Error("blocked"); } } });
  const running = s.flow.run(form(), "copy");
  s.calls[0].resolve({ data: "x", rows: 1, truncated: false });
  await running;
  assert.deepEqual(s.copied, ["x"]);
  assert.equal(s.states.at(-1).status, "done");
});

test("download: a blob is saved under a safe name", async () => {
  const s = setup();
  const running = s.flow.run(form({ kind: "view_csv" }), "download");
  assert.equal(s.calls[0].as, "blob");
  assert.equal(s.calls[0].body.kind, "view_csv");
  s.calls[0].resolve({ data: "BLOB", rows: 10, truncated: false });
  await running;
  assert.equal(s.saved.length, 1);
  assert.equal(s.saved[0].data, "BLOB");
  assert.match(s.saved[0].name, /^dmslog-demo-view-\d{8}-\d{6}\.csv$/);
  assert.match(s.states.at(-1).message, /Downloaded 10 rows/);
  assert.equal(s.states.at(-1).truncated, false);
});

test("an empty result says so and saves nothing", async () => {
  const s = setup();
  const running = s.flow.run(form({ kind: "view_csv" }), "download");
  s.calls[0].resolve({ data: "", rows: 0, truncated: false });
  await running;
  assert.equal(s.saved.length, 0);
  assert.match(s.states.at(-1).message, /Nothing to export/);
});

test("a new run aborts the previous one and drops its answer", async () => {
  const s = setup();
  const first = s.flow.run(form(), "download");
  const second = s.flow.run(form({ kind: "lines" }), "download");
  assert.equal(s.calls[0].signal.aborted, true);
  s.calls[0].resolve({ data: "old", rows: 1, truncated: false });
  s.calls[1].resolve({ data: "new", rows: 1, truncated: false });
  await Promise.all([first, second]);
  assert.deepEqual(s.saved.map((x) => x.data), ["new"]);
});

test("a case change while waiting drops the answer; reset aborts", async () => {
  const s = setup();
  const running = s.flow.run(form(), "download");
  s.current.ctx = ctx({ caseId: "other" });
  s.calls[0].resolve({ data: "x", rows: 1, truncated: false });
  await running;
  assert.equal(s.saved.length, 0);
  s.current.ctx = ctx();
  const again = s.flow.run(form(), "download");
  s.flow.reset();
  assert.equal(s.calls[1].signal.aborted, true);
  s.calls[1].resolve({ data: "late", rows: 1, truncated: false });
  await again;
  assert.equal(s.saved.length, 0);
  assert.equal(s.states.at(-1).status, "idle");
});

test("errors: server message, no case, bad limit", async () => {
  const s = setup();
  const running = s.flow.run(form(), "copy");
  s.calls[0].reject(Object.assign(new Error("boom"), { name: "ApiError" }));
  await running;
  assert.equal(s.states.at(-1).status, "error");
  assert.match(s.states.at(-1).message, /boom/);
  s.current.ctx = ctx({ caseId: null });
  await s.flow.run(form(), "copy");
  assert.match(s.states.at(-1).message, /Open a case/);
  s.current.ctx = ctx();
  await s.flow.run(form({ maxRows: "0" }), "copy");
  assert.match(s.states.at(-1).message, /whole number/);
  assert.equal(s.calls.length, 1);
});

test("the selected record is copied without a request", async () => {
  const clips = [];
  const s = setup({ clipboard: { writeText: async (t) => clips.push(t) } });
  s.current.ctx = ctx({ recordId: 3, entry: { id: 3, ts: null, text: "hello" } });
  await s.flow.run(form({ kind: "record" }), "copy");
  assert.deepEqual(clips, ["hello\n"]);
  assert.equal(s.calls.length, 0);
  s.current.ctx = ctx();
  await s.flow.run(form({ kind: "record" }), "copy");
  assert.match(s.states.at(-1).message, /Select a record/);
});
