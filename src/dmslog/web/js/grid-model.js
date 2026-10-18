// Pure model of the virtual grid (no DOM): keyset cursors, the buffered row window and the
// proportional scroll mapping. The DOM layer lives in grid.js.

import { cursorTime } from "./time.js";

export const ROW_HEIGHT = 20;       // px, also used by the CSS through the row elements
export const PAGE_LIMIT = 200;      // rows per request (server maximum is 500)
export const MAX_ROWS = 1200;       // rows kept in memory before trimming the far end
export const PREFETCH_MARGIN = 60;  // fetch when fewer rows than this remain past the viewport
export const SCROLL_SPAN = 20000;   // px of scrollbar travel mapped onto the time range
export const END_CURSOR = Number.MAX_SAFE_INTEGER + ":" + Number.MAX_SAFE_INTEGER;

export function makeCursor(row) {
  return cursorTime(row) + ":" + row.id; // the adjusted time when the rows carry one (the server's cursor axis)
}

// Cursor that starts at the first entry with ts >= the given instant (id -1 sorts before all ids).
export function jumpCursor(ts) {
  return Math.round(ts) + ":-1";
}

export function parseCursor(cursor) {
  const m = /^(-?\d+):(-?\d+)$/.exec(String(cursor));
  return m ? { ts: Number(m[1]), id: Number(m[2]) } : null;
}

// Scroll position (px, 0..span) for an instant inside {min, max}.
export function tsToScroll(ts, range, span = SCROLL_SPAN) {
  if (!range || range.max <= range.min) {
    return 0;
  }
  const fraction = Math.min(1, Math.max(0, (ts - range.min) / (range.max - range.min)));
  return Math.round(fraction * span);
}

// Instant (ms) for a scroll position.
export function scrollToTs(position, range, span = SCROLL_SPAN) {
  if (!range) {
    return null;
  }
  const fraction = Math.min(1, Math.max(0, position / span));
  return Math.round(range.min + fraction * (range.max - range.min));
}

// New first visible row after the grid changed height (a dock opened, dragged or maximized, a breakpoint change).
// selected < 0 or outside the old window: the top stays (only capped to maxTop). Selected row still inside the new
// height: the top stays, so it keeps its distance from the top. It would fall out: it goes to a third of the height.
export function topAfterResize({ selected, top, visibleBefore, visibleAfter, length }) {
  const maxTop = Math.max(0, length - visibleAfter);
  const kept = Math.min(Math.max(0, top), maxTop);
  if (selected < 0 || selected < top || selected >= top + visibleBefore) {
    return kept;
  }
  if (selected - top < visibleAfter) {
    return kept;
  }
  return Math.min(Math.max(0, selected - Math.floor(visibleAfter / 3)), maxTop);
}

// Which page to fetch next, if any: "fwd" near the buffered end, "back" near the start.
export function fetchPlan(top, visible, length, atStart, atEnd, margin = PREFETCH_MARGIN) {
  if (length === 0) {
    return null;
  }
  if (!atEnd && top + visible + margin > length) {
    return "fwd";
  }
  if (!atStart && top < margin) {
    return "back";
  }
  return null;
}

export class RowWindow {
  constructor(maxRows = MAX_ROWS) {
    this.maxRows = maxRows;
    this.reset();
  }

  reset() {
    this.rows = [];
    this.top = 0;
    this.selected = -1;
    this.atStart = false;
    this.atEnd = false;
    this.prevCursor = null;
    this.nextCursor = null;
  }

  // Replaces the buffer with a fresh page (initial load or jump).
  load(rows, prev, next, top = 0) {
    this.rows = rows.slice();
    this.prevCursor = prev;
    this.nextCursor = next;
    this.atStart = prev === null;
    this.atEnd = next === null;
    this.top = Math.max(0, Math.min(top, this.rows.length - 1));
    this.selected = -1;
  }

  // Adds rows after the buffer; trims the head when over the cap.
  append(rows, next) {
    this.rows.push(...rows);
    this.nextCursor = next;
    this.atEnd = next === null || rows.length === 0;
    const excess = this.rows.length - this.maxRows;
    if (excess > 0 && this.top >= excess) {
      this.rows.splice(0, excess);
      this.top -= excess;
      if (this.selected >= 0) {
        this.selected = this.selected >= excess ? this.selected - excess : -1;
      }
      this.atStart = false;
      this.prevCursor = makeCursor(this.rows[0]);
    }
  }

  // Adds rows before the buffer (rows in ascending order); trims the tail when over the cap.
  prepend(rows, prev) {
    const n = rows.length;
    this.rows.unshift(...rows);
    this.top += n;
    if (this.selected >= 0) {
      this.selected += n;
    }
    this.prevCursor = prev;
    this.atStart = prev === null || n === 0;
    const excess = this.rows.length - this.maxRows;
    if (excess > 0 && this.top + excess < this.rows.length) {
      this.rows.length = this.maxRows;
      if (this.selected >= this.rows.length) {
        this.selected = -1;
      }
      this.atEnd = false;
      this.nextCursor = makeCursor(this.rows[this.rows.length - 1]);
    }
  }

