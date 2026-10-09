// Pure helpers and loading flows of the "Payload" section of the entry detail and of the
// "payload expected but absent" notice of the Operations panel (no DOM). Payload text, roots,
// operation names and reasons come from the ERS (untrusted): this module only shapes them.
// Every flow starts a new generation per request, aborts the previous one and drops answers of an
// older generation or of another case.
import { S, format } from "./strings.js";
import { formatTs } from "./time.js";

export const LARGE_PAYLOAD_CHARS = 200000; // above this the text is shown in chunks
export const CHUNK_CHARS = 50000;
export const ANNOUNCE_SLACK_MS = 2000; // the announcement is at most ~1 s before its payload record
export const MODES = ["formatted", "raw"];
export const LEVEL_PAGE_LIMIT = 1000;
const LEVEL_MAX_PAGES = 5;

// Log level order printed by the adapters themselves (docs/formato/payloads.md, section 2):
// DebugLog (3) < Warning (4) < Info (5) < Verbose (6). A payload is written when the level in force
// is at least Info, the MessageLogLevel of the operations in the observed ERS captures.
export const INFO_LEVEL_NUM = 5;
const LEVEL_BY_NAME = { debug: 3, debuglog: 3, warning: 4, info: 5, information: 5, verbose: 6 };

function errorText(error) {
  return error && error.message ? error.message : "";
}

function text(value) {
  return value === null || value === undefined ? "" : String(value);
}

