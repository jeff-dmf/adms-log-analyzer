// "Extract" panel (ROADMAP 9.8) on the fake DOM: live validation, Run, the columns with their drill-down, the
// notices, stale results, case changes, repeated clicks and the "Promote to message type" dialog. The "server" is a set
// of promises the test settles (no waiting on the wall clock). Regex, values and TOML are untrusted text.
import test from "node:test";
import assert from "node:assert/strict";

import { installGlobalDom, tick } from "./fixtures/fake-dom.mjs";

const doc = installGlobalDom();
const { mountExtractPanel } = await import("../../src/dmslog/web/js/extract-panel.js");
const { registerAppPanels } = await import("../../src/dmslog/web/js/app-actions.js");
const { api } = await import("../../src/dmslog/web/js/api.js");
const { S, format } = await import("../../src/dmslog/web/js/strings.js");

const defer = () => {
  let resolve;
  let reject;
  const promise = new Promise((a, b) => { resolve = a; reject = b; });
  return { promise, resolve, reject };
};
const cls = (node, name) => node.className.split(/\s+/).includes(name);
const find = (root, name) => root.all().find((n) => cls(n, name));
const findAll = (root, name) => root.all().filter((n) => cls(n, name));
// every test mounts its own panel in the shared document: the dialog of the newest one is the last
const lastDialog = () => doc.body.all().filter((n) => n.tagName === "DIALOG").pop();
const byText = (root, text) => root.all().find((n) => n.tagName === "BUTTON" && n.textContent === text);

function result(overrides = {}) {
  return {
    regex: "r", pattern: "p", target: "msg", columns: [], rows: [], scanned: 10, matched: 3, cut: 0, truncated: false,
    window: 2889, ...overrides,
  };
}

function column(name, values, extra = {}) {
  return {
    name, type: "str", suggested_type: "str", present: 3, distinct: values.length,
    values: values.map(([value, count], i) => ({
      value, count, pct: Math.round((count * 10000) / 3) / 100,
      leaf: value === null ? { not: { extract: { regex: "r", group: name, eq: "x" } } } : { extract: { regex: "r", group: name, eq: value } },
    })),
    others: { count: 0, groups: 0 }, numeric: null, ...extra,
  };
}

function world({ caseId = "A", filter = null } = {}) {
  const state = { caseId, filter };
  const delays = [];
  const calls = { check: [], run: [], promote: [] };
  const drills = [];
  const copied = [];
  const closes = [];
  const fake = (list) => (...args) => {
    const d = defer();
    list.push({ args, ...d });
    return d.promise;
  };
  const fakeApi = {
    extract: {
      check: fake(calls.check),
      run: fake(calls.run),
      promote: fake(calls.promote),
    },
  };
  const host = doc.createElement("section");
  doc.body.appendChild(host);
  const panel = mountExtractPanel(host, {
    api: fakeApi,
    getCase: () => state.caseId,
    getFilter: () => state.filter,
    onDrill: (leaf) => drills.push(leaf),
    onRequestClose: () => closes.push(1),
    delay: () => { const d = defer(); delays.push(d); return d.promise; },
    copyText: (text) => { copied.push(text); return Promise.resolve(); },
  });
  const w = {
    state, delays, calls, drills, copied, closes, panel, host, doc,
    regex: find(host, "ext-regex"),
    msg: find(host, "ext-regex-msg"),
    target: find(host, "ext-target"),
    limit: find(host, "ext-limit"),
    top: find(host, "ext-top"),
    run: find(host, "ext-run"),
    promote: find(host, "ext-promote"),
    status: find(host, "ext-status"),
    stale: find(host, "ext-stale"),
    type(text) {
      w.regex.value = text;
      w.regex.dispatch("input");
    },
    // types, lets the pause end and answers the check with the groups of the text
    async typeChecked(text, columns) {
      w.type(text);
      w.delays[w.delays.length - 1].resolve();
      await tick();
      const call = w.calls.check[w.calls.check.length - 1];
      call.resolve({ regex: text, pattern: text, target: "msg", columns, anchored: false, window: 2889, steps: 1 });
      await tick();
    },
    async runWith(answer) {
      w.run.click();
      await tick();
      w.calls.run[w.calls.run.length - 1].resolve(answer);
      await tick();
    },
  };
  return w;
}

