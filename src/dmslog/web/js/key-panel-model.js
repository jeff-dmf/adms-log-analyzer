// Pure helpers and the loading flow of the "same key" panel (no DOM): every record that carries one
// correlation key, in all sources of the case, with keyset "load more". Texts come from the ERS
// (untrusted) and are only shaped here. A new key, a closed panel or a case change starts a new
// generation: answers of an older generation or of another case are dropped.
import { isSensitiveName } from "./grid-model.js";
import { baseName } from "./source-filter-model.js";
import { S, format, pluralKey } from "./strings.js";
import { formatTs } from "./time.js";

export const KEY_PAGE_LIMIT = 200;

// One row of the panel, the columns of the grid: Time, Source (file name, never the path; "S" badge when sensitive),
// Level, Message type (readable title, never the template code) and Message. lookups (all optional):
//   getSourceName(sourceId) -> the source's name as the case lists it, or undefined
//   getTypeLabel(tplId)     -> the readable title of the message type, or "" when unknown
export function buildKeyRow(record, lookups = {}) {
  const known = typeof lookups.getSourceName === "function" ? lookups.getSourceName(record.source_id) : undefined;
  // An unknown source is named by its id: then the member of the record is the better name.
  const unknown = known === undefined || known === null || known === "" || String(known) === String(record.source_id);
  const raw = unknown ? record.member : known;
  const full = raw === null || raw === undefined ? "" : baseName(raw);
  const sensitive = record.sensitive === true || isSensitiveName(full);
  const label = typeof lookups.getTypeLabel === "function" && Number.isInteger(record.tpl_id) && record.tpl_id !== 0
    ? lookups.getTypeLabel(record.tpl_id)
    : "";
  return {
    id: record.id,
    sourceId: record.source_id,
    ts: record.ts === undefined ? null : record.ts,
    time: formatTs(record.ts),
    source: sensitive ? full.replace(/\.sensitive(?=\.log)/i, "") : full,
    sourceTitle: sensitive && full ? full + " (" + S.sourceSensitive + ")" : full,
    sensitive,
    level: Number.isInteger(record.level) && S.levelNames[record.level] ? S.levelNames[record.level] : "",
    type: label === null || label === undefined ? "" : String(label),
    text: record.msg === null || record.msg === undefined ? "" : String(record.msg),
    sev: record.sev,
  };
}

// The filter "Show in grid" applies: records that carry this key (same shape as the "key" chip).
export function keyFilter(type, value) {
  return { key: { type: String(type), eq: String(value) } };
}

// What "Show in grid" does with the views of the strip: "narrow" (the current view has conditions of its own: they become
// the parent and the key filter opens a child view), "none" (this view already is that key: a second click must not stack
// an identical view), "in-place" (nothing to narrow, or no chained views: the key joins
// the conditions of this view) or "full" (the stack is full: say so, change nothing). Measures that are not finite
// numbers count as "no stack".
export function planShowInGrid({ chained, ownEmpty, depth, maxDepth, alreadyShown = false }) {
  if (alreadyShown === true) {
    return "none";
  }
  if (chained !== true || ownEmpty === true || !Number.isFinite(depth) || !Number.isFinite(maxDepth)) {
    return "in-place";
  }
  return depth + 1 >= maxDepth ? "full" : "narrow";
}

// "N records so far" / "N records", with the right plural (and a thousands separator).
export function keyCountText(count, more) {
  return format(S[pluralKey(more ? "keyCountMore" : "keyCount", count)], { count: Number.isFinite(count) ? count : 0 });
}

export function keyHeading(type, value) {
  return format(S.keyHeading, { type, value });
}

function errorText(error) {
  return error && error.message ? error.message : "";
}

export function createKeyFlow({ api, getCase, onState, limit = KEY_PAGE_LIMIT }) {
  let generation = 0;
  let controller = null;
  const state = {
    caseId: null,
    type: null,
    value: null,
    records: [],
    next: null,
    status: "idle", // idle | nocase | loading | ready | error
    loadingMore: false,
    error: null,
  };

  function emit() {
    onState(state);
  }

  function close() {
    generation += 1;
    if (controller) {
      controller.abort();
      controller = null;
    }
    state.caseId = null;
    state.type = null;
    state.value = null;
    state.records = [];
    state.next = null;
    state.status = "idle";
    state.loadingMore = false;
    state.error = null;
    emit();
  }

  async function open(type, value) {
    close();
    const caseId = getCase() || null;
    state.type = type;
    state.value = value;
    if (!caseId) {
      state.status = "nocase";
      emit();
      return;
    }
    const token = generation;
    controller = new AbortController();
    const signal = controller.signal;
    state.caseId = caseId;
    state.status = "loading";
    emit();
    try {
      const data = await api.keyRecords(caseId, type, value, { limit }, signal);
      if (token !== generation) {
        return;
      }
      state.records = Array.isArray(data.records) ? data.records : [];
      state.next = data.next === undefined ? null : data.next;
      state.status = "ready";
      emit();
    } catch (error) {
      if (token === generation && !signal.aborted) {
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
    const token = generation;
    const caseId = state.caseId;
    const signal = controller.signal;
    state.loadingMore = true;
    emit();
    try {
      const data = await api.keyRecords(caseId, state.type, state.value, { limit, after: state.next }, signal);
      if (token !== generation) {
        return;
      }
      const known = new Set(state.records.map((record) => record.id));
      for (const record of Array.isArray(data.records) ? data.records : []) {
        if (!known.has(record.id)) {
          state.records.push(record);
        }
      }
      state.next = data.next === undefined ? null : data.next;
      state.loadingMore = false;
      emit();
    } catch (error) {
      if (token === generation && !signal.aborted) {
        state.loadingMore = false;
        state.status = "error";
        state.error = errorText(error);
        emit();
      }
    }
  }

  return { state, open, close, loadMore };
}
