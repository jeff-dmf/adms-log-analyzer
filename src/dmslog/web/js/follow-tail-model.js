// Pure model of "Follow tail": where the grid is relative to the end of the data, and the flow that
// reacts to the server's `watch` events (reload the tail, or count what arrived while scrolled up).
// No DOM here; follow-tail.js owns the elements and the EventSource.

export const DEBOUNCE_MS = 400;            // wait for a burst of watch events to settle
export const MAX_WAIT_MS = 2000;           // a continuous stream of events still refreshes this often
export const DERIVED_DEBOUNCE_MS = 1500;   // the derived panels reload less often
export const DERIVED_MAX_WAIT_MS = 6000;

// Debounce with a maximum wait: `trigger()` postpones `run` by `wait` ms, but never beyond `maxWait` ms
// after the first trigger of a burst. `timers` ({now, setTimeout, clearTimeout}) is injectable for tests.
export function createDebounce({ run, wait, maxWait, timers }) {
  const t = timers || {
    now: () => Date.now(),
    setTimeout: (fn, ms) => setTimeout(fn, ms),
    clearTimeout: (id) => clearTimeout(id),
  };
  let timer = null;
  let first = null;

  function fire() {
    timer = null;
    first = null;
    run();
  }

  return {
    trigger() {
      const now = t.now();
      if (first === null) {
        first = now;
      }
      if (timer !== null) {
        t.clearTimeout(timer);
      }
      const delay = Math.max(0, Math.min(wait, first + maxWait - now));
      timer = t.setTimeout(fire, delay);
    },
    cancel() {
      if (timer !== null) {
        t.clearTimeout(timer);
      }
      timer = null;
      first = null;
    },
    pending: () => timer !== null,
  };
}

export { MAX_STREAM_FAILURES, createStreamGuard } from "./status-bar-model.js";

// "bottom": the last row of the data is in view; "scrolled": the buffer ends the data but the view is
// above its last row; "middle": the buffer does not reach the end of the data.
export function tailPosition(info) {
  if (!info || !info.atEnd) {
    return "middle";
  }
  return info.top + info.visible >= info.length ? "bottom" : "scrolled";
}

// How many records arrived since the baseline total (never negative, 0 when unknown).
export function pendingCount(baseTotal, currentTotal) {
  if (baseTotal === null || baseTotal === undefined) {
    return 0;
  }
  const base = Number(baseTotal);
  const current = Number(currentTotal);
  if (!Number.isFinite(base) || !Number.isFinite(current)) {
    return 0;
  }
  return Math.max(0, current - base);
}

// Parses the data of a `watch` event; null when it is not an object.
export function parseWatchEvent(text) {
  try {
    const data = JSON.parse(text);
    return data && typeof data === "object" ? data : null;
  } catch (err) {
    return null;
  }
}

// What a `watch` event says that matters here: whether a source appeared or was rewritten (a rotation shows as
// "new" plus "reindex"). An appended file ("append") only grows the total, which the count already follows.
const SOURCE_CHANGES = new Set(["new", "reindex", "resume"]);
export function watchInfo(data) {
  const list = data && Array.isArray(data.sources) ? data.sources : [];
  return { sourcesChanged: list.some((item) => item && SOURCE_CHANGES.has(item.action)) };
}

// Pause while the tab is hidden: the stream stays open, but nothing is counted or reloaded. What arrives while
// hidden (or sits in a debounce when the tab hides) is remembered, and ONE update runs when the tab is back.
// `sources` accumulates over a burst: take it when the derived refresh runs.
export function createWatchGate(hidden = false) {
  const gate = {
    hidden: Boolean(hidden),
    dirty: false,
    sources: false,
    // A watch event arrived. True: schedule the refreshes now; false: remembered for later.
    note(info) {
      if (info && info.sourcesChanged) {
        gate.sources = true;
      }
      if (gate.hidden) {
        gate.dirty = true;
        return false;
      }
      return true;
    },
    // `pending`: a refresh was scheduled or running when the tab hid (it is cancelled, so it must run again).
    hide(pending = false) {
      gate.hidden = true;
      if (pending) {
        gate.dirty = true;
      }
    },
    // Back in view: {refresh, sources} for the single update, or null when nothing was missed.
    show() {
      gate.hidden = false;
      if (!gate.dirty) {
        return null;
      }
      gate.dirty = false;
      gate.sources = false;
      return { refresh: true, sources: true }; // a gap may have hidden a new source: refresh them once
    },
    takeSources() {
      const value = gate.sources;
      gate.sources = false;
      return value;
    },
  };
  return gate;
}

