// Pure helpers of the "Effective configuration" panel (no DOM): rows for the key table and the
// operation table from the GET /api/config answer. Values come from the ERS (untrusted text).
import { S, format, pluralKey } from "./strings.js";
import { formatTs } from "./time.js";

const MAX_WINDOWS_SHOWN = 5;

// Same order as the server (store/config.py): DebugLog < Warning < Info < Verbose.
const LEVEL_ORDER = { debuglog: 3, warning: 4, info: 5, verbose: 6 };
const LEVEL_SYNONYMS = { debug: "debuglog", information: "info" };

export const STATUS_TONE = { only: "neutral", wins: "neutral", overridden: "muted", ambiguous: "warn" };

export function levelRank(value) {
  if (typeof value !== "string") {
    return null;
  }
  const key = value.trim().toLowerCase();
  const rank = LEVEL_ORDER[LEVEL_SYNONYMS[key] || key];
  return rank === undefined ? null : rank;
}

export function baseName(path) {
  if (typeof path !== "string" || path === "") {
    return "";
  }
  const parts = path.split(/[\\/]/);
  return parts[parts.length - 1];
}

// A registry value is a string, null or (for list keys) an array of strings.
export function formatValue(value) {
  if (value === null || value === undefined) {
    return S.cfgNoValue;
  }
  if (Array.isArray(value)) {
    return value.length === 0 ? S.cfgNoValue : value.map((item) => (item === null ? "" : String(item))).join(", ");
  }
  return String(value) === "" ? S.cfgNoValue : String(value);
}

export function statusLabel(status) {
  switch (status) {
    case "only": return S.cfgStatusOnly;
    case "wins": return S.cfgStatusWins;
    case "overridden": return S.cfgStatusOverridden;
    case "ambiguous": return S.cfgStatusAmbiguous;
    default: return S.cfgStatusUnknown;
  }
}

export function keyLabel(key) {
  if (key.key_path && key.name) {
    return key.key_path + "/" + key.name;
  }
  return key.key_path || key.name || "";
}

function layerText(layer) {
  if (layer === null || layer === undefined) {
    return "";
  }
  return format(S.cfgLayerLine, { layer });
}

// Rows of the key table. Divergence is "yes" / "no" / "na" (nothing logged to compare with).
export function buildKeyRows(config) {
  const keys = config && Array.isArray(config.keys) ? config.keys : [];
  return keys.map((key) => {
    const layers = Array.isArray(key.layers) ? key.layers : [];
    const winner = layers.find((item) => item.effective) || null;
    const observed = key.observed || null;
    let divergence = "na";
    if (observed) {
      divergence = key.diverges ? "yes" : "no";
    }
    const label = keyLabel(key);
    return {
      id: key.scope + "|" + key.key_path + "|" + key.name,
      label,
      value: formatValue(key.effective),
      sourceFile: winner ? baseName(winner.source_file) : "",
      sourceLayer: winner ? [winner.layer_label, layerText(winner.layer)].filter(Boolean).join(", ") : "",
      status: key.status,
      statusLabel: statusLabel(key.status),
      tone: STATUS_TONE[key.status] || "neutral",
      observedText: observed
        ? (observed.is_default ? format(S.cfgObservedDefault, { value: formatValue(observed.value) })
          : formatValue(observed.value))
        : S.cfgNotObserved,
      observedEntryId: observed && observed.entry_id ? observed.entry_id : null,
      divergence,
      divergenceLabel: divergence === "yes" ? S.cfgDivergesYes : divergence === "no" ? S.cfgDivergesNo : S.cfgDivergesNa,
      layers: layers.map((item) => ({
        layer: item.layer,
        layerText: [item.layer_label, layerText(item.layer)].filter(Boolean).join(", "),
        file: item.source_file || "",
        value: formatValue(item.value),
        status: item.status,
        statusLabel: statusLabel(item.status),
        tone: STATUS_TONE[item.status] || "neutral",
        effective: Boolean(item.effective),
      })),
    };
  });
}

// Text filter (key, value, file, status label; case-insensitive) and the divergences-only switch.
export function filterKeyRows(rows, text, onlyDiverging) {
  const needle = String(text || "").trim().toLowerCase();
  return rows.filter((row) => {
    if (onlyDiverging && row.divergence !== "yes") {
      return false;
    }
    if (needle === "") {
      return true;
    }
    return [row.label, row.value, row.sourceFile, row.statusLabel, row.observedText]
      .some((part) => String(part).toLowerCase().includes(needle));
  });
}

export function countDiverging(rows) {
  return rows.filter((row) => row.divergence === "yes").length;
}

// "Log level in force" line: null value = unknown.
export function logLevelText(logLevel) {
  if (!logLevel || logLevel.value === null || logLevel.value === undefined) {
    return S.cfgLogLevelUnknown;
  }
  const source = logLevel.source === "observed" ? S.cfgLogLevelObserved : S.cfgLogLevelRegistry;
  return format(S.cfgLogLevel, { value: logLevel.value, source });
}

// The API gives payload_expected (true / false / null) but not the reason; the reason is rebuilt
// from the same two inputs the server used (the level in force and MessageLogLevel).
export function payloadReason(messageLevel, logLevel, expected) {
  if (messageLevel === null || messageLevel === undefined) {
    return S.cfgReasonNotSet;
  }
  if (String(messageLevel).trim().toLowerCase() === "none") {
    return S.cfgReasonNever;
  }
  const have = logLevel && logLevel.value !== null && logLevel.value !== undefined ? String(logLevel.value) : null;
  if (levelRank(messageLevel) === null) {
    return format(S.cfgReasonBadMessageLevel, { value: messageLevel });
  }
  if (have === null) {
    return S.cfgReasonNoLogLevel;
  }
  if (levelRank(have) === null) {
    return format(S.cfgReasonBadLogLevel, { value: have });
  }
  return format(expected ? S.cfgReasonEnough : S.cfgReasonTooLow, { log: have, message: messageLevel });
}

