// 9.12 UI: the "Missing steps" panel on the fake DOM: states, rows written as text, opening an event in the grid, the
// summary picking a name, the cause filter, keyset "Load more", focus when the button goes away, late answers.
import test from "node:test";
import assert from "node:assert/strict";

import { El, installGlobalDom, tick } from "./fixtures/fake-dom.mjs";

const doc = installGlobalDom();
globalThis.Element = El;
const { initMissingPanel } = await import("../../src/dmslog/web/js/missing-panel.js");
const { S } = await import("../../src/dmslog/web/js/strings.js");

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}
const abortError = () => Object.assign(new Error("aborted"), { name: "AbortError" });
const cls = (node, name) => node.className.split(/\s+/).includes(name);
const find = (root, pred) => root.all().find(pred);
const byClass = (root, name) => root.all().filter((n) => cls(n, name));

const item = (id, extra = {}) => ({
  event: { id, kind: "operation", name: "Op" + id, family: "Wfm", process: "Wfm", ts_start: id * 1000, ts_end: null, status: "open", start_entry_id: id * 10 },
  steps: ["start", "end"], missing: ["end"], cause: "end_of_log", proven: false, ...extra,
});

function world(optionsPanel = {}) {
  const calls = [];
  const current = { caseId: "A" };
  const shown = [];
  const hold = (name) => (caseId, params, signal) => {
    const d = deferred();
    calls.push({ name, caseId, params, d });
    signal?.addEventListener?.("abort", () => d.reject(abortError()));
    return d.promise;
  };
  const host = doc.createElement("section");
  doc.body.appendChild(host);
  let closed = 0;
  const panel = initMissingPanel(host, {
    api: { missingEvents: hold("list"), missingSummary: hold("summary") },
    getCase: () => current.caseId,
    onShowInGrid: (target) => shown.push(target),
    onRequestClose: () => { closed += 1; },
    ...optionsPanel,
  });
  const of = (name) => calls.filter((c) => c.name === name);
  const selects = host.all().filter((n) => n.tagName === "SELECT");
  const [kind, status, cause] = selects;
  const rows = () => byClass(host, "ms-row");
  const sumRows = () => byClass(host, "ms-sum-row");
  const statusText = () => byClass(host, "ms-status")[0].textContent;
  const more = () => byClass(host, "ms-more")[0];
  const section = (name) => byClass(host, name)[0];
  return { host, panel, calls, of, current, shown, kind, status, cause, rows, sumRows, statusText, more, section, closed: () => closed };
}

async function loaded(w, items, next = null, groups = []) {
  const loading = w.panel.refresh();
  w.of("list").at(-1).d.resolve({ items, next });
  w.of("summary").at(-1).d.resolve({ groups, total: groups.reduce((s, g) => s + g.n, 0) });
  await loading;
  await tick();
}

test("the panel frame: a title, Refresh and Close; Close asks the dock; the states say what happens", async () => {
  const w = world();
  assert.equal(find(w.host, (n) => n.tagName === "H2").textContent, S.msTitle);
  const buttons = w.host.all().filter((n) => n.tagName === "BUTTON");
  const close = buttons.find((b) => cls(b, "panel-close"));
  close.click();
  assert.equal(w.closed(), 1);
  const loading = w.panel.refresh();
  assert.equal(w.statusText(), S.msLoading);
  w.of("list")[0].d.resolve({ items: [], next: null });
  w.of("summary")[0].d.resolve({ groups: [], total: 0 });
  await loading;
  await tick();
  assert.equal(w.statusText(), S.msEmpty);
  assert.equal(w.rows().length, 0);
  assert.equal(w.section("ms-summary").getAttribute("aria-label"), S.msSummaryTitle);
});

test("without a case it says so and asks for nothing", async () => {
  const w = world();
  w.current.caseId = null;
  await w.panel.refresh();
  assert.equal(w.statusText(), S.msNoCase);
  assert.equal(w.calls.length, 0);
});

test("an error shows the message in the status line (role status, error styling); Refresh tries again and clears it", async () => {
  const w = world();
  const failing = w.panel.refresh();
  w.of("list")[0].d.reject(new Error("boom"));
  w.of("summary")[0].d.reject(new Error("sum"));
  await failing;
  await tick();
  const line = byClass(w.host, "ms-status")[0];
  assert.equal(line.getAttribute("role"), "status");
  assert.match(line.textContent, new RegExp(S.msError.replace(/[.?]/g, "\\$&") + ": boom"));
  assert.ok(cls(line, "is-error"));
  assert.match(byClass(w.host, "ms-note").map((n) => n.textContent).join("|"), /Could not load the summary/);
  await loaded(w, [item(1)]);
  assert.ok(!cls(line, "is-error"));
  assert.equal(w.rows().length, 1);
});

