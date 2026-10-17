// "Missing steps" panel (ROADMAP 9.12, ARQUITETURA 10.9): the pure model and the loading flow. No DOM.
//   GET /api/events/missing          keyset page of {event, steps, missing, cause, proven}  (kind, status, name, after, limit)
//   GET /api/events/missing/summary  {groups: [{kind, family, name, missing, n, causes}], total}  (kind, status)
// The server has no filter on the cause: the Cause selector works on the items already loaded and the flow reads further
// pages (a bounded number of them) when none of the loaded ones match. Names, keys and families come from the ERS
// (untrusted): this module only shapes them. Every answer carries the generation of the request that asked, so a new
// filter, a refresh or a case change drops the answers in flight, and a page never lands twice (ids are deduplicated).
import { gridTarget, kindLabel, statusLabel, statusTone, familyText } from "./events-model.js";
import { formatTs } from "./time.js";
import { S, format, pluralKey } from "./strings.js";

export const MISSING_KINDS = ["operation", "startup", "map_reload", "retry_seq"];
export const MISSING_STATUSES = ["open", "truncated", "ok", "business_error", "fail", "timeout"];
export const CAUSES = ["superseded", "timeout", "capacity", "capture_cut", "end_of_log"];
export const PAGE_LIMIT = 200;
export const MAX_PAGES_PER_STEP = 10; // pages read in one step while the cause filter finds nothing

const present = (value) => value !== null && value !== undefined && String(value).trim() !== "";
const asCount = (value) => (typeof value === "number" && Number.isFinite(value) && value > 0 ? Math.floor(value) : 0);

export function causeLabel(cause) {
  return S.msCauseNames[cause] || String(cause === null || cause === undefined ? "" : cause);
}

export function stepLabel(step) {
  return S.msStepNames[step] || String(step === null || step === undefined ? "" : step);
}

// Query of GET /api/events/missing. The cause is not sent (the server has no such filter).
export function buildListParams(filters, after, limit = PAGE_LIMIT) {
  const f = filters || {};
  const params = { limit };
  for (const key of ["kind", "status", "name"]) {
    if (present(f[key])) {
      params[key] = String(f[key]).trim();
    }
  }
  if (present(after)) {
    params.after = after;
  }
  return params;
}

// Query of GET /api/events/missing/summary: the groups are the way to a name, so the name never narrows them.
export function buildSummaryParams(filters) {
  const f = filters || {};
  const params = {};
  for (const key of ["kind", "status"]) {
    if (present(f[key])) {
      params[key] = String(f[key]).trim();
    }
  }
  return params;
}

// One item of the list as plain data. A malformed item (no event) gives null and is skipped.
export function buildItemRow(item) {
  const event = item && item.event && typeof item.event === "object" ? item.event : null;
  if (event === null || !Number.isSafeInteger(event.id)) {
    return null;
  }
  const missing = Array.isArray(item.missing) ? item.missing.map(stepLabel).join(", ") : "";
  const cause = typeof item.cause === "string" ? item.cause : "";
  return {
    id: event.id,
    kind: event.kind,
    kindLabel: kindLabel(event.kind),
    name: event.name || S.evUnnamed,
    time: event.ts_start === null || event.ts_start === undefined ? "" : formatTs(event.ts_start),
    process: familyText(event),
    missing,
    cause,
    causeLabel: causeLabel(cause),
    proven: item.proven === true,
    status: event.status,
    statusLabel: statusLabel(event.status),
    tone: statusTone(event.status),
    target: gridTarget(event),
  };
}

// The summary rows to show under the Cause filter: with a cause, the count of that cause (groups without any hide).
export function summaryRows(groups, cause) {
  const rows = [];
  for (const group of Array.isArray(groups) ? groups : []) {
    if (!group || typeof group !== "object") {
      continue;
    }
    const causes = group.causes && typeof group.causes === "object" ? group.causes : {};
    const count = present(cause) ? asCount(causes[cause]) : asCount(group.n);
    if (count === 0) {
      continue;
    }
    const parts = CAUSES.filter((c) => asCount(causes[c]) > 0).map((c) => format(S.msCauseCount, { cause: causeLabel(c), count: asCount(causes[c]).toLocaleString("en-US") }));
    rows.push({
      key: [group.kind, group.family, group.name].map((v) => (v === null || v === undefined ? "" : String(v))).join("\u0000"),
      kind: group.kind,
      kindLabel: kindLabel(group.kind),
      family: group.family || "",
      name: group.name === null || group.name === undefined || group.name === "" ? S.evUnnamed : String(group.name),
      rawName: group.name === null || group.name === undefined ? "" : String(group.name),
      canFilter: typeof group.name === "string" && group.name !== "" && !group.name.includes(","), // the name filter is a comma list
      missing: stepLabel(group.missing),
      count,
      causes: parts.join(", "),
    });
  }
  return rows;
}

export function summaryTotal(rows) {
  return rows.reduce((sum, row) => sum + row.count, 0);
}

export function countText(shown, hasMore) {
  const n = asCount(shown);
  return format(S[pluralKey(hasMore ? "msCountMore" : "msCount", n)], { count: n.toLocaleString("en-US") });
}

const errorText = (error) => (error && error.message ? error.message : "");
const isAbort = (error) => Boolean(error && error.name === "AbortError");

