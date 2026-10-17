// Pure helpers of the "same key" panel.
import test from "node:test";
import assert from "node:assert/strict";

import { buildKeyRow, createShowKeyInGrid, filterHasKey, keyCountText, keyFilter, keyHeading } from "../../src/dmslog/web/js/key-panel-model.js";

const lookups = {
  getSourceName: (id) => (id === 2 ? "C:\\ADMS\\Log\\WfmAdapter_41.sensitive.log" : undefined),
  getTypeLabel: (id) => (id === 9 ? "Operation started" : ""),
};

test("a record row keeps the text literal, shows the file name and the type title, never the path or the code", () => {
  const row = buildKeyRow({
    id: 5, source_id: 2, member: "Log/s.log", sensitive: true, ts: 0, tid: 31, level: 3, tpl_id: 9, tpl_code: "T-1", sev: 4,
    msg: "<img src=x onerror=alert(1)>",
  }, lookups);
  assert.deepEqual(
    { id: row.id, source: row.source, sensitive: row.sensitive, level: row.level, type: row.type, text: row.text },
    { id: 5, source: "WfmAdapter_41.log", sensitive: true, level: "ERROR", type: "Operation started", text: "<img src=x onerror=alert(1)>" },
  );
  assert.equal(row.sourceTitle, "WfmAdapter_41.sensitive.log (sensitive)");
  assert.equal(row.time, "1970-01-01 00:00:00.000");
  assert.equal(JSON.stringify(row).includes("T-1"), false, "the template code is not carried");
  assert.equal(JSON.stringify(row).includes("ADMS"), false, "the directory is not carried");
});

test("nulls, unknown source and unknown type are tolerated", () => {
  const odd = buildKeyRow({ id: 6, source_id: null, member: null, sensitive: null, ts: null, tid: null, level: null, tpl_id: null, tpl_code: "X", msg: null }, lookups);
  assert.deepEqual(
    { time: odd.time, source: odd.source, sensitive: odd.sensitive, level: odd.level, type: odd.type, text: odd.text },
    { time: "", source: "", sensitive: false, level: "", type: "Unclassified", text: "" },
  );
  // a source the case does not list is named by its id: the member (file name only) is used instead
  const row = buildKeyRow({ id: 7, source_id: 3, member: "Log/dir/a.log", sensitive: false, ts: 1, level: 99, tpl_id: 4, msg: "m" }, { getSourceName: (id) => String(id), getTypeLabel: () => null });
  assert.equal(row.source, "a.log");
  assert.equal(row.level, "");
  assert.equal(row.type, "Unclassified", "a type without a title is neutral, never the code");
  // no lookups at all
  assert.equal(buildKeyRow({ id: 8, source_id: 1, member: "x/y.log", ts: 1, msg: "" }).source, "y.log");
  // a sensitive source recognised by its name even when the record does not say so
  assert.equal(buildKeyRow({ id: 9, source_id: 2, member: "q.log", sensitive: null, ts: 1 }, lookups).sensitive, true);
});

test("heading, filter and count with the right plural", () => {
  assert.equal(keyHeading("msgid", "a b"), "msgid = a b");
  assert.deepEqual(keyFilter("msgid", 42), { key: { type: "msgid", eq: "42" } });
  assert.equal(keyCountText(1, false), "1 record");
  assert.equal(keyCountText(2, false), "2 records");
  assert.equal(keyCountText(0, false), "0 records");
  assert.equal(keyCountText(1, true), "1 record so far (more available)");
  assert.equal(keyCountText(3, true), "3 records so far (more available)");
  assert.equal(keyCountText(NaN, false), "0 records");
});

test("Show in grid plan: narrow a view with conditions, key joins an empty or unchained one, a full stack stops", async () => {
  const { planShowInGrid } = await import("../../src/dmslog/web/js/key-panel-model.js");
  const base = { chained: true, ownEmpty: false, depth: 1, maxDepth: 10 };
  assert.equal(planShowInGrid(base), "narrow");
  assert.equal(planShowInGrid({ ...base, ownEmpty: true }), "in-place");
  assert.equal(planShowInGrid({ ...base, chained: false }), "in-place");
  assert.equal(planShowInGrid({ ...base, depth: 9 }), "full");
  assert.equal(planShowInGrid({ ...base, depth: 8 }), "narrow");
  assert.equal(planShowInGrid({ ...base, ownEmpty: true, depth: 9 }), "in-place", "an empty view never needs a new level");
  assert.equal(planShowInGrid({ ...base, depth: NaN }), "in-place");
  assert.equal(planShowInGrid({ ...base, maxDepth: null }), "in-place");
  assert.equal(planShowInGrid({ ...base, maxDepth: 0, depth: 0 }), "full");
  assert.equal(planShowInGrid({ ...base, alreadyShown: true }), "none", "a second click does not stack an identical view");
  assert.equal(planShowInGrid({ ...base, alreadyShown: true, depth: 9 }), "none");
  assert.equal(planShowInGrid({ ...base, alreadyShown: "yes" }), "narrow", "only a real true counts");
});

