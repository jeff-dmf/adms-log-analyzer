// Virtual log grid: a pooled set of row elements over a keyset-paged RowWindow. The scrollbar
// maps onto the time range of the chosen sources; wheel and keys move by rows.
import { api } from "./api.js";
import {
  END_CURSOR, PAGE_LIMIT, ROW_HEIGHT, SCROLL_SPAN, RowWindow, fetchPlan, jumpCursor, scrollToTs,
  tsToScroll,
} from "./grid-model.js";
import { renderHighlighted } from "./search.js";
import { S, format } from "./strings.js";
import { formatTs } from "./time.js";

export const COLUMNS = [
  { key: "ts", label: () => S.columnTs },
  { key: "pid", label: () => S.columnPid },
  { key: "tid", label: () => S.columnTid },
  { key: "level", label: () => S.columnLevel },
  { key: "source", label: () => S.columnSource },
  { key: "msg", label: () => S.columnMessage },
];

export class Grid {
  constructor({ viewport, rowsLayer, spacer, header, onSelect, onStatus, onError }) {
    this.viewport = viewport;
    this.rowsLayer = rowsLayer;
    this.spacer = spacer;
    this.header = header;
    this.onSelect = onSelect;
    this.onStatus = onStatus;
    this.onError = onError;
    this.window = new RowWindow();
    this.visibleColumns = COLUMNS.map((c) => c.key);
    this.sourceNames = new Map();
    this.terms = [];
    this.caseId = null;
    this.filter = null;
    this.range = null;
    this.generation = 0;
    this.loading = false;
    this.controller = null;
    this.pool = [];
    this.visible = 0;
    this.lastScroll = 0;
    this.pendingJump = null;
    this.selectTimer = null;
    this.wheelRemainder = 0;
    this.spacer.style.height = SCROLL_SPAN + "px";
    this.bind();
    this.renderHeader();
  }

  bind() {
    this.viewport.addEventListener("wheel", (event) => {
      event.preventDefault();
      const unit = event.deltaMode === 1 ? 1 : ROW_HEIGHT;
      this.wheelRemainder += event.deltaY / unit * (event.deltaMode === 1 ? 3 : 1);
      const rows = Math.trunc(this.wheelRemainder);
      if (rows !== 0) {
        this.wheelRemainder -= rows;
        this.scrollRows(rows);
      }
    }, { passive: false });
    this.viewport.addEventListener("scroll", () => this.onScrollBar());
    this.viewport.addEventListener("keydown", (event) => this.onKey(event));
    this.rowsLayer.addEventListener("click", (event) => {
      const rowEl = event.target.closest(".grid-row");
      if (rowEl && rowEl.dataset.index !== undefined) {
        this.window.selected = Number(rowEl.dataset.index);
        this.render();
        this.notifySelect(0);
        this.viewport.focus();
      }
    });
    if (typeof ResizeObserver === "function") {
      new ResizeObserver(() => this.render()).observe(this.viewport);
    }
  }

  // -- configuration ------------------------------------------------------------------------

  setColumns(keys) {
    this.visibleColumns = COLUMNS.map((c) => c.key).filter((k) => keys.includes(k));
    this.pool = [];
    this.rowsLayer.textContent = "";
    this.renderHeader();
    this.render();
  }

  setSourceNames(map) {
    this.sourceNames = map;
    this.render();
  }

  setTerms(terms) {
    this.terms = terms;
    this.render();
  }

  renderHeader() {
    this.header.textContent = "";
    for (const column of COLUMNS) {
      if (!this.visibleColumns.includes(column.key)) {
        continue;
      }
      const cell = document.createElement("div");
      cell.className = "grid-cell col-" + column.key;
      cell.setAttribute("role", "columnheader");
      cell.textContent = column.label();
      this.header.appendChild(cell);
    }
  }

  // -- loading ------------------------------------------------------------------------------

  async setView({ caseId, filter, range }) {
    this.caseId = caseId;
    this.filter = filter;
    this.range = range;
    await this.reload();
  }

