// Pure helpers and the loading flow of the "same key" panel (no DOM): every record that carries one
// correlation key, in all sources of the case, with keyset "load more". Texts come from the ERS
// (untrusted) and are only shaped here. A new key, a closed panel or a case change starts a new
// generation: answers of an older generation or of another case are dropped.
import { S, format } from "./strings.js";
import { formatTs } from "./time.js";

export const KEY_PAGE_LIMIT = 200;

export function buildKeyRow(record) {
  const tid = record.tid === null || record.tid === undefined ? "" : "0x" + Number(record.tid).toString(16);
  return {
    id: record.id,
    sourceId: record.source_id,
    ts: record.ts === undefined ? null : record.ts,
    time: formatTs(record.ts),
    source: record.member ? String(record.member) : "",
    sensitive: record.sensitive === true,
    tid,
    code: record.tpl_code || "",
    text: record.msg === null || record.msg === undefined ? "" : String(record.msg),
    sev: record.sev,
  };
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
