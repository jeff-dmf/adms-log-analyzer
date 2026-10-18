// Virtual log grid: a pooled set of row elements over a keyset-paged RowWindow. The scrollbar
// maps onto the time range of the chosen sources; wheel and keys move by rows.
import { api, bindCurrent, errorKind } from "./api.js";
import {
  END_CURSOR, PAGE_LIMIT, ROW_HEIGHT, SCROLL_SPAN, RowWindow, SourceLabels, fetchPlan, isMultiDay,
  jumpCursor, scrollToTs, shortTs, topAfterResize, tsToScroll,
} from "./grid-model.js";
import { ColumnResizer } from "./column-resize.js";
import { baseName } from "./source-filter-model.js";
import { markerLabel } from "./bookmarks-model.js";
import { renderHighlighted } from "./search.js";
import { S, format, pluralKey } from "./strings.js";
import { cursorTime, formatTs } from "./time.js";
import { CELL_PAD_PX, SOURCE_CH_MULTIDAY, TS_CH_MULTIDAY } from "./column-resize-model.js";

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

// Shown (right after Time) while some collection has a clock offset (9.15): Time keeps the original time, this one the adjusted
// time the order uses. Never part of COLUMNS: the Columns menu does not list it and the stored column choice ignores it.
export const ADJUSTED_COLUMN = { key: "adjusted", label: () => S.ckColumnAdjusted };

function cellClass(column) {
  return column.field === undefined ? "col-" + column.key : "col-field";
}

function fieldText(value) {
  if (value === null || value === undefined) {
    return "";
  }
  return typeof value === "object" ? JSON.stringify(value) : String(value);
}

// One frame of the browser. Resolved at call time (a test installs its own); a timer stands in where there is no
// animation frame. The Grid takes both as options (requestFrame, cancelFrame) so a test can drive the frames by hand.
function defaultRequestFrame(callback) {
  return typeof requestAnimationFrame === "function" ? requestAnimationFrame(callback) : setTimeout(callback, 16);
}

function defaultCancelFrame(handle) {
  if (typeof cancelAnimationFrame === "function") {
    cancelAnimationFrame(handle);
  } else {
    clearTimeout(handle);
  }
}

// Wheel travel of one event, normalised from pixel (0), line (1) or page (2) units.
//   vertical: in rows (a line is 3 rows, a page is the visible rows less one, a pixel is 1/ROW_HEIGHT row);
//   horizontal: in px (a line is 3 rows of height, a page is the viewport width).
export function wheelTravel(event, { visible = 1, pageWidth = 0 } = {}) {
  const mode = event.deltaMode;
  if (event.shiftKey || Math.abs(event.deltaX) > Math.abs(event.deltaY)) {
    const raw = event.deltaX || event.deltaY || 0;
    const px = mode === 1 ? raw * 3 * ROW_HEIGHT : mode === 2 ? raw * (pageWidth > 0 ? pageWidth : 400) : raw;
    return { axis: "x", amount: px };
  }
  const raw = event.deltaY || 0;
  const rows = mode === 1 ? raw * 3 : mode === 2 ? raw * Math.max(1, visible - 1) : raw / ROW_HEIGHT;
  return { axis: "y", amount: rows };
}

