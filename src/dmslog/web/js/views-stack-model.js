// Chained views (ROADMAP 9.5): a stack of views where each view is its parent's filter AND its own.
// Pure model, no DOM. A view's "state" is the live filter state of the toolbar (search text, simple
// filter bar, toggles and the filter tree); the effective filter sent to the server is the AND of
// the own filter of every view from the root down to the active one. Nothing here reaches the
// server: the composition is an ordinary {"and": [...]} filter.
import { andFilters } from "./filters.js";
import { MAX_DEPTH as MAX_TREE_DEPTH, MAX_LIST, MAX_NODES, MAX_TEXT } from "./filter-tree-model.js";
import { S, format } from "./strings.js";

export const MAX_DEPTH = 10; // views in the stack, root included
export const MAX_NAME = 40;
const MAX_VALUE_CHARS = 24;
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

function clip(value, max = MAX_VALUE_CHARS) {
  const text = String(value);
  return text.length > max ? text.slice(0, max - 1) + "…" : text;
}

function list(arg) {
  return (Array.isArray(arg) ? arg : [arg]).map((v) => clip(v)).join(", ");
}

function isObject(v) {
  return v !== null && typeof v === "object" && !Array.isArray(v);
}

// Short automatic name of one filter node ("tid = 12", "key gid = ..."). Only values the user chose
// (or the filter holds) appear; null when the node has no short form.
export function describeNode(node) {
  if (!isObject(node)) {
    return null;
  }
  const keys = Object.keys(node);
  if (keys.length !== 1) {
    return null;
  }
  const kind = keys[0];
  const arg = node[kind];
  switch (kind) {
    case "tid":
    case "pid":
    case "event":
    case "source":
    case "collection":
      return kind + (Array.isArray(arg) && arg.length > 1 ? " in " : " = ") + list(arg);
    case "tpl":
      return "template " + (Array.isArray(arg) && arg.length > 1 ? "in " : "= ") + list(arg);
    case "sev":
    case "level": {
      if (isObject(arg) && Object.keys(arg).length === 1) {
        const [op] = Object.keys(arg);
        const sign = { le: "<=", ge: ">=", eq: "=", ne: "!=", lt: "<", gt: ">" }[op];
        return sign ? kind + " " + sign + " " + clip(arg[op]) : kind;
      }
      return kind + " " + (isObject(arg) ? "range" : "= " + list(arg));
    }
    case "ts":
      return S.vsNameTimeRange;
    case "text":
      return isObject(arg) && typeof arg.fts === "string" ? S.vsNameText + " " + JSON.stringify(clip(arg.fts)) : S.vsNameText;
    case "key":
      if (isObject(arg) && typeof arg.type === "string") {
        if ("eq" in arg) {
          return "key " + clip(arg.type) + " = " + clip(arg.eq);
        }
        return "key " + clip(arg.type) + (Array.isArray(arg.in) ? " in " + list(arg.in) : "");
      }
      return "key";
    case "field":
      return isObject(arg) && typeof arg.name === "string"
        ? clip(arg.name) + " = " + (arg.eq === null || arg.eq === undefined ? "null" : clip(arg.eq))
        : "field";
    case "not": {
      const inner = describeNode(arg);
      return inner ? "not " + inner : "not …";
    }
    default:
      return kind;
  }
}

// Name from the LAST top-level node of a filter, or null when the filter is empty.
export function describeFilter(filter) {
  if (!isObject(filter)) {
    return null;
  }
  const keys = Object.keys(filter);
  let nodes = [filter];
  if (keys.length === 1 && keys[0] === "and" && Array.isArray(filter.and)) {
    nodes = filter.and;
  }
  if (nodes.length === 0) {
    return null;
  }
  return describeNode(nodes[nodes.length - 1]) || S.vsNameFilter;
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
export function createViewsStack({ ownFilter, getLive, maxDepth = MAX_DEPTH }) {
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
      return describeFilter(filterOf(i)) || (i === 0 ? S.vsRootName : format(S.vsViewName, { n: i + 1 }));
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

// Alt+Left / Alt+Right: back / forward in the stack of views. Returns "back" | "forward" | null.
export function viewsKey(event) {
  if (!event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) {
    return null;
  }
  if (event.key === "ArrowLeft") {
    return "back";
  }
  return event.key === "ArrowRight" ? "forward" : null;
}
