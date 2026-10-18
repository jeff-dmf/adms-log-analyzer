// The Panels panel on a strict fake DOM (collections without array methods, innerHTML throws): what each widget state
// and chart type shows, the table next to every chart, the single live region, focus after create / delete / move,
// the editor, and what is left alive when the tab closes.
import test from "node:test";
import assert from "node:assert/strict";

import { installStrictDom } from "./fixtures/strict-dom.mjs";
import { ApiFail, flush, makeWorld, panel, runAnswer, widget } from "./fixtures/panels-world.mjs";

const doc = installStrictDom();
const { mountPanelsPanel } = await import("../../src/dmslog/web/js/panels-panel.js");
const { S } = await import("../../src/dmslog/web/js/strings.js");

const HOSTILE = "<img src=x onerror=alert(1)>";
const cls = (node, name) => node.className.split(/\s+/).includes(name);
const byClass = (root, name) => root.all().filter((n) => cls(n, name));
const first = (root, name) => {
  const found = byClass(root, name)[0];
  assert.ok(found, "no ." + name);
  return found;
};
const textOf = (root) => root.textContent;
const press = (node, key) => node.dispatch("keydown", { key });

function fakeUplot() {
  const made = [];
  class Plot {
    constructor(options, data, host) {
      this.options = options;
      this.data = data;
      this.host = host;
      this.destroyed = false;
      made.push(this);
    }

    destroy() {
      this.destroyed = true;
    }
  }
  Plot.tzDate = (d) => d;
  Plot.made = made;
  return Plot;
}

const BAR = widget("w3", { title: "Per type", chart: "bar", query: { rows: "entries", group_by: ["tpl"], measures: ["count"], limit: 10 }, options: { x: "tpl", y: "count" } });
const LINE = widget("w4", {
  title: "Per hour", chart: "line", query: { rows: "entries", group_by: ["bucket:1h"], measures: ["count"], limit: 10 },
  options: { x: "bucket:1h", y: "count" },
});
const STAT = widget("w2", { title: "Total", chart: "stat", query: { rows: "entries", group_by: [], measures: ["count"], limit: 1 }, options: { y: "count" } });
const TABLE = widget("w1", { title: HOSTILE });
const P = panel("aaaaaaaaaaaa", HOSTILE + " name", [TABLE, STAT, BAR, LINE]);

function result(columns, rows) {
  return { columns, rows, groups: rows.length, truncated: false, total: null, exact: true, method: "exact", notes: [] };
}
const TPL = [{ id: "tpl", role: "group" }, { id: "count", role: "measure" }];

function answerFor(p) {
  const a = runAnswer(p);
  a.results = [
    { id: "w1", status: "ok", error: null, result: result(TPL, [{ values: [1, 3], labels: [HOSTILE, null] }, { values: [2, 5], labels: ["Plain", null] }]) },
    { id: "w2", status: "ok", error: null, result: result([{ id: "count", role: "measure" }], [{ values: [1234], labels: [] }]) },
    { id: "w3", status: "ok", error: null, result: result(TPL, [{ values: [1, 3], labels: [HOSTILE, null] }, { values: [2, 6], labels: ["Plain", null] }]) },
    { id: "w4", status: "ok", error: null, result: result([{ id: "bucket:1h", role: "group" }, { id: "count", role: "measure" }], [{ values: [3600000, 2], labels: [null, null] }, { values: [7200000, 4], labels: [null, null] }]) },
  ].filter((r) => p.widgets.some((w) => w.id === r.id));
  return a;
}

function mount(options = {}) {
  const w = makeWorld({ panels: options.panels ?? [P] });
  const host = doc.createElement("div");
  doc.body.appendChild(host);
  const uplot = options.uplot ?? fakeUplot();
  const loadUplot = options.loadUplot ?? (async () => uplot);
  const ui = mountPanelsPanel(host, {
    api: w.api, getCase: () => w.caseId, getFieldNames: () => ["op"], onRequestClose: () => {},
    flowOptions: { now: w.options.now, setTimer: w.options.setTimer, clearTimer: w.options.clearTimer, connect: w.options.connect },
    loadUplot,
  });
  return { w, host, ui, uplot };
}

async function shown(options = {}) {
  const m = mount(options);
  m.ui.refresh();
  m.w.answerList();
  await flush();
  return m;
}

async function opened(options = {}, answer = answerFor) {
  const m = await shown(options);
  if (m.w.held.run.length > 0) {
    m.w.held.run[0].resolve(answer(m.w.server.panels[0]));
    await flush();
  }
  return m;
}

