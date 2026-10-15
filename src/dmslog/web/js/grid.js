// Virtual log grid: a pooled set of row elements over a keyset-paged RowWindow. The scrollbar
// maps onto the time range of the chosen sources; wheel and keys move by rows.
import { api } from "./api.js";
import {
  END_CURSOR, PAGE_LIMIT, ROW_HEIGHT, SCROLL_SPAN, RowWindow, fetchPlan, jumpCursor, scrollToTs,
  topAfterResize, tsToScroll,
} from "./grid-model.js";
import { ColumnResizer } from "./column-resize.js";
import { baseName } from "./source-filter-model.js";
import { markerLabel } from "./bookmarks-model.js";
import { renderHighlighted } from "./search.js";
import { S, format, pluralKey } from "./strings.js";
import { formatTs } from "./time.js";

export const COLUMNS = [
  { key: "ts", label: () => S.columnTs },
  { key: "pid", label: () => S.columnPid },
  { key: "tid", label: () => S.columnTid },
  { key: "level", label: () => S.columnLevel },
  { key: "source", label: () => S.columnSource },
  { key: "msg", label: () => S.columnMessage },
];

// Shown only for a case with more than one collection (main.js decides); never part of COLUMNS, so the
// stored column choice and the Columns menu of the fixed columns do not depend on it.
export const COLLECTION_COLUMN = { key: "collection", label: () => S.columnCollection };

function cellClass(column) {
  return column.field === undefined ? "col-" + column.key : "col-field";
}

function fieldText(value) {
  if (value === null || value === undefined) {
    return "";
  }
  return typeof value === "object" ? JSON.stringify(value) : String(value);
}

export class Grid {
  constructor({
    viewport, rowsLayer, spacer, header, onSelect, onCount, onPosition, onError, getMark, onVisible, onView,
    getHighlight,
  }) {
    this.viewport = viewport;
    this.rowsLayer = rowsLayer;
    this.spacer = spacer;
    this.header = header;
    this.onSelect = onSelect;
    this.onCount = onCount || (() => {}); // ({n, exact} | "loading" | null, {announce}) -> status bar (UI-13)
    this.onPosition = onPosition || (() => {}); // (ts | null): first visible record's time; never announced
    this.onError = onError;
    this.getMark = getMark || (() => null); // bookmark of a record id: {color} | null
    this.getHighlight = getHighlight || (() => null); // highlight rule of a record id: {color, label} | null
    this.onVisible = onVisible || (() => {}); // ids of the rendered rows (bookmark lookup)
    this.onView = onView || (() => {});
    this.countGeneration = 0;
    this.countController = null;
    this.total = null; // last entry count of the filter (a number), the baseline of "follow tail"
    this.fieldColumns = [];
    this.window = new RowWindow();
    this.visibleColumns = COLUMNS.map((c) => c.key);
    this.sourceNames = new Map();
    this.collectionColumn = false; // the Collection column (C<n>) is shown
    this.collectionTags = new Map(); // collection group -> {tag, color}
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
    this.knownHeight = 0; // viewport height at the last resize (ensureSelectedVisible)
    this.spacer.style.height = SCROLL_SPAN + "px";
    this.resizer = new ColumnResizer({
      viewport, rowsLayer, header,
      // Every pooled slot, hidden ones included: a hidden row keeps the current widths when render() shows it again.
      getCells: (key) => this.pool.filter((s) => s.cells[key]).map((s) => s.cells[key]),
      getVisibleCells: (key) => this.pool.filter((s) => !s.el.hidden && s.cells[key]).map((s) => s.cells[key]),
      getKeys: () => this.columnDefs().map((c) => c.key),
    });
    this.bind();
    this.renderHeader();
  }