  cancel() {
    this.generation += 1;
    this.loading = false;
    if (this.controller) {
      this.controller.abort();
      this.controller = null;
    }
  }

  async reload() {
    this.cancel();
    this.window.reset();
    this.render();
    if (this.caseId === null) {
      this.onStatus("");
      return;
    }
    this.refreshCount();
    await this.jump(null, "fwd");
  }

  async refreshCount() {
    const generation = this.generation;
    try {
      const result = await api.count(this.caseId, this.filter);
      if (generation === this.generation) {
        const template = result.exact ? S.gridCount : S.gridCountApprox;
        this.countText = format(template, { count: Number(result.count).toLocaleString("en-US") });
        this.updateStatus();
      }
    } catch (err) {
      if (generation === this.generation) {
        this.countText = "";
        this.onError(err);
      }
    }
  }

  updateStatus() {
    const row = this.window.topRow();
    const parts = [];
    if (this.countText) {
      parts.push(this.countText);
    }
    if (row) {
      parts.push(format(S.gridPosition, { time: formatTs(row.ts) }));
    }
    this.onStatus(parts.join(" - "));
  }

  // Replaces the window with the page found at `cursor` (null = first page).
  async jump(cursor, dir) {
    this.cancel();
    const generation = this.generation;
    this.controller = new AbortController();
    this.loading = true;
    this.onStatus(S.loading);
    try {
      const page = await api.page(this.caseId, this.filter, cursor, dir, PAGE_LIMIT, this.controller.signal);
      if (generation !== this.generation) {
        return;
      }
      this.window.load(page.rows, page.prev, page.next, 0);
      this.ensurePool();
      if (dir === "back") {
        this.window.top = this.window.maxTop(this.visible);
      }
    } catch (err) {
      if (generation === this.generation && !(err && err.name === "AbortError")) {
        this.onError(err);
      }
      return;
    } finally {
      if (generation === this.generation) {
        this.loading = false;
        this.pendingJump = null;
      }
    }
    this.render();
    this.syncScroll();
    this.pump();
  }

  // Fetches more rows while the viewport is close to either end of the buffer.
  async pump() {
    const generation = this.generation;
    while (generation === this.generation && !this.loading) {
      const w = this.window;
      const plan = fetchPlan(w.top, this.visible, w.rows.length, w.atStart, w.atEnd);
      if (plan === null) {
        return;
      }
      this.loading = true;
      this.controller = new AbortController();
      const cursor = plan === "fwd" ? w.nextCursor : w.prevCursor;
      try {
        const page = await api.page(this.caseId, this.filter, cursor, plan, PAGE_LIMIT, this.controller.signal);
        if (generation !== this.generation) {
          return;
        }
        if (plan === "fwd") {
          w.append(page.rows, page.next);
        } else {
          w.prepend(page.rows, page.prev);
        }
      } catch (err) {
        if (generation === this.generation && !(err && err.name === "AbortError")) {
          this.onError(err);
        }
        return;
      } finally {
        if (generation === this.generation) {
          this.loading = false;
        }
      }
      this.render();
    }
  }

  jumpToTime(ms) {
    return this.jump(jumpCursor(ms), "fwd");
  }

  goHome() {
    return this.jump(null, "fwd");
  }

  goEnd() {
    return this.jump(END_CURSOR, "back");
  }

  // -- scrolling ----------------------------------------------------------------------------

  scrollRows(delta) {
    this.window.moveTop(delta, this.visible);
    this.render();
    this.syncScroll();
    this.pump();
  }

  syncScroll() {
    const row = this.window.topRow();
    if (!row || this.pendingJump !== null) {
      return;
    }
    this.viewport.scrollTop = tsToScroll(row.ts, this.range);
    this.lastScroll = this.viewport.scrollTop;
  }

  onScrollBar() {
    const position = this.viewport.scrollTop;
    if (Math.abs(position - this.lastScroll) < 3 || this.caseId === null) {
      return;
    }
    this.lastScroll = position;
    if (this.pendingJump !== null) {
      clearTimeout(this.pendingJump);
    }
    this.pendingJump = setTimeout(() => {
      this.pendingJump = null;
      const fraction = position / SCROLL_SPAN;
      if (fraction >= 0.995) {
        this.goEnd();
      } else if (fraction <= 0.005 || !this.range) {
        this.goHome();
      } else {
        this.jumpToTime(scrollToTs(position, this.range));
      }
    }, 80);
  }

