// Pure rules of the Panels panel (panels-model.js): error texts, query <-> form, chart fit and options (checked against the
// real validator of store/panels.py), chart data, notices and the refresh delay.
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

import {
  CHARTS, DEFAULT_LIMITS, chartFit, chartSpec, cleanList, cleanPanel, defaultQuery, defaultWidget, deriveOptions, draftProblem,
  errorMessage, formFromQuery, formToQuery, nameProblem, nextWidgetId, parseNotice, problemLines, queryEditable, refreshDelay,
  sortedPanels, statValue, summarizeQuery, widgetBody, widgetErrorText,
} from "../../src/dmslog/web/js/panels-model.js";
import { S } from "../../src/dmslog/web/js/strings.js";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");

test("every code of PANEL_ERRORS and PANEL_WIDGET_ERRORS has a text of its own in strings.js", () => {
  const source = fs.readFileSync(path.join(ROOT, "src/dmslog/messages.py"), "utf8");
  const codes = (name) => {
    const body = source.slice(source.indexOf(name + " = {"));
    return [...body.slice(0, body.indexOf("\n}")).matchAll(/^ {4}"(\w+)":/gm)].map((m) => m[1]);
  };
  const panelCodes = codes("PANEL_ERRORS");
  const widgetCodes = codes("PANEL_WIDGET_ERRORS");
  assert.ok(panelCodes.length >= 25 && widgetCodes.length === 5, "the lists were found");
  for (const code of panelCodes) {
    assert.ok(typeof S.pnErrors[code] === "string" && S.pnErrors[code] !== "", "PANEL_ERRORS " + code);
  }
  for (const code of widgetCodes) {
    assert.ok(typeof S.pnWidgetErrors[code] === "string" && S.pnWidgetErrors[code] !== "", "PANEL_WIDGET_ERRORS " + code);
  }
});

test("errorMessage: our text for every code, the limit filled in, never the server's own words", () => {
  assert.equal(errorMessage({ code: "rev_conflict", message: "server words" }), S.pnErrors.rev_conflict);
  assert.match(errorMessage({ code: "too_many_panels" }, { ...DEFAULT_LIMITS, panels: 7 }), /at most 7 panels/);
  assert.match(errorMessage({ code: "bad_name" }), /1 to 80 characters/);
  assert.match(errorMessage({ code: "too_many_rows" }), /4000 rows/);
  assert.equal(errorMessage({ code: "never_heard_of_it", message: "boom" }), S.pnErrGeneric);
  assert.equal(errorMessage({ code: "network", status: 0 }), S.pnErrNetwork);
  assert.equal(errorMessage(null), S.pnErrGeneric);
  assert.equal(errorMessage({ code: "constructor" }), S.pnErrGeneric, "a code is never looked up through the prototype");
  assert.equal(errorMessage({ code: "__proto__" }), S.pnErrGeneric);
});

test("widgetErrorText: static text per code, unknown codes read as a failure, the estimate is appended for a timeout", () => {
  assert.equal(widgetErrorText({ code: "unknown_field" }), S.pnWidgetErrors.unknown_field);
  assert.equal(widgetErrorText({ code: "budget" }), S.pnWidgetErrors.budget);
  assert.equal(widgetErrorText({ code: "<img>" }), S.pnWidgetErrors.failed);
  assert.equal(widgetErrorText(null), S.pnWidgetErrors.failed);
  assert.match(widgetErrorText({ code: "timeout", estimated_rows: 1234567 }), /about 1,234,567 rows/);
});

test("problemLines: an unusable file and each skipped panel, as static text", () => {
  const list = cleanList({ panels: [], invalid: true, problems: [{ index: 2, code: "bad_query", extra: "<b>" }, { index: "x", code: 1 }] });
  const lines = problemLines(list);
  assert.equal(lines.length, 2);
  assert.equal(lines[0], S.pnFileInvalid);
  assert.match(lines[1], /^Panel 3 of the panels file was skipped/);
  assert.ok(!lines.join("").includes("<b>"));
});