export function createMissingFlow({ api, getCase, onState, limit = PAGE_LIMIT, maxPages = MAX_PAGES_PER_STEP }) {
  let listGeneration = 0;
  let summaryGeneration = 0;
  let listController = null;
  let summaryController = null;
  let loaded = []; // every item read so far (rows), before the cause filter
  const seen = new Set();
  const state = {
    caseId: null,
    filters: { kind: "", status: "", cause: "", name: "" },
    items: [], // `loaded` under the cause filter
    version: 0, // changes whenever `items` is not just the old list plus new rows at the end
    next: null,
    status: "nocase", // nocase | loading | ready | error
    loadingMore: false,
    error: null,
    moreError: null, // a failed "Load more": the list stays, the button tries again
    summary: { status: "none", groups: [], total: 0, error: null }, // none | loading | ready | error
  };

  const emit = () => onState(state);
  const matches = (row) => !present(state.filters.cause) || row.cause === state.filters.cause;

  function refilter() {
    state.items = loaded.filter(matches);
    state.version += 1;
  }

  function stopList() {
    listGeneration += 1;
    if (listController) {
      listController.abort();
      listController = null;
    }
    state.loadingMore = false;
  }

  function take(rows) {
    let added = 0;
    for (const row of rows) {
      if (!seen.has(row.id)) {
        seen.add(row.id);
        loaded.push(row);
        if (matches(row)) {
          state.items.push(row);
          added += 1;
        }
      }
    }
    return added;
  }

  // Reads pages until `wanted` new rows pass the cause filter, the list ends, or maxPages were read.
  async function readPages(token, signal, wanted) {
    let added = 0;
    for (let pages = 0; pages < maxPages && added < wanted; pages += 1) {
      const forCase = state.caseId;
      const data = await api.missingEvents(forCase, buildListParams(state.filters, state.next, limit), signal);
      if (token !== listGeneration) {
        return false;
      }
      const rows = (data && Array.isArray(data.items) ? data.items : []).map(buildItemRow).filter((row) => row !== null);
      added += take(rows);
      state.next = data && data.next !== undefined && data.next !== null && data.next !== "" ? data.next : null;
      if (state.next === null) {
        break;
      }
    }
    return true;
  }

  async function loadList() {
    stopList();
    const token = listGeneration;
    listController = new AbortController();
    const signal = listController.signal;
    loaded = [];
    seen.clear();
    state.items = [];
    state.version += 1;
    state.next = null;
    state.error = null;
    state.moreError = null;
    state.status = state.caseId ? "loading" : "nocase";
    emit();
    if (!state.caseId) {
      return;
    }
    try {
      if (!(await readPages(token, signal, 1))) {
        return;
      }
      state.status = "ready";
      emit();
    } catch (error) {
      if (token === listGeneration && !isAbort(error)) {
        state.status = "error";
        state.error = errorText(error);
        emit();
      }
    }
  }

  async function loadSummary() {
    summaryGeneration += 1;
    const token = summaryGeneration;
    if (summaryController) {
      summaryController.abort();
    }
    summaryController = new AbortController();
    const signal = summaryController.signal;
    state.summary = { status: state.caseId ? "loading" : "none", groups: [], total: 0, error: null };
    emit();
    if (!state.caseId) {
      return;
    }
    try {
      const data = await api.missingSummary(state.caseId, buildSummaryParams(state.filters), signal);
      if (token !== summaryGeneration) {
        return;
      }
      state.summary = {
        status: "ready",
        groups: data && Array.isArray(data.groups) ? data.groups : [],
        total: data ? asCount(data.total) : 0,
        error: null,
      };
      emit();
    } catch (error) {
      if (token === summaryGeneration && !isAbort(error)) {
        state.summary = { status: "error", groups: [], total: 0, error: errorText(error) };
        emit();
      }
    }
  }

  // Both parts start from the case of the page now; a late answer of another case or of an older request is dropped.
  function refresh() {
    state.caseId = getCase() || null;
    return Promise.all([loadList(), loadSummary()]).then(() => undefined);
  }

  async function loadMore() {
    if (state.status !== "ready" || state.loadingMore || state.next === null || !state.caseId) {
      return;
    }
    const token = listGeneration;
    const signal = listController ? listController.signal : undefined;
    state.loadingMore = true;
    state.moreError = null;
    emit();
    try {
      const completed = await readPages(token, signal, 1);
      if (!completed || token !== listGeneration) {
        return;
      }
      state.loadingMore = false;
      emit();
    } catch (error) {
      if (token === listGeneration && !isAbort(error)) {
        state.loadingMore = false;
        state.moreError = errorText(error) || S.msMoreError;
        emit();
      }
    }
  }

  // patch: any of kind, status, name, cause. Only the cause is applied to what is loaded; the others read again.
  function setFilters(patch) {
    const before = state.filters;
    state.filters = { ...before, ...patch };
    const changed = (key) => before[key] !== state.filters[key];
    if (changed("kind") || changed("status") || changed("name")) {
      const summaryChanged = changed("kind") || changed("status");
      state.caseId = getCase() || null;
      return Promise.all([loadList(), summaryChanged ? loadSummary() : Promise.resolve()]).then(() => undefined);
    }
    if (changed("cause")) {
      refilter();
      emit();
      if (state.items.length === 0 && state.next !== null && state.status === "ready") {
        return loadMore();
      }
    }
    return Promise.resolve();
  }

  // The case of the page changed (or there is none): forget everything and read again.
  function reset() {
    state.filters = { kind: "", status: "", cause: "", name: "" };
    return refresh();
  }

  return { state, refresh, reset, loadMore, setFilters };
}
