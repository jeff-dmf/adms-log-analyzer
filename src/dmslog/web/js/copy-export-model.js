// Pure helpers and the run flow of the "Copy as" menu (no DOM): build the request of
// POST /api/export/view from the form, name the file, describe the outcome. Text of the log is
// untrusted: it only goes to the clipboard or a Blob, never to the page as markup.
import { copyText } from "./export-model.js";
import { S, format, pluralKey } from "./strings.js";
import { formatTs } from "./time.js";

export const CX_KINDS = Object.freeze(["values", "lines", "record", "view_csv", "events_csv"]);
export const CX_DEFAULTS = Object.freeze({ maxRows: 100000, separator: "newline", quote: "none", unique: false });
export const CX_LIMITS = Object.freeze({ maxRowsMin: 1, maxRowsMax: 1000000 });
export const EVENT_STATUSES = Object.freeze(["ok", "business_error", "fail", "timeout", "open", "truncated"]);
const FIXED_COLUMNS = Object.freeze(["ts", "pid", "tid", "level", "template", "source", "msg"]);
const CSV_KINDS = new Set(["view_csv", "events_csv"]);

export function isCsv(kind) {
  return CSV_KINDS.has(kind);
}

// What each kind can do: the clipboard needs text a person would paste; CSV is a file.
export function actionsFor(kind) {
  return { copy: !isCsv(kind), download: kind !== "record" };
}

// Which kinds make sense now: ctx = { caseId, recordId }.
export function kindAvailability(ctx) {
  const hasCase = Boolean(ctx && ctx.caseId);
  return {
    values: hasCase,
    lines: hasCase,
    record: hasCase && Number.isSafeInteger(ctx.recordId) && ctx.recordId > 0,
    view_csv: hasCase,
    events_csv: hasCase,
  };
}

export function parseMaxRows(raw) {
  const text = String(raw === undefined || raw === null ? "" : raw).trim();
  if (!/^\d{1,9}$/.test(text)) {
    return null;
  }
  const value = Number(text);
  return value >= CX_LIMITS.maxRowsMin && value <= CX_LIMITS.maxRowsMax ? value : null;
}

// Choices of the "Values of" select: the visible columns, the extracted fields and the key types.
// ctx = { columns: [fixed keys], fieldColumns: [names], keyTypes: [names] }; value is "column:x" or "key:x".
export function sourceChoices(ctx) {
  const choices = [];
  const visible = new Set((ctx && ctx.columns) || FIXED_COLUMNS);
  for (const key of FIXED_COLUMNS) {
    if (visible.has(key)) {
      choices.push({ value: "column:" + key, label: format(S.cxSourceColumn, { name: columnLabel(key) }) });
    }
  }
  for (const name of (ctx && ctx.fieldColumns) || []) {
    choices.push({ value: "column:fields." + name, label: format(S.cxSourceField, { name }) });
  }
  for (const name of (ctx && ctx.keyTypes) || []) {
    choices.push({ value: "key:" + name, label: format(S.cxSourceKey, { name }) });
  }
  return choices;
}

const COLUMN_LABEL_KEYS = {
  ts: "columnTs", pid: "columnPid", tid: "columnTid", level: "columnLevel", template: "columnTemplate",
  source: "columnSource", msg: "columnMessage",
};

function columnLabel(key) {
  return S[COLUMN_LABEL_KEYS[key]] || key;
}

function trimmed(value) {
  return value === undefined || value === null ? "" : String(value).trim();
}

// The body of the request (without `case`), or null when the form cannot run.
// form = { kind, source, separator, quote, unique, bom, maxRows, events: {status, name, q} }
// ctx = { caseId, filter, columns, fieldColumns }
export function buildRequest(form, ctx) {
  if (!ctx || !ctx.caseId || !CX_KINDS.includes(form.kind) || form.kind === "record") {
    return null;
  }
  const maxRows = parseMaxRows(form.maxRows);
  if (maxRows === null) {
    return null;
  }
  const body = { kind: form.kind, filter: ctx.filter || null, max_rows: maxRows };
  if (form.kind === "values") {
    const source = String(form.source || "");
    const at = source.indexOf(":");
    const type = source.slice(0, at);
    const name = source.slice(at + 1);
    if (at < 0 || !name || (type !== "column" && type !== "key")) {
      return null;
    }
    body[type] = name;
    body.separator = form.separator === "comma" ? "comma" : "newline";
    body.quote = ["double", "single"].includes(form.quote) ? form.quote : "none";
    body.unique = Boolean(form.unique);
  } else if (form.kind === "view_csv") {
    const visible = new Set((ctx.columns && ctx.columns.length > 0) ? ctx.columns : FIXED_COLUMNS);
    body.columns = FIXED_COLUMNS.filter((key) => visible.has(key))
      .concat((ctx.fieldColumns || []).map((name) => "fields." + name));
    body.bom = Boolean(form.bom);
  } else if (form.kind === "events_csv") {
    const events = {};
    const eventsForm = form.events || {};
    if (trimmed(eventsForm.status)) {
      events.status = trimmed(eventsForm.status);
    }
    if (trimmed(eventsForm.name)) {
      events.name = trimmed(eventsForm.name);
    }
    if (trimmed(eventsForm.q)) {
      events.q = trimmed(eventsForm.q);
    }
    body.events = events;
    body.bom = Boolean(form.bom);
  }
  return body;
}

function pad(value) {
  return String(value).padStart(2, "0");
}