  maxTop(visible) {
    return Math.max(0, this.rows.length - visible);
  }

  moveTop(delta, visible) {
    this.top = Math.max(0, Math.min(this.top + delta, this.maxTop(visible)));
    return this.top;
  }

  // Moves the selection by `delta` rows (clamped) and scrolls it into view.
  moveSelection(delta, visible) {
    if (this.rows.length === 0) {
      return -1;
    }
    const base = this.selected < 0 ? this.top : this.selected;
    this.selected = Math.max(0, Math.min(base + delta, this.rows.length - 1));
    if (this.selected < this.top) {
      this.top = this.selected;
    } else if (this.selected >= this.top + visible) {
      this.top = Math.min(this.selected - visible + 1, this.maxTop(visible));
    }
    return this.selected;
  }

  topRow() {
    return this.rows[this.top] || null;
  }
}

// -- Columns (UI-16) -------------------------------------------------------------------------------------

const KEEP_TAIL = /(?:\.sensitive)?\.log(?:\.\d+)?$/i; // what the middle ellipsis never cuts: .sensitive, .log, .log.N
export const ELLIPSIS = "…";
export const BADGE_CHARS = 3;                          // characters of the source cell taken by the "S" badge

// File name cut in the middle to at most `maxChars` characters, keeping the tail (".sensitive.log.0") and the start.
// maxChars not finite (width not measured, or Infinity) = no cut; 0 or less = "".
export function middleEllipsis(name, maxChars) {
  const text = name === null || name === undefined ? "" : String(name);
  if (typeof maxChars !== "number" || Number.isNaN(maxChars) || maxChars === Infinity) {
    return text;
  }
  const max = Math.floor(maxChars);
  if (max <= 0) {
    return "";
  }
  if (text.length <= max) {
    return text;
  }
  if (max === 1) {
    return ELLIPSIS;
  }
  const room = max - 1; // characters left around the ellipsis; at least one stays at the start
  const match = KEEP_TAIL.exec(text);
  const wanted = match ? match[0].length : Math.floor(room / 3);
  const tailLen = Math.min(wanted, room - 1);
  const head = text.slice(0, room - tailLen);
  const tail = tailLen > 0 ? text.slice(text.length - tailLen) : "";
  return head + ELLIPSIS + tail;
}

// True for a source written by the sensitive adapter log (<Process>_<pid>.sensitive.log[.N]).
export function isSensitiveName(name) {
  return /\.sensitive\.log(?:\.\d+)?$/i.test(String(name === null || name === undefined ? "" : name));
}

// The case covers more than one calendar day (wall-clock text, so UTC getters). Missing or non-finite bounds: false.
export function isMultiDay(range) {
  if (!range || !Number.isFinite(range.min) || !Number.isFinite(range.max)) {
    return false;
  }
  const day = 86400000;
  return Math.floor(range.min / day) !== Math.floor(range.max / day);
}

// Time text of a grid cell from the full "YYYY-MM-DD HH:MM:SS.mmm": clock only, or "MM-DD HH:MM:SS.mmm" for a case of several days.
export function shortTs(full, multiDay) {
  if (typeof full !== "string" || full.length < 23) {
    return full === null || full === undefined ? "" : String(full);
  }
  return full.slice(multiDay ? 5 : 11);
}

// Display label of a source for the current width in characters, memoized by source id: the text is cut once per
// (source, width), never per rendered row. A new width, a new name list or reset() empties the memo.
export class SourceLabels {
  constructor() {
    this.maxChars = Infinity;
    this.names = new Map();
    this.cache = new Map();
    this.computed = 0; // how many labels were really computed (the cache guard of the tests)
  }

  setNames(names) {
    this.names = names;
    this.cache.clear();
  }

  setMaxChars(value) {
    const next = typeof value === "number" && !Number.isNaN(value) ? value : Infinity;
    if (next !== this.maxChars) {
      this.maxChars = next;
      this.cache.clear();
    }
  }

  // {text, title, sensitive} of a source id; `name` is the base name, undefined when unknown.
  get(id, baseName) {
    let label = this.cache.get(id);
    if (label === undefined) {
      this.computed += 1;
      const known = baseName !== undefined;
      const full = known ? baseName : String(id);
      const sensitive = known && isSensitiveName(full);
      const reserved = sensitive ? BADGE_CHARS : 0;
      const room = this.maxChars === Infinity ? Infinity : this.maxChars - reserved;
      // The "S" badge already says "sensitive": the cell drops ".sensitive" so the name and pid stay readable in
      // 20 characters; the title keeps the full name.
      const shown = sensitive ? full.replace(/\.sensitive(?=\.log)/i, "") : full;
      label = { text: middleEllipsis(shown, room), full, sensitive };
      this.cache.set(id, label);
    }
    return label;
  }
}

