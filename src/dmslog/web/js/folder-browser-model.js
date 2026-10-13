// Pure model of the built-in folder browser (GET /api/fs/list): current folder, entries, the
// selection of ZIPs (kept while navigating), and the remembered start folder.
import { pathKey } from "./case-files-model.js";

export const STORAGE_KEY = "dmslog.browseDir";

export function readRememberedDir(storage) {
  try {
    const value = storage ? storage.getItem(STORAGE_KEY) : null;
    return typeof value === "string" && value !== "" ? value : null;
  } catch (err) {
    return null;
  }
}

export function rememberDir(storage, path) {
  try {
    if (storage) {
      storage.setItem(STORAGE_KEY, path);
    }
  } catch (err) {
    // Not persisted; the browser still works.
  }
}

// Breadcrumb parts of a path: [{label, path}] from the root to the folder. Both separators.
export function breadcrumbs(path) {
  const text = String(path || "");
  if (text === "") {
    return [];
  }
  const sep = text.includes("\\") && !text.startsWith("/") ? "\\" : "/";
  const parts = text.split(/[\\/]+/).filter((p) => p !== "");
  const crumbs = [];
  if (sep === "/") {
    crumbs.push({ label: "/", path: "/" });
    let acc = "";
    for (const part of parts) {
      acc += "/" + part;
      crumbs.push({ label: part, path: acc });
    }
    return crumbs;
  }
  if (text.startsWith("\\\\")) {
    // UNC: \\server\share\...
    let acc = "\\\\";
    parts.forEach((part, index) => {
      acc += (index === 0 ? "" : "\\") + part;
      crumbs.push({ label: part, path: acc });
    });
    return crumbs;
  }
  let acc = "";
  parts.forEach((part, index) => {
    acc = index === 0 ? part + "\\" : acc.replace(/\\$/, "") + "\\" + part;
    crumbs.push({ label: part, path: acc });
  });
  return crumbs;
}

export class FolderBrowser {
  // fs: api.fs; storage: window.localStorage or null; onChange(model) after each state change.
  constructor({ fs, storage, onChange }) {
    this.fs = fs;
    this.storage = storage || null;
    this.onChange = onChange || (() => {});
    this.path = "";
    this.parent = null;
    this.entries = [];
    this.shortcuts = [];
    this.truncated = false;
    this.skipped = 0;
    this.otherFiles = 0;
    this.status = "idle"; // idle | loading | ready | error
    this.error = null; // {code, message}
    this.showHidden = false;
    this.selected = new Map(); // pathKey -> {path, size}
    this.anchor = null; // path of the last clicked ZIP (for Shift ranges)
    this.seq = 0;
  }

  changed() {
    this.onChange(this);
  }

  get visible() {
    return this.entries.filter((entry) => this.showHidden || !entry.hidden);
  }

  get selectedCount() {
    return this.selected.size;
  }

  selectedEntries() {
    return [...this.selected.values()];
  }

  isSelected(entry) {
    return this.selected.has(pathKey(entry.path));
  }

  selectable(entry) {
    return entry.type === "zip" && !entry.broken;
  }

  setShowHidden(value) {
    this.showHidden = Boolean(value);
    this.changed();
  }

  // First load: the remembered folder, or the home folder when it is gone.
  async open() {
    const remembered = readRememberedDir(this.storage);
    if (remembered !== null) {
      const ok = await this.load(remembered);
      if (ok || !this.error || this.error.code !== "path_not_found") {
        return ok;
      }
    }
    return this.load(undefined);
  }

  // Navigates; false when the folder could not be listed (the previous listing stays).
  async load(path) {
    const seq = ++this.seq;
    this.status = "loading";
    this.error = null;
    this.changed();
    let data;
    try {
      data = await this.fs.list(path);
    } catch (err) {
      if (seq !== this.seq) {
        return false;
      }
      this.status = "error";
      this.error = { code: err && err.code ? err.code : "", message: String(err && err.message ? err.message : err) };
      this.changed();
      return false;
    }
    if (seq !== this.seq) {
      return false; // a newer navigation took over
    }
    this.path = data.path;
    this.parent = data.parent === undefined ? null : data.parent;
    this.entries = Array.isArray(data.entries) ? data.entries : [];
    this.shortcuts = Array.isArray(data.shortcuts) ? data.shortcuts : [];
    this.truncated = Boolean(data.truncated);
    this.skipped = Number(data.skipped || 0);
    this.otherFiles = Number(data.other_files || 0);
    this.anchor = null;
    this.status = "ready";
    rememberDir(this.storage, data.path);
    this.changed();
    return true;
  }

  // mode: "toggle" (checkbox / Ctrl), "only" (plain click) or "range" (Shift, from the anchor).
  select(entry, mode) {
    if (!this.selectable(entry)) {
      return;
    }
    const key = pathKey(entry.path);
    if (mode === "range" && this.anchor !== null) {
      const zips = this.visible.filter((e) => this.selectable(e));
      const from = zips.findIndex((e) => e.path === this.anchor);
      const to = zips.findIndex((e) => e.path === entry.path);
      if (from >= 0 && to >= 0) {
        const [lo, hi] = from < to ? [from, to] : [to, from];
        for (const e of zips.slice(lo, hi + 1)) {
          this.selected.set(pathKey(e.path), { path: e.path, size: e.size });
        }
        this.changed();
        return;
      }
    }
    if (mode === "only") {
      this.selected.clear();
      this.selected.set(key, { path: entry.path, size: entry.size });
    } else if (this.selected.has(key)) {
      this.selected.delete(key);
    } else {
      this.selected.set(key, { path: entry.path, size: entry.size });
    }
    this.anchor = entry.path;
    this.changed();
  }

  selectAllVisible() {
    for (const e of this.visible) {
      if (this.selectable(e)) {
        this.selected.set(pathKey(e.path), { path: e.path, size: e.size });
      }
    }
    this.changed();
  }

  clearSelection() {
    this.selected.clear();
    this.changed();
  }
}

// Focus handling of the modal. `keys` are the stable keys (path or index) of the focusable
// elements after a render; returns the key to focus again or null (fall back to the list).
export function restoreFocusKey(previousKey, keys) {
  return previousKey !== null && previousKey !== undefined && keys.includes(previousKey) ? previousKey : null;
}

// Tab trap: `index` is the position of the focused element among the dialog's focusable nodes
// (-1 when it is not one of them), `inside` tells if it is still inside the dialog.
// Returns "first", "last" or null (let the browser move the focus).
export function trapTarget(count, index, inside, shift) {
  if (count === 0) {
    return null;
  }
  if (!inside || index < 0) {
    return shift ? "last" : "first";
  }
  if (shift && index === 0) {
    return "last";
  }
  if (!shift && index === count - 1) {
    return "first";
  }
  return null;
}