test("choosing a panel in the list opens and runs it; the list goes back to the open panel when the choice is refused", async () => {
  const Z = panel("zzzzzzzzzzzz", "Zed", [widget("w1")]);
  const m = await opened({ panels: [P, Z] });
  const select = first(m.host, "pn-select");
  assert.deepEqual(select.options.map((o) => o.value), [P.id, Z.id]);
  select.value = Z.id;
  select.dispatch("change");
  assert.equal(first(m.host, "pn-heading").textContent, "Zed");
  assert.deepEqual(m.w.held.run.at(-1).args, ["A", Z.id, undefined]);
  m.w.held.run.at(-1).resolve(runAnswer(Z));
  await flush();
  first(m.host, "pn-edit").click();
  select.value = P.id; // a keyboard user cannot reach it (disabled), but a script can: the model refuses
  select.dispatch("change");
  assert.equal(select.value, Z.id);
  assert.equal(m.ui.flow.state.selectedId, Z.id);
});

test("one live region only; the panel is idle until it is shown", () => {
  const { host, w } = mount();
  const live = host.all().filter((n) => n.attrs["aria-live"] || n.attrs.role === "status" || n.attrs.role === "alert");
  assert.equal(live.length, 1);
  assert.equal(live[0].attrs.role, "status");
  assert.equal(live[0].attrs["aria-live"], "polite");
  assert.equal(w.held.list.length, 0);
});

test("the buttons and the list wait while a change is written; the results region says when it is busy; the foot is for editing only", async () => {
  const m = await opened();
  const widgets = first(m.host, "pn-widgets");
  assert.equal(widgets.attrs["aria-busy"], "false");
  assert.equal(first(m.host, "pn-save").parent.hidden, true);
  first(m.host, "pn-run").click();
  assert.equal(widgets.attrs["aria-busy"], "true");
  m.w.held.run.at(-1).resolve(answerFor(m.w.server.panels[0]));
  await flush();
  assert.equal(widgets.attrs["aria-busy"], "false");
  first(m.host, "pn-new").click();
  first(m.host, "pn-name-input").value = "Gamma";
  first(m.host, "pn-name-form").dispatch("submit");
  for (const name of ["pn-new", "pn-rename", "pn-edit", "pn-delete", "pn-select"]) {
    assert.equal(first(m.host, name).disabled, true, name + " waits");
  }
  m.w.held.create[0].reject(new ApiFail(400, "bad_name"));
  await flush();
  for (const name of ["pn-new", "pn-rename", "pn-edit", "pn-delete", "pn-select"]) {
    assert.equal(first(m.host, name).disabled, false, name + " is back");
  }
});

test("the list, the heading, the counts and every widget card; log text and names are only text", async () => {
  const { host } = await opened();
  const select = first(host, "pn-select");
  assert.equal(select.value, P.id);
  assert.equal(select.options[0].textContent, P.name);
  assert.equal(first(host, "pn-heading").textContent, P.name);
  assert.match(textOf(first(host, "pn-meta")), /^4 widgets - Updated \d\d:\d\d:\d\d$/);
  const cards = byClass(host, "pn-widget");
  assert.equal(cards.length, 4);
  assert.equal(cards[0].children[0].textContent, HOSTILE, "the title is text");
  assert.equal(host.all().some((n) => n.tagName === "IMG"), false);
  // table widget: headers and cells
  const table = cards[0].all().find((n) => n.tagName === "TABLE");
  assert.deepEqual(table.all().filter((n) => n.tagName === "TH").map((n) => n.textContent), ["Message type", "Count"]);
  assert.deepEqual(table.all().filter((n) => n.tagName === "TD").map((n) => n.textContent), [HOSTILE, "3", "Plain", "5"]);
  // number widget
  assert.equal(first(cards[1], "pn-stat-value").textContent, "1,234");
  assert.equal(first(cards[1], "pn-stat-label").textContent, S.aggCount);
});

test("a bar chart is an image with a label, decorative bars, and a data table behind a disclosure", async () => {
  const { host } = await opened();
  const card = byClass(host, "pn-widget")[2];
  const bars = first(card, "pn-bars");
  assert.equal(bars.attrs.role, "img");
  assert.match(bars.attrs["aria-label"], /Bar chart of Per type, 2 bars/);
  const rows = byClass(card, "pn-bar-row");
  assert.equal(rows.length, 2);
  assert.equal(first(rows[1], "pn-bar-fill").style.width, "100.0%");
  assert.equal(first(rows[0], "pn-bar-fill").style.width, "50.0%");
  assert.equal(first(rows[0], "pn-bar-label").textContent, HOSTILE);
  const details = first(card, "pn-details");
  assert.equal(details.all().some((n) => n.tagName === "TABLE"), false, "the table is built when the disclosure opens");
  details.open = true;
  details.dispatch("toggle");
  const table = details.all().find((n) => n.tagName === "TABLE");
  assert.deepEqual(table.all().filter((n) => n.tagName === "TD").map((n) => n.textContent), [HOSTILE, "3", "Plain", "6"]);
});

