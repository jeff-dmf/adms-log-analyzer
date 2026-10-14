// Highlight rules, pure part: quick-form conditions, labels, keys and the marking store with a fake api.
import test from "node:test";
import assert from "node:assert/strict";

import {
  MARK_BATCH, PALETTE, buildCondition, colorLabel, createHighlightStore, cursorOf, defaultName, describeCondition,
  highlightNavKey, nextColor,
} from "../../src/dmslog/web/js/highlight-model.js";
import { S } from "../../src/dmslog/web/js/strings.js";

const tick = async () => { for (let i = 0; i < 8; i += 1) { await Promise.resolve(); } };

test("palette: eight named colors, the next free one first", () => {
  assert.equal(PALETTE.length, 8);
  for (const color of PALETTE) {
    assert.equal(colorLabel(color), S.hlColorNames[color]);
  }
  assert.equal(nextColor([]), "yellow");
  assert.equal(nextColor([{ color: "yellow" }, { color: "orange" }]), "red");
  assert.equal(nextColor(PALETTE.map((color) => ({ color }))), "yellow");
});

test("buildCondition writes filter-tree leaves", () => {
  assert.deepEqual(buildCondition("text", " timeout crew* ", ""), { cond: { text: { fts: "timeout crew*" } } });
  assert.deepEqual(buildCondition("substr", " a b ", ""), { cond: { text: { substr: " a b " } } });
  assert.deepEqual(buildCondition("regex", "GID: \\d+", ""), { cond: { text: { regex: "GID: \\d+" } } });
  assert.deepEqual(buildCondition("key", "123", " gid "), { cond: { key: { type: "gid", eq: "123" } } });
  assert.deepEqual(buildCondition("field", "Close", "op"), { cond: { field: { name: "op", eq: "Close" } } });
  assert.deepEqual(buildCondition("tpl", "WFM-081, 12", ""), { cond: { tpl: ["WFM-081", 12] } });
  assert.deepEqual(buildCondition("sev", "2", ""), { cond: { sev: { le: 2 } } });
  assert.deepEqual(buildCondition("text", "  ", ""), { error: "empty" });
  assert.deepEqual(buildCondition("key", "1", "bad type"), { error: "badName" });
  assert.deepEqual(buildCondition("field", "1", "a-b"), { error: "badName" });
  assert.deepEqual(buildCondition("tpl", "a b", ""), { error: "badTpl" });
  assert.deepEqual(buildCondition("sev", "9", ""), { error: "badSev" });
  assert.deepEqual(buildCondition("regex", "x".repeat(257), ""), { error: "tooLong" });
  assert.deepEqual(buildCondition("nope", "x", ""), { error: "empty" });
  for (const key of ["empty", "badName", "badTpl", "badSev", "tooLong"]) {
    assert.ok(S.hlBad[key], key);
  }
});

test("describeCondition and defaultName are short plain text", () => {
  assert.equal(describeCondition({ key: { type: "gid", eq: "<b>1</b>" } }), "key gid = <b>1</b>");
  assert.equal(describeCondition({ key: { type: "gid" } }), "has a key gid");
  assert.equal(describeCondition({ text: { regex: "a+" } }), "regex: /a+/");
  assert.equal(describeCondition({ text: { fts: "x" } }), "words: x");
  assert.equal(describeCondition({ text: { substr: "x" } }), 'text: "x"');
  assert.equal(describeCondition({ tpl: ["A", 2] }), "message type A, 2");
  assert.equal(describeCondition({ sev: { le: 2 } }), S.hlDescSev.replace("{value}", S.severityNames[2]));
  assert.equal(describeCondition({ field: { name: "op", eq: null } }), "op = null");
  const long = describeCondition({ and: [{ text: { fts: "y".repeat(400) } }] });
  assert.ok(long.startsWith("filter {") && long.length < 180 && long.endsWith("…"));
  assert.equal(describeCondition(null), "");
  assert.equal(defaultName("key", "1", "gid"), "gid = 1");
  assert.equal(defaultName("regex", "a+", ""), "/a+/");
  assert.equal(defaultName("text", "z".repeat(200), "").length, 80);
});

test("] and [ navigate; modified keys and other keys do not", () => {
  assert.equal(highlightNavKey({ key: "]" }), "fwd");
  assert.equal(highlightNavKey({ key: "[" }), "back");
  for (const key of ["n", "N", "t", "T", "b", "B", "c"]) {
    assert.equal(highlightNavKey({ key }), null, key); // the existing shortcuts stay theirs
  }
  assert.equal(highlightNavKey({ key: "]", ctrlKey: true }), null);
  assert.equal(highlightNavKey({ key: "[", altKey: true }), null);
  assert.equal(highlightNavKey({ key: "]", metaKey: true }), null);
  assert.equal(cursorOf({ ts: 5, id: 7 }), "5:7");
  assert.equal(cursorOf({ ts: null, id: 7 }), "null:7");
  assert.equal(cursorOf(null), null);
});

