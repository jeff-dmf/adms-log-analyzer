// DOM side of the resizable grid columns: the separator in each header cell (mouse, touch and
// keyboard), the widths applied to header and pooled rows, and the horizontal scroll sync.
import { ColumnWidths, MAX_WIDTH, MIN_WIDTH, fitWidth, keyAction } from "./column-resize-model.js";
import { S, format } from "./strings.js";

export class ColumnResizer {
  // `getCells(key)`: the rendered body cells of a column; `getKeys()`: the visible column keys in order.
  constructor({ viewport, rowsLayer, header, getCells, getKeys, storage }) {
    this.viewport = viewport;
    this.rowsLayer = rowsLayer;
    this.header = header;
    this.getCells = getCells;
    this.getKeys = getKeys;
    this.model = new ColumnWidths(storage);
    this.headerCells = new Map();
    if (typeof viewport.addEventListener === "function") {
      viewport.addEventListener("scroll", () => this.syncScroll());
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
    handle.addEventListener("pointerdown", (event) => {
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
        handle.removeEventListener("pointermove", move);
        handle.removeEventListener("pointerup", end);
        handle.removeEventListener("pointercancel", end);
        handle.classList.remove("dragging");
        this.model.save();
      };
      handle.addEventListener("pointermove", move);
      handle.addEventListener("pointerup", end);
      handle.addEventListener("pointercancel", end);
    });
    handle.addEventListener("click", (event) => event.stopPropagation());
    handle.addEventListener("dblclick", (event) => {
      event.preventDefault();
      event.stopPropagation();
      this.fit(key);
    });
    handle.addEventListener("keydown", (event) => {
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
    });
  }

  setWidth(key, width, persist) {
    this.model.set(key, width, persist);
    this.apply();
  }

  // Fits the column to the widest of its header and the rendered cells (scrollWidth ignores the truncation).
  fit(key) {
    const widths = [];
    const head = this.headerCells.get(key);
    if (head) {
      widths.push(head.scrollWidth);
    }
    for (const cell of this.getCells(key)) {
      widths.push(cell.scrollWidth);
    }
    this.setWidth(key, fitWidth(widths), true);
  }

  reset() {
    this.model.reset();
    this.apply();
  }

  // Applies the widths to the header, the pooled rows and the layer that sets the horizontal scroll.
  apply() {
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
    this.syncScroll();
  }

  // Width of one column for a freshly created body cell.
  widthOf(key) {
    return this.model.get(key) + "px";
  }

  // The header follows the body horizontally; its right padding matches the body's vertical scrollbar.
  syncScroll() {
    const v = this.viewport;
    if (typeof v.offsetWidth === "number" && typeof v.clientWidth === "number") {
      const bar = Math.max(0, v.offsetWidth - v.clientWidth - 2 * (v.clientLeft || 0));
      this.header.style.paddingRight = bar + "px";
    }
    if (typeof v.scrollLeft === "number") {
      this.header.scrollLeft = v.scrollLeft;
    }
  }
}
