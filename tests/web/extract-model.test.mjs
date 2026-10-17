// "Extract" panel (ROADMAP 9.8): the rules without DOM. Flows are driven by promises the test settles (no clock).
import test from "node:test";
import assert from "node:assert/strict";

import {
  buildPromote, buildResult, canPromote, classifyError, createCheckFlow, createPromoteFlow, createRunFlow,
  drillFilter, errorContext, errorOffset, formatNumber, formatPct, groupNames, inputState, isInexactInteger,
  parseBounded, promoteIds, reconcileChoices, requestKey, statsView, typesPayload, usableLeaf, valueView,
} from "../../src/dmslog/web/js/extract-model.js";
import { S } from "../../src/dmslog/web/js/strings.js";

const defer = () => {
  let resolve;
  let reject;
  const promise = new Promise((a, b) => { resolve = a; reject = b; });
  return { promise, resolve, reject };
};
const tick = async () => { for (let i = 0; i < 6; i += 1) await Promise.resolve(); };

test("parseBounded accepts plain digits inside the bound and nothing else", () => {
  assert.equal(parseBounded("1", 10000), 1);
  assert.equal(parseBounded(" 10000 ", 10000), 10000);
  for (const bad of ["0", "10001", "", "  ", "1e3", "+5", "-1", "1.5", "0x10", "NaN", "Infinity", "99999999999999999999", null, undefined, "٣"]) {
    assert.equal(parseBounded(bad, 10000), null, String(bad));
  }
});

test("errorOffset reads the trailing (at offset N) and only that", () => {
  assert.equal(errorOffset("invalid regex: a repeat is unbounded (at offset 12)"), 12);
  assert.equal(errorOffset("invalid regex: x (at offset 0)"), 0);
  assert.equal(errorOffset("the text says (at offset 7) in the middle"), null);
  assert.equal(errorOffset("no offset here"), null);
  assert.equal(errorOffset(undefined), null);
  assert.equal(errorOffset("(at offset 99999999999999999999)"), null);
});

test("errorContext counts code points, clips and tolerates bad offsets", () => {
  assert.deepEqual(errorContext("ab𝒳cd", 3), { before: "ab𝒳", at: "c", after: "d" });
  assert.deepEqual(errorContext("abc", 3), { before: "abc", at: "", after: "" });
  assert.equal(errorContext("abc", 4), null);
  assert.equal(errorContext("abc", -1), null);
  assert.equal(errorContext("abc", null), null);
  assert.equal(errorContext("abc", NaN), null);
  assert.equal(errorContext("abc", 1.5), null);
  const long = errorContext("x".repeat(100) + "!" + "y".repeat(100), 100);
  assert.equal(long.at, "!");
  assert.ok(long.before.startsWith("…") && long.after.endsWith("…"));
  assert.equal(long.before.length, 25);
});

test("classifyError separates the regex problems, the timeout and the rest", () => {
  const regex = classifyError({ code: "bad_regex", message: "invalid regex: x (at offset 3)", status: 400 });
  assert.deepEqual([regex.problem, regex.offset], ["regex", 3]);
  assert.equal(classifyError({ code: "regex_too_costly", message: "too costly" }).problem, "regex");
  assert.equal(classifyError({ code: "extract_timeout", status: 408, message: "slow" }).problem, "timeout");
  assert.equal(classifyError({ code: "http_408", status: 408, message: "slow" }).problem, "timeout");
  assert.equal(classifyError({ code: "result_too_large", message: "big" }).problem, "other");
  assert.equal(classifyError(null).problem, "other");
  assert.equal(classifyError(new Error("x")).text, "x");
});

test("groupNames reads the named groups of a half typed regex", () => {
  assert.deepEqual(groupNames("a(?P<id>\\d+) (?P<id>x) (?P<Bad>y) (?P<n_2>z"), ["id", "n_2"]);
  assert.deepEqual(groupNames("(?P<a>1)(?P<b>2)(?P<c"), ["a", "b"]);
  assert.deepEqual(groupNames(""), []);
  assert.deepEqual(groupNames(null), []);
  assert.equal(groupNames(Array.from({ length: 30 }, (_, i) => "(?P<g" + i + ">x)").join("")).length, 16);
});