test("rows are written as text: the name of the log is not markup; the cause, the proof and the status are words", async () => {
  const w = world();
  await loaded(w, [
    item(1, { event: { ...item(1).event, name: "<img src=x onerror=alert(1)>" } }),
    item(2, { cause: "superseded", proven: true }),
  ]);
  const [first, second] = w.rows();
  const cells = first.children;
  assert.equal(cells[3].textContent, "<img src=x onerror=alert(1)>");
  assert.equal(first.all().some((n) => n.tagName === "IMG"), false);
  assert.match(second.textContent, /Superseded by a later record/);
  assert.match(second.textContent, new RegExp(S.msProven));
  assert.match(first.textContent, new RegExp(S.msNotProven));
  assert.match(first.textContent, /Open/);
  assert.equal(w.statusText(), "2 events shown");
});

test("an item opens its event in the grid with the window and the record; one without a time does not", async () => {
  const w = world();
  await loaded(w, [item(4), item(5, { event: { ...item(5).event, ts_start: null } })]);
  const [open4, open5] = byClass(w.host, "ms-open");
  open4.click();
  assert.deepEqual(w.shown, [{ from: 4000, to: 4000, entryId: 40 }]);
  assert.equal(open5.getAttribute("aria-disabled"), "true");
  open5.click();
  assert.equal(w.shown.length, 1);
  assert.match(open4.getAttribute("aria-label"), /Operation Op4/);
});

test("a click on the list after the case changed opens nothing", async () => {
  const w = world();
  await loaded(w, [item(4)]);
  w.current.caseId = "B";
  byClass(w.host, "ms-open")[0].click();
  assert.equal(w.shown.length, 0);
});

test("the summary lists kind, family, name, step and causes; Only this sets the kind and the name and reads the list again", async () => {
  const w = world();
  await loaded(w, [item(1)], null, [
    { kind: "operation", family: "Wfm", name: "Alpha", missing: "end", n: 3, causes: { superseded: 1, end_of_log: 2 } },
    { kind: "startup", family: "Wfm", name: null, missing: "hot", n: 1, causes: { timeout: 1 } },
  ]);
  const rows = w.sumRows();
  assert.equal(rows.length, 2);
  assert.match(rows[0].textContent, /Alpha/);
  assert.match(rows[0].textContent, /Superseded by a later record: 1, End of log: 2/);
  assert.match(byClass(w.host, "ms-note")[0].textContent, /4 events miss a step/);
  const pick = byClass(rows[1], "ms-pick")[0];
  assert.equal(pick.getAttribute("aria-disabled"), "true", "a group without a name cannot narrow the list");
  pick.click();
  assert.equal(w.of("list").length, 1);
  byClass(rows[0], "ms-pick")[0].click();
  assert.deepEqual(w.of("list")[1].params, { limit: 200, kind: "operation", name: "Alpha" });
  assert.deepEqual(w.of("summary")[1].params, { kind: "operation" });
  assert.equal(w.kind.value, "operation");
  const tag = byClass(w.host, "ms-name-tag")[0];
  assert.equal(tag.hidden, false);
  assert.match(tag.textContent, /Alpha/);
});

test("clearing the name filter reads the list again and puts the focus on a control that still exists", async () => {
  const w = world();
  await loaded(w, [item(1)], null, [{ kind: "operation", family: "Wfm", name: "Alpha", missing: "end", n: 1, causes: { end_of_log: 1 } }]);
  byClass(w.sumRows()[0], "ms-pick")[0].click();
  w.of("list")[1].d.resolve({ items: [item(1)], next: null });
  w.of("summary")[1].d.resolve({ groups: [], total: 0 });
  await tick();
  const clear = byClass(w.host, "ms-name-clear")[0];
  clear.focus();
  clear.click();
  assert.equal(w.of("list")[2].params.name, undefined);
  assert.equal(doc.activeElement, w.kind);
  assert.equal(byClass(w.host, "ms-name-tag")[0].hidden, true);
});

test("the summary section keeps the focus when the button that had it is rebuilt", async () => {
  const w = world();
  await loaded(w, [item(1)], null, [{ kind: "operation", family: "Wfm", name: "Alpha", missing: "end", n: 1, causes: { end_of_log: 1 } }]);
  const pick = byClass(w.sumRows()[0], "ms-pick")[0];
  pick.focus();
  const again = w.panel.refresh();
  w.of("list").at(-1).d.resolve({ items: [item(1)], next: null });
  w.of("summary").at(-1).d.resolve({ groups: [{ kind: "operation", family: "Wfm", name: "Alpha", missing: "end", n: 2, causes: { end_of_log: 2 } }], total: 2 });
  await again;
  await tick();
  assert.equal(doc.activeElement, w.section("ms-summary"));
});

