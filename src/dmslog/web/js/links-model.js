// Pure helpers and loading flows of the "Links" section, the counterpart preview and the
// "Side by side" view (no DOM). Everything in the answers comes from the ERS (untrusted text):
// this module only shapes it. Every flow starts a new generation per request, aborts the previous
// one and drops answers of an older generation or of another case.
import { S, format } from "./strings.js";
import { formatTs } from "./time.js";

export const PREVIEW_LINES = 40;
export const SIDE_BY_SIDE_LINES = 2000; // what the server keeps per pane (store/keys.py contract)
export const MAX_KEY_CHIPS = 40;
const KIND_ORDER = ["normal_sensitive", "response", "callback", "retry", "same_key"];

export function confidenceLabel(confidence) {
  const name = S.lnkConfidenceNames[confidence];
  return name === undefined ? S.lnkConfidenceUnknown : name;
}

// Colour only reinforces the text: 3 strong, 2 good, 1 fair, 0 weak.
export function confidenceTone(confidence) {
  return confidence >= 3 ? "high" : confidence === 2 ? "good" : confidence === 1 ? "fair" : "low";
}

export function kindLabel(kind) {
  return S.lnkKindNames[kind] || String(kind === null || kind === undefined ? "" : kind);
}

export function sideLabel(sensitive) {
  if (sensitive === null || sensitive === undefined) {
    return S.lnkSideUnknown;
  }
  return sensitive ? S.lnkSideSensitive : S.lnkSideNormal;
}

function kindRank(kind) {
  const index = KIND_ORDER.indexOf(kind);
  return index < 0 ? KIND_ORDER.length : index;
}

function order(a, b) {
  return kindRank(a.kind) - kindRank(b.kind) ||
    b.confidence - a.confidence ||
    (a.ts === null ? -1 : a.ts) - (b.ts === null ? -1 : b.ts) ||
    a.entryId - b.entryId;
}

// Link rows of GET /api/entries/<id>/links, best first (normal/sensitive pair, then confidence).
export function buildLinkRows(links) {
  const rows = (Array.isArray(links) ? links : []).map((link) => {
    const sensitive = link.source ? link.source.sensitive : null;
    return {
      entryId: link.entry_id,
      kind: link.kind,
      kindLabel: kindLabel(link.kind),
      confidence: link.confidence,
      confidenceLabel: confidenceLabel(link.confidence),
      tone: confidenceTone(link.confidence),
      sensitive: sensitive === undefined ? null : sensitive,
      sideLabel: sideLabel(sensitive),
      member: link.source && link.source.member ? String(link.source.member) : "",
      ts: link.ts === undefined ? null : link.ts,
      time: formatTs(link.ts),
      tid: link.tid === null || link.tid === undefined ? "" : format(S.lnkTid, { tid: "0x" + Number(link.tid).toString(16) }),
      code: link.tpl_code || "",
      text: link.msg === null || link.msg === undefined ? "" : String(link.msg),
    };
  });
  rows.sort(order);
  return rows;
}

// Label of the "Show in ..." button for a counterpart (which side it is on).
export function revealLabel(row) {
  if (row.sensitive === true) {
    return S.lnkShowInSensitive;
  }
  if (row.sensitive === false) {
    return S.lnkShowInNormal;
  }
  return S.lnkShowLinked;
}

// The row the section's main buttons act on: the first one (normal/sensitive pair preferred).
export function bestRow(rows) {
  return rows.length > 0 ? rows[0] : null;
}

// Key chips of an entry: {type, value, label}, deduplicated and capped.
export function buildKeyChips(entry) {
  const chips = [];
  const seen = new Set();
  const keys = entry && Array.isArray(entry.keys) ? entry.keys : [];
  for (const item of keys) {
    if (!item || item.key_type === null || item.key_type === undefined || item.value === null || item.value === undefined) {
      continue;
    }
    const type = String(item.key_type);
    const value = String(item.value);
    const id = type + "\u0000" + value;
    if (seen.has(id)) {
      continue;
    }
    seen.add(id);
    chips.push({ type, value, label: type + ": " + value });
    if (chips.length >= MAX_KEY_CHIPS) {
      break;
    }
  }
  return chips;
}

function errorText(error) {
  return error && error.message ? error.message : "";
}

