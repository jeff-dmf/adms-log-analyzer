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

// The top-level conditions of a filter (an {and:[...]} is unwrapped; any other node is one condition).
export function topNodes(filter) {
  if (!filter) {
    return [];
  }
  const keys = Object.keys(filter);
  return keys.length === 1 && keys[0] === "and" && Array.isArray(filter.and) ? filter.and.slice() : [filter];
}

export function wrapNodes(nodes) {
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

// Where the keyboard navigation starts: the selected row, or (UI-28) with nothing selected the first visible record for
// forward and the last for backward. The anchor sits one id outside that edge record, so the edge record itself is
// the first candidate (records are ordered by time, then id). null = no row to start from (an empty grid): the search
// then runs over the whole view. rows: the rendered records, top to bottom.
export function navAnchor(selected, visibleRows, dir) {
  if (selected) {
    return selected;
  }
  const rows = (Array.isArray(visibleRows) ? visibleRows : []).filter((row) => row && Number.isFinite(row.id));
  if (rows.length === 0) {
    return null;
  }
  const back = dir === "back";
  const edge = back ? rows[rows.length - 1] : rows[0];
  return { ...edge, id: edge.id + (back ? 1 : -1) };
}

// What n/N (kind "error") and t/T (kind "template") do: {problem: string key of strings.js} when there is nothing to start
// from, else {request} for api.page. Errors need no selection (they start at the visible edge); a message type needs a
// selected record that has one.
export function navPlan(kind, dir, selected, visibleRows, filter) {
  if (kind !== "error") {
    if (!selected) {
      return { problem: "navNoSelection" };
    }
    if (!(selected.tpl_id > 0)) {
      return { problem: "navNoTemplate" };
    }
  }
  const extra = kind === "error" ? NAV_ERROR_FILTER : { tpl: [selected.tpl_id] };
  return { request: navRequest(filter, extra, navAnchor(selected, visibleRows, dir), dir) };
}

// Latest-request guard for the keyboard navigation: begin() aborts the previous request and
// returns {seq, signal}; invalidate() (a view change) aborts it and makes its answer stale.
export function createLatestRequest() {
  let seq = 0;
  let controller = null;
  const abort = () => {
    if (controller) {
      controller.abort();
      controller = null;
    }
  };
  return {
    begin() {
      abort();
      seq += 1;
      controller = new AbortController();
      return { seq, signal: controller.signal };
    },
    invalidate() {
      abort();
      seq += 1;
    },
    isCurrent: (mine) => mine === seq,
  };
}

// True when the key event comes from a control where typing must not trigger shortcuts.
export function isTypingTarget(target) {
  if (!target) {
    return false;
  }
  const tag = String(target.tagName || "").toUpperCase();
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || Boolean(target.isContentEditable);
}

// True when the target sits inside an open menu (role="menu", see ui-menu.js): the keys there belong to the menu.
export function isInMenu(target) {
  for (let node = target; node; node = node.parentElement || node.parentNode || node.parent) {
    if (typeof node.getAttribute === "function" && node.getAttribute("role") === "menu") {
      return true;
    }
  }
  return false;
}

// True when a global one-key shortcut must stay quiet: typing in a field, keys of an open menu, or an event
// somebody already handled.
export function ignoresShortcut(event) {
  return Boolean(event.defaultPrevented) || isTypingTarget(event.target) || isInMenu(event.target) || isInDialog(event.target);
}

// True when the target sits inside a dialog (UI-23): the keys there belong to the dialog, so the viewer's one-key
// shortcuts (b, c, n, ...) stay quiet while focus is on its title, Close or action buttons.
export function isInDialog(target) {
  for (let node = target; node; node = node.parentElement || node.parentNode || node.parent) {
    if (String(node.tagName || "").toUpperCase() === "DIALOG") {
      return true;
    }
  }
  return false;
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
