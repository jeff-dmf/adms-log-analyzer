// Pure model of the "files of a new case" list: names, kinds, duplicates, and the create flow.
// No DOM here; the DOM side is case-files.js.
import { S, format } from "./strings.js";
import { formatTs } from "./time.js";

// Same convention as the server (ingest/ers.py): ERS_CL_<GUID>_<yyyymmddHHMMSS>_[Sensitive_]<Product>[_desc].zip
const ERS_NAME = /^ERS_CL_[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}_\d{14}_(Sensitive_)?[A-Za-z]+(_.*)?\.zip$/i;

export const KIND_ENTERPRISE = "enterprise";
export const KIND_SENSITIVE = "sensitive";
export const MAX_ITEMS = 200;

export const STATUS_PENDING = "pending";
export const STATUS_WORKING = "working";
export const STATUS_OK = "ok";
export const STATUS_REFUSED = "refused";
export const STATUS_FAILED = "failed";

export function splitPath(path) {
  const text = String(path);
  const cut = Math.max(text.lastIndexOf("/"), text.lastIndexOf("\\"));
  if (cut < 0) {
    return { dir: "", name: text };
  }
  return { dir: text.slice(0, cut === 0 ? 1 : cut), name: text.slice(cut + 1) };
}

// "enterprise", "sensitive" or null (not an ERS name; the server decides what to do with it).
export function ersKind(name) {
  const match = ERS_NAME.exec(String(name));
  if (!match) {
    return null;
  }
  return match[1] ? KIND_SENSITIVE : KIND_ENTERPRISE;
}

export function isZipName(name) {
  return /\.zip$/i.test(String(name));
}

export function formatSize(bytes) {
  if (typeof bytes !== "number" || !Number.isFinite(bytes) || bytes < 0) {
    return "";
  }
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  const text = unit === 0 || value >= 100 ? String(Math.round(value)) : value.toFixed(1);
  return text + " " + units[unit];
}

