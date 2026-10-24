// Pure helpers and loading flows of the Operations panel and the clock-jump banner (no DOM).
// Event names, keys and entry text come from the ERS (untrusted): this module only shapes them.
// Every flow clears its state before a new request and drops answers of an older generation or of
// another case, so a table never outlives its case.
import { familyProcessText } from "./process-label.js";
import { S, format } from "./strings.js";
import { formatTs } from "./time.js";

export const OPERATION_KIND = "operation";
export const PAGE_LIMIT = 200;
export const EVENT_KINDS = [
  "operation", "exception", "retry_seq", "restart", "startup", "level_window", "gap", "clock_jump",
  "service_state", "map_reload", "stats_block",
];
export const STATUS_VALUES = ["ok", "business_error", "fail", "timeout", "open", "truncated"];
const STATUS_TONE = {
  ok: "ok", business_error: "warn", fail: "bad", timeout: "bad", open: "muted", truncated: "muted",
};

export function statusLabel(status) {
  if (status === null || status === undefined || status === "") {
    return S.evStatusNone;
  }
  return S.evStatusNames[status] || String(status);
}

export function statusTone(status) {
  return STATUS_TONE[status] || "neutral";
}

export function kindLabel(kind) {
  return S.evKindNames[kind] || String(kind === null || kind === undefined ? "" : kind);
}

function trimNumber(value) {
  return String(Math.round(value * 100) / 100);
}

// Milliseconds as "340 ms", "2.5 s", "3 min 4 s", "2 h 5 min"; null/invalid -> "".
export function formatDuration(ms) {
  if (ms === null || ms === undefined || !Number.isFinite(ms) || ms < 0) {
    return "";
  }
  if (ms < 1000) {
    return format(S.evDurationMs, { n: trimNumber(ms) });
  }
  if (ms < 60000) {
    return format(S.evDurationS, { n: trimNumber(ms / 1000) });
  }
  if (ms < 3600000) {
    const minutes = Math.floor(ms / 60000);
    return format(S.evDurationMin, { m: minutes, s: Math.round((ms - minutes * 60000) / 1000) });
  }
  const hours = Math.floor(ms / 3600000);
  return format(S.evDurationH, { h: hours, m: Math.round((ms - hours * 3600000) / 60000) });
}

// Query parameters of GET /api/events from the panel filters (empty values are left out by api.js).
export function buildEventParams(filters, after, limit = PAGE_LIMIT) {
  const f = filters || {};
  const params = { kind: f.kind || OPERATION_KIND, limit };
  for (const key of ["name", "status", "stream", "q", "from", "to"]) {
    const value = f[key];
    if (value !== null && value !== undefined && String(value).trim() !== "") {
      params[key] = typeof value === "string" ? value.trim() : value;
    }
  }
  if (after !== null && after !== undefined && after !== "") {
    params.after = after;
  }
  return params;
}

export function familyText(event) {
  return familyProcessText(event);
}

export function buildEventRow(event) {
  return {
    id: event.id,
    kind: event.kind,
    kindLabel: kindLabel(event.kind),
    time: formatTs(event.ts_start),
    process: familyText(event),
    name: event.name || S.evUnnamed,
    key: event.key || "",
    durationText: formatDuration(event.duration_ms),
    status: event.status,
    statusLabel: statusLabel(event.status),
    tone: statusTone(event.status),
    entries: event.n_entries === null || event.n_entries === undefined ? "" : String(event.n_entries),
    clockSuspect: Boolean(event.detail && (event.detail.clock_jump || event.detail.possible_clock_jump)),
  };
}

export function buildEventRows(events) {
  return (Array.isArray(events) ? events : []).map(buildEventRow);
}

// Where "Show in grid" goes: the event's time window and the entry to select.
export function gridTarget(event) {
  if (!event || event.ts_start === null || event.ts_start === undefined) {
    return null;
  }
  const to = event.ts_end === null || event.ts_end === undefined ? event.ts_start : Math.max(event.ts_end, event.ts_start);
  return { from: event.ts_start, to, entryId: event.start_entry_id || null };
}

function detailValue(value) {
  if (value === null || value === undefined) {
    return "";
  }
  if (typeof value === "object") {
    return JSON.stringify(value);
  }
  return String(value);
}