// "Payload expected in N window(s)": windows are [start, end, expected] (end null = open); the
// list names the expected windows only (at most MAX_WINDOWS_SHOWN, then "+N more"). Empty text when
// the operation has no windows (counts are over the windows returned; omitted ones are reported apart) (no level change was logged, the single answer applies).
export function windowsText(windows, dropped) {
  const list = Array.isArray(windows) ? windows.filter((w) => Array.isArray(w) && w.length >= 3) : [];
  if (list.length === 0) {
    return "";
  }
  const expected = list.filter((w) => w[2] === true);
  const omitted = Number(dropped) || 0;
  let head = format(S[pluralKey("cfgWindowsLine", list.length)], { n: expected.length, total: list.length });
  if (omitted > 0) {
    head += " " + format(S[pluralKey("cfgWindowsOmitted", omitted)], { n: omitted });
  }
  if (expected.length === 0) {
    return head;
  }
  const spans = expected.slice(0, MAX_WINDOWS_SHOWN).map((w) =>
    formatTs(w[0]) + " - " + (w[1] === null || w[1] === undefined ? S.cfgWindowOpen : formatTs(w[1])));
  const more = expected.length > MAX_WINDOWS_SHOWN
    ? " " + format(S.cfgWindowsMore, { n: expected.length - MAX_WINDOWS_SHOWN }) : "";
  return head + ": " + spans.join("; ") + more;
}

// Rows of the operation table: expected is "yes" / "no" / "unknown".
export function buildOperationRows(config) {
  const operations = config && Array.isArray(config.operations) ? config.operations : [];
  const logLevel = config ? config.log_level : null;
  return operations.map((op) => {
    const expected = op.payload_expected === true ? "yes" : op.payload_expected === false ? "no" : "unknown";
    return {
      operation: op.operation,
      level: op.message_log_level === null || op.message_log_level === undefined ? S.cfgLevelNotSet : String(op.message_log_level),
      expected,
      expectedLabel: expected === "yes" ? S.cfgPayloadYes : expected === "no" ? S.cfgPayloadNo : S.cfgPayloadUnknown,
      reason: payloadReason(op.message_log_level, logLevel, op.payload_expected),
      windowsText: windowsText(op.payload_expected_windows, op.payload_windows_dropped),
      source: [baseName(op.source_file), op.layer_label].filter(Boolean).join(", "),
    };
  });
}

// Option labels of the product selector; `value` is the index in the answer of /api/config.
export function productOptions(products) {
  return (Array.isArray(products) ? products : []).map((item, index) => ({
    value: String(index),
    label: format(S.cfgProductOption, { product: item.product, group: item.group, keys: item.keys }),
  }));
}
// Loading flow of the configuration panel, free of DOM so it can be tested with a fake api.
// Every refresh or product change starts a new generation: state is cleared first and answers
// of an older generation (or of another case) are dropped, so a table never outlives its case.

export function createConfigFlow({ api, getCase, onState }) {
  let generation = 0;
  let controller = null;
  const state = {
    caseId: null, products: [], selected: 0, config: null, status: "nocase", error: null,
  };

  function emit() {
    onState(state);
  }

  function begin() {
    generation += 1;
    if (controller) {
      controller.abort();
    }
    controller = new AbortController();
    return { token: generation, signal: controller.signal };
  }

  function fail(error) {
    state.config = null;
    state.status = "error";
    state.error = error && error.message ? error.message : "";
    emit();
  }

  async function load(caseId, run) {
    const chosen = state.products[state.selected];
    if (!chosen) {
      state.config = null;
      state.status = "empty";
      emit();
      return;
    }
    try {
      const data = await api.config(caseId, chosen.product, chosen.group, run.signal);
      if (run.token !== generation) {
        return;
      }
      state.config = data;
      state.status = "ready";
      emit();
    } catch (error) {
      if (run.token === generation && !run.signal.aborted) {
        fail(error);
      }
    }
  }

  async function refresh() {
    const run = begin();
    const caseId = getCase();
    const previous = state.caseId === caseId ? state.products[state.selected] : null;
    // Clear everything of the old case before any request.
    state.caseId = caseId || null;
    state.products = [];
    state.selected = 0;
    state.config = null;
    state.error = null;
    state.status = caseId ? "loading" : "nocase";
    emit();
    if (!caseId) {
      return;
    }
    try {
      const data = await api.configProducts(caseId, run.signal);
      if (run.token !== generation) {
        return;
      }
      state.products = data.products || [];
      const keep = previous
        ? state.products.findIndex((p) => p.product === previous.product && p.group === previous.group)
        : -1;
      state.selected = keep >= 0 ? keep : 0;
      await load(caseId, run);
    } catch (error) {
      if (run.token === generation && !run.signal.aborted) {
        fail(error);
      }
    }
  }

  function select(index) {
    const caseId = getCase();
    if (!caseId || caseId !== state.caseId) {
      return;
    }
    const run = begin();
    state.selected = index;
    state.config = null;
    state.status = "loading";
    emit();
    return load(caseId, run);
  }

  return { state, refresh, select };
}
