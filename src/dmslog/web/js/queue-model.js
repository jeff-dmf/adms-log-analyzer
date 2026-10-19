// "Message queue" panel (issue #74, docs/notas/fila-mensagens-desenho.md section 5): the pure model and the loading flow. No DOM.
//   GET    /api/queue/summary?case=      {enabled, dbs: [...], totals: {items, pending, sent, other}}
//   GET    /api/queue/items?case=&...    keyset page {items: [...], next}  (collection, db, state, type, q, limit, after)
//   GET    /api/queue/items/{id}/preview {item, form, available, reason, decode, reconstruction, formatted}
//   DELETE /api/queue?case=&group=       {dbs, items}
// Everything in the answers comes from a database of the ERS (untrusted): this module only shapes it, the panel writes it
// with textContent. Every answer carries the generation of the request that asked, so a new filter, a refresh, another
// preview or a case change drops the answers in flight. A failed request never empties what is on screen.
import { formatTs } from "./time.js";
import { S, format, pluralKey } from "./strings.js";

export const PAGE_LIMIT = 200;
export const COUNT_CAP = 1000; // the server counts log records up to this number per key
export const STATE_FILTERS = ["unsent", "sent", "all"];
export const FORMS = ["wire", "stored"];

const GUID = /^\{?[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}\}?$/i;
const DIGITS = /^\d{1,19}$/;
const present = (value) => value !== null && value !== undefined && String(value).trim() !== "";
const asCount = (value) => (typeof value === "number" && Number.isFinite(value) && value > 0 ? Math.floor(value) : 0);
const text = (value) => (value === null || value === undefined ? "" : String(value));
const group = (n) => asCount(n).toLocaleString("en-US");

export function defaultFilters() {
  return { state: "unsent", type: "", collection: "", db: "", q: "" };
}

// "Not sent" is everything the adapter has not confirmed (pending and other); "All" sends no state.
export function stateParam(filter) {
  if (filter === "sent") {
    return "sent";
  }
  if (filter === "all") {
    return "";
  }
  return "pending,other";
}

// The message ID / item ID box: a GUID (with or without braces) or digits. "" is fine (no search).
export function queryProblem(value) {
  const q = text(value).trim();
  if (q === "" || GUID.test(q) || DIGITS.test(q)) {
    return "";
  }
  return S.mqBadQuery;
}

// Query of GET /api/queue/items.
export function buildItemsParams(filters, after, limit = PAGE_LIMIT) {
  const f = filters || {};
  const params = { limit };
  const state = stateParam(f.state || "unsent");
  if (state !== "") {
    params.state = state;
  }
  for (const key of ["collection", "db", "type", "q"]) {
    if (present(f[key])) {
      params[key] = String(f[key]).trim();
    }
  }
  if (present(after)) {
    params.after = after;
  }
  return params;
}

export function formatSize(bytes) {
  if (typeof bytes !== "number" || !Number.isFinite(bytes) || bytes < 0) {
    return "";
  }
  if (bytes < 1024) {
    return group(bytes) + " B";
  }
  if (bytes < 1024 * 1024) {
    return (Math.round((bytes / 1024) * 10) / 10).toLocaleString("en-US") + " KB";
  }
  return (Math.round((bytes / (1024 * 1024)) * 10) / 10).toLocaleString("en-US") + " MB";
}

// A count of log records for a key: capped by the server at COUNT_CAP.
export function countText(n) {
  const count = asCount(n);
  return count >= COUNT_CAP ? group(COUNT_CAP) + "+" : group(count);
}

export function abbreviateId(value) {
  const id = text(value).replace(/[{}]/g, "");
  return id.length > 13 ? id.slice(0, 8) + "…" + id.slice(-4) : id;
}

export function stateLabel(state) {
  return S.mqStateNames[state] || text(state);
}

// ---- the databases ------------------------------------------------------------------------------------------------

export function warningText(code) {
  return S.mqWarningNames[code] || text(code);
}