test("the open data table stays open through an update", async () => {
  const { host, w } = await opened();
  const details = first(byClass(host, "pn-widget")[2], "pn-details");
  details.open = true;
  details.dispatch("toggle");
  w.advance(10000);
  w.streams.at(-1).onNotice(JSON.stringify({ seq: 1, min_interval_ms: 5000 }));
  w.held.run[1].resolve(answerFor(w.server.panels[0]));
  await flush();
  const again = first(byClass(host, "pn-widget")[2], "pn-details");
  assert.notEqual(again, details, "the card was rebuilt");
  assert.equal(again.open, true);
  assert.ok(again.all().some((n) => n.tagName === "TABLE"));
});

test("a line chart is drawn by uPlot from the data, in UTC, once; the table is beside it; a rebuild destroys the old chart", async () => {
  const { host, uplot, w } = await opened();
  assert.equal(uplot.made.length, 1);
  const plot = uplot.made[0];
  assert.deepEqual(plot.data, [[3600, 7200], [2, 4]]);
  assert.equal(plot.options.scales.x.time, true);
  assert.equal(typeof plot.options.tzDate, "function");
  assert.equal(first(byClass(host, "pn-widget")[3], "pn-plot").attrs.role, "img");
  assert.match(first(byClass(host, "pn-widget")[3], "pn-plot").attrs["aria-label"], /Line chart of Per hour/);
  assert.ok(first(byClass(host, "pn-widget")[3], "pn-details"));
  w.advance(10000);
  w.streams.at(-1).onNotice("{}");
  w.held.run[1].resolve(answerFor(w.server.panels[0]));
  await flush();
  assert.equal(plot.destroyed, true);
  assert.equal(uplot.made.length, 2);
  assert.equal(uplot.made[1].destroyed, false);
});

test("a chart whose library arrives after a newer rebuild is not drawn; a library that fails leaves the table", async () => {
  let release;
  const late = new Promise((resolve) => { release = resolve; });
  const uplot = fakeUplot();
  const { w } = await opened({ uplot, loadUplot: () => late });
  w.advance(10000);
  w.streams.at(-1).onNotice("{}");
  w.held.run[1].resolve(answerFor(w.server.panels[0]));
  await flush();
  release(uplot);
  await flush();
  assert.equal(uplot.made.length, 1, "only the chart of the current rebuild");
  const failing = await opened({ loadUplot: async () => { throw new Error("no chunk"); } });
  assert.ok(first(byClass(failing.host, "pn-widget")[3], "pn-details"), "the table is still there");
});

test("closing the tab destroys the charts, aborts the run, closes the stream and leaves no timer", async () => {
  const { ui, uplot, w } = await opened();
  w.advance(10000);
  w.streams.at(-1).onNotice("{}");
  const running = w.held.run.at(-1);
  ui.closed();
  assert.equal(uplot.made.every((p) => p.destroyed), true);
  assert.equal(running.aborted, true);
  assert.equal(w.live().length, 0);
  assert.equal(w.timers.size, 0);
  ui.destroy();
});

test("widget states: not run, running, error, skipped by the budget, no records", async () => {
  const Q = { rows: "entries", group_by: ["tpl"], measures: ["count"], limit: 10 };
  const p = panel("aaaaaaaaaaaa", "States", [widget("w1"), widget("w2"), widget("w3"), widget("w4"), widget("w5", { query: Q })]);
  const m = await shown({ panels: [p] });
  const states = () => byClass(m.host, "pn-widget").map((c) => c.children[1].textContent);
  assert.deepEqual(states(), Array(5).fill(S.pnWidgetLoading));
  m.w.held.run[0].resolve({
    panel: p.id, rev: 1, complete: false, elapsed_ms: 15000,
    results: [
      { id: "w1", status: "error", result: null, error: { code: "unknown_field", message: "server words" } },
      { id: "w2", status: "error", result: null, error: { code: "timeout", message: "x", estimated_rows: 5000 } },
      { id: "w3", status: "skipped", result: null, error: { code: "budget", message: "x" } },
      { id: "w4", status: "ok", error: null, result: result(TPL, []) },
    ],
  });
  await flush();
  assert.deepEqual(states(), [
    S.pnWidgetErrors.unknown_field,
    S.pnWidgetErrors.timeout + " " + S.pnWidgetTimeoutRows.replace("{rows}", "5,000"),
    S.pnWidgetErrors.budget,
    S.pnNoRows,
    S.pnWidgetNotRun,
  ]);
  assert.equal(first(m.host, "pn-status").textContent, S.pnRanPartial);
});

