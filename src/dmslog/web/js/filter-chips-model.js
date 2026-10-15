// Filter chips (UI-18): the pure model between the live state of a view and the removable chips of the
// filter strip. It works on the live state (views-stack-model.js emptyState()) plus the side selection, not
// on the composed JSON, because the JSON forgets where a condition came from ({sev:{le:4}} can be "Hide noise"
// or "Severity at least Warning"). No DOM. The texts come from `S` (strings.js), passed in.
import { topNodes, wrapNodes } from "./filters.js";
import { format, pluralKey } from "./strings.js";

export const MAX_LABEL = 32; // chip label length; the title keeps the full text
const MAX_VALUES = 3; // values listed in one chip before "+N"

const isObject = (v) => v !== null && typeof v === "object" && !Array.isArray(v);
const isNum = (v) => typeof v === "number" && Number.isFinite(v); // 0 is a measure; null and NaN are not
const asList = (v) => (Array.isArray(v) ? v : [v]);

function clip(text, max = MAX_LABEL) {
  const t = String(text);
  return t.length > max ? t.slice(0, max - 1) + "…" : t;
}

function joinValues(values, S) {
  const shown = values.slice(0, MAX_VALUES).map(String).join(", ");
  return values.length > MAX_VALUES ? shown + format(S.chipMoreValues, { n: values.length - MAX_VALUES }) : shown;
}

// "{from} to {to}" / "from {from}" / "until {to}", or null when neither end is a number.
function timeLabel(from, to, lookups, S) {
  const hasFrom = isNum(from);
  const hasTo = isNum(to);
  if (hasFrom && hasTo) {
    return format(S.chipTimeBoth, { from: lookups.formatTs(from), to: lookups.formatTs(to) });
  }
  if (hasFrom) {
    return format(S.chipTimeFrom, { from: lookups.formatTs(from) });
  }
  return hasTo ? format(S.chipTimeUntil, { to: lookups.formatTs(to) }) : null;
}

const NAME_TABLES = { sev: "severityNames", level: "levelNames" };

function orderedLabel(kind, arg, S) {
  if (isObject(arg) && Object.keys(arg).length === 1 && isNum(arg.le)) {
    const name = (S[NAME_TABLES[kind]] || {})[arg.le] || String(arg.le);
    return format(kind === "sev" ? S.chipSev : S.chipLevel, { name });
  }
  return null;
}

// Short label of one SIMPLE leaf node (one key, a shape this module knows), or null (-> "Filter tree").
// `not` of a simple leaf is simple too. Unknown leaf types are null: they never break the strip.
export function chipLabel(node, lookups, S) {
  if (!isObject(node)) {
    return null;
  }
  const keys = Object.keys(node);
  if (keys.length !== 1) {
    return null;
  }
  const kind = keys[0];
  const arg = node[kind];
  if (kind === "not") {
    const inner = chipLabel(arg, lookups, S);
    return inner === null ? null : format(S.chipNot, { label: inner });
  }
  const model = (S.chipLeaf || {})[kind];
  switch (kind) {
    case "sev":
    case "level":
      return orderedLabel(kind, arg, S);
    case "ts":
      return isObject(arg) && Object.keys(arg).every((k) => k === "ge" || k === "le") ? timeLabel(arg.ge, arg.le, lookups, S) : null;
    case "text": {
      if (!isObject(arg)) {
        return null;
      }
      const [mode] = Object.keys(arg);
      const key = { fts: "chipText", substr: "chipTextSubstr", regex: "chipTextRegex" }[mode];
      return key && typeof arg[mode] === "string" && Object.keys(arg).length === 1 ? format(S[key], { text: arg[mode] }) : null;
    }
    case "sensitive":
      return typeof arg === "boolean" ? S[arg ? "chipSensitiveOn" : "chipSensitiveOff"] : null;
    case "source":
    case "collection":
    case "tpl": {
      const values = asList(arg);
      const name = { source: lookups.sourceName, collection: lookups.collectionTag, tpl: lookups.typeLabel }[kind];
      return model && values.length > 0 ? format(model, { value: joinValues(values.map((v) => name(v)), S) }) : null;
    }
    case "key": {
      if (!isObject(arg) || typeof arg.type !== "string") {
        return null;
      }
      if ("eq" in arg) {
        return format(S.chipKeyEq, { type: arg.type, value: String(arg.eq) });
      }
      if (Array.isArray(arg.in)) {
        return format(S.chipKeyIn, { type: arg.type, value: joinValues(arg.in, S) });
      }
      return format(S.chipKeyAny, { type: arg.type });
    }
    case "field":
      return isObject(arg) && typeof arg.name === "string"
        ? format(S.chipField, { name: arg.name, value: arg.eq === null || arg.eq === undefined ? "null" : String(arg.eq) })
        : null;
    case "payload": {
      if (!isObject(arg) || Object.keys(arg).length === 0) {
        return null;
      }
      const parts = Object.keys(arg).map((k) => k + "=" + (Array.isArray(arg[k]) ? arg[k].join("|") : String(arg[k])));
      return model ? format(model, { value: parts.join(", ") }) : null;
    }
    case "tid":
    case "pid":
    case "event":
    case "stream":
    case "family":
    case "event_status":
    case "event_name":
    case "event_kind":
    case "flags": {
      const values = asList(arg);
      return model && values.length > 0 && values.every((v) => typeof v === "string" || isNum(v))
        ? format(model, { value: joinValues(values, S) })
        : null;
    }
    default:
      return null;
  }
}