test("cleanPanel and cleanList: only well-formed panels get in, whatever else the server said", () => {
  const good = { id: "aaaaaaaaaaaa", rev: 2, name: "A", widgets: [{ id: "w1", title: "t", query: { rows: "entries" }, chart: "table", options: {} }] };
  assert.equal(cleanPanel(good).rev, 2);
  for (const bad of [null, [], { ...good, rev: "2" }, { ...good, name: 3 }, { ...good, widgets: "x" }, { ...good, widgets: [{ id: 1 }] },
    { ...good, widgets: [{ ...good.widgets[0], chart: "pie" }] }, { ...good, id: 5 }]) {
    assert.equal(cleanPanel(bad), null);
  }
  const list = cleanList({ panels: [good, { nope: 1 }], problems: "x", invalid: "yes", limits: { panels: 3, widgets: "9", extra: 1 } });
  assert.equal(list.panels.length, 1);
  assert.deepEqual(list.problems, []);
  assert.equal(list.invalidFile, false, "only a real true counts");
  assert.deepEqual(list.limits, { ...DEFAULT_LIMITS, panels: 3 });
  assert.deepEqual(cleanList(undefined).panels, []);
});

test("sortedPanels orders by name, ignoring case, then by id; it does not change its input", () => {
  const input = [{ id: "b", name: "beta" }, { id: "a", name: "Alpha" }, { id: "c", name: "alpha" }];
  assert.deepEqual(sortedPanels(input).map((p) => p.id), ["a", "c", "b"]);
  assert.deepEqual(input.map((p) => p.id), ["b", "a", "c"]);
});

test("nameProblem: 1 to the limit characters after trimming", () => {
  assert.notEqual(nameProblem(""), null);
  assert.notEqual(nameProblem("   "), null);
  assert.notEqual(nameProblem(undefined), null);
  assert.equal(nameProblem(" x "), null);
  assert.equal(nameProblem("x".repeat(80)), null);
  assert.notEqual(nameProblem("x".repeat(81)), null);
});

test("nextWidgetId: the smallest free w<n>, valid for the server's id rule", () => {
  assert.equal(nextWidgetId([]), "w1");
  assert.equal(nextWidgetId([{ id: "w1" }, { id: "w3" }]), "w2");
  assert.equal(nextWidgetId([{ id: "w2" }]), "w1");
  assert.match(nextWidgetId(Array.from({ length: 15 }, (_, i) => ({ id: "w" + (i + 1) }))), /^[a-z0-9_-]{1,32}$/);
});

// ---- query <-> form

const QUERIES = [
  defaultQuery(),
  { rows: "events", events: { kind: ["operation"], status: ["fail", "timeout"] }, group_by: ["name"], measures: ["count", "p90:duration"], order: { by: "p90:duration", dir: "desc" }, limit: 20 },
  { rows: "entries", filter: { and: [{ sev: { le: 3 } }, { sev: { le: 2 } }] }, group_by: ["bucket:1h", "tpl"], measures: ["count"], limit: 1000 },
  { rows: "entries", filter: { sev: { le: 3 } }, group_by: ["key.errcode"], measures: ["count", "distinct:source"], lookups: ["error.reason"], limit: 50 },
  { rows: "entries", group_by: [], measures: ["count"], limit: 1 },
];

test("formFromQuery then formToQuery gives the same query back (key order and a lone filter aside)", () => {
  for (const query of QUERIES) {
    assert.equal(queryEditable(query), true, JSON.stringify(query));
    const back = formToQuery(formFromQuery(query));
    assert.deepEqual(Object.keys(back.query).sort(), Object.keys(query).sort());
    assert.equal(JSON.stringify(back.query.group_by), JSON.stringify(query.group_by));
    assert.equal(JSON.stringify(back.query.measures), JSON.stringify(query.measures));
  }
});