test("the status line says the outcome of what the user did, and stays silent for an automatic update", async () => {
  const m = await opened();
  const status = first(m.host, "pn-status");
  assert.equal(status.textContent, S.pnRanOther.replace("{count}", "4").replace("{seconds}", "0.1"));
  m.w.advance(10000);
  m.w.streams.at(-1).onNotice("{}");
  m.w.held.run[1].resolve({ ...answerFor(m.w.server.panels[0]), elapsed_ms: 4000 });
  await flush();
  assert.equal(status.textContent, S.pnRanOther.replace("{count}", "4").replace("{seconds}", "0.1"), "unchanged");
  m.w.held.run.length = 0;
  first(m.host, "pn-run").click();
  assert.equal(m.w.held.run.length, 1);
});

test("the live region stays the only one, and a role, after notices", async () => {
  const m = await opened();
  m.w.held.run.length = 0;
  first(m.host, "pn-run").click();
  m.w.held.run[0].reject(new ApiFail(500, "failed"));
  await flush();
  const live = m.host.all().filter((n) => n.attrs["aria-live"] || n.attrs.role === "status" || n.attrs.role === "alert");
  assert.equal(live.length, 1);
  assert.equal(live[0].attrs.role, "status");
  assert.equal(live[0].className.includes("is-error"), true);
});

test("destroy leaves nothing behind: no stream, no timer, no chart, no request", async () => {
  const { ui, uplot, w } = await opened();
  w.advance(10000);
  w.streams.at(-1).onNotice("{}");
  const running = w.held.run.at(-1);
  ui.destroy();
  assert.equal(running.aborted, true);
  assert.equal(w.live().length, 0);
  assert.equal(w.timers.size, 0);
  assert.equal(uplot.made.every((p) => p.destroyed), true);
});

test("a stopped stream is said in the meta line and in the live region", async () => {
  const m = await opened();
  m.w.streams.at(-1).close();
  m.w.streams.at(-1).onStopped();
  assert.equal(first(m.host, "pn-status").textContent, S.pnLiveStopped);
  assert.match(first(m.host, "pn-meta").textContent, /Live updates stopped/);
});

// ---- create, rename, delete: focus and keys

test("New: the name field takes the focus, Escape closes it without closing the dock, Create puts the focus on the new panel", async () => {
  const m = await opened();
  first(m.host, "pn-new").click();
  const form = first(m.host, "pn-name-form");
  const input = first(m.host, "pn-name-input");
  assert.equal(form.hidden, false);
  assert.equal(doc.activeElement, input);
  const esc = press(input, "Escape");
  assert.equal(esc.prevented, true, "the dock must not see this Escape");
  assert.equal(form.hidden, true);
  assert.equal(doc.activeElement, first(m.host, "pn-new"), "the focus returns to the button that opened it");
  first(m.host, "pn-new").click();
  input.value = "  Gamma ";
  form.dispatch("submit");
  assert.deepEqual(m.w.held.create[0].args[0], { name: "Gamma", widgets: [] });
  assert.equal(first(m.host, "pn-name-ok").disabled, true, "one change at a time");
  m.w.held.create[0].resolve(panel("cccccccccccc", "Gamma", [], 1));
  await flush();
  assert.equal(form.hidden, true);
  assert.equal(doc.activeElement, first(m.host, "pn-heading"));
  assert.equal(first(m.host, "pn-heading").textContent, "Gamma");
  assert.equal(first(m.host, "pn-select").value, "cccccccccccc");
  assert.equal(first(m.host, "pn-status").textContent, S.pnCreated.replace("{name}", "Gamma"));
  assert.match(first(m.host, "pn-view").textContent, /no widgets/);
});

test("Rename starts with the current name and keeps what was typed when the server refuses", async () => {
  const m = await opened();
  first(m.host, "pn-rename").click();
  const input = first(m.host, "pn-name-input");
  assert.equal(input.value, P.name);
  input.value = "Other";
  first(m.host, "pn-name-ok").click();
  m.w.held.update[0].reject(new ApiFail(409, "duplicate_name"));
  await flush();
  assert.equal(input.value, "Other");
  assert.equal(first(m.host, "pn-status").textContent, S.pnErrors.duplicate_name);
  assert.equal(first(m.host, "pn-name-form").hidden, false);
});