// Number of leaf conditions inside a node (and/or/not recurse; anything else is one).
function countConditions(node) {
  if (!isObject(node)) {
    return 1;
  }
  const keys = Object.keys(node);
  if (keys.length === 1 && (keys[0] === "and" || keys[0] === "or") && Array.isArray(node[keys[0]])) {
    return node[keys[0]].reduce((sum, n) => sum + countConditions(n), 0);
  }
  if (keys.length === 1 && keys[0] === "not") {
    return countConditions(node.not);
  }
  return 1;
}

const textOf = (live) => (typeof live.searchText === "string" ? live.searchText.trim() : "");
const idsOf = (list) => (Array.isArray(list) ? list : []);

// {} means "no filter" (store/filters.py): it is not a condition.
const isCondition = (node) => !(isObject(node) && Object.keys(node).length === 0);

function make(id, kind, full, removable) {
  return { id, kind, label: clip(full), title: full, removable };
}

// Chips of the active view, in the order of the contract: sources, collections, text, sev, level, time,
// noise, unclassified, node:0..n (removable simple conditions of the filter tree), tree (the rest, no ×).
export function chipsFromState(live, selection, lookups, S) {
  const chips = [];
  const sources = idsOf(selection && selection.sourceIds);
  const groups = idsOf(selection && selection.collectionIds);
  if (sources.length === 1) {
    chips.push(make("sources", "sources", format(S.chipSourceOne, { name: lookups.sourceName(sources[0]) }), true));
  } else if (sources.length > 1) {
    chips.push(make("sources", "sources", format(S[pluralKey("chipSource", sources.length)], { count: sources.length }), true));
  }
  if (groups.length > 0) {
    chips.push(make("collections", "collections", groups.map((g) => lookups.collectionTag(g)).join(", "), true));
  }
  const text = textOf(live);
  if (text !== "") {
    chips.push(make("text", "text", format(S.chipText, { text }), true));
  }
  const form = live.formState || {};
  if (isNum(form.sevMax)) {
    chips.push(make("sev", "sev", format(S.chipSev, { name: (S.severityNames || {})[form.sevMax] || String(form.sevMax) }), true));
  }
  if (isNum(form.levelMax)) {
    chips.push(make("level", "level", format(S.chipLevel, { name: (S.levelNames || {})[form.levelMax] || String(form.levelMax) }), true));
  }
  const time = timeLabel(form.from, form.to, lookups, S);
  if (time !== null) {
    chips.push(make("time", "time", time, true));
  }
  const toggles = live.toggles || {};
  if (toggles.hideNoise) {
    chips.push(make("noise", "noise", S.chipNoise, true));
  }
  if (toggles.unclassified) {
    chips.push(make("unclassified", "unclassified", S.chipUnclassified, true));
  }
  let rest = 0;
  topNodes(live.userFilter || null).forEach((node, i) => {
    if (!isCondition(node)) {
      return;
    }
    const label = chipLabel(node, lookups, S);
    if (label === null) {
      rest += countConditions(node);
    } else {
      chips.push(make("node:" + i, "node", label, true));
    }
  });
  if (rest > 0) {
    chips.push(make("tree", "tree", format(S[pluralKey("chipTree", rest)], { n: rest }), false));
  }
  return chips;
}

