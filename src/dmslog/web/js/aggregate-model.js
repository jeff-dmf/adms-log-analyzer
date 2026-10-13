// Pure model of the "Aggregate" panel (no DOM). Server side: POST /api/entries/aggregate
// (store/aggregate.py, ARQUITETURA 10.6). The panel builds a declarative request from choices of
// fixed lists; values in the answer come from untrusted logs and are only turned into plain strings.
import { andFilters } from "./filters.js";
import { S, format } from "./strings.js";
import { formatTs } from "./time.js";

export const BUCKETS = ["1m", "5m", "15m", "1h", "1d"];
export const FUNCTIONS = ["min", "max", "avg", "sum", "p50", "p90", "p95", "p99"];
export const ENTRY_DIMS = ["tpl", "source", "collection", "family", "sev", "level", "tid", "pid"];
export const EVENT_DIMS = ["name", "kind", "status", "family", "collection", "sev", "tid", "key"];
export const EVENT_VALUES = ["duration", "entries"];
export const MAX_GROUP_BY = 3;
export const MAX_MEASURES = 4; // the server takes 8; four keep the form short
export const DEFAULT_LIMIT = 100;
export const MAX_LIMIT = 1000;
export const MAX_SERIES = 8; // lines of the time chart (the largest ones)
export const MAX_BARS = 50;
export const ERRORS_ONLY = { sev: { le: 2 } }; // "Only errors": critical and error records
// lookup -> the rows and the dimension it needs (knowledge pack columns; the pack is optional)
export const LOOKUPS = {
  "error.reason": { rows: "entries", needs: "key.errcode" },
  "error.severity": { rows: "entries", needs: "key.errcode" },
  "operation.direction": { rows: "events", needs: "name" },
  "operation.service": { rows: "events", needs: "name" },
};
const FIELD_NAME = /^[A-Za-z_][A-Za-z0-9_]{0,63}$/;
const KEY_TYPE = /^[A-Za-z0-9_.-]{1,64}$/;
const LIST_WORD_MAX = 200;

// Ready-made questions (strings.js titles). `form` is merged over the empty form.
export const EXAMPLES = [
  {
    id: "op_duration",
    title: () => S.aggExOpDuration,
    form: {
      rows: "events", eventKinds: "operation", groupBy: ["name"],
      measures: ["count", "p50:duration", "p90:duration", "p99:duration"], orderBy: "p90:duration",
    },
  },
  {
    id: "errors_per_hour",
    title: () => S.aggExErrorsPerHour,
    form: { rows: "entries", onlyErrors: true, groupBy: ["tpl", "bucket:1h"], measures: ["count"], limit: "1000" },
  },
  {
    id: "by_collection",
    title: () => S.aggExByCollection,
    form: { rows: "entries", groupBy: ["collection", "family"], measures: ["count", "distinct:source"] },
  },
  {
    id: "ops_per_hour",
    title: () => S.aggExOpsPerHour,
    form: { rows: "events", eventKinds: "operation", groupBy: ["bucket:1h", "status"], measures: ["count"], limit: "1000" },
  },
  {
    id: "failed_ops",
    title: () => S.aggExFailedOps,
    form: {
      rows: "events", eventStatuses: "fail, timeout, business_error", groupBy: ["family", "name"],
      measures: ["count", "distinct:key"],
    },
  },
  {
    id: "error_codes",
    title: () => S.aggExErrorCodes,
    form: { rows: "entries", groupBy: ["key.errcode"], measures: ["count", "distinct:source"], lookups: ["error.reason"] },
  },
];

export function emptyForm() {
  return {
    rows: "entries", groupBy: ["tpl", "", ""], measures: ["count", "", "", ""], orderBy: "", orderDir: "desc",
    limit: String(DEFAULT_LIMIT), lookups: [], eventKinds: "", eventNames: "", eventStatuses: "", onlyErrors: false,
  };
}

