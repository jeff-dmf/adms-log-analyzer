// Simple filters -> the JSON filter tree of ARQUITETURA section 10 (compiled to SQL on the server).
import { parseTs } from "./time.js";

export const EMPTY_FILTER_STATE = Object.freeze({
  sourceIds: [],
  collectionIds: [],
  sevMax: null,
  levelMax: null,
  from: null,
  to: null,
  text: "",
});

// state: {sourceIds: int[], collectionIds?: int[] (collection groups, U21), sevMax: 1..5|null, levelMax: 1..8|null, from: ms|null, to: ms|null,
//         text: string}. Returns the tree, or null when nothing narrows the view.
export function buildFilter(state) {
  const nodes = [];
  if (state.sourceIds && state.sourceIds.length > 0) {
    nodes.push({ source: state.sourceIds.slice() });
  }
  if (state.collectionIds && state.collectionIds.length > 0) {
    nodes.push({ collection: state.collectionIds.slice() });
  }
  if (state.sevMax !== null && state.sevMax !== undefined) {
    nodes.push({ sev: { le: state.sevMax } });
  }
  if (state.levelMax !== null && state.levelMax !== undefined) {
    nodes.push({ level: { le: state.levelMax } });
  }
  const range = {};
  if (state.from !== null && state.from !== undefined) {
    range.ge = state.from;
  }
  if (state.to !== null && state.to !== undefined) {
    range.le = state.to;
  }
  if (Object.keys(range).length > 0) {
    nodes.push({ ts: range });
  }
  const text = (state.text || "").trim();
  if (text !== "") {
    nodes.push({ text: { fts: text } });
  }
  return nodes.length === 0 ? null : { and: nodes };
}

// Reads the raw form values (strings) into a filter state; `error` names the invalid field.
export function stateFromForm(raw) {
  const state = { ...EMPTY_FILTER_STATE, sourceIds: (raw.sourceIds || []).slice() };
  state.text = raw.text || "";
  state.sevMax = raw.sevMax ? Number(raw.sevMax) : null;
  state.levelMax = raw.levelMax ? Number(raw.levelMax) : null;
  const errors = [];
  for (const [key, value] of [["from", raw.from], ["to", raw.to]]) {
    if (value && value.trim() !== "") {
      const ms = parseTs(value);
      if (ms === null) {
        errors.push(key);
      } else {
        state[key] = ms;
      }
    }
  }
  return { state, errors };
}

// Time span (ms) covered by the selected sources (all when none selected), narrowed by the
// time window. Returns {min, max} or null when unknown.
export function timeSpan(sources, selectedIds, from, to) {
  const chosen = sources.filter((s) => selectedIds.length === 0 || selectedIds.includes(s.id));
  let min = null;
  let max = null;
  for (const s of chosen) {
    if (s.ts_min !== null && s.ts_min !== undefined && (min === null || s.ts_min < min)) {
      min = s.ts_min;
    }
    if (s.ts_max !== null && s.ts_max !== undefined && (max === null || s.ts_max > max)) {
      max = s.ts_max;
    }
  }
  if (min === null || max === null) {
    return null;
  }
  if (from !== null && from !== undefined && from > min) {
    min = from;
  }
  if (to !== null && to !== undefined && to < max) {
    max = to;
  }
  return max >= min ? { min, max } : null;
}

// ---- composition of the single current filter -------------------------------------------------

const TS_KEYS = new Set(["ge", "le"]);

function topNodes(filter) {
  if (!filter) {
    return [];
  }
  const keys = Object.keys(filter);
  return keys.length === 1 && keys[0] === "and" && Array.isArray(filter.and) ? filter.and.slice() : [filter];
}

function wrapNodes(nodes) {
  return nodes.length === 0 ? null : { and: nodes };
}

function isTsRangeNode(node) {
  const keys = node && typeof node === "object" ? Object.keys(node) : [];
  if (keys.length !== 1 || keys[0] !== "ts") {
    return false;
  }
  const inner = node.ts;
  return inner !== null && typeof inner === "object" && Object.keys(inner).every((k) => TS_KEYS.has(k));
}

// Adds, replaces or removes (range = null) the top-level ts condition of `filter`.
// range: {from: ms|null, to: ms|null}. Never mutates its input.
export function withTsRange(filter, range) {
  const nodes = topNodes(filter).filter((n) => !isTsRangeNode(n));
  if (range) {
    const ts = {};
    if (range.from !== null && range.from !== undefined) {
      ts.ge = Math.round(range.from);
    }
    if (range.to !== null && range.to !== undefined) {
      ts.le = Math.round(range.to);
    }
    if (Object.keys(ts).length > 0) {
      nodes.push({ ts });
    }
  }
  return wrapNodes(nodes);
}

