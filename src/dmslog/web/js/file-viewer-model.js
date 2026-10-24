// Pure model of the text viewer for files that are not indexed (R4c): which skipped sources can be viewed, how they are
// grouped, the load/reveal state of the viewer. The notes of reduced sources (badge, detail) are in case-files-model.js,
// which the opening loads anyway; this module loads with the tree or the viewer. No DOM here; the DOM side is file-viewer.js. Everything from the API is untrusted text.
import { countText, formatSize, isNotIndexed, label, safeNumber } from "./case-files-model.js";

// ---- which skipped sources can be viewed --------------------------------------------------------------------------

// The closed list of the server (store/filetext.py TEXT_EXTENSIONS); the server decides in the end.
export const TEXT_EXTENSIONS = new Set([
  ".xml", ".xsd", ".xsl", ".xslt", ".xmlold", ".config", ".csc", ".sql", ".init", ".ini", ".html", ".htm", ".old",
  ".example", ".csv", ".json",
]);
export const REASON_NOT_INDEXABLE = "not_indexable";
export const REASON_OUT_OF_SCOPE = "out_of_scope";
const LIMIT_REASONS = new Set(["too_large", "ratio", "encrypted"]);
export const DEFAULT_MAX_VIEW_BYTES = 4 * 1024 * 1024;


// ".xml" for "a/B.XML"; "" when the name has no extension. "x.db3-wal" keeps its whole tail (".db3-wal").
export function extOf(name) {
  const base = String(name === null || name === undefined ? "" : name).split(/[\\/]/).pop();
  const at = base.lastIndexOf(".");
  return at <= 0 ? "" : base.slice(at).toLowerCase();
}

export function isViewable(source) {
  return isNotIndexed(source) && source.error === REASON_NOT_INDEXABLE && TEXT_EXTENSIONS.has(extOf(source.member || source.name));
}

const TYPE_LABELS = [
  [[".blg"], "ntTypeBlg", "Performance counter log (.blg)"],
  [[".etl"], "ntTypeEtl", "Event trace (.etl)"],
  [[".evtx"], "ntTypeEvtx", "Windows event log (.evtx)"],
  [[".db3", ".db3-wal", ".db3-shm"], "ntTypeDb3", "SQLite database (.db3)"],
];

// "Performance counter log (.blg)" for a known binary type; "" for the others.
export function typeLabel(ext) {
  for (const [exts, key, fallback] of TYPE_LABELS) {
    if (exts.includes(ext)) {
      return label(key, fallback);
    }
  }
  return "";
}

// ---- the "Not indexed" group --------------------------------------------------------------------------------------

export const NI_ROOT_KEY = "\u0000ni/"; // holds a NUL, which a member name never has: it cannot collide with a folder
export const NI_GROUPS = [
  { id: "other", key: "ntOther", fallback: "Other logs (not selected)" },
  { id: "text", key: "ntText", fallback: "Text files" },
  { id: "binary", key: "ntBinary", fallback: "Binary files" },
  { id: "limit", key: "ntLimit", fallback: "Over the limit" },
];

// "other" | "text" | "binary" | "limit": the subgroup of a skipped source.
export function notIndexedGroup(source) {
  if (LIMIT_REASONS.has(source.error)) {
    return "limit";
  }
  if (source.error === REASON_NOT_INDEXABLE) {
    return isViewable(source) ? "text" : "binary";
  }
  return "other";
}