const ID = [{ name: "id", type: "str" }];

test("typing validates through extract/check after the pause, and only the last text is asked", async () => {
  const w = world();
  w.type("a");
  w.type("ab");
  assert.equal(w.calls.check.length, 0, "nothing before the pause ends");
  assert.equal(w.msg.textContent, S.exChecking);
  w.delays[0].resolve();
  await tick();
  assert.equal(w.calls.check.length, 0, "the first pause belongs to a replaced input");
  w.delays[1].resolve();
  await tick();
  assert.equal(w.calls.check.length, 1);
  assert.deepEqual(w.calls.check[0].args[0], { regex: "ab", target: "msg", types: {} });
  w.calls.check[0].resolve({ columns: ID, window: 2889 });
  await tick();
  assert.equal(w.msg.textContent, format(S.exCheckOkOne, { window: "2,889" }));
  assert.equal(w.regex.getAttribute("aria-invalid"), "false");
});

test("a bad regex is shown next to the field with the offset and what is near it, and Run waits", async () => {
  const w = world();
  w.type("ab(?P<x>[");
  w.delays[0].resolve();
  await tick();
  w.calls.check[0].reject(Object.assign(new Error("invalid regex: unclosed class (at offset 8)"), { code: "bad_regex", status: 400 }));
  await tick();
  assert.equal(w.msg.textContent, "invalid regex: unclosed class (at offset 8)");
  assert.ok(cls(w.msg, "ui-error"));
  assert.equal(w.regex.getAttribute("aria-invalid"), "true");
  assert.equal(w.regex.getAttribute("aria-describedby"), w.msg.id);
  const context = find(w.host, "ext-context");
  assert.equal(context.hidden, false);
  assert.equal(context.textContent, "Near: ab(?P<x>⟨[⟩");
  assert.equal(w.run.disabled, true);
  await w.typeChecked("ab(?P<x>b)", [{ name: "x", type: "str" }]);
  assert.equal(context.hidden, true);
  assert.equal(w.regex.getAttribute("aria-invalid"), "false");
  assert.equal(w.run.disabled, false);
});

test("Run is enabled only with a case, a checked regex and valid numbers", async () => {
  const w = world({ caseId: null });
  assert.equal(w.run.disabled, true);
  w.state.caseId = "A";
  w.panel.refresh();
  assert.equal(w.run.disabled, true, "no regex yet");
  await w.typeChecked("(?P<id>\\d+)", ID);
  assert.equal(w.run.disabled, false);
  for (const bad of ["0", "10001", "", "1e3", "abc", "NaN"]) {
    w.limit.value = bad;
    w.limit.dispatch("input");
    assert.equal(w.run.disabled, true, "limit " + bad);
    assert.equal(w.limit.getAttribute("aria-invalid"), "true");
  }
  w.limit.value = "50";
  w.limit.dispatch("input");
  assert.equal(w.run.disabled, false);
  w.top.value = "101";
  w.top.dispatch("input");
  assert.equal(w.run.disabled, true);
  w.top.value = "5";
  w.top.dispatch("input");
  w.type("(?P<id>\\d+)x"); // being checked again: the verdict is not in yet
  assert.equal(w.run.disabled, true);
  assert.ok(w.run.title.length > 0, "the reason is a title");
});

