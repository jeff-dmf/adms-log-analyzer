// Pure model of "Open only part first" (ROADMAP 9.9, ARQUITETURA 9.3b): the time window, the preview of the sources
// (POST .../collections/plan), what goes into `first` of POST .../collections, and the pending-work summary
// (GET .../pending). No DOM here; the DOM side is partial-open.js and resume-banner.js. Everything that comes from the
// API (names, members, times, counts) is untrusted: it is normalized here and written with textContent only.
import { parseTs } from "./time.js";

export const MAX_FIRST_SOURCES = 500; // the server accepts at most 500 names in first.sources
export const MAX_PLAN_SOURCES = 2000; // the preview shows at most this many rows
const CAPTURE_STAMP = /_(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})_/;

// A count from the API as a safe non-negative integer: null, undefined, NaN, negatives and text become 0.
export function safeCount(value) {
  const n = typeof value === "number" ? value : Number(value);
  return Number.isFinite(n) && n > 0 ? Math.floor(n) : 0;
}

// An instant (epoch ms of the log clock) as a safe integer, or null.
export function safeInstant(value) {
  return typeof value === "number" && Number.isSafeInteger(value) ? value : null;
}

// One window field. {state: "empty"} | {state: "ok", ms} | {state: "bad"}. The year must be the one typed: Date.UTC
// reads years 0 to 99 as 1900 to 1999, which would silently move the window.
export function parseInstant(text) {
  const value = text === null || text === undefined ? "" : String(text);
  if (value.trim() === "") {
    return { state: "empty", ms: null };
  }
  const ms = parseTs(value);
  if (ms === null || !Number.isSafeInteger(ms)) {
    return { state: "bad", ms: null };
  }
  const year = /^\s*(\d{4})-/.exec(value);
  if (!year || new Date(ms).getUTCFullYear() !== Number(year[1])) {
    return { state: "bad", ms: null };
  }
  return { state: "ok", ms };
}

// The window `[from, to)` of the two fields. errors.from / errors.to: "bad" (not a time) | "order" (to is not after
// from; reported on `to`). Both empty is a valid "no window".
export function parseWindow(fromText, toText) {
  const from = parseInstant(fromText);
  const to = parseInstant(toText);
  const errors = {};
  if (from.state === "bad") {
    errors.from = "bad";
  }
  if (to.state === "bad") {
    errors.to = "bad";
  }
  if (!errors.from && !errors.to && from.state === "ok" && to.state === "ok" && from.ms >= to.ms) {
    errors.to = "order";
  }
  return {
    from: from.state === "ok" ? from.ms : null,
    to: to.state === "ok" ? to.ms : null,
    errors,
    valid: !errors.from && !errors.to,
    empty: from.state === "empty" && to.state === "empty",
  };
}

function text(value) {
  return typeof value === "string" ? value : "";
}

// The sources of a `plan` answer, normalized. A row without a member name cannot be chosen and is dropped; a repeated
// member keeps its first row. {sources, truncated}.
export function normalizePlan(data) {
  const list = data && Array.isArray(data.sources) ? data.sources : [];
  const seen = new Set();
  const sources = [];
  let truncated = false;
  for (const raw of list) {
    if (!raw || typeof raw !== "object" || typeof raw.member !== "string" || raw.member === "" || seen.has(raw.member)) {
      continue;
    }
    if (sources.length >= MAX_PLAN_SOURCES) {
      truncated = true;
      break;
    }
    seen.add(raw.member);
    sources.push({
      member: raw.member,
      name: text(raw.name) !== "" ? raw.name : raw.member,
      family: text(raw.family),
      process: text(raw.process),
      pid: typeof raw.pid === "number" && Number.isFinite(raw.pid) ? raw.pid : null,
      sensitive: raw.sensitive === true,
      rotation: raw.rotation === null || raw.rotation === undefined ? null : String(raw.rotation),
      size: raw.size === null || raw.size === undefined ? null : safeCount(raw.size),
      selected: raw.selected === true,
      indexable: raw.indexable !== false,
      firstTs: safeInstant(raw.first_ts),
      lastTs: safeInstant(raw.last_ts),
    });
  }
  return { sources, truncated };
}

// Only a source the scope selects and the indexer can read may be opened first.
export function canChoose(source) {
  return source.selected === true && source.indexable === true;
}

export function defaultChecked(sources) {
  return new Set(sources.filter(canChoose).map((s) => s.member));
}

// Epoch ms (log clock) of the capture stamp in an ERS file name (..._yyyymmddHHMMSS_...), or null.
export function captureTs(name) {
  const m = CAPTURE_STAMP.exec(String(name));
  if (!m) {
    return null;
  }
  const [year, month, day, hour, minute, second] = m.slice(1).map(Number);
  if (month < 1 || month > 12 || day < 1 || day > 31 || hour > 23 || minute > 59 || second > 59) {
    return null;
  }
  const ms = Date.UTC(year, month - 1, day, hour, minute, second);
  const back = new Date(ms);
  return back.getUTCMonth() === month - 1 && back.getUTCDate() === day ? ms : null;
}