test("'Only errors' is the form's own condition; any other filter is kept as it is", () => {
  const form = formFromQuery({ rows: "entries", filter: { and: [{ tpl: [4] }, { sev: { le: 2 } }] }, group_by: ["tpl"], measures: ["count"], limit: 5 });
  assert.equal(form.onlyErrors, true);
  assert.deepEqual(form.baseFilter, { and: [{ tpl: [4] }] });
  const plain = formFromQuery({ rows: "entries", filter: { tpl: [4] }, group_by: ["tpl"], measures: ["count"], limit: 5 });
  assert.equal(plain.onlyErrors, false);
  assert.deepEqual(plain.baseFilter, { and: [{ tpl: [4] }] });
  plain.onlyErrors = true;
  assert.deepEqual(formToQuery(plain).query.filter, { and: [{ tpl: [4] }, { sev: { le: 2 } }] });
});

test("a query the form cannot show whole is not editable: more than four measures, event values with commas, unknown members", () => {
  assert.equal(queryEditable({ rows: "entries", group_by: ["tpl"], measures: ["count", "distinct:source", "distinct:sev", "distinct:level", "distinct:tid"], limit: 5 }), false);
  assert.equal(queryEditable({ rows: "events", events: { name: ["a,b"] }, group_by: ["name"], measures: ["count"], limit: 5 }), false);
  assert.equal(queryEditable({ rows: "entries", group_by: ["tpl"], measures: ["count"], limit: 5, exact: true }), false);
  assert.equal(queryEditable(null), false);
  assert.equal(queryEditable({ rows: "entries" }), false);
});

test("formToQuery reports what is wrong with the form, in the words of the Aggregate panel", () => {
  const form = formFromQuery(defaultQuery());
  assert.equal(formToQuery({ ...form, measures: ["", "", "", ""] }).error, S.aggNeedMeasure);
  assert.match(formToQuery({ ...form, limit: "0" }).error, /1 and 1000|1000/);
  assert.equal(formToQuery({ ...form, groupBy: ["bucket:1h", "bucket:5m", ""] }).error, S.aggOneBucket);
  assert.equal("filter" in formToQuery(form).query, false, "no filter member when there is none");
});

test("summarizeQuery and defaultWidget", () => {
  assert.equal(summarizeQuery(defaultQuery()), "Records, by Message type: Count");
  assert.match(summarizeQuery({ rows: "events", group_by: [], measures: ["count"] }), /Operations and events, by nothing: Count/);
  const widget = defaultWidget([{ id: "w1" }]);
  assert.deepEqual(widget, { id: "w2", title: "", query: defaultQuery(), chart: "table", options: {} });
});

// ---- chart fit and options, against the real validator

test("chartFit says why, in the words of strings.js", () => {
  assert.equal(chartFit("table", defaultQuery()), null);
  assert.equal(chartFit("bar", defaultQuery()), null);
  assert.equal(chartFit("bar", { group_by: [], measures: ["count"] }), S.pnFit.bar);
  assert.equal(chartFit("stat", defaultQuery()), S.pnFit.stat);
  assert.equal(chartFit("stat", { group_by: [], measures: ["count"] }), null);
  assert.equal(chartFit("line", defaultQuery()), S.pnFit.line);
  assert.equal(chartFit("line", { group_by: ["tpl", "bucket:1h"], measures: ["count"] }), null);
});

test("deriveOptions keeps a valid earlier choice and drops one the query no longer allows", () => {
  const query = { group_by: ["tpl", "sev", "bucket:1h"], measures: ["count", "p90:duration"] };
  assert.deepEqual(deriveOptions("bar", query), { series: "sev", x: "tpl", y: "count" });
  assert.deepEqual(deriveOptions("bar", query, { x: "sev", y: "p90:duration", stacked: true, top: 5 }), { series: "tpl", stacked: true, top: 5, x: "sev", y: "p90:duration" });
  assert.deepEqual(deriveOptions("bar", query, { x: "gone", y: "gone", series: "gone", top: 99 }), { series: "sev", x: "tpl", y: "count" });
  assert.deepEqual(deriveOptions("line", query), { series: "tpl", x: "bucket:1h", y: "count" });
  assert.deepEqual(deriveOptions("stat", { group_by: [], measures: ["p50:duration"] }), { y: "p50:duration" });
  assert.deepEqual(deriveOptions("table", query, { x: "tpl" }), {});
  assert.deepEqual(deriveOptions("line", { group_by: ["tpl"], measures: ["count"] }), {}, "does not fit: nothing to derive");
});

