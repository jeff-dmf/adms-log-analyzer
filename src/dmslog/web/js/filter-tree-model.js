// Pure model of the filter tree editor (no DOM): build, edit, validate and convert from/to the
// JSON filter of ARQUITETURA section 10, which store/filters.py compiles. Trees are immutable:
// every edit returns a new root.
//
// Editor tree:  group {id, kind:"group", op:"and"|"or"|"not", children:[node]}
//               leaf  {id, kind:"leaf", type, data}
// A "not" group negates the AND of its children (one child -> {"not": child}).
// JSON the editor has no form for (tid, key, payload, ...) is kept as a "raw" leaf, so any
// valid filter round-trips unchanged. New leaf types (template, event) plug in through
// registerLeafType().

export const MAX_DEPTH = 16; // same limits as store/filters.py
export const MAX_NODES = 200;
export const MAX_LIST = 1000;
export const MAX_TEXT = 512;
export const MAX_NAME = 80; // saved filter names (store/views.py)
export const MAX_VIEWS = 100;
export const HISTORY_LIMIT = 50;
const HARD_NODE_CAP = MAX_NODES * 2; // edits stop here even before validation complains

export const GROUP_OPS = ["and", "or", "not"];
export const ORDER_OPS = ["le", "ge", "eq"];
const SEV_RANGE = [1, 5];
const LEVEL_RANGE = [1, 8];

let counter = 0;
function newId() {
  counter += 1;
  return "n" + counter;
}

const isInt = (v) => Number.isSafeInteger(v);
const isObject = (v) => v !== null && typeof v === "object" && !Array.isArray(v);

// ---- leaf types ------------------------------------------------------------------------------
// def: {defaults(), validate(data) -> error code | null, toJson(data), fromJson(json) -> data | null}

function orderedLeaf(key, [lo, hi]) {
  return {
    defaults: () => ({ op: "le", value: null }),
    validate: (d) => (!ORDER_OPS.includes(d.op) || !isInt(d.value) || d.value < lo || d.value > hi ? "badValue" : null),
    toJson: (d) => ({ [key]: { [d.op]: d.value } }),
    fromJson: (j) => {
      const arg = j[key];
      if (!isObject(arg)) {
        return null;
      }
      const ops = Object.keys(arg);
      if (ops.length !== 1 || !ORDER_OPS.includes(ops[0]) || !isInt(arg[ops[0]])) {
        return null;
      }
      return { op: ops[0], value: arg[ops[0]] };
    },
  };
}

