// Filter and window helpers of the repeat features (ROADMAP 9.12), pure and small: the filter chips (loaded with the
// first view) use them, so they must not pull the late controller of repeat-model.js along with them.
import { topNodes, wrapNodes } from "./filters.js";
import { formatDuration } from "./events-model.js";
import { S, format } from "./strings.js";

export const DEFAULT_WINDOW_MS = 1000; // the server default (store/repeats.py)
export const MAX_WINDOW_MS = 3600000; // the server maximum
export const WINDOW_CHOICES = [100, 500, 1000, 5000, 30000, 60000];

const isObject = (v) => v !== null && typeof v === "object" && !Array.isArray(v);

// A window in milliseconds the server accepts (an integer from 0 to 3,600,000), or null. 0 is a real window (same
// millisecond only); NaN, null, text, fractions, negatives and larger values are not.
export function normalizeWindow(value) {
  return typeof value === "number" && Number.isInteger(value) && value >= 0 && value <= MAX_WINDOW_MS ? value : null;
}

export function spanText(ms) {
  const n = normalizeWindow(ms);
  if (n === null) {
    return "";
  }
  if (n >= 60000 && n % 60000 === 0) {
    return n % 3600000 === 0 ? format(S.repeatSpanHour, { n: n / 3600000 }) : format(S.repeatSpanMin, { n: n / 60000 });
  }
  return formatDuration(n);
}

// The windows the "Repeat window" menu offers: the presets, plus the one in force when it is not a preset.
export function windowChoices(current) {
  const list = WINDOW_CHOICES.slice();
  const n = normalizeWindow(current);
  if (n !== null && !list.includes(n)) {
    list.push(n);
    list.sort((a, b) => a - b);
  }
  return list;
}

// ---- the filter -----------------------------------------------------------------------------------------------

export const hideNode = (windowMs) => ({ not: { repeat: { window_ms: normalizeWindow(windowMs) ?? DEFAULT_WINDOW_MS } } });

// {ms} (the window) for the simple forms of the leaf (only window_ms, valid or absent), else null: a leaf with fields or tid
// is somebody else's filter and is left alone.
export function repeatSpec(leaf) {
  if (!isObject(leaf) || Object.keys(leaf).length !== 1 || !isObject(leaf.repeat)) {
    return null;
  }
  const spec = leaf.repeat;
  const keys = Object.keys(spec);
  if (keys.some((k) => k !== "window_ms")) {
    return null;
  }
  if (keys.length === 0) {
    return { ms: DEFAULT_WINDOW_MS };
  }
  const windowMs = normalizeWindow(spec.window_ms);
  return windowMs === null ? null : { ms: windowMs };
}

// {ms} when the node is {"not": {"repeat": {window_ms?}}}.
export function hideSpec(node) {
  return isObject(node) && Object.keys(node).length === 1 && "not" in node ? repeatSpec(node.not) : null;
}

// The first top-level hide node of a user filter: {index, ms} or null.
export function findHide(userFilter) {
  const nodes = topNodes(userFilter || null);
  for (let index = 0; index < nodes.length; index += 1) {
    const spec = hideSpec(nodes[index]);
    if (spec !== null) {
      return { index, ms: spec.ms };
    }
  }
  return null;
}

export function withoutHide(userFilter) {
  return wrapNodes(topNodes(userFilter || null).filter((node) => hideSpec(node) === null));
}

// Adds the hide node (the one hide node: any other of its kind goes). Never mutates its input.
export function withHide(userFilter, windowMs) {
  return wrapNodes([...topNodes(withoutHide(userFilter)), hideNode(windowMs)]);
}
