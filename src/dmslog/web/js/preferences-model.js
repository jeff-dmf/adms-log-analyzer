// Preferences: export and import of the versioned package (ARQUITETURA 10.12). No DOM here.
//
// Two flows, each with ONE operation in flight at a time. An operation is fixed when it starts (the sections, or the
// text of the file with the mode and the policy) and every answer is checked against a generation counter before it
// changes anything: a changed file or option, a new request, a reset or a dispose makes a late answer worthless.
// Apply sends exactly what Check looked at (the same text, mode and policy), never the live form.
//
// Nothing the server or the file says is shown: failures and report reasons are static texts chosen by code
// (strings.js). The only values of the package that reach the page are the item names the server echoes as clean
// labels (80 characters), and those go through textContent.
import { errorKind } from "./api.js";
import { S, format } from "./strings.js";

// Mirrors store/preferences.py MAX_PACKAGE_BYTES (the server refuses more with 413); a bigger file is refused here,
// before it is read.
export const MAX_PACKAGE_BYTES = 8 * 1024 * 1024;

export const SECTIONS = [
  { id: "presets", titleKey: "pfSecPresets", hintKey: "pfSecPresetsHint" },
  { id: "alert_rules", titleKey: "pfSecAlertRules", hintKey: "pfSecAlertRulesHint" },
  { id: "redaction", titleKey: "pfSecRedaction", hintKey: "pfSecRedactionHint" },
];
export const SECTION_IDS = SECTIONS.map((section) => section.id);

// The redaction section carries the user's anonymization terms in clear text: off unless asked for.
export const defaultSelection = () => ({ presets: true, alert_rules: true, redaction: false });

export const MODES = ["merge", "replace"];
const OUTCOMES = ["rejected", "partial", "applied", "unchanged", "failed"];
const ITEM_OUTCOMES = ["added", "updated", "unchanged", "rejected", "not_applied", "unknown"];
const SECTION_STATUSES = ["absent", "unchanged", "changed", "rejected"];
export const MAX_ROWS = 200; // items listed per section; the counts always cover all of them
const MAX_NAME_CHARS = 80;
const MAX_FILE_NAME_CHARS = 120;

const sectionTitle = (id) => S[SECTIONS.find((section) => section.id === id).titleKey];

// ---- messages: { key, params } for a template of S, { map, code } for a text chosen by code
const message = (key, params) => ({ key, params: params || {} });
const byCode = (map, code) => ({ map, code: typeof code === "string" ? code : "" });

export function renderMessage(ref) {
  if (!ref) {
    return "";
  }
  if (ref.map) {
    const table = S[ref.map];
    return Object.prototype.hasOwnProperty.call(table, ref.code) ? table[ref.code] : table.unknown;
  }
  const params = { ...ref.params };
  if (Array.isArray(params.sections)) {
    params.sections = params.sections.map(sectionTitle).join(", ");
  }
  if (typeof params.section === "string" && SECTION_IDS.includes(params.section)) {
    params.section = sectionTitle(params.section);
  }
  return format(S[ref.key], params);
}

export const renderMessages = (refs) => refs.map(renderMessage).join(" ");

// ---- small pure helpers
export function selectedSections(selection) {
  return SECTION_IDS.filter((id) => selection[id] === true);
}

export function formatSize(bytes) {
  if (!Number.isFinite(bytes) || bytes < 0) {
    return "";
  }
  if (bytes < 1024) {
    return format(S.pfSizeBytes, { value: bytes });
  }
  if (bytes < 1024 * 1024) {
    return format(S.pfSizeKb, { value: (bytes / 1024).toFixed(1) });
  }
  return format(S.pfSizeMb, { value: (bytes / (1024 * 1024)).toFixed(1) });
}

const pad = (n) => String(n).padStart(2, "0");

// "dmslog-preferences-2026-10-18.json": the local date, nothing of any case.
export function exportFileName(date) {
  if (!(date instanceof Date) || Number.isNaN(date.getTime())) {
    return "dmslog-preferences.json";
  }
  return `dmslog-preferences-${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}.json`;
}

const cleanLabel = (value, limit) => String(value === undefined || value === null ? "" : value)
  .replace(/\p{Cc}/gu, " ")
  .slice(0, limit);

const count = (value) => (Number.isSafeInteger(value) && value >= 0 ? value : 0);
const isObject = (value) => value !== null && typeof value === "object" && !Array.isArray(value);