// The node of the tree (same shape as source-tree-model.js nodes) for the skipped sources: a folder "Not indexed (n)"
// with a folder per subgroup and, inside it, a folder per extension ("" -> "no extension") with the files. Its nodes are
// never checkable (ids is empty); allIds are the sources the filter can find. null when there is nothing to show.
export function buildNotIndexed(sources) {
  const skipped = sources.filter(isNotIndexed);
  if (skipped.length === 0) {
    return null;
  }
  const folder = (key, name, extra = {}) => ({
    key, name, folder: true, notIndexed: true, children: [], ids: [], allIds: [], files: 0, records: 0, unreadable: 0,
    sensitive: 0, ...extra,
  });
  const root = folder(NI_ROOT_KEY, label("ntGroup", "Not indexed ({n})", { n: countText(skipped.length) }), { baseName: "" });
  for (const group of NI_GROUPS) {
    const members = skipped.filter((s) => notIndexedGroup(s) === group.id);
    if (members.length === 0) {
      continue;
    }
    const sub = folder(NI_ROOT_KEY + group.id + "/", label(group.key, group.fallback) + " (" + countText(members.length) + ")");
    const byExt = new Map();
    for (const source of members) {
      const ext = extOf(source.member || source.name);
      if (!byExt.has(ext)) {
        byExt.set(ext, []);
      }
      byExt.get(ext).push(source);
    }
    const exts = [...byExt.keys()].sort((a, b) => (byExt.get(b).length - byExt.get(a).length) || (a < b ? -1 : a > b ? 1 : 0));
    for (const ext of exts) {
      const list = byExt.get(ext).sort((a, b) => (String(a.member || a.name) < String(b.member || b.name) ? -1 : 1));
      const shown = ext === "" ? label("ntNoExt", "No extension") : ext;
      const kind = group.id === "binary" ? typeLabel(ext) : "";
      const node = folder(sub.key + ext + "/", (kind !== "" ? kind : shown) + " (" + countText(list.length) + ")");
      for (const source of list) {
        const name = String(source.member || source.name || source.id);
        node.children.push({
          key: node.key + "#" + source.id, name, folder: false, notIndexed: true, viewable: isViewable(source), children: [],
          ids: [], allIds: [source.id], files: 1, records: 0, unreadable: 0, sensitive: 0, source,
        });
        node.allIds.push(source.id);
        node.files += 1;
      }
      sub.children.push(node);
      sub.allIds.push(...node.allIds);
      sub.files += node.files;
    }
    root.children.push(sub);
    root.allIds.push(...sub.allIds);
    root.files += sub.files;
  }
  return root;
}

// ---- the "Reduced" badge and the notes --------------------------------------------------------------------------

// The rows of the "Not indexed" group on screen, for the flat list: [{node, level, open}]. `open` is a Set of folder keys;
// `all` opens every folder (a filter is typed). Same keys as the tree, so a folder means the same in both views.
export function niRows(root, open, all) {
  const rows = [];
  const walk = (node, level) => {
    const isOpen = node.folder && (all || open.has(node.key));
    rows.push({ node, level, open: isOpen });
    if (isOpen) {
      node.children.forEach((child) => walk(child, level + 1));
    }
  };
  if (root !== null) {
    walk(root, 1);
  }
  return rows;
}

// ---- the viewer ---------------------------------------------------------------------------------------------------

// The text and the state of a failed load by the code of the server's answer.
export function viewerError(error) {
  const status = error && typeof error.status === "number" ? error.status : 0;
  const code = error && error.code ? String(error.code) : "";
  if (status === 404 || code === "source_not_found") {
    return { code: "not_found", text: label("fvNotFound", "This file is not in the case.") };
  }
  if (status === 409 || code === "not_viewable") {
    return { code: "not_viewable", text: label("fvNotViewable", "This file cannot be shown as text.") };
  }
  if (status === 413 || code === "file_too_large") {
    const limit = safeNumber(error.limit !== undefined ? error.limit : error.body && error.body.limit);
    return {
      code: "too_large",
      text: label("fvTooLarge", "This file is larger than {limit} and is not shown.", {
        limit: formatSize(limit === null ? DEFAULT_MAX_VIEW_BYTES : limit),
      }),
    };
  }
  if (status === 422 || code === "binary_content") {
    return { code: "binary", text: label("fvBinary", "This is not a text file.") };
  }
  if (status === 410 || code === "container_missing") {
    return { code: "missing", text: label("fvMissing", "The collection file is no longer available.") };
  }
  if (error && error.name === "AbortError") {
    return { code: "aborted", text: "" };
  }
  const detail = error && error.message ? " " + String(error.message) : "";
  return { code: "failed", text: label("fvFailed", "The file could not be read.") + detail };
}

