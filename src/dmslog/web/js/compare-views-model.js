// "Compare views" panel (ROADMAP 9.7, ARQUITETURA 10.10): the rules without DOM. Two sides of one case are compared by
// the sequence of their message types; the server aligns them (POST /api/entries/compare). This module builds the
// request from the form, runs it as ONE validated operation, pages the aligned rows and shapes the answer for the
// panel. Everything that comes from the log (message text, labels, names) is only passed on as strings; the panel
// writes it with textContent.
//
// api.compareViews.run(caseId, body, signal)    -> the answer of /api/entries/compare
// api.views.list(caseId, signal)                -> { views: [{id, name, filter}] }          (saved views, filter mode)
// api.events(caseId, params, signal)            -> { items: [event] }                        (operation and process modes)
import { S, countNoun, format } from "./strings.js";
import { formatTs, parseTs } from "./time.js";

export const MODES = ["filter", "window", "operation", "process"];
export const MAX_SIDE_CHOICES = [1000, 5000, 20000]; // the backend allows 1 to 20,000 (DEFAULT_MAX_SIDE = 5,000)
export const DEFAULT_MAX_SIDE = 5000;
export const PAGE_LIMIT = 200; // rows per page (the backend allows 1 to 1,000)
export const LIST_LIMIT = 200; // operations read for the pickers
export const CURRENT = "current"; // the value of the side selector for "the view as it is now"
const SAFE_ID = /^[1-9][0-9]{0,15}$/; // positive, well inside the integers a number keeps exactly
const MAX_CELL_TEXT = 160;

// ---- the request

// Epoch ms of a typed time ("2026-01-01 12:00:00.000"), or null.
export function parseTime(text) {
  return parseTs(String(text === null || text === undefined ? "" : text));
}

function sideWindow(input, required) {
  const rawFrom = String(input.from || "").trim();
  const rawTo = String(input.to || "").trim();
  if (!required && rawFrom === "" && rawTo === "") {
    return { window: null };
  }
  const from = parseTime(rawFrom);
  const to = parseTime(rawTo);
  if (from === null) {
    return { error: "badFrom" };
  }
  if (to === null) {
    return { error: "badTo" };
  }
  if (from >= to) {
    return { error: "windowOrder" };
  }
  return { window: { from, to } };
}

function parseId(text) {
  const value = String(text === null || text === undefined ? "" : text).trim();
  return SAFE_ID.test(value) ? Number(value) : null;
}

// One side as the backend wants it, or { error: key } (the key of S.cvInvalid).
function buildSide(mode, input, views, common) {
  const side = input || {};
  if (mode === "filter") {
    if (side.source === CURRENT || side.source === undefined || side.source === "") {
      return { value: { filter: common || null } };
    }
    const view = views.find((v) => String(v.id) === String(side.source));
    return view ? { value: { filter: view.filter || null } } : { error: "noView" };
  }
  if (mode === "window") {
    const w = sideWindow(side, true);
    return w.error ? { error: w.error } : { value: { from: w.window.from, to: w.window.to } };
  }
  if (mode === "operation") {
    const event = parseId(side.event);
    return event === null ? { error: "noOperation" } : { value: { event } };
  }
  const stream = parseId(side.stream);
  if (stream === null) {
    return { error: "noProcess" };
  }
  const w = sideWindow(side, false);
  if (w.error) {
    return { error: w.error };
  }
  return { value: w.window ? { stream, from: w.window.from, to: w.window.to } : { stream } };
}