// What an ApiError (or anything thrown) means for the user; null for an abort (nobody is waiting).
// `unknownOutcome` is true for Apply: a lost answer may mean the files were written.
export function failureMessage(err, { unknownOutcome = false } = {}) {
  if (err && err.name === "AbortError") {
    return null;
  }
  const kind = errorKind(err);
  const status = Number(err && err.status);
  if (status === 413 || (err && err.code === "package_too_large")) {
    return message("pfErrTooLarge", { limit: formatSize(MAX_PACKAGE_BYTES) });
  }
  if (kind === "forbidden") {
    return message("pfErrSession");
  }
  if (kind === "network" || kind === "server" || kind === "timeout" || kind === "unknown") {
    if (unknownOutcome) {
      return message("pfErrApplyUnknown");
    }
    return message(kind === "network" ? "pfErrNetwork" : "pfErrServer");
  }
  if (status === 400 && err && typeof err.code === "string" && Object.prototype.hasOwnProperty.call(S.pfStructural, err.code)) {
    return byCode("pfStructural", err.code);
  }
  return message("pfErrRequest");
}

// ---- the report of the server as something the dialog can draw (null when it is not a report)
function sectionView(id, raw, dryRun) {
  const section = isObject(raw) ? raw : {};
  const status = SECTION_STATUSES.includes(section.status) ? section.status : "rejected";
  const counts = {};
  for (const key of ["added", "updated", "unchanged", "rejected", "removed"]) {
    counts[key] = count(isObject(section.counts) ? section.counts[key] : 0);
  }
  const allItems = Array.isArray(section.items) ? section.items : [];
  const items = [];
  for (let index = 0; index < allItems.length && items.length < MAX_ROWS; index += 1) {
    const item = isObject(allItems[index]) ? allItems[index] : {};
    const outcome = ITEM_OUTCOMES.includes(item.outcome) ? item.outcome : "unknown";
    items.push({
      number: Number.isSafeInteger(item.index) ? item.index + 1 : index + 1,
      name: cleanLabel(item.name, MAX_NAME_CHARS),
      outcome,
      reason: item.code ? byCode("pfReason", item.code) : null,
    });
  }
  const removedAll = Array.isArray(section.removed) ? section.removed : [];
  const removed = removedAll.slice(0, MAX_ROWS).map((name) => cleanLabel(name, MAX_NAME_CHARS));
  const unknown = section.state === "unknown";
  let statusKey = "pfStatusRejected";
  if (unknown) {
    statusKey = "pfStatusUnknown";
  } else if (status === "absent") {
    statusKey = "pfStatusAbsent";
  } else if (status === "unchanged") {
    statusKey = "pfStatusUnchanged";
  } else if (status === "changed") {
    statusKey = dryRun ? "pfStatusWillChange" : "pfStatusChanged";
  }
  return {
    id,
    status,
    statusKey,
    unknown,
    counts,
    items,
    hiddenItems: Math.max(0, allItems.length - items.length),
    removed,
    hiddenRemoved: Math.max(0, counts.removed - removed.length),
    warnings: (Array.isArray(section.warnings) ? section.warnings : [])
      .filter(isObject).map((warning) => byCode("pfReason", warning.code)),
    error: isObject(section.error) ? byCode("pfReason", section.error.code) : null,
    noteKey: id === "redaction" && status === "changed" ? "pfRedactionNote" : null,
  };
}

export function buildReportView(raw) {
  if (!isObject(raw) || !OUTCOMES.includes(raw.outcome) || !isObject(raw.sections) || typeof raw.dry_run !== "boolean") {
    return null;
  }
  const dryRun = raw.dry_run;
  const sections = [];
  for (const id of SECTION_IDS) {
    if (Object.prototype.hasOwnProperty.call(raw.sections, id)) {
      sections.push(sectionView(id, raw.sections[id], dryRun));
    }
  }
  const migrated = isObject(raw.schema) && Array.isArray(raw.schema.migrated) ? raw.schema.migrated : [];
  const suffix = dryRun ? "Check" : "Done";
  return {
    dryRun,
    outcome: raw.outcome,
    applied: raw.applied === true,
    headline: message("pfOut" + raw.outcome[0].toUpperCase() + raw.outcome.slice(1) + suffix),
    errors: (Array.isArray(raw.errors) ? raw.errors : []).filter(isObject).map((error) => byCode("pfStructural", error.code)),
    migratedFrom: migrated.length > 0 && Number.isSafeInteger(migrated[0]) ? migrated[0] : null,
    sections,
    // Apply only makes sense after a Check that found something to write.
    canApply: dryRun && (raw.outcome === "applied" || raw.outcome === "partial")
      && sections.some((section) => section.status === "changed"),
  };
}