// Which view change the screen really shows (UI-25). `begin(attempt)` is called for every view change and returns a
// token; `settle(token, outcome, retryable)` says what to do with the answer: "confirm" (the attempt is now the
// confirmed view), "rollback" (go back to `to`, the last confirmed view of the same case, or null when there is none;
// the attempt is kept for Retry when it is retryable) or "stale" (a newer change or another case owns the screen, so
// a late answer, success or failure, changes nothing). Pure: the caller restores the toolbar, the trail and the filter.
export function createViewTracker() {
  let seq = 0;
  let current = null;
  let confirmed = null;
  let failed = null;
  return {
    begin(attempt) {
      seq += 1;
      current = attempt;
      failed = null; // a newer view change replaces the failed one
      return seq;
    },
    settle(token, outcome, retryable = true) {
      if (token !== seq || current === null) {
        return { action: "stale" };
      }
      if (outcome === "ok") {
        confirmed = current;
        failed = null;
        return { action: "confirm" };
      }
      if (outcome === "failed") {
        failed = retryable ? current : null;
        return { action: "rollback", to: confirmed !== null && confirmed.caseId === current.caseId ? confirmed : null };
      }
      return { action: "stale" };
    },
    // The user navigated while this view change was loading: it is dropped. Returns the confirmed view to go back to
    // (null when there is none); the answers of the dropped change are stale from now on.
    abandon() {
      seq += 1;
      current = null;
      failed = null;
      return confirmed;
    },
    dropRetry() {
      failed = null;
    },
    // The attempt that did not load and may be repeated (null when nothing failed, or it cannot succeed by repeating).
    retryTarget: () => failed,
    get confirmed() {
      return confirmed;
    },
    // Another case was opened: nothing of the old one survives, and its late answers are stale.
    caseChanged() {
      seq += 1;
      current = null;
      confirmed = null;
      failed = null;
    },
  };
}

// The orchestration around the tracker (UI-25), with its effects injected so it can be tested without the page:
//   capture()            the state of the view being applied: {caseId, live, selection, stack, filter} (filter is set by
//                        the caller once composed)
//   restoreState(a)      puts the toolbar, the side selection and the trail back to attempt `a`
//   afterRollback(a)     what follows a rollback (terms, filter, timeline, chips); `a` is the view that is back
//   onConfirm()          the first page of the new view is there (the detail pane may go)
// begin() -> {attempt, token}; settle(token, outcome, retryable) applies the verdict; abandon() is the user navigating
// during a pending change; retryState() restores the attempt that failed and returns it (the caller applies the view).
export function createViewFlow({ capture, restoreState, afterRollback, onConfirm }) {
  const tracker = createViewTracker();
  return {
    tracker,
    begin() {
      const attempt = capture();
      return { attempt, token: tracker.begin(attempt) };
    },
    settle(token, outcome, retryable = true) {
      const verdict = tracker.settle(token, outcome, retryable);
      if (verdict.action === "confirm") {
        onConfirm();
      } else if (verdict.action === "rollback" && verdict.to !== null) {
        restoreState(verdict.to);
        afterRollback(verdict.to);
      }
      return verdict;
    },
    // Applies the verdict when the view change settles. `kindOf()` names the kind of the failure (read when it settles).
    // Never rejects: a change that throws is simply never confirmed.
    track(token, loaded, kindOf, retryableOf) {
      return loaded.then((outcome) => {
        const retryable = typeof retryableOf === "function" ? retryableOf(kindOf()) : true;
        return this.settle(token, outcome, retryable);
      }, () => ({ action: "stale" }));
    },
    abandon() {
      const to = tracker.abandon();
      if (to !== null) {
        restoreState(to);
        afterRollback(to);
      }
      return to;
    },
    retryState(caseId) {
      const failed = tracker.retryTarget();
      if (failed === null || failed.caseId !== caseId) {
        return null;
      }
      restoreState(failed);
      return failed;
    },
    retryTarget: () => tracker.retryTarget(),
    // The view change that failed is no longer what Retry repeats (something else failed or worked since).
    dropRetry() {
      tracker.dropRetry();
    },
    caseChanged: () => tracker.caseChanged(),
  };
}

// createViewFlow with the page's pieces injected (UI-25), so the decisions live here and main.js only connects:
//   getCase(), captureLive(), restoreLive(live), getSelection() -> {ids, groups}, setSelection({ids, groups}),
//   getStack() -> a views stack (checkpoint/rollback) or null, refreshDerived() (the panels fed by the selection:
//   Operations, Dashboard, the clock banner), afterRollback(to), onConfirm().
// Restoring a view restores its selection AND tells the derived panels, in a rollback and in a Retry alike.
export function createViewController(deps) {
  return createViewFlow({
    capture: () => {
      const stack = deps.getStack();
      return {
        caseId: deps.getCase(),
        live: deps.captureLive(),
        selection: deps.getSelection(),
        stack: stack ? stack.checkpoint() : null,
        filter: null,
      };
    },
    restoreState: (to) => {
      deps.restoreLive(to.live);
      deps.setSelection(to.selection);
      const stack = deps.getStack();
      if (stack && to.stack) {
        stack.rollback(to.stack);
      }
      deps.refreshDerived();
    },
    afterRollback: deps.afterRollback,
    onConfirm: deps.onConfirm,
  });
}
