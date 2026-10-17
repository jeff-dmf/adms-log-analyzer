// DOM side of the resizable grid columns: the separator in each header cell (mouse, touch and
// keyboard), the widths applied to header and pooled rows, and the horizontal scroll sync.
import { ColumnWidths, MAX_WIDTH, MIN_WIDTH, fitWidth, keyAction } from "./column-resize-model.js";
import { S, format } from "./strings.js";

export class ColumnResizer {
  // `getCells(key)`: the rendered body cells of a column; `getKeys()`: the visible column keys in order.
  constructor({
    viewport, rowsLayer, header, getCells, getVisibleCells, getKeys, storage, onLayout, prepareFit, measureChar,
  }) {
    this.viewport = viewport;
    this.rowsLayer = rowsLayer;
    this.header = header;
    this.getCells = getCells;
    this.getVisibleCells = getVisibleCells || getCells;
    this.getKeys = getKeys;
    this.model = new ColumnWidths(storage);
    this.onLayout = onLayout || (() => {});       // () after the widths were applied (the grid recomputes its cut names)
    this.prepareFit = prepareFit || (() => null);  // (key) -> restore function | null: shows untruncated text while fitting
    this.measureProbe = measureChar || (() => probeCharWidth(rowsLayer));
    this.headerCells = new Map();
    // Layout facts for the scroll path, measured by measureLayout() when the layout changes (apply(), the viewport's
    // ResizeObserver) and never on a scroll event: a wheel burst must cost no layout read (UI-27).
    this.scrollX = 0;            // horizontal scroll position, kept in memory (scrollTo() writes it)
    this.maxScrollX = Infinity;  // furthest scrollLeft; Infinity while nothing was measured
    this.pageWidth = 0;          // client width of the viewport (a "page" of the horizontal wheel)
    this.destroyed = false;
    this.listeners = new AbortController(); // every listener of the separators; destroy() removes them all
  }

  // Stops the resizer: its listeners are removed and every entry point (pointer, keys, double click, setWidth, fit,
  // reset, apply) does nothing afterwards, so no measurement or stored preference follows a dropped grid.
  destroy() {
    this.destroyed = true;
    this.listeners.abort();
    this.headerCells.clear();
  }

  // The body was scrolled sideways by the browser (not by scrollTo()): the header and the kept position follow.
  adoptScrollLeft(x) {
    if (this.destroyed || typeof x !== "number" || Math.abs(x - this.scrollX) < 1) {
      return;
    }
    this.scrollX = x;
    this.header.scrollLeft = x;
  }

  // Measures the width of one character of the grid font and applies it to the default widths. Called once at the start
  // and when the columns or the font change, never per row and never on scroll. A failed measurement (0, NaN, null: the
  // grid is hidden or not laid out yet) keeps the previous value. Returns true when the value changed.
  measure() {
    const before = this.model.charPx;
    let value = null;
    try {
      value = this.measureProbe();
    } catch (err) {
      value = null;
    }
    this.model.setCharPx(value);
    return this.model.charPx !== before;
  }

  // Default width in ch of one column (the time column of a multi-day case); re-applies the widths.
  setDefaultCh(key, ch, apply = true) {
    this.model.setDefaultCh(key, ch);
    if (apply) {
      this.apply();
    }
  }

  // Called when the header is rebuilt.
  resetHeader() {
    this.headerCells.clear();
  }

  // Adds the separator to a header cell (called by Grid.renderHeader after creating the cell).
  decorate(cell, column) {
    this.headerCells.set(column.key, cell);
    if (typeof cell.appendChild !== "function") {
      return;
    }
    cell.style.width = this.model.get(column.key) + "px";
    const handle = document.createElement("div");
    handle.className = "col-resizer";
    handle.setAttribute("role", "separator");
    handle.setAttribute("aria-orientation", "vertical");
    handle.setAttribute("tabindex", "0");
    handle.setAttribute("aria-label", format(S.columnResizeLabel, { name: column.label() }));
    handle.setAttribute("aria-valuemin", String(MIN_WIDTH));
    handle.setAttribute("aria-valuemax", String(MAX_WIDTH));
    handle.setAttribute("aria-valuenow", String(this.model.get(column.key)));
    this.bindHandle(handle, column.key);
    cell.appendChild(handle);
  }

  bindHandle(handle, key) {
    const signal = this.listeners.signal;
    handle.addEventListener("pointerdown", (event) => {
      if (this.destroyed) {
        return;
      }
      if (event.button !== undefined && event.button !== 0) {
        return;
      }
      event.preventDefault();
      event.stopPropagation();
      const startX = event.clientX;
      const startWidth = this.model.get(key);
      handle.setPointerCapture(event.pointerId);
      handle.classList.add("dragging");
      const move = (e) => this.setWidth(key, startWidth + e.clientX - startX, false);
      const end = () => {
        if (this.destroyed) {
          return;
        }
        handle.removeEventListener("pointermove", move);
        handle.removeEventListener("pointerup", end);
        handle.removeEventListener("pointercancel", end);
        handle.classList.remove("dragging");
        this.model.save();
      };
      handle.addEventListener("pointermove", move, { signal });
      handle.addEventListener("pointerup", end, { signal });
      handle.addEventListener("pointercancel", end, { signal });
    }, { signal });
    handle.addEventListener("click", (event) => event.stopPropagation(), { signal });
    handle.addEventListener("dblclick", (event) => {
      if (this.destroyed) {
        return;
      }
      event.preventDefault();
      event.stopPropagation();
      this.fit(key);
    });
    handle.addEventListener("keydown", (event) => {
      if (this.destroyed) {
        return;
      }
      const action = keyAction(event.key, event.shiftKey);
      if (!action) {
        return;
      }
      event.preventDefault();
      event.stopPropagation();
      if (action.fit) {
        this.fit(key);
      } else if (action.to) {
        this.setWidth(key, action.to === "min" ? MIN_WIDTH : MAX_WIDTH, true);
      } else {
        this.setWidth(key, this.model.get(key) + action.delta, true);
      }
    }, { signal });
  }