// Detail pane content from GET /api/events/<id>: label/value pairs, entries and related events.
export function buildDetailView(data) {
  const event = data && data.event ? data.event : null;
  if (!event) {
    return null;
  }
  const fields = [
    [S.evFieldKind, kindLabel(event.kind)],
    [S.evFieldName, event.name || ""],
    [S.evFieldKey, event.key || ""],
    [S.evFieldFamily, event.family || ""],
    [S.evFieldProcess, event.process || ""],
    [S.evFieldStream, event.stream_id === null || event.stream_id === undefined ? "" : String(event.stream_id)],
    [S.evFieldTid, event.tid === null || event.tid === undefined ? "" : String(event.tid)],
    [S.evFieldStart, formatTs(event.ts_start)],
    [S.evFieldEnd, formatTs(event.ts_end)],
    [S.evFieldDuration, formatDuration(event.duration_ms)],
    [S.evFieldStatus, statusLabel(event.status)],
    [S.evFieldEntries, event.n_entries === null || event.n_entries === undefined ? "" : String(event.n_entries)],
    [S.evFieldSensitive, event.sensitive ? S.evYes : S.evNo],
  ].map(([label, value]) => ({ label, value }));
  const detail = event.detail && typeof event.detail === "object" ? event.detail : {};
  for (const name of Object.keys(detail).sort()) {
    fields.push({ label: format(S.evFieldDetail, { name }), value: detailValue(detail[name]) });
  }
  const entries = (Array.isArray(data.entries) ? data.entries : []).map((entry) => ({
    id: entry.id,
    time: formatTs(entry.ts),
    tid: entry.tid === null || entry.tid === undefined ? "" : String(entry.tid),
    level: entry.level === null || entry.level === undefined ? "" : String(entry.level),
    sev: entry.sev,
    code: entry.tpl_code || "",
    text: entry.msg === null || entry.msg === undefined ? "" : String(entry.msg),
  }));
  const total = Number.isFinite(data.entries_total) ? data.entries_total : entries.length;
  return {
    id: event.id,
    title: kindLabel(event.kind) + ": " + (event.name || S.evUnnamed),
    tone: statusTone(event.status),
    statusLabel: statusLabel(event.status),
    fields,
    entries,
    entriesTotal: total,
    entriesPartial: total > entries.length,
    related: buildEventRows(data.related),
    target: gridTarget(event),
  };
}

// Loads a first page and further pages (keyset: `next` of one answer is `after` of the next one).
// Filters: { kind, name, status, q }. Detail of the selected event is loaded on its own guard.
export function createEventsFlow({ api, getCase, onState, limit = PAGE_LIMIT }) {
  let listGeneration = 0;
  let detailGeneration = 0;
  let listController = null;
  let detailController = null;
  const state = {
    caseId: null,
    filters: { kind: OPERATION_KIND, name: "", status: "", q: "" },
    events: [],
    next: null,
    status: "nocase",
    loadingMore: false,
    error: null,
    selectedId: null,
    detail: { status: "none", data: null, error: null },
  };

  function emit() {
    onState(state);
  }

  function abortDetail() {
    detailGeneration += 1;
    if (detailController) {
      detailController.abort();
      detailController = null;
    }
    state.selectedId = null;
    state.detail = { status: "none", data: null, error: null };
  }

  function errorText(error) {
    return error && error.message ? error.message : "";
  }

  async function refresh() {
    listGeneration += 1;
    const token = listGeneration;
    if (listController) {
      listController.abort();
    }
    listController = new AbortController();
    const signal = listController.signal;
    abortDetail();
    const caseId = getCase() || null;
    state.caseId = caseId;
    state.events = [];
    state.next = null;
    state.loadingMore = false;
    state.error = null;
    state.status = caseId ? "loading" : "nocase";
    emit();
    if (!caseId) {
      return;
    }
    try {
      const data = await api.events(caseId, buildEventParams(state.filters, null, limit), signal);
      if (token !== listGeneration) {
        return;
      }
      state.events = Array.isArray(data.events) ? data.events : [];
      state.next = data.next === undefined ? null : data.next;
      state.status = "ready";
      emit();
    } catch (error) {
      if (token === listGeneration && !signal.aborted) {
        state.status = "error";
        state.error = errorText(error);
        emit();
      }
    }
  }

  async function loadMore() {
    if (state.status !== "ready" || state.loadingMore || state.next === null || !state.caseId) {
      return;
    }
    const token = listGeneration;
    const caseId = state.caseId;
    const signal = listController.signal;
    state.loadingMore = true;
    emit();
    try {
      const data = await api.events(caseId, buildEventParams(state.filters, state.next, limit), signal);
      if (token !== listGeneration) {
        return;
      }
      const known = new Set(state.events.map((event) => event.id));
      for (const event of Array.isArray(data.events) ? data.events : []) {
        if (!known.has(event.id)) {
          state.events.push(event);
        }
      }
      state.next = data.next === undefined ? null : data.next;
      state.loadingMore = false;
      emit();
    } catch (error) {
      if (token === listGeneration && !signal.aborted) {
        state.loadingMore = false;
        state.error = errorText(error);
        state.status = "error";
        emit();
      }
    }
  }

  function setFilters(patch) {
    state.filters = { ...state.filters, ...patch };
    return refresh();
  }

  async function select(id) {
    const caseId = state.caseId;
    if (!caseId || caseId !== getCase()) {
      return;
    }
    detailGeneration += 1;
    const token = detailGeneration;
    if (detailController) {
      detailController.abort();
    }
    detailController = new AbortController();
    const signal = detailController.signal;
    state.selectedId = id;
    state.detail = { status: "loading", data: null, error: null };
    emit();
    try {
      const data = await api.event(caseId, id, signal);
      if (token !== detailGeneration) {
        return;
      }
      state.detail = { status: "ready", data, error: null };
      emit();
    } catch (error) {
      if (token === detailGeneration && !signal.aborted) {
        state.detail = { status: "error", data: null, error: errorText(error) };
        emit();
      }
    }
  }

  function clearSelection() {
    abortDetail();
    emit();
  }

  return { state, refresh, loadMore, setFilters, select, clearSelection };
}

