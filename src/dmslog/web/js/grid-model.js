// Pure model of the virtual grid (no DOM): keyset cursors, the buffered row window and the
// proportional scroll mapping. The DOM layer lives in grid.js.

export const ROW_HEIGHT = 22;       // px, also used by the CSS through the row elements
export const PAGE_LIMIT = 200;      // rows per request (server maximum is 500)
export const MAX_ROWS = 1200;       // rows kept in memory before trimming the far end
export const PREFETCH_MARGIN = 60;  // fetch when fewer rows than this remain past the viewport
export const SCROLL_SPAN = 20000;   // px of scrollbar travel mapped onto the time range
export const END_CURSOR = Number.MAX_SAFE_INTEGER + ":" + Number.MAX_SAFE_INTEGER;

export function makeCursor(row) {
  return row.ts + ":" + row.id;
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
