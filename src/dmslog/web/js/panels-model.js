// Model of the "Panels" dock panel (ROADMAP 9.11, ARQUITETURA 10.14): user-built panels, each a named list of widgets
// (a declarative aggregation of 10.8 plus a chart type). No DOM here; panels-panel.js owns the elements.
//
// Two layers:
//   pure helpers   error texts, widget ids, query <-> form, chart fit and options, chart data, notice parsing and the
//                  refresh delay;
//   createPanelsFlow()   the one state machine of the panel. Every asynchronous effect checks, when it answers, that
//                  the thing it was started for is still the current one: the run epoch (case, panel, mode, newest run),
//                  the list sequence and the destroyed flag. A late answer is dropped, never applied.
import {
  ERRORS_ONLY, MAX_GROUP_BY, MAX_LIMIT, MAX_MEASURES, buildRequest, buildView, dimLabel, emptyForm, formatNumber,
  groupText, measureLabel,
} from "./aggregate-model.js";
import { topNodes, wrapNodes } from "./filters.js";
import { createStreamGuard } from "./status-bar-model.js";
import { S, format, pluralKey } from "./strings.js";

export const CHARTS = ["table", "bar", "line", "stat"];
export const DEFAULT_LIMITS = { panels: 20, widgets: 16, rows: 4000, title_chars: 80, name_chars: 80 };
export const DEFAULT_MIN_INTERVAL_MS = 5000; // the server's own default (10.14)
export const MIN_INTERVAL_MS = 500;
export const MAX_DELAY_MS = 60000;
export const MAX_BARS = 50; // categories of a bar chart (the server's `top` goes up to the same number)
export const MAX_SERIES = 8;
export const MAX_TOP = 50;

const BUCKET = "bucket:";
const isBucket = (id) => typeof id === "string" && id.startsWith(BUCKET);
const isObject = (value) => value !== null && typeof value === "object" && !Array.isArray(value);
const clone = (value) => JSON.parse(JSON.stringify(value));

// ---- errors -----------------------------------------------------------------------------------------------

const LIMIT_OF = {
  bad_name: "name_chars", bad_title: "title_chars", bad_widgets: "widgets", too_many_panels: "panels",
  too_many_rows: "rows",
};

// The text for a failed request. Server codes are stable; the text is ours (strings.js), never the server's, and
// carries no query or log text.
export function errorMessage(err, limits = DEFAULT_LIMITS) {
  const code = err && typeof err.code === "string" ? err.code : "";
  if (code === "network" || (err && err.status === 0)) {
    return S.pnErrNetwork;
  }
  const template = Object.prototype.hasOwnProperty.call(S.pnErrors, code) ? S.pnErrors[code] : S.pnErrGeneric;
  const key = LIMIT_OF[code];
  const limit = key ? (limits && Number.isFinite(limits[key]) ? limits[key] : DEFAULT_LIMITS[key]) : 0;
  return format(template, { limit });
}

export function widgetErrorText(error) {
  const code = error && typeof error.code === "string" ? error.code : "";
  const text = Object.prototype.hasOwnProperty.call(S.pnWidgetErrors, code) ? S.pnWidgetErrors[code] : S.pnWidgetErrors.failed;
  if (code === "timeout" && error && Number.isFinite(error.estimated_rows) && error.estimated_rows > 0) {
    return text + " " + format(S.pnWidgetTimeoutRows, { rows: formatNumber(error.estimated_rows) });
  }
  return text;
}

// What the file of panels says about itself: a line per skipped entry and one for an unusable file.
export function problemLines(list) {
  const lines = [];
  if (list.invalidFile) {
    lines.push(S.pnFileInvalid);
  }
  for (const item of list.problems) {
    const why = errorMessage({ code: item.code }, list.limits);
    lines.push(format(S.pnProblem, { n: item.index + 1, why }));
  }
  return lines;
}

// ---- panels -----------------------------------------------------------------------------------------------

export function sortedPanels(panels) {
  return panels.slice().sort((a, b) => {
    const x = a.name.toLowerCase();
    const y = b.name.toLowerCase();
    return x < y ? -1 : x > y ? 1 : (a.id < b.id ? -1 : a.id > b.id ? 1 : 0);
  });
}

function validWidget(raw) {
  return isObject(raw) && typeof raw.id === "string" && isObject(raw.query) && CHARTS.includes(raw.chart);
}

// A panel as the server sent it, or null when it does not have the shape the panel relies on.
export function cleanPanel(raw) {
  if (!isObject(raw) || typeof raw.id !== "string" || !Number.isInteger(raw.rev) || typeof raw.name !== "string"
    || !Array.isArray(raw.widgets) || !raw.widgets.every(validWidget)) {
    return null;
  }
  return {
    id: raw.id,
    rev: raw.rev,
    name: raw.name,
    widgets: raw.widgets.map((w) => ({
      id: w.id, title: typeof w.title === "string" ? w.title : "", query: w.query, chart: w.chart,
      options: isObject(w.options) ? w.options : {},
    })),
  };
}

export function cleanList(data) {
  const raw = isObject(data) ? data : {};
  const limits = { ...DEFAULT_LIMITS };
  if (isObject(raw.limits)) {
    for (const key of Object.keys(DEFAULT_LIMITS)) {
      if (Number.isInteger(raw.limits[key]) && raw.limits[key] > 0) {
        limits[key] = raw.limits[key];
      }
    }
  }
  const problems = (Array.isArray(raw.problems) ? raw.problems : [])
    .filter((p) => isObject(p) && Number.isInteger(p.index) && typeof p.code === "string")
    .map((p) => ({ index: p.index, code: p.code }));
  const panels = (Array.isArray(raw.panels) ? raw.panels : []).map(cleanPanel).filter(Boolean);
  return { panels, problems, invalidFile: raw.invalid === true, limits };
}

