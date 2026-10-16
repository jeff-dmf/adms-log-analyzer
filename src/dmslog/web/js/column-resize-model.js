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

// Default widths in characters (`ch`) of the grid's monospace font (UI-16): the width of a character changes between
// Windows and Linux, the text of the column does not. Widths the user dragged are stored in px and win over these.
export const DEFAULT_CH = { ts: 12, pid: 6, tid: 6, level: 9, source: 20, collection: 5 };
export const TS_CH_MULTIDAY = 18;  // "MM-DD HH:MM:SS.mmm", used when the case covers more than one day
export const SOURCE_CH_MULTIDAY = 16; // the wider time column is paid by Source (its middle ellipsis keeps the tail)
// Layout of the page at the acceptance size (css/tokens.css, css/app.css): sources panel, detail panel, grid panel
// padding (2 x --space-3) and border, viewport border (2 px) and the vertical scrollbar.
export const LAYOUT_PX = { side: 248, detail: 380, panelChrome: 17, viewportBorder: 2, scrollbar: 15 };
export const NOMINAL_CHAR_PX = 7.2; // 12px mono; the real value is measured by the DOM layer
export const CELL_PAD_PX = 8;       // 2 x --cell-pad
const DEFAULT_PX = { msg: 300 };
const FIELD_DEFAULT = 140;

// Width of a column when the user never resized it; `key` is the column key ("fields.<name>" for parsed fields).
// `charPx`: width of one character (measured once per font or column change); not a positive number = nominal.
// `chOverride`: Map of key -> ch that replaces the table (the time column of a multi-day case).
export function defaultWidth(key, charPx = NOMINAL_CHAR_PX, chOverride = null) {
  const px = Number.isFinite(charPx) && charPx > 0 ? charPx : NOMINAL_CHAR_PX;
  const ch = chOverride && chOverride.has(key) ? chOverride.get(key) : DEFAULT_CH[key];
  if (typeof ch === "number" && Number.isFinite(ch)) {
    return Math.ceil(ch * px) + CELL_PAD_PX + 1; // ceil + 1px: the text of `ch` characters must never be cut
  }
  return Object.prototype.hasOwnProperty.call(DEFAULT_PX, key) ? DEFAULT_PX[key] : FIELD_DEFAULT;
}

// Characters that fit in the Message column (the flexible one) for a page `viewportWidth` px wide with the default
// widths of `keys` (all but "msg"). Not a positive finite number for the page or the character = 0.
export function messageChars(viewportWidth, keys, { charPx = NOMINAL_CHAR_PX, multiDay = false } = {}) {
  if (!Number.isFinite(viewportWidth) || viewportWidth <= 0) {
    return 0;
  }
  const chOverride = new Map();
  if (multiDay) {
    chOverride.set("ts", TS_CH_MULTIDAY);
    chOverride.set("source", SOURCE_CH_MULTIDAY);
  }
  const px = Number.isFinite(charPx) && charPx > 0 ? charPx : NOMINAL_CHAR_PX;
  const { side, detail, panelChrome, viewportBorder, scrollbar } = LAYOUT_PX;
  const room = viewportWidth - side - detail - panelChrome - viewportBorder - scrollbar;
  const fixed = keys.filter((k) => k !== "msg").reduce((sum, k) => sum + defaultWidth(k, px, chOverride), 0);
  return Math.max(0, Math.floor((room - fixed - CELL_PAD_PX) / px));
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
    this.charPx = NOMINAL_CHAR_PX;
    this.chOverride = new Map();
    this.load();
  }

  // Width of one character of the grid font; a failed measurement (0, NaN, null) keeps the previous value.
  setCharPx(value) {
    if (typeof value === "number" && Number.isFinite(value) && value > 0) {
      this.charPx = value;
      return true;
    }
    return false;
  }

  // Default width in ch for one column (null removes the override).
  setDefaultCh(key, ch) {
    if (ch === null || ch === undefined) {
      this.chOverride.delete(key);
    } else {
      this.chOverride.set(key, ch);
    }
  }

  isCustom(key) {
    return this.widths.has(key);
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
    return this.widths.has(key) ? this.widths.get(key) : defaultWidth(key, this.charPx, this.chOverride);
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
