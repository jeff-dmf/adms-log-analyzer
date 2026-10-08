// Simple filters -> the JSON filter tree of ARQUITETURA section 10 (compiled to SQL on the server).
import { parseTs } from "./time.js";

export const EMPTY_FILTER_STATE = Object.freeze({
  sourceIds: [],
  sevMax: null,
  levelMax: null,
  from: null,
  to: null,
  text: "",
});

// state: {sourceIds: int[], sevMax: 1..5|null, levelMax: 1..8|null, from: ms|null, to: ms|null,
//         text: string}. Returns the tree, or null when nothing narrows the view.
export function buildFilter(state) {
  const nodes = [];
  if (state.sourceIds && state.sourceIds.length > 0) {
    nodes.push({ source: state.sourceIds.slice() });
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