export const LEAF_TYPES = {
  source: {
    defaults: () => ({ ids: [] }),
    validate: (d) => {
      if (!Array.isArray(d.ids) || d.ids.length === 0) {
        return "emptyList";
      }
      if (d.ids.length > MAX_LIST) {
        return "listTooLong";
      }
      return d.ids.every((v) => isInt(v) && v >= 0) ? null : "badValue";
    },
    toJson: (d) => ({ source: d.ids.slice() }),
    fromJson: (j) => {
      const arg = j.source;
      const ids = Array.isArray(arg) ? arg : [arg];
      return ids.length > 0 && ids.every((v) => isInt(v) && v >= 0) ? { ids: ids.slice() } : null;
    },
  },
  sev: orderedLeaf("sev", SEV_RANGE),
  level: orderedLeaf("level", LEVEL_RANGE),
  ts: {
    defaults: () => ({ from: null, to: null }),
    validate: (d) => {
      const has = (v) => v !== null && v !== undefined;
      if (d.invalid) {
        return "badTime"; // set by the editor when a time text cannot be parsed
      }
      if (!has(d.from) && !has(d.to)) {
        return "emptyRange";
      }
      if ((has(d.from) && !isInt(d.from)) || (has(d.to) && !isInt(d.to))) {
        return "badValue";
      }
      return has(d.from) && has(d.to) && d.from > d.to ? "badRange" : null;
    },
    toJson: (d) => {
      const range = {};
      if (d.from !== null && d.from !== undefined) {
        range.ge = d.from;
      }
      if (d.to !== null && d.to !== undefined) {
        range.le = d.to;
      }
      return { ts: range };
    },
    fromJson: (j) => {
      const arg = j.ts;
      if (!isObject(arg)) {
        return null;
      }
      const keys = Object.keys(arg);
      if (keys.length === 0 || !keys.every((k) => (k === "ge" || k === "le") && isInt(arg[k]))) {
        return null;
      }
      return { from: "ge" in arg ? arg.ge : null, to: "le" in arg ? arg.le : null };
    },
  },
  text: {
    defaults: () => ({ fts: "" }),
    validate: (d) => {
      if (typeof d.fts !== "string" || d.fts.trim() === "" || !/[\p{L}\p{N}]/u.test(d.fts)) {
        return "emptyText";
      }
      return d.fts.length > MAX_TEXT ? "textTooLong" : null;
    },
    toJson: (d) => ({ text: { fts: d.fts.trim() } }),
    fromJson: (j) => {
      const arg = j.text;
      return isObject(arg) && Object.keys(arg).length === 1 && typeof arg.fts === "string" ? { fts: arg.fts } : null;
    },
  },
  // Entries that hold a payload: {"payload": {"roles": [...], "fmt": [...], "orphan": bool}}.
  // orphan true = payload without an operation, false = with one; empty lists = no restriction.
  payload: {
    defaults: () => ({ roles: [], fmt: [], orphan: null }),
    validate: (d) => {
      const okList = (v) => Array.isArray(v) && v.every((x) => typeof x === "string" && /^[A-Za-z0-9_.-]{1,64}$/.test(x));
      if (!okList(d.roles) || !okList(d.fmt) || (d.orphan !== null && typeof d.orphan !== "boolean")) {
        return "badValue";
      }
      return d.roles.length > MAX_LIST || d.fmt.length > MAX_LIST ? "listTooLong" : null;
    },
    toJson: (d) => {
      const arg = {};
      if (d.roles.length > 0) {
        arg.roles = d.roles.slice();
      }
      if (d.fmt.length > 0) {
        arg.fmt = d.fmt.slice();
      }
      if (d.orphan !== null) {
        arg.orphan = d.orphan;
      }
      return { payload: arg };
    },
    fromJson: (j) => {
      const arg = j.payload;
      if (!isObject(arg) || !Object.keys(arg).every((k) => k === "roles" || k === "fmt" || k === "orphan")) {
        return null;
      }
      const list = (v) => (v === undefined ? [] : Array.isArray(v) ? v : null);
      const roles = list(arg.roles);
      const fmt = list(arg.fmt);
      const orphan = arg.orphan === undefined ? null : arg.orphan;
      if (roles === null || fmt === null || (orphan !== null && typeof orphan !== "boolean")) {
        return null;
      }
      if (![...roles, ...fmt].every((x) => typeof x === "string")) {
        return null;
      }
      return { roles: roles.slice(), fmt: fmt.slice(), orphan };
    },
  },
  // JSON without a form in the editor: kept verbatim (validated by the server on use).
  raw: {
    defaults: () => ({ json: {} }),
    validate: (d) => (isObject(d.json) && Object.keys(d.json).length === 1 ? null : "badValue"),
    toJson: (d) => structuredClone(d.json),
    fromJson: () => null,
  },
};

// Extension point for the template/event leaves of the rest of M2: registerLeafType("tpl", def).
// fromJson of a registered type is tried before falling back to "raw".
export function registerLeafType(type, def) {
  if (type === "raw" || Object.prototype.hasOwnProperty.call(LEAF_TYPES, type)) {
    throw new Error("leaf_type_exists");
  }
  LEAF_TYPES[type] = def;
}

export function leafTypeNames() {
  return Object.keys(LEAF_TYPES).filter((t) => t !== "raw");
}

// ---- construction ----------------------------------------------------------------------------

export function makeGroup(op = "and", children = []) {
  return { id: newId(), kind: "group", op, children };
}

export function makeLeaf(type, data) {
  const def = LEAF_TYPES[type];
  return { id: newId(), kind: "leaf", type, data: { ...def.defaults(), ...(data || {}) } };
}

export function emptyTree() {
  return makeGroup("and");
}

// ---- JSON -> tree ----------------------------------------------------------------------------

function nodeFromJson(json) {
  if (isObject(json) && Object.keys(json).length === 1) {
    const [op] = Object.keys(json);
    if (op === "and" || op === "or") {
      return Array.isArray(json[op]) ? makeGroup(op, json[op].map(nodeFromJson)) : rawLeaf(json);
    }
    if (op === "not") {
      const inner = nodeFromJson(json.not);
      return inner.kind === "group" && inner.op === "and" ? makeGroup("not", inner.children) : makeGroup("not", [inner]);
    }
    const def = LEAF_TYPES[op];
    if (def && op !== "raw") {
      const data = def.fromJson(json);
      if (data !== null) {
        return { id: newId(), kind: "leaf", type: op, data };
      }
    }
  }
  return rawLeaf(json);
}

function rawLeaf(json) {
  return { id: newId(), kind: "leaf", type: "raw", data: { json: structuredClone(json) } };
}