test("Delete: the question names the panel as text, Cancel is focused, Escape cancels, deleting moves the focus to the list", async () => {
  const m = await opened({ panels: [P, panel("bbbbbbbbbbbb", "Zed", [])] });
  first(m.host, "pn-delete").click();
  const row = first(m.host, "pn-confirm");
  assert.equal(row.hidden, false);
  assert.equal(first(m.host, "pn-confirm-text").textContent, S.pnDeleteAsk.replace("{name}", P.name));
  assert.equal(doc.activeElement, first(m.host, "pn-confirm-cancel"));
  assert.equal(press(row, "Escape").prevented, true);
  assert.equal(row.hidden, true);
  assert.equal(doc.activeElement, first(m.host, "pn-delete"));
  first(m.host, "pn-delete").click();
  first(m.host, "pn-confirm-ok").click();
  m.w.held.list[1].resolve({ panels: m.w.server.panels, problems: [], invalid: false, limits: {} });
  await flush();
  m.w.held.remove[0].resolve(null);
  await flush();
  assert.equal(row.hidden, true);
  assert.equal(doc.activeElement, first(m.host, "pn-select"));
  assert.equal(first(m.host, "pn-select").value, "bbbbbbbbbbbb");
});

test("problems of the panels file are listed as static text; the buttons follow the state", async () => {
  const m = mount({ panels: [] });
  m.ui.refresh();
  m.w.answerList(0, { panels: [], invalid: false, problems: [{ index: 0, code: "bad_query" }, { index: 3, code: HOSTILE }], limits: {} });
  await flush();
  const items = byClass(m.host, "pn-problem").map((n) => n.textContent);
  assert.equal(items.length, 2);
  assert.equal(first(m.host, "pn-problems").hidden, false);
  assert.match(items[0], /^Panel 1 of the panels file was skipped/);
  assert.ok(!items.join("").includes(HOSTILE));
  assert.equal(first(m.host, "pn-run").disabled, true);
  assert.equal(first(m.host, "pn-edit").disabled, true);
  assert.equal(first(m.host, "pn-delete").disabled, true);
  assert.equal(first(m.host, "pn-new").disabled, false);
  assert.equal(first(m.host, "pn-empty").textContent, S.pnNoPanelsYet);
});

// ---- the editor

async function editing(options) {
  const m = await opened(options);
  first(m.host, "pn-edit").click();
  return m;
}

test("Edit hides the results, shows the widget list with its keyboard controls and the Save / Cancel foot", async () => {
  const m = await editing();
  assert.equal(first(m.host, "pn-view").hidden, true);
  assert.equal(first(m.host, "pn-editor").hidden, false);
  assert.equal(first(m.host, "pn-save").parent.hidden, false);
  assert.equal(first(m.host, "pn-select").disabled, true);
  assert.equal(first(m.host, "pn-run").disabled, true);
  assert.equal(doc.activeElement, byClass(m.host, "pn-heading")[1]);
  const items = byClass(m.host, "pn-edit-item");
  assert.equal(items.length, 4);
  assert.equal(first(items[0], "pn-up").disabled, true);
  assert.equal(first(items[3], "pn-down").disabled, true);
  assert.equal(first(items[0], "pn-edit-widget").attrs["aria-label"], S.pnEditWidgetLabel.replace("{name}", HOSTILE));
  assert.equal(m.w.live().length, 0, "no stream while editing");
});

test("moving a widget keeps the focus on the same control, also at the end of the list", async () => {
  const m = await editing();
  const down = () => byClass(m.host, "pn-edit-item")[0].all().find((n) => n.dataset.key === "down:w1");
  down().focus();
  down().click();
  assert.equal(doc.activeElement.dataset.key, "down:w1");
  assert.deepEqual(byClass(m.host, "pn-edit-name").map((n) => n.textContent), [STAT.title, HOSTILE, BAR.title, LINE.title]);
  const last = byClass(m.host, "pn-edit-item")[3];
  last.all().find((n) => n.dataset.key === "up:w4").focus();
  last.all().find((n) => n.dataset.key === "up:w4").click();
  last.all().find((n) => n.dataset.key === "up:w4");
  const bottom = byClass(m.host, "pn-edit-item").at(-1);
  const key = doc.activeElement.dataset.key;
  assert.equal(key, "up:w4");
  assert.equal(bottom.all().some((n) => n.dataset.key === key) || byClass(m.host, "pn-edit-item").some((i) => i.all().some((n) => n === doc.activeElement)), true);
});

