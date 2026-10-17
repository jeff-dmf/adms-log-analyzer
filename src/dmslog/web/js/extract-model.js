// "Extract" panel (ROADMAP 9.8, ARQUITETURA 10.11): the rules without DOM. The regex, the extracted values and the
// TOML text come from untrusted logs or from what the user typed; this module only computes strings and numbers,
// the panel writes them with textContent.
//
// api.extract.check(body, signal), api.extract.run(caseId, body, signal), api.extract.promote(caseId, body, signal).
import { andFilters } from "./filters.js";

export const TARGETS = ["msg", "text"];
export const TYPES = ["str", "int", "hex", "guid", "float"];
export const DEFAULT_LIMIT = 1000;
export const MAX_LIMIT = 10000;
export const DEFAULT_TOP = 20;
export const MAX_TOP = 100;
export const MAX_PROMOTE_IDS = 20;
export const CHECK_DEBOUNCE_MS = 300;
const MAX_SNIPPET = 24;

// A whole number from 1 to `max` typed as plain digits ("1e3", "+5", "0x10", " 7 ", "" and 1.5 are not numbers here).
export function parseBounded(text, max) {
  const value = String(text === null || text === undefined ? "" : text).trim();
  if (!/^[0-9]{1,9}$/.test(value)) {
    return null;
  }
  const n = Number(value);
  return Number.isSafeInteger(n) && n >= 1 && n <= max ? n : null;
}

// The server writes "... (at offset N)" at the end of a bad_regex / regex_too_costly message; the offset counts
// characters (code points) of the text that was sent.
export function errorOffset(message) {
  const match = /\(at offset ([0-9]{1,6})\)\s*$/.exec(String(message || ""));
  if (!match) {
    return null;
  }
  const n = Number(match[1]);
  return Number.isSafeInteger(n) ? n : null;
}

// The text around the offset in code points: { before, at, after } (all clipped), or null when the offset is
// unknown or beyond the end (a problem "at the end" has at = "").
export function errorContext(regex, offset) {
  if (!Number.isSafeInteger(offset) || offset < 0) {
    return null;
  }
  const chars = Array.from(String(regex === null || regex === undefined ? "" : regex));
  if (offset > chars.length) {
    return null;
  }
  const start = Math.max(0, offset - MAX_SNIPPET);
  return {
    before: (start > 0 ? "…" : "") + chars.slice(start, offset).join(""),
    at: chars[offset] || "",
    after: chars.slice(offset + 1, offset + 1 + MAX_SNIPPET).join("") + (chars.length > offset + 1 + MAX_SNIPPET ? "…" : ""),
  };
}

// What the user can do about an API error: { code, text, offset, problem } with problem "regex" for the errors of
// the regex itself (shown next to the field), "timeout" for 408 and "other" for the rest.
export function classifyError(err) {
  const code = err && typeof err.code === "string" ? err.code : "";
  const text = err && err.message ? String(err.message) : "";
  if (code === "bad_regex" || code === "regex_too_costly") {
    return { code, text, offset: errorOffset(text), problem: "regex" };
  }
  if (code === "extract_timeout" || (err && err.status === 408)) {
    return { code: code || "extract_timeout", text, offset: null, problem: "timeout" };
  }
  return { code, text, offset: null, problem: "other" };
}

// types of the request: only the groups the user declared as something other than text ("str" is the default).
export function typesPayload(columns, choices) {
  const out = {};
  for (const column of columns || []) {
    const chosen = choices && Object.prototype.hasOwnProperty.call(choices, column.name) ? choices[column.name] : "str";
    if (TYPES.includes(chosen) && chosen !== "str") {
      out[column.name] = chosen;
    }
  }
  return out;
}

