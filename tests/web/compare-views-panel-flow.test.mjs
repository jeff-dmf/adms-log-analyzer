// 9.7 UI: the "Compare views" panel on the fake DOM: the sides, the states, the aligned rows written as text, keyboard
// movement, "Show in grid", paging, and what happens to pending requests when a newer one starts, the tab closes, the
// case or the view changes.
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";

import { El, installGlobalDom, tick } from "./fixtures/fake-dom.mjs";

const doc = installGlobalDom();
globalThis.Element = El;
const { mountCompareViewsPanel } = await import("../../src/dmslog/web/js/compare-views-panel.js");
const { S } = await import("../../src/dmslog/web/js/strings.js");
const { parseTime } = await import("../../src/dmslog/web/js/compare-views-model.js");

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}
const abortError = () => Object.assign(new Error("aborted"), { name: "AbortError" });
const cls = (node, name) => node.className.split(/\s+/).includes(name);
const byClass = (root, name) => root.all().filter((n) => cls(n, name));

const HOSTILE = "<img src=x onerror=alert(1)>";
const VIEWS = [{ id: 7, name: "Errors " + HOSTILE, filter: { sev: { le: 2 } } }];
const EVENTS = [
  { id: 5, name: "GetIncidents", ts_start: parseTime("2026-01-01 10:00:00"), status: "ok", stream_id: 3, family: "Wfm", process: "Wfm" },
  { id: 6, name: HOSTILE, ts_start: parseTime("2026-01-01 10:05:00"), status: "fail", stream_id: 4, family: "Smn", process: "Smn" },
];

const rowA = (id, extra = {}) => ({ id, ts: parseTime("2026-01-01 10:00:00") + id, label: "Request received", msg: "first " + id, ...extra });
const answer = (extra = {}) => ({
  mode: "filter",
  a: { count: 3, truncated: false, first_ts: 1, last_ts: 2 },
  b: { count: 3, truncated: false, first_ts: 1, last_ts: 2 },
  stats: { same: 2, only_a: 1, only_b: 0, changed_a: 0, changed_b: 0, similarity: 0.8 },
  templates: [{ code: "WFM-S010", label: "Request received", unknown: false, a: 2, b: 1 }], templates_total: 1,
  runs_total: 2, total_rows: 3, offset: 0, next_offset: null, digest: "d1", notes: [],
  rows: [
    { op: "same", a_pos: 0, b_pos: 0, a: rowA(1), b: rowA(11) },
    { op: "only_a", a_pos: 1, b_pos: null, a: rowA(2, { msg: HOSTILE }), b: null },
    { op: "same", a_pos: 2, b_pos: 1, a: rowA(3), b: rowA(13) },
  ],
  ...extra,
});

function world(overrides = {}) {
  const calls = [];
  const lists = [];
  const revealed = [];
  const current = { caseId: "A", filter: { sev: { le: 3 } } };
  let closed = 0;
  const hold = (name) => (caseId, ...args) => {
    const d = deferred();
    const signal = args.at(-1);
    calls.push({ name, caseId, args, signal, d });
    signal?.addEventListener?.("abort", () => d.reject(abortError()));
    return d.promise;
  };
  const host = doc.createElement("section");
  doc.body.appendChild(host);
  const api = {
    compareViews: { run: hold("compare") },
    views: { list: (caseId, signal) => { lists.push(["views", caseId]); return Promise.resolve({ views: VIEWS }); } },
    events: (caseId, params, signal) => { lists.push(["events", caseId, params]); return Promise.resolve({ items: EVENTS }); },
    ...overrides,
  };
  const panel = mountCompareViewsPanel(host, {
    api,
    getCase: () => current.caseId,
    getFilter: () => current.filter,
    onReveal: (row) => revealed.push(row),
    onRequestClose: () => { closed += 1; },
  });
  const byId = (suffix) => host.all().find((n) => n.id.endsWith("-" + suffix));
  const w = {
    host, panel, calls, lists, revealed, current, byId, closed: () => closed,
    compare: () => calls.filter((c) => c.name === "compare"),
    mode: () => host.all().find((n) => n.id.endsWith("-mode")),
    max: () => host.all().find((n) => n.id.endsWith("-max")),
    diff: () => host.all().find((n) => n.tagName === "INPUT" && n.type === "checkbox"),
    run: () => byClass(host, "cv-run")[0],
    cancel: () => byClass(host, "cv-cancel")[0],
    status: () => byClass(host, "cv-status")[0],
    rows: () => byClass(host, "cv-row"),
    opens: () => byClass(host, "cv-open"),
    more: () => byClass(host, "cv-more")[0],
    raise: () => byClass(host, "cv-raise")[0],
    results: () => byClass(host, "cv-results")[0],
    setMode(mode) { const m = w.mode(); m.value = mode; m.dispatch("change"); },
  };
  return w;
}