test("Run sends the filter, the target, the numbers and only the types the user declared", async () => {
  const w = world({ filter: { sev: { le: 3 } } });
  await w.typeChecked("(?P<id>\\d+) (?P<name>\\w+)", [{ name: "id", type: "str" }, { name: "name", type: "str" }]);
  const select = w.host.all().find((n) => n.tagName === "SELECT" && n.dataset.group === "id");
  select.value = "int";
  select.dispatch("change");
  w.delays[w.delays.length - 1].resolve();
  await tick();
  const check = w.calls.check[w.calls.check.length - 1];
  assert.deepEqual(check.args[0].types, { id: "int" });
  check.resolve({ columns: [{ name: "id", type: "int" }, { name: "name", type: "str" }], window: 100 });
  await tick();
  w.target.value = "text";
  w.target.dispatch("change");
  w.delays[w.delays.length - 1].resolve();
  await tick();
  w.calls.check[w.calls.check.length - 1].resolve({ columns: [{ name: "id", type: "int" }, { name: "name", type: "str" }], window: 100 });
  await tick();
  w.limit.value = "250";
  w.limit.dispatch("input");
  w.run.click();
  await tick();
  assert.equal(w.calls.run.length, 1);
  assert.equal(w.calls.run[0].args[0], "A");
  assert.deepEqual(w.calls.run[0].args[1], {
    filter: { sev: { le: 3 } }, regex: "(?P<id>\\d+) (?P<name>\\w+)", target: "text", types: { id: "int" }, limit: 250, top: 20,
  });
  assert.equal(w.run.textContent, S.exRunning);
  assert.equal(w.status.textContent, S.exRunning);
});

test("the result shows columns with top values, the suggested type, statistics and the notices; markup stays text", async () => {
  const w = world();
  await w.typeChecked("(?P<id>\\d+)", ID);
  await w.runWith(result({
    cut: 2, truncated: true,
    rows: [{ id: 11 }, { id: 12 }],
    columns: [
      column("id", [["<img src=x onerror=alert(1)>", 2], ["", 1], [null, 7]], {
        suggested_type: "int", others: { count: 4, groups: 2 },
        numeric: { type: "int", count: 3, min: 0, max: 9, avg: 4.5, sum: 13, p50: 4, p90: 8, p99: 9 },
      }),
    ],
  }));
  assert.equal(w.status.textContent, format(S.exSummary, { matched: "3", scanned: "10" }));
  const notes = findAll(w.host, "ext-note").map((n) => n.textContent);
  assert.deepEqual(notes, [format(S.exCutOther, { count: "2", window: "2,889" }), format(S.exTruncated, { limit: "1,000" })]);
  const values = findAll(w.host, "ext-value");
  assert.deepEqual(values.map((n) => n.textContent), ["<img src=x onerror=alert(1)>", S.exValueEmpty, S.exValueNone]);
  assert.deepEqual(values.map((n) => cls(n, "is-text") || cls(n, "is-empty") || cls(n, "is-null")), [true, true, true]);
  assert.equal(w.host.all().filter((n) => n.tagName === "IMG").length, 0);
  assert.equal(find(w.host, "ext-others").textContent, format(S.exOthers, { count: "4", groups: "2" }));
  assert.match(find(w.host, "ext-suggest-text").textContent, /Integer/);
  const stats = findAll(w.host, "ext-stat").map((n) => n.textContent);
  assert.ok(stats.includes("Min0") && stats.includes("Average4.5") && stats.includes("p999"), stats.join("|"));
  assert.equal(w.promote.disabled, false);
});

test("a result without matches, a null measure and an unsafe integer do not break the table", async () => {
  const w = world();
  await w.typeChecked("(?P<id>\\d+)", ID);
  await w.runWith(result({ matched: 0, scanned: 0, columns: [column("id", [])] }));
  assert.equal(find(w.host, "ext-empty").textContent, S.exNoMatches);
  assert.equal(w.promote.disabled, true);
  await w.runWith(result({
    columns: [column("id", [[2 ** 60, 1], [5, 2]], { numeric: { type: "int", count: 3, min: 0, max: 2 ** 60, avg: null, sum: null, p50: 5, p90: 5, p99: 5 } })],
    rows: [{ id: 1 }],
  }));
  const buttons = findAll(w.host, "ext-include");
  assert.equal(buttons[0].disabled, true, "an integer a double cannot hold is not applied as a filter");
  assert.equal(buttons[0].title, S.exLeafUnsafe);
  assert.equal(buttons[1].disabled, false);
  assert.match(findAll(w.host, "ext-value")[0].textContent, /^1152921504606846976$|^1152921504606847000$/);
  const stats = findAll(w.host, "ext-stat").map((n) => n.textContent);
  assert.ok(stats.some((s) => s.startsWith("Max≈")) && stats.includes("Average–"));
});

