// Pure helpers and the loading flow of the "Operations and handoffs" section of the "same key" panel (no DOM): the
// operations that carry one correlation key and the handoffs between its records, in time order. Names come from the ERS
// (untrusted): this module only shapes them. A new key, a closed panel or a case change starts a new generation: answers
// of an older generation or of another case are dropped.
import { formatDuration, gridTarget, statusLabel, statusTone } from "./events-model.js";
import { S, format } from "./strings.js";
import { formatTs } from "./time.js";

export const CHAIN_FAILED_STATUSES = ["fail", "timeout", "business_error"];

// A string of the UI; the fallback keeps the section working while strings.js has no entry yet.
function text(key, fallback) {
  const value = S[key];
  return typeof value === "string" && value !== "" ? value : fallback;
}

function plural(base, n, one, other) {
  return format(text(base + (n === 1 ? "One" : "Other"), n === 1 ? one : other), { count: n });
}

function numberOrNull(value) {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

// "DMS_FunctionHost #7" for a pooled process, else the process name; "" when there is none.
export function chainProcessLabel(item) {
  if (!item || typeof item !== "object") {
    return "";
  }
  const process = item.process === null || item.process === undefined ? "" : String(item.process);
  if (item.pool && Number.isInteger(item.instance)) {
    return format(text("poolInstance", "{pool} #{instance}"), { pool: item.pool, instance: item.instance });
  }
  return process;
}

function withCollection(label, collection, multi) {
  return multi && collection !== null && collection !== undefined ? "C" + collection + " · " + label : label;
}

function confidenceText(confidence) {
  const names = S.lnkConfidenceNames || {};
  return names[confidence] === undefined ? "" : String(names[confidence]);
}

function chainOrder(a, b) {
  if (a.collection !== b.collection) {
    return (a.collection === null ? Infinity : a.collection) - (b.collection === null ? Infinity : b.collection);
  }
  if (a.ts !== b.ts) {
    if (a.ts === null) {
      return 1;
    }
    if (b.ts === null) {
      return -1;
    }
    return a.ts - b.ts;
  }
  if (a.kind !== b.kind) {
    return a.kind === "handoff" ? -1 : 1; // a handoff comes before the operation it starts
  }
  return a.index - b.index;
}

// Rows of the table, the summary text and the first failure, from the answer of GET /api/keys/<type>/<value>/chain.
// -> { rows, summary, failure, multi }; rows are empty when the key has no operation and no handoff.
export function buildChain(data) {
  const operations = data && Array.isArray(data.operations) ? data.operations : [];
  const handoffs = data && Array.isArray(data.handoffs) ? data.handoffs : [];
  const groups = new Set();
  for (const event of operations) {
    groups.add(event && event.collection !== undefined ? event.collection : null);
  }
  for (const link of handoffs) {
    groups.add(link && link.from && link.from.collection !== undefined ? link.from.collection : null);
  }
  const multi = groups.size > 1;
  const rows = [];
  operations.forEach((event, index) => {
    if (!event || typeof event !== "object") {
      return;
    }
    const collection = event.collection === undefined ? null : event.collection;
    const status = event.status === undefined ? null : event.status;
    const start = numberOrNull(event.ts_start);
    const end = numberOrNull(event.ts_end);
    rows.push({
      kind: "operation",
      index,
      collection,
      ts: start,
      end: end !== null ? end : start,
      time: formatTs(event.ts_start),
      process: withCollection(chainProcessLabel(event), collection, multi),
      processBare: chainProcessLabel(event),
      step: event.name ? String(event.name) : S.evUnnamed,
      status,
      statusText: statusLabel(status),
      tone: statusTone(status),
      duration: formatDuration(event.duration_ms),
      records: event.n_entries === null || event.n_entries === undefined ? "" : String(event.n_entries),
      eventId: event.id,
      target: gridTarget(event),
      entryId: null,
      entryTs: null,
    });
  });
  handoffs.forEach((link, index) => {
    if (!link || typeof link !== "object" || !link.from || !link.to) {
      return;
    }
    const collection = link.from.collection === undefined ? null : link.from.collection;
    const label = chainProcessLabel(link.from) + " → " + chainProcessLabel(link.to);
    rows.push({
      kind: "handoff",
      index,
      collection,
      ts: numberOrNull(link.from.ts),
      end: numberOrNull(link.to.ts),
      time: formatTs(link.from.ts),
      process: withCollection(label, collection, multi),
      processBare: label,
      step: text("chainHandoff", "Handoff"),
      status: null,
      statusText: confidenceText(link.confidence),
      tone: "neutral",
      duration: formatDuration(link.dt_ms),
      records: "",
      eventId: null,
      target: null,
      entryId: Number.isInteger(link.to.entry_id) ? link.to.entry_id : null,
      entryTs: numberOrNull(link.to.ts),
    });
  });
  rows.sort(chainOrder);
  const failedRow = rows.find((row) => row.kind === "operation" && CHAIN_FAILED_STATUSES.includes(row.status)) || null;
  return {
    rows,
    multi,
    failure: failedRow
      ? format(text("chainFailedAt", "Failed at {name} ({process})"), { name: failedRow.step, process: failedRow.processBare })
      : "",
    summary: chainSummary(rows, multi),
  };
}

// "3 operations, 2 handoffs, 310 ms from first to last"; the span is left out with several collections (each has its own
// clock) and when there is a single moment only.
export function chainSummary(rows, multi) {
  if (rows.length === 0) {
    return "";
  }
  const nOps = rows.filter((row) => row.kind === "operation").length;
  const nLinks = rows.length - nOps;
  let line = format(text("chainSummary", "{operations}, {handoffs}"), {
    operations: plural("chainSummaryOperations", nOps, "{count} operation", "{count} operations"),
    handoffs: plural("chainSummaryHandoffs", nLinks, "{count} handoff", "{count} handoffs"),
  });
  if (!multi) {
    const moments = [];
    for (const row of rows) {
      for (const value of [row.ts, row.end]) {
        if (value !== null) {
          moments.push(value);
        }
      }
    }
    const span = moments.length > 1 ? Math.max(...moments) - Math.min(...moments) : 0;
    if (span > 0) {
      line = format(text("chainSummaryDuration", "{summary}, {duration} from first to last"), {
        summary: line, duration: formatDuration(span),
      });
    }
  }
  return line;
}

export function chainTruncatedText(scanned) {
  return format(text("chainTruncated", "Only the first {n} records with this key were read; the list may be incomplete."), {
    n: Number.isFinite(scanned) ? scanned : 0,
  });
}

function errorText(error) {
  return error && error.message ? error.message : "";
}

export function createChainFlow({ api, getCase, onState }) {
  let generation = 0;
  let controller = null;
  const state = {
    caseId: null,
    type: null,
    value: null,
    status: "idle", // idle | loading | ready | error
    data: null,
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
    state.status = "idle";
    state.data = null;
    state.error = null;
    emit();
  }

  async function open(type, value) {
    close();
    const caseId = getCase() || null;
    if (!caseId) {
      return; // the records flow says "no case"; the section stays hidden
    }
    const token = generation;
    controller = new AbortController();
    const signal = controller.signal;
    state.caseId = caseId;
    state.type = type;
    state.value = value;
    state.status = "loading";
    emit();
    try {
      const data = await api.keyChain(caseId, type, value, {}, signal);
      if (token !== generation) {
        return;
      }
      state.data = data && typeof data === "object" ? data : {};
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

  return { state, open, close };
}