// The form of an example: lists padded to the number of selects of the panel.
export function exampleForm(id) {
  const example = EXAMPLES.find((e) => e.id === id);
  if (!example) {
    return null;
  }
  const form = { ...emptyForm(), ...structuredClone(example.form) };
  form.groupBy = pad(form.groupBy, MAX_GROUP_BY);
  form.measures = pad(form.measures, MAX_MEASURES);
  return form;
}

function pad(list, n) {
  const out = (list || []).slice(0, n);
  while (out.length < n) {
    out.push("");
  }
  return out;
}

// -- labels ---------------------------------------------------------------------------------------

export function dimLabel(id) {
  if (typeof id !== "string") {
    return "";
  }
  if (id.startsWith("bucket:")) {
    return format(S.aggBucketLabel, { width: S.aggBucketNames[id.slice(7)] || id.slice(7) });
  }
  if (id.startsWith("key.")) {
    return format(S.aggKeyLabel, { type: id.slice(4) });
  }
  if (id.startsWith("fields.")) {
    return format(S.aggFieldLabel, { name: id.slice(7) });
  }
  return S.aggDimNames[id] || id;
}

function valueLabel(value) {
  return S.aggValueNames[value] || (value && value.startsWith("fields.") ? value.slice(7) : String(value));
}

export function measureLabel(id) {
  if (id === "count") {
    return S.aggCount;
  }
  const [fn, ...rest] = String(id).split(":");
  const arg = rest.join(":");
  if (fn === "distinct") {
    return format(S.aggDistinctOf, { what: dimLabel(arg) });
  }
  return format(S.aggFnOf, { fn: S.aggFnNames[fn] || fn, what: valueLabel(arg) });
}

export function columnLabel(column) {
  if (column.role === "group") {
    return dimLabel(column.id);
  }
  if (column.role === "lookup") {
    return S.aggLookupNames[column.id] || column.id;
  }
  return measureLabel(column.id);
}

// -- choices -------------------------------------------------------------------------------------

function validKeys(keyTypes) {
  return Array.from(new Set(keyTypes || [])).filter((t) => typeof t === "string" && KEY_TYPE.test(t)).sort();
}

function validFields(fieldNames) {
  return Array.from(new Set(fieldNames || [])).filter((n) => typeof n === "string" && FIELD_NAME.test(n)).sort();
}

// Group-by choices: {value, label, group} with group "column" | "time" | "key" | "field".
export function dimOptions(rows, keyTypes, fieldNames) {
  const columns = rows === "events" ? EVENT_DIMS : ENTRY_DIMS;
  const options = columns.map((d) => ({ value: d, label: dimLabel(d), group: "column" }));
  for (const b of BUCKETS) {
    options.push({ value: "bucket:" + b, label: dimLabel("bucket:" + b), group: "time" });
  }
  if (rows !== "events") {
    for (const t of validKeys(keyTypes)) {
      options.push({ value: "key." + t, label: dimLabel("key." + t), group: "key" });
    }
    for (const n of validFields(fieldNames)) {
      options.push({ value: "fields." + n, label: dimLabel("fields." + n), group: "field" });
    }
  }
  return options;
}

// Measure choices: {value, label, group} with group "count" | "distinct" | "number".
export function measureOptions(rows, keyTypes, fieldNames) {
  const options = [{ value: "count", label: measureLabel("count"), group: "count" }];
  for (const d of dimOptions(rows, keyTypes, fieldNames)) {
    if (d.group !== "time") {
      options.push({ value: "distinct:" + d.value, label: measureLabel("distinct:" + d.value), group: "distinct" });
    }
  }
  const values = rows === "events" ? EVENT_VALUES : validFields(fieldNames).map((n) => "fields." + n);
  for (const value of values) {
    for (const fn of FUNCTIONS) {
      options.push({ value: fn + ":" + value, label: measureLabel(fn + ":" + value), group: "number" });
    }
  }
  return options;
}

// Lookups the form can ask (their dimension is grouped and the rows match).
export function applicableLookups(form) {
  const dims = new Set(form.groupBy.filter(Boolean));
  return Object.keys(LOOKUPS).filter((name) => LOOKUPS[name].rows === form.rows && dims.has(LOOKUPS[name].needs));
}