// ---- export
// deps: api (preferences.export(sections, signal)), save(name, text) (throws when it cannot), now(), onState(state).
// State: { selection, phase: idle|working|done, messages: [ref], isError, seq } (seq changes when the messages do).
export function createExportFlow({ api, save, now = () => new Date(), onState = () => {} }) {
  let selection = defaultSelection();
  let phase = "idle";
  let messages = [];
  let isError = false;
  let seq = 0;
  let gen = 0;
  let controller = null;
  let disposed = false;

  const snapshot = () => ({ selection: { ...selection }, phase, messages, isError, seq, busy: phase === "working" });
  const emit = () => {
    if (!disposed) {
      onState(snapshot());
    }
  };
  const say = (refs, error) => {
    messages = refs;
    isError = error;
    seq += 1;
  };
  const stop = () => {
    gen += 1;
    if (controller !== null) {
      controller.abort();
      controller = null;
    }
  };

  async function run() {
    if (phase === "working" || disposed) {
      return false;
    }
    const sections = selectedSections(selection);
    if (sections.length === 0) {
      say([message("pfErrNoSection")], true);
      emit();
      return false;
    }
    stop();
    const mine = gen;
    controller = new AbortController();
    const { signal } = controller;
    phase = "working";
    say([message("pfExportWorking")], false);
    emit();
    let result;
    try {
      result = await api.preferences.export(sections, signal);
    } catch (err) {
      if (mine !== gen) {
        return false;
      }
      controller = null;
      phase = "idle";
      say([failureMessage(err) || message("pfErrExport")], true);
      emit();
      return false;
    }
    if (mine !== gen) {
      return false;
    }
    controller = null;
    if (!isObject(result) || typeof result.text !== "string" || !isObject(result.package)) {
      phase = "idle";
      say([message("pfErrBadReport")], true);
      emit();
      return false;
    }
    const name = exportFileName(now());
    try {
      save(name, result.text);
    } catch {
      phase = "idle";
      say([message("pfErrSave")], true);
      emit();
      return false;
    }
    const included = SECTION_IDS.filter((id) => Object.prototype.hasOwnProperty.call(result.package, id));
    const notes = [message("pfExportDone", { name, sections: included })];
    for (const warning of Array.isArray(result.warnings) ? result.warnings : []) {
      if (isObject(warning) && SECTION_IDS.includes(warning.section)) {
        notes.push(message("pfWarnLeftOut", { section: warning.section }));
      }
    }
    if (sections.includes("redaction") && !included.includes("redaction") && !notes.some((n) => n.key === "pfWarnLeftOut" && n.params.section === "redaction")) {
      notes.push(message("pfExportNoRedaction"));
    }
    phase = "idle";
    say(notes, false);
    emit();
    return true;
  }

  return {
    get state() {
      return snapshot();
    },
    setSection(id, on) {
      if (!SECTION_IDS.includes(id) || phase === "working" || disposed) {
        return false;
      }
      selection = { ...selection, [id]: Boolean(on) };
      say([], false);
      emit();
      return true;
    },
    run,
    reset() {
      stop();
      selection = defaultSelection();
      phase = "idle";
      say([], false);
      emit();
    },
    dispose() {
      stop();
      disposed = true;
    },
  };
}

// ---- import
// The bytes of the file as text, or a "not_utf8" error. Blob.text() would replace invalid bytes by U+FFFD and change the
// package silently (a redaction term, a name); the server refuses invalid UTF-8, so the browser must too. A BOM is kept
// (ignoreBOM) so the text is the file as it is: the server refuses a BOM too (bad_json), and the user sees that error.
export async function readUtf8(file) {
  const bytes = await file.arrayBuffer();
  try {
    return new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(bytes);
  } catch {
    throw Object.assign(new Error("not utf-8"), { code: "not_utf8" });
  }
}
const defaultRead = readUtf8;