export function nameProblem(raw, limits = DEFAULT_LIMITS) {
  const name = String(raw === undefined || raw === null ? "" : raw).trim();
  if (name === "" || name.length > limits.name_chars) {
    return format(S.pnErrors.bad_name, { limit: limits.name_chars });
  }
  return null;
}

// ---- widgets: query <-> form ------------------------------------------------------------------------------

export function nextWidgetId(widgets) {
  const used = new Set(widgets.map((w) => w.id));
  for (let n = 1; ; n += 1) {
    if (!used.has("w" + n)) {
      return "w" + n;
    }
  }
}

function pad(list, n) {
  const out = (list || []).slice(0, n);
  while (out.length < n) {
    out.push("");
  }
  return out;
}

// The form of the Aggregate builder for a stored query. `baseFilter` is the part of the filter the form cannot show
// (kept as it is); "Only errors" is the one condition the form owns.
export function formFromQuery(query) {
  const form = { ...emptyForm(), baseFilter: null };
  form.rows = query.rows === "events" ? "events" : "entries";
  form.groupBy = pad(query.group_by, MAX_GROUP_BY);
  form.measures = pad(query.measures, MAX_MEASURES);
  form.limit = String(query.limit === undefined ? emptyForm().limit : query.limit);
  if (isObject(query.order)) {
    form.orderBy = String(query.order.by);
    form.orderDir = query.order.dir === "asc" ? "asc" : "desc";
  }
  form.lookups = Array.isArray(query.lookups) ? query.lookups.slice() : [];
  const events = isObject(query.events) ? query.events : {};
  form.eventKinds = Array.isArray(events.kind) ? events.kind.join(", ") : "";
  form.eventNames = Array.isArray(events.name) ? events.name.join(", ") : "";
  form.eventStatuses = Array.isArray(events.status) ? events.status.join(", ") : "";
  const nodes = topNodes(query.filter || null);
  const last = nodes.length - 1;
  if (last >= 0 && JSON.stringify(nodes[last]) === JSON.stringify(ERRORS_ONLY)) {
    form.onlyErrors = true;
    form.baseFilter = wrapNodes(nodes.slice(0, last));
  } else {
    form.baseFilter = wrapNodes(nodes);
  }
  return form;
}

// {query} (a body member of POST /api/panels) or {error: text}. A query has no `filter` member when there is none.
export function formToQuery(form) {
  const built = buildRequest(form, form.baseFilter || null);
  if (built.error) {
    return { error: built.error };
  }
  const query = built.request;
  if (query.filter === null || query.filter === undefined) {
    delete query.filter;
  }
  return { query };
}

function canonical(value) {
  if (Array.isArray(value)) {
    return value.map(canonical);
  }
  if (isObject(value)) {
    const out = {};
    for (const key of Object.keys(value).sort()) {
      out[key] = canonical(value[key]);
    }
    return out;
  }
  return value;
}

function normalized(query) {
  const copy = { ...query };
  if ("filter" in copy) {
    const filter = wrapNodes(topNodes(copy.filter || null));
    if (filter === null) {
      delete copy.filter;
    } else {
      copy.filter = filter;
    }
  }
  return JSON.stringify(canonical(copy));
}

// True when the form can show the query without losing anything (it is rebuilt from the form and compared).
export function queryEditable(query) {
  if (!isObject(query) || !Array.isArray(query.group_by) || !Array.isArray(query.measures)) {
    return false;
  }
  try {
    const back = formToQuery(formFromQuery(query));
    return back.query !== undefined && normalized(back.query) === normalized(query);
  } catch (err) {
    return false;
  }
}

export function defaultQuery() {
  return formToQuery({ ...emptyForm(), baseFilter: null }).query;
}

export function defaultWidget(widgets) {
  return { id: nextWidgetId(widgets), title: "", query: defaultQuery(), chart: "table", options: {} };
}

export function summarizeQuery(query) {
  const dims = (query.group_by || []).map(dimLabel);
  const measures = (query.measures || []).map(measureLabel);
  return format(S.pnQuerySummary, {
    rows: query.rows === "events" ? S.aggRowsEvents : S.aggRowsEntries,
    groups: dims.length ? dims.join(", ") : S.pnNoGroups,
    measures: measures.join(", "),
  });
}

export function widgetName(widget, index) {
  return widget.title !== "" ? widget.title : format(S.pnUntitled, { n: index + 1 });
}

// ---- widgets: chart fit and options ------------------------------------------------------------------------

// Why `chart` cannot draw `query` (a text), or null when it can (the same rules as store/panels.py).
export function chartFit(chart, query) {
  const dims = query.group_by || [];
  if (chart === "stat" && dims.length > 0) {
    return S.pnFit.stat;
  }
  if (chart === "bar" && dims.length === 0) {
    return S.pnFit.bar;
  }
  if (chart === "line" && !dims.some(isBucket)) {
    return S.pnFit.line;
  }
  return null;
}

// The chart options for a query: x, y and series are chosen from the query (the previous choice stays while it is
// still valid), so the editor needs no extra controls. Always valid for the server when chartFit() is null.
export function deriveOptions(chart, query, previous = {}) {
  if (chart === "table" || chartFit(chart, query) !== null) {
    return {};
  }
  const dims = query.group_by || [];
  const measures = query.measures || [];
  const before = isObject(previous) ? previous : {};
  const y = measures.includes(before.y) ? before.y : measures[0];
  if (chart === "stat") {
    return { y };
  }
  const xPool = chart === "line" ? dims.filter(isBucket) : dims;
  const x = xPool.includes(before.x) ? before.x : xPool[0];
  const out = { x, y };
  const series = dims.includes(before.series) && before.series !== x ? before.series : dims.find((d) => d !== x);
  if (series !== undefined) {
    out.series = series;
  }
  if (chart === "bar" && typeof before.stacked === "boolean") {
    out.stacked = before.stacked;
  }
  if (Number.isInteger(before.top) && before.top >= 1 && before.top <= MAX_TOP) {
    out.top = before.top;
  }
  return out;
}