export class Grid {
  constructor({
    viewport, rowsLayer, spacer, header, onSelect, onCount, onPosition, onError, getMark, onVisible, onView,
    getHighlight, getRepeat, onFailure, onRecovered, onAbandon, requestFrame, cancelFrame, onContextMenu,
  }) {
    this.requestFrame = requestFrame || defaultRequestFrame;
    this.cancelFrame = cancelFrame || defaultCancelFrame;
    this.viewport = viewport;
    this.rowsLayer = rowsLayer;
    this.spacer = spacer;
    this.header = header;
    this.onSelect = onSelect;
    // Right click on a record, Menu key or Shift+F10 on the grid (UI-29): ({row, x, y, keyboard}), the record is selected.
    this.onContextMenu = onContextMenu || null;
    this.onCount = onCount || (() => {}); // ({n, exact} | "loading" | null, {announce}) -> status bar (UI-13)
    this.onPosition = onPosition || (() => {}); // (ts | null): first visible record's time; never announced
    this.onError = onError;
    // A request failed (UI-25): (error, {kind, retry}). The old rows stay on screen; without this hook the error goes
    // to onError. onRecovered() follows the next request that works, so the notice can go away.
    this.onFailure = onFailure || ((err) => this.onError(err));
    this.onRecovered = onRecovered || (() => {}); // (scope): "grid" (rows) or "count"
    // A pending view change was dropped because the user navigated (Home, End, a time, a column change) before its first
    // page arrived: the owner puts the controls back to the view the rows belong to.
    this.onAbandon = onAbandon || (() => {});
    this.countFailure = null; // {error, kind, announce}: the count that failed, repeatable alone with retryCount()
    this.countFailureShown = false;
    this.getMark = getMark || (() => null); // bookmark of a record id: {color} | null
    this.getHighlight = getHighlight || (() => null); // highlight rule of a record id: {color, label} | null
    this.getRepeat = getRepeat || (() => null); // repeat mark of a record id: {label} | null (repeat-model.js, 9.12)
    this.onVisible = onVisible || (() => {}); // ids of the rendered rows (bookmark lookup)
    this.onView = onView || (() => {});
    this.failure = null; // {kind, error, op}: what failed last and can be repeated (retry())
    this.failureShown = false; // onFailure was called and no request worked since
    this.pending = null; // a view change waiting for its first page: {caseId, filter, range, count, ...}
    this.shownCount = null; // what the status bar shows now: {n, exact} | null
    this.busy = false;
    this.countGeneration = 0;
    this.scopeEpoch = 0;   // grows every time a view is adopted or the grid is cleared: a late answer for case A is not for A shown again
    this.navEpoch = 0;     // counts the user's own moves of the view (rows, keys, Home/End, scrollbar, jumps)
    this.tailOp = null;    // the operation (followTail) that is loading the end: a move by the user cancels that load
    this.tailJump = false; // set only while followTail() starts its own jump (not a user move)
    this.countController = null;
    this.total = null; // last entry count of the filter (a number), the baseline of "follow tail"
    this.fieldColumns = [];
    this.window = new RowWindow();
    this.visibleColumns = COLUMNS.map((c) => c.key);
    this.sourceNames = new Map();
    this.sourceLabels = new SourceLabels(); // cut names by (source, width in characters), see relayoutSource()
    this.fitting = false;
    this.multiDay = false;                  // the case covers several days: the time column shows MM-DD too
    this.collectionColumn = false; // the Collection column (C<n>) is shown
    this.collectionTags = new Map(); // collection group -> {tag, color}
    this.adjustedColumn = false; // the Adjusted time column is shown (clock offsets in the case)
    this.terms = [];
    this.caseId = null;
    this.filter = null;
    this.range = null;
    this.generation = 0;
    this.loading = false;
    this.controller = null;
    this.pool = [];
    this.rowSeq = 0;      // mints the unique id of a row element (aria-activedescendant target, see render())
    this.active = null;   // {slot, recordId}: the row aria-activedescendant points at
    this.countFresh = false; // shownCount belongs to the view whose rows are in the window (see adopt())
    this.visible = 0;
    this.lastScroll = 0;
    this.pendingJump = null;
    this.selectTimer = null;
    this.wheelRemainder = 0; // wheel travel in rows not applied yet (the fraction stays); applied once per frame
    this.wheelX = 0;         // horizontal wheel travel in px not applied yet (clamped in order at every event)
    this.wheelFrame = null;  // handle of the frame that applies it, or null
    this.viewHeight = 0;     // viewport height from the ResizeObserver (0: not reported yet or hidden), see ensurePool()
    this.observer = null;
    this.listeners = new AbortController();
    this.fontsHandler = null;
    this.destroyed = false;
    this.knownHeight = 0; // viewport height at the last resize (ensureSelectedVisible)
    this.spacer.style.height = SCROLL_SPAN + "px";
    // The header sits outside the scrolling viewport in the page layout; aria-owns puts it inside the grid for assistive
    // technology (UI-26). The attributes below are set on the grid itself, so this adds no layout work.
    this.idPrefix = viewport.id || "grid";
    rowsLayer.setAttribute("role", "rowgroup");
    if (header.id) {
      viewport.setAttribute("aria-owns", header.id);
    }
    this.resizer = new ColumnResizer({
      viewport, rowsLayer, header,
      // Every pooled slot, hidden ones included: a hidden row keeps the current widths when render() shows it again.
      getCells: (key) => this.pool.filter((s) => s.cells[key]).map((s) => s.cells[key]),
      getVisibleCells: (key) => this.pool.filter((s) => !s.el.hidden && s.cells[key]).map((s) => s.cells[key]),
      getKeys: () => this.columnDefs().map((c) => c.key),
      onLayout: () => this.relayoutSource(),
      prepareFit: (key) => this.prepareFit(key),
    });
    if (typeof document !== "undefined" && document.fonts && typeof document.fonts.addEventListener === "function") {
      // A font that finished loading changes the character width: measure again (never per row or on scroll).
      this.fontsHandler = () => {
        if (!this.destroyed && this.resizer.measure()) {
          this.resizer.apply();
        }
      };
      document.fonts.addEventListener("loadingdone", this.fontsHandler);
    }
    this.bind();
    this.renderHeader();
  }

  bind() {
    // The wheel only adds up: the rows move once per frame (applyWheel), so 120 events in a burst cost one render.
    // Every listener is tied to one AbortController (destroy() removes them all); the handlers also check `destroyed`
    // for hosts that ignore the signal.
    const signal = this.listeners.signal;
    this.viewport.addEventListener("wheel", (event) => {
      event.preventDefault();
      if (this.destroyed) {
        return;
      }
      const travel = wheelTravel(event, { visible: this.visible, pageWidth: this.resizer.pageWidth });
      const w = this.window;
      if (travel.axis === "x") {
        // Clamped at every event, so a reversal after the edge starts from the edge (excess is dropped, not remembered).
        // A delta, never a target: the frame adds it to the position the body has THEN (a drag of the bar in between
        // is kept). Clamped here against the position now, so an excess at an edge is dropped in order.
        const at = this.resizer.scrollX;
        this.wheelX = Math.min(Math.max(this.wheelX + travel.amount, -at), this.resizer.maxScrollX - at);
      } else {
        this.userMoved(); // the gesture itself is the later intention: an end-of-data load in flight must not replace the rows
        this.wheelRemainder = Math.min(Math.max(this.wheelRemainder + travel.amount, -w.top), w.maxTop(this.visible) - w.top);
      }
      if (this.wheelFrame === null) {
        this.wheelFrame = this.requestFrame(() => this.applyWheel());
      }
    }, { passive: false, signal });
    this.viewport.addEventListener("scroll", () => {
      if (!this.destroyed) {
        this.onScrollBar();
      }
    }, { signal });
    this.viewport.addEventListener("keydown", (event) => {
      if (!this.destroyed) {
        this.onKey(event);
      }
    }, { signal });
    this.viewport.addEventListener("contextmenu", (event) => {
      if (!this.destroyed) {
        this.onContextMenuEvent(event);
      }
    }, { signal });
    this.rowsLayer.addEventListener("click", (event) => {
      if (this.destroyed) {
        return;
      }
      const rowEl = event.target.closest(".grid-row");
      if (rowEl && rowEl.dataset.index !== undefined) {
        this.window.selected = Number(rowEl.dataset.index);
        this.render();
        this.notifySelect(0);
        this.viewport.focus();
      }
    }, { signal });
    if (typeof ResizeObserver === "function") {
      // The grid owns the rule "the selected row stays visible" (docs/notas/ui-contratos.md, 2.5): a dock opening,
      // dragging or maximizing, a drawer or a breakpoint all arrive here as a size change of the viewport.
      this.observer = new ResizeObserver((entries) => {
        if (this.destroyed) {
          return;
        }
        const last = entries && entries.length ? entries[entries.length - 1] : null;
        const height = last && last.contentRect ? last.contentRect.height : this.readHeight();
        this.resizer.measureLayout(); // bar width and horizontal range follow the viewport size, never the scroll
        const wasHidden = !(this.viewHeight > 0);
        this.viewHeight = height > 0 ? height : 0; // ensurePool() sizes the pool from this, with no layout read
        this.ensureSelectedVisible(height);
        if (wasHidden && height > 0) {
          this.syncScroll(); // a scrollTop written while the viewport was hidden did not stick
        }
      });
      this.observer.observe(this.viewport);
    }
  }