test("the value buttons apply their leaf; a value without a leaf is disabled with the reason; labels name the value", async () => {
  const w = world();
  await w.typeChecked("(?P<id>\\d+)", ID);
  const values = column("id", [["7", 2], ["x".repeat(2000), 1]]);
  values.values[1].leaf = null;
  await w.runWith(result({ columns: [values], rows: [{ id: 1 }] }));
  const [first, second] = findAll(w.host, "ext-include");
  assert.equal(first.getAttribute("aria-label"), format(S.exFilterOnlyLabel, { name: "id", value: "7" }));
  first.click();
  assert.deepEqual(w.drills, [{ extract: { regex: "r", group: "id", eq: "7" } }]);
  assert.equal(second.disabled, true);
  assert.equal(second.title, S.exNoLeaf);
  second.click();
  assert.equal(w.drills.length, 1);
  // the drill-down changes the filter of the grid: the result is out of date, not recomputed
  w.state.filter = { and: [w.drills[0]] };
  w.panel.refresh();
  assert.equal(w.stale.hidden, false);
  assert.equal(w.stale.textContent, S.exStale);
  assert.equal(w.calls.run.length, 1, "no request without the user");
  assert.equal(findAll(w.host, "ext-value").length, 2, "the old result stays visible");
  w.state.filter = null;
  w.panel.refresh();
  assert.equal(w.stale.hidden, true);
});

test("editing the regex, the target or the numbers marks the result as out of date", async () => {
  const w = world();
  await w.typeChecked("(?P<id>\\d+)", ID);
  await w.runWith(result({ columns: [column("id", [["1", 3]])], rows: [{ id: 1 }] }));
  assert.equal(w.stale.hidden, true);
  w.limit.value = "10";
  w.limit.dispatch("input");
  assert.equal(w.stale.hidden, false);
  w.limit.value = "1000";
  w.limit.dispatch("input");
  assert.equal(w.stale.hidden, true);
  w.type("(?P<id>\\d+)z");
  assert.equal(w.stale.hidden, false);
});

test("repeated clicks: the newest run wins and the older answer is dropped", async () => {
  const w = world();
  await w.typeChecked("(?P<id>\\d+)", ID);
  w.run.click();
  w.run.click();
  await tick();
  assert.equal(w.calls.run.length, 2);
  w.calls.run[1].resolve(result({ matched: 2, columns: [column("id", [["new", 2]])], rows: [{ id: 2 }] }));
  await tick();
  w.calls.run[0].resolve(result({ matched: 1, columns: [column("id", [["old", 1]])], rows: [{ id: 1 }] }));
  await tick();
  assert.deepEqual(findAll(w.host, "ext-value").map((n) => n.textContent), ["new"]);
  assert.equal(w.run.textContent, S.exRun);
});

test("an answer that arrives after the case changed is dropped and reset() forgets the result", async () => {
  const w = world();
  await w.typeChecked("(?P<id>\\d+)", ID);
  w.run.click();
  await tick();
  w.state.caseId = "B";
  w.panel.reset();
  w.calls.run[0].resolve(result({ columns: [column("id", [["from-A", 3]])], rows: [{ id: 1 }] }));
  await tick();
  assert.equal(findAll(w.host, "ext-value").length, 0);
  assert.equal(w.status.textContent, S.exIdle);
  assert.equal(w.regex.value, "(?P<id>\\d+)", "the typed regex stays");
  // a result of case A that was already shown does not survive into case B
  await w.runWith(result({ columns: [column("id", [["B", 3]])], rows: [{ id: 1 }] }));
  assert.equal(findAll(w.host, "ext-value").length, 1);
  w.panel.reset();
  assert.equal(findAll(w.host, "ext-value").length, 0);
  assert.equal(w.promote.disabled, true);
  assert.equal(w.run.disabled, false, "the case is open and the regex is valid: Run is possible");
  w.state.caseId = null;
  w.panel.reset();
  assert.equal(w.run.disabled, true);
  assert.equal(w.status.textContent, S.exNoCase);
});

