// Highlight rules (ROADMAP 9.1), pure part: palette, conditions of the quick form, labels, the ]/[ keys
// and the store that marks the grid rows. No DOM. Every condition is a filter tree (ARQUITETURA 10)
// evaluated by the server: POST /api/entries/highlight marks the ids the grid shows (one batch of at
// most MARK_BATCH ids, every enabled rule at once) and POST /api/entries/highlight/next finds the next
// highlighted record by keyset.
import { parseTplList } from "./filters.js";
import { S, format } from "./strings.js";

export const PALETTE = Object.freeze(["yellow", "orange", "red", "pink", "purple", "blue", "cyan", "green"]);
export const DEFAULT_COLOR = "yellow";
export const MAX_RULES = 20; // store/highlights.py MAX_RULES
export const MARK_BATCH = 500; // store/highlights.py MAX_MARK_IDS
export const MAX_VALUE = 512;
export const MAX_REGEX = 256; // store/filters.py MAX_REGEX_CHARS
export const QUICK_KINDS = Object.freeze(["text", "substr", "regex", "key", "tpl", "sev", "field"]);
const NAME_RE = /^[A-Za-z_][A-Za-z0-9_]{0,63}$/;
const KEY_TYPE_RE = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/;
const MAX_NAME = 80;

export function colorLabel(color) {
  return (S.hlColorNames && S.hlColorNames[color]) || String(color);
}

// First palette color no rule uses yet (yellow when all are taken).
export function nextColor(rules) {
  const used = new Set((rules || []).map((r) => r.color));
  return PALETTE.find((c) => !used.has(c)) || DEFAULT_COLOR;
}

// Quick form -> {cond} or {error: key of S.hlBad}. kind is one of QUICK_KINDS; `extra` is the key type
// (key) or the field name (field).
export function buildCondition(kind, value, extra) {
  const text = String(value === null || value === undefined ? "" : value);
  const trimmed = text.trim();
  const other = String(extra === null || extra === undefined ? "" : extra).trim();
  if (kind === "sev") {
    const n = Number(trimmed);
    return Number.isInteger(n) && n >= 1 && n <= 5 ? { cond: { sev: { le: n } } } : { error: "badSev" };
  }
  if (trimmed === "") {
    return { error: "empty" };
  }
  if (text.length > (kind === "regex" ? MAX_REGEX : MAX_VALUE)) {
    return { error: "tooLong" };
  }
  switch (kind) {
    case "text":
      return { cond: { text: { fts: trimmed } } };
    case "substr":
      return { cond: { text: { substr: text } } }; // exact text: spaces count
    case "regex":
      return { cond: { text: { regex: text } } };
    case "key":
      return KEY_TYPE_RE.test(other) ? { cond: { key: { type: other, eq: trimmed } } } : { error: "badName" };
    case "field":
      return NAME_RE.test(other) ? { cond: { field: { name: other, eq: trimmed } } } : { error: "badName" };
    case "tpl": {
      const ids = parseTplList(trimmed);
      return ids.length > 0 && !ids.includes(-1) ? { cond: { tpl: ids } } : { error: "badTpl" };
    }
    default:
      return { error: "empty" };
  }
}

function compact(value, limit) {
  const text = typeof value === "string" ? value : JSON.stringify(value);
  return text.length > limit ? text.slice(0, limit - 1) + "…" : text;
}