// The widgets the editor can make are checked by the server's own validator, when a python is at hand.
function pythonValidates(widgets) {
  const python = process.env.DMSLOG_PYTHON || path.join("/home/jeffdmf/Projects/adms-log-analyzer", ".venv/bin/python");
  const candidates = [python, "python3", "python"];
  const script = [
    "import json, sys",
    "sys.path.insert(0, 'src')",
    "from dmslog.store import panels",
    "out = []",
    "for w in json.load(sys.stdin):",
    "    try:",
    "        clean, _ = panels.validate_widget(w)",
    "        out.append({'ok': True, 'options': clean['options']})",
    "    except panels.PanelError as exc:",
    "        out.append({'ok': False, 'code': exc.code})",
    "print(json.dumps(out))",
  ].join("\n");
  for (const exe of candidates) {
    const run = spawnSync(exe, ["-c", script], { cwd: ROOT, input: JSON.stringify(widgets), encoding: "utf8", timeout: 30000 });
    if (run.status === 0) {
      return JSON.parse(run.stdout.trim().split("\n").pop());
    }
  }
  return null;
}

test("a widget the editor builds passes store/panels.py validate_widget; one that does not fit fails with chart_needs", (t) => {
  const queries = [
    ...QUERIES,
    { rows: "entries", group_by: ["bucket:1h"], measures: ["count", "distinct:source"], limit: 100 },
    { rows: "entries", group_by: ["tpl", "bucket:1h"], measures: ["count"], limit: 100 },
    { rows: "events", group_by: ["bucket:1h", "status"], measures: ["count"], limit: 100 },
    { rows: "entries", group_by: ["tpl", "sev", "level"], measures: ["count", "distinct:tid"], limit: 100 },
  ];
  const widgets = [];
  const expected = [];
  for (const query of queries) {
    for (const chart of CHARTS) {
      const reason = chartFit(chart, query);
      widgets.push({ id: "w1", title: "t", query, chart, options: deriveOptions(chart, query) });
      expected.push(reason === null ? "ok" : "chart_needs");
    }
  }
  const results = pythonValidates(widgets);
  if (results === null) {
    t.skip("no python with the dmslog sources at hand");
    return;
  }
  results.forEach((result, i) => {
    const label = JSON.stringify(widgets[i]);
    if (expected[i] === "ok") {
      assert.deepEqual(result, { ok: true, options: widgets[i].options }, label);
    } else {
      assert.deepEqual(result, { ok: false, code: "chart_needs" }, label);
    }
  });
  assert.equal(results.length, queries.length * CHARTS.length);
});

test("draftProblem: form errors first, then limits, then fit, then the sum of the row limits", () => {
  const limits = { ...DEFAULT_LIMITS, widgets: 2, rows: 150 };
  const mk = (id, extra = {}) => ({ id, title: "t", query: defaultQuery(), chart: "table", options: {}, ...extra });
  assert.equal(draftProblem({ widgets: [mk("a")] }, limits), null);
  assert.deepEqual(draftProblem({ widgets: [mk("a")] }, limits, { a: "Fix this." }), { widgetId: "a", text: "Fix this." });
  assert.equal(draftProblem({ widgets: [mk("a"), mk("b"), mk("c")] }, limits).widgetId, null);
  assert.deepEqual(draftProblem({ widgets: [mk("a", { chart: "stat" })] }, limits), { widgetId: "a", text: S.pnFit.stat });
  assert.match(draftProblem({ widgets: [mk("a", { title: "x".repeat(81) })] }, limits).text, /at most 80/);
  assert.match(draftProblem({ widgets: [mk("a"), mk("b")] }, limits).text, /more than 150 rows/);
});

