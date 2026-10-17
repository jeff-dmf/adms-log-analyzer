// Repeated records (ROADMAP 9.12, ARQUITETURA 10.9): the pure model behind "Hide repeated records", the discreet
// "Repeat of #N, +Δ ms" marker of the grid rows and of the detail. No DOM. One module so main.js loads it once, late:
// the opening never pays for it (the static graph of main.js is at its budget, tests/web/boot-graph.test.mjs).
//
//   filter   {"not": {"repeat": {"window_ms": N}}} is the removable chip "Hide repeats within ..."
//   marks    POST /api/entries/repeats {case, ids <= 1000, repeat} -> {marks: {"<id>": {prev, dt_ms, earlier}}}; only
//            the repeats are listed, so an id that is not in the answer is "looked at, not a repeat"
// Every answer carries the token (generation) of the page that asked: a case change, a new window or a reindex drops
// the answers in flight, and a late answer never writes over newer state.
import {
  DEFAULT_WINDOW_MS, MAX_WINDOW_MS, WINDOW_CHOICES, findHide, hideNode, hideSpec, normalizeWindow, repeatSpec, spanText,
  windowChoices, withHide, withoutHide,
} from "./repeat-filter.js";
import { S, format, pluralKey } from "./strings.js";

// The helpers live in repeat-filter.js (the filter chips use them without this module); re-exported for the callers.
export {
  DEFAULT_WINDOW_MS, MAX_WINDOW_MS, WINDOW_CHOICES, findHide, hideNode, hideSpec, normalizeWindow, repeatSpec, spanText,
  windowChoices, withHide, withoutHide,
};

export const MARK_BATCH = 1000; // ids per request (the server maximum)
export const MAX_KNOWN = 20000; // ids remembered before the store starts over (a long scroll must not grow it without end)
export const PREFS_KEY = "dmslog.repeats";

const isObject = (v) => v !== null && typeof v === "object" && !Array.isArray(v);

// ---- remembered choices ---------------------------------------------------------------------------------------

export function parsePrefs(raw) {
  const out = { mark: true, windowMs: DEFAULT_WINDOW_MS };
  let data = null;
  try {
    data = typeof raw === "string" ? JSON.parse(raw) : null;
  } catch (err) {
    data = null;
  }
  if (!isObject(data)) {
    return out;
  }
  if (typeof data.mark === "boolean") {
    out.mark = data.mark;
  }
  const windowMs = normalizeWindow(data.windowMs);
  if (windowMs !== null) {
    out.windowMs = windowMs;
  }
  return out;
}

// storage: {getItem, setItem} or null; every access is guarded (private windows, blocked site data).
export function createPrefs(storage) {
  let state = parsePrefs(null);
  try {
    state = parsePrefs(storage ? storage.getItem(PREFS_KEY) : null);
  } catch (err) {
    state = parsePrefs(null);
  }
  const save = () => {
    try {
      if (storage) {
        storage.setItem(PREFS_KEY, JSON.stringify(state));
      }
    } catch (err) {
      // not remembered; the choice still holds for this session
    }
  };
  return {
    mark: () => state.mark,
    windowMs: () => state.windowMs,
    setMark(on) {
      state = { ...state, mark: Boolean(on) };
      save();
    },
    setWindow(ms) {
      const n = normalizeWindow(ms);
      if (n !== null) {
        state = { ...state, windowMs: n };
        save();
      }
    },
  };
}

// ---- marks ----------------------------------------------------------------------------------------------------

// One mark of the answer: {prev, dtMs (null when not a number), earlier} or null when it is not usable.
export function parseMark(raw) {
  if (!isObject(raw) || !Number.isSafeInteger(raw.prev) || raw.prev < 0) {
    return null;
  }
  const dtMs = typeof raw.dt_ms === "number" && Number.isFinite(raw.dt_ms) && raw.dt_ms >= 0 ? Math.round(raw.dt_ms) : null;
  const earlier = Number.isSafeInteger(raw.earlier) && raw.earlier > 0 ? raw.earlier : 0;
  return { prev: raw.prev, dtMs, earlier };
}

const grouped = (n) => n.toLocaleString("en-US");

// "Repeat of #12, +340 ms" (the row's tooltip and the detail line).
export function markLabel(mark) {
  return mark.dtMs === null
    ? format(S.repeatMarkNoDt, { prev: mark.prev })
    : format(S.repeatMark, { prev: mark.prev, dt: grouped(mark.dtMs) });
}

export function earlierLabel(mark) {
  return mark.earlier > 0 ? format(S[pluralKey("repeatEarlier", mark.earlier)], { count: grouped(mark.earlier) }) : "";
}

