// Pure helpers and the loading flow of the payload list panel (no DOM): GET /api/payloads/page
// with filters and keyset "load more". Operation names, peers, roots and reasons come from the ERS
// (untrusted) and are only shaped here. A new filter set, a refresh or a case change starts a new
// generation: answers of an older generation or of another case are dropped.
import { groupDigits, reasonText } from "./payload-model.js";
import { S } from "./strings.js";
import { formatTs } from "./time.js";

export const PAYLOAD_PAGE_LIMIT = 200;
export const PAYLOAD_ROLES = ["request", "response", "fault", "delta", "content", "publication", "other"];
export const PAYLOAD_FORMATS = ["xml", "xml_seq", "html", "json", "text"];
export const PAYLOAD_DIRECTIONS = ["in", "out"];
export const EMPTY_FILTERS = { role: "", direction: "", peer: "", operation: "", fmt: "", q: "", orphan: false };

// Query parameters of GET /api/payloads/page. Lists are comma separated; empty values are left out.
export function buildPayloadParams(filters, after, limit = PAYLOAD_PAGE_LIMIT) {
  const f = filters || {};
  const params = { limit };
  for (const key of ["role", "direction", "peer", "operation", "fmt", "q"]) {
    const raw = f[key];
    if (raw === null || raw === undefined) {
      continue;
    }
    const value = String(raw)
      .split(key === "operation" || key === "q" ? "\u0000" : ",")
      .map((part) => part.trim())
      .filter((part) => part !== "")
      .join(",");
    if (value !== "") {
      params[key] = value;
    }
  }
  if (f.orphan) {
    params.orphan = 1;
  }
  if (after !== null && after !== undefined && after !== "") {
    params.after = after;
  }
  return params;
}

export function buildPayloadRow(meta) {
  const orphan = meta.event_id === null || meta.event_id === undefined;
  const reason = reasonText(meta.reason);
  return {
    id: meta.id,
    entryId: meta.entry_id,
    ts: meta.ts === undefined ? null : meta.ts,
    time: formatTs(meta.ts),
    role: meta.role ? String(meta.role) : "",
    direction: meta.direction ? String(meta.direction) : "",
    peer: meta.peer ? String(meta.peer) : "",
    operation: meta.operation ? String(meta.operation) : orphan ? S.plgNoOperation : "",
    fmt: meta.fmt ? String(meta.fmt) : "",
    size: groupDigits(meta.char_len),
    side: meta.sensitive === true ? S.plgSideSensitive : meta.sensitive === false ? S.plgSideNormal : "",
    member: meta.member ? String(meta.member) : "",
    orphan,
    note: orphan ? reason : "",
  };
}

function errorText(error) {
  return error && error.message ? error.message : "";
}

export function createPayloadListFlow({ api, getCase, onState, limit = PAYLOAD_PAGE_LIMIT }) {
  let generation = 0;
  let controller = null;
  const state = {
    caseId: null,
    filters: { ...EMPTY_FILTERS },
    payloads: [],
    next: null,
    status: "idle", // idle | nocase | loading | ready | error
    loadingMore: false,
    error: null,
  };

  function emit() {
    onState(state);
  }

  function stop() {
    generation += 1;
    if (controller) {
      controller.abort();
      controller = null;
    }
  }

  function close() {
    stop();
    state.caseId = null;
    state.payloads = [];
    state.next = null;
    state.status = "idle";
    state.loadingMore = false;
    state.error = null;
    emit();
  }

  async function refresh() {
    stop();
    const token = generation;
    const caseId = getCase() || null;
    state.caseId = caseId;
    state.payloads = [];
    state.next = null;
    state.loadingMore = false;
    state.error = null;
    state.status = caseId ? "loading" : "nocase";
    emit();
    if (!caseId) {
      return;
    }
    controller = new AbortController();
    const signal = controller.signal;
    try {
      const data = await api.payloadPage(caseId, buildPayloadParams(state.filters, null, limit), signal);
      if (token !== generation) {
        return;
      }
      state.payloads = Array.isArray(data.payloads) ? data.payloads : [];
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
      const data = await api.payloadPage(caseId, buildPayloadParams(state.filters, state.next, limit), signal);
      if (token !== generation) {
        return;
      }
      const known = new Set(state.payloads.map((item) => item.id));
      for (const item of Array.isArray(data.payloads) ? data.payloads : []) {
        if (!known.has(item.id)) {
          state.payloads.push(item);
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

  function setFilters(patch) {
    state.filters = { ...state.filters, ...patch };
    return refresh();
  }

  return { state, refresh, loadMore, setFilters, close };
}