// `view` is the grid adapter: tailInfo() -> {atEnd, top, visible, length}, followTail(isCurrent) -> Promise<false |
// anything> (reload the end of the data and keep the view at the bottom; it checks isCurrent() before it counts and
// resolves to false when the operation was dropped, e.g. the user scrolled meanwhile), total() -> number | null (count
// shown by the grid), showTotal(n, exact) -> boolean (optional: the grid shows the newest total, so the status and
// the collection agree; false = the grid refused it and nothing is adopted).
// ONE source of truth for the total: the newest count answer (`state.total`). `base` is the total when the
// view stopped following (the grid's own count at that time); pending = total - base, shown as "new records".
// `onState` receives {on, pending, position}.
// ONE operation token (`generation`) covers every async effect: it is bumped by a new refresh, hiding the tab,
// returning to the bottom while counting, switching off, a case change and destroy. The effects (the count, the
// publication to the grid, the load of the tail) check it right before they happen, and the grid receives it as
// `isCurrent` so its own steps stop too.
export function createFollowFlow({ api, getCase, getFilter, view, onState }) {
  let generation = 0;
  let controller = null;
  const state = { on: false, pending: 0, position: "bottom", total: null };
  let base = null;   // total when the view left the bottom; null while following
  let pushed = null; // the last total this flow gave to the grid; a different grid total means the grid reloaded
  let kind = null;   // what the running operation does: "count" or "load" (the tail)

  function forget() {
    base = null;
    pushed = null;
    state.pending = 0;
  }

  function emit() {
    onState({ ...state });
  }

  let active = false; // a refresh or engage of the current generation is running

  function cancel() {
    generation += 1;
    active = false;
    kind = null;
    if (controller) {
      controller.abort();
      controller = null;
    }
  }

  // Loads the end of the data. Only a completed load counts as "at the bottom"; a dropped one (the user scrolled,
  // the operation became obsolete) leaves the baseline alone.
  async function loadTail(mine) {
    kind = "load";
    let done;
    try {
      done = await view.followTail(() => mine === generation);
    } finally {
      if (mine === generation) {
        active = false;
        kind = null;
      }
    }
    if (mine !== generation) {
      return;
    }
    if (done !== false) {
      forget();
    }
    state.position = tailPosition(view.tailInfo());
    emit();
  }

  async function refresh() {
    if (!state.on) {
      return;
    }
    const caseId = getCase();
    if (caseId === null || caseId === undefined) {
      return;
    }
    cancel();
    const mine = generation;
    active = true;
    const filter = getFilter();
    const position = tailPosition(view.tailInfo());
    state.position = position;
    if (position === "bottom") {
      // Load the end of the data first: only then are the counted records in the buffer.
      await loadTail(mine);
      return;
    }
    kind = "count";
    controller = new AbortController();
    try {
      const result = await api.count(caseId, filter, controller.signal);
      if (mine !== generation || caseId !== getCase() || !state.on || filter !== getFilter()) {
        return;
      }
      const total = Number(result.count);
      const shown = view.total();
      const nextBase = base === null || (pushed !== null && shown !== pushed) ? shown : base; // first count since the view left the end, or the grid reloaded with its own total
      if (typeof view.showTotal === "function" && nextBase !== null && !view.showTotal(total, result.exact !== false)) {
        return; // the grid refused it (a view change or a failed count owns the number): adopt nothing
      }
      base = nextBase;
      state.total = total;
      state.pending = pendingCount(base, total);
      pushed = total;
      emit();
    } catch (err) {
      // A failed count only leaves the previous number in place.
    } finally {
      if (mine === generation) {
        controller = null;
        active = false;
        kind = null;
      }
    }
  }

  // The grid moved (scroll, jump, reload): returning to the bottom clears the counter.
  function viewChanged() {
    const position = tailPosition(view.tailInfo());
    if (position === state.position) {
      return;
    }
    state.position = position;
    if (position === "bottom" && state.on && state.pending > 0) {
      // Back at the end of the OLD buffer: the pending records are not loaded yet.
      refresh().catch(() => {});
      return;
    }
    if (position === "bottom") {
      if (kind === "count") {
        cancel(); // a count started while scrolled up must not recreate the counter now that the view is at the end
      }
      forget();
    }
    emit();
  }

  function setOn(value) {
    const next = Boolean(value);
    if (next === state.on) {
      return;
    }
    state.on = next;
    state.total = null;
    forget();
    cancel();
    state.position = tailPosition(view.tailInfo());
    emit();
  }

  // Follow tail was just switched on: go to the end whatever the position (scrolling up later stops following).
  async function engage() {
    if (!state.on) {
      return;
    }
    if (getCase() === null || getCase() === undefined) {
      return;
    }
    cancel();
    const mine = generation;
    active = true;
    await loadTail(mine);
  }

  function reset() {
    cancel();
    forget();
    state.total = null;
    state.position = "bottom";
    emit();
  }

  return { state, refresh, engage, viewChanged, setOn, reset, cancel, busy: () => active };
}
