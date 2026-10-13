// Pure model of the "Distinct values" panel and of the "Group by template" view (no DOM).
// Server side: POST /api/entries/distinct (store/distinct.py). Values come from untrusted logs:
// this module only turns them into plain strings; the panel writes them with textContent.
import { andFilters } from "./filters.js";
import { S, format } from "./strings.js";

export const DEFAULT_TOP = 20; // same default as the server
export const MAX_TOP = 500;
export const GROUP_TOP = 200; // templates listed by the group view
export const GROUP_PAGE = 50; // rows fetched per expanded template (keyset pages)
export const COLUMN_FIELDS = ["tpl", "source", "sev", "level", "tid", "pid"];
const FIELD_NAME = /^[A-Za-z_][A-Za-z0-9_]{0,63}$/;
const KEY_TYPE = /^[A-Za-z0-9_.-]{1,64}$/;

// Options of the field chooser: {value, label, group} with group "column" | "key" | "field".
export function fieldOptions(keyTypes, fieldNames) {
  const options = COLUMN_FIELDS.map((name) => ({ value: name, label: S.dstColumnNames[name], group: "column" }));
  for (const type of Array.from(new Set(keyTypes || [])).sort()) {
    if (typeof type === "string" && KEY_TYPE.test(type)) {
      options.push({ value: "key." + type, label: type, group: "key" });
    }
  }
  for (const name of Array.from(new Set(fieldNames || [])).sort()) {
    if (typeof name === "string" && FIELD_NAME.test(name)) {
      options.push({ value: "fields." + name, label: name, group: "field" });
    }
  }
  return options;
}

// The N of "top N": an integer from 1 to MAX_TOP, else null.
export function parseTop(text) {
  const value = Number(String(text).trim());
  return Number.isInteger(value) && value >= 1 && value <= MAX_TOP ? value : null;
}

// Display text of one value of `field`; `label` is the server label (the readable title of a
// template, its code when it has none; the file name of a source).
export function valueText(field, value, label) {
  if (value === null || value === undefined) {
    return S.dstEmpty;
  }
  if (field === "tpl") {
    return value === 0 ? S.dstUnclassified : label || String(value);
  }
  if (field === "source") {
    return label ? format(S.dstSourceValue, { name: label, id: value }) : String(value);
  }
  if (field === "sev") {
    return S.severityNames[value] || String(value);
  }
  if (field === "level") {
    return S.levelNames[value] || String(value);
  }
  if (field === "tid" && Number.isInteger(value)) {
    return "0x" + value.toString(16);
  }
  return typeof value === "object" ? JSON.stringify(value) : String(value);
}

export function formatCount(n) {
  return n === null || n === undefined ? "" : Number(n).toLocaleString("en-US");
}

export function formatPct(pct) {
  return Number(pct).toFixed(pct >= 10 ? 1 : 2) + "%";
}

// Rows for the table plus the summary line, from a server answer. `exactRequested`: the answer is
// the one of "Count exactly" (an estimate then means the exact count did not fit its time).
export function buildView(field, data, exactRequested = false) {
  const values = Array.isArray(data && data.values) ? data.values : [];
  const total = data ? data.total : null;
  const rows = values.map((v) => ({
    text: valueText(field, v.value, v.label),
    count: formatCount(v.count),
    pct: formatPct(v.pct),
    share: total ? Math.max(0, Math.min(1, v.count / total)) : 0,
    leaf: v.leaf || null,
    firstTs: v.first_ts === undefined ? null : v.first_ts,
    lastTs: v.last_ts === undefined ? null : v.last_ts,
    value: v.value,
    // Hover text: the template code next to its title, so the code stays one glance away.
    hint: v.code && v.code !== v.label ? format(S.dstValueTitle, { text: valueText(field, v.value, v.label), code: v.code }) : valueText(field, v.value, v.label),
  }));
  const others = data && data.others ? data.others : { count: 0, groups: 0 };
  let summary;
  if (!data || data.method === "none" || total === null || total === undefined) {
    summary = S.dstTooSlow;
  } else {
    summary = format(data.exact ? S.dstSummary : S.dstSummaryApprox, {
      total: formatCount(total), groups: formatCount(data.groups),
    });
  }
  return {
    rows,
    others: others.count > 0 ? format(S.dstOthers, { count: formatCount(others.count), groups: formatCount(others.groups) }) : "",
    summary,
    approximate: Boolean(data) && !data.exact,
    // Estimates may miss rare values: warn, and offer the exact count unless it was just tried.
    // ("none": the summary already says the count took too long.)
    warning: data && !data.exact && data.method !== "none" ? (exactRequested ? S.dstExactTooSlow : S.dstEstimateWarning) : "",
    canCountExactly: Boolean(data) && !data.exact && !exactRequested,
  };
}