// Links of the selected entry plus a lazily loaded preview of one counterpart.
export function createLinksFlow({ api, getCase, onState }) {
  let generation = 0;
  let controller = null;
  let previewGeneration = 0;
  let previewController = null;
  const state = {
    caseId: null,
    entryId: null,
    status: "idle", // idle | loading | ready | error
    rows: [],
    error: null,
    preview: { entryId: null, status: "none", text: "", truncated: false, error: null },
  };

  function emit() {
    onState(state);
  }

  function abortPreview() {
    previewGeneration += 1;
    if (previewController) {
      previewController.abort();
      previewController = null;
    }
    state.preview = { entryId: null, status: "none", text: "", truncated: false, error: null };
  }

  function reset() {
    generation += 1;
    if (controller) {
      controller.abort();
      controller = null;
    }
    abortPreview();
    state.caseId = null;
    state.entryId = null;
    state.status = "idle";
    state.rows = [];
    state.error = null;
    emit();
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
      const data = await api.entryLinks(caseId, entryId, signal);
      if (token !== generation) {
        return;
      }
      state.rows = buildLinkRows(data && data.links);
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

  // Preview of one counterpart: its message and the first PREVIEW_LINES continuation lines.
  async function preview(entryId) {
    const caseId = state.caseId;
    if (!caseId || caseId !== getCase() || !state.rows.some((row) => row.entryId === entryId)) {
      return;
    }
    abortPreview();
    previewGeneration += 1;
    const token = previewGeneration;
    previewController = new AbortController();
    const signal = previewController.signal;
    state.preview = { entryId, status: "loading", text: "", truncated: false, error: null };
    emit();
    try {
      const data = await api.entryHead(caseId, entryId, PREVIEW_LINES, signal);
      if (token !== previewGeneration) {
        return;
      }
      state.preview = {
        entryId,
        status: "ready",
        text: data && data.text !== null && data.text !== undefined ? String(data.text) : "",
        truncated: Boolean(data && data.cont_truncated),
        error: null,
      };
      emit();
    } catch (error) {
      if (token === previewGeneration && !signal.aborted) {
        state.preview = { entryId, status: "error", text: "", truncated: false, error: errorText(error) };
        emit();
      }
    }
  }

  function closePreview() {
    abortPreview();
    emit();
  }

  return { state, load, reset, preview, closePreview };
}

function sideTitle(sensitive) {
  if (sensitive === null || sensitive === undefined) {
    return S.sbsPaneOther;
  }
  return sensitive ? S.sbsPaneSensitive : S.sbsPaneNormal;
}

function value(text) {
  return text === null || text === undefined || text === "" ? S.sbsNoValue : String(text);
}

// One pane of the side-by-side view from an entry of GET /api/entries/<id>/side-by-side.
export function buildPane(entry) {
  if (!entry) {
    return null;
  }
  const headers = [
    [S.sbsSource, value(entry.member)],
    [S.sbsTime, value(formatTs(entry.ts))],
    [S.sbsTid, entry.tid === null || entry.tid === undefined ? S.sbsNoValue : "0x" + Number(entry.tid).toString(16)],
    [S.sbsTemplate, value(entry.tpl)],
  ].map(([label, text]) => ({ label, text }));
  return {
    id: entry.id,
    title: sideTitle(entry.sensitive),
    headers,
    text: entry.text === null || entry.text === undefined ? "" : String(entry.text),
    truncated: Boolean(entry.cont_truncated),
  };
}

export function buildSideView(data) {
  if (!data || !data.a || !data.b) {
    return null;
  }
  return {
    kindText: format(S.sbsKind, { kind: kindLabel(data.kind), confidence: confidenceLabel(data.confidence) }),
    left: buildPane(data.a),
    right: buildPane(data.b),
  };
}

// Loads the pair for one entry; the newest request wins.
export function createSideBySideFlow({ api, getCase, onState }) {
  let generation = 0;
  let controller = null;
  const state = { caseId: null, entryId: null, status: "idle", data: null, error: null };

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
    state.entryId = null;
    state.status = "idle";
    state.data = null;
    state.error = null;
    emit();
  }

  async function open(entryId) {
    close();
    const caseId = getCase() || null;
    if (!caseId) {
      state.status = "nocase";
      emit();
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
      const data = await api.sideBySide(caseId, entryId, signal);
      if (token !== generation) {
        return;
      }
      state.data = data;
      state.status = "ready";
      emit();
    } catch (error) {
      if (token === generation && !signal.aborted) {
        state.status = error && error.status === 404 && error.code === "no_counterpart" ? "none" : "error";
        state.error = errorText(error);
        emit();
      }
    }
  }

  return { state, open, close };
}