test("typesPayload sends only the groups declared as something other than text", () => {
  const columns = [{ name: "a" }, { name: "b" }, { name: "c" }];
  assert.deepEqual(typesPayload(columns, { a: "int", b: "str", c: "bogus", z: "hex" }), { a: "int" });
  assert.deepEqual(typesPayload(columns, null), {});
  assert.deepEqual(typesPayload(null, { a: "int" }), {});
  assert.deepEqual(reconcileChoices({ a: "int", z: "hex", b: "nope" }, columns), { a: "int" });
});

test("inputState: the case, the checked regex and the numbers decide whether Run can work", () => {
  const ok = { caseId: 1, regex: "x", check: { status: "ok" }, limit: "1000", top: "20" };
  assert.deepEqual(inputState(ok), { ok: true, problem: null, limit: 1000, top: 20 });
  assert.equal(inputState({ ...ok, caseId: null }).problem, "exNoCase");
  assert.equal(inputState({ ...ok, regex: "" }).problem, "exNeedRegex");
  assert.equal(inputState({ ...ok, check: { status: "error" } }).problem, "exBadRegexShort");
  assert.equal(inputState({ ...ok, check: { status: "checking" } }).problem, "exChecking");
  assert.equal(inputState({ ...ok, check: { status: "idle" } }).problem, "exChecking");
  assert.equal(inputState({ ...ok, check: null }).problem, "exBadRegexShort");
  for (const limit of ["0", "10001", "", "abc", "1e3", "NaN"]) {
    assert.equal(inputState({ ...ok, limit }).problem, "exBadLimit", limit);
  }
  for (const top of ["0", "101", "", "-1"]) {
    assert.equal(inputState({ ...ok, top }).problem, "exBadTop", top);
  }
  for (const key of ["exNoCase", "exNeedRegex", "exBadRegexShort", "exChecking", "exBadLimit", "exBadTop"]) {
    assert.ok(S[key], key);
  }
});

test("numbers: zero, null, NaN, infinities and integers a double cannot hold", () => {
  assert.equal(formatNumber(0), "0");
  assert.equal(formatNumber(-0), "0");
  assert.equal(formatNumber(1234567), "1,234,567");
  assert.equal(formatNumber(0.1 + 0.2), "0.3");
  assert.equal(formatNumber(null), "–");
  assert.equal(formatNumber(undefined), "–");
  assert.equal(formatNumber(NaN), "–");
  assert.equal(formatNumber(Infinity), "–");
  assert.equal(formatNumber("12"), "–");
  assert.equal(formatNumber(2 ** 53 - 1), "9,007,199,254,740,991");
  assert.match(formatNumber(2 ** 60), /^≈[0-9]{19}$/);
  assert.equal(isInexactInteger(2 ** 53), true);
  assert.equal(isInexactInteger(2 ** 53 - 1), false);
  assert.equal(isInexactInteger(1.5), false);
  assert.equal(formatPct(33.33), "33.33%");
  assert.equal(formatPct(0), "0%");
  assert.equal(formatPct(NaN), "–");
  assert.equal(formatPct(null), "–");
});

test("valueView tells no match, empty text, numbers and texts apart", () => {
  assert.deepEqual(valueView(null, S), { kind: "null", text: S.exValueNone });
  assert.deepEqual(valueView(undefined, S), { kind: "null", text: S.exValueNone });
  assert.deepEqual(valueView("", S), { kind: "empty", text: S.exValueEmpty });
  assert.deepEqual(valueView(0, S), { kind: "number", text: "0" });
  assert.deepEqual(valueView("0", S), { kind: "text", text: "0" });
  assert.deepEqual(valueView("<b>x</b>", S), { kind: "text", text: "<b>x</b>" });
});

test("statsView is empty without numbers and tolerates null measures", () => {
  assert.deepEqual(statsView(null), []);
  assert.deepEqual(statsView({ count: 0 }), []);
  assert.deepEqual(statsView({ count: NaN }), []);
  const rows = statsView({ count: 3, min: 0, max: 9, avg: null, p50: 4, p90: 8, p99: 9, sum: null });
  assert.deepEqual(rows.map((r) => r.key), ["count", "min", "max", "avg", "p50", "p90", "p99"]);
  assert.equal(rows.find((r) => r.key === "min").value, "0");
  assert.equal(rows.find((r) => r.key === "avg").value, "–");
});

