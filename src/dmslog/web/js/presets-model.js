// Pure model of the presets panel (no DOM): grouping by family, what the save button does, import
// and export helpers. The server (store/presets.py) is the authority; this only avoids round trips.
import { MAX_NAME, sanitizeName } from "./filter-tree-model.js";

export const MAX_USER_PRESETS = 200; // store/presets.py MAX_PRESETS
export const FORMATS = ["json", "toml"];

const same = (a, b) => a.toLowerCase() === b.toLowerCase();
const familyOf = (p) => p.family || "";

// Same identity as the server: name ignoring letter case + family.
export function sameIdentity(a, b) {
  return same(a.name, b.name) && familyOf(a) === familyOf(b);
}

// Groups for display: presets of every adapter first, then one group per family (alphabetical);
// inside a group the curated ones come first, then the user's, each by name.
export function groupByFamily(presets) {
  const groups = new Map();
  for (const preset of presets) {
    const key = familyOf(preset);
    if (!groups.has(key)) {
      groups.set(key, []);
    }
    groups.get(key).push(preset);
  }
  const order = (a, b) =>
    Number(Boolean(b.builtin)) - Number(Boolean(a.builtin)) || a.name.toLowerCase().localeCompare(b.name.toLowerCase());
  return [...groups.keys()]
    .sort((a, b) => (a === "" ? -1 : b === "" ? 1 : a.localeCompare(b)))
    .map((key) => ({ family: key === "" ? null : key, items: groups.get(key).sort(order) }));
}

// Input: {raw, family: string|null, presets (all listed), renaming: preset|null, confirmed: preset-like|null}.
// Output: {action: "create"|"overwrite"|"rename"|"confirmOverwrite"|"error", name, family, target?, error?}
// where error is "empty" | "tooLong" | "duplicate" | "reserved" | "tooMany".
export function planPresetSave({ raw, family = null, presets, renaming = null, confirmed = null }) {
  const name = sanitizeName(raw);
  const fam = family || null;
  if (name === "") {
    return { action: "error", name, family: fam, error: "empty" };
  }
  if ([...name].length > MAX_NAME) {
    return { action: "error", name, family: fam, error: "tooLong" };
  }
  const wanted = { name, family: fam };
  const clash = presets.find((p) => sameIdentity(p, wanted));
  if (renaming) {
    const keepsFamily = { name, family: renaming.family || null };
    const other = presets.find((p) => sameIdentity(p, keepsFamily) && !sameIdentity(p, renaming));
    if (other) {
      return { action: "error", name, family: renaming.family || null, error: other.builtin ? "reserved" : "duplicate" };
    }
    return { action: "rename", name, family: renaming.family || null, target: renaming };
  }
  if (clash && clash.builtin) {
    return { action: "error", name, family: fam, error: "reserved" };
  }
  if (clash) {
    if (confirmed && sameIdentity(confirmed, wanted)) {
      return { action: "overwrite", name, family: fam, target: clash };
    }
    return { action: "confirmOverwrite", name, family: fam, target: clash };
  }
  if (presets.filter((p) => !p.builtin).length >= MAX_USER_PRESETS) {
    return { action: "error", name, family: fam, error: "tooMany" };
  }
  return { action: "create", name, family: fam };
}

// The body of a create/update request.
export function presetBody({ name, family, description, filter }) {
  return { name, family: family || null, description: description || "", filter };
}

// "json" or "toml" from a file name, null for anything else.
export function formatFromFileName(fileName) {
  const match = /\.(json|toml)$/i.exec(String(fileName || ""));
  return match ? match[1].toLowerCase() : null;
}

// Reads the text of an import file into the request payload: JSON is parsed here, TOML goes as text.
export function importPayload(format, text) {
  if (format === "json") {
    return JSON.parse(String(text).replace(/^\uFEFF/, ""));
  }
  return String(text);
}

// What the server answered to an import -> numbers and the list of ignored items.
export function importSummary(report) {
  const ignored = Array.isArray(report && report.ignored) ? report.ignored : [];
  return {
    added: Number((report && report.added) || 0),
    updated: Number((report && report.updated) || 0),
    ignored: ignored.length,
    ignoredItems: ignored.map((i) => ({ name: String(i.name || ""), message: String(i.message || i.code || "") })),
  };
}

// The text and media type to download for an export answer.
export function exportFile(answer) {
  if (answer.format === "toml") {
    return { text: String(answer.text || ""), type: "application/toml;charset=utf-8" };
  }
  return { text: JSON.stringify(answer.data, null, 2), type: "application/json;charset=utf-8" };
}