export function dbErrorText(code) {
  return S.mqErrorNames[code] || text(code);
}

// One database as plain data. `status` is "ok" | "not_a_queue" | "error" (anything else counts as ok).
export function buildDbRow(db) {
  const d = db && typeof db === "object" ? db : {};
  const status = d.status === "not_a_queue" || d.status === "error" ? d.status : "ok";
  // The way the database was read (snapshot) is a warning when it was not the normal one, then the reader's own warnings.
  const codes = [];
  for (const code of [d.snapshot === "no_wal" || d.snapshot === "wal_failed" ? d.snapshot : "",
    ...(Array.isArray(d.warnings) ? d.warnings : []).map((w) => (typeof w === "string" ? w : w && w.code))]) {
    if (present(code) && !codes.includes(code)) {
      codes.push(code);
    }
  }
  const warnings = status === "error" ? [] : codes.map(warningText);
  const items = asCount(d.items);
  const unsent = asCount(d.pending) + asCount(d.other);
  return {
    id: d.id,
    tag: text(d.tag),
    name: text(d.name),
    adapter: text(d.adapter),
    cache: text(d.cache),
    status,
    error: status === "error" || present(d.error) ? dbErrorText(d.error || "") : "",
    warnings,
    items,
    unsent,
    isQueue: status !== "not_a_queue",
    summary: status === "not_a_queue"
      ? text(d.name)
      : [text(d.tag), text(d.adapter), text(d.cache)].filter((x) => x !== "").join(" · ")
        + " — " + format(S[pluralKey("mqDbItems", items)], { count: group(items) })
        + ", " + format(S.mqDbUnsent, { count: group(unsent) }),
  };
}

// The view of a summary answer: the counters, the database rows and the filter choices.
export function buildSummaryView(summary) {
  const s = summary && typeof summary === "object" ? summary : {};
  const totals = s.totals && typeof s.totals === "object" ? s.totals : {};
  const dbs = (Array.isArray(s.dbs) ? s.dbs : []).map(buildDbRow);
  const collections = [];
  for (const db of Array.isArray(s.dbs) ? s.dbs : []) {
    if (db && present(db.collection) && !collections.some((c) => c.id === String(db.collection))) {
      collections.push({ id: String(db.collection), tag: text(db.tag) });
    }
  }
  return {
    enabled: s.enabled === true,
    dbs,
    queues: dbs.filter((d) => d.isQueue),
    others: dbs.filter((d) => !d.isQueue),
    collections,
    items: asCount(totals.items),
    unsent: asCount(totals.pending) + asCount(totals.other),
    sent: asCount(totals.sent),
  };
}

export function headerText(view) {
  return format(S.mqCountUnsent, { count: group(view.unsent) }) + " · " + format(S[pluralKey("mqCountItems", view.items)], { count: group(view.items) });
}

// Why there is no list, or "" when there is one to show: disabled | no_dbs | empty | filtered.
export function emptyKind(view, itemsShown, filtered) {
  if (view === null) {
    return "";
  }
  if (!view.enabled && view.queues.length === 0) {
    return "disabled";
  }
  if (view.queues.length === 0) {
    return "no_dbs";
  }
  if (view.items === 0) {
    return "empty";
  }
  if (itemsShown === 0 && filtered) {
    return "filtered";
  }
  return "";
}

export function emptyText(kind) {
  return { disabled: S.mqDisabled, no_dbs: S.mqNoDbs, empty: S.mqEmptyQueues, filtered: S.mqNoMatch }[kind] || "";
}

export function isFiltered(filters) {
  const f = filters || defaultFilters();
  return f.state !== "unsent" || present(f.type) || present(f.collection) || present(f.db) || present(f.q);
}

// ---- the items ----------------------------------------------------------------------------------------------------

