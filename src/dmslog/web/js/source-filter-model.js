// Pure model of the source-list filter (no DOM): case-insensitive substring match on the log file
// name, the match range for highlighting, and the per-session persistence of the typed text.
// The DOM layer lives in source-filter.js.

export const STORAGE_KEY = "dmslog.sourceFilter";
export const DEBOUNCE_MS = 120;

// File name without any directory part (either separator).
export function baseName(name) {
  const text = name === null || name === undefined ? "" : String(name);
  const cut = Math.max(text.lastIndexOf("/"), text.lastIndexOf("\\"));
  return cut >= 0 ? text.slice(cut + 1) : text;
}

// Case folding that is safe for any script: a fixed locale (no Turkish dotted-i surprises) and NFC.
export function fold(text) {
  return String(text).normalize("NFC").toLocaleLowerCase("en-US");
}

// Folds the WHOLE string (context rules such as the Greek final sigma apply, exactly as in fold())
// and maps every folded unit to the start and end of the original code point it came from, so a
// match maps back to whole characters (never half a surrogate pair). Folding may change lengths ("İ").
function foldWithMap(text) {
  const normal = String(text).normalize("NFC");
  const folded = normal.toLocaleLowerCase("en-US");
  const start = []; // folded index -> start of its original code point
  const end = [];   // folded index -> end of its original code point
  let index = 0;
  for (const ch of normal) {
    const units = ch.toLocaleLowerCase("en-US").length;
    for (let k = 0; k < units; k += 1) {
      start.push(index);
      end.push(index + ch.length);
    }
    index += ch.length;
  }
  if (start.length !== folded.length) {
    // Unexpected length change: map every unit to the whole name (still a correct match).
    return { folded, start: Array(folded.length).fill(0), end: Array(folded.length).fill(normal.length) };
  }
  return { folded, start, end };
}

// {start, end} of the first case-insensitive occurrence of `query` in `name` (indexes into the
// NFC-normalised name), or null. An empty query matches without a range.
export function matchRange(name, query) {
  const needle = fold(String(query).trim());
  if (needle === "") {
    return { start: 0, end: 0 };
  }
  const { folded, start, end } = foldWithMap(name);
  const at = folded.indexOf(needle); // plain substring: regex characters are literals
  if (at < 0) {
    return null;
  }
  return { start: start[at], end: end[at + needle.length - 1] };
}

// Sources whose file name contains the query, in the original order: [{source, name, range}].
export function filterSources(sources, query) {
  const out = [];
  for (const source of sources) {
    const name = baseName(source.name);
    const range = matchRange(name, query);
    if (range !== null) {
      out.push({ source, name, range });
    }
  }
  return out;
}

// Text parts for a highlighted name: [{text, match}], never empty pieces.
export function highlightParts(name, range) {
  const text = String(name).normalize("NFC");
  if (!range || range.end <= range.start) {
    return [{ text, match: false }];
  }
  const parts = [];
  if (range.start > 0) {
    parts.push({ text: text.slice(0, range.start), match: false });
  }
  parts.push({ text: text.slice(range.start, range.end), match: true });
  if (range.end < text.length) {
    parts.push({ text: text.slice(range.end), match: false });
  }
  return parts;
}

export function isFiltering(query) {
  return String(query).trim() !== "";
}

function defaultStorage() {
  try {
    return window.sessionStorage;
  } catch (err) {
    return null;
  }
}

export function loadQuery(storage = defaultStorage()) {
  try {
    const value = storage ? storage.getItem(STORAGE_KEY) : null;
    return typeof value === "string" ? value.slice(0, 200) : "";
  } catch (err) {
    return "";
  }
}

export function saveQuery(query, storage = defaultStorage()) {
  try {
    if (!storage) {
      return;
    }
    if (isFiltering(query)) {
      storage.setItem(STORAGE_KEY, String(query));
    } else {
      storage.removeItem(STORAGE_KEY);
    }
  } catch (err) {
    // Not persisted; the filter still works.
  }
}