// First thing that stops a draft from being saved, as {widgetId, text}, or null.
export function draftProblem(draft, limits, formErrors = {}) {
  for (const widget of draft.widgets) {
    if (formErrors[widget.id]) {
      return { widgetId: widget.id, text: formErrors[widget.id] };
    }
  }
  if (draft.widgets.length > limits.widgets) {
    return { widgetId: null, text: format(S.pnErrors.bad_widgets, { limit: limits.widgets }) };
  }
  for (const widget of draft.widgets) {
    const reason = chartFit(widget.chart, widget.query);
    if (reason !== null) {
      return { widgetId: widget.id, text: reason };
    }
    if (widget.title.length > limits.title_chars) {
      return { widgetId: widget.id, text: format(S.pnErrors.bad_title, { limit: limits.title_chars }) };
    }
  }
  const rows = draft.widgets.reduce((sum, w) => sum + (Number.isInteger(w.query.limit) ? w.query.limit : MAX_LIMIT), 0);
  if (rows > limits.rows) {
    return { widgetId: null, text: format(S.pnErrors.too_many_rows, { limit: limits.rows }) };
  }
  return null;
}

// ---- widgets: what a result shows ----------------------------------------------------------------------------

function columnIndex(columns, role, id) {
  return columns.findIndex((c) => c.role === role && c.id === id);
}

const numberOrNull = (v) => (typeof v === "number" && Number.isFinite(v) ? v : null);

// The big number of a "stat" widget, or null without a row.
export function statValue(widget, result) {
  const columns = Array.isArray(result && result.columns) ? result.columns : [];
  const rows = Array.isArray(result && result.rows) ? result.rows : [];
  const at = columnIndex(columns, "measure", widget.options.y !== undefined ? widget.options.y : (widget.query.measures || [])[0]);
  if (at < 0 || rows.length === 0 || !Array.isArray(rows[0].values)) {
    return null;
  }
  const value = numberOrNull(rows[0].values[at]);
  return { label: measureLabel(columns[at].id), text: value === null ? S.aggEmptyValue : formatNumber(value), value };
}

// Data of a "bar" or "line" widget: {kind, measure, categories|xs, series: [{label, values}], stacked, max, truncated}
// or null when there is nothing to draw. Bars: one bar per combination of the group values that are not the series;
// lines: x is the time bucket, one line per combination of the other groups. Combinations are compared by their
// VALUES (two sources named a.log stay apart); their text is only the label.
export function chartSpec(widget, result) {
  const columns = Array.isArray(result && result.columns) ? result.columns : [];
  const rows = Array.isArray(result && result.rows) ? result.rows : [];
  const o = widget.options;
  const yAt = columnIndex(columns, "measure", o.y !== undefined ? o.y : (widget.query.measures || [])[0]);
  const xAt = columnIndex(columns, "group", o.x);
  if (yAt < 0 || xAt < 0 || rows.length === 0) {
    return null;
  }
  const isLine = widget.chart === "line";
  const groups = columns.map((c, i) => (c.role === "group" ? i : -1)).filter((i) => i >= 0);
  const sAt = o.series !== undefined ? columnIndex(columns, "group", o.series) : -1;
  const across = isLine ? [xAt] : groups.filter((i) => i !== sAt); // what a position on the x axis is made of
  const apart = isLine ? [...(sAt >= 0 ? [sAt] : []), ...groups.filter((i) => i !== xAt && i !== sAt)] : (sAt >= 0 ? [sAt] : []);
  const text = (row, i) => groupText(columns[i].id, row.values[i], (row.labels || [])[i]);
  const measure = measureLabel(columns[yAt].id);
  const keyOf = (row, dims) => JSON.stringify(dims.map((i) => (row.values[i] === undefined ? null : row.values[i])));
  const labelOf = (row, dims) => dims.map((i) => text(row, i)).join(" \u00b7 ");

  const places = new Map(); // key -> {value, label, total}
  const lines = new Map(); // key -> {label, cells: Map(place key -> number), total}
  for (const row of rows) {
    if (!Array.isArray(row.values)) {
      continue;
    }
    const placeKey = keyOf(row, across);
    if (!places.has(placeKey)) {
      places.set(placeKey, { value: row.values[xAt], label: labelOf(row, across), total: 0 });
    }
    const lineKey = keyOf(row, apart);
    if (!lines.has(lineKey)) {
      lines.set(lineKey, { label: apart.length ? labelOf(row, apart) : measure, cells: new Map(), total: 0 });
    }
    const v = numberOrNull(row.values[yAt]);
    const line = lines.get(lineKey);
    line.cells.set(placeKey, v);
    line.total += v || 0;
    places.get(placeKey).total += v || 0;
  }
  let order = Array.from(places.entries());
  let truncatedCategories = false;
  if (isLine) {
    order = order.filter(([, place]) => Number.isFinite(place.value)).sort((a, b) => a[1].value - b[1].value);
  } else {
    const cap = Number.isInteger(o.top) ? Math.min(o.top, MAX_BARS) : MAX_BARS;
    if (order.length > cap) {
      truncatedCategories = true;
      const ranked = order.map((entry, index) => ({ entry, index })).sort((a, b) => b.entry[1].total - a.entry[1].total || a.index - b.index);
      const keep = new Set(ranked.slice(0, cap).map((item) => item.entry[0]));
      order = order.filter(([key]) => keep.has(key));
    }
  }
  const ranked = Array.from(lines.values()).sort((a, b) => b.total - a.total);
  const shown = ranked.slice(0, MAX_SERIES);
  const series = shown.map((line) => ({ label: line.label, values: order.map(([key]) => (line.cells.has(key) ? line.cells.get(key) : null)) }));
  const stacked = !isLine && o.stacked === true && series.length > 1;
  let max = 0;
  order.forEach((_entry, i) => {
    if (stacked) {
      max = Math.max(max, series.reduce((sum, item) => sum + Math.max(0, item.values[i] || 0), 0));
    } else {
      for (const item of series) {
        max = Math.max(max, item.values[i] || 0);
      }
    }
  });
  const spec = {
    kind: isLine ? "lines" : "bars", measure, series, stacked, max,
    truncated: { categories: truncatedCategories, series: ranked.length > shown.length },
  };
  if (isLine) {
    spec.xs = order.map(([, place]) => place.value / 1000);
  } else {
    spec.categories = order.map(([, place]) => place.label);
  }
  return spec;
}

