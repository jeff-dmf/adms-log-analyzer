// Pure helpers and the request flows of the export panel and the redaction settings (no DOM).
//
// The export flow builds the whole report on the server (POST /api/export), so the preview the
// user sees is exactly what is copied or downloaded. A new build, a case change or a reset starts a
// new generation: answers of an older generation are dropped. The text of the report comes from the
// ERS (untrusted) and is only ever handed to textContent, the clipboard or a Blob.

export const EXPORT_DEFAULTS = Object.freeze({
  context: 0,
  maxBytes: 30000,
  includePayloads: false,
});
export const EXPORT_LIMITS = Object.freeze({ contextMax: 100, maxBytesMin: 2000, maxBytesMax: 2000000 });
export const SELECTION_KINDS = Object.freeze(["filter", "record", "event"]);
const MATCH_ALL = Object.freeze({ and: [] });

function isId(value) {
  return Number.isSafeInteger(value) && value > 0;
}

// Which selections make sense now: `ctx` = { caseId, filter, recordId, eventId }.
export function selectionAvailability(ctx) {
  const hasCase = Boolean(ctx && ctx.caseId);
  return {
    filter: hasCase,
    record: hasCase && isId(ctx.recordId),
    event: hasCase && isId(ctx.eventId),
  };
}

export function defaultSelectionKind(ctx) {
  const available = selectionAvailability(ctx);
  if (available.event) {
    return "event";
  }
  return available.record ? "record" : "filter";
}

// The `selection` object of the request, or null when the kind is not available.
export function buildSelection(kind, ctx) {
  const available = selectionAvailability(ctx);
  if (!SELECTION_KINDS.includes(kind) || !available[kind]) {
    return null;
  }
  if (kind === "record") {
    return { ids: [ctx.recordId] };
  }
  if (kind === "event") {
    return { events: [ctx.eventId] };
  }
  const filter = ctx.filter && typeof ctx.filter === "object" && Object.keys(ctx.filter).length > 0 ? ctx.filter : MATCH_ALL;
  return { filter };
}

function wholeNumber(value, fallback) {
  const text = String(value === undefined || value === null ? "" : value).trim();
  return /^\d{1,9}$/.test(text) ? Number(text) : fallback;
}

// Form values -> request options. Numbers outside the server limits are clamped; the
// anonymization flag is ALWAYS sent, true unless the form says exactly `false`.
export function normalizeOptions(form) {
  const raw = form || {};
  const context = Math.min(wholeNumber(raw.context, EXPORT_DEFAULTS.context), EXPORT_LIMITS.contextMax);
  const maxBytes = Math.min(
    Math.max(wholeNumber(raw.maxBytes, EXPORT_DEFAULTS.maxBytes), EXPORT_LIMITS.maxBytesMin),
    EXPORT_LIMITS.maxBytesMax,
  );
  return {
    anonymize: raw.anonymize !== false,
    include_payloads: raw.includePayloads === true,
    context,
    max_bytes: maxBytes,
  };
}

// { body } for api.exportReport, or { error } (a strings.js key) when nothing can be exported.
export function buildExportRequest(kind, ctx, form) {
  const selection = buildSelection(kind, ctx);
  if (!ctx || !ctx.caseId) {
    return { error: "exportNoCase" };
  }
  if (selection === null) {
    return { error: "exportNoSelection" };
  }
  return { caseId: ctx.caseId, selection, options: normalizeOptions(form) };
}

export function legendRows(legend) {
  if (!Array.isArray(legend)) {
    return [];
  }
  return legend.map((row) => ({
    label: String(row.label === undefined ? "" : row.label),
    distinct: Number(row.distinct) || 0,
    replaced: Number(row.replaced) || 0,
  }));
}

function errorText(error) {
  return error && error.message ? String(error.message) : "";
}