test("errors: a timeout, a too large result and a bad answer show the server text; the old result is not kept", async () => {
  const w = world();
  await w.typeChecked("(?P<id>\\d+)", ID);
  w.run.click();
  await tick();
  w.calls.run[0].reject(Object.assign(new Error("The extraction did not finish in time"), { code: "extract_timeout", status: 408 }));
  await tick();
  assert.equal(w.status.textContent, format(S.exError, { error: "The extraction did not finish in time" }));
  assert.ok(cls(w.status, "ui-error"));
  w.run.click();
  await tick();
  w.calls.run[1].reject(Object.assign(new Error(""), { code: "result_too_large", status: 400 }));
  await tick();
  assert.equal(w.status.textContent, format(S.exError, { error: S.exErrorGeneric }));
  assert.equal(w.run.disabled, false, "Run can be tried again");
  await w.runWith(null);
  assert.equal(w.status.textContent, format(S.exSummary, { matched: "0", scanned: "0" }));
});

test("Enter in the regex runs; while the check is pending it runs once the check accepts it; an error cancels it", async () => {
  const w = world();
  w.type("(?P<id>\\d+)");
  w.regex.dispatch("keydown", { key: "Enter" });
  assert.equal(w.calls.run.length, 0);
  w.delays[0].resolve();
  await tick();
  w.calls.check[0].resolve({ columns: ID, window: 5 });
  await tick();
  assert.equal(w.calls.run.length, 1, "the run waited for the verdict");
  w.type("(?P<id>\\d+)(");
  w.regex.dispatch("keydown", { key: "Enter" });
  w.delays[1].resolve();
  await tick();
  w.calls.check[1].reject(Object.assign(new Error("invalid regex: x (at offset 12)"), { code: "bad_regex" }));
  await tick();
  assert.equal(w.calls.run.length, 1);
  w.calls.check.length = 0;
  await w.typeChecked("(?P<id>\\d+)", ID);
  assert.equal(w.calls.run.length, 1, "Enter was forgotten when the regex changed again");
  w.regex.dispatch("keydown", { key: "Enter" });
  await tick();
  assert.equal(w.calls.run.length, 2);
});

test("types: the selects are kept while the groups stay, 'Use <type>' declares the suggestion and checks again", async () => {
  const w = world();
  await w.typeChecked("(?P<id>\\d+)", ID);
  const select = w.host.all().find((n) => n.tagName === "SELECT" && n.dataset.group === "id");
  assert.equal(select.getAttribute("aria-label"), format(S.exTypeFor, { name: "id" }));
  select.focus();
  await w.typeChecked("(?P<id>\\d+)x", ID);
  assert.equal(w.host.all().find((n) => n.tagName === "SELECT" && n.dataset.group === "id"), select, "same select");
  assert.equal(doc.activeElement, select, "the focus stays on the select");
  await w.runWith(result({ columns: [column("id", [["1", 3]], { suggested_type: "int" })], rows: [{ id: 1 }] }));
  const use = find(w.host, "ext-use-suggested");
  assert.equal(use.getAttribute("aria-label"), format(S.exUseSuggestedLabel, { type: "Integer", name: "id" }));
  use.click();
  assert.equal(select.value, "int");
  w.delays[w.delays.length - 1].resolve();
  await tick();
  assert.deepEqual(w.calls.check[w.calls.check.length - 1].args[0].types, { id: "int" });
});