// ---- what a result shows

const RESULT = {
  columns: [{ id: "tpl", role: "group" }, { id: "sev", role: "group" }, { id: "count", role: "measure" }],
  rows: [
    { values: [1, 4, 10], labels: ["Alpha", null, null] },
    { values: [1, 2, 5], labels: ["Alpha", null, null] },
    { values: [2, 4, 7], labels: ["<img src=x onerror=alert(1)>", null, null] },
    { values: [3, 4, null], labels: ["Gamma", null, null] },
  ],
  groups: 4, truncated: false, total: 22, exact: true, method: "exact", notes: [],
};

test("chartSpec (bars): categories by value, one series per second dimension, labels are only text", () => {
  const spec = chartSpec({ chart: "bar", query: { measures: ["count"] }, options: { x: "tpl", y: "count", series: "sev" } }, RESULT);
  assert.equal(spec.kind, "bars");
  assert.deepEqual(spec.categories, ["Alpha", "<img src=x onerror=alert(1)>", "Gamma"]);
  assert.deepEqual(spec.series.map((s) => s.label), [S.severityNames[4], S.severityNames[2]], "largest first");
  assert.equal(spec.series.length, 2);
  assert.deepEqual(spec.series.map((s) => s.values), [[10, 7, null], [5, null, null]]);
  assert.equal(spec.max, 10);
  assert.equal(spec.stacked, false);
});

test("chartSpec: stacked uses the column total, top keeps the largest bars, a null stays a gap", () => {
  const widget = { chart: "bar", query: { measures: ["count"] }, options: { x: "tpl", y: "count", series: "sev", stacked: true } };
  const spec = chartSpec(widget, RESULT);
  assert.equal(spec.stacked, true);
  assert.equal(spec.max, 15);
  const top = chartSpec({ ...widget, options: { ...widget.options, top: 1 } }, RESULT);
  assert.deepEqual(top.categories, ["Alpha"]);
  assert.equal(top.truncated.categories, true);
  const joined = chartSpec({ ...widget, options: { x: "tpl", y: "count" } }, RESULT);
  assert.equal(joined.categories.length, 4, "a group that is not the series makes its own bar");
  assert.deepEqual(joined.series.map((s) => s.values), [[10, 5, 7, null]]);
});

test("chartSpec (lines): seconds on the x axis, sorted, series ranked by size", () => {
  const result = {
    columns: [{ id: "bucket:1h", role: "group" }, { id: "status", role: "group" }, { id: "count", role: "measure" }],
    rows: [
      { values: [7200000, "ok", 3] }, { values: [3600000, "ok", 1] }, { values: [3600000, "fail", 9] }, { values: [null, "ok", 4] },
    ],
  };
  const spec = chartSpec({ chart: "line", query: { measures: ["count"] }, options: { x: "bucket:1h", y: "count", series: "status" } }, result);
  assert.equal(spec.kind, "lines");
  assert.deepEqual(spec.xs, [3600, 7200]);
  assert.deepEqual(spec.series.map((s) => s.label), ["fail", "ok"]);
  assert.deepEqual(spec.series.map((s) => s.values), [[9, null], [1, 3]]);
});

test("chartSpec is null without a row or a matching column; statValue reads the first row", () => {
  assert.equal(chartSpec({ chart: "bar", query: {}, options: { x: "nope", y: "count" } }, RESULT), null);
  assert.equal(chartSpec({ chart: "bar", query: {}, options: { x: "tpl", y: "count" } }, { columns: RESULT.columns, rows: [] }), null);
  assert.equal(chartSpec({ chart: "bar", query: {}, options: {} }, null), null);
  const stat = statValue({ chart: "stat", query: { measures: ["count"] }, options: { y: "count" } }, { columns: [{ id: "count", role: "measure" }], rows: [{ values: [1234] }] });
  assert.equal(stat.text, "1,234");
  assert.equal(stat.label, S.aggCount);
  assert.equal(statValue({ chart: "stat", query: { measures: ["count"] }, options: {} }, { columns: [{ id: "count", role: "measure" }], rows: [{ values: [null] }] }).text, S.aggEmptyValue);
  assert.equal(statValue({ chart: "stat", query: { measures: ["count"] }, options: {} }, { columns: [], rows: [] }), null);
});