  bind() {
    this.viewport.addEventListener("wheel", (event) => {
      event.preventDefault();
      if (event.shiftKey || Math.abs(event.deltaX) > Math.abs(event.deltaY)) {
        this.viewport.scrollLeft += event.deltaX || event.deltaY; // horizontal scroll of wide columns
        return;
      }
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
      // The grid owns the rule "the selected row stays visible" (docs/notas/ui-contratos.md, 2.5): a dock opening,
      // dragging or maximizing, a drawer or a breakpoint all arrive here as a size change of the viewport.
      new ResizeObserver((entries) => {
        const last = entries && entries.length ? entries[entries.length - 1] : null;
        this.ensureSelectedVisible(last && last.contentRect ? last.contentRect.height : this.viewport.clientHeight);
      }).observe(this.viewport);
    }
  }

  // Keeps the selected row visible after the viewport changed height. Public and idempotent. Height 0 (the dock is
  // maximized and hides the grid) recalculates nothing and leaves `top` alone; the same height twice does nothing.
  ensureSelectedVisible(height = this.viewport.clientHeight) {
    if (!(height > 0)) {
      this.render();
      return;
    }
    const before = this.knownHeight;
    this.knownHeight = height;
    if (before > 0 && before !== height) {
      const w = this.window;
      const top = topAfterResize({
        selected: w.selected,
        top: w.top,
        visibleBefore: Math.max(1, Math.floor(before / ROW_HEIGHT)),
        visibleAfter: Math.max(1, Math.floor(height / ROW_HEIGHT)),
        length: w.rows.length,
      });
      if (top !== w.top) {
        w.top = top;
        this.render();
        this.syncScroll();
        this.pump();
        return;
      }
    }
    this.render();
  }

  // -- configuration ------------------------------------------------------------------------

  setColumns(keys) {
    this.visibleColumns = COLUMNS.map((c) => c.key).filter((k) => keys.includes(k));
    this.pool = [];
    this.rowsLayer.textContent = "";
    this.renderHeader();
    this.render();
  }

  // Dynamic columns from parsed fields: names without the "fields." prefix. Needs a reload because
  // the page request names the columns.
  setFieldColumns(names) {
    const next = names.slice();
    if (next.join("\n") === this.fieldColumns.join("\n")) {
      return Promise.resolve();
    }
    this.fieldColumns = next;
    this.pool = [];
    this.rowsLayer.textContent = "";
    this.renderHeader();
    return this.caseId === null ? Promise.resolve() : this.reload();
  }

  // Shows or hides the Collection column; it needs a reload because the page request names it.
  setCollectionColumn(on, reload = true) {
    const next = Boolean(on);
    if (next === this.collectionColumn) {
      return Promise.resolve();
    }
    this.collectionColumn = next;
    this.pool = [];
    this.rowsLayer.textContent = "";
    this.renderHeader();
    return this.caseId === null || !reload ? Promise.resolve() : this.reload();
  }

  // tags: Map group id -> {tag: "C2", color: 2}
  setCollectionTags(tags) {
    this.collectionTags = tags;
    this.render();
  }

  // The visible columns in order: the fixed ones (the Collection column right after Source), then one
  // per chosen parsed field.
  columnDefs() {
    const defs = [];
    for (const column of COLUMNS) {
      if (!this.visibleColumns.includes(column.key)) {
        continue;
      }
      defs.push(column);
      if (column.key === "source" && this.collectionColumn) {
        defs.push(COLLECTION_COLUMN);
      }
    }
    if (this.collectionColumn && !defs.includes(COLLECTION_COLUMN)) {
      defs.splice(Math.max(0, defs.length - 1), 0, COLLECTION_COLUMN);
    }
    for (const name of this.fieldColumns) {
      defs.push({ key: "fields." + name, label: () => name, field: name });
    }
    return defs;
  }

  columnNames() {
    const names = this.fieldColumns.map((n) => "fields." + n);
    return this.collectionColumn ? ["collection", ...names] : names;
  }

  selectedRow() {
    return this.window.rows[this.window.selected] || null;
  }

  // Loads the window at `row` and selects it (used by the keyboard navigation).
  async revealRow(row) {
    await this.jump(row.ts === null || row.ts === undefined ? "null:" + (row.id - 1) : row.ts + ":" + (row.id - 1), "fwd");
    const index = this.window.rows.findIndex((r) => r.id === row.id);
    if (index >= 0) {
      this.window.selected = index;
      this.window.top = index;
      this.render();
      this.syncScroll();
      this.notifySelect(0);
    }
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
    this.resizer.resetHeader();
    for (const column of this.columnDefs()) {
      const cell = document.createElement("div");
      cell.className = "grid-cell " + cellClass(column);
      cell.setAttribute("role", "columnheader");
      cell.textContent = column.label();
      this.header.appendChild(cell);
      this.resizer.decorate(cell, column);
    }
    this.resizer.apply();
  }

  resetColumnWidths() {
    this.resizer.reset();
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
    // New case or filter: the old count is no longer a baseline, and its answer must not arrive.
    this.total = null;
    this.countGeneration += 1;
    if (this.countController) {
      this.countController.abort();
      this.countController = null;
    }
    this.render();
    if (this.caseId === null) {
      this.onCount(null);
      this.onPosition(null);
      return;
    }
    this.onCount("loading");
    this.refreshCount(true);
    await this.jump(null, "fwd");
  }

  // The count has its own generation: navigation (jump, scroll) must not discard its answer; only a newer
  // count or a reload (new case or filter) does.
  async refreshCount(announce = false) {
    this.countGeneration += 1;
    const generation = this.countGeneration;
    if (this.countController) {
      this.countController.abort();
    }
    const controller = new AbortController();
    this.countController = controller;
    try {
      const result = await api.count(this.caseId, this.filter, controller.signal);
      if (generation === this.countGeneration) {
        this.total = Number(result.count);
        this.onCount({ n: this.total, exact: Boolean(result.exact) }, { announce });
      }
    } catch (err) {
      if (generation === this.countGeneration && !(err && err.name === "AbortError")) {
        this.onCount(null);
        this.onError(err);
      }
    } finally {
      if (generation === this.countGeneration) {
        this.countController = null;
      }
    }
  }

  updateStatus() {
    const row = this.window.topRow();
    this.onPosition(row ? row.ts : null);
  }

  // Replaces the window with the page found at `cursor` (null = first page).
  async jump(cursor, dir) {
    this.cancel();
    const generation = this.generation;
    this.controller = new AbortController();
    this.loading = true;
    try {
      const page = await api.page(this.caseId, this.filter, cursor, dir, PAGE_LIMIT, this.controller.signal, this.columnNames());
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
        const page = await api.page(this.caseId, this.filter, cursor, plan, PAGE_LIMIT, this.controller.signal, this.columnNames());
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

  // Where the view stands relative to the end of the data (see follow-tail-model.js).
  tailInfo() {
    return { atEnd: this.window.atEnd, top: this.window.top, visible: this.visible, length: this.window.rows.length };
  }

  // Reloads the end of the data and keeps the view at the bottom; the selected entry stays selected
  // when it is still in the buffer.
  async followTail() {
    const selected = this.selectedRow();
    await this.goEnd();
    if (selected) {
      const index = this.window.rows.findIndex((r) => r.id === selected.id);
      if (index >= 0) {
        this.window.selected = index;
        this.render();
      }
    }
    await this.refreshCount();
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
    for (const column of this.columnDefs()) {
      const cell = document.createElement("div");
      cell.className = "grid-cell " + cellClass(column);
      cell.setAttribute("role", "gridcell");
      cell.style.width = this.resizer.widthOf(column.key);
      rowEl.appendChild(cell);
      cells[column.key] = cell;
    }
    this.rowsLayer.appendChild(rowEl);
    return { el: rowEl, cells };
  }

  render() {
    this.ensurePool();
    const w = this.window;
    this.rowsLayer.setAttribute("aria-rowcount", String(w.rows.length));
    const shown = [];
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
      const mark = this.getMark(row.id);
      const hl = this.getHighlight(row.id);
      slot.el.setAttribute("aria-label", format(S.rowLabel, { id: row.id }) + (mark ? ", " + markerLabel(mark) : "") +
        (hl ? ", " + hl.label : ""));
      if (mark) {
        slot.el.dataset.bm = mark.color;
      } else {
        delete slot.el.dataset.bm;
      }
      if (hl) {
        slot.el.dataset.hl = hl.color;
      } else {
        delete slot.el.dataset.hl;
      }
      shown.push(row.id);
      slot.el.dataset.sev = String(row.sev);
      this.fillCells(slot, row);
    }
    this.onVisible(shown);
    this.updateStatus();
    this.onView();
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
    for (const name of this.fieldColumns) {
      const cell = cells["fields." + name];
      if (cell) {
        cell.textContent = fieldText(row["fields." + name]);
      }
    }
    if (cells.source) {
      const name = this.sourceNames.get(row.source_id);
      cells.source.textContent = name === undefined ? String(row.source_id) : baseName(name);
    }
    if (cells.collection) {
      const info = this.collectionTags.get(row.collection);
      cells.collection.textContent = info ? info.tag : row.collection === null || row.collection === undefined ? "" : String(row.collection);
      if (info) {
        cells.collection.dataset.tag = String(info.color);
      } else {
        delete cells.collection.dataset.tag;
      }
    }
    if (cells.msg) {
      renderHighlighted(cells.msg, row.msg === null || row.msg === undefined ? "" : String(row.msg), this.terms);
      if (row.nlines > 1) {
        const extra = document.createElement("span");
        extra.className = "extra-lines";
        extra.textContent = format(S[pluralKey("extraLines", row.nlines - 1)], { count: row.nlines - 1 });
        cells.msg.appendChild(extra);
      }
    }
  }
}