// Short human text of a condition (what the panel lists). Unknown shapes show their JSON, shortened.
export function describeCondition(cond) {
  if (!cond || typeof cond !== "object") {
    return "";
  }
  const keys = Object.keys(cond);
  if (keys.length === 1) {
    const op = keys[0];
    const arg = cond[op];
    if (op === "text" && arg && typeof arg === "object") {
      if (typeof arg.fts === "string") {
        return format(S.hlDescText, { value: compact(arg.fts, 120) });
      }
      if (typeof arg.substr === "string") {
        return format(S.hlDescSubstr, { value: compact(arg.substr, 120) });
      }
      if (typeof arg.regex === "string") {
        return format(S.hlDescRegex, { value: compact(arg.regex, 120) });
      }
    }
    if (op === "key" && arg && typeof arg.type === "string") {
      if (typeof arg.eq === "string") {
        return format(S.hlDescKey, { type: arg.type, value: compact(arg.eq, 120) });
      }
      if (arg.in === undefined) {
        return format(S.hlDescKeyAny, { type: arg.type });
      }
    }
    if (op === "tpl") {
      return format(S.hlDescTpl, { value: compact((Array.isArray(arg) ? arg : [arg]).join(", "), 120) });
    }
    if (op === "sev" && arg && typeof arg === "object" && Number.isInteger(arg.le)) {
      return format(S.hlDescSev, { value: S.severityNames[arg.le] || String(arg.le) });
    }
    if (op === "field" && arg && typeof arg.name === "string") {
      return format(S.hlDescField, { name: arg.name, value: compact(arg.eq === null ? "null" : String(arg.eq), 120) });
    }
  }
  return format(S.hlDescFilter, { json: compact(cond, 160) });
}

// Default rule name from the quick form (the user can type another).
export function defaultName(kind, value, extra) {
  const v = String(value || "").trim();
  const e = String(extra || "").trim();
  let name;
  if (kind === "key" || kind === "field") {
    name = e + " = " + v;
  } else if (kind === "sev") {
    name = format(S.hlDescSev, { value: S.severityNames[Number(v)] || v });
  } else if (kind === "regex") {
    name = "/" + v + "/";
  } else {
    name = v;
  }
  return compact(name, MAX_NAME);
}

// "]" next highlighted record, "[" previous (layout-independent `key`; no modifier). n/N, t/T, b/B, c are
// taken (filters.js navKey/bookmarkKey).
export function highlightNavKey(event) {
  if (event.ctrlKey || event.metaKey || event.altKey) {
    return null;
  }
  if (event.key === "]") {
    return "fwd";
  }
  return event.key === "[" ? "back" : null;
}

export function cursorOf(row) {
  if (!row) {
    return null;
  }
  return (row.ts === null || row.ts === undefined ? "null" : String(row.ts)) + ":" + row.id;
}

function isAbort(error) {
  return Boolean(error && error.name === "AbortError");
}