// spec: { mode, a, b, common, views, maxSide, onlyDiff }. `a`/`b` are the form values of the side for the mode; `common`
// is the filter of the current view (the final one: chained views already resolved by the grid). In filter mode the
// sides carry their own filters and there is no common one. The result is { ok: true, body } or { ok: false, errors:
// [{ side, key }] }; nothing is sent in the second case.
export function buildRequest(spec) {
  const mode = spec.mode;
  if (!MODES.includes(mode)) {
    return { ok: false, errors: [{ side: "a", key: "badMode" }] };
  }
  const views = Array.isArray(spec.views) ? spec.views : [];
  const common = mode === "filter" ? null : spec.common || null;
  const errors = [];
  const sides = {};
  for (const name of ["a", "b"]) {
    const built = buildSide(mode, spec[name], views, spec.common || null);
    if (built.error) {
      errors.push({ side: name, key: built.error });
    } else {
      sides[name] = built.value;
    }
  }
  if (errors.length > 0) {
    return { ok: false, errors };
  }
  const maxSide = MAX_SIDE_CHOICES.includes(spec.maxSide) ? spec.maxSide : DEFAULT_MAX_SIDE;
  return {
    ok: true,
    body: {
      mode, filter: common, a: sides.a, b: sides.b, max_side: maxSide, offset: 0, limit: PAGE_LIMIT,
      only_diff: Boolean(spec.onlyDiff),
    },
  };
}

export function nextMaxSide(current) {
  const larger = MAX_SIDE_CHOICES.filter((n) => n > current);
  return larger.length === 0 ? null : larger[0];
}

// ---- errors (static texts only: the server's own message is never shown)

export function classifyError(err) {
  const code = err && err.code ? String(err.code) : "";
  const status = err && err.status !== undefined && err.status !== null ? Number(err.status) : NaN;
  let kind = "other";
  if (code === "network" || status === 0) {
    kind = "network";
  } else if (status === 408 || /timeout/i.test(code)) {
    kind = "timeout";
  } else if (status === 404 && (code === "event_not_found" || code === "stream_not_found")) {
    kind = "gone";
  } else if (status >= 500) {
    kind = "server";
  }
  const key = Object.prototype.hasOwnProperty.call(S.cvErrors, code) ? code : kind;
  return { code, kind, key: Object.prototype.hasOwnProperty.call(S.cvErrors, key) ? key : "other" };
}

export function errorText(error) {
  return S.cvErrors[error.key] || S.cvErrors.other;
}

// ---- the answer

function trimText(text) {
  const clean = String(text === null || text === undefined ? "" : text).replace(/\s+/g, " ").trim();
  const chars = Array.from(clean);
  return chars.length > MAX_CELL_TEXT ? chars.slice(0, MAX_CELL_TEXT).join("") + "…" : clean;
}

function cell(entry) {
  if (!entry || typeof entry !== "object" || !Number.isSafeInteger(entry.id)) {
    return null;
  }
  return {
    id: entry.id,
    ts: Number.isFinite(entry.ts) ? entry.ts : null,
    time: formatTs(entry.ts),
    type: entry.label || entry.tpl || S.cvUnknownType,
    text: trimText(entry.msg),
  };
}

export function opLabel(op) {
  return S.cvOps[op] || S.cvOps.same;
}

export function rowView(row, index) {
  const op = Object.prototype.hasOwnProperty.call(S.cvOps, row && row.op) ? row.op : "same";
  return { key: index, op, opLabel: opLabel(op), a: cell(row && row.a), b: cell(row && row.b) };
}

// What a side compared, in words. The names come from the log (untrusted): the panel writes them as text.
export function describeSide(side, mode, viewName) {
  if (!side || typeof side !== "object") {
    return "";
  }
  if (mode === "filter") {
    return viewName || S.cvCurrentView;
  }
  if (mode === "window") {
    return format(S.cvWindowText, { from: formatTs(side.from), to: formatTs(side.to) });
  }
  if (mode === "operation" && side.event) {
    const e = side.event;
    return format(S.cvOperationText, { name: e.name || S.evUnnamed, time: formatTs(e.ts_start) });
  }
  if (mode === "process" && side.stream) {
    const s = side.stream;
    const label = [s.family, s.process].filter((x, i, all) => x && all.indexOf(x) === i).join(" / ");
    const base = format(S.cvProcessText, { name: label || S.cvUnknownType, pid: s.pid === null || s.pid === undefined ? "?" : s.pid });
    return side.from === undefined ? base : base + " " + format(S.cvWindowText, { from: formatTs(side.from), to: formatTs(side.to) });
  }
  return "";
}