test("filterHasKey finds the key at any depth of the AND tree, and only the same key", () => {
  const key = keyFilter("gid", "7");
  assert.equal(filterHasKey(key, key), true);
  assert.equal(filterHasKey({ and: [{ sev: { le: 3 } }, key] }, key), true);
  assert.equal(filterHasKey({ and: [{ and: [key] }, { sev: { le: 3 } }] }, key), true);
  assert.equal(filterHasKey({ and: [{ sev: { le: 3 } }] }, key), false);
  assert.equal(filterHasKey(keyFilter("gid", "8"), key), false);
  assert.equal(filterHasKey(keyFilter("msgid", "7"), key), false);
  assert.equal(filterHasKey({ key: { type: "gid" } }, key), false);
  assert.equal(filterHasKey(null, key), false);
  assert.equal(filterHasKey("x", key), false);
});

function harness(over = {}) {
  const calls = [];
  const st = { userFilter: null, parents: null, view: { chained: true, ownEmpty: false, depth: 1, maxDepth: 10 }, caseId: "A", narrowed: true };
  const run = createShowKeyInGrid({
    getCase: () => st.caseId,
    getUserFilter: () => st.userFilter,
    getParentsFilter: () => st.parents,
    state: () => st.view,
    narrowWith: (f) => { calls.push(["narrow", f]); return st.narrowed; },
    applyHere: async (f) => { calls.push(["here", f]); },
    message: (t, e) => calls.push(["message", t, e]),
    onError: (e) => calls.push(["error", e.message]),
    ...over,
  });
  return { run, calls, st };
}
const arg = { filter: keyFilter("gid", "7") };

test("Show in grid: a view with conditions opens ONE child that already holds the key (a single reload)", async () => {
  const { run, calls } = harness();
  await run(arg);
  assert.deepEqual(calls, [["narrow", arg.filter]], "no second applyHere after the narrowing");
});

test("Show in grid: an empty view or no stack applies the key in place", async () => {
  const a = harness();
  a.st.view = { chained: true, ownEmpty: true, depth: 0, maxDepth: 10 };
  await a.run(arg);
  assert.deepEqual(a.calls, [["here", arg.filter]]);
  const b = harness();
  b.st.view = { chained: false, ownEmpty: false, depth: 0, maxDepth: 0 };
  await b.run(arg);
  assert.deepEqual(b.calls, [["here", arg.filter]]);
});

test("Show in grid: repeated clicks never duplicate the key, with or without the stack", async () => {
  // the view {sev} + key (no stack loaded): the key is inside the AND
  const a = harness();
  a.st.view = { chained: false, ownEmpty: false, depth: 0, maxDepth: 0 };
  a.st.userFilter = { and: [{ sev: { le: 3 } }, arg.filter] };
  await a.run(arg);
  assert.deepEqual(a.calls, []);
  // the key is already in an ancestor view
  const b = harness();
  b.st.parents = { and: [arg.filter] };
  await b.run(arg);
  assert.deepEqual(b.calls, []);
  // the filter of this very view
  const c = harness();
  c.st.userFilter = arg.filter;
  await c.run(arg);
  assert.deepEqual(c.calls, []);
});

test("Show in grid: a full stack says so and changes nothing; no case, no filter and failures are quiet or reported", async () => {
  const a = harness();
  a.st.view = { chained: true, ownEmpty: false, depth: 9, maxDepth: 10 };
  await a.run(arg);
  assert.deepEqual(a.calls, [["message", "The stack is limited to 10 views.", true]]);
  const b = harness();
  b.st.caseId = null;
  await b.run(arg);
  await harness().run({});
  assert.deepEqual(b.calls, []);
  const c = harness({ narrowWith: () => { throw new Error("boom"); } });
  await c.run(arg);
  assert.deepEqual(c.calls, [["error", "boom"]]);
});
