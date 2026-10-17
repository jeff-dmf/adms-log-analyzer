// Chained views (ROADMAP 9.5): a stack of views where each view is its parent's filter AND its own.
// Pure model, no DOM. A view's "state" is the live filter state of the toolbar (search text, simple
// filter bar, toggles and the filter tree); the effective filter sent to the server is the AND of
// the own filter of every view from the root down to the active one. Nothing here reaches the
// server: the composition is an ordinary {"and": [...]} filter.
import { andFilters, topNodes } from "./filters.js";
import { chipLabel } from "./filter-chips-model.js";
import { MAX_DEPTH as MAX_TREE_DEPTH, MAX_LIST, MAX_NODES, MAX_TEXT } from "./filter-tree-model.js";
import { S, format } from "./strings.js";

export const MAX_DEPTH = 10; // views in the stack, root included
export const MAX_NAME = 40;
const STORAGE_PREFIX = "dmslog.viewsStack.";

export function emptyState() {
  return {
    searchText: "",
    form: { sev: "", level: "", from: "", to: "" }, // raw input strings of the filter bar
    formState: { sevMax: null, levelMax: null, from: null, to: null }, // the applied values
    toggles: { hideNoise: false, unclassified: false },
    userFilter: null,
  };
}

function isObject(v) {
  return v !== null && typeof v === "object" && !Array.isArray(v);
}

// Lookups for the names of the views when the caller has none (tests): ids and times as plain text.
const PLAIN_LOOKUPS = {
  sourceName: (id) => String(id),
  collectionTag: (group) => String(group),
  typeLabel: (id) => String(id),
  formatTs: (ms) => String(ms),
};

// Name from the LAST top-level node of a filter, or null when the filter is empty. The words are the chips' (the
// shared labels of filter-chips-model.js, with the lookups of ui-contratos.md 4.1); a node the chips cannot name
// is "filter".
export function describeFilter(filter, lookups = PLAIN_LOOKUPS) {
  if (!isObject(filter)) {
    return null;
  }
  const nodes = topNodes(filter).filter((n) => !(isObject(n) && Object.keys(n).length === 0));
  if (nodes.length === 0) {
    return null;
  }
  const label = chipLabel(nodes[nodes.length - 1], lookups, S);
  return label === null ? S.vsNameFilter : label.length > MAX_NAME ? label.slice(0, MAX_NAME - 1) + "…" : label;
}

export function sanitizeName(raw) {
  return String(raw === null || raw === undefined ? "" : raw).replace(/\s+/g, " ").trim().slice(0, MAX_NAME);
}

// JSON value of a leaf argument: plain, shallow, with bounded lists, strings and keys.
function boundedValue(v, depth) {
  if (v === null || typeof v === "boolean") {
    return true;
  }
  if (typeof v === "number") {
    return Number.isFinite(v);
  }
  if (typeof v === "string") {
    return v.length <= MAX_TEXT;
  }
  if (depth >= 4) {
    return false;
  }
  if (Array.isArray(v)) {
    return v.length <= MAX_LIST && v.every((x) => boundedValue(x, depth + 1));
  }
  const keys = isObject(v) ? Object.keys(v) : null;
  return keys !== null && keys.length <= 8 && keys.every((k) => k.length <= 64 && boundedValue(v[k], depth + 1));
}

// True when `filter` is a well-formed filter tree within the limits of the filter editor
// (depth, node count, list and text sizes). Iterative: a hostile nesting cannot overflow the stack.
export function isValidFilter(filter) {
  if (filter === null) {
    return true;
  }
  let nodes = 0;
  const pending = [[filter, 1]];
  while (pending.length > 0) {
    const [node, depth] = pending.pop();
    nodes += 1;
    if (!isObject(node) || Object.keys(node).length !== 1 || depth > MAX_TREE_DEPTH || nodes > MAX_NODES) {
      return false;
    }
    const [op] = Object.keys(node);
    const arg = node[op];
    if (op === "and" || op === "or") {
      if (!Array.isArray(arg) || arg.length > MAX_NODES) {
        return false;
      }
      arg.forEach((child) => pending.push([child, depth + 1]));
    } else if (op === "not") {
      pending.push([arg, depth + 1]);
    } else if (!boundedValue(arg, 0)) {
      return false;
    }
  }
  return true;
}