// The marks of the rows on screen. get(id) -> {prev, dtMs, earlier, label} | null, cheap and synchronous (the grid asks
// per row at every render); ensure(ids) queues the ids of the page on screen (the last call wins: the ids of a page the
// reader already left are not asked for), and a short timer merges a burst of scrolling into one request.
//   getWindow()   the window of the marks now (read at every call: a change drops what was looked up)
//   isOn()        marks are wanted at all
//   onChange()    some row got a mark (the grid draws again)
export function createRepeatStore({ api, getCase, getWindow, isOn, onChange, schedule = (fn) => setTimeout(fn, 30), batch = MARK_BATCH }) {
  let caseId = null;
  let windowMs = null;
  let generation = 0;
  let marks = new Map(); // id -> mark (only the repeats)
  let answered = new Set(); // ids whose answer arrived: a repeat (in `marks`) or not
  let asked = new Set(); // ids asked for: answered, in flight or failed (a failure is not retried on every scroll)
  let queue = [];
  let timer = null;
  let controller = null;

  function drop() {
    generation += 1;
    marks = new Map();
    answered = new Set();
    asked = new Set();
    queue = [];
    timer = null;
    if (controller) {
      controller.abort();
      controller = null;
    }
  }

  // Follows the case and the window of the page; a change of either drops everything. True when marks are wanted.
  function sync() {
    const current = getCase() || null;
    const wanted = normalizeWindow(getWindow());
    if (current !== caseId || wanted !== windowMs) {
      caseId = current;
      windowMs = wanted;
      drop();
    }
    return caseId !== null && windowMs !== null && Boolean(isOn());
  }

  function accept(ids, data) {
    const answer = isObject(data) && isObject(data.marks) ? data.marks : {};
    let added = false;
    for (const id of ids) {
      answered.add(id);
      const mark = parseMark(answer[String(id)]);
      if (mark !== null) {
        marks.set(id, { ...mark, label: markLabel(mark) });
        added = true;
      }
    }
    return added;
  }

  async function flush() {
    timer = null;
    if (!sync() || queue.length === 0) {
      queue = [];
      return;
    }
    const ids = queue.slice(0, batch);
    queue = queue.slice(ids.length);
    const token = generation;
    const own = new AbortController();
    controller = own;
    for (const id of ids) {
      asked.add(id);
    }
    try {
      const data = await api.repeatMarks(caseId, ids, { window_ms: windowMs }, own.signal);
      if (token !== generation) {
        return;
      }
      if (accept(ids, data)) {
        onChange();
      }
    } catch (err) {
      // A hint is not worth a message; the ids stay asked until a new window, case or reindex.
    }
    if (token === generation && queue.length > 0 && timer === null) {
      timer = schedule(flush);
    }
  }

  const store = {
    caseId: () => caseId,
    // rowsCase: the case the id belongs to (see ensure); another case than the open one has no mark.
    get(id, rowsCase) {
      return rowsCase !== undefined && rowsCase !== (getCase() || null) ? null : marks.get(id) || null;
    },
    known: (id) => answered.has(id),
    // The ids of the page on screen, which belong to the case `rowsCase` (the grid's own: it keeps the rows of the old
    // case until the new view arrives, and ids repeat from one case to the next). Rows already asked for are skipped; the
    // queue is replaced, not added to.
    ensure(ids, rowsCase) {
      if (!sync() || (rowsCase !== undefined && rowsCase !== caseId)) {
        return;
      }
      if (asked.size > MAX_KNOWN) {
        drop(); // starts over: what is on screen is asked for again
      }
      queue = ids.filter((id) => Number.isSafeInteger(id) && id >= 0 && !asked.has(id));
      if (queue.length > 0 && timer === null) {
        timer = schedule(flush);
      }
    },
    // The records were indexed again (ids moved), or marks were turned off.
    clear() {
      drop();
    },
    // The mark of one record, for the detail: what the grid already looked up, else one request of its own.
    // Resolves to the mark or null; undefined when the answer is stale (case, window or reindex changed meanwhile).
    async lookup(id, signal) {
      if (!sync() || !Number.isSafeInteger(id) || id < 0) {
        return null;
      }
      if (answered.has(id)) {
        return marks.get(id) || null;
      }
      if (asked.size > MAX_KNOWN) {
        drop(); // the same budget as ensure(): single lookups must not grow the store without end
      }
      asked.add(id);
      const token = generation;
      const data = await api.repeatMarks(caseId, [id], { window_ms: windowMs }, signal);
      if (token !== generation) {
        return undefined;
      }
      if (accept([id], data)) {
        onChange();
      }
      return marks.get(id) || null;
    },
  };
  return store;
}

// ---- the controller main.js and the actions talk to ---------------------------------------------------------------

// getUserFilter()/setUserFilter(filter): the filter tree's filter of the open view (main.js). setUserFilter returns the
// promise of the view change. onChange(): redraw the rows (and the detail line).
export function createRepeats({ api, getCase, getUserFilter, setUserFilter, storage, onChange, schedule }) {
  const prefs = createPrefs(storage);
  const store = createRepeatStore({
    api, getCase, schedule, onChange,
    getWindow: () => prefs.windowMs(),
    isOn: () => prefs.mark(),
  });
  return {
    store,
    marking: () => prefs.mark(),
    window: () => prefs.windowMs(),
    windows: () => windowChoices(prefs.windowMs()),
    windowLabel: spanText,
    hidden: () => findHide(getUserFilter()) !== null,
    get: (id, rowsCase) => (prefs.mark() ? store.get(id, rowsCase) : null),
    ensure: (ids, rowsCase) => store.ensure(ids, rowsCase),
    lookup: (id, signal) => (prefs.mark() ? store.lookup(id, signal) : Promise.resolve(null)),
    // Records were indexed again (ids moved) or the case changed: forget the marks. No redraw here: the view reloads next,
    // and a redraw now would ask for the marks of rows that are about to go.
    invalidate() {
      store.clear();
    },
    toggleMarks() {
      prefs.setMark(!prefs.mark());
      store.clear();
      onChange();
    },
    // The chip in the strip is the state: on -> add it with the window in force; off -> take every hide node out.
    toggleHide() {
      if (getCase() === null) {
        return Promise.resolve();
      }
      const current = getUserFilter();
      return setUserFilter(findHide(current) === null ? withHide(current, prefs.windowMs()) : withoutHide(current));
    },
    // The window of the markers and of the hide chip. An invalid value changes nothing.
    setWindow(ms) {
      const n = normalizeWindow(ms);
      if (n === null || n === prefs.windowMs()) {
        return Promise.resolve();
      }
      prefs.setWindow(n);
      store.clear();
      onChange();
      const current = getUserFilter();
      return getCase() !== null && findHide(current) !== null ? setUserFilter(withHide(current, n)) : Promise.resolve();
    },
  };
}