// Rules of the current case and the colors of the rows already looked up. onChange(kind): "rules" (the
// list changed), "marks" (rows got their colors). get(id) -> {color, name, label, ruleId} | null.
export function createHighlightStore({ api, getCase, onChange, schedule = (fn) => setTimeout(fn, 30) }) {
  let caseId = null;
  let rules = [];
  let active = []; // enabled rules in priority order: the index the server answers points here
  let generation = 0;
  let marks = new Map(); // entry id -> index in `active`
  let checked = new Set();
  let queue = new Set();
  let timer = null;
  let controller = null;
  let loading = null;

  function clearMarks() {
    generation += 1;
    marks = new Map();
    checked = new Set();
    queue = new Set();
    timer = null;
    if (controller) {
      controller.abort();
      controller = null;
    }
  }

  function setRules(list) {
    rules = Array.isArray(list) ? list.slice() : [];
    active = rules.filter((r) => r.enabled);
    clearMarks();
    onChange("rules");
  }

  // Follows the case of the page; a new case drops everything and loads its rules (unless the caller is
  // load() itself).
  function sync(autoLoad = true) {
    const current = getCase() || null;
    if (current !== caseId) {
      caseId = current;
      rules = [];
      active = [];
      clearMarks();
      loading = null;
      if (caseId && autoLoad) {
        store.load().catch(() => {});
      } else {
        Promise.resolve().then(() => onChange("rules")); // never re-enter the caller (grid.render)
      }
    }
    return caseId;
  }

  async function flush() {
    timer = null;
    const forCase = caseId;
    if (!forCase || queue.size === 0 || active.length === 0) {
      queue = new Set();
      return;
    }
    const ids = Array.from(queue).slice(0, MARK_BATCH);
    for (const id of ids) {
      queue.delete(id);
    }
    const token = generation;
    const conds = active.map((r) => r.cond);
    controller = new AbortController();
    try {
      const data = await api.highlights.mark(forCase, conds, ids, controller.signal);
      if (token !== generation) {
        return;
      }
      for (const id of ids) {
        checked.add(id);
      }
      for (const [key, index] of Object.entries((data && data.marks) || {})) {
        if (Number.isInteger(index) && index >= 0 && index < active.length) {
          marks.set(Number(key), index);
        }
      }
      onChange("marks");
    } catch (error) {
      if (token === generation && !isAbort(error)) {
        for (const id of ids) {
          checked.add(id); // no retry on every scroll; a rule change looks them up again
        }
      }
    }
    if (token === generation && queue.size > 0 && timer === null) {
      timer = schedule(flush);
    }
  }

  async function write(promise) {
    const forCase = caseId;
    const result = await promise;
    if (forCase === caseId) {
      await store.load();
    }
    return result;
  }

  const store = {
    rules: () => rules.slice(),
    active: () => active.slice(),
    caseId: () => caseId,
    get(entryId) {
      const index = marks.get(entryId);
      const rule = index === undefined ? null : active[index];
      return rule ? { color: rule.color, name: rule.name, ruleId: rule.id, label: format(S.hlRowLabel, { name: rule.name }) } : null;
    },
    known: (entryId) => checked.has(entryId),
    ensure(ids) {
      if (!sync() || active.length === 0) {
        return;
      }
      for (const id of ids) {
        if (Number.isSafeInteger(id) && !checked.has(id)) {
          queue.add(id);
        }
      }
      if (queue.size > 0 && timer === null) {
        timer = schedule(flush);
      }
    },
    // Records were reindexed (ids changed): look the visible rows up again.
    invalidateMarks() {
      clearMarks();
      onChange("marks");
    },
    async load() {
      const forCase = sync(false);
      if (!forCase) {
        return [];
      }
      const pending = api.highlights.list(forCase).then((data) => {
        if (forCase === caseId && loading === pending) {
          setRules((data && data.rules) || []);
        }
        return rules;
      });
      loading = pending;
      return pending;
    },
    create: (rule) => write(api.highlights.create(caseId, rule)),
    update: (id, changes) => write(api.highlights.update(caseId, id, changes)),
    remove: (id) => write(api.highlights.remove(caseId, id)),
    // Moves a rule one place up (delta -1) or down (+1) in the priority order.
    move(id, delta) {
      const ids = rules.map((r) => r.id);
      const from = ids.indexOf(id);
      const to = from + delta;
      if (from < 0 || to < 0 || to >= ids.length) {
        return Promise.resolve(null);
      }
      ids.splice(to, 0, ids.splice(from, 1)[0]);
      return write(api.highlights.order(caseId, ids));
    },
    highlightKey: (type, value, color) => write(api.highlights.key(caseId, type, value, color || nextColor(rules))),
    // Next/previous highlighted record of the view after `row`: {row, rule} (rule = the rule object).
    async next(filter, row, dir, signal) {
      const forCase = sync();
      if (!forCase || active.length === 0) {
        return { row: null, rule: null, none: "rules" };
      }
      const snapshot = active.slice();
      const data = await api.highlights.next(forCase, filter, snapshot.map((r) => r.cond), cursorOf(row), dir, signal);
      const found = data && data.row ? data.row : null;
      const index = data ? data.rule : null;
      const rule = found && Number.isInteger(index) && index >= 0 ? snapshot[index] || null : null;
      if (rule && forCase === caseId && active.includes(rule)) {
        marks.set(found.id, active.indexOf(rule)); // the row is colored as soon as the grid shows it
        checked.add(found.id);
      }
      return { row: found, rule, none: found ? null : "match" };
    },
    reset() {
      caseId = null;
      sync();
    },
  };

  return store;
}