test("an update of the editor does not take the focus away from the control that has it", async () => {
  const m = await editing();
  const second = () => byClass(m.host, "pn-edit-item")[1].all().find((n) => n.dataset.key === "edit:w2");
  second().focus();
  m.ui.flow.updateWidget("w1", { title: "A new title" }); // the list is rebuilt
  assert.equal(doc.activeElement.dataset.key, "edit:w2");
  assert.equal(doc.activeElement, second());
});

test("the key types of the case the form was opened in are the only ones that reach it", async () => {
  const m = await opened();
  let release;
  m.w.api.keyTypes = (caseId) => new Promise((resolve) => { release = () => resolve({ types: [{ type: "msgid" }] }); void caseId; });
  first(m.host, "pn-edit").click();
  byClass(m.host, "pn-edit-item")[0].all().find((n) => n.dataset.key === "edit:w1").click();
  const stale = release;
  m.w.caseId = "B";
  m.ui.reset();
  stale();
  await flush();
  const options = () => byClass(m.host, "pn-group")[0].all().filter((n) => n.tagName === "OPTION").map((n) => n.value);
  assert.equal(options().includes("key.msgid"), false, "the answer of case A is dropped");
});

test("Add widget opens the form on its title; the chart that does not fit says why and blocks Save", async () => {
  const m = await editing();
  first(m.host, "pn-add").click();
  const form = first(m.host, "pn-widget-form");
  assert.equal(form.hidden, false);
  assert.equal(doc.activeElement, first(m.host, "pn-title"));
  const title = first(m.host, "pn-title");
  title.value = "Mine";
  title.dispatch("input");
  assert.equal(byClass(m.host, "pn-edit-name").at(-1).textContent, "Mine");
  const chart = first(m.host, "pn-chart");
  chart.value = "line";
  chart.dispatch("change");
  assert.equal(first(m.host, "pn-fit").hidden, false);
  assert.equal(first(m.host, "pn-fit").textContent, S.pnFit.line);
  first(m.host, "pn-save").click();
  assert.equal(m.w.held.update.length, 0);
  assert.equal(first(m.host, "pn-status").textContent, S.pnFit.line);
  chart.value = "bar";
  chart.dispatch("change");
  assert.equal(first(m.host, "pn-fit").hidden, true);
});

test("changing the form changes the query of the draft; an invalid form is explained next to the fields and blocks Save", async () => {
  const m = await editing();
  byClass(m.host, "pn-edit-item")[0].all().find((n) => n.dataset.key === "edit:w1").click();
  assert.equal(first(m.host, "pn-title").value, HOSTILE);
  const groups = byClass(m.host, "pn-group");
  groups[1].value = "sev";
  groups[1].dispatch("change");
  assert.match(byClass(m.host, "pn-edit-meta")[0].textContent, /Message type, Severity/);
  const measures = byClass(m.host, "pn-measure");
  for (const measure of measures) {
    measure.value = "";
  }
  measures[0].dispatch("change");
  const error = first(m.host, "pn-query-error");
  assert.equal(error.hidden, false);
  assert.equal(error.textContent, S.aggNeedMeasure);
  first(m.host, "pn-save").click();
  assert.equal(m.w.held.update.length, 0);
  assert.equal(first(m.host, "pn-status").textContent, S.aggNeedMeasure);
});

test("a valid example fills the form and the draft; Save sends the panel with the revision and returns to the results", async () => {
  const m = await editing();
  byClass(m.host, "pn-edit-item")[0].all().find((n) => n.dataset.key === "edit:w1").click();
  const examples = first(m.host, "pn-examples");
  examples.value = "op_duration";
  examples.dispatch("change");
  assert.equal(first(m.host, "pn-rows").value, "events");
  first(m.host, "pn-save").click();
  assert.equal(m.w.held.update.length, 1);
  const [id, body, rev] = m.w.held.update[0].args;
  assert.equal(id, P.id);
  assert.equal(rev, 1);
  assert.equal(body.widgets[0].query.rows, "events");
  assert.deepEqual(body.widgets[0].query.measures, ["count", "p50:duration", "p90:duration", "p99:duration"]);
  assert.equal(body.widgets[0].title, HOSTILE);
  assert.equal(first(m.host, "pn-save").disabled, true, "busy");
  m.w.held.update[0].resolve(panel(P.id, P.name, body.widgets, 2));
  await flush();
  assert.equal(first(m.host, "pn-editor").hidden, true);
  assert.equal(first(m.host, "pn-view").hidden, false);
  assert.equal(doc.activeElement, first(m.host, "pn-heading"));
  assert.equal(m.w.held.run.length, 2, "the saved panel runs");
});