// -- request -------------------------------------------------------------------------------------

function listOf(text) {
  return String(text || "").split(",").map((s) => s.trim()).filter(Boolean);
}

function uniqueFilled(list) {
  const out = [];
  for (const item of list || []) {
    if (item && !out.includes(item)) {
      out.push(item);
    }
  }
  return out;
}

export function parseLimit(text) {
  const value = Number(String(text).trim());
  return Number.isInteger(value) && value >= 1 && value <= MAX_LIMIT ? value : null;
}

// The request body for the server, or {error} with the text to show. `viewFilter` is the ONE
// composed filter of the grid; "Only errors" adds the severity condition to it.
export function buildRequest(form, viewFilter) {
  const groupBy = uniqueFilled(form.groupBy);
  const measures = uniqueFilled(form.measures);
  if (measures.length === 0) {
    return { error: S.aggNeedMeasure };
  }
  if (groupBy.filter((d) => d.startsWith("bucket:")).length > 1) {
    return { error: S.aggOneBucket };
  }
  const limit = parseLimit(form.limit);
  if (limit === null) {
    return { error: format(S.aggBadLimit, { max: MAX_LIMIT }) };
  }
  const request = {
    rows: form.rows === "events" ? "events" : "entries",
    filter: form.onlyErrors ? andFilters(viewFilter, ERRORS_ONLY) : viewFilter || null,
    group_by: groupBy,
    measures,
    limit,
  };
  if (request.rows === "events") {
    const events = {};
    for (const [name, text] of [["kind", form.eventKinds], ["name", form.eventNames], ["status", form.eventStatuses]]) {
      const values = listOf(text);
      if (values.some((v) => v.length > LIST_WORD_MAX)) {
        return { error: S.aggBadEventList };
      }
      if (values.length) {
        events[name] = values;
      }
    }
    if (Object.keys(events).length) {
      request.events = events;
    }
  }
  const ids = [...groupBy, ...measures];
  if (form.orderBy && ids.includes(form.orderBy)) {
    request.order = { by: form.orderBy, dir: form.orderDir === "asc" ? "asc" : "desc" };
  }
  const lookups = applicableLookups({ ...form, rows: request.rows }).filter((l) => (form.lookups || []).includes(l));
  if (lookups.length) {
    request.lookups = lookups;
  }
  return { request };
}

// -- result --------------------------------------------------------------------------------------

export function formatNumber(value) {
  if (value === null || value === undefined) {
    return "";
  }
  return Number(value).toLocaleString("en-US", { maximumFractionDigits: 3 });
}

// Display text of one group value; `label` is the server label (template title, file name, C<n>).
export function groupText(dimId, value, label) {
  if (value === null || value === undefined) {
    return S.aggEmptyValue;
  }
  if (dimId.startsWith("bucket:")) {
    return formatTs(value);
  }
  if (label !== null && label !== undefined) {
    return String(label);
  }
  if (dimId === "tpl") {
    return value === 0 ? S.dstUnclassified : String(value);
  }
  if (dimId === "sev") {
    return S.severityNames[value] || String(value);
  }
  if (dimId === "level") {
    return S.levelNames[value] || String(value);
  }
  if (dimId === "tid" && Number.isInteger(value)) {
    return "0x" + value.toString(16);
  }
  return typeof value === "object" ? JSON.stringify(value) : String(value);
}

const METHOD_NOTES = {
  sample: () => S.aggSampleWarning,
  bucket_estimate: () => S.aggBucketWarning,
};