// The user filter after a drill-down: AND the leaf (or its negation) into it.
export function drillFilter(userFilter, leaf, exclude) {
  if (!leaf) {
    return userFilter;
  }
  return andFilters(userFilter, exclude ? { not: leaf } : leaf);
}

// Request flow of the panel: one request at a time, stale answers dropped.
// api.distinct(caseId, filter, field, limit, span, signal); onState(state) after each change.
export function createDistinctFlow({ api, getCase, getFilter, onState }) {
  const state = { status: "idle", field: "tpl", top: DEFAULT_TOP, data: null, error: "", exact: false };
  let controller = null;
  let seq = 0;
  const emit = () => onState && onState(state);

  // `exact`: "Count exactly" (skip the server's direct sample); any other reload is the default.
  async function load({ field, top, exact = false } = {}) {
    state.exact = Boolean(exact);
    if (field !== undefined) {
      state.field = field;
    }
    if (top !== undefined) {
      state.top = top;
    }
    seq += 1;
    const mine = seq;
    if (controller) {
      controller.abort();
    }
    const caseId = getCase();
    if (!caseId) {
      controller = null;
      Object.assign(state, { status: "idle", data: null, error: "" });
      emit();
      return;
    }
    controller = new AbortController();
    const signal = controller.signal;
    Object.assign(state, { status: "loading", error: "" });
    emit();
    try {
      const data = await api.distinct(caseId, getFilter(), state.field, state.top, false, signal, state.exact);
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

  function cancel() {
    seq += 1;
    if (controller) {
      controller.abort();
      controller = null;
    }
    Object.assign(state, { status: "idle", data: null, error: "" });
    emit();
  }

  return { state, load, cancel };
}

// "Group by template": the template list (with first/last time) and, per expanded template, its
// rows through the grid's keyset pages. api.page(caseId, filter, cursor, dir, limit, signal).
export function createGroupFlow({ api, getCase, getFilter, onState, pageLimit = GROUP_PAGE }) {
  const state = { status: "idle", data: null, error: "", groups: new Map(), exact: false };
  let controller = null;
  let seq = 0;
  const emit = () => onState && onState(state);

  // `exact`: "Count exactly" (skip the server's direct sample); any other reload is the default.
  async function load({ exact = false } = {}) {
    state.exact = Boolean(exact);
    seq += 1;
    const mine = seq;
    if (controller) {
      controller.abort();
    }
    for (const group of state.groups.values()) {
      if (group.controller) {
        group.controller.abort();
      }
    }
    state.groups = new Map();
    const caseId = getCase();
    if (!caseId) {
      controller = null;
      Object.assign(state, { status: "idle", data: null, error: "" });
      emit();
      return;
    }
    controller = new AbortController();
    const signal = controller.signal;
    Object.assign(state, { status: "loading", error: "" });
    emit();
    try {
      const data = await api.distinct(caseId, getFilter(), "tpl", GROUP_TOP, true, signal, state.exact);
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

  async function fetchRows(tplId, group, cursor) {
    const mine = seq;
    const caseId = getCase();
    group.controller = new AbortController();
    group.loading = true;
    emit();
    try {
      const filter = andFilters(getFilter(), { tpl: [tplId] });
      const page = await api.page(caseId, filter, cursor, "fwd", pageLimit, group.controller.signal);
      if (mine !== seq || state.groups.get(tplId) !== group || !group.open) {
        return;
      }
      const seen = new Set(group.rows.map((r) => r.id));
      group.rows.push(...(page.rows || []).filter((r) => !seen.has(r.id)));
      group.next = page.next || null;
      group.error = "";
    } catch (err) {
      if (mine !== seq || (err && err.name === "AbortError")) {
        return;
      }
      group.error = err && err.message ? err.message : S.errorGeneric;
    } finally {
      if (state.groups.get(tplId) === group) {
        group.loading = false;
        group.controller = null;
      }
    }
    emit();
  }

  // Opens or closes the rows of one template; opening loads the first page.
  function toggle(tplId) {
    const current = state.groups.get(tplId);
    if (current && current.open) {
      if (current.controller) {
        current.controller.abort();
      }
      state.groups.delete(tplId);
      emit();
      return Promise.resolve();
    }
    const group = { open: true, rows: [], next: null, loading: false, error: "", controller: null };
    state.groups.set(tplId, group);
    return fetchRows(tplId, group, null);
  }

  function loadMore(tplId) {
    const group = state.groups.get(tplId);
    if (!group || !group.open || group.loading || !group.next) {
      return Promise.resolve();
    }
    return fetchRows(tplId, group, group.next);
  }

  function cancel() {
    seq += 1;
    if (controller) {
      controller.abort();
      controller = null;
    }
    for (const group of state.groups.values()) {
      if (group.controller) {
        group.controller.abort();
      }
    }
    state.groups = new Map();
    Object.assign(state, { status: "idle", data: null, error: "" });
    emit();
  }

  return { state, load, toggle, loadMore, cancel };
}