test("a conflict on Save keeps the editor open with every edit and the message", async () => {
  const m = await editing();
  byClass(m.host, "pn-edit-item")[0].all().find((n) => n.dataset.key === "edit:w1").click();
  first(m.host, "pn-title").value = "Mine";
  first(m.host, "pn-title").dispatch("input");
  first(m.host, "pn-save").click();
  m.w.held.update[0].reject(new ApiFail(409, "rev_conflict"));
  await flush();
  m.w.server.panels = [panel(P.id, P.name, P.widgets, 3)];
  m.w.answerList();
  await flush();
  assert.equal(first(m.host, "pn-editor").hidden, false);
  assert.equal(first(m.host, "pn-title").value, "Mine");
  assert.equal(first(m.host, "pn-status").textContent, S.pnErrors.rev_conflict);
  assert.equal(first(m.host, "pn-save").disabled, false);
});

test("Cancel returns to the results of the saved panel and a second Edit starts from it, not from the old form", async () => {
  const m = await editing();
  byClass(m.host, "pn-edit-item")[0].all().find((n) => n.dataset.key === "edit:w1").click();
  first(m.host, "pn-title").value = "Changed";
  first(m.host, "pn-title").dispatch("input");
  first(m.host, "pn-cancel-edit").click();
  assert.equal(first(m.host, "pn-view").hidden, false);
  assert.equal(doc.activeElement, first(m.host, "pn-heading"));
  first(m.host, "pn-edit").click();
  byClass(m.host, "pn-edit-item")[0].all().find((n) => n.dataset.key === "edit:w1").click();
  assert.equal(first(m.host, "pn-title").value, HOSTILE);
});

test("a query the form cannot show is kept and only the title and chart are offered", async () => {
  const odd = widget("w1", { query: { rows: "entries", group_by: ["tpl"], measures: ["count", "distinct:source", "distinct:sev", "distinct:level", "distinct:tid"], limit: 5 } });
  const m = await editing({ panels: [panel("aaaaaaaaaaaa", "Odd", [odd])] });
  byClass(m.host, "pn-edit-item")[0].all().find((n) => n.dataset.key === "edit:w1").click();
  assert.equal(first(m.host, "pn-query-note").hidden, false);
  assert.equal(first(m.host, "pn-query-note").textContent, S.pnQueryReadOnly);
  assert.equal(byClass(m.host, "pn-group")[0].parent.parent.hidden, true);
  first(m.host, "pn-save").click();
  assert.deepEqual(m.w.held.update[0].args[1].widgets[0].query, odd.query);
});

test("a case change: the results of the old case go, the new case runs when the dock refreshes the tab", async () => {
  const m = await opened();
  m.w.caseId = "B";
  m.ui.reset();
  assert.deepEqual(byClass(m.host, "pn-widget").map((c) => c.children[1].textContent), Array(4).fill(S.pnWidgetNotRun));
  m.ui.refresh();
  await flush();
  assert.deepEqual(m.w.held.run.at(-1).args, ["B", P.id, undefined]);
  assert.deepEqual(byClass(m.host, "pn-widget").map((c) => c.children[1].textContent), Array(4).fill(S.pnWidgetLoading));
});

// ---- review round: stored values, writing, focus

test("editing a query keeps a stored grouping that the options do not have (yet): changing only the limit does not erase it", async () => {
  const keyed = widget("w1", { title: "By key", query: { rows: "entries", group_by: ["key.msgid"], measures: ["count"], limit: 100 } });
  const Pk = panel("kkkkkkkkkkkk", "Keyed", [keyed]);
  const w0 = makeWorld({ panels: [Pk] });
  let release;
  w0.api.keyTypes = () => new Promise((resolve) => { release = resolve; }); // the key types of the case have not arrived
  const host = doc.createElement("div");
  doc.body.appendChild(host);
  const ui = mountPanelsPanel(host, {
    api: w0.api, getCase: () => w0.caseId, getFieldNames: () => [], onRequestClose: () => {},
    flowOptions: { now: w0.options.now, setTimer: w0.options.setTimer, clearTimer: w0.options.clearTimer, connect: w0.options.connect },
    loadUplot: async () => fakeUplot(),
  });
  ui.refresh();
  w0.answerList();
  await flush();
  w0.held.run[0].resolve(runAnswer(Pk));
  await flush();
  first(host, "pn-edit").click();
  byClass(host, "pn-edit-item")[0].all().find((n) => n.dataset.key === "edit:w1").click();
  const groups = byClass(host, "pn-group");
  assert.equal(groups[0].value, "key.msgid", "the stored grouping is selected although it is not among the options yet");
  assert.equal(groups[1].value, "");
  const limit = first(host, "pn-limit");
  limit.value = "50";
  limit.dispatch("change");
  first(host, "pn-save").click();
  assert.deepEqual(w0.held.update[0].args[1].widgets[0].query.group_by, ["key.msgid"]);
  assert.equal(w0.held.update[0].args[1].widgets[0].query.limit, 50);
  w0.held.update[0].reject(new ApiFail(500, "boom"));
  await flush();
  release({ types: [{ type: "msgid" }] }); // now the option exists: the select shows it once, in its group
  await flush();
  assert.equal(groups[0].value, "key.msgid");
  assert.equal(groups[0].options.filter((o) => o.value === "key.msgid").length, 1);
  const extra = groups[0].options.filter((o) => o.value === "key.msgid");
  assert.equal(extra[0].parent.tagName, "OPTGROUP");
});