test("a type for a group the regex no longer has is not sent, and the focus leaves a select that disappears", async () => {
  const w = world();
  await w.typeChecked("(?P<id>\\d+)(?P<n>x)", [{ name: "id", type: "str" }, { name: "n", type: "str" }]);
  const nSelect = w.host.all().find((n) => n.tagName === "SELECT" && n.dataset.group === "n");
  nSelect.value = "int";
  nSelect.dispatch("change");
  nSelect.focus();
  w.type("(?P<id>\\d+)"); // the group n is gone from the text
  w.delays[w.delays.length - 1].resolve();
  await tick();
  const sent = w.calls.check[w.calls.check.length - 1].args[0];
  assert.deepEqual(sent.types, {}, "no type for a missing group (the server would refuse it)");
  sent.regex && w.calls.check[w.calls.check.length - 1].resolve({ columns: ID, window: 3 });
  await tick();
  assert.equal(doc.activeElement, w.regex, "the select was replaced: the focus goes back to the field");
  assert.equal(w.host.all().filter((n) => n.tagName === "SELECT" && n.dataset.group === "n").length, 0);
  w.type("");
  await tick();
  assert.equal(find(w.host, "ext-types").hidden, true);
  assert.equal(w.msg.textContent, "");
});

// ---- Promote to message type -------------------------------------------------------------------------------------

async function withResult(options = {}) {
  const w = world();
  await w.typeChecked("^(?P<id>\\d+)", ID);
  await w.runWith(result({
    columns: [column("id", [["1", 3]])], rows: Array.from({ length: 25 }, (_, i) => ({ id: i + 1 })), ...options,
  }));
  return w;
}

test("Promote is available only for a result of the first line with records", async () => {
  const w = await withResult();
  assert.equal(w.promote.disabled, false);
  w.target.value = "text";
  w.target.dispatch("change");
  assert.equal(w.promote.disabled, false, "the shown result is still the one of the first line (and is marked out of date)");
  assert.equal(w.stale.hidden, false);
  await w.typeChecked("^(?P<id>\\d+)", ID);
  await w.runWith(result({ target: "text", columns: [column("id", [["1", 3]])], rows: [{ id: 1 }] }));
  assert.equal(w.promote.disabled, true, "a result over the whole record cannot become a message type");
  assert.equal(w.promote.title, S.exPromoteNeedsMsg);
  const t = world();
  assert.equal(t.promote.disabled, true);
  assert.equal(t.promote.title, S.exPromoteNoRecords);
});

test("Promote opens a dialog with at most 20 ids, shows the TOML as text with the verdict and warnings, and writes nothing", async () => {
  const w = await withResult();
  const opener = w.promote;
  opener.focus();
  opener.click();
  const dialog = lastDialog();
  assert.ok(dialog.open, "the dialog is open");
  assert.equal(doc.activeElement.tagName, "H2", "focus moves to the dialog title");
  assert.equal(w.calls.promote.length, 1);
  const [caseId, body] = w.calls.promote[0].args;
  assert.equal(caseId, "A");
  assert.equal(body.ids.length, 20);
  assert.deepEqual([body.regex, body.target, body.types], ["^(?P<id>\\d+)", "msg", {}]);
  assert.equal("family" in body, false);
  const generate = find(dialog, "ext-generate");
  assert.equal(generate.disabled, true, "one request at a time");
  w.calls.promote[0].resolve({
    toml: "[[template]]\ncode = \"<script>x</script>\"\n", file: "src/dmslog/adapters/awfm.toml",
    check: { loads: false, error: "bad regex <b>" },
    warnings: [{ code: "lead_varies", message: "The text before the match differs" }],
    records: [],
  });
  await tick();
  assert.equal(find(dialog, "ext-toml").value, "[[template]]\ncode = \"<script>x</script>\"\n");
  assert.equal(find(dialog, "ext-file").textContent, format(S.exPromoteFile, { file: "src/dmslog/adapters/awfm.toml" }));
  const check = find(dialog, "ext-dialog-check");
  assert.equal(check.textContent, format(S.exCheckFails, { error: "bad regex <b>" }));
  assert.ok(cls(check, "ui-error"));
  assert.deepEqual(findAll(dialog, "ext-warning").map((n) => n.textContent), ["The text before the match differs"]);
  assert.equal(dialog.all().filter((n) => n.tagName === "SCRIPT" || n.tagName === "B").length, 0);
  assert.equal(generate.disabled, false);
  // family and code go into the next request
  find(dialog, "ext-family").value = " AWFM ";
  find(dialog, "ext-code").value = "";
  generate.click();
  assert.equal(w.calls.promote[1].args[1].family, "AWFM");
  assert.equal("code" in w.calls.promote[1].args[1], false);
  w.calls.promote[1].resolve({ toml: "[[template]]\n", file: "f.toml", check: { loads: true, error: null }, warnings: [] });
  await tick();
  assert.equal(find(dialog, "ext-dialog-check").textContent, S.exCheckLoads);
  assert.equal(findAll(dialog, "ext-warning").length, 0);
  // the run did not issue anything but the two requests
  assert.equal(w.calls.run.length, 1);
});