test("widgetBody: empty result, table text always present, sample estimates carry their notice", () => {
  const widget = { chart: "bar", query: { measures: ["count"] }, options: { x: "tpl", y: "count" } };
  assert.equal(widgetBody(widget, { columns: RESULT.columns, rows: [], groups: 0 }).empty, true);
  const body = widgetBody(widget, { ...RESULT, method: "sample", exact: false });
  assert.equal(body.empty, false);
  assert.equal(body.view.rows.length, 4);
  assert.ok(body.view.notices.includes(S.aggSampleWarning));
  assert.ok(!body.view.notices.includes(S.aggExactTooSlow), "a panel never asked for an exact answer");
  assert.ok(body.spec !== null);
});

// ---- notices and the delay

test("parseNotice: the fields of the event, clamped; anything else is not a notice", () => {
  assert.deepEqual(parseNotice('{"cycle":3,"seq":9,"to_id":5,"throttled":false,"retry_after_ms":null,"min_interval_ms":5000}'),
    { seq: 9, throttled: false, retryAfterMs: null, minIntervalMs: 5000 });
  assert.deepEqual(parseNotice('{"seq":9,"throttled":true,"retry_after_ms":1500,"min_interval_ms":5000}'),
    { seq: 9, throttled: true, retryAfterMs: 1500, minIntervalMs: 5000 });
  assert.equal(parseNotice('{"throttled":true,"min_interval_ms":8000}').retryAfterMs, 8000, "throttled without a time: wait the interval");
  assert.equal(parseNotice('{"throttled":"true"}').throttled, false, "only a real true counts");
  assert.equal(parseNotice('{"min_interval_ms":1e12}').minIntervalMs, 60000);
  assert.equal(parseNotice('{"min_interval_ms":-5}').minIntervalMs, 500);
  assert.equal(parseNotice("{}").minIntervalMs, 5000);
  for (const bad of ["", "null", "[]", "3", "nope", undefined]) {
    assert.equal(parseNotice(bad), null);
  }
});

test("refreshDelay: after the interval since the last start, never before the server's retry time, never more than a minute", () => {
  assert.equal(refreshDelay({ now: 100, lastStartedAt: null }), 0);
  assert.equal(refreshDelay({ now: 10000, lastStartedAt: 4000 }), 0);
  assert.equal(refreshDelay({ now: 6000, lastStartedAt: 4000 }), 3000);
  assert.equal(refreshDelay({ now: 6000, lastStartedAt: 4000, minIntervalMs: 2000 }), 0);
  assert.equal(refreshDelay({ now: 6000, lastStartedAt: 4000, retryAfterMs: 4500 }), 4500);
  assert.equal(refreshDelay({ now: 6000, lastStartedAt: 4000, retryAfterMs: 1 }), 3000);
  assert.equal(refreshDelay({ now: 0, lastStartedAt: 0, minIntervalMs: 10 ** 9 }), 60000);
  assert.equal(refreshDelay({ now: 0, lastStartedAt: 0, retryAfterMs: NaN }), 5000);
});

test("chartSpec draws the eight largest series and says there are more", () => {
  const rows = Array.from({ length: 10 }, (_, i) => ({ values: [1, i + 1, i + 1], labels: [null, null, null] }));
  const result = { columns: [{ id: "tpl", role: "group" }, { id: "level", role: "group" }, { id: "count", role: "measure" }], rows };
  const spec = chartSpec({ chart: "bar", query: { measures: ["count"] }, options: { x: "tpl", y: "count", series: "level" } }, result);
  assert.equal(spec.series.length, 8);
  assert.equal(spec.truncated.series, true);
  assert.equal(spec.series[0].values[0], 10, "the largest first");
});