function sanitizeState(raw) {
  const base = emptyState();
  if (!isObject(raw)) {
    return base;
  }
  const str = (v) => (typeof v === "string" ? v.slice(0, 600) : "");
  const num = (v) => (Number.isFinite(v) ? v : null);
  const form = isObject(raw.form) ? raw.form : {};
  const applied = isObject(raw.formState) ? raw.formState : {};
  const toggles = isObject(raw.toggles) ? raw.toggles : {};
  return {
    searchText: str(raw.searchText),
    form: { sev: str(form.sev), level: str(form.level), from: str(form.from), to: str(form.to) },
    formState: { sevMax: num(applied.sevMax), levelMax: num(applied.levelMax), from: num(applied.from), to: num(applied.to) },
    toggles: { hideNoise: toggles.hideNoise === true, unclassified: toggles.unclassified === true },
    userFilter: isObject(raw.userFilter) && isValidFilter(raw.userFilter) ? raw.userFilter : null,
  };
}

// options: ownFilter(state) -> filter|null (the view's own conditions, without the sources),
//          getLive() -> the live state of the active view, maxDepth.
// lookups: {sourceName, collectionTag, typeLabel, formatTs} (getters are fine: read at each name).
export function createViewsStack({ ownFilter, getLive, maxDepth = MAX_DEPTH, lookups = PLAIN_LOOKUPS }) {
  let nodes = [{ state: emptyState(), customName: "" }];
  let index = 0;

  const stateOf = (i) => (i === index ? getLive() : nodes[i].state);
  const filterOf = (i) => ownFilter(stateOf(i));
  const saveActive = () => {
    nodes[index].state = getLive();
  };

  return {
    maxDepth,
    get index() {
      return index;
    },
    get size() {
      return nodes.length;
    },
    get depth() {
      return index; // 0 = root
    },
    get isRoot() {
      return index === 0;
    },
    canBack: () => index > 0,
    canForward: () => index < nodes.length - 1,
    name(i) {
      const node = nodes[i];
      if (node.customName) {
        return node.customName;
      }
      return describeFilter(filterOf(i), lookups) || (i === 0 ? S.vsRootName : format(S.vsViewName, { n: i + 1 }));
    },
    // The ancestors' filter: what every request of the active view is ANDed with.
    parentsFilter() {
      const own = [];
      for (let i = 0; i < index; i += 1) {
        own.push(ownFilter(nodes[i].state));
      }
      return andFilters(...own);
    },
    // {index, name, active, future}[]: `future` marks views reachable only by "forward".
    crumbs() {
      return nodes.map((node, i) => ({ index: i, name: this.name(i), active: i === index, future: i > index }));
    },
    // Own conditions of the active view become the parent; returns {ok, reason, state}.
    narrow() {
      if (index + 1 >= maxDepth) {
        return { ok: false, reason: "full" };
      }
      if (ownFilter(getLive()) === null) {
        return { ok: false, reason: "empty" };
      }
      saveActive();
      nodes = nodes.slice(0, index + 1);
      nodes.push({ state: emptyState(), customName: "" });
      index += 1;
      return { ok: true, state: emptyState() };
    },
    // Makes view i the active one; returns its state (to put back on the toolbar), or null.
    goTo(i) {
      if (!Number.isInteger(i) || i < 0 || i >= nodes.length || i === index) {
        return null;
      }
      saveActive();
      index = i;
      return nodes[i].state;
    },
    rename(i, raw) {
      if (i >= 0 && i < nodes.length) {
        nodes[i].customName = sanitizeName(raw);
      }
    },
    reset() {
      nodes = [{ state: emptyState(), customName: "" }];
      index = 0;
    },
    // JSON-safe snapshot (sessionStorage); null when the stack is just the root.
    serialize() {
      if (nodes.length === 1 && !nodes[0].customName) {
        return null;
      }
      saveActive();
      return { v: 1, index, nodes: nodes.map((n) => ({ name: n.customName, state: n.state })) };
    },
    // Replaces the stack with a saved one; returns the live state to apply, or null when invalid.
    load(raw) {
      if (!isObject(raw) || raw.v !== 1 || !Array.isArray(raw.nodes) || raw.nodes.length === 0 || raw.nodes.length > maxDepth
        || !Number.isInteger(raw.index) || raw.index < 0 || raw.index >= raw.nodes.length) {
        return null;
      }
      nodes = raw.nodes.map((n) => ({ state: sanitizeState(isObject(n) ? n.state : null), customName: sanitizeName(isObject(n) ? n.name : "") }));
      index = raw.index;
      return nodes[index].state;
    },
  };
}

// sessionStorage persistence per case, never throwing (blocked or full storage just skips it).
export function saveStack(storage, caseId, stack) {
  try {
    const data = stack.serialize();
    const key = STORAGE_PREFIX + caseId;
    if (data === null) {
      storage.removeItem(key);
    } else {
      storage.setItem(key, JSON.stringify(data));
    }
  } catch (err) {
    // Not persisted; the views still work.
  }
}

export function loadStack(storage, caseId, stack) {
  try {
    const text = storage.getItem(STORAGE_PREFIX + caseId);
    return text ? stack.load(JSON.parse(text)) : null;
  } catch (err) {
    return null;
  }
}