// A filter (or null) -> editor root (always an AND group).
export function fromJson(json) {
  if (json === null || json === undefined || (isObject(json) && Object.keys(json).length === 0)) {
    return emptyTree();
  }
  const node = nodeFromJson(json);
  return node.kind === "group" && node.op === "and" ? node : makeGroup("and", [node]);
}

// ---- tree -> JSON ----------------------------------------------------------------------------

// Normalization while serializing: empty groups vanish, single-child and/or collapse, nested
// groups of the same operator flatten, not(not x) -> x. Returns JSON or null (nothing).
function nodeToJson(node) {
  if (node.kind === "leaf") {
    return LEAF_TYPES[node.type].toJson(node.data);
  }
  const parts = [];
  for (const child of node.children) {
    const json = nodeToJson(child);
    if (json === null) {
      continue;
    }
    const key = Object.keys(json)[0];
    if (node.op !== "not" && key === node.op && Array.isArray(json[key])) {
      parts.push(...json[key]);
    } else {
      parts.push(json);
    }
  }
  if (parts.length === 0) {
    return null;
  }
  if (node.op === "not") {
    const inner = parts.length === 1 ? parts[0] : { and: parts };
    return "not" in inner && Object.keys(inner).length === 1 ? inner.not : { not: inner };
  }
  return parts.length === 1 ? parts[0] : { [node.op]: parts };
}

export function measureJson(json) {
  let nodes = 0;
  let depth = 0;
  const walk = (j, d) => {
    nodes += 1;
    depth = Math.max(depth, d);
    if (isObject(j)) {
      const [op] = Object.keys(j);
      if (op === "and" || op === "or") {
        j[op].forEach((c) => walk(c, d + 1));
      } else if (op === "not") {
        walk(j.not, d + 1);
      }
    }
  };
  if (json !== null) {
    walk(json, 1);
  }
  return { nodes, depth };
}

// Validation + serialization in one call. {filter, errors:[{id, code}], limit, nodes, depth}.
// `filter` is null when empty or when there is any error. `limit` is "depth", "nodes" or null.
export function buildFilter(root) {
  const errors = [];
  const visit = (node) => {
    if (node.kind === "leaf") {
      const code = LEAF_TYPES[node.type].validate(node.data);
      if (code) {
        errors.push({ id: node.id, code });
      }
    } else {
      node.children.forEach(visit);
    }
  };
  visit(root);
  if (errors.length > 0) {
    return { filter: null, errors, limit: null, nodes: 0, depth: 0 };
  }
  const json = nodeToJson(root);
  const { nodes, depth } = measureJson(json);
  let limit = null;
  if (depth > MAX_DEPTH) {
    limit = "depth";
  } else if (nodes > MAX_NODES) {
    limit = "nodes";
  }
  return { filter: limit ? null : json, errors, limit, nodes, depth };
}

// ---- editing (immutable) ---------------------------------------------------------------------

export function findNode(root, id) {
  if (root.id === id) {
    return root;
  }
  if (root.kind === "group") {
    for (const child of root.children) {
      const found = findNode(child, id);
      if (found) {
        return found;
      }
    }
  }
  return null;
}

export function findParent(root, id) {
  if (root.kind !== "group") {
    return null;
  }
  for (const child of root.children) {
    if (child.id === id) {
      return root;
    }
    const found = findParent(child, id);
    if (found) {
      return found;
    }
  }
  return null;
}

export function countNodes(node) {
  return node.kind === "leaf" ? 1 : 1 + node.children.reduce((n, c) => n + countNodes(c), 0);
}

export function treeDepth(node) {
  return node.kind === "leaf" ? 1 : 1 + node.children.reduce((d, c) => Math.max(d, treeDepth(c)), 0);
}

// Rebuilds the path to `id`, applying fn(node, parentChildren) -> replacement list for that node.
function replaceIn(node, id, fn) {
  if (node.kind !== "group") {
    return node;
  }
  const children = [];
  let changed = false;
  for (const child of node.children) {
    if (child.id === id) {
      children.push(...fn(child));
      changed = true;
    } else {
      const next = replaceIn(child, id, fn);
      changed = changed || next !== child;
      children.push(next);
    }
  }
  return changed ? { ...node, children } : node;
}

function mapNode(node, id, fn) {
  if (node.id === id) {
    return fn(node);
  }
  if (node.kind !== "group") {
    return node;
  }
  let changed = false;
  const children = node.children.map((child) => {
    const next = mapNode(child, id, fn);
    changed = changed || next !== child;
    return next;
  });
  return changed ? { ...node, children } : node;
}