function sideView(side, mode, name) {
  const data = side && typeof side === "object" ? side : {};
  const count = Number.isFinite(data.count) ? data.count : 0;
  return {
    count,
    countText: countNoun("nRecord", count),
    truncated: Boolean(data.truncated),
    description: describeSide(data, mode, name),
    span: data.first_ts === null || data.first_ts === undefined || data.last_ts === null || data.last_ts === undefined
      ? "" : format(S.cvSpanText, { from: formatTs(data.first_ts), to: formatTs(data.last_ts) }),
  };
}

function percent(similarity) {
  return Number.isFinite(similarity) ? Math.round(similarity * 1000) / 10 : 0;
}

// The notes the server can send, each with a static explanation (and the keys of S.cvNotes).
export function noteTexts(notes, maxSide) {
  const out = [];
  for (const note of Array.isArray(notes) ? notes : []) {
    if (Object.prototype.hasOwnProperty.call(S.cvNotes, note)) {
      out.push(format(S.cvNotes[note], { max: Number(maxSide).toLocaleString("en-US") }));
    }
  }
  return out;
}

export function templateRows(templates) {
  const out = [];
  for (const t of Array.isArray(templates) ? templates : []) {
    const a = Number.isFinite(t.a) ? t.a : 0;
    const b = Number.isFinite(t.b) ? t.b : 0;
    const delta = b - a;
    out.push({
      label: t.label === null || t.label === undefined ? S.cvUnknownType : String(t.label),
      code: t.code === null || t.code === undefined ? "" : String(t.code),
      unknown: Boolean(t.unknown),
      a,
      b,
      delta,
      deltaText: delta === 0 ? S.cvDeltaSame : (delta > 0 ? "+" : "-") + Math.abs(delta).toLocaleString("en-US"),
    });
  }
  return out;
}

// view of the first page: sides, counts, notes, message types. `body` is the request that produced it.
export function buildResult(data, body, viewNames) {
  const d = data && typeof data === "object" ? data : {};
  const stats = d.stats && typeof d.stats === "object" ? d.stats : {};
  const mode = MODES.includes(d.mode) ? d.mode : body.mode;
  const names = viewNames || {};
  const count = (n) => (Number.isFinite(n) ? n : 0);
  const a = sideView(d.a, mode, names.a);
  const b = sideView(d.b, mode, names.b);
  const templatesTotal = Number.isFinite(d.templates_total) ? d.templates_total : (d.templates || []).length;
  return {
    mode,
    a,
    b,
    truncatedSides: [a.truncated ? "a" : null, b.truncated ? "b" : null].filter(Boolean),
    stats: {
      same: count(stats.same), onlyA: count(stats.only_a), onlyB: count(stats.only_b),
      changedA: count(stats.changed_a), changedB: count(stats.changed_b), similarity: percent(stats.similarity),
    },
    optimal: d.optimal !== false,
    notes: noteTexts(d.notes, body.max_side),
    templates: templateRows(d.templates),
    templatesTotal,
    runsTotal: count(d.runs_total),
    totalRows: count(d.total_rows),
    digest: typeof d.digest === "string" ? d.digest : "",
  };
}

// Moves the focus among the aligned rows: the next row that is not "same" in `dir` (1 or -1) from `index`, or -1.
export function nextDifference(rows, index, dir) {
  for (let i = index + dir; i >= 0 && i < rows.length; i += dir) {
    if (rows[i].op !== "same") {
      return i;
    }
  }
  return -1;
}

// Grid navigation of the aligned rows. cell = { row, col } (col 0 = A, 1 = B). Returns the new cell or null.
export function moveCell(current, key, rowCount, { page = 10 } = {}) {
  if (rowCount <= 0) {
    return null;
  }
  const clamp = (n, low, high) => Math.min(Math.max(n, low), high);
  const { row, col } = current;
  if (key === "ArrowDown") {
    return { row: clamp(row + 1, 0, rowCount - 1), col };
  }
  if (key === "ArrowUp") {
    return { row: clamp(row - 1, 0, rowCount - 1), col };
  }
  if (key === "ArrowRight") {
    return { row, col: 1 };
  }
  if (key === "ArrowLeft") {
    return { row, col: 0 };
  }
  if (key === "PageDown") {
    return { row: clamp(row + page, 0, rowCount - 1), col };
  }
  if (key === "PageUp") {
    return { row: clamp(row - page, 0, rowCount - 1), col };
  }
  if (key === "Home") {
    return { row: 0, col };
  }
  if (key === "End") {
    return { row: rowCount - 1, col };
  }
  return null;
}