// The period the choosable sources cover: the earliest first_ts to the latest last_ts; when no last_ts is known
// (zip members are not read to the end) the capture time of the file name; else the latest first_ts. `end` says which:
// "read" (a last_ts of a log), "capture" (the capture time of the name) or "latest" (only the latest first_ts: the real
// end is later). `approx` = end is not "read". null when there is no time at all.
export function periodOf(sources, fileName = "") {
  let from = null;
  let last = null;
  let latestFirst = null;
  for (const source of sources) {
    if (!canChoose(source)) {
      continue;
    }
    if (source.firstTs !== null) {
      from = from === null ? source.firstTs : Math.min(from, source.firstTs);
      latestFirst = latestFirst === null ? source.firstTs : Math.max(latestFirst, source.firstTs);
    }
    if (source.lastTs !== null) {
      last = last === null ? source.lastTs : Math.max(last, source.lastTs);
    }
  }
  if (from === null) {
    return null;
  }
  if (last !== null) {
    return { from, to: Math.max(last, from), approx: false, end: "read" };
  }
  const capture = captureTs(fileName);
  if (capture !== null && capture >= from) {
    return { from, to: capture, approx: true, end: "capture" };
  }
  return { from, to: Math.max(latestFirst, from), approx: true, end: "latest" };
}

const PERIOD_END_ORDER = ["read", "capture", "latest"]; // the vaguest end wins in a union

// The union of several periods, or null.
export function mergePeriods(periods) {
  let result = null;
  for (const period of periods) {
    if (!period) {
      continue;
    }
    result = result === null
      ? { ...period }
      : {
        from: Math.min(result.from, period.from),
        to: Math.max(result.to, period.to),
        approx: result.approx || period.approx,
        end: PERIOD_END_ORDER.indexOf(period.end) > PERIOD_END_ORDER.indexOf(result.end) ? period.end : result.end,
      };
  }
  return result;
}

// The rotation chain a source belongs to, as the server groups them (stream = family, process, pid, sensitive; chain =
// file name without a trailing ".N"): the first pass always takes a chain whole, so choosing core.log.1 means choosing
// core.log too.
export function chainKey(source) {
  return [source.family, source.process, source.pid === null ? "" : source.pid, source.sensitive ? 1 : 0,
    String(source.name).replace(/\.\d+$/, "")].join("\u0000");
}

// What the checked boxes mean for one previewed path, with chains expanded as the server will: {names, none, tooMany,
// total, count}. names === null: every choosable source is chosen (no restriction); none: nothing choosable is checked.
// `count` is the number of sources that really go first (the checked ones plus the rest of their chains).
export function chosenSources(sources, checked) {
  const eligible = sources.filter(canChoose);
  const chains = new Set(eligible.filter((s) => checked.has(s.member)).map(chainKey));
  const names = eligible.filter((s) => chains.has(chainKey(s))).map((s) => s.member);
  const total = eligible.length;
  if (total === 0 || names.length === total) {
    return { names: null, none: false, tooMany: false, total, count: names.length };
  }
  return { names, none: names.length === 0, tooMany: names.length > MAX_FIRST_SOURCES, total, count: names.length };
}

// The members of the chain of `member` among the choosable sources (the box and its siblings change together).
export function chainMembers(sources, member) {
  const found = sources.find((s) => s.member === member);
  if (!found || !canChoose(found)) {
    return [];
  }
  const key = chainKey(found);
  return sources.filter((s) => canChoose(s) && chainKey(s) === key).map((s) => s.member);
}

// The `first` object of POST .../collections, or null when nothing narrows the opening. Keys that are not set are left
// out (the server rejects an empty object and unknown keys).
export function buildFirst({ from = null, to = null, sources = null }) {
  const first = {};
  if (safeInstant(from) !== null) {
    first.from = from;
  }
  if (safeInstant(to) !== null) {
    first.to = to;
  }
  if (Array.isArray(sources) && sources.length > 0) {
    first.sources = sources.slice();
  }
  return Object.keys(first).length > 0 ? first : null;
}

// Whether `[from, to)` can hold anything of the period. Open ends are unbounded. Unknown period: true.
export function windowTouchesPeriod(window, period) {
  if (!period) {
    return true;
  }
  const from = window.from === null ? -Infinity : window.from;
  const to = window.to === null ? Infinity : window.to;
  return from <= period.to && to > period.from;
}

// -- pending work (GET .../pending) -----------------------------------------------------------------------------

// {running, items: [{group, partial, pending, indexing}]} from the answer; anything unexpected is an empty list.
export function normalizePending(data) {
  const list = data && Array.isArray(data.pending) ? data.pending : [];
  const items = [];
  for (const raw of list) {
    if (!raw || typeof raw !== "object") {
      continue;
    }
    items.push({
      group: raw.group === null || raw.group === undefined ? null : raw.group,
      partial: safeCount(raw.partial),
      pending: safeCount(raw.pending),
      indexing: safeCount(raw.indexing),
    });
  }
  return { running: Boolean(data && data.running === true), items };
}

// {collections, sources}: how many collections are incomplete and how many of their sources still have work.
export function pendingTotals(items) {
  let sources = 0;
  for (const item of items) {
    sources += item.partial + item.pending + item.indexing;
  }
  return { collections: items.length, sources };
}

// Names of `done.first_unmatched`: strings only, no empty names.
export function unmatchedNames(data) {
  const list = data && Array.isArray(data.first_unmatched) ? data.first_unmatched : [];
  return list.filter((name) => typeof name === "string" && name !== "");
}

// done.partial_left as a safe count.
export function partialLeft(data) {
  return safeCount(data && data.partial_left);
}