// A text answer of the server as plain data: strings and non-negative integers, or null for something else.
export function normalizeText(data) {
  if (!data || typeof data !== "object" || typeof data.text !== "string") {
    return null;
  }
  return {
    sourceId: data.source_id,
    name: typeof data.name === "string" ? data.name : "",
    member: typeof data.member === "string" ? data.member : "",
    size: safeNumber(data.size),
    encoding: typeof data.encoding === "string" ? data.encoding : "",
    lines: safeNumber(data.lines) || 0,
    hidden: safeNumber(data.hidden) || 0,
    revealed: data.revealed === true,
    withheld: data.withheld === true,
    text: data.text,
  };
}

export function factsText(file) {
  const parts = [];
  if (file.size !== null) {
    parts.push(formatSize(file.size));
  }
  parts.push(label("fvLines", "{n} lines", { n: countText(file.lines) }));
  if (file.encoding !== "") {
    parts.push(file.encoding);
  }
  return parts.join(" - ");
}

export function hiddenText(file) {
  if (file.withheld && !file.revealed) {
    return label("fvWithheld", "Content hidden: this file mentions {n} credentials", { n: countText(file.hidden) });
  }
  if (file.hidden === 0) {
    return label("fvNoneHidden", "No values hidden");
  }
  return label(file.revealed ? "fvHiddenShown" : "fvHiddenCount", file.revealed ? "{n} hidden values shown" : "{n} values hidden", {
    n: countText(file.hidden),
  });
}

// The state of one opening of the viewer. fetchText(caseId, id, {reveal}, signal) -> the server's answer. Nothing is
// kept after reset(): the revealed text lives only in this object for as long as the file is open.
export class ViewerState {
  constructor(fetchText, onChange) {
    this.fetchText = fetchText;
    this.onChange = onChange || (() => {});
    this.generation = 0;
    this.controller = null;
    this.reset();
  }

  reset() {
    this.cancel();
    this.generation += 1;
    this.caseId = null;
    this.source = null;
    this.phase = "idle"; // "idle" | "loading" | "ready" | "error"
    this.masked = null; // the answer with the hidden values masked
    this.revealedFile = null; // the answer with the original values, after the click
    this.showing = false; // the revealed text is the one on screen
    this.error = null;
  }

  cancel() {
    if (this.controller) {
      this.controller.abort();
      this.controller = null;
    }
  }

  get file() {
    return this.showing && this.revealedFile ? this.revealedFile : this.masked;
  }

  changed() {
    this.onChange(this);
  }

  async open(caseId, source) {
    this.reset();
    this.caseId = caseId;
    this.source = source;
    this.phase = "loading";
    this.changed();
    const loaded = await this.load(false);
    if (loaded !== null) {
      this.masked = loaded;
      this.phase = "ready";
      this.changed();
    }
  }

  // Shows the original values (one request, reveal=1); a second call hides them again without a request.
  async toggleReveal() {
    if (this.phase !== "ready" || !this.masked || this.masked.hidden === 0) {
      return;
    }
    if (this.showing) {
      this.showing = false;
      this.changed();
      return;
    }
    if (this.revealedFile) {
      this.showing = true;
      this.changed();
      return;
    }
    const generation = this.generation;
    const loaded = await this.load(true, true);
    if (loaded !== null && generation === this.generation) {
      this.revealedFile = loaded;
      this.showing = true;
      this.changed();
    }
  }

  // keepReady: a failed reveal keeps the masked text on screen and reports the error next to it.
  async load(reveal, keepReady = false) {
    this.cancel();
    const generation = this.generation;
    const controller = typeof AbortController === "undefined" ? null : new AbortController();
    this.controller = controller;
    try {
      const data = await this.fetchText(this.caseId, this.source.id, { reveal }, controller ? controller.signal : undefined);
      if (generation !== this.generation) {
        return null;
      }
      const file = normalizeText(data);
      if (file === null) {
        throw new Error(label("fvFailed", "The file could not be read."));
      }
      this.error = null;
      return file;
    } catch (err) {
      if (generation !== this.generation || (err && err.name === "AbortError")) {
        return null;
      }
      this.error = viewerError(err);
      if (!keepReady) {
        this.phase = "error";
      }
      this.changed();
      return null;
    } finally {
      if (this.controller === controller) {
        this.controller = null;
      }
    }
  }
}