test("Copy writes the text; a refusal says so and selects the text", async () => {
  const w = await withResult();
  w.promote.click();
  const dialog = lastDialog();
  const copy = find(dialog, "ext-copy");
  assert.equal(copy.disabled, true, "nothing to copy yet");
  w.calls.promote[0].resolve({ toml: "[[template]]\n", file: "f.toml", check: { loads: true }, warnings: [] });
  await tick();
  assert.equal(copy.disabled, false);
  copy.click();
  await tick();
  assert.deepEqual(w.copied, ["[[template]]\n"]);
  assert.equal(find(dialog, "ext-dialog-status").textContent, S.exCopied);
});

test("closing the dialog cancels the pending request (a late answer writes nothing) and the focus returns to the button", async () => {
  const w = await withResult();
  w.promote.focus();
  w.promote.click();
  const dialog = lastDialog();
  find(dialog, "dialog-close").click();
  assert.equal(dialog.open, false);
  assert.equal(doc.activeElement, w.promote);
  w.calls.promote[0].resolve({ toml: "late", file: "f", check: { loads: true }, warnings: [] });
  await tick();
  assert.equal(find(dialog, "ext-toml").value, "");
  w.promote.click(); // opens again with a fresh request
  assert.equal(w.calls.promote.length, 2);
  assert.equal(find(dialog, "ext-toml").value, "");
});

test("a server refusal inside the dialog is shown there; reset() and closed() close the dialog", async () => {
  const w = await withResult();
  w.promote.click();
  const dialog = lastDialog();
  w.calls.promote[0].reject(Object.assign(new Error("the records come from several families; give the family"), { code: "mixed_families", status: 400 }));
  await tick();
  assert.equal(find(dialog, "ext-dialog-status").textContent, format(S.exError, { error: "the records come from several families; give the family" }));
  assert.ok(cls(find(dialog, "ext-dialog-status"), "ui-error"));
  w.state.caseId = "B";
  w.panel.reset();
  assert.equal(dialog.open, false, "a case change closes the dialog");
  w.state.caseId = "A";
  await w.runWith(result({ columns: [column("id", [["1", 3]])], rows: [{ id: 1 }] }));
  w.promote.click();
  assert.equal(dialog.open, true);
  w.panel.closed();
  assert.equal(dialog.open, false, "closing the tab closes the dialog");
});

test("an Escape or a native cancel of the dialog closes it through the same path", async () => {
  const w = await withResult();
  w.promote.focus();
  w.promote.click();
  const dialog = lastDialog();
  const event = dialog.dispatch("cancel");
  assert.equal(event.defaultPrevented, true);
  assert.equal(dialog.open, false);
  assert.equal(doc.activeElement, w.promote);
});