// Windows paths (drive letter or UNC) compare without case; POSIX paths compare exactly.
export function pathKey(path) {
  const text = String(path).trim();
  if (/^[A-Za-z]:[\\/]/.test(text) || text.startsWith("\\\\")) {
    return "w:" + text.replace(/\//g, "\\").replace(/\\+$/, "").toLowerCase();
  }
  return "p:" + (text.length > 1 ? text.replace(/\/+$/, "") : text);
}

function newItem(path, size, id) {
  const { dir, name } = splitPath(path);
  return {
    id,
    path,
    name,
    dir,
    size: typeof size === "number" ? size : null,
    kind: ersKind(name),
    isZip: isZipName(name),
    status: STATUS_PENDING,
    message: "",
  };
}

export class CaseFilesState {
  constructor(onChange) {
    this.onChange = onChange || (() => {});
    this.items = [];
    this.notices = []; // {type: "duplicate"|"error"|"info"|"limit", name, message}
    this.busy = false;
    this.createdCaseId = null;
    this.createdName = null;
    this.addCase = null; // case the results of addAll() belong to
    this.nextId = 1;
  }

  // Forgets every per-file result (the list stays): used when another case is opened, because a file
  // that went into case A has not been added to case B.
  resetResults() {
    for (const item of this.items) {
      item.status = STATUS_PENDING;
      item.code = "";
      item.message = "";
    }
    this.notices = [];
    this.addCase = null;
    this.changed();
  }

  changed() {
    this.onChange(this);
  }

  // entries: [{path, size?}] or plain path strings. Returns how many were added.
  add(entries) {
    this.notices = this.notices.filter((n) => n.type === "info");
    const known = new Set(this.items.map((item) => pathKey(item.path)));
    let added = 0;
    for (const entry of entries) {
      const path = String(typeof entry === "string" ? entry : entry.path).trim();
      if (path === "") {
        continue;
      }
      const key = pathKey(path);
      if (known.has(key)) {
        this.notices.push({ type: "duplicate", name: splitPath(path).name, message: "" });
        continue;
      }
      if (this.items.length >= MAX_ITEMS) {
        this.notices.push({ type: "limit", name: splitPath(path).name, message: String(MAX_ITEMS) });
        continue;
      }
      known.add(key);
      this.items.push(newItem(path, typeof entry === "string" ? null : entry.size, this.nextId++));
      added += 1;
    }
    this.changed();
    return added;
  }

  addErrors(errors) {
    for (const error of errors || []) {
      this.notices.push({
        type: "error",
        name: splitPath(error.path || "").name,
        message: String(error.message || error.error || ""),
      });
    }
    this.changed();
  }

  addNotice(type, message) {
    this.notices = this.notices.filter((n) => !(n.type === type && n.message === message));
    this.notices.push({ type, name: "", message });
    this.changed();
  }

  clearNotices() {
    this.notices = [];
    this.changed();
  }

  remove(id) {
    if (this.busy) {
      return;
    }
    this.items = this.items.filter((item) => item.id !== id);
    this.changed();
  }

  clear() {
    if (this.busy) {
      return;
    }
    this.items = [];
    this.notices = [];
    this.createdCaseId = null;
    this.createdName = null;
    this.changed();
  }

  get canCreate() {
    return !this.busy && this.items.length > 0;
  }

  // Adds every file of the list to the case that is already open (no case is created).
  // deps: {openPath(path) -> {status, code?, message?}}; caseId: the case the files go into (results are
  // tied to it: another case starts from a clean list). Same per-file results as create(); resolves to
  // {state: "ok"|"partial"|"blocked", ok, total}.
  async addAll(deps, caseId = null) {
    if (this.busy) {
      return { state: "blocked", reason: "busy" };
    }
    if (this.items.length === 0) {
      return { state: "blocked", reason: "files" };
    }
    if (this.addCase !== caseId) {
      this.resetResults();
      this.addCase = caseId;
    }
    this.busy = true;
    this.notices = [];
    this.changed();
    await this.openEach(deps);
    this.busy = false;
    const total = this.items.length;
    const ok = this.items.filter((item) => item.status === STATUS_OK).length;
    if (ok === total) {
      this.items = [];
    }
    this.changed();
    return { state: ok === total ? "ok" : "partial", ok, total };
  }

  async openEach(deps) {
    for (const item of this.items) {
      if (item.status === STATUS_OK) {
        continue;
      }
      item.status = STATUS_WORKING;
      item.message = "";
      this.changed();
      let result;
      try {
        result = await deps.openPath(item.path);
      } catch (err) {
        result = { status: STATUS_FAILED, message: String(err && err.message ? err.message : err) };
      }
      item.status = result.status === STATUS_OK || result.status === STATUS_REFUSED ? result.status : STATUS_FAILED;
      item.code = result.code || "";
      item.message = result.message || "";
      this.changed();
    }
  }

  // deps: {createCase(name) -> {id}, selectCase(id), openPath(path) -> {status: "ok"|"failed"|"refused", code?, message?}}
  // Creates the case (once: a retry with the same name reuses it) and opens each ZIP in sequence;
  // one file failing never stops the others nor empties the list. Resolves to
  // {state: "ok"|"partial"|"blocked", caseId, ok, total}.
  async create(name, deps) {
    const trimmed = String(name).trim();
    if (this.busy) {
      return { state: "blocked", reason: "busy" };
    }
    if (trimmed === "") {
      return { state: "blocked", reason: "name" };
    }
    if (this.items.length === 0) {
      return { state: "blocked", reason: "files" };
    }
    this.busy = true;
    this.notices = [];
    this.changed();
    try {
      if (this.createdCaseId === null || this.createdName !== trimmed) {
        const restarting = this.createdCaseId !== null;
        const created = await deps.createCase(trimmed);
        this.createdCaseId = created.id;
        this.createdName = trimmed;
        if (restarting) {
          // Another name means another case: nothing was opened in it yet.
          for (const item of this.items) {
            item.status = STATUS_PENDING;
            item.code = "";
            item.message = "";
          }
          this.notices.push({ type: "restart", name: "", message: "" });
        }
      }
    } catch (err) {
      this.busy = false;
      this.notices.push({ type: "error", name: "", message: String(err && err.message ? err.message : err) });
      this.changed();
      return { state: "blocked", reason: "case" };
    }
    try {
      await deps.selectCase(this.createdCaseId);
    } catch (err) {
      // The case exists (the name was accepted); only opening it failed: not a name error.
      this.busy = false;
      this.notices.push({ type: "error", name: "", message: String(err && err.message ? err.message : err) });
      this.changed();
      return { state: "blocked", reason: "select" };
    }
    await this.openEach(deps);
    this.busy = false;
    const total = this.items.length;
    const ok = this.items.filter((item) => item.status === STATUS_OK).length;
    const caseId = this.createdCaseId;
    if (ok === total) {
      this.items = [];
      this.createdCaseId = null;
      this.createdName = null;
      this.changed();
      return { state: "ok", caseId, ok, total };
    }
    this.changed();
    return { state: "partial", caseId, ok, total };
  }
}

// Maps the error of POST /collections to a result item; `text` supplies the plain-language strings.
export function refusalResult(error, text) {
  const code = error && error.code ? String(error.code) : "";
  let message;
  if (code === "duplicate_collection") {
    message = text.duplicate;
  } else if (code === "job_running") {
    message = text.busy;
  } else {
    message = String(error && error.message ? error.message : error);
  }
  return { status: error && error.status === 409 ? STATUS_REFUSED : STATUS_FAILED, code, message };
}

// Decides between the native file dialog and the built-in browser. `api` is api.fs.
// browse() resolves to {outcome, ...}: "paths" {paths, errors} | "cancelled" | "timed_out" {minutes}
// | "fallback" (open the built-in browser) | "busy" | "error" {message}.
export class PickFlow {
  constructor(fs, timeoutMinutes = 15) {
    this.fs = fs;
    this.timeoutMinutes = timeoutMinutes;
    this.native = null; // null = unknown until capabilities() answers
    this.picking = false;
  }

  async loadCapabilities() {
    if (this.native !== null) {
      return this.native;
    }
    try {
      const caps = await this.fs.capabilities();
      this.native = Boolean(caps && caps.native && caps.native.available);
    } catch (err) {
      this.native = false;
    }
    return this.native;
  }

  async browse() {
    if (this.picking) {
      return { outcome: "busy" };
    }
    if (!(await this.loadCapabilities())) {
      return { outcome: "fallback" };
    }
    this.picking = true;
    try {
      const result = await this.fs.pick("zip", true);
      if (result.timed_out) {
        return { outcome: "timed_out", minutes: this.timeoutMinutes };
      }
      if (result.cancelled) {
        return { outcome: "cancelled" };
      }
      return { outcome: "paths", paths: result.paths || [], errors: result.errors || [] };
    } catch (err) {
      if (err && err.code === "picker_unavailable") {
        this.native = false;
        return { outcome: "fallback" };
      }
      if (err && err.code === "picker_failed") {
        this.native = false;
        return { outcome: "fallback", failed: true };
      }
      if (err && err.code === "picker_busy") {
        return { outcome: "busy" };
      }
      return { outcome: "error", message: String(err && err.message ? err.message : err) };
    } finally {
      this.picking = false;
    }
  }

  async cancel() {
    try {
      await this.fs.cancelPick();
    } catch (err) {
      // The pending pick answers on its own.
    }
  }
}


// ---- reduced sources, not indexed sources and "Large files" (R3c, R4c); pure helpers the opening loads anyway ----------
// (the viewer and the "Not indexed" group are in file-viewer-model.js, which loads on demand)

// A string of strings.js, or the English default until the key is added there.
export function label(key, fallback, values) {
  const text = Object.prototype.hasOwnProperty.call(S, key) && typeof S[key] === "string" ? S[key] : fallback;
  return values ? format(text, values) : text;
}

export function countText(n) {
  return n.toLocaleString("en-US");
}

// A source the indexer skipped (not selected, not indexable or over the limit): listed, never read into the index.
export function isNotIndexed(source) {
  return Boolean(source) && source.status === "skipped";
}

export function isReduced(source) {
  return Boolean(source) && source.reduced !== null && source.reduced !== undefined && typeof source.reduced === "object";
}

export function safeNumber(value) {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
}

export function formatMb(mb) {
  return mb >= 1024 && mb % 1024 === 0 ? mb / 1024 + " GB" : mb + " MB";
}

// {from, to} as text; both "unknown" when the server did not know the times.
function rangeParts(reduced) {
  const from = safeNumber(reduced.skipped_from_ts);
  const to = safeNumber(reduced.skipped_to_ts);
  if (from === null || to === null) {
    const unknown = label("srcReducedUnknownTime", "unknown time");
    return { from: unknown, to: unknown };
  }
  return { from: formatTs(from), to: formatTs(to) };
}

export function badgeText() {
  return label("srcReducedBadge", "Reduced");
}

// The `title` of the badge: what was indexed, what was skipped (size, lines, times) and the limit.
export function reducedTitle(source) {
  if (!isReduced(source)) {
    return "";
  }
  const reduced = source.reduced;
  const size = safeNumber(source.size);
  const head = safeNumber(reduced.head_end);
  const start = safeNumber(reduced.tail_start);
  const tail = size !== null && start !== null && size >= start ? size - start : null;
  return label(
    "srcReducedTitle",
    "Indexed the first {head} and the last {tail} of {size}. Skipped {skipped} in between ({lines} lines, {from} to {to}). Per-file limit: {cap}.",
    {
      head: head === null ? "?" : formatSize(head),
      tail: tail === null ? "?" : formatSize(tail),
      size: size === null ? "?" : formatSize(size),
      skipped: formatSize(safeNumber(reduced.skipped_bytes) || 0),
      lines: countText(safeNumber(reduced.skipped_lines) || 0),
      ...rangeParts(reduced),
      cap: formatSize(safeNumber(reduced.cap) || 0),
    },
  );
}

export const FLAG_AFTER_SKIP = 128;

export function isAfterSkip(entry) {
  return Boolean(entry) && Number.isInteger(entry.flags) && (entry.flags & FLAG_AFTER_SKIP) !== 0;
}

// The note of the record detail for the first record after a skipped stretch; "" for any other record. `source` is the
// source info of the record ({reduced} when known); without the numbers the note still says that lines were skipped.
export function afterSkipNote(entry, source) {
  if (!isAfterSkip(entry)) {
    return "";
  }
  if (!isReduced(source)) {
    return label("detailAfterSkipBare", "Lines before this record were skipped.");
  }
  const reduced = source.reduced;
  return label("detailAfterSkip", "Lines before this record were skipped: {lines} lines ({skipped}), {from} to {to}.", {
    lines: countText(safeNumber(reduced.skipped_lines) || 0),
    skipped: formatSize(safeNumber(reduced.skipped_bytes) || 0),
    ...rangeParts(reduced),
  });
}

// ---- "Large files" in the opening of a case -----------------------------------------------------------------------

export const HEAD_MB = 1;
export const DEFAULT_CAP_MB = 256;
export const CAP_OPTIONS = [
  { value: String(DEFAULT_CAP_MB), mb: DEFAULT_CAP_MB, key: "capDefault", fallback: "First 1 MB and last 256 MB (default)" },
  { value: "1024", mb: 1024, key: "capLarge", fallback: "First 1 MB and last 1 GB" },
  { value: "0", mb: 0, key: "capWhole", fallback: "Whole file (slow, large database)" },
];

// [value, text] pairs for the select (the same shape main.js gives fillSelect).
export function capChoices() {
  return CAP_OPTIONS.map((option) => [option.value, label(option.key, option.fallback)]);
}

// The limit in MB the select stands for: 256, 1024 or 0 (no limit); anything else is the default.
export function capMbOf(value) {
  const found = CAP_OPTIONS.find((option) => option.value === String(value));
  return found ? found.mb : DEFAULT_CAP_MB;
}

// The note under the preview of the sources: what a file over the limit gets. "" when there is no limit.
export function previewCappedText(capMb) {
  const mb = capMbOf(capMb);
  if (mb === 0) {
    return "";
  }
  return label(
    "previewCapped",
    "Over the {cap} per-file limit: only the first {head} and the last {cap} will be indexed.",
    { cap: formatMb(mb), head: formatMb(HEAD_MB) },
  );
}