// What a widget shows once it has a result: {view} (table text from the shared builder) plus the widget-specific part.
export function widgetBody(widget, result) {
  const view = buildView(result, false);
  const empty = view.rows.length === 0;
  const body = { view, empty, stat: null, spec: null };
  if (empty) {
    return body;
  }
  if (widget.chart === "stat") {
    body.stat = statValue(widget, result);
  } else if (widget.chart === "bar" || widget.chart === "line") {
    body.spec = chartSpec(widget, result);
  }
  return body;
}

// ---- notices of the server and the refresh policy --------------------------------------------------------------

const clampNumber = (value, low, high) => (Number.isFinite(value) ? Math.min(high, Math.max(low, Math.round(value))) : null);

// The `panels` event of the progress stream (10.14): {seq, throttled, retryAfterMs, minIntervalMs}, or null when the
// text is not a notice. Only a hint that something may have changed.
export function parseNotice(text) {
  let data;
  try {
    data = JSON.parse(text);
  } catch (err) {
    return null;
  }
  if (!isObject(data)) {
    return null;
  }
  const minIntervalMs = clampNumber(data.min_interval_ms, MIN_INTERVAL_MS, MAX_DELAY_MS) ?? DEFAULT_MIN_INTERVAL_MS;
  const throttled = data.throttled === true;
  const retry = clampNumber(data.retry_after_ms, 0, MAX_DELAY_MS);
  return {
    seq: Number.isFinite(data.seq) ? data.seq : null,
    throttled,
    retryAfterMs: throttled ? (retry ?? minIntervalMs) : null,
    minIntervalMs,
  };
}

// Milliseconds to wait before the next automatic run: never sooner than `minIntervalMs` after the start of the
// last run, and not before the server's `retryAfterMs`. 0 means now.
export function refreshDelay({ now, lastStartedAt, minIntervalMs = DEFAULT_MIN_INTERVAL_MS, retryAfterMs = null }) {
  const floor = lastStartedAt === null || lastStartedAt === undefined ? 0 : Math.max(0, lastStartedAt + minIntervalMs - now);
  const retry = Number.isFinite(retryAfterMs) ? Math.max(0, retryAfterMs) : 0;
  return Math.min(MAX_DELAY_MS, Math.max(floor, retry));
}

// The progress stream of a case, listening to `panels` events. The browser reconnects by itself after a dropped
// stream (onOpen fires again); after three failures in a row it stops (onStopped). Returns {close()}.
export function connectPanelStream({ EventSourceImpl, url, onNotice, onOpen, onStopped }) {
  const Impl = EventSourceImpl === undefined && typeof EventSource === "function" ? EventSource : EventSourceImpl;
  if (!Impl) {
    return { close() {} };
  }
  const guard = createStreamGuard();
  let source = null;
  try {
    source = new Impl(url);
    const mine = source;
    source.addEventListener("panels", (event) => {
      if (source === mine) {
        onNotice(event.data);
      }
    });
    source.addEventListener("open", () => {
      if (source === mine) {
        guard.opened();
        onOpen();
      }
    });
    source.addEventListener("error", () => {
      if (source === mine && (mine.readyState === 2 ? guard.stop() : guard.failed())) {
        mine.close();
        source = null;
        onStopped();
      }
    });
  } catch (err) {
    source = null;
  }
  return {
    close() {
      if (source) {
        source.close();
        source = null;
      }
    },
  };
}

// ---- the flow ---------------------------------------------------------------------------------------------------

const isAbort = (err) => Boolean(err) && err.name === "AbortError";

function emptyRun() {
  return { status: "idle", panelId: null, caseId: null, results: {}, complete: true, elapsedMs: 0, finishedAt: null, error: "", auto: false, version: 0, rv: 0 };
}

function parseRun(data, panel) {
  if (!isObject(data) || data.panel !== panel.id || !Array.isArray(data.results)) {
    return null;
  }
  const known = new Set(panel.widgets.map((w) => w.id));
  const results = {};
  for (const item of data.results) {
    if (isObject(item) && known.has(item.id) && ["ok", "error", "skipped"].includes(item.status)) {
      results[item.id] = {
        status: item.status,
        result: isObject(item.result) ? item.result : null,
        error: isObject(item.error) ? item.error : null,
      };
    }
  }
  return {
    rev: data.rev,
    results,
    complete: data.complete !== false,
    elapsedMs: Number.isFinite(data.elapsed_ms) ? data.elapsed_ms : 0,
  };
}

