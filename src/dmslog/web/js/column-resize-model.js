// Pure model of the resizable grid columns (no DOM): limits, keyboard steps, fit-to-content and the
// per-user persistence of the widths. The DOM layer lives in column-resize.js.

export const MIN_WIDTH = 40;      // px
export const MAX_WIDTH = 1200;    // px
export const FIT_MAX_WIDTH = 800; // px, ceiling of "fit to content"
export const FIT_SLACK = 4;       // px added to the measured content (subpixel rounding)
export const STEP = 10;           // px per arrow key
export const BIG_STEP = 50;       // px with Shift
export const STORAGE_KEY = "dmslog.columnWidths";
const MAX_STORED = 200;           // entries kept in storage (dynamic field columns come and go)

const DEFAULT_WIDTHS = { ts: 190, pid: 72, tid: 72, level: 84, template: 150, source: 160, msg: 300 };
const FIELD_DEFAULT = 140;

// Width of a column when the user never resized it; `key` is the column key ("fields.<name>" for parsed fields).
export function defaultWidth(key) {
  return Object.prototype.hasOwnProperty.call(DEFAULT_WIDTHS, key) ? DEFAULT_WIDTHS[key] : FIELD_DEFAULT;
}

export function clampWidth(value, min = MIN_WIDTH, max = MAX_WIDTH) {
  const n = Number(value);
  if (!Number.isFinite(n)) {
    return min;
  }
  return Math.min(max, Math.max(min, Math.round(n)));
}

// Width that shows the widest content (`contentWidths`: scrollWidth of the header and of the rendered cells).
export function fitWidth(contentWidths) {
  let widest = 0;
  for (const w of contentWidths) {
    if (Number.isFinite(w) && w > widest) {
      widest = w;
    }
  }
  return clampWidth(Math.ceil(widest) + FIT_SLACK, MIN_WIDTH, FIT_MAX_WIDTH);
}

// What a key on the focused separator does: {delta} | {fit: true} | {to: "min"|"max"} | null.
export function keyAction(key, shiftKey) {
  const step = shiftKey ? BIG_STEP : STEP;
  switch (key) {
    case "ArrowRight":
      return { delta: step };
    case "ArrowLeft":
      return { delta: -step };
    case "Enter":
      return { fit: true };
    case "Home":
      return { to: "min" };
    case "End":
      return { to: "max" };
    default:
      return null;
  }
}

function defaultStorage() {
  try {
    return globalThis.localStorage || null;
  } catch (err) {
    return null; // blocked storage may throw on access
  }
}

// Widths by column key, with the persistence. Storage failures are swallowed: the widths then live
// for the session only.
export class ColumnWidths {
  constructor(storage) {
    this.storage = storage === undefined ? defaultStorage() : storage;
    this.widths = new Map();
    this.load();
  }

  load() {
    this.widths = new Map();
    try {
      const stored = JSON.parse(this.storage.getItem(STORAGE_KEY));
      if (stored && typeof stored === "object" && !Array.isArray(stored)) {
        for (const [key, value] of Object.entries(stored).slice(0, MAX_STORED)) {
          if (typeof value === "number" && Number.isFinite(value)) {
            this.widths.set(key, clampWidth(value));
          }
        }
      }
    } catch (err) {
      // Missing, blocked or corrupt storage: defaults.
    }
  }

  save() {
    try {
      const entries = Array.from(this.widths.entries()).slice(-MAX_STORED);
      this.storage.setItem(STORAGE_KEY, JSON.stringify(Object.fromEntries(entries)));
    } catch (err) {
      // Not persisted; the view still works.
    }
  }

  get(key) {
    return this.widths.has(key) ? this.widths.get(key) : defaultWidth(key);
  }

  // Sets (clamped) and returns the width; `persist` false while dragging.
  set(key, width, persist = true) {
    const value = clampWidth(width);
    this.widths.delete(key); // newest last, so the cap drops the oldest
    this.widths.set(key, value);
    if (persist) {
      this.save();
    }
    return value;
  }

  adjust(key, delta) {
    return this.set(key, this.get(key) + delta);
  }

  reset() {
    this.widths = new Map();
    try {
      this.storage.removeItem(STORAGE_KEY);
    } catch (err) {
      // Nothing to remove or blocked.
    }
  }

  total(keys) {
    return keys.reduce((sum, key) => sum + this.get(key), 0);
  }
}