export function cloneWithNewIds(node) {
  return node.kind === "leaf"
    ? { id: newId(), kind: "leaf", type: node.type, data: structuredClone(node.data) }
    : { id: newId(), kind: "group", op: node.op, children: node.children.map(cloneWithNewIds) };
}

export function canGrow(root, extra = 1) {
  return countNodes(root) + extra <= HARD_NODE_CAP;
}

export function addChild(root, groupId, node) {
  if (!canGrow(root, countNodes(node))) {
    return root;
  }
  return mapNode(root, groupId, (g) => (g.kind === "group" ? { ...g, children: [...g.children, node] } : g));
}

export function removeNode(root, id) {
  return id === root.id ? root : replaceIn(root, id, () => []);
}

export function duplicateNode(root, id) {
  const original = findNode(root, id);
  if (!original || id === root.id || !canGrow(root, countNodes(original))) {
    return root;
  }
  return replaceIn(root, id, (n) => [n, cloneWithNewIds(n)]);
}

// Wraps a node in a new group of `op` (NOT / AND / OR). The root itself cannot be wrapped.
export function wrapNode(root, id, op) {
  if (id === root.id || !canGrow(root) || !GROUP_OPS.includes(op)) {
    return root;
  }
  return replaceIn(root, id, (n) => [makeGroup(op, [n])]);
}

// Dissolves a group, moving its children into the parent in its place.
export function ungroup(root, id) {
  const target = findNode(root, id);
  if (!target || target.kind !== "group" || id === root.id) {
    return root;
  }
  return replaceIn(root, id, (g) => g.children);
}

export function setGroupOp(root, id, op) {
  return GROUP_OPS.includes(op) ? mapNode(root, id, (g) => (g.kind === "group" ? { ...g, op } : g)) : root;
}

export function updateLeaf(root, id, patch) {
  return mapNode(root, id, (n) => (n.kind === "leaf" ? { ...n, data: { ...n.data, ...patch } } : n));
}

export function changeLeafType(root, id, type) {
  if (!LEAF_TYPES[type] || type === "raw") {
    return root;
  }
  return mapNode(root, id, (n) => (n.kind === "leaf" ? { ...n, type, data: LEAF_TYPES[type].defaults() } : n));
}

// Moves a node by `delta` positions among its siblings.
export function moveNode(root, id, delta) {
  const parent = findParent(root, id);
  if (!parent) {
    return root;
  }
  const from = parent.children.findIndex((c) => c.id === id);
  const to = from + delta;
  if (to < 0 || to >= parent.children.length) {
    return root;
  }
  return mapNode(root, parent.id, (g) => {
    const children = g.children.slice();
    const [item] = children.splice(from, 1);
    children.splice(to, 0, item);
    return { ...g, children };
  });
}

// ---- undo / redo -----------------------------------------------------------------------------

export class History {
  constructor(initial, limit = HISTORY_LIMIT) {
    this.limit = limit;
    this.present = initial;
    this.past = [];
    this.future = [];
  }

  // Records a new state; identical states (same object) are ignored.
  push(next) {
    if (next === this.present) {
      return this.present;
    }
    this.past.push(this.present);
    if (this.past.length > this.limit) {
      this.past.shift();
    }
    this.present = next;
    this.future = [];
    return this.present;
  }

  // Replaces the present without a history entry (typing in a field).
  replace(next) {
    this.present = next;
    return this.present;
  }

  get canUndo() {
    return this.past.length > 0;
  }

  get canRedo() {
    return this.future.length > 0;
  }

  undo() {
    if (this.canUndo) {
      this.future.push(this.present);
      this.present = this.past.pop();
    }
    return this.present;
  }

  redo() {
    if (this.canRedo) {
      this.past.push(this.present);
      this.present = this.future.pop();
    }
    return this.present;
  }
}

// ---- saved filter names (same rules as store/views.py) ---------------------------------------

export function sanitizeName(raw) {
  const text = String(raw).normalize("NFC").replace(/\s/gu, " ").replace(/\p{C}/gu, "");
  return text.split(" ").filter((p) => p !== "").join(" ");
}

// Returns {name, error} where error is null, "empty", "tooLong", "duplicate" or "tooMany".
export function checkViewName(raw, existingNames, maxViews = MAX_VIEWS) {
  const name = sanitizeName(raw);
  let error = null;
  if (name === "") {
    error = "empty";
  } else if ([...name].length > MAX_NAME) {
    error = "tooLong";
  } else if (existingNames.some((n) => n.toLowerCase() === name.toLowerCase())) {
    error = "duplicate";
  } else if (existingNames.length >= maxViews) {
    error = "tooMany";
  }
  return { name, error };
}