// ---- pickers (lists of the case)

export function viewOptions(views) {
  const out = [{ value: CURRENT, label: S.cvCurrentView }];
  for (const v of Array.isArray(views) ? views : []) {
    if (v && v.id !== undefined && v.id !== null) {
      out.push({ value: String(v.id), label: String(v.name === undefined || v.name === null ? "" : v.name) });
    }
  }
  return out;
}

export function operationOptions(events) {
  const out = [];
  for (const e of Array.isArray(events) ? events : []) {
    if (e && Number.isSafeInteger(e.id)) {
      out.push({
        value: String(e.id),
        label: format(S.cvOperationOption, { name: e.name || S.evUnnamed, time: formatTs(e.ts_start), status: e.status || "" }).trim(),
      });
    }
  }
  return out;
}

// The processes (streams) seen among the events read: each id once, with its family and process.
export function processOptions(events) {
  const seen = new Set();
  const out = [];
  for (const e of Array.isArray(events) ? events : []) {
    if (e && Number.isSafeInteger(e.stream_id) && !seen.has(e.stream_id)) {
      seen.add(e.stream_id);
      const label = [e.family, e.process].filter((x, i, all) => x && all.indexOf(x) === i).join(" / ");
      out.push({ value: String(e.stream_id), label: format(S.cvProcessOption, { name: label || S.cvUnknownType, id: e.stream_id }) });
    }
  }
  return out;
}

// One loader per list. A list belongs to a SCOPE: the case, the collections chosen and the view (getScope() is one string
// for all three). An answer publishes only if it is the latest load of its list AND the scope is still the one it was asked
// for; a list read for another scope is dropped as soon as the scope changes (sync) and never served from the cache
// (ensure). state(name) = { status: "idle"|"loading"|"ready"|"error", items, scope }.
export function createListsFlow({ api, getCase, getScope = () => "", onChange }) {
  const lists = {
    views: { status: "idle", items: [], scope: null },
    operation: { status: "idle", items: [], scope: null },
    process: { status: "idle", items: [], scope: null },
  };
  const tokens = { views: 0, operation: 0, process: 0 };
  const controllers = { views: null, operation: null, process: null };
  const fetchers = {
    views: async (caseId, signal) => (await api.views.list(caseId, signal)).views,
    operation: async (caseId, signal) => (await api.events(caseId, { kind: "operation", limit: LIST_LIMIT }, signal)).items,
    process: async (caseId, signal) => (await api.events(caseId, { limit: LIST_LIMIT }, signal)).items,
  };
  const scopeNow = () => (getCase() ? String(getCase()) + "|" + getScope() : null);

  function stop(name) {
    tokens[name] += 1;
    if (controllers[name]) {
      controllers[name].abort();
      controllers[name] = null;
    }
  }

  function empty(name) {
    stop(name);
    Object.assign(lists[name], { status: "idle", items: [], scope: null });
    onChange(name);
  }

  async function load(name) {
    stop(name);
    const mine = tokens[name];
    const caseId = getCase();
    const scope = scopeNow();
    if (!caseId) {
      Object.assign(lists[name], { status: "idle", items: [], scope: null });
      onChange(name);
      return;
    }
    const controller = typeof AbortController === "function" ? new AbortController() : null;
    controllers[name] = controller;
    Object.assign(lists[name], { status: "loading", items: [], scope });
    onChange(name);
    const current = () => mine === tokens[name] && getCase() === caseId && scopeNow() === scope;
    let items;
    let failed = false;
    try {
      items = await fetchers[name](caseId, controller ? controller.signal : undefined);
    } catch (err) {
      if (!current() || (err && err.name === "AbortError")) {
        return;
      }
      failed = true;
    }
    if (!current()) {
      return;
    }
    controllers[name] = null;
    Object.assign(lists[name], failed ? { status: "error", items: [] } : { status: "ready", items: Array.isArray(items) ? items : [] });
    onChange(name);
  }

  return {
    lists,
    load,
    // Drops every list (and its load on the way) that was asked for another scope.
    sync() {
      const now = scopeNow();
      for (const name of Object.keys(lists)) {
        if (lists[name].status !== "idle" && lists[name].scope !== now) {
          empty(name);
        }
      }
    },
    // Loads a list that is not loaded, failed, or was read for another scope; a loading or ready one of this scope is left alone.
    ensure(name) {
      const list = lists[name];
      if (list.status === "idle" || list.status === "error" || list.scope !== scopeNow()) {
        return load(name);
      }
      return Promise.resolve();
    },
    // The tab closed: a list still on the way is dropped (an answer would arrive for nothing); loaded ones stay.
    cancelPending() {
      for (const name of Object.keys(lists)) {
        if (lists[name].status === "loading") {
          empty(name);
        }
      }
    },
    // Forgets everything without touching the server.
    clear() {
      for (const name of Object.keys(lists)) {
        stop(name);
        Object.assign(lists[name], { status: "idle", items: [], scope: null });
      }
      onChange(null);
    },
  };
}