// One item of the list as plain data. A malformed item (no id) gives null and is skipped.
export function buildItemRow(item) {
  if (!item || typeof item !== "object" || !Number.isSafeInteger(item.id)) {
    return null;
  }
  const log = item.log && typeof item.log === "object" ? item.log : {};
  const msgid = asCount(log.msgid);
  const imId = asCount(log.im_id);
  const ts = typeof item.ts_adjusted === "number" ? item.ts_adjusted : item.ts;
  const payloadId = Number.isSafeInteger(log.payload_id) ? log.payload_id : null;
  return {
    id: item.id,
    time: typeof ts === "number" ? formatTs(ts) : "",
    tag: text(item.tag),
    type: text(item.msg_type),
    state: text(item.state),
    stateLabel: stateLabel(item.state),
    messageId: text(item.message_id),
    messageShort: abbreviateId(item.message_id),
    itemId: text(item.item_id),
    size: typeof item.size === "number" ? item.size : null,
    sizeText: formatSize(item.size),
    preview: item.preview === "too_large" || item.preview === "no_body" ? item.preview : "available",
    inLog: countText(msgid),
    inLogCount: msgid,
    canShowLog: msgid > 0 && present(item.message_id),
    canShowCache: imId > 0 && present(item.item_id),
    payloadId,
  };
}

export function rowLabel(row) {
  return format(S.mqRowLabel, { id: row.itemId, type: row.type || S.mqNoType, state: row.stateLabel });
}

export function countLine(shown, hasMore) {
  const n = asCount(shown);
  return format(S[pluralKey(hasMore ? "mqShownMore" : "mqShown", n)], { count: group(n) });
}

// ---- the preview --------------------------------------------------------------------------------------------------

export function noteText(note) {
  const code = typeof note === "string" ? note : note && note.code;
  return S.mqNoteNames[code] || text(code);
}

// What the reconstruction was based on: "exemplars" (k payloads logged in the case), "catalog" or "none".
export function basisKind(reconstruction) {
  const basis = reconstruction && typeof reconstruction.basis === "string" ? reconstruction.basis.toLowerCase() : "";
  if (basis === "" || basis === "none") {
    return "none";
  }
  return basis.includes("catalog") ? "catalog" : "exemplars";
}

export function basisText(reconstruction) {
  const kind = basisKind(reconstruction);
  if (kind === "catalog") {
    return S.mqBasisCatalog;
  }
  if (kind === "none") {
    return S.mqBasisNone;
  }
  const raw = reconstruction.exemplars;
  const count = Array.isArray(raw) ? raw.length : asCount(raw);
  return format(S[pluralKey("mqBasisPayloads", count)], { count: group(count) });
}

// The preview answer as plain data. kind: "ok" (XML to show) | "unavailable" | "decode_error".
export function buildPreviewView(data, row) {
  const d = data && typeof data === "object" ? data : {};
  const form = d.form === "stored" ? "stored" : "wire";
  const base = { form, label: form === "wire" ? S.mqReconstructionLabel : S.mqStoredNote, basis: "", notes: [], text: "", truncated: false };
  if (d.available === false) {
    const sizeText = row ? row.sizeText : "";
    return {
      ...base,
      kind: "unavailable",
      message: d.reason === "too_large" ? format(S.mqTooLarge, { size: sizeText || "—" }) : S.mqNoBody,
    };
  }
  if (d.decode && d.decode.ok === false) {
    return { ...base, kind: "decode_error", message: format(S.mqDecodeFailed, { code: text(d.decode.error) || "?" }) };
  }
  const reconstruction = d.reconstruction && typeof d.reconstruction === "object" ? d.reconstruction : null;
  const formatted = d.formatted && typeof d.formatted === "object" ? d.formatted : {};
  return {
    ...base,
    kind: "ok",
    basis: form === "wire" ? basisText(reconstruction) : "",
    notes: form === "wire" && reconstruction && Array.isArray(reconstruction.notes) ? reconstruction.notes.map(noteText) : [],
    text: text(formatted.text),
    truncated: formatted.truncated_display === true,
  };
}

// ---- the flow -----------------------------------------------------------------------------------------------------