  // The only place that reads the viewport height from the DOM: before the observer reported a height (or without a
  // ResizeObserver) and while the viewport is hidden. Never on the scroll path once a height is known.
  readHeight() {
    const height = this.viewport.clientHeight;
    return height > 0 ? height : 0;
  }

  // Applies what the wheel gathered since the last frame: one move of the rows, one render.
  applyWheel() {
    this.wheelFrame = null;
    const x = this.wheelX;
    this.wheelX = 0;
    if (x !== 0) {
      this.resizer.scrollTo(this.resizer.scrollX + x); // the kept position, clamped; writes only
    }
    const rows = Math.trunc(this.wheelRemainder);
    if (rows !== 0) {
      this.wheelRemainder -= rows;
      this.moveRows(rows);
    }
  }

  // Drops the wheel travel and the frame that would apply it: the rows it was meant for are gone (another case or
  // view, or the grid itself).
  cancelWheel() {
    if (this.wheelFrame !== null) {
      this.cancelFrame(this.wheelFrame);
      this.wheelFrame = null;
    }
    this.wheelRemainder = 0;
    this.wheelX = 0;
  }

  // The grid is dropped: no frame, observer callback or wheel event touches it afterwards.
  destroy() {
    if (this.destroyed) {
      return;
    }
    this.destroyed = true;
    this.cancelWheel();
    this.cancel();           // page requests: generation bumped (a late answer is superseded) and the fetch aborted
    this.invalidateCount();  // the same for the count
    this.pending = null;
    this.listeners.abort();
    if (this.fontsHandler && document.fonts && typeof document.fonts.removeEventListener === "function") {
      document.fonts.removeEventListener("loadingdone", this.fontsHandler);
    }
    this.resizer.destroy();
    clearTimeout(this.selectTimer);
    if (this.pendingJump !== null) {
      clearTimeout(this.pendingJump);
      this.pendingJump = null;
    }
    if (this.observer) {
      this.observer.disconnect();
      this.observer = null;
    }
  }