  setWidth(key, width, persist) {
    if (this.destroyed) {
      return;
    }
    this.model.set(key, width, persist);
    this.apply();
  }

  // Fits the column to the widest of its header and the rendered cells, measured without the applied
  // width and without flex-grow (see intrinsicWidths).
  fit(key) {
    if (this.destroyed) {
      return;
    }
    const restore = this.prepareFit(key);
    try {
      const cells = [...this.getVisibleCells(key)];
      const head = this.headerCells.get(key);
      if (head) {
        cells.push(head);
      }
      this.setWidth(key, fitWidth(intrinsicWidths(cells)), true);
    } finally {
      if (typeof restore === "function") {
        restore();
      }
    }
  }

  reset() {
    if (this.destroyed) {
      return;
    }
    this.model.reset();
    this.apply();
  }

  // Applies the widths to the header, the pooled rows and the layer that sets the horizontal scroll.
  apply() {
    if (this.destroyed) {
      return;
    }
    const keys = this.getKeys();
    for (const key of keys) {
      const width = this.model.get(key) + "px";
      const head = this.headerCells.get(key);
      if (head) {
        head.style.width = width;
        const handle = head.lastChild;
        if (handle && typeof handle.setAttribute === "function") {
          handle.setAttribute("aria-valuenow", String(this.model.get(key)));
        }
      }
      for (const cell of this.getCells(key)) {
        cell.style.width = width;
      }
    }
    this.rowsLayer.style.minWidth = this.model.total(keys) + "px";
    this.header.style.minWidth = "";
    this.measureLayout();
    this.onLayout();
  }

  // Width of one column for a freshly created body cell.
  widthOf(key) {
    return this.model.get(key) + "px";
  }

  // The only geometry reads of the resizer, made when the layout changes (never on a scroll event). The header's right
  // padding matches the body's vertical scrollbar; the header follows the body horizontally.
  measureLayout() {
    if (this.destroyed) {
      return;
    }
    const v = this.viewport;
    if (typeof v.offsetWidth === "number" && typeof v.clientWidth === "number") {
      const bar = Math.max(0, v.offsetWidth - v.clientWidth - 2 * (v.clientLeft || 0));
      this.header.style.paddingRight = bar + "px";
      this.pageWidth = v.clientWidth;
    }
    if (typeof v.scrollWidth === "number" && typeof v.clientWidth === "number") {
      this.maxScrollX = Math.max(0, v.scrollWidth - v.clientWidth);
    }
    if (typeof v.scrollLeft === "number") {
      this.scrollX = v.scrollLeft;
      this.header.scrollLeft = this.scrollX;
    }
  }

  // Moves the body and the header to `x` (clamped to the measured range) with writes only.
  scrollTo(x) {
    const next = Math.min(Math.max(0, x), this.maxScrollX);
    this.scrollX = next;
    this.viewport.scrollLeft = next;
    this.header.scrollLeft = next;
  }
}

// Content width of each cell with its applied width and flex-grow removed. Batched (set all, read all,
// restore all) so the browser lays out once.
export function intrinsicWidths(cells) {
  const saved = cells.map((c) => [c.style.width, c.style.flex]);
  try {
    for (const c of cells) {
      c.style.width = "auto";
      c.style.flex = "none";
    }
    return cells.map((c) => Math.max(c.scrollWidth || 0, c.offsetWidth || 0));
  } finally {
    cells.forEach((c, i) => {
      c.style.width = saved[i][0];
      c.style.flex = saved[i][1];
    });
  }
}

// Width of one character of the grid font, measured on a hidden row (so the grid's own font rules apply). null when the
// grid is not laid out (zero width) or the DOM is not available.
export function probeCharWidth(rowsLayer) {
  if (typeof document === "undefined" || !rowsLayer || typeof rowsLayer.appendChild !== "function") {
    return null;
  }
  const probe = document.createElement("div");
  probe.className = "grid-row grid-probe";
  probe.setAttribute("aria-hidden", "true");
  const text = "0".repeat(PROBE_CHARS);
  const span = document.createElement("span");
  if (typeof span.getBoundingClientRect !== "function" || typeof probe.remove !== "function") {
    return null; // not a real layout engine
  }
  span.textContent = text;
  probe.appendChild(span);
  rowsLayer.appendChild(probe);
  try {
    const width = span.getBoundingClientRect().width;
    return Number.isFinite(width) && width > 0 ? width / PROBE_CHARS : null;
  } finally {
    probe.remove();
  }
}
const PROBE_CHARS = 40;