function fakeApi(rules) {
  const calls = { list: 0, mark: [], next: [], key: [], order: [], create: [], update: [], remove: [] };
  const state = { rules: rules.slice() };
  const api = {
    highlights: {
      list: async (caseId) => { calls.list += 1; return { rules: state.rules.slice(), caseId }; },
      mark: async (caseId, conds, ids) => {
        calls.mark.push({ caseId, conds, ids });
        const marks = {};
        for (const id of ids) {
          if (id % 2 === 0) { marks[String(id)] = 0; } else if (id % 3 === 0) { marks[String(id)] = 1; }
        }
        return { marks };
      },
      next: async (...args) => { calls.next.push(args); return { row: { id: 42, ts: 9 }, rule: 1 }; },
      key: async (...args) => { calls.key.push(args); state.rules.push({ id: 9, enabled: true, color: args[3], name: "k", cond: {} }); return {}; },
      order: async (...args) => { calls.order.push(args); return {}; },
      create: async (...args) => { calls.create.push(args); return {}; },
      update: async (...args) => { calls.update.push(args); return {}; },
      remove: async (...args) => { calls.remove.push(args); return {}; },
    },
  };
  return { api, calls, state };
}

const RULES = [
  { id: 1, name: "even", color: "red", enabled: true, cond: { sev: 1 } },
  { id: 2, name: "off", color: "blue", enabled: false, cond: { sev: 2 } },
  { id: 3, name: "three", color: "green", enabled: true, cond: { sev: 3 } },
];

test("store loads the rules of the case and marks rows in batches with the enabled rules only", async () => {
  const { api, calls } = fakeApi(RULES);
  let caseId = "C1";
  const changes = [];
  const queued = [];
  const store = createHighlightStore({ api, getCase: () => caseId, onChange: (k) => changes.push(k), schedule: (fn) => queued.push(fn) });
  store.ensure([1, 2]); // first call: loads the rules (no rule known yet, nothing queued)
  await tick();
  assert.equal(calls.list, 1);
  assert.deepEqual(store.active().map((r) => r.id), [1, 3]);
  assert.deepEqual(changes, ["rules"]);
  const ids = Array.from({ length: MARK_BATCH + 10 }, (_, k) => k + 1);
  store.ensure(ids);
  store.ensure(ids); // queued once
  assert.equal(queued.length, 1);
  await queued.shift()();
  assert.equal(calls.mark.length, 1);
  assert.equal(calls.mark[0].ids.length, MARK_BATCH);
  assert.deepEqual(calls.mark[0].conds, [{ sev: 1 }, { sev: 3 }]);
  assert.equal(queued.length, 1); // the rest goes in a second batch
  await queued.shift()();
  assert.equal(calls.mark[1].ids.length, 10);
  assert.deepEqual(store.get(4), { color: "red", name: "even", ruleId: 1, label: "highlighted by even" });
  assert.equal(store.get(9).ruleId, 3); // index 1 of the enabled rules = rule 3, not the disabled rule 2
  assert.equal(store.get(5), null);
  assert.ok(store.known(5));
  store.ensure(ids); // everything known: no request
  assert.equal(queued.length, 0);
  store.invalidateMarks();
  assert.equal(store.get(4), null);
  assert.equal(changes.at(-1), "marks");
  caseId = "C2"; // another case: rules and marks are dropped, the new rules load
  store.ensure([1]);
  assert.equal(store.rules().length, 0);
  await tick();
  assert.equal(calls.list, 2);
});

test("an answer for old rules is dropped when the rules changed meanwhile", async () => {
  const { api, calls } = fakeApi(RULES);
  let release;
  api.highlights.mark = (caseId, conds, ids) => new Promise((resolve) => {
    calls.mark.push(ids);
    release = () => resolve({ marks: Object.fromEntries(ids.map((id) => [String(id), 0])) });
  });
  const queued = [];
  const store = createHighlightStore({ api, getCase: () => "C1", onChange: () => {}, schedule: (fn) => queued.push(fn) });
  await store.load();
  store.ensure([10, 11]);
  const pending = queued.shift()();
  await store.load(); // rules reloaded: marks generation changes
  release();
  await pending;
  assert.equal(store.get(10), null);
});

test("store writes go to the api and reload the rules; next and move use the current order", async () => {
  const { api, calls } = fakeApi(RULES);
  const store = createHighlightStore({ api, getCase: () => "C1", onChange: () => {} });
  await store.load();
  await store.highlightKey("gid", "7");
  assert.deepEqual(calls.key[0], ["C1", "gid", "7", "yellow"]); // first free color
  assert.equal(calls.list, 2);
  await store.move(3, -1);
  assert.deepEqual(calls.order[0], ["C1", [1, 3, 2, 9]]);
  assert.equal(await store.move(1, -1), null); // already first
  await store.update(2, { enabled: true });
  await store.remove(2);
  await store.create({ name: "x" });
  assert.deepEqual([calls.update[0], calls.remove[0], calls.create[0]], [["C1", 2, { enabled: true }], ["C1", 2], ["C1", { name: "x" }]]);
  const found = await store.next({ sev: 1 }, { id: 5, ts: 3 }, "back");
  assert.deepEqual(calls.next[0].slice(0, 5), ["C1", { sev: 1 }, [{ sev: 1 }, { sev: 3 }, {}], "3:5", "back"]);
  assert.equal(found.row.id, 42);
  assert.equal(found.rule.id, 3);
  assert.equal(store.get(42).ruleId, 3); // colored at once
});

test("next without enabled rules does not call the server", async () => {
  const { api, calls } = fakeApi([{ ...RULES[1] }]);
  const store = createHighlightStore({ api, getCase: () => "C1", onChange: () => {} });
  await store.load();
  assert.deepEqual(await store.next(null, { id: 1, ts: 1 }, "fwd"), { row: null, rule: null, none: "rules" });
  assert.equal(calls.next.length, 0);
  store.ensure([1, 2]);
  assert.equal(calls.mark.length, 0);
});