// 1234567 -> "1,234,567" (locale independent).
export function groupDigits(n) {
  return String(Math.round(Number(n) || 0)).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

export function sizeText(chars) {
  return format(S.plSize, { size: groupDigits(chars) });
}

// Link confidence of a payload to its operation, written as text (colour never carries it).
export function confidenceText(confidence) {
  const name = S.plConfidenceNames[confidence];
  return name === undefined ? S.plConfidenceUnknown : name;
}

// Why a payload is shown as is / is an orphan: a known reason gets a sentence, any other text is kept.
export function reasonText(reason) {
  if (reason === null || reason === undefined || reason === "") {
    return "";
  }
  const known = S.plReasonNames[reason];
  return known === undefined ? String(reason) : known;
}

export function wellFormedText(value) {
  if (value === 1 || value === true) {
    return S.plWellFormedYes;
  }
  if (value === 0 || value === false) {
    return S.plWellFormedNo;
  }
  return S.plWellFormedUnknown;
}

// Chips for fmt / root / role / direction / peer / operation (empty values are left out).
export function buildChips(meta) {
  const specs = [
    ["fmt", S.plChipFmt], ["root", S.plChipRoot], ["role", S.plChipRole],
    ["direction", S.plChipDirection], ["peer", S.plChipPeer], ["operation", S.plChipOperation],
  ];
  const chips = [];
  for (const [field, template] of specs) {
    const value = text(meta && meta[field]);
    if (value !== "") {
      chips.push({ field, label: format(template, { value }) });
    }
  }
  return chips;
}

// Status line parts: well-formed, truncated, reason, size.
export function buildStatusParts(meta, formatted) {
  const parts = [wellFormedText(meta.well_formed), meta.truncated ? S.plTruncatedYes : S.plTruncatedNo];
  parts.push(sizeText(meta.char_len));
  const reasons = [];
  for (const reason of [meta.reason, formatted && !formatted.ok ? formatted.reason : null]) {
    const shown = reasonText(reason);
    if (shown !== "" && !reasons.includes(shown)) {
      reasons.push(shown);
    }
  }
  for (const shown of reasons) {
    parts.push(format(S.plReason, { reason: shown }));
  }
  if (formatted && formatted.truncated_display) {
    parts.push(S.plDisplayTruncated);
  }
  return parts;
}

// Items of GET /api/entries/<id>/payloads: payloads of the entry plus those it announces.
export function buildItems(data, entryId) {
  const payloads = data && Array.isArray(data.payloads) ? data.payloads : [];
  return payloads.map((meta) => ({
    id: meta.id,
    meta,
    announced: meta.entry_id !== entryId && meta.ref_entry_id === entryId,
    label: format(S.plItem, { id: meta.id, role: text(meta.role), direction: text(meta.direction) }),
  }));
}

// Which text a mode shows. Formatted needs a successful reformat; otherwise the raw text is shown.
export function displayView(data, mode) {
  if (!data) {
    return null;
  }
  const raw = text(data.text);
  const formatted = data.formatted && typeof data.formatted === "object" ? data.formatted : null;
  const formattedUsable = Boolean(formatted && formatted.ok === true && typeof formatted.text === "string");
  const showFormatted = mode === "formatted" && formattedUsable;
  return {
    raw,
    formatted: formattedUsable ? formatted.text : null,
    formattedUsable,
    shown: showFormatted ? "formatted" : "raw",
    text: showFormatted ? formatted.text : raw,
    statusParts: data.payload ? buildStatusParts(data.payload, formatted) : [],
  };
}

// Text in chunks that end at a line break when one is close (never splits a text into many tiny
// pieces: linear time). Texts up to LARGE_PAYLOAD_CHARS stay in one piece.
export function splitChunks(value, size = CHUNK_CHARS, large = LARGE_PAYLOAD_CHARS) {
  const source = text(value);
  if (source.length <= large) {
    return [source];
  }
  const chunks = [];
  let start = 0;
  while (start < source.length) {
    let end = Math.min(source.length, start + size);
    if (end < source.length) {
      const cut = source.lastIndexOf("\n", end - 1);
      if (cut > start + size / 2) {
        end = cut + 1;
      }
    }
    chunks.push(source.slice(start, end));
    start = end;
  }
  return chunks;
}

// Target for "Go to announcement": the normal record that announced the payload. Its time is a
// little before the payload record, so the cursor is moved back.
export function announcementTarget(meta, entryId) {
  if (!meta || meta.ref_entry_id === null || meta.ref_entry_id === undefined || meta.ref_entry_id === entryId) {
    return null;
  }
  const ts = meta.ts === null || meta.ts === undefined ? null : meta.ts - ANNOUNCE_SLACK_MS;
  return { id: meta.ref_entry_id, ts };
}

// Target of the record that carries the payload (the sensitive side for an announcing record).
export function carrierTarget(meta, entryId) {
  if (!meta || meta.entry_id === entryId) {
    return null;
  }
  return { id: meta.entry_id, ts: meta.ts === undefined ? null : meta.ts };
}

export function timeText(ts) {
  return formatTs(ts);
}

// Loads the payloads of the selected entry, then the text of the selected one (and, on request,
// of its pair). A new entry, a reset or a case change drops answers of older requests.
export function createPayloadFlow({ api, getCase, onState }) {
  let generation = 0;
  let controller = null;
  let detailGeneration = 0;
  let detailController = null;
  let pairGeneration = 0;
  let pairController = null;
  const state = {
    caseId: null,
    entryId: null,
    status: "idle", // idle | loading | ready | error
    items: [],
    error: null,
    selectedId: null,
    mode: "formatted",
    detail: { status: "none", data: null, error: null },
    pair: { open: false, status: "none", data: null, error: null },
  };

  function emit() {
    onState(state);
  }

  function abortPair() {
    pairGeneration += 1;
    if (pairController) {
      pairController.abort();
      pairController = null;
    }
    state.pair = { open: false, status: "none", data: null, error: null };
  }

  function abortDetail() {
    detailGeneration += 1;
    if (detailController) {
      detailController.abort();
      detailController = null;
    }
    abortPair();
    state.selectedId = null;
    state.detail = { status: "none", data: null, error: null };
  }

  function reset() {
    generation += 1;
    if (controller) {
      controller.abort();
      controller = null;
    }
    abortDetail();
    state.caseId = null;
    state.entryId = null;
    state.status = "idle";
    state.items = [];
    state.error = null;
    emit();
  }

  async function select(payloadId) {
    const caseId = state.caseId;
    if (!caseId || caseId !== getCase() || !state.items.some((item) => item.id === payloadId)) {
      return;
    }
    abortDetail();
    const token = detailGeneration;
    detailController = new AbortController();
    const signal = detailController.signal;
    state.selectedId = payloadId;
    state.detail = { status: "loading", data: null, error: null };
    emit();
    try {
      const data = await api.payload(caseId, payloadId, "pretty", signal);
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

  async function load(entryId) {
    reset();
    const caseId = getCase() || null;
    if (!caseId || entryId === null || entryId === undefined) {
      return;
    }
    const token = generation;
    controller = new AbortController();
    const signal = controller.signal;
    state.caseId = caseId;
    state.entryId = entryId;
    state.status = "loading";
    emit();
    try {
      const data = await api.entryPayloads(caseId, entryId, signal);
      if (token !== generation) {
        return;
      }
      state.items = buildItems(data, entryId);
      state.status = "ready";
      emit();
      if (state.items.length > 0) {
        const own = state.items.find((item) => !item.announced);
        await select((own || state.items[0]).id);
      }
    } catch (error) {
      if (token === generation && !signal.aborted) {
        state.status = "error";
        state.error = errorText(error);
        emit();
      }
    }
  }

  function setMode(mode) {
    if (MODES.includes(mode) && mode !== state.mode) {
      state.mode = mode;
      emit();
    }
  }

  // Opens / closes the pair next to the shown payload (the pair text is fetched on demand).
  async function togglePair() {
    const caseId = state.caseId;
    const data = state.detail.data;
    if (state.pair.open) {
      abortPair();
      emit();
      return;
    }
    const pair = data && data.pair ? data.pair : null;
    if (!caseId || caseId !== getCase() || !pair) {
      return;
    }
    abortPair();
    const token = pairGeneration;
    pairController = new AbortController();
    const signal = pairController.signal;
    state.pair = { open: true, status: "loading", data: null, error: null };
    emit();
    try {
      const answer = await api.payload(caseId, pair.id, "pretty", signal);
      if (token !== pairGeneration) {
        return;
      }
      state.pair = { open: true, status: "ready", data: answer, error: null };
      emit();
    } catch (error) {
      if (token === pairGeneration && !signal.aborted) {
        state.pair = { open: true, status: "error", data: null, error: errorText(error) };
        emit();
      }
    }
  }

  return { state, load, reset, select, setMode, togglePair };
}

// ---- "payload expected but absent by the level window" -------------------------------------------

// Rank of a level window event: by the printed level name when known, else by detail.level_num.
export function windowRank(event) {
  const detail = event && event.detail && typeof event.detail === "object" ? event.detail : {};
  const name = typeof detail.level_name === "string" ? detail.level_name.trim().toLowerCase() : "";
  if (Object.prototype.hasOwnProperty.call(LEVEL_BY_NAME, name)) {
    return LEVEL_BY_NAME[name];
  }
  return Number.isFinite(detail.level_num) ? detail.level_num : null;
}

function windowLabel(event) {
  const detail = event && event.detail && typeof event.detail === "object" ? event.detail : {};
  const name = typeof detail.level_name === "string" && detail.level_name !== "" ? detail.level_name : "";
  if (name !== "") {
    return name;
  }
  return Number.isFinite(detail.level_num) ? String(detail.level_num) : S.plNoticeLevelUnknown;
}

// The level window that covers an operation: starts at or before its start and ends at or after
// its end (or is still open). Windows of another stream are skipped when both ids are known.
export function coveringWindow(operation, windows) {
  if (!operation || operation.ts_start === null || operation.ts_start === undefined) {
    return null;
  }
  const start = operation.ts_start;
  const end = operation.ts_end === null || operation.ts_end === undefined ? start : Math.max(operation.ts_end, start);
  let best = null;
  for (const win of Array.isArray(windows) ? windows : []) {
    if (!win || win.ts_start === null || win.ts_start === undefined || win.ts_start > start) {
      continue;
    }
    if (win.ts_end !== null && win.ts_end !== undefined && win.ts_end < end) {
      continue;
    }
    if (win.stream_id !== null && win.stream_id !== undefined &&
        operation.stream_id !== null && operation.stream_id !== undefined && win.stream_id !== operation.stream_id) {
      continue;
    }
    if (best === null || win.ts_start > best.ts_start) {
      best = win;
    }
  }
  return best;
}

// {kind, text} or null. Only operations without a linked payload get a notice, and only when a
// level window covers them (without one the level in force is unknown).
//   kind "absent":       the window level is at least Info, so a payload should exist
//   kind "not_expected": the window level is below Info (below MessageLogLevel)
export function payloadNotice({ operation, payloadCount, windows }) {
  if (!operation || payloadCount > 0) {
    return null;
  }
  const win = coveringWindow(operation, windows);
  if (win === null) {
    return null;
  }
  const rank = windowRank(win);
  if (rank === null) {
    return null;
  }
  const level = windowLabel(win);
  if (rank >= INFO_LEVEL_NUM) {
    return { kind: "absent", text: format(S.plNoticeAbsent, { level }) };
  }
  return { kind: "not_expected", text: format(S.plNoticeNotExpected, { level }) };
}

// Checks the operation shown in the Operations detail: one request for "has a payload" plus the
// level windows of the case (cached per case; clearCache() after a re-index).
export function createPayloadNoticeFlow({ api, getCase, onResult }) {
  let generation = 0;
  let controller = null;
  const cache = new Map(); // case id -> Promise of the level windows

  async function fetchWindows(caseId) {
    const windows = [];
    let after = null;
    for (let page = 0; page < LEVEL_MAX_PAGES; page += 1) {
      const params = { kind: "level_window", limit: LEVEL_PAGE_LIMIT };
      if (after !== null) {
        params.after = after;
      }
      const data = await api.events(caseId, params);
      windows.push(...(data && Array.isArray(data.events) ? data.events : []));
      after = data && data.next !== undefined ? data.next : null;
      if (after === null) {
        break;
      }
    }
    return windows;
  }

  function windowsFor(caseId) {
    if (!cache.has(caseId)) {
      const pending = fetchWindows(caseId);
      cache.set(caseId, pending);
      pending.catch(() => cache.delete(caseId));
    }
    return cache.get(caseId);
  }

  function cancel() {
    generation += 1;
    if (controller) {
      controller.abort();
      controller = null;
    }
  }

  async function check(operation) {
    cancel();
    const caseId = getCase() || null;
    if (!caseId || !operation || operation.kind !== "operation" || operation.id === undefined) {
      onResult(null);
      return;
    }
    const token = generation;
    controller = new AbortController();
    const signal = controller.signal;
    try {
      const [page, windows] = await Promise.all([
        api.payloadPage(caseId, { event: operation.id, limit: 1 }, signal),
        windowsFor(caseId),
      ]);
      if (token !== generation || caseId !== getCase()) {
        return;
      }
      const count = page && Array.isArray(page.payloads) ? page.payloads.length : 0;
      onResult(payloadNotice({ operation, payloadCount: count, windows }));
    } catch (error) {
      if (token === generation && !signal.aborted) {
        onResult(null); // the notice is a hint: an error just hides it
      }
    }
  }

  function clearCache() {
    cache.clear();
  }

  return { check, cancel, clearCache };
}
