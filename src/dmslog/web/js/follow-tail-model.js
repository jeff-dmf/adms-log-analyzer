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

// `view` is the grid adapter: tailInfo() -> {atEnd, top, visible, length}, followTail() -> Promise (reload
// the end of the data and keep the view at the bottom), total() -> number | null (count shown by the grid).
// `onState` receives {on, pending, position}. Answers that arrive after a newer refresh, a case change
// or switching off are dropped (generation token); the request is aborted too.
export function createFollowFlow({ api, getCase, getFilter, view, onState }) {
  let generation = 0;
  let controller = null;
  const state = { on: false, pending: 0, position: "bottom" };

  function emit() {
    onState({ ...state });
  }

  function cancel() {
    generation += 1;
    if (controller) {
      controller.abort();
      controller = null;
    }
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
    const position = tailPosition(view.tailInfo());
    state.position = position;
    if (position === "bottom") {
      // Load the end of the data first: only then are the counted records in the buffer.
      await view.followTail();
      if (mine === generation) {
        state.pending = 0;
        emit();
      }
      return;
    }
    controller = new AbortController();
    try {
      const result = await api.count(caseId, getFilter(), controller.signal);
      if (mine !== generation || caseId !== getCase() || !state.on) {
        return;
      }
      state.pending = pendingCount(view.total(), result.count);
      emit();
    } catch (err) {
      // A failed count only leaves the previous number in place.
    } finally {
      if (mine === generation) {
        controller = null;
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
      state.pending = 0;
    }
    emit();
  }

  function setOn(value) {
    const next = Boolean(value);
    if (next === state.on) {
      return;
    }
    state.on = next;
    state.pending = 0;
    cancel();
    state.position = tailPosition(view.tailInfo());
    emit();
  }

  function reset() {
    cancel();
    state.pending = 0;
    state.position = "bottom";
    emit();
  }

  return { state, refresh, viewChanged, setOn, reset, cancel };
}