test("while a panel is written its fields are disabled; after a failure they are back and the next Save sends what they show", async () => {
  const m = await editing();
  byClass(m.host, "pn-edit-item")[0].all().find((n) => n.dataset.key === "edit:w1").click();
  const title = first(m.host, "pn-title");
  title.value = "Mine";
  title.dispatch("input");
  first(m.host, "pn-save").click();
  const fields = [title, first(m.host, "pn-chart"), first(m.host, "pn-rows"), first(m.host, "pn-limit"), first(m.host, "pn-order"),
    ...byClass(m.host, "pn-group"), ...byClass(m.host, "pn-measure"), first(m.host, "pn-add")];
  for (const field of fields) {
    assert.equal(field.disabled, true, field.className);
  }
  for (const key of ["edit", "up", "down", "remove"]) {
    for (const button of byClass(m.host, "pn-" + key).concat(byClass(m.host, "pn-" + key + "-widget"))) {
      assert.equal(button.disabled, true, button.className);
    }
  }
  title.value = "Late"; // a script, not a user: the model refuses
  title.dispatch("input");
  assert.equal(m.ui.flow.state.draft.widgets[0].title, "Mine");
  m.w.held.update[0].reject(new ApiFail(409, "busy"));
  await flush();
  for (const field of fields) {
    assert.equal(field.disabled, false, field.className);
  }
  assert.equal(doc.activeElement, title, "the focus returns to the field that was in use");
  title.value = "Mine"; // what the user sees after the failure is what the next Save sends
  first(m.host, "pn-save").click();
  assert.equal(m.w.held.update[1].args[1].widgets[0].title, "Mine");
});

test("an automatic update keeps the focus on the \"Data table\" summary of its widget", async () => {
  const { host, w } = await opened();
  const summary = first(byClass(host, "pn-widget")[2], "pn-summary");
  summary.focus();
  assert.equal(doc.activeElement, summary);
  w.advance(10000);
  w.streams.at(-1).onNotice(JSON.stringify({ seq: 1, min_interval_ms: 5000 }));
  w.held.run[1].resolve(answerFor(w.server.panels[0]));
  await flush();
  const again = first(byClass(host, "pn-widget")[2], "pn-summary");
  assert.notEqual(again, summary, "the card was rebuilt");
  assert.equal(doc.activeElement, again);
  const other = first(byClass(host, "pn-widget")[3], "pn-summary");
  assert.notEqual(doc.activeElement, other, "and not on another widget's summary");
});

test("bars of values that are negative, huge or stacked keep their place on one scale with a zero line", async () => {
  const wide = widget("w1", { title: "Signed", chart: "bar", query: { rows: "entries", group_by: ["tpl"], measures: ["count"], limit: 10 }, options: { x: "tpl", y: "count" } });
  const m = await opened({ panels: [panel("aaaaaaaaaaaa", "A", [wide])] }, (p) => {
    const a = runAnswer(p);
    a.results[0].result = result(TPL, [{ values: [1, -50], labels: ["neg", null] }, { values: [2, 100], labels: ["pos", null] }]);
    return a;
  });
  const rows = byClass(m.host, "pn-bar-row");
  const fill = (row) => first(row, "pn-bar-fill").style;
  assert.equal(fill(rows[0]).width, "33.3%");
  assert.equal(fill(rows[0]).left, "0.0%");
  assert.equal(fill(rows[1]).width, "66.7%");
  assert.equal(fill(rows[1]).left, "33.3%");
  assert.equal(byClass(m.host, "pn-bar-zero").length, 2);
  assert.equal(byClass(m.host, "pn-bar-zero")[0].style.left, "33.3%");
});
