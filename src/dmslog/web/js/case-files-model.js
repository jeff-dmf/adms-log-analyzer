// Pure model of the "files of a new case" list: names, kinds, duplicates, and the create flow.
// No DOM here; the DOM side is case-files.js.

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
    this.nextId = 1;
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
      await deps.selectCase(this.createdCaseId);
    } catch (err) {
      this.busy = false;
      this.notices.push({ type: "error", name: "", message: String(err && err.message ? err.message : err) });
      this.changed();
      return { state: "blocked", reason: "case" };
    }
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
  if (code === "different_guid") {
    message = text.conflictServer;
  } else if (code === "different_capture") {
    message = text.conflictCapture;
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