// The names of the groups written in the typed text, in order and without repeats: the types sent with a check
// must name groups the regex has (the server refuses the others), and the regex may be half typed.
export function groupNames(regex) {
  const names = [];
  for (const match of String(regex === null || regex === undefined ? "" : regex).matchAll(/\(\?P<([a-z][a-z0-9_]{0,31})>/g)) {
    if (!names.includes(match[1])) {
      names.push(match[1]);
    }
  }
  return names.slice(0, 16);
}

// The user's choices that still apply after a new regex: the groups that kept their name keep their type.
export function reconcileChoices(previous, columns) {
  const out = {};
  for (const column of columns || []) {
    if (previous && Object.prototype.hasOwnProperty.call(previous, column.name) && TYPES.includes(previous[column.name])) {
      out[column.name] = previous[column.name];
    }
  }
  return out;
}

// The state of the inputs: { ok, problem } where problem is a key of S (or null). `check` is the state of the
// live validation: only a regex the server accepted for these very inputs can run (a check in progress waits).
export function inputState({ caseId, regex, check, limit, top }) {
  if (!caseId) {
    return { ok: false, problem: "exNoCase", limit: null, top: null };
  }
  const limitValue = parseBounded(limit, MAX_LIMIT);
  const topValue = parseBounded(top, MAX_TOP);
  if (String(regex || "") === "") {
    return { ok: false, problem: "exNeedRegex", limit: limitValue, top: topValue };
  }
  if (!check || check.status === "error") {
    return { ok: false, problem: "exBadRegexShort", limit: limitValue, top: topValue };
  }
  if (check.status !== "ok") {
    return { ok: false, problem: "exChecking", limit: limitValue, top: topValue };
  }
  if (limitValue === null) {
    return { ok: false, problem: "exBadLimit", limit: limitValue, top: topValue };
  }
  if (topValue === null) {
    return { ok: false, problem: "exBadTop", limit: limitValue, top: topValue };
  }
  return { ok: true, problem: null, limit: limitValue, top: topValue };
}

// Identity of a request: the result stays valid for the case, filter and inputs it was computed for.
export function requestKey(parts) {
  return JSON.stringify([parts.caseId, parts.filter || null, parts.regex, parts.target, parts.types, parts.limit, parts.top]);
}

// -- numbers and values -----------------------------------------------------------------------------------------

const isNum = (n) => typeof n === "number" && Number.isFinite(n);

// A JSON number that is not an exact integer of a double: the server sends integers up to 18 digits, JSON.parse
// rounds the ones above 2^53, so neither the text nor a filter built from it can be trusted.
export function isInexactInteger(n) {
  return isNum(n) && Number.isInteger(n) && !Number.isSafeInteger(n);
}

export function formatNumber(n) {
  if (!isNum(n)) {
    return "–";
  }
  if (isInexactInteger(n)) {
    return "≈" + n.toLocaleString("en-US", { useGrouping: false, maximumFractionDigits: 0 });
  }
  if (n === 0) {
    return "0"; // also -0
  }
  if (Number.isInteger(n)) {
    return n.toLocaleString("en-US", { maximumFractionDigits: 0 });
  }
  return String(Number(n.toPrecision(12)));
}

export function formatPct(pct) {
  return isNum(pct) ? String(Math.round(pct * 100) / 100) + "%" : "–";
}

// The text of one value of a column: null (the regex did not match, or the group did not take part) and "" are
// told apart from a text; kinds: "null" | "empty" | "number" | "text".
export function valueView(value, S) {
  if (value === null || value === undefined) {
    return { kind: "null", text: S.exValueNone };
  }
  if (value === "") {
    return { kind: "empty", text: S.exValueEmpty };
  }
  return { kind: typeof value === "number" ? "number" : "text", text: String(value) };
}

const STAT_KEYS = ["count", "min", "max", "avg", "p50", "p90", "p99"];

// The statistics of a numeric column as [{ key, value }] (value already formatted); [] without numbers.
export function statsView(numeric) {
  if (!numeric || typeof numeric !== "object" || !isNum(numeric.count) || numeric.count <= 0) {
    return [];
  }
  return STAT_KEYS.map((key) => ({ key, value: formatNumber(numeric[key]) }));
}

// A drill-down leaf the panel may apply: the server gave one (long texts have none) and its value survived JSON.
export function usableLeaf(leaf) {
  if (!leaf || typeof leaf !== "object" || !leaf.extract || typeof leaf.extract !== "object") {
    return { ok: false, reason: "exNoLeaf" };
  }
  if (isInexactInteger(leaf.extract.eq)) {
    return { ok: false, reason: "exLeafUnsafe" };
  }
  return { ok: true, reason: null };
}

// The result as the panel draws it. Tolerates a missing or odd answer: every count is a finite number or 0.
export function buildResult(data) {
  const count = (n) => (isNum(n) && n >= 0 ? n : 0);
  const columns = (data && Array.isArray(data.columns) ? data.columns : []).map((column) => {
    const values = (Array.isArray(column.values) ? column.values : []).map((item) => ({
      value: item.value === undefined ? null : item.value,
      count: count(item.count),
      pct: isNum(item.pct) ? item.pct : 0,
      leaf: item.leaf || null,
    }));
    const others = column.others && typeof column.others === "object" ? column.others : {};
    return {
      name: String(column.name),
      type: TYPES.includes(column.type) ? column.type : "str",
      suggested: TYPES.includes(column.suggested_type) ? column.suggested_type : "str",
      present: count(column.present),
      distinct: count(column.distinct),
      values,
      othersCount: count(others.count),
      othersGroups: count(others.groups),
      stats: statsView(column.numeric),
    };
  });
  return {
    regex: data && typeof data.regex === "string" ? data.regex : "",
    target: data && data.target === "text" ? "text" : "msg",
    scanned: count(data && data.scanned),
    matched: count(data && data.matched),
    cut: count(data && data.cut),
    truncated: Boolean(data && data.truncated),
    window: count(data && data.window),
    ids: (data && Array.isArray(data.rows) ? data.rows : [])
      .map((row) => row && row.id)
      .filter((id) => Number.isSafeInteger(id) && id >= 0),
    columns,
  };
}

// Ids for "Promote to template": the first records that matched (the server takes up to 20).
export function promoteIds(result) {
  return result && Array.isArray(result.ids) ? result.ids.slice(0, MAX_PROMOTE_IDS) : [];
}

export function canPromote(result, target) {
  return Boolean(result) && result.target === "msg" && target === "msg" && promoteIds(result).length > 0;
}

// The promote answer as the dialog draws it.
export function buildPromote(data) {
  const check = data && data.check && typeof data.check === "object" ? data.check : {};
  return {
    toml: data && typeof data.toml === "string" ? data.toml : "",
    file: data && typeof data.file === "string" ? data.file : "",
    loads: check.loads === true,
    checkError: typeof check.error === "string" ? check.error : "",
    warnings: (data && Array.isArray(data.warnings) ? data.warnings : []).map((w) => ({
      code: String(w && w.code),
      message: String((w && w.message) || (w && w.code) || ""),
    })),
  };
}

// The user filter after applying a drill-down leaf.
export function drillFilter(userFilter, leaf) {
  return leaf ? andFilters(userFilter, leaf) : userFilter;
}

// -- flows (promises and tokens, no clock) ------------------------------------------------------------------------

function realDelay() {
  return new Promise((resolve) => setTimeout(resolve, CHECK_DEBOUNCE_MS));
}

// Live validation while typing. schedule(input) waits for `delay()` (a promise; the panel passes a timer, tests a
// promise they settle), then asks the server; a newer schedule()/cancel() makes every older step a no-op, so a late
// answer never overwrites the state of a newer input. `input` = { regex, target, types }.
// States: idle | checking | ok { result } | error { error: {text, offset, code, problem} }.
export function createCheckFlow({ api, onState, delay = realDelay }) {
  const state = { status: "idle", result: null, error: null, input: null };
  let seq = 0;
  let controller = null;
  const emit = () => onState && onState(state);

  function abort() {
    if (controller) {
      controller.abort();
      controller = null;
    }
  }

  async function schedule(input) {
    seq += 1;
    const mine = seq;
    abort();
    state.input = input;
    if (!input || String(input.regex || "") === "") {
      Object.assign(state, { status: "idle", result: null, error: null });
      emit();
      return;
    }
    // The previous verdict stays until the new one arrives: no flicker while typing.
    state.status = "checking";
    emit();
    await delay();
    if (mine !== seq) {
      return;
    }
    controller = typeof AbortController === "function" ? new AbortController() : null;
    const mySignal = controller ? controller.signal : undefined;
    try {
      const result = await api.extract.check(
        { regex: input.regex, target: input.target, types: input.types }, mySignal);
      if (mine !== seq) {
        return;
      }
      Object.assign(state, { status: "ok", result, error: null });
    } catch (err) {
      if (mine !== seq || (err && err.name === "AbortError")) {
        return;
      }
      Object.assign(state, { status: "error", result: null, error: classifyError(err) });
    }
    emit();
  }

  return {
    state,
    schedule,
    cancel() {
      seq += 1;
      abort();
      Object.assign(state, { status: "idle", result: null, error: null, input: null });
      emit();
    },
  };
}

// The extraction itself: one request counts, a new run or cancel() drops the older answer, and an answer that
// arrives after the case changed is dropped too. States: idle | loading | done { data } | error { error }.
export function createRunFlow({ api, getCase, onState }) {
  const state = { status: "idle", data: null, error: null, caseId: null, key: null };
  let seq = 0;
  let controller = null;
  const emit = () => onState && onState(state);

  function abort() {
    if (controller) {
      controller.abort();
      controller = null;
    }
  }

  async function run(body, key) {
    seq += 1;
    const mine = seq;
    abort();
    const caseId = getCase();
    if (!caseId) {
      Object.assign(state, { status: "idle", data: null, error: null, caseId: null, key: null });
      emit();
      return;
    }
    controller = typeof AbortController === "function" ? new AbortController() : null;
    Object.assign(state, { status: "loading", error: null, caseId });
    emit();
    try {
      const data = await api.extract.run(caseId, body, controller ? controller.signal : undefined);
      if (mine !== seq || getCase() !== caseId) {
        return;
      }
      Object.assign(state, { status: "done", data: buildResult(data), error: null, key });
    } catch (err) {
      if (mine !== seq || getCase() !== caseId || (err && err.name === "AbortError")) {
        return;
      }
      Object.assign(state, { status: "error", error: classifyError(err) });
    }
    emit();
  }

  return {
    state,
    run,
    cancel() {
      seq += 1;
      abort();
      Object.assign(state, { status: "idle", data: null, error: null, caseId: null, key: null });
      emit();
    },
  };
}

// "Promote to template": one request at a time, the same token rule; closing the dialog cancels it.
// States: idle | loading | done { promote } | error { error }.
export function createPromoteFlow({ api, getCase, onState }) {
  const state = { status: "idle", promote: null, error: null };
  let seq = 0;
  let controller = null;
  const emit = () => onState && onState(state);

  function abort() {
    if (controller) {
      controller.abort();
      controller = null;
    }
  }

  async function load(body) {
    seq += 1;
    const mine = seq;
    abort();
    const caseId = getCase();
    if (!caseId) {
      Object.assign(state, { status: "idle", promote: null, error: null });
      emit();
      return;
    }
    controller = typeof AbortController === "function" ? new AbortController() : null;
    Object.assign(state, { status: "loading", error: null });
    emit();
    try {
      const data = await api.extract.promote(caseId, body, controller ? controller.signal : undefined);
      if (mine !== seq || getCase() !== caseId) {
        return;
      }
      Object.assign(state, { status: "done", promote: buildPromote(data), error: null });
    } catch (err) {
      if (mine !== seq || getCase() !== caseId || (err && err.name === "AbortError")) {
        return;
      }
      Object.assign(state, { status: "error", error: classifyError(err) });
    }
    emit();
  }

  return {
    state,
    load,
    cancel() {
      seq += 1;
      abort();
      Object.assign(state, { status: "idle", promote: null, error: null });
      emit();
    },
  };
}