// Clock-jump banner: events of kind clock_jump, plus gaps flagged detail.possible_clock_jump.
// Result: null (nothing to warn about) or { kind: "jump" | "possible", count, more, first, target }.
export function summarizeClockJumps(jumps, gaps, jumpsMore) {
  const list = (Array.isArray(jumps) ? jumps : []).filter((e) => e.ts_start !== null && e.ts_start !== undefined);
  if (list.length > 0) {
    const first = list.reduce((a, b) => (b.ts_start < a.ts_start ? b : a));
    return { kind: "jump", count: list.length, more: Boolean(jumpsMore), first: first.ts_start, target: gridTarget(first) };
  }
  const flagged = (Array.isArray(gaps) ? gaps : []).filter(
    (e) => e.detail && e.detail.possible_clock_jump && e.ts_start !== null && e.ts_start !== undefined,
  );
  if (flagged.length > 0) {
    const first = flagged.reduce((a, b) => (b.ts_start < a.ts_start ? b : a));
    return { kind: "possible", count: flagged.length, more: false, first: first.ts_start, target: gridTarget(first) };
  }
  return null;
}

export function bannerText(summary) {
  if (!summary) {
    return "";
  }
  const time = formatTs(summary.first);
  if (summary.kind === "possible") {
    return format(S.clkBannerPossible, { time });
  }
  if (summary.more) {
    return format(S.clkBannerManyMore, { count: summary.count, time });
  }
  return summary.count === 1
    ? format(S.clkBannerOne, { time })
    : format(S.clkBannerMany, { count: summary.count, time });
}

export const CLOCK_JUMP_LIMIT = 50;
export const GAP_SCAN_LIMIT = 200;

// One small request for clock_jump events at case open (and when indexing finished); when there is
// none, gaps are scanned for detail.possible_clock_jump (the answer cannot be filtered by detail).
export function createClockJumpFlow({ api, getCase, onState }) {
  let generation = 0;
  let controller = null;
  const state = { caseId: null, summary: null };

  function emit() {
    onState(state);
  }

  async function refresh() {
    generation += 1;
    const token = generation;
    if (controller) {
      controller.abort();
    }
    controller = new AbortController();
    const signal = controller.signal;
    const caseId = getCase() || null;
    state.caseId = caseId;
    state.summary = null;
    emit();
    if (!caseId) {
      return;
    }
    try {
      const jumps = await api.events(caseId, { kind: "clock_jump", limit: CLOCK_JUMP_LIMIT }, signal);
      if (token !== generation) {
        return;
      }
      let summary = summarizeClockJumps(jumps.events, [], jumps.next !== null && jumps.next !== undefined);
      if (summary === null) {
        const gaps = await api.events(caseId, { kind: "gap", limit: GAP_SCAN_LIMIT }, signal);
        if (token !== generation) {
          return;
        }
        summary = summarizeClockJumps([], gaps.events, false);
      }
      state.summary = summary;
      emit();
    } catch (error) {
      // A failed hint is not worth an error banner: stay silent and keep the banner hidden.
      if (token === generation) {
        state.summary = null;
        emit();
      }
    }
  }

  return { state, refresh };
}