// ---- the comparison: ONE operation at a time
//
// An operation = { id, caseId, body, page } created by start(); every effect (the answer of the first page, of a later
// page, a failure) first checks that it is still THE operation (same object) of the same case. A newer start(), cancel(),
// reset() or a case change makes every earlier answer drop itself. The frozen `body` is what the result describes: later
// edits of the form do not change it, "Only differences" and a larger limit rerun it.
export function createCompareFlow({ api, getCase, onState, getViewKey = () => "" }) {
  const state = {
    status: "idle", // idle | loading | ok | too_large | error
    errors: [], // [{ side, key }] of the last submitted form that was refused
    error: null, // { code, kind, key } of a failed run
    cancelled: false,
    stale: false, // the view changed after the result was read
    viewKey: null, // the view the shown result was compared for (a rerun keeps the one of the frozen request)
    caseId: null,
    body: null,
    result: null, // buildResult(first page)
    rows: [], // rowView of every page loaded, in order
    nextOffset: null,
    loadingMore: false,
    moreError: null,
    changed: false, // a later page belongs to another comparison (the digest differs)
    names: null,
  };
  let current = null;
  let counter = 0;
  const emit = () => onState && onState(state);

  function drop() {
    if (current && current.controller) {
      current.controller.abort();
    }
    current = null;
  }

  function clearResult() {
    Object.assign(state, {
      errors: [], error: null, cancelled: false, stale: false, result: null, rows: [], nextOffset: null,
      loadingMore: false, moreError: null, changed: false, body: null, caseId: null, names: null, viewKey: null,
    });
  }

  function live(op) {
    return current === op && getCase() === op.caseId;
  }

  async function start(body, names, viewKey) {
    const caseId = getCase();
    drop();
    if (!caseId) {
      clearResult();
      state.status = "idle";
      emit();
      return;
    }
    counter += 1;
    const op = { id: counter, caseId, body, observed: getViewKey(), controller: typeof AbortController === "function" ? new AbortController() : null };
    current = op;
    clearResult();
    Object.assign(state, { status: "loading", caseId, body, names: names || null, viewKey, stale: viewKey !== getViewKey() });
    emit();
    let data;
    try {
      data = await api.compareViews.run(caseId, body, op.controller ? op.controller.signal : undefined);
    } catch (err) {
      if (!live(op) || (err && err.name === "AbortError")) {
        return;
      }
      current = null;
      Object.assign(state, { status: "error", error: classifyError(err) });
      emit();
      return;
    }
    if (!live(op)) {
      return;
    }
    current = null;
    const result = buildResult(data, body, names);
    const rows = (Array.isArray(data && data.rows) ? data.rows : []).map((row, i) => rowView(row, i));
    Object.assign(state, {
      status: result.truncatedSides.length > 0 ? "too_large" : "ok",
      result,
      rows,
      nextOffset: Number.isSafeInteger(data.next_offset) ? data.next_offset : null,
    });
    emit();
  }

  return {
    state,
    // spec: see buildRequest. `names` = { a, b } names of the chosen saved views (shown in the sides' headers).
    run(spec, names) {
      const built = buildRequest(spec);
      if (!built.ok) {
        // Nothing is sent and nothing shown is touched (neither the result nor an operation in progress): the form says what is wrong.
        state.errors = built.errors;
        emit();
        return Promise.resolve();
      }
      return start(built.body, names, getViewKey());
    },
    // The same comparison with other options (only differences, a larger limit): from the frozen request, never the form.
    rerun(patch) {
      if (state.body === null || state.caseId === null || state.caseId !== getCase()) {
        return Promise.resolve();
      }
      const body = { ...state.body, offset: 0 };
      if (patch.onlyDiff !== undefined) {
        body.only_diff = Boolean(patch.onlyDiff);
      }
      if (patch.maxSide !== undefined && MAX_SIDE_CHOICES.includes(patch.maxSide)) {
        body.max_side = patch.maxSide;
      }
      return start(body, state.names, state.viewKey);
    },
    // The next page of the rows of the result that is shown.
    async more() {
      if ((state.status !== "ok" && state.status !== "too_large") || state.nextOffset === null || state.loadingMore || state.changed) {
        return;
      }
      if (state.caseId !== getCase()) {
        return;
      }
      const offset = state.nextOffset;
      const digest = state.result.digest;
      const op = { id: ++counter, caseId: state.caseId, observed: getViewKey(), body: { ...state.body, offset }, controller: typeof AbortController === "function" ? new AbortController() : null };
      current = op;
      state.loadingMore = true;
      state.moreError = null;
      emit();
      let data;
      try {
        data = await api.compareViews.run(op.caseId, op.body, op.controller ? op.controller.signal : undefined);
      } catch (err) {
        if (!live(op) || (err && err.name === "AbortError")) {
          return;
        }
        current = null;
        Object.assign(state, { loadingMore: false, moreError: classifyError(err) });
        emit();
        return;
      }
      if (!live(op)) {
        return;
      }
      current = null;
      state.loadingMore = false;
      if (!data || data.digest !== digest || data.offset !== offset) {
        // Another comparison (the case was indexed meanwhile): mixing its rows with these would lie.
        state.changed = true;
        state.nextOffset = null;
        emit();
        return;
      }
      const base = state.rows.length;
      for (const [i, row] of (Array.isArray(data.rows) ? data.rows : []).entries()) {
        state.rows.push(rowView(row, base + i));
      }
      state.nextOffset = Number.isSafeInteger(data.next_offset) ? data.next_offset : null;
      emit();
    },
    // The user cancelled the one in progress (or the tab closed: quiet = true). What was shown before is gone because a
    // run clears it first, so the panel is idle again.
    cancel({ quiet = false } = {}) {
      if (current === null) {
        return;
      }
      const wasMore = state.loadingMore;
      drop();
      if (wasMore) {
        state.loadingMore = false;
      } else {
        clearResult();
        state.status = "idle";
        state.cancelled = !quiet;
      }
      emit();
    },
    // The view of the grid changed: a result read for the old one says so; an operation in progress is cancelled.
    viewChanged() {
      if (state.status === "idle") {
        return;
      }
      const now = getViewKey();
      // The out-of-date mark is the plain comparison with the view the result was read for (back to it = not out of date).
      const stale = state.viewKey !== now;
      // An operation in progress is cancelled only if the view changed after IT started (a rerun of an old result started
      // on the view that is shown now stays).
      if (current !== null && current.observed !== now) {
        const wasMore = state.loadingMore;
        drop();
        if (!wasMore) {
          clearResult();
          state.status = "idle";
          state.stale = true;
          emit();
          return;
        }
        state.loadingMore = false;
        state.stale = stale;
        emit();
        return;
      }
      if (stale !== state.stale) {
        state.stale = stale;
        emit();
      }
    },
    // Another case: nothing of the old one stays.
    reset() {
      drop();
      clearResult();
      state.status = "idle";
      emit();
    },
  };
}