async function ready(w) {
  await tick();
  await tick();
}

async function compared(w, extra = {}) {
  await ready(w);
  w.run().click();
  w.compare().at(-1).d.resolve(answer(extra));
  await tick();
  await tick();
}

test("the frame: a title, Reload lists and Close; Close asks the dock; one live region says what to do first", async () => {
  const w = world();
  await ready(w);
  assert.equal(w.host.all().find((n) => n.tagName === "H2").textContent, S.cvTitle);
  const buttons = w.host.all().filter((n) => n.tagName === "BUTTON");
  assert.ok(buttons.some((b) => cls(b, "panel-refresh") && b.textContent === S.cvRefreshLists));
  buttons.find((b) => cls(b, "panel-close")).click();
  assert.equal(w.closed(), 1);
  assert.equal(w.status().textContent, S.cvIdle);
  assert.equal(w.status().getAttribute("role"), "status");
  const live = w.host.all().filter((n) => n.attrs.role === "status" || n.attrs.role === "alert" || n.attrs["aria-live"] !== undefined);
  assert.equal(live.length, 1, "one live region for the whole panel");
  assert.equal(w.results().hidden, true);
  assert.equal(w.cancel().hidden, true);
});

test("no document or window listeners; nothing is built from HTML", async () => {
  const w = world();
  await compared(w);
  assert.deepEqual(Object.keys(doc.listeners).filter((k) => (doc.listeners[k] || []).length > 0), []);
  for (const file of ["compare-views-panel.js", "compare-views-model.js"]) {
    const source = fs.readFileSync(new URL("../../src/dmslog/web/js/" + file, import.meta.url), "utf8");
    assert.doesNotMatch(source, /innerHTML|outerHTML|insertAdjacentHTML|document\.write|\beval\(|new Function/, file);
  }
  // DOM collections have no array methods: the panel keeps its own arrays of the buttons and never reads `.children`
  const panelSource = fs.readFileSync(new URL("../../src/dmslog/web/js/compare-views-panel.js", import.meta.url), "utf8");
  assert.doesNotMatch(panelSource, /\.children\b|\.childNodes\b|querySelectorAll|getElementsBy/);
  assert.doesNotMatch(panelSource, /document\.addEventListener|window\.addEventListener|setTimeout|setInterval|requestAnimationFrame/);
});

test("modes show their own fields and load their own lists once; names from the log stay text", async () => {
  const w = world();
  await ready(w);
  assert.deepEqual(w.lists, [["views", "A"]]);
  const groups = (side) => ["filter", "window", "operation", "process"].map((m) => byClass(w.host, "cv-side-" + side)[0].all().find((n) => cls(n, "cv-group-" + m)).hidden);
  assert.deepEqual(groups("a"), [false, true, true, true]);
  w.setMode("operation");
  await ready(w);
  assert.deepEqual(groups("a"), [true, true, false, true]);
  assert.deepEqual(w.lists.at(-1), ["events", "A", { kind: "operation", limit: 200 }]);
  const op = w.byId("a-op");
  assert.deepEqual(op.options.map((o) => o.textContent), [
    S.cvChooseOperation, "GetIncidents at 2026-01-01 10:00:00.000 ok", HOSTILE + " at 2026-01-01 10:05:00.000 fail",
  ]);
  w.setMode("process");
  await ready(w);
  assert.deepEqual(w.lists.at(-1), ["events", "A", { limit: 200 }]);
  assert.deepEqual(w.byId("b-proc").options.map((o) => o.textContent), [S.cvChooseProcess, "Wfm", "Smn"]);
  w.setMode("operation");
  await ready(w);
  assert.equal(w.lists.length, 3, "a list already loaded for the case is not asked again");
  w.setMode("window");
  await ready(w);
  assert.equal(w.lists.length, 3, "windows need no list");
});

test("filter mode: A is the current view, B starts on the first saved view; Compare sends the filters and shows loading", async () => {
  const w = world();
  await ready(w);
  assert.equal(w.byId("a-view").value, "current");
  assert.equal(w.byId("b-view").value, "7");
  assert.deepEqual(w.byId("b-view").options.map((o) => o.textContent), [S.cvCurrentView, "Errors " + HOSTILE]);
  w.run().click();
  assert.equal(w.status().textContent, S.cvLoading);
  assert.equal(w.cancel().hidden, false);
  const body = w.compare()[0].args[0];
  assert.deepEqual([w.compare()[0].caseId, body.mode, body.filter, body.a, body.b], ["A", "filter", null, { filter: { sev: { le: 3 } } }, { filter: { sev: { le: 2 } } }]);
  w.compare()[0].d.resolve(answer());
  await tick();
  await tick();
  assert.equal(w.cancel().hidden, true);
  assert.equal(w.results().hidden, false);
  assert.match(w.status().textContent, /^Compared 3 records in side A with 3 records in side B: 80% similar$/);
  const cards = byClass(w.host, "cv-card");
  assert.equal(cards[1].all().find((n) => cls(n, "cv-card-desc")).textContent, "Errors " + HOSTILE);
  assert.equal(cards[0].all().find((n) => cls(n, "cv-card-desc")).textContent, S.cvCurrentView);
});

test("the aligned rows: the kind of difference is written, records are text, a missing side says so", async () => {
  const w = world();
  await compared(w);
  const rows = w.rows();
  assert.equal(rows.length, 3);
  assert.deepEqual(rows.map((r) => byClass(r, "cv-op-label")[0].textContent), ["Same", "Only in A", "Same"]);
  const second = rows[1];
  const text = byClass(second, "cv-cell-text");
  assert.equal(text.length, 1);
  assert.equal(text[0].textContent, HOSTILE);
  assert.equal(text[0].children.length, 0, "no element was built from the log text");
  assert.equal(byClass(second, "cv-cell-b")[0].textContent, S.cvNoRecord.replace("{side}", "B") + S.cvNoneMark);
  assert.equal(second.all().filter((n) => cls(n, "cv-open")).length, 1);
  const first = byClass(rows[0], "cv-open")[0];
  assert.equal(first.getAttribute("aria-label"), "Show side A record in the grid: Request received, 2026-01-01 10:00:00.001");
  assert.equal(byClass(w.host, "cv-types")[0].all().filter((n) => cls(n, "cv-type")).length, 1);
  assert.equal(byClass(w.host, "cv-rows-count")[0].textContent, "3 of 3 rows");
  assert.equal(byClass(w.host, "cv-rows")[0].getAttribute("role"), "grid");
});

test("Show in grid: Enter or click on a record asks for exactly that record; a missing side has no button", async () => {
  const w = world();
  await compared(w);
  const opens = w.opens();
  assert.equal(opens.length, 5);
  opens[0].click();
  opens[1].click();
  assert.deepEqual(w.revealed, [{ id: 1, ts: parseTime("2026-01-01 10:00:00") + 1 }, { id: 11, ts: parseTime("2026-01-01 10:00:00") + 11 }]);
  w.current.caseId = "B";
  opens[2].click();
  assert.equal(w.revealed.length, 2, "ids belong to the case the result was read for");
});

test("keyboard: one tab stop, arrows move the focus and the stop, a missing side is skipped, Home and End", async () => {
  const w = world();
  await compared(w);
  const tabStops = () => w.opens().filter((b) => b.getAttribute("tabindex") === "0");
  assert.equal(tabStops().length, 1);
  assert.equal(tabStops()[0], w.opens()[0]);
  const cell = (row, name) => byClass(w.rows()[row], "cv-cell-" + name)[0].all().find((n) => cls(n, "cv-open"));
  const key = (key) => doc.activeElement.dispatch("keydown", { key });
  cell(0, "a").focus();
  assert.equal(key("ArrowRight").prevented, true);
  assert.equal(doc.activeElement, cell(0, "b"));
  assert.equal(tabStops().length, 1);
  assert.equal(tabStops()[0], cell(0, "b"));
  key("ArrowDown");
  assert.equal(doc.activeElement, cell(1, "a"), "row 2 has no record on B: the focus falls back to A");
  key("ArrowRight");
  assert.equal(doc.activeElement, cell(1, "a"));
  key("End");
  assert.equal(doc.activeElement, cell(2, "a"));
  key("Home");
  assert.equal(doc.activeElement, cell(0, "a"));
  assert.equal(key("ArrowUp").prevented, true);
  assert.equal(doc.activeElement, cell(0, "a"));
  assert.equal(key("x").prevented, false);
  assert.equal(doc.activeElement.dispatch("keydown", { key: "ArrowDown", ctrlKey: true }).prevented, false, "browser shortcuts are left alone");
  assert.equal(tabStops().length, 1);
});

test("next and previous difference move the focus to a row that is not the same, and say so at the ends", async () => {
  const w = world();
  await compared(w);
  const next = byClass(w.host, "cv-next")[0];
  const prev = byClass(w.host, "cv-prev")[0];
  next.click();
  assert.equal(doc.activeElement, byClass(w.rows()[1], "cv-open")[0]);
  next.click();
  assert.equal(doc.activeElement, byClass(w.rows()[1], "cv-open")[0]);
  assert.equal(w.status().textContent, S.cvNoNextDiff);
  prev.click();
  assert.equal(w.status().textContent, S.cvNoPrevDiff);
});

test("a refused form shows the reason by the side, announces once, and asks for nothing", async () => {
  const w = world();
  await ready(w);
  w.setMode("operation");
  await ready(w);
  w.run().click();
  assert.equal(w.compare().length, 0);
  const errors = byClass(w.host, "cv-side-error");
  assert.equal(errors[0].textContent, S.cvInvalid.noOperation);
  assert.equal(errors[0].hidden, false);
  assert.equal(byClass(w.host, "cv-side-a")[0].getAttribute("aria-describedby"), errors[0].id);
  assert.equal(w.status().textContent, S.cvFixSides);
  assert.ok(cls(w.status(), "is-error"));
  w.byId("a-op").value = "5";
  w.byId("b-op").value = "6";
  w.run().click();
  assert.equal(w.compare().length, 1);
  assert.equal(errors[0].hidden, true);
  assert.equal(byClass(w.host, "cv-side-a")[0].getAttribute("aria-describedby"), null);
  assert.deepEqual(w.compare()[0].args[0].a, { event: 5 });
  assert.deepEqual(w.compare()[0].args[0].filter, { sev: { le: 3 } }, "the other modes use the current view");
});

test("window mode: times are typed as the log shows them", async () => {
  const w = world();
  await ready(w);
  w.setMode("window");
  w.byId("a-from").value = "2026-01-01 10:00";
  w.byId("a-to").value = "2026-01-01 11:00";
  w.byId("b-from").value = "garbage";
  w.byId("b-to").value = "2026-01-01 11:00";
  w.run().click();
  assert.equal(w.compare().length, 0);
  assert.equal(byClass(w.host, "cv-side-error")[1].textContent, S.cvInvalid.badFrom);
  w.byId("b-from").value = "2026-01-01 12:00";
  w.byId("b-to").value = "2026-01-01 13:00";
  w.run().click();
  assert.deepEqual(w.compare()[0].args[0].a, { from: parseTime("2026-01-01 10:00"), to: parseTime("2026-01-01 11:00") });
});

test("a newer Compare replaces the older: the older request is aborted and its late answer never shows", async () => {
  const w = world();
  await ready(w);
  w.run().click();
  w.run().click();
  assert.equal(w.compare().length, 2);
  assert.equal(w.compare()[0].signal.aborted, true);
  w.compare()[1].d.resolve(answer({ digest: "second", a: { count: 7, truncated: false, first_ts: 1, last_ts: 2 } }));
  await tick();
  await tick();
  w.compare()[0].d.resolve(answer({ digest: "first" }));
  await tick();
  assert.match(w.status().textContent, /7 records in side A/);
});

test("Cancel stops the run, hides itself and gives the focus back to Compare", async () => {
  const w = world();
  await ready(w);
  w.run().click();
  w.cancel().focus();
  w.cancel().click();
  assert.equal(w.compare()[0].signal.aborted, true);
  assert.equal(w.cancel().hidden, true);
  assert.equal(doc.activeElement, w.run());
  assert.equal(w.status().textContent, S.cvCancelled);
  w.compare()[0].d.resolve(answer());
  await tick();
  assert.equal(w.results().hidden, true);
});

test("closing the tab cancels what is pending (comparison and lists); the form stays", async () => {
  const signals = [];
  const w = world({ events: (caseId, params, signal) => { signals.push(signal); return new Promise(() => {}); } });
  await ready(w);
  w.run().click();
  assert.equal(w.compare().length, 1);
  w.setMode("operation");
  assert.equal(signals.length, 1);
  assert.equal(signals[0].aborted, false);
  w.panel.closed();
  assert.equal(w.compare()[0].signal.aborted, true);
  assert.equal(signals[0].aborted, true, "the list on the way is dropped too");
  assert.equal(w.status().textContent, S.cvIdle);
  assert.equal(w.cancel().hidden, true);
  w.compare()[0].d.resolve(answer());
  await tick();
  assert.equal(w.results().hidden, true);
  assert.equal(w.mode().value, "operation", "the form stays");
  assert.equal(w.byId("a-op").options[0].textContent, S.cvChooseOperation, "a dropped list can be asked again");
});

test("the case changing: a late answer is dropped and reset() forgets the result and the lists", async () => {
  const w = world();
  await ready(w);
  w.run().click();
  w.current.caseId = "B";
  w.compare()[0].d.resolve(answer());
  await tick();
  await tick();
  assert.equal(w.results().hidden, true, "an answer for the case that was left is not shown");
  w.current.caseId = "A";
  await compared(w);
  assert.equal(w.results().hidden, false);
  w.current.caseId = "B";
  w.panel.reset();
  await ready(w);
  assert.equal(w.results().hidden, true);
  assert.equal(w.rows().length, 0);
  assert.equal(w.status().textContent, S.cvIdle);
  assert.deepEqual(w.lists.at(-1), ["views", "B"], "the lists are read again for the new case");
});

test("the view changing: the result stays, marked out of date; a run in progress is cancelled with an explanation", async () => {
  const w = world();
  await compared(w);
  const stale = byClass(w.host, "cv-stale")[0];
  assert.equal(stale.hidden, true);
  w.panel.refresh();
  assert.equal(stale.hidden, false);
  assert.equal(stale.textContent, S.cvViewChanged);
  assert.equal(w.rows().length, 3);
  w.run().click();
  assert.equal(stale.hidden, true, "a new run is for the new view");
  w.panel.refresh();
  assert.equal(w.compare().at(-1).signal.aborted, true);
  assert.equal(w.status().textContent, S.cvViewChangedRun);
  assert.equal(stale.hidden, false);
  assert.equal(w.results().hidden, true);
});

test("an error says why in the live region and the next run clears it; the server's text is not shown", async () => {
  const w = world();
  await ready(w);
  w.run().click();
  w.compare()[0].d.reject(Object.assign(new Error("<b>raw server text</b>"), { status: 404, code: "event_not_found" }));
  await tick();
  await tick();
  assert.equal(w.status().textContent, S.cvError + ": " + S.cvErrors.event_not_found);
  assert.ok(cls(w.status(), "is-error"));
  assert.doesNotMatch(w.host.textContent, /raw server text/);
  w.run().click();
  assert.equal(w.status().textContent, S.cvLoading);
  assert.ok(!cls(w.status(), "is-error"));
});

test("a side cut at the limit: explained, and Compare up to N reruns the same comparison with a larger limit", async () => {
  const w = world();
  await compared(w, { a: { count: 5000, truncated: true, first_ts: 1, last_ts: 2 }, notes: ["truncated_a"] });
  assert.match(w.status().textContent, /A side was cut at the record limit$/);
  assert.equal(byClass(w.host, "cv-notes")[0].hidden, false);
  assert.match(byClass(w.host, "cv-notes")[0].textContent, /Side A has more than 5,000 records: only the first 5,000 in order were compared\./);
  assert.match(byClass(w.host, "cv-card")[0].textContent, /\(cut at the limit\)/);
  const raise = w.raise();
  assert.equal(raise.hidden, false);
  assert.equal(raise.textContent, "Compare up to 20,000 records per side");
  w.byId("a-view").value = "7"; // the form moved on: the rerun is of the result, not of the form
  raise.focus();
  raise.click();
  const body = w.compare().at(-1).args[0];
  assert.equal(body.max_side, 20000);
  assert.deepEqual(body.a, { filter: { sev: { le: 3 } } });
  assert.equal(w.max().value, "20000");
  assert.equal(doc.activeElement, w.run(), "the button went away with the old result: the focus does not fall to the page");
  w.compare().at(-1).d.resolve(answer());
  await tick();
  await tick();
  assert.equal(w.raise().hidden, true);
});

test("Only differences redoes the comparison shown with the other rows, and does nothing before a first run", async () => {
  const w = world();
  await ready(w);
  w.diff().checked = true;
  w.diff().dispatch("change");
  assert.equal(w.compare().length, 0);
  await compared(w);
  w.byId("a-view").value = "7"; // the form moved on; the rows shown are for the sides that were compared
  w.diff().checked = true;
  w.diff().dispatch("change");
  assert.equal(w.compare().length, 2);
  assert.equal(w.compare()[1].args[0].only_diff, true);
  assert.deepEqual(w.compare()[1].args[0].a, { filter: { sev: { le: 3 } } });
  assert.equal(w.compare()[1].args[0].offset, 0);
});

test("Load more appends the next page; the focus moves on to the first new row when the button goes away", async () => {
  const w = world();
  await compared(w, { next_offset: 3, total_rows: 5 });
  const more = w.more();
  assert.equal(more.hidden, false);
  assert.equal(more.textContent, S.cvLoadMore);
  more.focus();
  more.click();
  assert.equal(more.textContent, S.cvLoadingMore);
  assert.equal(more.getAttribute("aria-disabled"), "true");
  more.click();
  assert.equal(w.compare().length, 2, "a second click while loading asks for nothing");
  assert.equal(w.compare()[1].args[0].offset, 3);
  w.compare()[1].d.resolve(answer({
    offset: 3, next_offset: null, total_rows: 5,
    rows: [{ op: "only_b", a_pos: null, b_pos: 2, a: null, b: rowA(14) }, { op: "same", a_pos: 3, b_pos: 3, a: rowA(4), b: rowA(15) }],
  }));
  await tick();
  await tick();
  assert.equal(w.rows().length, 5);
  assert.equal(more.hidden, true);
  assert.equal(doc.activeElement, byClass(w.rows()[3], "cv-open")[0]);
  assert.equal(byClass(w.host, "cv-rows-count")[0].textContent, "5 of 5 rows");
  assert.equal(w.opens().filter((b) => b.getAttribute("tabindex") === "0").length, 1);
});

test("a page of another comparison is refused with a message instead of being mixed in; a failed page can be retried", async () => {
  let w = world();
  await compared(w, { next_offset: 3, total_rows: 5 });
  w.more().click();
  w.compare()[1].d.resolve(answer({ offset: 3, digest: "other", next_offset: null }));
  await tick();
  await tick();
  assert.equal(w.rows().length, 3);
  assert.equal(w.status().textContent, S.cvChanged);
  assert.equal(w.more().hidden, true);
  w = world();
  await compared(w, { next_offset: 3, total_rows: 5 });
  w.more().click();
  w.compare()[1].d.reject(Object.assign(new Error("x"), { status: 408, code: "query_timeout" }));
  await tick();
  await tick();
  assert.equal(w.more().textContent, S.cvRetry);
  assert.equal(w.status().textContent, S.cvMoreError + ": " + S.cvErrors.query_timeout);
  assert.equal(w.rows().length, 3);
});

test("rebuilding the results while the focus is inside them never leaves it on the page", async () => {
  const w = world();
  await compared(w);
  w.opens()[0].focus();
  w.run().click();
  assert.equal(doc.activeElement, w.run());
});

test("a message repeated by the panel (no later difference twice) is announced again", async () => {
  const w = world();
  await compared(w);
  const writes = [];
  const line = w.status();
  Object.defineProperty(line, "textContent", { get: () => line._text, set: (v) => { writes.push(v); line._text = String(v); line.children = []; }, configurable: true });
  const next = byClass(w.host, "cv-next")[0];
  next.click(); // moves to the only difference
  next.click();
  next.click();
  assert.deepEqual(writes, ["", S.cvNoNextDiff, "", S.cvNoNextDiff], "the text is cleared first, so a screen reader reads it again");
});