// Adds a template to the top-level {tpl:[...]} condition (merged into it when present, so the
// view shows entries of any chosen template; ids are not duplicated).
export function withTemplate(filter, tplId) {
  const nodes = topNodes(filter);
  const index = nodes.findIndex((n) => n && typeof n === "object" && Object.keys(n).length === 1 && Array.isArray(n.tpl));
  if (index >= 0) {
    if (!nodes[index].tpl.includes(tplId)) {
      nodes[index] = { tpl: [...nodes[index].tpl, tplId] };
    }
  } else {
    nodes.push({ tpl: [tplId] });
  }
  return wrapNodes(nodes);
}

// AND of any number of filters (nulls skipped); null when nothing narrows the view.
export function andFilters(...filters) {
  const nodes = [];
  for (const f of filters) {
    if (f) {
      nodes.push(...topNodes(f));
    }
  }
  return wrapNodes(nodes);
}

// The one filter sent to the server: sources + search text + simple bar + the filter tree
// (userFilter) + the quick toggles. state is the buildFilter() state.
export function composeFilter(state, userFilter, toggles) {
  const quick = [];
  if (toggles && toggles.hideNoise) {
    quick.push({ sev: { le: 4 } });
  }
  if (toggles && toggles.unclassified) {
    quick.push({ tpl: [0] });
  }
  return andFilters(buildFilter(state), userFilter, wrapNodes(quick));
}

export const NAV_ERROR_FILTER = Object.freeze({ sev: { le: 2 } });

// Request for the keyboard navigation: the next/previous entry that matches `extra` inside the
// current filter, starting after/before the selected row. dir: "fwd" | "back".
export function navRequest(filter, extra, row, dir) {
  const cursor = row ? (row.ts === null || row.ts === undefined ? "null" : String(row.ts)) + ":" + row.id : null;
  return { filter: andFilters(filter, extra), cursor, dir, limit: 1 };
}

// "n"/"N"/"t"/"T" -> {kind: "error"|"template", dir} | null. Ignores modified keys.
export function navKey(event) {
  if (event.ctrlKey || event.metaKey || event.altKey) {
    return null;
  }
  const map = { n: ["error", "fwd"], N: ["error", "back"], t: ["template", "fwd"], T: ["template", "back"] };
  const hit = map[event.key];
  return hit ? { kind: hit[0], dir: hit[1] } : null;
}

// "b" toggles the bookmark of the selected record, "B" opens the bookmarks panel, "c" starts or
// finishes comparing two records. Returns "toggle" | "panel" | "compare" | null. Ignores modified keys.
export function bookmarkKey(event) {
  if (event.ctrlKey || event.metaKey || event.altKey) {
    return null;
  }
  const map = { b: "toggle", B: "panel", c: "compare" };
  return Object.prototype.hasOwnProperty.call(map, event.key) ? map[event.key] : null;
}

// True when the key event comes from a control where typing must not trigger shortcuts.
export function isTypingTarget(target) {
  if (!target) {
    return false;
  }
  const tag = String(target.tagName || "").toUpperCase();
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || Boolean(target.isContentEditable);
}

// ---- template leaf for the filter tree ({tpl: [id | code, ...]}) -----------------------------

export const TPL_LEAF = {
  defaults: () => ({ ids: [] }),
  validate: (d) => {
    if (!Array.isArray(d.ids) || d.ids.length === 0) {
      return "emptyList";
    }
    if (d.ids.length > 1000) {
      return "listTooLong";
    }
    const ok = (v) => (Number.isSafeInteger(v) && v >= 0) || (typeof v === "string" && /^[A-Za-z0-9_.-]{1,64}$/.test(v));
    return d.ids.every(ok) ? null : "badValue";
  },
  toJson: (d) => ({ tpl: d.ids.slice() }),
  fromJson: (j) => {
    const arg = j.tpl;
    const ids = Array.isArray(arg) ? arg : [arg];
    const ok = (v) => (Number.isSafeInteger(v) && v >= 0) || (typeof v === "string" && /^[A-Za-z0-9_.-]{1,64}$/.test(v));
    return ids.length > 0 && ids.every(ok) ? { ids: ids.slice() } : null;
  },
};

// "12, ABC_1" -> [12, "ABC_1"]; invalid parts become -1 (fails validation).
export function parseTplList(text) {
  return String(text)
    .split(",")
    .map((p) => p.trim())
    .filter((p) => p !== "")
    .map((p) => (/^\d{1,15}$/.test(p) ? Number(p) : /^[A-Za-z0-9_.-]{1,64}$/.test(p) ? p : -1));
}