  onKey(event) {
    const page = Math.max(1, this.visible - 1);
    const moves = { ArrowDown: 1, ArrowUp: -1, PageDown: page, PageUp: -page };
    if (Object.prototype.hasOwnProperty.call(moves, event.key)) {
      event.preventDefault();
      this.window.moveSelection(moves[event.key], this.visible);
      this.render();
      this.syncScroll();
      this.notifySelect(120);
      this.pump();
    } else if (event.key === "Home") {
      event.preventDefault();
      this.goHome();
    } else if (event.key === "End") {
      event.preventDefault();
      this.goEnd();
    } else if (event.key === "Enter") {
      event.preventDefault();
      this.notifySelect(0);
    }
  }

  notifySelect(delay) {
    clearTimeout(this.selectTimer);
    this.selectTimer = setTimeout(() => {
      const row = this.window.rows[this.window.selected];
      if (row) {
        this.onSelect(row);
      }
    }, delay);
  }

  // -- rendering ----------------------------------------------------------------------------

  ensurePool() {
    const height = this.viewport.clientHeight || 400;
    this.visible = Math.max(1, Math.ceil(height / ROW_HEIGHT) + 1);
    while (this.pool.length < this.visible) {
      this.pool.push(this.createRow());
    }
  }

  createRow() {
    const rowEl = document.createElement("div");
    rowEl.className = "grid-row";
    rowEl.setAttribute("role", "row");
    rowEl.style.height = ROW_HEIGHT + "px";
    const cells = {};
    for (const key of this.visibleColumns) {
      const cell = document.createElement("div");
      cell.className = "grid-cell col-" + key;
      cell.setAttribute("role", "gridcell");
      rowEl.appendChild(cell);
      cells[key] = cell;
    }
    this.rowsLayer.appendChild(rowEl);
    return { el: rowEl, cells };
  }

  render() {
    this.ensurePool();
    const w = this.window;
    this.rowsLayer.setAttribute("aria-rowcount", String(w.rows.length));
    for (let i = 0; i < this.pool.length; i += 1) {
      const slot = this.pool[i];
      const index = w.top + i;
      const row = w.rows[index];
      if (!row || i >= this.visible) {
        slot.el.hidden = true;
        delete slot.el.dataset.index;
        continue;
      }
      slot.el.hidden = false;
      slot.el.dataset.index = String(index);
      const selected = index === w.selected;
      slot.el.classList.toggle("selected", selected);
      slot.el.setAttribute("aria-selected", selected ? "true" : "false");
      slot.el.setAttribute("aria-label", format(S.rowLabel, { id: row.id }));
      slot.el.dataset.sev = String(row.sev);
      this.fillCells(slot, row);
    }
    this.updateStatus();
  }

  fillCells(slot, row) {
    const { cells } = slot;
    if (cells.ts) {
      cells.ts.textContent = formatTs(row.ts);
    }
    if (cells.pid) {
      cells.pid.textContent = row.pid === null || row.pid === undefined ? "" : String(row.pid);
    }
    if (cells.tid) {
      cells.tid.textContent = row.tid === null || row.tid === undefined ? "" : "0x" + Number(row.tid).toString(16);
    }
    if (cells.level) {
      cells.level.textContent = S.levelNames[row.level] || "";
    }
    if (cells.source) {
      cells.source.textContent = this.sourceNames.get(row.source_id) || String(row.source_id);
    }
    if (cells.msg) {
      renderHighlighted(cells.msg, row.msg === null || row.msg === undefined ? "" : String(row.msg), this.terms);
      if (row.nlines > 1) {
        const extra = document.createElement("span");
        extra.className = "extra-lines";
        extra.textContent = format(S.extraLines, { count: row.nlines - 1 });
        cells.msg.appendChild(extra);
      }
    }
  }
}