test("the cause filter narrows the loaded rows with no request and comes back", async () => {
  const w = world();
  await loaded(w, [item(1), item(2, { cause: "timeout", proven: true }), item(3)]);
  const before = w.calls.length;
  w.cause.value = "timeout";
  w.cause.dispatch("change");
  await tick();
  assert.equal(w.rows().length, 1);
  assert.equal(w.rows()[0].dataset.id, "2");
  assert.equal(w.calls.length, before);
  assert.equal(w.statusText(), "1 event shown");
  w.cause.value = "";
  w.cause.dispatch("change");
  assert.equal(w.rows().length, 3);
});

test("a filter that matches nothing says so; Load more is offered while the server has more", async () => {
  const w = world();
  await loaded(w, [item(1)], "tok");
  assert.equal(w.more().hidden, false);
  assert.equal(w.more().textContent, S.msLoadMore);
  w.cause.value = "capacity";
  w.cause.dispatch("change");
  await tick();
  // nothing loaded matches and the server has more: the flow keeps reading
  assert.equal(w.of("list").length, 2);
  w.of("list")[1].d.resolve({ items: [item(2)], next: null });
  await tick();
  assert.equal(w.rows().length, 0);
  assert.equal(w.statusText(), S.msEmptyFiltered);
  assert.equal(w.more().hidden, true);
});

test("Load more appends the next keyset page, is aria-disabled (not disabled) while loading and ignores a second click", async () => {
  const w = world();
  await loaded(w, [item(1), item(2)], "t2");
  const more = w.more();
  more.focus();
  more.click();
  assert.equal(w.of("list")[1].params.after, "t2");
  assert.equal(more.getAttribute("aria-disabled"), "true");
  assert.equal(more.disabled, false, "a disabled button would lose the focus");
  assert.equal(more.textContent, S.msLoadingMore);
  more.click();
  assert.equal(w.of("list").length, 2);
  w.of("list")[1].d.resolve({ items: [item(2), item(3)], next: "t3" });
  await tick();
  assert.deepEqual(w.rows().map((r) => r.dataset.id), ["1", "2", "3"]);
  assert.equal(more.getAttribute("aria-disabled"), "false");
  assert.equal(doc.activeElement, more, "the focus stayed on the button");
  assert.equal(w.statusText(), "3 events shown, more available");
});

test("the last page hides the button and the focus goes to the list instead of a node that is gone", async () => {
  const w = world();
  await loaded(w, [item(1)], "t1");
  const more = w.more();
  more.focus();
  more.click();
  w.of("list")[1].d.resolve({ items: [item(2)], next: null });
  await tick();
  assert.equal(more.hidden, true);
  assert.equal(doc.activeElement, w.section("ms-list"));
  assert.equal(w.section("ms-list").getAttribute("tabindex") ?? String(w.section("ms-list").tabIndex), "-1");
});

test("a failed Load more keeps the rows, names the failure and offers Try again", async () => {
  const w = world();
  await loaded(w, [item(1)], "t1");
  w.more().click();
  w.of("list")[1].d.reject(new Error("net down"));
  await tick();
  assert.equal(w.rows().length, 1);
  const alert = byClass(w.host, "ms-more-error")[0];
  assert.equal(alert.hidden, false);
  assert.match(alert.textContent, /net down/);
  assert.equal(w.more().textContent, S.msRetry);
  w.more().click();
  assert.equal(alert.hidden, true);
  w.of("list")[2].d.resolve({ items: [item(2)], next: null });
  await tick();
  assert.equal(w.rows().length, 2);
});

test("a case change: the selectors go back to any and the answers of the old case never draw", async () => {
  const w = world();
  await loaded(w, [item(1)], "t1");
  w.status.value = "open";
  w.status.dispatch("change");
  const pending = w.of("list").at(-1);
  w.current.caseId = "B";
  const reset = w.panel.reset();
  pending.d.resolve({ items: [item(99)], next: null }); // late answer of case A
  assert.equal(w.status.value, "");
  const newest = w.of("list").at(-1);
  assert.equal(newest.caseId, "B");
  newest.d.resolve({ items: [item(7)], next: null });
  w.of("summary").at(-1).d.resolve({ groups: [], total: 0 });
  await reset;
  await tick();
  assert.deepEqual(w.rows().map((r) => r.dataset.id), ["7"]);
});

test("the kind and status selectors read the list again with their values", async () => {
  const w = world();
  await loaded(w, [item(1)]);
  w.kind.value = "map_reload";
  w.kind.dispatch("change");
  assert.deepEqual(w.of("list").at(-1).params, { limit: 200, kind: "map_reload" });
  w.status.value = "truncated";
  w.status.dispatch("change");
  assert.deepEqual(w.of("list").at(-1).params, { limit: 200, kind: "map_reload", status: "truncated" });
  assert.deepEqual(w.of("summary").at(-1).params, { kind: "map_reload", status: "truncated" });
});