// "dmslog-<case>-<what>-YYYYMMDD-HHMMSS.<ext>", safe on Windows and Linux.
export function fileName(kind, caseId, now = new Date()) {
  const stamp = String(now.getFullYear()) + pad(now.getMonth() + 1) + pad(now.getDate()) + "-" +
    pad(now.getHours()) + pad(now.getMinutes()) + pad(now.getSeconds());
  const what = { values: "values", lines: "lines", record: "record", view_csv: "view", events_csv: "operations" }[kind] || "export";
  const safeCase = String(caseId || "case").replace(/[^A-Za-z0-9._-]+/g, "_").slice(0, 60);
  return "dmslog-" + safeCase + "-" + what + "-" + stamp + (isCsv(kind) ? ".csv" : ".txt");
}

// The selected record as text, like the lines export: header then message and continuation.
export function recordText(entry) {
  if (!entry) {
    return "";
  }
  const hasTs = entry.ts !== null && entry.ts !== undefined;
  let head = "";
  if (hasTs) {
    let ids = entry.pid === null || entry.pid === undefined ? "" : "[PID=" + entry.pid + "]";
    if (entry.tid !== null && entry.tid !== undefined) {
      ids += "[TID=0x" + Number(entry.tid).toString(16) + "]";
    }
    head = formatTs(entry.ts) + (ids ? " " + ids : "");
    const level = S.levelNames[entry.level];
    head += level ? " " + level + " - " : " ";
  }
  return head + String(entry.text === undefined || entry.text === null ? entry.msg || "" : entry.text) + "\n";
}

export function resultMessage(kind, result, how, file) {
  const parts = [];
  if (how === "copy") {
    parts.push(format(S[pluralKey("cxCopied", result.rows)], { rows: result.rows }));
  } else {
    parts.push(format(S[pluralKey("cxDownloaded", result.rows)], { rows: result.rows, file }));
  }
  if (result.truncated) {
    parts.push(format(S[pluralKey("cxTruncated", result.rows)], { rows: result.rows }));
  }
  return parts.join(" ");
}

// The flow. `api.exportView(caseId, body, as, signal)`; `getContext()` the viewer right now;
// `sink` = { copy(text) -> "clipboard"|"fallback"|"failed", save(blob, fileName) }.
// A new run (or a case change) drops the answers of an older one.
export function createCopyExportFlow({ api, getContext, onState, sink, clipboard }) {
  let generation = 0;
  let controller = null;
  let state = { status: "idle", message: "", truncated: false, rows: 0 };

  function set(next) {
    state = { ...state, ...next };
    onState(state);
  }

  function reset() {
    generation += 1;
    if (controller) {
      controller.abort();
      controller = null;
    }
    set({ status: "idle", message: "", truncated: false, rows: 0 });
  }

  async function run(form, how) {
    const ctx = getContext();
    if (!ctx || !ctx.caseId) {
      set({ status: "error", message: S.cxNoCase, truncated: false, rows: 0 });
      return;
    }
    generation += 1;
    const mine = generation;
    if (controller) {
      controller.abort();
    }
    controller = null;
    if (form.kind === "record") {
      const text = recordText(ctx.entry);
      if (!text) {
        set({ status: "error", message: S.cxNoRecord, truncated: false, rows: 0 });
        return;
      }
      if (how === "download") {
        return;
      }
      const outcome = await copyText(text, { clipboard, fallback: sink.copy });
      if (mine === generation) {
        set(outcome === "failed"
          ? { status: "error", message: S.cxCopyFailed, truncated: false, rows: 1 }
          : { status: "done", message: S.cxCopiedRecord, truncated: false, rows: 1 });
      }
      return;
    }
    const body = buildRequest(form, ctx);
    if (body === null) {
      const bad = parseMaxRows(form.maxRows) === null;
      set({
        status: "error",
        message: bad ? format(S.cxBadLimit, { max: CX_LIMITS.maxRowsMax }) : S.cxError,
        truncated: false,
        rows: 0,
      });
      return;
    }
    controller = new AbortController();
    const signal = controller.signal;
    set({ status: "working", message: S.cxWorking, truncated: false, rows: 0 });
    try {
      const result = await api.exportView(ctx.caseId, body, how === "copy" ? "text" : "blob", signal);
      if (mine !== generation || ctx.caseId !== getContext().caseId) {
        return;
      }
      if (result.rows === 0) {
        set({ status: "done", message: S.cxNothing, truncated: false, rows: 0 });
        return;
      }
      if (how === "copy") {
        const outcome = await copyText(result.data, { clipboard, fallback: sink.copy });
        if (mine !== generation) {
          return;
        }
        if (outcome === "failed") {
          set({ status: "error", message: S.cxCopyFailed, truncated: result.truncated, rows: result.rows });
          return;
        }
        set({ status: "done", message: resultMessage(form.kind, result, "copy"), truncated: result.truncated, rows: result.rows });
      } else {
        const file = fileName(form.kind, ctx.caseId);
        sink.save(result.data, file);
        set({
          status: "done",
          message: resultMessage(form.kind, result, "download", file),
          truncated: result.truncated,
          rows: result.rows,
        });
      }
    } catch (err) {
      if ((err && err.name === "AbortError") || mine !== generation) {
        return;
      }
      set({
        status: "error",
        message: S.cxError + (err && err.message ? ": " + err.message : ""),
        truncated: false,
        rows: 0,
      });
    }
  }

  return { run, reset, get state() { return state; } };
}