// Table, summary and notices from a server answer. `exactRequested`: the answer of "Compute exactly".
// `extra`: condition the panel added to the view (ANDed into every drill-down).
export function buildView(data, exactRequested = false, extra = null) {
  const columns = Array.isArray(data && data.columns) ? data.columns : [];
  const groups = columns.filter((c) => c.role === "group").length;
  const rows = (Array.isArray(data && data.rows) ? data.rows : []).map((row) => {
    const values = Array.isArray(row.values) ? row.values : [];
    const labels = Array.isArray(row.labels) ? row.labels : [];
    const cells = columns.map((column, i) => {
      if (column.role === "group") {
        return groupText(column.id, values[i], labels[i]);
      }
      if (column.role === "measure") {
        return formatNumber(values[i]);
      }
      return values[i] === null || values[i] === undefined ? "" : String(values[i]);
    });
    const drill = row.drill && extra ? andFilters(extra, row.drill) : row.drill || null;
    return { cells, values, drill, title: cells.slice(0, groups).join(" · ") };
  });
  const notices = [];
  if (data && METHOD_NOTES[data.method]) {
    notices.push(METHOD_NOTES[data.method]());
  }
  if (data && !data.exact && exactRequested) {
    notices.push(S.aggExactTooSlow);
  }
  for (const note of (data && data.notes) || []) {
    if (note === "lookups_without_pack") {
      notices.push(S.aggPackMissing);
    } else if (note === "lookup_ambiguous") {
      notices.push(S.aggLookupAmbiguous);
    }
  }
  const shown = rows.length;
  const total = data ? data.groups : 0;
  let summary = data && data.truncated ? format(S.aggSummaryTruncated, { shown, groups: formatNumber(total) })
    : format(S.aggSummary, { groups: formatNumber(total) });
  if (data && data.total !== null && data.total !== undefined) {
    summary += " " + format(data.exact ? S.aggTotal : S.aggTotalApprox, { total: formatNumber(data.total) });
  }
  return {
    headers: columns.map(columnLabel),
    numeric: columns.map((c) => c.role === "measure"),
    rows,
    summary,
    notices,
    canComputeExactly: Boolean(data) && !data.exact && !exactRequested,
  };
}

// Plain text of the table for the clipboard ("Copy table"): tab separated, one line per row.
export function tableText(view) {
  const clean = (text) => String(text).replace(/[\t\r\n]+/g, " ");
  return [view.headers, ...view.rows.map((r) => r.cells)].map((cells) => cells.map(clean).join("\t")).join("\n") + "\n";
}

// Data for the chart from a server answer, or null when there is nothing to draw:
// {kind: "time", xs: [seconds], series: [{label, values}]} with a time bucket among the groups
// (one line per combination of the other groups, the MAX_SERIES largest), else
// {kind: "bars", labels: [...], series: [{label, values}]} over the rows (at most MAX_BARS).
// The value is the first measure.
export function chartData(data) {
  const columns = Array.isArray(data && data.columns) ? data.columns : [];
  const measureAt = columns.findIndex((c) => c.role === "measure");
  const groupAts = columns.map((c, i) => (c.role === "group" ? i : -1)).filter((i) => i >= 0);
  const rows = Array.isArray(data && data.rows) ? data.rows : [];
  if (measureAt < 0 || groupAts.length === 0 || rows.length === 0) {
    return null;
  }
  const label = measureLabel(columns[measureAt].id);
  const bucketAt = groupAts.find((i) => columns[i].id.startsWith("bucket:"));
  const textOf = (row, i) => groupText(columns[i].id, row.values[i], (row.labels || [])[i]);
  if (bucketAt === undefined) {
    const shown = rows.slice(0, MAX_BARS);
    return {
      kind: "bars",
      measure: label,
      labels: shown.map((row) => groupAts.map((i) => textOf(row, i)).join(" · ")),
      series: [{ label, values: shown.map((row) => numberOrNull(row.values[measureAt])) }],
      truncated: rows.length > shown.length,
    };
  }
  const others = groupAts.filter((i) => i !== bucketAt);
  const times = Array.from(new Set(rows.map((r) => r.values[bucketAt]).filter((t) => Number.isFinite(t)))).sort((a, b) => a - b);
  const index = new Map(times.map((t, k) => [t, k]));
  const series = new Map();
  for (const row of rows) {
    const t = row.values[bucketAt];
    if (!index.has(t)) {
      continue;
    }
    // a series is one combination of the other groups' VALUES (two sources named a.log stay apart);
    // the text is only its label
    const key = JSON.stringify(others.map((i) => row.values[i]));
    if (!series.has(key)) {
      const name = others.length ? others.map((i) => textOf(row, i)).join(" · ") : label;
      series.set(key, { label: name, values: new Array(times.length).fill(null), sum: 0 });
    }
    const s = series.get(key);
    const v = numberOrNull(row.values[measureAt]);
    s.values[index.get(t)] = v;
    s.sum += v || 0;
  }
  const ordered = Array.from(series.values()).sort((a, b) => b.sum - a.sum);
  return {
    kind: "time",
    measure: label,
    xs: times.map((t) => t / 1000),
    series: ordered.slice(0, MAX_SERIES).map(({ label: l, values }) => ({ label: l, values })),
    truncated: ordered.length > MAX_SERIES,
  };
}