const errorText = (error) => (error && error.message ? error.message : "");
const isAbort = (error) => Boolean(error && error.name === "AbortError");

// Options: api {queueSummary(caseId, signal), queueItems(caseId, params, signal), queuePreview(caseId, id, form, signal),
// queueRemove(caseId, group)}, getCase() -> case id | null, onState(state), limit.
export function createQueueFlow({ api, getCase, onState, limit = PAGE_LIMIT }) {
  let generation = 0; // summary + list (a refresh, a filter change, a case change)
  let previewGeneration = 0;
  let controller = null;
  let previewController = null;
  let seen = new Set();
  const state = {
    caseId: null,
    status: "nocase", // nocase | loading | ready | error
    error: null, // the load failed and there is nothing to show
    refreshError: null, // a later load failed: what was on screen stays
    summary: null, // buildSummaryView(...) or null
    filters: defaultFilters(),
    types: [], // message types seen so far in this case (the Type selector)
    items: [],
    version: 0, // changes whenever `items` is not just the old list plus new rows at the end
    next: null,
    loadingMore: false,
    moreError: null,
    selectedId: null,
    preview: { status: "idle", form: "wire", view: null, error: null }, // idle | loading | ready | error
    removing: false,
    removeError: null,
    removed: null, // {dbs, items} of the last removal
    reading: false, // the host is reading the databases (a job)
  };
  const emit = () => onState(state);

  function stop() {
    generation += 1;
    if (controller) {
      controller.abort();
      controller = null;
    }
    state.loadingMore = false;
  }

  function stopPreview() {
    previewGeneration += 1;
    if (previewController) {
      previewController.abort();
      previewController = null;
    }
  }

  function take(rows) {
    for (const row of rows) {
      if (!seen.has(row.id)) {
        seen.add(row.id);
        state.items.push(row);
        if (row.type !== "" && !state.types.includes(row.type)) {
          state.types.push(row.type);
        }
      }
    }
    state.types.sort();
  }

  function pageOf(data) {
    const rows = (data && Array.isArray(data.items) ? data.items : []).map(buildItemRow).filter((row) => row !== null);
    const next = data && data.next !== undefined && data.next !== null && data.next !== "" ? data.next : null;
    return { rows, next };
  }

  // Reads the summary and the first page. `applied` are the filters that produced the list on screen; a failure of a
  // filter change puts them back (the selectors must not claim a list that is not there).
  async function load({ applied = null, keepSelection = false } = {}) {
    stop();
    const token = generation;
    controller = new AbortController();
    const signal = controller.signal;
    state.caseId = getCase() || null;
    state.refreshError = null;
    state.error = null;
    state.moreError = null;
    if (!state.caseId) {
      state.status = "nocase";
      state.summary = null;
      state.items = [];
      state.version += 1;
      state.next = null;
      seen = new Set();
      emit();
      return;
    }
    const firstLoad = state.summary === null;
    if (firstLoad) {
      state.status = "loading";
    }
    emit();
    const caseId = state.caseId;
    try {
      const [summary, data] = await Promise.all([
        api.queueSummary(caseId, signal),
        api.queueItems(caseId, buildItemsParams(state.filters, null, limit), signal),
      ]);
      if (token !== generation) {
        return;
      }
      state.summary = buildSummaryView(summary);
      const page = pageOf(data);
      state.items = [];
      seen = new Set();
      take(page.rows);
      state.next = page.next;
      state.version += 1;
      state.status = "ready";
      if (!keepSelection || (state.selectedId !== null && !seen.has(state.selectedId))) {
        closePreviewState(); // the open item left the list (or this is a new case): its preview goes with it
      }
      emit();
    } catch (error) {
      if (token !== generation || isAbort(error)) {
        return;
      }
      if (applied !== null) {
        state.filters = applied;
      }
      if (state.summary === null) {
        state.status = "error";
        state.error = errorText(error) || S.mqLoadError;
      } else {
        state.refreshError = errorText(error) || S.mqRefreshError;
      }
      emit();
    }
  }

  function closePreviewState() {
    stopPreview();
    state.selectedId = null;
    state.preview = { status: "idle", form: state.preview.form, view: null, error: null };
  }

  function refresh() {
    return load({ keepSelection: true });
  }

  function reset() {
    state.filters = defaultFilters();
    state.types = [];
    state.summary = null;
    state.removed = null;
    state.removeError = null;
    state.reading = false;
    closePreviewState();
    return load();
  }

  async function loadMore() {
    if (state.status !== "ready" || state.loadingMore || state.next === null || !state.caseId) {
      return;
    }
    const token = generation;
    const signal = controller ? controller.signal : undefined;
    state.loadingMore = true;
    state.moreError = null;
    emit();
    try {
      const data = await api.queueItems(state.caseId, buildItemsParams(state.filters, state.next, limit), signal);
      if (token !== generation) {
        return;
      }
      const page = pageOf(data);
      take(page.rows);
      state.next = page.next;
      state.loadingMore = false;
      emit();
    } catch (error) {
      if (token === generation && !isAbort(error)) {
        state.loadingMore = false;
        state.moreError = errorText(error) || S.mqMoreError;
        emit();
      }
    }
  }

  // patch: any of state, type, collection, db, q. Everything is applied by the server; a bad search is refused here.
  function setFilters(patch) {
    const applied = state.filters;
    const next = { ...applied, ...patch };
    if (patch && Object.prototype.hasOwnProperty.call(patch, "q")) {
      next.q = text(patch.q).trim();
      const problem = queryProblem(next.q);
      if (problem !== "") {
        state.refreshError = problem;
        emit();
        return Promise.resolve();
      }
    }
    if (JSON.stringify(next) === JSON.stringify(applied)) {
      return Promise.resolve();
    }
    state.filters = next;
    closePreviewState();
    return load({ applied });
  }

  async function select(id, form = state.preview.form) {
    const row = state.items.find((item) => item.id === id);
    if (!row || !state.caseId) {
      return;
    }
    stopPreview();
    const token = previewGeneration;
    previewController = new AbortController();
    state.selectedId = id;
    state.preview = { status: "loading", form, view: null, error: null };
    emit();
    try {
      const data = await api.queuePreview(state.caseId, id, form, previewController.signal);
      if (token !== previewGeneration) {
        return;
      }
      state.preview = { status: "ready", form, view: buildPreviewView({ ...data, form }, row), error: null };
      emit();
    } catch (error) {
      if (token === previewGeneration && !isAbort(error)) {
        state.preview = { status: "error", form, view: null, error: errorText(error) || S.mqPreviewError };
        emit();
      }
    }
  }

  function setForm(form) {
    if (!FORMS.includes(form) || state.selectedId === null || form === state.preview.form) {
      return Promise.resolve();
    }
    return select(state.selectedId, form);
  }

  function closePreview() {
    closePreviewState();
    emit();
  }

  // Removes the queue data of the case (every collection, or only `groupId`), then reloads.
  async function removeData(groupId) {
    if (state.removing || !state.caseId) {
      return false;
    }
    const caseId = state.caseId;
    state.removing = true;
    state.removeError = null;
    state.removed = null;
    emit();
    try {
      const result = await api.queueRemove(caseId, groupId === undefined ? null : groupId);
      if (caseId !== state.caseId) {
        return false;
      }
      state.removing = false;
      state.removed = { dbs: asCount(result && result.dbs), items: asCount(result && result.items) };
      closePreviewState();
      await load();
      return true;
    } catch (error) {
      if (caseId === state.caseId) {
        state.removing = false;
        state.removeError = errorText(error) || S.mqRemoveError;
        emit();
      }
      return false;
    }
  }

  function setReading(on) {
    state.reading = Boolean(on);
    emit();
  }

  return { state, refresh, reset, loadMore, setFilters, select, setForm, closePreview, removeData, setReading };
}