test("usableLeaf: a leaf is needed and its number must survive JSON", () => {
  assert.deepEqual(usableLeaf(null), { ok: false, reason: "exNoLeaf" });
  assert.deepEqual(usableLeaf({}), { ok: false, reason: "exNoLeaf" });
  assert.equal(usableLeaf({ extract: { regex: "x", group: "g", eq: 7 } }).ok, true);
  assert.equal(usableLeaf({ extract: { regex: "x", group: "g", eq: null } }).ok, true);
  assert.equal(usableLeaf({ extract: { regex: "x", group: "g", eq: 2 ** 60 } }).reason, "exLeafUnsafe");
  assert.ok(S.exNoLeaf && S.exLeafUnsafe);
});

test("buildResult survives a missing or odd answer", () => {
  const empty = buildResult(null);
  assert.deepEqual([empty.scanned, empty.matched, empty.cut, empty.truncated, empty.columns.length], [0, 0, 0, false, 0]);
  const odd = buildResult({
    scanned: NaN, matched: -4, cut: "x", truncated: 1, rows: [{ id: 5 }, { id: "7" }, null, { id: 2 ** 60 }, { id: -1 }],
    columns: [{ name: "a", type: "weird", suggested_type: "int", values: [{ value: undefined, count: NaN, pct: null }], others: null }],
  });
  assert.deepEqual([odd.scanned, odd.matched, odd.cut, odd.truncated], [0, 0, 0, true]);
  assert.deepEqual(odd.ids, [5]);
  assert.deepEqual([odd.columns[0].type, odd.columns[0].suggested, odd.columns[0].othersCount], ["str", "int", 0]);
  assert.deepEqual(odd.columns[0].values, [{ value: null, count: 0, pct: 0, leaf: null }]);
});

test("promote helpers: at most 20 ids, only for the first line, answer tolerated when odd", () => {
  const result = buildResult({ target: "msg", rows: Array.from({ length: 30 }, (_, i) => ({ id: i + 1 })), columns: [] });
  assert.equal(promoteIds(result).length, 20);
  assert.equal(canPromote(result, "msg"), true);
  assert.equal(canPromote(result, "text"), false);
  assert.equal(canPromote(buildResult({ target: "text", rows: [{ id: 1 }], columns: [] }), "text"), false);
  assert.equal(canPromote(buildResult({ target: "msg", rows: [], columns: [] }), "msg"), false);
  assert.equal(canPromote(null, "msg"), false);
  const answer = buildPromote({ toml: "[[template]]", file: "x.toml", check: { loads: false, error: "bad" }, warnings: [{ code: "lead_varies" }, { code: "a", message: "m" }] });
  assert.deepEqual([answer.loads, answer.checkError, answer.warnings.map((w) => w.message)], [false, "bad", ["lead_varies", "m"]]);
  assert.deepEqual(buildPromote(null), { toml: "", file: "", loads: false, checkError: "", warnings: [] });
});

test("requestKey and drillFilter", () => {
  const base = { caseId: 1, filter: null, regex: "r", target: "msg", types: {}, limit: 5, top: 2 };
  assert.equal(requestKey(base), requestKey({ ...base, filter: undefined }));
  assert.notEqual(requestKey(base), requestKey({ ...base, limit: 6 }));
  const leaf = { extract: { regex: "r", group: "g", eq: "1" } };
  assert.deepEqual(drillFilter(null, leaf), { and: [leaf] });
  assert.deepEqual(drillFilter({ sev: { le: 2 } }, leaf), { and: [{ sev: { le: 2 } }, leaf] });
  assert.deepEqual(drillFilter({ sev: { le: 2 } }, null), { sev: { le: 2 } });
});

// ---- flows ------------------------------------------------------------------------------------------------------

function checkWorld() {
  const delays = [];
  const calls = [];
  const states = [];
  const api = {
    extract: {
      check: (body) => {
        const d = defer();
        calls.push({ body, ...d });
        return d.promise;
      },
    },
  };
  const flow = createCheckFlow({
    api,
    delay: () => {
      const d = defer();
      delays.push(d);
      return d.promise;
    },
    onState: (s) => states.push(s.status),
  });
  return { flow, delays, calls, states };
}

test("check flow: nothing is asked until the pause settles, and a newer input drops the older step", async () => {
  const w = checkWorld();
  const first = w.flow.schedule({ regex: "a", target: "msg", types: {} });
  const second = w.flow.schedule({ regex: "ab", target: "msg", types: {} });
  assert.equal(w.calls.length, 0);
  w.delays[0].resolve();
  await first;
  assert.equal(w.calls.length, 0, "the first pause belongs to an input that was replaced");
  w.delays[1].resolve();
  await tick();
  assert.equal(w.calls.length, 1);
  assert.equal(w.calls[0].body.regex, "ab");
  w.calls[0].resolve({ columns: [], window: 5 });
  await second;
  assert.equal(w.flow.state.status, "ok");
});