function numberOrNull(v) {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

// The user filter after "Filter by this row".
export function drillFilter(userFilter, drill) {
  return drill ? andFilters(userFilter, drill) : userFilter;
}

export function csvFileName(caseId, now = new Date()) {
  const safeCase = String(caseId || "case").replace(/[^A-Za-z0-9._-]+/g, "_").slice(0, 60);
  const stamp = now.toISOString().replace(/[-:]/g, "").replace(/\..*$/, "").replace("T", "-");
  return "dmslog-" + safeCase + "-aggregate-" + stamp + ".csv";
}

// Request flow: one request at a time, stale answers dropped.
// api.aggregate(caseId, body, signal); api.aggregateCsv(caseId, body, signal) -> {data}.
export function createAggregateFlow({ api, getCase, onState }) {
  // caseId: the case the request and the result belong to (CSV and drill-down stay bound to it)
  const state = { status: "idle", data: null, error: "", exact: false, request: null, stale: false, caseId: null };
  let controller = null;
  let seq = 0;
  const emit = () => onState && onState(state);

  async function run(request, { exact = false } = {}) {
    seq += 1;
    const mine = seq;
    if (controller) {
      controller.abort();
    }
    const caseId = getCase();
    if (!caseId) {
      controller = null;
      Object.assign(state, { status: "idle", data: null, error: S.aggNoCase, request: null, caseId: null });
      emit();
      return;
    }
    controller = new AbortController();
    Object.assign(state, { status: "loading", data: null, error: "", exact: Boolean(exact), request, stale: false, caseId });
    emit();
    try {
      const body = exact ? { ...request, exact: true } : request;
      const data = await api.aggregate(caseId, body, controller.signal);
      if (mine !== seq) {
        return;
      }
      Object.assign(state, { status: "ready", data });
    } catch (err) {
      if (mine !== seq || (err && err.name === "AbortError")) {
        return;
      }
      Object.assign(state, { status: "error", data: null, error: err && err.message ? err.message : S.errorGeneric });
    }
    emit();
  }

  // The CSV of the last request (same body, format csv): resolves to the text, or throws.
  async function csv() {
    if (!state.request) {
      throw new Error(S.aggRunFirst);
    }
    const body = state.exact ? { ...state.request, exact: true } : state.request;
    const result = await api.aggregateCsv(state.caseId, body);
    return result.data;
  }

  // The view or the case changed: a pending answer is dropped (it belongs to the old view); a ready
  // result stays visible as stale for the same case, and is cleared for another case.
  function markStale() {
    if (state.status === "loading") {
      seq += 1;
      if (controller) {
        controller.abort();
        controller = null;
      }
      Object.assign(state, { status: "idle", data: null, error: S.aggStale, request: null, caseId: null });
      emit();
    } else if (state.status === "ready" && getCase() !== state.caseId) {
      Object.assign(state, { status: "idle", data: null, error: "", request: null, stale: false, caseId: null });
      emit();
    } else if (state.status === "ready") {
      state.stale = true;
      emit();
    }
  }

  function cancel() {
    seq += 1;
    if (controller) {
      controller.abort();
      controller = null;
    }
    Object.assign(state, { status: "idle", data: null, error: "", request: null, stale: false, caseId: null });
    emit();
  }

  return { state, run, csv, markStale, cancel };
}