  // Keeps the selected row visible after the viewport changed height. Public and idempotent. Height 0 (the dock is
  // maximized and hides the grid) recalculates nothing and leaves `top` alone; the same height twice does nothing.
  ensureSelectedVisible(height = this.viewHeight > 0 ? this.viewHeight : this.readHeight()) {
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

  // Drops every pooled row. aria-activedescendant must not point at a removed element while the rows are rebuilt (or
  // when the reload that follows fails), so the reference goes with the pool.
  discardPool() {
    this.pool = [];
    this.rowsLayer.textContent = "";
    this.viewport.removeAttribute("aria-activedescendant");
  }

  setColumns(keys) {
    this.visibleColumns = COLUMNS.map((c) => c.key).filter((k) => keys.includes(k));
    this.discardPool();
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
    this.discardPool();
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
    this.discardPool();
    this.renderHeader();
    return this.caseId === null || !reload ? Promise.resolve() : this.reload();
  }

  // Shows or hides the Adjusted time column. The rows already carry ts_adjusted (the server adds it while an offset exists),
  // so no reload is needed; the view reloads anyway when an offset changes (the order moves).
  setAdjustedColumn(on) {
    const next = Boolean(on);
    if (next === this.adjustedColumn) {
      return;
    }
    this.adjustedColumn = next;
    this.discardPool();
    this.renderHeader();
    this.render();
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
      // The original time stays visible while the adjusted one is shown, even when the user hid the Time column.
      if (!this.visibleColumns.includes(column.key) && !(column.key === "ts" && this.adjustedColumn)) {
        continue;
      }
      defs.push(column);
      if (column.key === "ts" && this.adjustedColumn) {
        defs.push(ADJUSTED_COLUMN);
      }
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

  // The scope an operation started in: the case, and an epoch that grows whenever a view is adopted or the grid is cleared
  // (so case A shown again is not the A the operation started in). Revalidated after every wait, right before an effect.
  captureScope() {
    return { caseId: this.caseId, epoch: this.scopeEpoch };
  }

  inScope(scope) {
    return !this.destroyed && this.caseId === scope.caseId && this.scopeEpoch === scope.epoch;
  }

  // The instant a cursor for `row` must name. With clock offsets the cursor axis is the adjusted time; a record that comes
  // from elsewhere (events, bookmarks, links) only has its log time, so its adjusted time is read first (GET /entries/{id}).
  // undefined = the scope changed meanwhile.
  async revealTime(row, scope = this.captureScope()) {
    if (typeof row.ts_adjusted === "number" || !this.adjustedColumn || row.ts === null || row.ts === undefined) {
      const at = cursorTime(row);
      return at === undefined ? null : at;
    }
    const entry = await api.entry(scope.caseId, row.id);
    if (!this.inScope(scope)) {
      return undefined;
    }
    return cursorTime(entry && entry.id === row.id ? entry : row);
  }

  // Loads the window at `row` and selects it (used by the keyboard navigation). Nothing is selected unless the grid is still
  // in the scope the call started in AND the jump it made was applied: a cancelled jump (outcome "superseded") may leave the
  // rows of another case, where a record can have the same id.
  async revealRow(row) {
    const scope = this.captureScope();
    const at = await this.revealTime(row, scope);
    if (at === undefined || !this.inScope(scope)) {
      return; // another case (or view) took over while the adjusted time was being read
    }
    const outcome = await this.jump(at === null ? "null:" + (row.id - 1) : at + ":" + (row.id - 1), "fwd", null, () => this.inScope(scope));
    if (outcome !== "ok" || !this.inScope(scope)) {
      return;
    }
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
    this.sourceLabels.setNames(map);
    this.render();
  }

  // Characters that fit in the Source cell, from its width and the measured character width; Infinity while the
  // character width is unknown. Called after every width change (never per row): a new value empties the label cache.
  relayoutSource() {
    const model = this.resizer.model;
    const width = model.get("source") - CELL_PAD_PX;
    const chars = Number.isFinite(width) && model.charPx > 0 ? Math.floor(width / model.charPx) : Infinity;
    if (!this.fitting && chars !== this.sourceLabels.maxChars) {
      this.sourceLabels.setMaxChars(chars);
      if (this.pool.length > 0) {
        this.render();
      }
    }
  }

  // "Fit to content" of the Source column measures the whole names: show them uncut while it measures.
  prepareFit(key) {
    if (key !== "source") {
      return null;
    }
    this.fitting = true;
    this.sourceLabels.setMaxChars(Infinity);
    this.render();
    return () => {
      this.fitting = false;
      this.relayoutSource();
    };
  }

  // The time column is wider only for a case that covers more than one day; decided when the case changes and then
  // kept (a narrower source selection does not shrink the column back and forth).
  setMultiDay(on) {
    const next = Boolean(on);
    if (next === this.multiDay) {
      return;
    }
    this.multiDay = next;
    this.resizer.setDefaultCh("ts", next ? TS_CH_MULTIDAY : null, false);
    this.resizer.setDefaultCh("adjusted", next ? TS_CH_MULTIDAY : null, false);
    this.resizer.setDefaultCh("source", next ? SOURCE_CH_MULTIDAY : null);
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
      cell.title = column.label(); // narrow columns (Collection) cut the label
      this.header.appendChild(cell);
      this.resizer.decorate(cell, column);
    }
    this.viewport.setAttribute("aria-colcount", String(this.columnDefs().length));
    this.resizer.measure(); // once per column change
    this.resizer.apply();
  }

  resetColumnWidths() {
    this.resizer.reset();
  }

  // -- loading ------------------------------------------------------------------------------

  // `range`: time span of the filtered view; `caseRange`: span (or a list of spans) of the whole case, which decides the time column (the
  // restored filter of one day must not hide the date of a multi-day case). Without it the view range is used.
  // The view change is confirmed only when its first page arrives: until then the old rows (and the old filter, range
  // and count) stay. A new case has nothing to keep, so it is adopted at once. Resolves to "ok", "failed" (the old
  // rows stay; see onFailure) or "superseded" (a newer request replaced this one).
  async setView({ caseId, filter, range, caseRange }) {
    // `caseRange` may be a list (the adjusted and the original span, 9.15): the dates show when ANY of them crosses a day, since
    // the Time column shows original times and the Adjusted one adjusted times.
    const spans = Array.isArray(caseRange) ? caseRange : [caseRange === undefined ? range : caseRange];
    this.setMultiDay(spans.some((span) => isMultiDay(span)) || (caseId === this.caseId && this.multiDay));
    return this.reload({ caseId, filter, range, count: null, countFailed: false, announce: false, committed: false, failed: false });
  }

  cancel() {
    this.generation += 1;
    this.loading = false;
    this.setBusy(false);
    if (this.controller) {
      this.controller.abort();
      this.controller = null;
    }
  }

  // aria-busy on the grid while its rows are being replaced; `dim` shows the old rows muted while a new view loads.
  // The thin progress bar is CSS (it appears after 400 ms of aria-busy), so no timer is needed here.
  setBusy(on, dim = false) {
    const next = Boolean(on);
    this.busy = next;
    this.viewport.setAttribute("aria-busy", next ? "true" : "false");
    this.viewport.classList.toggle("is-reloading", next && dim);
  }

  hasFailure() {
    return this.failure !== null;
  }

  adopt(view) {
    if (!view.committed) {
      this.countFresh = false; // the count shown so far is the previous view's until its own answer is emitted
    }
    this.cancelWheel(); // travel gathered over the old rows does not move the new ones
    this.scopeEpoch += 1;
    this.caseId = view.caseId;
    this.filter = view.filter;
    this.range = view.range;
  }

  currentView() {
    return { caseId: this.caseId, filter: this.filter, range: this.range, count: null, countFailed: false, announce: false, committed: true, failed: false };
  }

  noteRecovered() {
    this.failure = null;
    if (this.failureShown) {
      this.failureShown = false;
      this.onRecovered("grid");
    }
  }

  // A pending view change that the user's navigation replaces: it is dropped (its page and its count are discarded) and
  // the owner is told, so the controls go back to the confirmed view. Never for a new case (nothing to go back to).
  abandonPending() {
    const pending = this.pending;
    if (pending === null || pending.committed || pending.failed) {
      return;
    }
    pending.abandoned = true;
    this.pending = null;
    this.invalidateCount();
    this.onCount(this.shownCount);
    this.restoreCountFailure(pending);
    this.onAbandon(pending);
  }

  // The count in flight (or held) belongs to a view that will not be shown: its answer, success or failure, is dropped.
  invalidateCount() {
    this.countGeneration += 1;
    if (this.countController) {
      this.countController.abort();
      this.countController = null;
    }
  }

  // A view change that did not replace the rows leaves the old view on screen: the count failure it had (and that the
  // attempt cleared) is registered again so it can still be repeated.
  restoreCountFailure(view) {
    const previous = view.prevCountFailure;
    if (previous) {
      view.prevCountFailure = null;
      this.failCount(previous.error, previous.announce);
    }
  }

  // Another case: every request of the old one is cancelled and forgotten at once (rows, count, failures, pending),
  // so a late answer can neither paint its rows nor register a recovery.
  closeCase() {
    this.pending = null;
    this.cancelWheel();
    this.cancel();
    this.invalidateCount();
    if (this.pendingJump !== null) {
      clearTimeout(this.pendingJump);
      this.pendingJump = null;
    }
    this.failure = null;
    this.failureShown = false;
    this.countFailure = null;
    this.countFailureShown = false;
    this.scopeEpoch += 1;
    this.caseId = null;
    this.filter = null;
    this.range = null;
    this.total = null;
    this.shownCount = null;
    this.window.reset();
    this.render();
    this.onCount(null);
    this.onPosition(null);
  }

  clearCountFailure() {
    this.countFailure = null;
    if (this.countFailureShown) {
      this.countFailureShown = false;
      this.onRecovered("count");
    }
  }

  failCount(error, announce) {
    const kind = errorKind(error);
    this.countFailure = { error, kind, announce: Boolean(announce) };
    this.countFailureShown = true;
    this.onFailure(error, { kind, scope: "count", op: "count", retry: () => this.retryCount() });
  }

  // Repeats only the count that failed (the rows are fine). One at a time; nothing failed: nothing happens.
  retryCount() {
    if (this.countFailure === null || this.countController !== null) {
      return Promise.resolve("noop");
    }
    const { announce } = this.countFailure;
    this.countFailure = null;
    this.onCount("loading");
    return this.refreshCount(announce).then(() => (this.countFailure === null ? "ok" : "failed"));
  }

  fail(err, op) {
    const kind = errorKind(err);
    this.failure = { kind, error: err, op };
    this.failureShown = true;
    this.onFailure(err, { kind, scope: "grid", op: op.kind, retry: () => this.retry() });
  }

  // Repeats only what failed: the page of the scroll, the jump, or the view change. A second call while it runs, or
  // with nothing failed, does nothing.
  retry() {
    const failure = this.failure;
    if (failure === null || this.loading) {
      return Promise.resolve("noop");
    }
    this.failure = null;
    const op = failure.op;
    if (op.kind === "page") {
      return this.pump().then(() => "ok");
    }
    if (op.kind === "jump") {
      return this.jump(op.cursor, op.dir);
    }
    return this.reload(op.target);
  }

  // `view`: a pending view change (setView) or null for the current view (columns changed).
  async reload(view = null) {
    if (view === null) {
      this.abandonPending(); // a column change must not leave an older view change half applied
    }
    this.cancel();
    const target = view || this.currentView();
    this.failure = null;
    // A count failure of the view on screen survives a view change that does not load (see restoreCountFailure).
    const carried = this.countFailure || (this.pending && !this.pending.committed ? this.pending.prevCountFailure : null) || null;
    if (carried !== null && target.prevCountFailure === undefined) {
      target.prevCountFailure = carried;
    }
    this.pending = target;
    // The count of the new view has its own answer, held until the page arrives; the old one is not a baseline for it.
    this.invalidateCount();
    this.clearCountFailure();
    if (target.caseId !== this.caseId || target.caseId === null) {
      // Another case: its rows cannot be replaced by those of the old one.
      this.adopt(target);
      target.committed = true;
      this.window.reset();
      this.total = null;
      this.render();
    }
    if (this.caseId === null) {
      this.pending = null;
      this.shownCount = null;
      this.syncGridAria();
      this.onCount(null);
      this.onPosition(null);
      return "ok";
    }
    this.onCount("loading");
    this.refreshCount(true, target);
    return this.jump(null, "fwd", target);
  }

  // The count has its own generation: navigation (jump, scroll) must not discard its answer; only a newer
  // count or a reload (new case or filter) does. `view`: the pending view change it belongs to, if any.
  async refreshCount(announce = false, view = null, isCurrent = null) {
    if (this.destroyed) {
      return;
    }
    this.countGeneration += 1;
    const generation = this.countGeneration;
    if (this.countController) {
      this.countController.abort();
    }
    const controller = new AbortController();
    const live = () => generation === this.countGeneration && (isCurrent === null || isCurrent());
    bindCurrent(controller.signal, live);
    this.countController = controller;
    const caseId = view ? view.caseId : this.caseId;
    const filter = view ? view.filter : this.filter;
    try {
      const result = await api.count(caseId, filter, controller.signal);
      if (live()) {
        const value = { n: Number(result.count), exact: Boolean(result.exact) };
        if (view && !view.committed) {
          view.count = value; // shown with its page, never before it (dropped if the page fails)
          view.announce = announce;
        } else {
          this.total = value.n;
          this.emitCount(value, { announce });
        }
        if (!view || view.committed) {
          this.clearCountFailure();
        }
      }
    } catch (err) {
      if (live() && !(err && err.name === "AbortError")) {
        if (view && !view.committed) {
          view.countFailed = true; // reported with its page, or dropped with it
          view.countError = err;
        } else {
          this.emitCount(null);
          this.failCount(err, announce);
        }
      }
    } finally {
      if (generation === this.countGeneration) {
        this.countController = null;
      }
    }
  }

  emitCount(value, options) {
    this.shownCount = value;
    this.countFresh = value !== null && typeof value === "object";
    this.syncGridAria();
    this.onCount(value, options);
  }

  updateStatus() {
    const row = this.window.topRow();
    this.onPosition(row ? row.ts : null);
  }

  // Replaces the window with the page found at `cursor` (null = first page). `view`: a pending view change, adopted
  // only when its page arrives. On failure the window, the selection and the scroll position are left as they were.
  // Resolves to "ok", "failed" or "superseded".
  async jump(cursor, dir, view = null, guard = null) {
    if (this.destroyed) {
      return "superseded";
    }
    this.cancelWheel(); // Home, End, a time and the scrollbar land here: older wheel travel must not move the new rows
    if (view === null) {
      if (!this.tailJump) {
        this.navEpoch += 1; // a move by the user (Follow tail's own jump is not one)
      }
      this.abandonPending();
    }
    this.cancel();
    const generation = this.generation;
    this.controller = new AbortController();
    bindCurrent(this.controller.signal, () => generation === this.generation && (guard === null || guard()));
    this.loading = true;
    this.failure = null;
    this.setBusy(true, view !== null);
    const target = view || this.currentView();
    let outcome = "superseded";
    const adoptedEarly = view !== null && view.committed; // another case: its count was never held back
    try {
      const page = await api.page(target.caseId, target.filter, cursor, dir, PAGE_LIMIT, this.controller.signal, this.columnNames());
      if (generation !== this.generation || (guard !== null && !guard())) {
        return "superseded"; // a newer jump, or the caller's operation is obsolete: the page is not applied
      }
      if (view) {
        this.adopt(view);
        view.committed = true;
        this.pending = null;
      }
      this.cancelWheel(); // travel gathered while this page was on its way was meant for the rows it replaces
      this.window.load(page.rows, page.prev, page.next, 0);
      this.ensurePool();
      if (dir === "back") {
        this.window.top = this.window.maxTop(this.visible);
      }
      outcome = "ok";
    } catch (err) {
      if (generation === this.generation && (guard === null || guard()) && !(err && err.name === "AbortError")) {
        if (view) {
          view.failed = true;
          this.pending = null;
          this.invalidateCount(); // its count is dropped with it: a late answer or failure changes nothing
          this.onCount(this.shownCount); // the held answer belongs to the view that did not load: show the old count
        }
        this.fail(err, view ? { kind: "reload", target: { ...view, failed: false, committed: false, count: null, countFailed: false, prevCountFailure: undefined } }
          : { kind: "jump", cursor, dir });
        if (view) {
          this.restoreCountFailure(view);
        }
        outcome = "failed";
      }
    } finally {
      if (generation === this.generation) {
        this.loading = false;
        this.pendingJump = null;
        this.setBusy(false);
      }
    }
    if (outcome === "failed") {
      this.syncScroll(); // the scrollbar goes back to where the rows are
      return outcome;
    }
    if (outcome !== "ok") {
      return outcome;
    }
    this.noteRecovered();
    if (view && !adoptedEarly) {
      this.total = view.count ? view.count.n : null; // the old total is no baseline for the new view
      if (view.countFailed) {
        this.emitCount(null);
        this.failCount(view.countError, view.announce);
      } else if (view.count) {
        this.emitCount(view.count, { announce: view.announce });
      }
    }
    this.render();
    this.syncScroll();
    this.pump();
    return outcome;
  }

  // Fetches more rows while the viewport is close to either end of the buffer. Stops at a failure (a scroll does not
  // hammer a server that is down); retry() or any navigation starts it again.
  async pump() {
    if (this.destroyed) {
      return;
    }
    const generation = this.generation;
    while (generation === this.generation && !this.loading && this.failure === null) {
      const w = this.window;
      const plan = fetchPlan(w.top, this.visible, w.rows.length, w.atStart, w.atEnd);
      if (plan === null) {
        return;
      }
      this.loading = true;
      this.controller = new AbortController();
      bindCurrent(this.controller.signal, () => generation === this.generation);
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
        this.noteRecovered();
      } catch (err) {
        if (generation === this.generation && !(err && err.name === "AbortError")) {
          this.fail(err, { kind: "page" });
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

  // Follow tail counted the case while the view is scrolled up: the status bar shows the newest total, so it
  // agrees with the collection and the "new records" button. Nothing is announced (the live region stays quiet).
  // Skipped while a view change or a count failure owns the count. True when it was shown.
  showTotal(count, exact = true, requestId = undefined) {
    const n = Number(count);
    if (requestId !== undefined && requestId !== this.countGeneration) {
      return false; // a newer count (a reload, another total) was ordered after this request
    }
    if (this.destroyed || this.caseId === null || this.pending || this.countFailure !== null || !Number.isFinite(n)) {
      return false;
    }
    this.invalidateCount(); // one order for the answers: a count requested before this total must not overwrite it
    this.total = n;
    this.emitCount({ n, exact: Boolean(exact) });
    return true;
  }

  // The order of count requests, shared with Follow tail: a request takes the current id here, and its answer is
  // accepted by showTotal() only while no newer count (reload, other total, invalidation) was ordered after it.
  beginCount() {
    return this.countGeneration;
  }

  // Reloads the end of the data and keeps the view at the bottom; the selected entry stays selected when it is
  // still in the buffer. `op` (optional): the caller's operation, {isCurrent()}. It is checked right before every effect (applying the
  // end page, showing its failure, requesting and publishing the count). A move by the user while the end loads
  // also ends it: the later intention prevails. Resolves to true when the view is at the end and its count was
  // requested, false when the operation was dropped.
  async followTail(op = null) {
    const selected = this.selectedRow();
    const mine = { isCurrent: () => op === null || op.isCurrent() };
    const epoch = this.navEpoch;
    const current = () => !this.destroyed && this.navEpoch === epoch && this.tailOp === mine && mine.isCurrent();
    this.tailOp = mine;
    let jumping;
    this.tailJump = true;
    try {
      jumping = this.goEnd(current);
    } finally {
      this.tailJump = false;
    }
    let outcome;
    try {
      outcome = await jumping;
    } finally {
      if (this.tailOp === mine) {
        this.tailOp = null; // only the owner clears it: an older load must not drop the protection of a newer one
      }
    }
    const done = () => !this.destroyed && this.navEpoch === epoch && mine.isCurrent();
    if (outcome !== "ok" || !done()) {
      return false;
    }
    if (selected) {
      const index = this.window.rows.findIndex((r) => r.id === selected.id);
      if (index >= 0) {
        this.window.selected = index;
        this.render();
      }
    }
    await this.refreshCount(false, null, done);
    return done();
  }

  // The user moved the view: the end-of-data load Follow tail has in flight is dropped (its page would replace the
  // rows the user chose).
  userMoved() {
    this.navEpoch += 1;
    if (this.tailOp !== null) {
      this.tailOp = null;
      this.cancel();
    }
  }

  jumpToTime(ms) {
    return this.jump(jumpCursor(ms), "fwd");
  }

  goHome() {
    return this.jump(null, "fwd");
  }

  goEnd(guard = null) {
    return this.jump(END_CURSOR, "back", null, guard);
  }

  // -- scrolling ----------------------------------------------------------------------------

  // A programmatic move of the rows: whatever the wheel gathered was meant for the old position.
  scrollRows(delta) {
    this.cancelWheel();
    this.moveRows(delta);
  }

  moveRows(delta) {
    if (this.destroyed) {
      return;
    }
    this.userMoved();
    this.window.moveTop(delta, this.visible);
    // The scrollbar first: writing scrollTop after the rows were rewritten makes the browser lay the page out on the
    // spot (a forced layout per wheel frame); with the layout still clean from the last frame the write is cheap.
    this.syncScroll();
    this.render();
    this.pump();
  }

  syncScroll() {
    const row = this.window.topRow();
    if (!row || this.pendingJump !== null) {
      return;
    }
    // Write only: reading scrollTop back would force a layout on every wheel frame. The target is an integer inside
    // the scroll travel (the rows layer fills the viewport, the spacer adds exactly SCROLL_SPAN), so it is what the
    // browser keeps, and the scroll event it causes matches lastScroll within onScrollBar's 3 px tolerance.
    const top = tsToScroll(cursorTime(row), this.range); // the range is on the same (adjusted) axis as the jumps
    this.viewport.scrollTop = top;
    this.lastScroll = top;
  }

  // The scrollbar was moved: when it is not where the rows are (3 px tolerance), the user did it, so the rows go there.
  // Known, as on main: this reads scrollTop on every scroll event, including the echo of syncScroll(); the real cost
  // in Chrome is measured by tools/ui_perf.mjs (follow-up).
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
      this.cancelWheel(); // the selection moved the rows: older wheel travel is stale
      this.userMoved();
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

  // The only layout read of the record menu, once per gesture: a pointer gives the record under it (it is selected like a
  // click does, without asking for its detail: the owner shows it); the keyboard (the event is not a right click and
  // targets the grid itself) gives the selected record.
  onContextMenuEvent(event) {
    if (this.onContextMenu === null) {
      return;
    }
    const rowEl = event.target && typeof event.target.closest === "function" ? event.target.closest(".grid-row") : null;
    if (rowEl && rowEl.dataset.index !== undefined) {
      const index = Number(rowEl.dataset.index);
      const row = this.window.rows[index];
      if (!row) {
        return;
      }
      event.preventDefault();
      this.holdSelection();
      if (this.window.selected !== index) {
        this.window.selected = index;
        this.render();
      }
      this.viewport.focus();
      this.onContextMenu({ row, x: event.clientX, y: event.clientY, keyboard: false });
      return;
    }
    const row = this.selectedRow();
    if (event.button === 2 || !row) {
      return; // a right click outside the records, or nothing selected: the browser's own menu
    }
    event.preventDefault();
    this.holdSelection();
    const at = this.selectedAnchor();
    this.onContextMenu({ row, x: at.x, y: at.y, keyboard: true });
  }

  // The record menu takes over the selection: a "show the selected record" still waiting for its delay (arrows pressed a
  // moment ago) must not fire later and close the menu. The owner of the menu shows the record itself.
  holdSelection() {
    clearTimeout(this.selectTimer);
    this.selectTimer = null;
  }

  // Where a menu opened from the keyboard goes: under the selected row, a little to the right of its left edge.
  selectedAnchor() {
    const slot = this.pool.find((s) => !s.el.hidden && s.el.dataset.index === String(this.window.selected));
    const box = (slot ? slot.el : this.viewport).getBoundingClientRect();
    return { x: box.left + 24, y: slot ? box.bottom : box.top };
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
    // The height the observer reported; the DOM is read only while none is known (readHeight).
    const height = (this.viewHeight > 0 ? this.viewHeight : this.readHeight()) || 400;
    this.visible = Math.max(1, Math.ceil(height / ROW_HEIGHT) + 1);
    while (this.pool.length < this.visible) {
      this.pool.push(this.createRow());
    }
  }

  createRow() {
    const rowEl = document.createElement("div");
    rowEl.className = "grid-row";
    rowEl.setAttribute("role", "row");
    this.mintId(rowEl);
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
    const hint = document.createElement("span"); // bookmark / highlight state as text for screen readers
    hint.className = "visually-hidden";
    return { el: rowEl, cells, hint };
  }

  // A new unique id for a row element. Ids are not reused: when the selected record moves into the slot the previous
  // selection used (scrolling by one row keeps the slot), the row gets a fresh id so aria-activedescendant changes.
  mintId(el) {
    this.rowSeq += 1;
    el.id = this.idPrefix + "-row-" + this.rowSeq;
  }

  // Grid-level ARIA, attributes only (no layout reads). The header is row 1. Positions are exact only while the window
  // still starts at the first record (keyset paging has no row offsets otherwise); aria-rowcount is the total when the
  // count is exact and at least as large as what is loaded, else -1 (unknown).
  syncGridAria() {
    const w = this.window;
    const count = this.shownCount;
    const known = this.countFresh && count !== null && typeof count === "object" && count.exact && count.n >= w.rows.length;
    this.viewport.setAttribute("aria-rowcount", known ? String(count.n + 1) : "-1");
    this.viewport.setAttribute("aria-colcount", String(this.columnDefs().length));
    this.exactPositions = w.atStart;
    if (this.header.setAttribute) {
      if (this.exactPositions) {
        this.header.setAttribute("aria-rowindex", "1");
      } else {
        this.header.removeAttribute("aria-rowindex");
      }
    }
  }

  render() {
    if (this.destroyed) {
      return;
    }
    this.ensurePool();
    const w = this.window;
    this.syncGridAria();
    let activeEl = null;
    const shown = [];
    for (let i = 0; i < this.pool.length; i += 1) {
      const slot = this.pool[i];
      const index = w.top + i;
      const row = w.rows[index];
      if (!row || i >= this.visible) {
        slot.el.hidden = true;
        delete slot.el.dataset.index;
        slot.el.removeAttribute("aria-rowindex");
        continue;
      }
      slot.el.hidden = false;
      slot.el.dataset.index = String(index);
      const selected = index === w.selected;
      slot.el.classList.toggle("selected", selected);
      slot.el.setAttribute("aria-selected", selected ? "true" : "false");
      const mark = this.getMark(row.id);
      const hl = this.getHighlight(row.id);
      const repeat = this.getRepeat(row.id);
      if (selected) {
        if (this.active && this.active.slot === slot && this.active.recordId !== row.id) {
          this.mintId(slot.el);
        }
        this.active = { slot, recordId: row.id };
        activeEl = slot.el;
      }
      if (this.exactPositions) {
        slot.el.setAttribute("aria-rowindex", String(index + 2));
      } else {
        slot.el.removeAttribute("aria-rowindex");
      }
      if (repeat) {
        slot.el.dataset.rp = "1"; // the glyph is CSS (css/repeats.css); the words are the tooltip and the hidden text
        slot.el.title = repeat.label;
      } else {
        delete slot.el.dataset.rp;
        slot.el.removeAttribute("title");
      }
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
      this.fillHint(slot, mark, hl, repeat);
    }
    if (activeEl) {
      this.viewport.setAttribute("aria-activedescendant", activeEl.id);
    } else {
      this.active = null;
      this.viewport.removeAttribute("aria-activedescendant"); // the selection is outside the rendered rows
    }
    this.onVisible(shown);
    this.updateStatus();
    this.onView();
  }

  // Bookmark and highlight as hidden text in the row's first cell, so the row keeps its cell contents for screen readers
  // (an aria-label on the row would replace them). fillCells() has just cleared the cell.
  fillHint(slot, mark, hl, repeat) {
    const parts = [];
    if (mark) {
      parts.push(markerLabel(mark));
    }
    if (hl) {
      parts.push(hl.label);
    }
    if (repeat) {
      parts.push(repeat.label);
    }
    if (parts.length === 0) {
      return;
    }
    const text = parts.join(", ");
    // The Source cell may carry an aria-label (sensitive files), which replaces its content in the accessible name: it
    // does not host the hint. If it is the only column, the text goes into its label instead.
    const key = Object.keys(slot.cells).find((k) => k !== "source");
    if (key !== undefined) {
      slot.hint.textContent = text;
      slot.cells[key].appendChild(slot.hint);
    } else if (slot.cells.source) {
      slot.cells.source.setAttribute("aria-label", slot.cells.source.title + ", " + text);
    }
  }

  fillCells(slot, row) {
    const { cells } = slot;
    if (cells.ts) {
      const full = formatTs(row.ts);
      cells.ts.textContent = shortTs(full, this.multiDay);
      cells.ts.title = full; // the full date, also when the cell shows the clock only
    }
    if (cells.adjusted) {
      const adjusted = typeof row.ts_adjusted === "number" ? row.ts_adjusted : row.ts;
      const full = formatTs(adjusted);
      cells.adjusted.textContent = shortTs(full, this.multiDay);
      cells.adjusted.title = full;
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
      const label = this.sourceLabels.get(row.source_id, name === undefined ? undefined : baseName(name));
      cells.source.textContent = label.text;
      cells.source.title = label.sensitive ? label.full + " (" + S.sourceSensitive + ")" : label.full;
      if (label.sensitive) {
        cells.source.dataset.badge = S.gridSensitiveBadge;
        // The visible text omits ".sensitive" (the S badge is a CSS pseudo-element): name the state for screen readers.
        cells.source.setAttribute("aria-label", cells.source.title);
      } else {
        delete cells.source.dataset.badge;
        cells.source.removeAttribute("aria-label");
      }
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