test("the Close button asks the dock to close; the dock panel registered in app-actions wires the panel", async () => {
  const w = world();
  find(w.host, "panel-close").click();
  assert.equal(w.closes.length, 1);
  const defs = [];
  registerAppPanels({ register: (def) => defs.push(def) }, { api, derivedApi: {} });
  const def = defs.find((d) => d.id === "extract");
  assert.equal(def.titleKey, "actExtract");
  assert.deepEqual(def.listens, ["view"]);
  const host = doc.createElement("section");
  doc.body.appendChild(host);
  const dockCalls = [];
  const filters = [];
  const instance = await def.mount(host, {
    caseId: () => "A", filter: () => ({ sev: { le: 2 } }), userFilter: () => ({ sev: { le: 2 } }),
    setUserFilter: (f) => filters.push(f), dock: { close: (id) => dockCalls.push(id) },
  });
  assert.deepEqual(Object.keys(instance).sort(), ["closed", "refresh", "reset"]);
  find(host, "panel-close").click();
  assert.deepEqual(dockCalls, ["extract"]);
  instance.reset();
  instance.closed();
  instance.refresh();
});

test("api.extract talks to the four routes: check without a case, the others with it", async () => {
  const seen = [];
  globalThis.fetch = async (url, options) => {
    seen.push({ url: String(url), body: JSON.parse(options.body), method: options.method });
    return { ok: true, status: 200, json: async () => ({}) };
  };
  await api.extract.check({ regex: "r", target: "msg", types: {} });
  await api.extract.run("A", { regex: "r" });
  await api.extract.values("A", { ids: [1] });
  await api.extract.promote("A", { ids: [1] });
  assert.deepEqual(seen.map((s) => [s.method, s.url]), [
    ["POST", "/api/extract/check"], ["POST", "/api/entries/extract"],
    ["POST", "/api/entries/extract/values"], ["POST", "/api/entries/extract/promote"],
  ]);
  assert.equal("case" in seen[0].body, false);
  assert.equal(seen[1].body.case, "A");
  assert.equal(seen[3].body.case, "A");
});

test("Copy: a refusal says so and selects the text; the dialog returns focus to the Promote button even if the focus was elsewhere", async () => {
  const w = await withResult();
  w.regex.focus(); // the focus is not on the button when the dialog opens (the opener is the button, not the active element)
  w.promote.click();
  const dialog = lastDialog();
  w.calls.promote[0].resolve({ toml: "[[template]]\n", file: "f.toml", check: { loads: true }, warnings: [] });
  await tick();
  // a second panel whose clipboard refuses
  const host = doc.createElement("section");
  doc.body.appendChild(host);
  const calls = [];
  mountExtractPanel(host, {
    api: { extract: { check: () => Promise.resolve({ columns: ID, window: 5 }), run: () => Promise.resolve(result({ rows: [{ id: 1 }], columns: [column("id", [["1", 1]])] })), promote: () => Promise.resolve({ toml: "x", file: "f", check: { loads: true }, warnings: [] }) } },
    getCase: () => "A", getFilter: () => null, onDrill() {}, delay: () => Promise.resolve(),
    copyText: (text) => { calls.push(text); return Promise.reject(new Error("denied")); },
  });
  const regex = find(host, "ext-regex");
  regex.value = "(?P<id>\\d+)";
  regex.dispatch("input");
  await tick();
  find(host, "ext-run").click();
  await tick();
  find(host, "ext-promote").click();
  await tick();
  const second = lastDialog();
  const secondToml = find(second, "ext-toml");
  let selects = 0;
  secondToml.select = () => { selects += 1; };
  find(second, "ext-copy").click();
  await tick();
  assert.deepEqual(calls, ["x"]);
  assert.equal(selects, 1);
  assert.equal(find(second, "ext-dialog-status").textContent, S.exCopyFailed);
  assert.ok(cls(find(second, "ext-dialog-status"), "ui-error"));
  find(second, "dialog-close").click();
  assert.equal(doc.activeElement, find(host, "ext-promote"));
  find(dialog, "dialog-close").click();
  assert.equal(doc.activeElement, w.promote, "the opener was captured before the dialog took the focus");
});