// deps: api (preferences.import(body, signal)), readFile(file) -> Promise<string> (default: readUtf8),
// onState(state), onApplied({unknown}) after an Apply that wrote files or whose outcome is unknown.
// Phases: empty, reading, ready, checking, checked, applying, applied.
export function createImportFlow({ api, readFile = defaultRead, onState = () => {}, onApplied = () => {} }) {
  let phase = "empty";
  let file = null;
  let text = null;
  let mode = "merge";
  let skipInvalid = false;
  let report = null;
  let checked = null; // { mode, policy, text }: exactly what the Check looked at
  let confirming = false;
  let detached = false; // the dialog closed while an Apply was running: the Apply is followed to its end anyway
  let messages = [];
  let isError = false;
  let seq = 0;
  let gen = 0;
  let controller = null;
  let disposed = false;

  const busy = () => phase === "reading" || phase === "checking" || phase === "applying";
  const snapshot = () => ({
    phase,
    file: file === null ? null : { ...file },
    mode,
    skipInvalid,
    report,
    confirming,
    messages,
    isError,
    seq,
    busy: busy(),
    frozen: phase === "applying", // file, mode and policy cannot change while the Apply runs
    canCheck: text !== null && (phase === "ready" || phase === "checked" || phase === "applied"),
    canApply: phase === "checked" && report !== null && report.canApply,
  });
  const emit = () => {
    if (!disposed) {
      onState(snapshot());
    }
  };
  const say = (refs, error) => {
    messages = refs;
    isError = error;
    seq += 1;
  };
  // Everything in flight stops mattering: its answer finds another generation.
  const stop = () => {
    gen += 1;
    if (controller !== null) {
      controller.abort();
      controller = null;
    }
  };
  const dropReport = () => {
    report = null;
    checked = null;
    confirming = false;
  };
  const wipe = () => {
    dropReport();
    text = null;
    file = null;
    mode = "merge";
    skipInvalid = false;
    phase = "empty";
    say([], false);
  };
  const backToReady = () => {
    phase = text === null ? "empty" : "ready";
  };

  async function chooseFile(chosen) {
    if (phase === "applying" || disposed) {
      return false;
    }
    stop();
    dropReport();
    text = null;
    file = null;
    phase = "empty";
    if (!chosen) {
      say([], false);
      emit();
      return false;
    }
    const size = chosen.size;
    if (!Number.isSafeInteger(size) || size < 0) {
      say([message("pfErrRead")], true);
      emit();
      return false;
    }
    if (size > MAX_PACKAGE_BYTES) {
      say([message("pfErrTooLarge", { limit: formatSize(MAX_PACKAGE_BYTES) })], true);
      emit();
      return false;
    }
    if (size === 0) {
      say([message("pfErrEmptyFile")], true);
      emit();
      return false;
    }
    file = { name: cleanLabel(chosen.name, MAX_FILE_NAME_CHARS), size };
    phase = "reading";
    const mine = gen;
    say([message("pfFileReading")], false);
    emit();
    let content;
    try {
      content = await readFile(chosen);
    } catch (err) {
      if (mine === gen) {
        file = null;
        phase = "empty";
        say([message(err && err.code === "not_utf8" ? "pfErrNotUtf8" : "pfErrRead")], true);
        emit();
      }
      return false;
    }
    if (mine !== gen) {
      return false;
    }
    if (typeof content !== "string" || content.trim() === "") {
      file = null;
      phase = "empty";
      say([message(typeof content === "string" ? "pfErrEmptyFile" : "pfErrRead")], true);
      emit();
      return false;
    }
    if (content.length > MAX_PACKAGE_BYTES) { // characters never outnumber the bytes that made them
      file = null;
      phase = "empty";
      say([message("pfErrTooLarge", { limit: formatSize(MAX_PACKAGE_BYTES) })], true);
      emit();
      return false;
    }
    text = content;
    phase = "ready";
    say([message("pfFileReady")], false);
    emit();
    return true;
  }

  // An option changed: a report that belongs to the old option is not a report of this form any more.
  function optionChanged() {
    if (phase === "checking" || phase === "checked" || phase === "applied") {
      stop();
      dropReport();
      backToReady();
      say([], false);
    } else if (confirming) {
      confirming = false;
    }
    emit();
  }

  async function check() {
    if (disposed || text === null || !(phase === "ready" || phase === "checked" || phase === "applied")) {
      return false;
    }
    stop();
    dropReport();
    const mine = gen;
    controller = new AbortController();
    const { signal } = controller;
    const sent = { mode, policy: skipInvalid ? "skip_item" : "reject_section", text };
    phase = "checking";
    say([message("pfChecking")], false);
    emit();
    let raw;
    try {
      raw = await api.preferences.import(
        { package: sent.text, dry_run: true, mode: sent.mode, on_item_error: sent.policy }, signal,
      );
    } catch (err) {
      if (mine !== gen) {
        return false;
      }
      controller = null;
      phase = "ready";
      say([failureMessage(err) || message("pfErrCheck")], true);
      emit();
      return false;
    }
    if (mine !== gen) {
      return false;
    }
    controller = null;
    const view = buildReportView(raw);
    if (view === null || !view.dryRun) {
      phase = "ready";
      say([message("pfErrBadReport")], true);
      emit();
      return false;
    }
    report = view;
    checked = sent;
    phase = "checked";
    say([view.headline], false);
    emit();
    return true;
  }

  // Replace asks first: the first call returns "confirm" and shows the question; apply({ confirmed: true }) goes on.
  async function apply({ confirmed = false } = {}) {
    if (disposed || phase !== "checked" || report === null || !report.canApply || checked === null) {
      return false;
    }
    if (checked.mode === "replace" && !confirmed) {
      confirming = true;
      say([], false);
      emit();
      return "confirm";
    }
    stop();
    const mine = gen;
    controller = new AbortController();
    const { signal } = controller;
    const sent = checked;
    confirming = false;
    phase = "applying";
    say([message("pfApplying")], false);
    emit();
    let raw;
    try {
      raw = await api.preferences.import(
        { package: sent.text, dry_run: false, mode: sent.mode, on_item_error: sent.policy }, signal,
      );
    } catch (err) {
      if (mine !== gen) {
        return lost();
      }
      controller = null;
      const ref = failureMessage(err, { unknownOutcome: true }) || message("pfErrApplyUnknown");
      dropReport();
      backToReady();
      say([ref], true);
      emit();
      if (ref.key === "pfErrApplyUnknown") {
        notifyApplied(true);
      }
      return finishDetached(false);
    }
    if (mine !== gen) {
      return lost();
    }
    controller = null;
    const view = buildReportView(raw);
    if (view === null || view.dryRun) {
      dropReport();
      backToReady();
      say([message("pfErrApplyUnknown")], true);
      emit();
      notifyApplied(true);
      return finishDetached(false);
    }
    report = view;
    checked = null;
    phase = "applied";
    say([view.headline], view.outcome === "failed");
    emit();
    if (view.applied) {
      notifyApplied(false);
    }
    return finishDetached(view.applied);
  }

  // An Apply whose answer was dropped (only a dispose does that): the files may have been written, so the reload still
  // happens, and nothing else is drawn.
  function lost() {
    notifyApplied(true);
    return false;
  }

  // The dialog closed during the Apply and was not reopened: the result was followed (reload done), now nothing is kept.
  function finishDetached(result) {
    if (detached && !disposed) {
      detached = false;
      wipe();
      emit();
    }
    return result;
  }

  function notifyApplied(unknown) {
    try {
      onApplied({ unknown });
    } catch {
      // a failing reload hook must not turn a finished import into an error
    }
  }

  return {
    get state() {
      return snapshot();
    },
    chooseFile,
    setMode(next) {
      if (!MODES.includes(next) || next === mode || phase === "applying" || disposed) {
        return false;
      }
      mode = next;
      optionChanged();
      return true;
    },
    setSkipInvalid(on) {
      if (Boolean(on) === skipInvalid || phase === "applying" || disposed) {
        return false;
      }
      skipInvalid = Boolean(on);
      optionChanged();
      return true;
    },
    check,
    apply,
    cancelConfirm() {
      if (confirming) {
        confirming = false;
        say([message("pfConfirmCancelled")], false);
        emit();
      }
    },
    // The dialog closed: nothing pending, nothing kept (the file may hold the user's redaction terms).
    // An Apply in flight is NOT stopped: it keeps being followed (the server may finish writing), nothing else is kept.
    reset() {
      if (phase === "applying" && !disposed) {
        detached = true;
        text = null;
        file = null;
        emit();
        return;
      }
      stop();
      wipe();
      emit();
    },
    // The dialog opened again: an Apply still running is shown as it is and its result stays visible.
    attach() {
      detached = false;
    },
    dispose() {
      stop();
      dropReport();
      text = null;
      file = null;
      disposed = true;
    },
  };
}