test("check flow: a late answer never overwrites the state of a newer input", async () => {
  const w = checkWorld();
  const first = w.flow.schedule({ regex: "a", target: "msg", types: {} });
  w.delays[0].resolve();
  await tick();
  const second = w.flow.schedule({ regex: "b", target: "msg", types: {} });
  w.delays[1].resolve();
  await tick();
  assert.equal(w.calls.length, 2);
  w.calls[1].resolve({ columns: [{ name: "b", type: "str" }], window: 9 });
  await second;
  w.calls[0].reject(Object.assign(new Error("old"), { code: "bad_regex" }));
  await first;
  assert.equal(w.flow.state.status, "ok");
  assert.equal(w.flow.state.result.columns[0].name, "b");
});

test("check flow: an empty regex asks nothing, cancel() forgets, an abort is silent", async () => {
  const w = checkWorld();
  await w.flow.schedule({ regex: "", target: "msg", types: {} });
  assert.equal(w.flow.state.status, "idle");
  assert.equal(w.delays.length, 0);
  const pending = w.flow.schedule({ regex: "a", target: "msg", types: {} });
  w.delays[0].resolve();
  await tick();
  w.calls[0].reject(Object.assign(new Error("aborted"), { name: "AbortError" }));
  await pending;
  assert.equal(w.flow.state.status, "checking", "an abort is not a verdict");
  w.flow.cancel();
  assert.equal(w.flow.state.status, "idle");
});

test("run flow: the newest run wins, a case change drops the answer, errors are classified", async () => {
  let current = "A";
  const calls = [];
  const flow = createRunFlow({
    api: { extract: { run: (caseId, body) => { const d = defer(); calls.push({ caseId, body, ...d }); return d.promise; } } },
    getCase: () => current,
    onState: () => {},
  });
  const one = flow.run({ regex: "1" }, "k1");
  const two = flow.run({ regex: "2" }, "k2");
  calls[1].resolve({ matched: 2, scanned: 2, columns: [], rows: [] });
  await two;
  calls[0].resolve({ matched: 1, scanned: 1, columns: [], rows: [] });
  await one;
  assert.deepEqual([flow.state.status, flow.state.data.matched, flow.state.key], ["done", 2, "k2"]);
  const third = flow.run({ regex: "3" }, "k3");
  current = "B";
  calls[2].resolve({ matched: 9, scanned: 9, columns: [], rows: [] });
  await third;
  assert.equal(flow.state.data.matched, 2, "the answer of case A is not used in case B");
  current = "B";
  const fourth = flow.run({ regex: "4" }, "k4");
  calls[3].reject(Object.assign(new Error("slow"), { code: "extract_timeout", status: 408 }));
  await fourth;
  assert.deepEqual([flow.state.status, flow.state.error.problem], ["error", "timeout"]);
  current = null;
  await flow.run({ regex: "5" }, "k5");
  assert.equal(flow.state.status, "idle");
  assert.equal(calls.length, 4, "no case, no request");
});

test("promote flow: cancel() drops the pending answer", async () => {
  const calls = [];
  const flow = createPromoteFlow({
    api: { extract: { promote: (caseId, body) => { const d = defer(); calls.push(d); return d.promise; } } },
    getCase: () => "A",
    onState: () => {},
  });
  const pending = flow.load({ ids: [1] });
  flow.cancel();
  calls[0].resolve({ toml: "x" });
  await pending;
  assert.equal(flow.state.status, "idle");
  assert.equal(flow.state.promote, null);
});

test("check flow: an older ACCEPTED answer that arrives last does not replace the newer verdict", async () => {
  const w = checkWorld();
  const first = w.flow.schedule({ regex: "a", target: "msg", types: {} });
  w.delays[0].resolve();
  await tick();
  const second = w.flow.schedule({ regex: "b", target: "msg", types: {} });
  w.delays[1].resolve();
  await tick();
  w.calls[1].resolve({ columns: [{ name: "b", type: "str" }], window: 9 });
  await second;
  w.calls[0].resolve({ columns: [{ name: "a", type: "str" }], window: 1 });
  await first;
  assert.equal(w.flow.state.result.columns[0].name, "b");
  assert.equal(w.states.filter((s) => s === "ok").length, 1, "one verdict was published");
});