export function createExportFlow({ api, getCase, onState }) {
  let generation = 0;
  let controller = null;
  const state = {
    status: "idle", // idle | loading | ready | error
    caseId: null,
    parts: [],
    active: 0,
    legend: [],
    warnings: [],
    counts: null,
    anonymized: true,
    mapSaved: false,
    error: null,
    errorKey: null,
  };

  function emit() {
    onState(state);
  }

  function clear() {
    state.parts = [];
    state.active = 0;
    state.legend = [];
    state.warnings = [];
    state.counts = null;
    state.mapSaved = false;
    state.error = null;
    state.errorKey = null;
  }

  function reset() {
    generation += 1;
    if (controller) {
      controller.abort();
      controller = null;
    }
    state.status = "idle";
    state.caseId = null;
    clear();
    emit();
  }

  async function build(kind, ctx, form) {
    reset();
    const request = buildExportRequest(kind, ctx, form);
    if (request.error) {
      state.status = "error";
      state.errorKey = request.error;
      emit();
      return;
    }
    if (getCase() !== request.caseId) {
      state.status = "error";
      state.errorKey = "exportNoCase";
      emit();
      return;
    }
    const token = generation;
    controller = new AbortController();
    const signal = controller.signal;
    state.caseId = request.caseId;
    state.anonymized = request.options.anonymize;
    state.status = "loading";
    emit();
    try {
      const data = await api.exportReport(request.caseId, request.selection, request.options, signal);
      if (token !== generation || getCase() !== request.caseId) {
        return;
      }
      state.parts = Array.isArray(data.parts) ? data.parts : [];
      state.legend = legendRows(data.legend);
      state.warnings = Array.isArray(data.warnings) ? data.warnings.map(String) : [];
      state.counts = data.counts || null;
      state.anonymized = data.anonymized !== false;
      state.mapSaved = data.map_saved === true;
      state.active = 0;
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

  function selectPart(index) {
    if (state.status === "ready" && Number.isInteger(index) && index >= 0 && index < state.parts.length) {
      state.active = index;
      emit();
    }
  }

  return { state, build, selectPart, reset };
}

// Copies `text`: the async Clipboard API first, then `fallback()` (a legacy copy through a
// textarea, supplied by the panel). Returns "clipboard", "fallback" or "failed".
export async function copyText(text, { clipboard, fallback }) {
  if (clipboard && typeof clipboard.writeText === "function") {
    try {
      await clipboard.writeText(text);
      return "clipboard";
    } catch (error) {
      // blocked or unavailable (permissions, insecure context): try the legacy way
    }
  }
  try {
    return fallback && fallback(text) === true ? "fallback" : "failed";
  } catch (error) {
    return "failed";
  }
}

// -- redaction settings ----------------------------------------------------------------------

// One value per line, trimmed, empty lines dropped, duplicates removed (first wins).
export function parseLines(text) {
  const seen = new Set();
  const out = [];
  for (const line of String(text === undefined || text === null ? "" : text).split(/\r?\n/)) {
    const value = line.trim();
    if (value !== "" && !seen.has(value)) {
      seen.add(value);
      out.push(value);
    }
  }
  return out;
}

export function profileToForm(profile) {
  const source = profile || {};
  const terms = source.terms || {};
  return {
    name: source.name || "default",
    categories: { ...(source.categories || {}) },
    word: (terms.word || []).join("\n"),
    literal: (terms.literal || []).join("\n"),
    exceptions: (source.exceptions || []).join("\n"),
    names: (source.payload_names || []).join("\n"),
  };
}

export function formToProfile(form) {
  return {
    name: form.name || "default",
    categories: { ...form.categories },
    terms: { word: parseLines(form.word), literal: parseLines(form.literal) },
    exceptions: parseLines(form.exceptions),
    payload_names: parseLines(form.names),
  };
}

export function createRedactionFlow({ api, onState }) {
  let generation = 0;
  let controller = null;
  const state = {
    status: "idle", // idle | loading | ready | saving | error
    profile: null,
    defaults: null,
    categories: [],
    invalid: false,
    message: "",
    saved: false,
    error: null,
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

  async function load() {
    const { token, signal } = begin();
    state.status = "loading";
    state.saved = false;
    state.error = null;
    emit();
    try {
      const data = await api.redaction(signal);
      if (token !== generation) {
        return;
      }
      state.profile = data.profile;
      state.defaults = data.defaults || data.profile;
      state.categories = Array.isArray(data.categories) ? data.categories : [];
      state.invalid = data.invalid === true;
      state.message = data.message ? String(data.message) : "";
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

  async function save(profile) {
    const { token, signal } = begin();
    state.status = "saving";
    state.saved = false;
    state.error = null;
    emit();
    try {
      const data = await api.saveRedaction(profile, signal);
      if (token !== generation) {
        return;
      }
      state.profile = data.profile;
      state.invalid = false;
      state.message = "";
      state.saved = true;
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

  return { state, load, save };
}