// The state after removing one chip. Never mutates its input; takes the condition out of the raw form text
// AND the applied value together, so the next captureLive() cannot bring it back. Unknown or non-removable
// ids (and "tree") return equal copies.
export function removeChip(live, selection, chipId) {
  const next = {
    ...live,
    form: { ...(live.form || { sev: "", level: "", from: "", to: "" }) },
    formState: { ...(live.formState || { sevMax: null, levelMax: null, from: null, to: null }) },
    toggles: { ...(live.toggles || { hideNoise: false, unclassified: false }) },
  };
  const sel = { sourceIds: idsOf(selection && selection.sourceIds).slice(), collectionIds: idsOf(selection && selection.collectionIds).slice() };
  switch (chipId) {
    case "sources":
      sel.sourceIds = [];
      break;
    case "collections":
      sel.collectionIds = [];
      break;
    case "text":
      next.searchText = "";
      break;
    case "sev":
      next.formState.sevMax = null;
      next.form.sev = "";
      break;
    case "level":
      next.formState.levelMax = null;
      next.form.level = "";
      break;
    case "time":
      next.formState.from = null;
      next.formState.to = null;
      next.form.from = "";
      next.form.to = "";
      break;
    case "noise":
      next.toggles.hideNoise = false;
      break;
    case "unclassified":
      next.toggles.unclassified = false;
      break;
    default: {
      const match = /^node:(\d+)$/.exec(String(chipId));
      if (match) {
        const nodes = topNodes(live.userFilter || null);
        const index = Number(match[1]);
        if (index < nodes.length) {
          next.userFilter = wrapNodes(nodes.filter((_, i) => i !== index));
        }
      }
    }
  }
  return { live: next, selection: sel };
}

// How many chips fit before "+N more". widths: outer width of each chip (gap included), in order;
// available: width of the chip area; moreWidth: width of the "+N more" button. A width that cannot be
// measured (0, NaN, no layout) never hides anything: everything shows.
export function fitChips(widths, available, moreWidth) {
  const list = Array.isArray(widths) ? widths.map((w) => (isNum(w) && w > 0 ? w : 0)) : [];
  if (!isNum(available) || available <= 0 || list.every((w) => w === 0)) {
    return list.length;
  }
  const total = list.reduce((a, b) => a + b, 0);
  if (total <= available) {
    return list.length;
  }
  const more = isNum(moreWidth) && moreWidth > 0 ? moreWidth : 0;
  let used = more;
  let count = 0;
  for (const w of list) {
    if (used + w > available) {
      break;
    }
    used += w;
    count += 1;
  }
  return count;
}

const hasConditions = (filter) => topNodes(filter || null).some(isCondition);

// "Filtered": the view's own conditions, the side selection or the ancestors' conditions narrow the records.
// Mirrors chipsFromState without needing lookups.
export function isFiltered(live, selection, parentsFilter) {
  const form = live.formState || {};
  const toggles = live.toggles || {};
  return (
    idsOf(selection && selection.sourceIds).length > 0 ||
    idsOf(selection && selection.collectionIds).length > 0 ||
    textOf(live) !== "" ||
    isNum(form.sevMax) ||
    isNum(form.levelMax) ||
    isNum(form.from) ||
    isNum(form.to) ||
    Boolean(toggles.hideNoise) ||
    Boolean(toggles.unclassified) ||
    hasConditions(live.userFilter) ||
    hasConditions(parentsFilter)
  );
}