// deps: api (api.panels.*, api.progressUrl, api.keyTypes is not used here), getCase(), onState(state),
//   now(), setTimer(fn, ms), clearTimer(id), connect({url, onNotice, onOpen, onStopped}) -> {close()}
// `state` is one object mutated in place; onState runs after every change. The DOM reads it and never writes it.
export function createPanelsFlow({
  api, getCase, onState = () => {}, now = () => Date.now(), setTimer = (fn, ms) => setTimeout(fn, ms),
  clearTimer = (id) => clearTimeout(id), connect = connectPanelStream,
}) {
  const state = {
    list: { status: "idle", panels: [], problems: [], invalidFile: false, limits: { ...DEFAULT_LIMITS }, error: "" },
    selectedId: null,
    mode: "view", // "view" | "edit"
    draft: null, // {name, widgets, baseRev, recreate}
    editingWidget: null,
    formErrors: {},
    naming: null, // {kind: "create" | "rename", id} while the name field is open
    confirming: false, // the delete confirmation is open
    busy: null, // "create" | "rename" | "save" | "delete" while a change is being written
    run: emptyRun(),
    streamStopped: false,
    notice: { seq: 0, kind: "info", text: "" }, // the one live region
    focus: { seq: 0, target: "" }, // where the DOM puts the focus after the next paint
    shown: false,
  };
  let destroyed = false;
  let runEpoch = 0; // grows whenever what a run belongs to changes (case, panel, mode, a newer run)
  let runController = null;
  let listSeq = 0;
  let listController = null;
  let listLoading = false;
  let lastStartedAt = null;
  let minIntervalMs = DEFAULT_MIN_INTERVAL_MS;
  let pending = null; // {due, timer}: the one scheduled automatic run
  let dirty = null; // a notice arrived during a run: {retryAfterMs}
  let stream = null;
  let streamKey = null;
  let streamSeq = 0;
  let lastAutoError = "";

  const emit = () => {
    if (!destroyed) {
      onState(state);
    }
  };
  const say = (kind, text) => {
    state.notice = { seq: state.notice.seq + 1, kind, text };
  };
  const focusOn = (target) => {
    state.focus = { seq: state.focus.seq + 1, target };
  };
  const selectedPanel = () => state.list.panels.find((p) => p.id === state.selectedId) || null;
  const limits = () => state.list.limits;

  // ---- run bookkeeping

  function clearPending() {
    if (pending !== null) {
      clearTimer(pending.timer);
      pending = null;
    }
    dirty = null;
  }

  // Whatever was running or scheduled belongs to the past: the answer of a started run is dropped.
  function cancelRun() {
    runEpoch += 1;
    clearPending();
    if (runController !== null) {
      runController.abort();
      runController = null;
    }
    if (state.run.status === "running") {
      state.run.status = Object.keys(state.run.results).length > 0 ? "ready" : "idle";
    }
  }

  function invalidateRun() {
    cancelRun();
    state.run = { ...emptyRun(), version: state.run.version + 1, rv: state.run.rv + 1 };
  }

  const canAuto = () => state.shown && !destroyed && state.mode === "view" && state.selectedId !== null && getCase() !== null;

  // ---- the stream

  function closeStream() {
    streamSeq += 1;
    if (stream !== null) {
      stream.close();
      stream = null;
    }
    streamKey = null;
  }

  function syncStream() {
    const caseId = canAuto() ? getCase() : null;
    if (caseId === null || (stream !== null && streamKey !== caseId)) {
      closeStream();
    }
    if (caseId === null || stream !== null || state.streamStopped) {
      return;
    }
    streamSeq += 1;
    const token = streamSeq;
    let opened = false;
    streamKey = caseId;
    stream = connect({
      url: api.progressUrl(caseId),
      onNotice: (text) => {
        if (token !== streamSeq || destroyed || getCase() !== caseId) {
          return; // another case, or a stream that was closed
        }
        const notice = parseNotice(text);
        if (notice !== null) {
          minIntervalMs = notice.minIntervalMs;
          requestRefresh({ retryAfterMs: notice.retryAfterMs });
        }
      },
      onOpen: () => {
        if (token !== streamSeq || destroyed) {
          return;
        }
        if (opened) {
          requestRefresh({ retryAfterMs: null }); // reconnected: whatever was missed is covered by running again
        }
        opened = true;
      },
      onStopped: () => {
        if (token !== streamSeq || destroyed) {
          return;
        }
        stream = null;
        streamKey = null;
        state.streamStopped = true;
        say("error", S.pnLiveStopped);
        emit();
      },
    });
  }

  // ---- automatic refresh

  function requestRefresh({ retryAfterMs = null } = {}) {
    if (!canAuto()) {
      return;
    }
    if (state.run.status === "running") {
      dirty = { retryAfterMs };
      return;
    }
    const delay = refreshDelay({ now: now(), lastStartedAt, minIntervalMs, retryAfterMs });
    if (delay <= 0) {
      startRun({ auto: true });
      return;
    }
    const due = now() + delay;
    if (pending !== null && pending.due <= due) {
      return; // an earlier run is already scheduled and will cover this notice
    }
    if (pending !== null) {
      clearTimer(pending.timer);
    }
    const token = runEpoch;
    pending = {
      due,
      timer: setTimer(() => {
        pending = null;
        if (token === runEpoch && canAuto()) {
          startRun({ auto: true });
        }
      }, delay),
    };
  }

  // ---- running

  async function startRun({ auto = false, retried = false } = {}) {
    const panel = selectedPanel();
    if (panel === null || state.mode !== "view") {
      return;
    }
    const caseId = getCase();
    if (caseId === null) {
      invalidateRun();
      say("error", S.pnNoCase);
      emit();
      return;
    }
    cancelRun();
    if (panel.widgets.length === 0) {
      state.run = { ...emptyRun(), status: "ready", panelId: panel.id, caseId, finishedAt: now(), version: state.run.version + 1, rv: state.run.rv + 1 };
      emit();
      return; // nothing to ask the server
    }
    const mine = runEpoch;
    const controller = new AbortController();
    runController = controller;
    lastStartedAt = now();
    const same = state.run.panelId === panel.id && state.run.caseId === caseId;
    const keeps = same && Object.keys(state.run.results).length > 0; // the cards on screen stay while the update runs
    state.run = {
      ...(same ? state.run : emptyRun()), status: "running", panelId: panel.id, caseId, auto, error: "",
      version: state.run.version + 1, rv: state.run.rv + (keeps ? 0 : 1),
    };
    emit();
    const live = () => mine === runEpoch && !destroyed && getCase() === caseId; // every change of panel or mode bumps the epoch
    let data;
    try {
      data = await api.panels.run(caseId, panel.id, undefined, controller.signal);
    } catch (err) {
      if (!live() || isAbort(err)) {
        return;
      }
      await runFailed(err, auto);
      return;
    }
    if (!live()) {
      return;
    }
    runController = null;
    const parsed = parseRun(data, panel);
    if (parsed === null || parsed.rev !== panel.rev) {
      if (parsed !== null && !retried) {
        // The panel was changed by someone else: read the list again and run the new revision, once.
        const loaded = await loadList({ run: false, keepRun: true });
        if (loaded && live() && canAuto()) {
          await startRun({ auto, retried: true });
        }
        return;
      }
      const text = parsed === null ? S.pnErrGeneric : S.pnErrors.stale_panel;
      state.run = { ...state.run, status: "error", error: text, version: state.run.version + 1 };
      say("error", text);
      emit();
      return;
    }
    state.run = {
      ...state.run, status: "ready", results: parsed.results, complete: parsed.complete, elapsedMs: parsed.elapsedMs,
      finishedAt: now(), error: "", version: state.run.version + 1, rv: state.run.rv + 1,
    };
    lastAutoError = "";
    if (!auto) {
      const ran = panel.widgets.length;
      say("info", parsed.complete
        ? format(S[pluralKey("pnRan", ran)], { count: ran, seconds: (parsed.elapsedMs / 1000).toFixed(1) })
        : S.pnRanPartial);
    }
    emit();
    if (dirty !== null) {
      const next = dirty;
      dirty = null;
      requestRefresh(next);
    }
  }

  async function runFailed(err, auto) {
    runController = null;
    const text = errorMessage(err, limits());
    state.run = { ...state.run, status: "error", error: text, version: state.run.version + 1 };
    if (!auto || text !== lastAutoError) {
      say("error", text);
    }
    lastAutoError = auto ? text : "";
    emit();
    if (err && err.code === "not_found") {
      await loadList({ run: false });
    }
  }

  // ---- the list

  function applyList(data, options) {
    applyClean(cleanList(data), options);
  }

  function applyClean(next, { keepRun = false } = {}) {
    const before = selectedPanel();
    state.list = { ...state.list, ...next, status: "ready", error: "" };
    const after = selectedPanel();
    if (state.selectedId !== null && after === null) {
      if (state.mode === "edit" && state.draft !== null) {
        state.draft.recreate = true; // the panel is gone: Save writes the draft as a new panel
        state.draft.baseRev = null;
        say("error", S.pnErrors.deleted_while_editing);
      } else {
        state.selectedId = null;
        invalidateRun();
      }
    } else if (before !== null && after !== null && before.rev !== after.rev && !keepRun && state.mode === "view") {
      invalidateRun();
    }
    if (state.selectedId === null && state.mode === "view") {
      const first = sortedPanels(state.list.panels)[0];
      state.selectedId = first ? first.id : null;
    }
  }

  async function loadList({ run = true, keepRun = false } = {}) {
    listSeq += 1;
    const mine = listSeq;
    if (listController !== null) {
      listController.abort();
    }
    const controller = new AbortController();
    listController = controller;
    listLoading = true;
    if (state.list.status !== "ready") {
      state.list.status = "loading";
    }
    emit();
    let data;
    try {
      data = await api.panels.list(controller.signal);
    } catch (err) {
      if (mine !== listSeq || destroyed || isAbort(err)) {
        return false;
      }
      listLoading = false;
      listController = null;
      state.list.status = state.list.panels.length > 0 || state.list.status === "ready" ? "ready" : "error";
      state.list.error = errorMessage(err, limits());
      say("error", state.list.error);
      emit();
      return false;
    }
    if (mine !== listSeq || destroyed) {
      return false;
    }
    listLoading = false;
    listController = null;
    applyList(data, { keepRun });
    emit();
    if (run && state.shown) {
      afterListChange();
    }
    return true;
  }

  function afterListChange() {
    if (state.list.status !== "ready" && !listLoading) {
      loadList({ run: false }); // a write was answered before the list ever was: read the rest now
    }
    syncStream();
    if (canAuto() && state.run.status !== "running") {
      startRun({ auto: false });
    }
  }

  // ---- showing and hiding

  function ensure() {
    if (!state.shown || destroyed) {
      return;
    }
    if (state.list.status !== "ready") {
      if (!listLoading) {
        loadList({ run: true });
      }
      return;
    }
    syncStream();
    if (canAuto() && state.run.status !== "running") {
      startRun({ auto: false });
    }
  }

  const flow = {
    state,

    // The tab is open and in front (true), or closed / behind another tab (false).
    setShown(on) {
      const next = Boolean(on);
      if (next === state.shown) {
        if (next) {
          ensure();
        }
        return;
      }
      state.shown = next;
      if (next) {
        ensure();
      } else {
        cancelRun();
        closeStream();
        state.streamStopped = false;
      }
      emit();
    },

    // A new case: the results belong to the old one. The panels themselves are the user's, not the case's.
    caseChanged() {
      invalidateRun();
      closeStream();
      state.streamStopped = false;
      lastAutoError = "";
      emit();
    },

    loadList: () => loadList({ run: true }),

    // "Refresh": read the list again and run the open panel.
    reloadAll() {
      return loadList({ run: true });
    },

    // Manual run: always runs (newest wins), and gives live updates another chance.
    run() {
      if (state.selectedId === null || state.mode !== "view" || destroyed) {
        return Promise.resolve();
      }
      if (state.streamStopped) {
        state.streamStopped = false;
      }
      syncStream();
      return startRun({ auto: false });
    },

    select(id) {
      if (state.busy !== null || state.mode === "edit" || id === state.selectedId || !state.list.panels.some((p) => p.id === id)) {
        return false;
      }
      state.naming = null;
      state.confirming = false;
      state.selectedId = id;
      invalidateRun();
      emit();
      if (state.shown) {
        afterListChange(); // the stream belongs to the case, not to the panel: it stays
      }
      return true;
    },

    // ---- names

    startNaming(kind) {
      if (state.busy !== null || state.mode !== "view") {
        return false;
      }
      if (kind === "create") {
        if (state.list.panels.length >= limits().panels) {
          say("error", format(S.pnErrors.too_many_panels, { limit: limits().panels }));
          emit();
          return false;
        }
        state.naming = { kind, id: null };
      } else if (kind === "rename" && state.selectedId !== null) {
        state.naming = { kind, id: state.selectedId };
      } else {
        return false;
      }
      state.confirming = false;
      focusOn("name");
      emit();
      return true;
    },

    cancelNaming() {
      if (state.naming === null || state.busy !== null) {
        return;
      }
      state.naming = null;
      focusOn(state.selectedId === null ? "select" : "name-opener");
      emit();
    },

    async submitName(raw) {
      const naming = state.naming;
      if (naming === null || state.busy !== null || state.mode !== "view") {
        return false;
      }
      const problem = nameProblem(raw, limits());
      if (problem !== null) {
        say("error", problem);
        emit();
        return false;
      }
      const name = String(raw).trim();
      const panel = naming.kind === "rename" ? selectedPanel() : null;
      if (naming.kind === "rename" && (panel === null || panel.id !== naming.id)) {
        state.naming = null;
        emit();
        return false;
      }
      state.busy = naming.kind;
      emit();
      try {
        const saved = cleanPanel(naming.kind === "create"
          ? await api.panels.create({ name, widgets: [] })
          : await api.panels.update(panel.id, { name, widgets: clone(panel.widgets) }, panel.rev));
        if (destroyed) {
          return false;
        }
        if (saved === null) {
          throw new Error("bad_answer"); // an unreadable answer: the generic text
        }
        upsert(saved);
        state.naming = null;
        state.selectedId = saved.id;
        invalidateRun();
        say("info", format(naming.kind === "create" ? S.pnCreated : S.pnRenamed, { name: saved.name }));
        focusOn("heading");
        state.busy = null;
        emit();
        if (state.shown) {
          afterListChange();
        }
        return true;
      } catch (err) {
        if (destroyed) {
          return false;
        }
        say("error", errorMessage(err, limits()));
        state.busy = null;
        emit();
        if (err && ["rev_conflict", "not_found", "file_invalid"].includes(err.code)) {
          state.naming = null;
          await loadList({ run: true });
        }
        return false;
      }
    },

    // ---- delete

    askDelete() {
      if (state.busy !== null || state.mode !== "view" || selectedPanel() === null) {
        return false;
      }
      state.naming = null;
      state.confirming = true;
      focusOn("confirm");
      emit();
      return true;
    },

    cancelDelete() {
      if (!state.confirming || state.busy !== null) {
        return;
      }
      state.confirming = false;
      focusOn("delete");
      emit();
    },

    // Deletes the revision the user saw: the list is read again first and a panel that changed meanwhile is not
    // deleted (DELETE has no `if_rev`, so this is the narrowest guard the API allows).
    async confirmDelete() {
      const panel = selectedPanel();
      if (!state.confirming || state.busy !== null || state.mode !== "view" || panel === null) {
        return false;
      }
      state.busy = "delete";
      emit();
      try {
        const fresh = cleanList(await api.panels.list());
        if (destroyed) {
          return false;
        }
        const current = fresh.panels.find((p) => p.id === panel.id) || null;
        if (current !== null && current.rev !== panel.rev) {
          state.confirming = false;
          applyClean(fresh);
          say("error", S.pnErrors.changed_before_delete);
          state.busy = null;
          emit();
          afterListChange();
          return false;
        }
        if (current !== null) {
          try {
            await api.panels.remove(panel.id);
          } catch (err) {
            if (!err || err.code !== "not_found") {
              throw err;
            }
          }
        }
        if (destroyed) {
          return false;
        }
        state.confirming = false;
        supersedeList();
        state.list.panels = state.list.panels.filter((p) => p.id !== panel.id);
        if (state.selectedId === panel.id) {
          state.selectedId = null;
          invalidateRun();
          const first = sortedPanels(state.list.panels)[0];
          state.selectedId = first ? first.id : null;
        }
        say("info", format(S.pnDeleted, { name: panel.name }));
        focusOn("select");
        state.busy = null;
        emit();
        if (state.shown) {
          afterListChange();
        }
        return true;
      } catch (err) {
        if (destroyed) {
          return false;
        }
        state.confirming = false;
        say("error", errorMessage(err, limits()));
        state.busy = null;
        emit();
        if (err && ["file_invalid", "busy"].includes(err.code)) {
          await loadList({ run: false });
        }
        return false;
      }
    },

    // ---- editing the widgets

    edit() {
      const panel = selectedPanel();
      if (panel === null || state.mode !== "view" || state.busy !== null) {
        return false;
      }
      cancelRun();
      closeStream();
      state.naming = null;
      state.confirming = false;
      state.mode = "edit";
      state.draft = { name: panel.name, widgets: clone(panel.widgets), baseRev: panel.rev, recreate: false };
      state.editingWidget = null;
      state.formErrors = {};
      focusOn("editor");
      emit();
      return true;
    },

    cancelEdit() {
      if (state.mode !== "edit" || state.busy !== null) {
        return false;
      }
      leaveEdit();
      emit();
      if (state.shown) {
        afterListChange();
      }
      return true;
    },

    addWidget() {
      const draft = state.draft;
      if (state.mode !== "edit" || draft === null || state.busy !== null) {
        return null;
      }
      if (draft.widgets.length >= limits().widgets) {
        say("error", format(S.pnErrors.bad_widgets, { limit: limits().widgets }));
        emit();
        return null;
      }
      const widget = defaultWidget(draft.widgets);
      draft.widgets.push(widget);
      setEditing(widget.id);
      focusOn("widget-title");
      emit();
      return widget.id;
    },

    removeWidget(id) {
      const draft = state.draft;
      if (state.mode !== "edit" || draft === null || state.busy !== null) {
        return false;
      }
      const at = draft.widgets.findIndex((w) => w.id === id);
      if (at < 0) {
        return false;
      }
      draft.widgets.splice(at, 1);
      delete state.formErrors[id];
      if (state.editingWidget === id) {
        state.editingWidget = null;
      }
      const next = draft.widgets[Math.min(at, draft.widgets.length - 1)];
      focusOn(next ? "edit:" + next.id : "add");
      emit();
      return true;
    },

    moveWidget(id, dir) {
      const draft = state.draft;
      if (state.mode !== "edit" || draft === null || state.busy !== null) {
        return false;
      }
      const at = draft.widgets.findIndex((w) => w.id === id);
      const to = at + (dir === "up" ? -1 : 1);
      if (at < 0 || to < 0 || to >= draft.widgets.length) {
        return false;
      }
      const [item] = draft.widgets.splice(at, 1);
      draft.widgets.splice(to, 0, item);
      focusOn((dir === "up" ? "up:" : "down:") + id);
      emit();
      return true;
    },

    setEditingWidget(id) {
      if (state.mode !== "edit" || state.draft === null) {
        return false;
      }
      setEditing(id);
      if (state.editingWidget !== null) {
        focusOn("widget-title");
      }
      emit();
      return true;
    },

    // patch: {title?, chart?, query?}. The options follow the chart and the query.
    updateWidget(id, patch) {
      const draft = state.draft;
      if (state.mode !== "edit" || draft === null || state.busy !== null) {
        return false;
      }
      const widget = draft.widgets.find((w) => w.id === id);
      if (!widget) {
        return false;
      }
      if (typeof patch.title === "string") {
        widget.title = patch.title;
      }
      const chartChanged = typeof patch.chart === "string" && CHARTS.includes(patch.chart) && patch.chart !== widget.chart;
      if (chartChanged) {
        widget.chart = patch.chart;
      }
      if (isObject(patch.query)) {
        widget.query = patch.query;
      }
      if (chartChanged || isObject(patch.query)) {
        widget.options = deriveOptions(widget.chart, widget.query, chartChanged ? {} : widget.options);
      }
      emit();
      return true;
    },

    setFormError(id, text) {
      if (text) {
        state.formErrors[id] = String(text);
      } else {
        delete state.formErrors[id];
      }
      emit();
    },

    async saveEdit() {
      const draft = state.draft;
      if (state.mode !== "edit" || draft === null || state.busy !== null) {
        return false;
      }
      const problem = draftProblem(draft, limits(), state.formErrors);
      if (problem !== null) {
        say("error", problem.text);
        if (problem.widgetId !== null) {
          setEditing(problem.widgetId);
        }
        focusOn(problem.widgetId !== null ? "widget-title" : "editor");
        emit();
        return false;
      }
      const panelId = state.selectedId;
      const body = { name: draft.name, widgets: clone(draft.widgets) };
      const { baseRev, recreate } = draft;
      state.busy = "save";
      emit();
      try {
        const saved = cleanPanel(recreate ? await api.panels.create(body) : await api.panels.update(panelId, body, baseRev));
        if (destroyed) {
          return false;
        }
        if (saved === null) {
          throw new Error("bad_answer"); // an unreadable answer: the generic text
        }
        upsert(saved);
        state.selectedId = saved.id;
        leaveEdit();
        invalidateRun();
        say("info", format(S.pnSaved, { name: saved.name }));
        focusOn("heading");
        state.busy = null;
        emit();
        if (state.shown) {
          afterListChange();
        }
        return true;
      } catch (err) {
        if (destroyed) {
          return false;
        }
        state.busy = null;
        if (err && err.code === "rev_conflict") {
          // Someone else saved first. The edit stays; the revision to write over is the one that is there now.
          say("error", S.pnErrors.rev_conflict);
          emit();
          await loadList({ run: false });
          const latest = state.list.panels.find((p) => p.id === panelId);
          if (state.draft === draft && latest) {
            draft.baseRev = latest.rev;
          }
          emit();
          return false;
        }
        if (err && err.code === "not_found") {
          draft.recreate = true;
          draft.baseRev = null;
          say("error", S.pnErrors.deleted_while_editing);
          emit();
          await loadList({ run: false });
          return false;
        }
        say("error", errorMessage(err, limits()));
        emit();
        return false;
      }
    },

    destroy() {
      destroyed = true;
      cancelRun();
      closeStream();
      listSeq += 1;
      if (listController !== null) {
        listController.abort();
        listController = null;
      }
    },
  };

  // The widget whose form is open. Leaving a form drops its error: the draft keeps the last valid query, which is
  // what the form shows when it opens again.
  function setEditing(id) {
    const next = id !== null && state.draft !== null && state.draft.widgets.some((w) => w.id === id) ? id : null;
    if (state.editingWidget !== null && state.editingWidget !== next) {
      delete state.formErrors[state.editingWidget];
    }
    state.editingWidget = next;
  }

  function leaveEdit() {
    state.mode = "view";
    state.draft = null;
    state.editingWidget = null;
    state.formErrors = {};
    focusOn("heading");
  }

  // A write answered: that is newer than any list still on its way, which is dropped (and read again if there is none yet).
  function supersedeList() {
    listSeq += 1;
    if (listController !== null) {
      listController.abort();
      listController = null;
    }
    listLoading = false;
    if (state.list.status !== "ready") {
      state.list.status = "idle";
    }
  }

  function upsert(panel) {
    supersedeList();
    const at = state.list.panels.findIndex((p) => p.id === panel.id);
    if (at >= 0) {
      state.list.panels[at] = panel;
    } else {
      state.list.panels.push(panel);
    }
  }

  return flow;
}
