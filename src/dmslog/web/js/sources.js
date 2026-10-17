// Sources panel: case selection, opening a collection with live progress (SSE), source list.
import { ApiError, api } from "./api.js";
import { takeCases, takeSources } from "./prefetch.js";
import { filterSources, highlightParts, isFiltering } from "./source-filter-model.js";
import { refusalResult } from "./case-files-model.js";
import { setFieldError } from "./field-error.js";
import { S, countNoun, format, pluralKey } from "./strings.js";

export function conflictMessage(error) {
  const key = "conflict_" + error.code;
  return Object.prototype.hasOwnProperty.call(S, key) ? S[key] : S.conflict_generic + " " + error.message;
}

const PATH_FIELD_ERRORS = new Set(["path_not_found", "path_not_absolute", "not_a_directory", "permission_denied", "path_unreadable"]);

export class SourcesPanel {
  constructor(refs, handlers) {
    this.refs = refs;
    this.handlers = handlers; // {onCase(caseId), onSources(sources, quiet), onSelection(ids), onDone(), onMessage(text, isError),
    // onProgress({value, max, text} | null): indexing progress for the status bar; onWatching(bool): serve --watch is on}
    this.caseId = null;
    this.sources = [];
    this.selected = new Set();
    this.events = null;
    this.query = ""; // source filter text (see source-filter.js)
    this.onRendered = null; // (visible, total) after each render of the list
    this.caseFiles = null; // set by main.js: the "files of the new case" list (case-files.js)
    this.addFiles = null; // set by main.js: the "add files to this case" list (case-files.js, mode "add")
    this.queueBusy = false; // the files of a new case are being opened: case switch and Open form are blocked
    this.waiter = null; // {jobId, caseId, resolve, seen}: a runCollection() waiting for its job's done event
    this.caseNames = new Map(); // case id -> name, for the fixed case header
    this.rows = new Map(); // source id -> {li, box, label, text, name, meta, mark, badge, sig}: the rows on screen
    this.order = []; // source ids in the order of the rows on screen
    this.emptyNode = null; // the "no sources" / "no match" line, when shown
    this.tabStop = null; // id of the row whose checkbox is the list's one tab stop (roving tabindex)
    this.settled = false; // the first selectCase() ran: until then the empty state is not shown, so the page does not flash
    this.recentCases = []; // [{id, name, sources, records}] of the last /cases answer, for the empty state
    this.dialogs = null; // the New case / Add files dialogs (case-dialogs.js), after their first use
    this.dialogsReady = null; // promise of their mount; reset by a failed load so the next use tries again
    this.dialogWanted = null; // the last request to open one ({kind, opener, focusPath}): older ones are dropped
    this.focusEmpty = false; // a case was just closed: the empty state takes the focus
    this.empty = null; // the empty state without a case (case-empty.js)
    this.emptyReady = null;
    this.loadDialogs = () => import("./case-dialogs.js"); // replaced by tests
    this.loadEmpty = () => import("./case-empty.js"); // replaced by tests
    this.menuReady = null; // promise of the case menu (ui-menu.js loads on first use)
    this.loadMenu = () => import("./ui-menu.js"); // replaced by tests
    this.bindList();
    this.bindHeader();
    refs.caseSelect.addEventListener("change", () => {
      if (this.queueBusy) {
        refs.caseSelect.value = this.caseId || "";
        this.handlers.onMessage(S.cfQueueBusy, true);
        return;
      }
      this.selectCase(refs.caseSelect.value || null);
    });
    refs.newCaseForm.addEventListener("submit", (event) => {
      event.preventDefault();
      this.createCase();
    });
    if (refs.pathInput) {
      refs.pathInput.addEventListener("input", () => setFieldError(refs.pathInput, null));
    }
    if (refs.newCaseName) {
      refs.newCaseName.addEventListener("input", () => setFieldError(refs.newCaseName, null));
    }
    refs.openForm.addEventListener("submit", (event) => {
      event.preventDefault();
      this.openCollection();
    });
    this.applyLayout();
  }

  // -- case header and layout ----------------------------------------------------------------

  bindHeader() {
    const { caseAddFiles, caseMenu } = this.refs;
    if (caseAddFiles) {
      caseAddFiles.removeAttribute("aria-controls"); // the form is a dialog now, not a part of the column
      caseAddFiles.setAttribute("aria-haspopup", "dialog");
      caseAddFiles.addEventListener("click", () => this.revealAddFiles(false, caseAddFiles));
    }
    if (caseMenu) {
      caseMenu.addEventListener("click", (event) => this.openCaseMenu(event));
    }
  }

  // With a case open the side column shows its fixed header and the sources; without one the page body gets the
  // `no-case` class (css/case-dialogs.css hides the column, the detail, the filters and the timeline) and the empty
  // state takes the middle. New case and Add files are dialogs (case-dialogs.js), never part of the column.
  applyLayout() {
    const refs = this.refs;
    const open = this.caseId !== null;
    if (typeof document !== "undefined" && document.body && document.body.classList) {
      document.body.classList.toggle("no-case", this.settled && !open);
    }
    this.syncCaseEmpty();
    if (!refs.caseHeader) {
      return;
    }
    refs.caseHeader.hidden = !open;
    if (refs.caseHeaderName) {
      refs.caseHeaderName.textContent = open ? this.caseNames.get(this.caseId) || "" : "";
      refs.caseHeaderName.title = refs.caseHeaderName.textContent;
    }
    if (refs.caseRow) {
      refs.caseRow.hidden = open;
    }
  }

  // The empty state exists only while no case is open; it is loaded on demand and a failed load is tried again at the
  // next layout pass. While it is shown the dialogs are preloaded, so New case… opens without waiting for a module.
  syncCaseEmpty() {
    const host = this.refs.emptyHost;
    if (!host) {
      return;
    }
    const wanted = this.settled && this.caseId === null;
    if (!wanted) {
      if (this.empty && this.empty.isShown()) {
        const hadFocus = this.empty.contains(document.activeElement);
        this.empty.hide(); // the button that had the focus is hidden now: the grid takes it, never the page behind
        if (hadFocus) {
          this.focusGrid();
        }
      }
      return;
    }
    this.ensureDialogs(true);
    if (this.empty) {
      this.empty.setCases(this.recentCases);
      this.empty.show();
      if (this.focusEmpty) {
        this.focusEmpty = false;
        this.empty.focusPrimary();
      }
      return;
    }
    if (!this.emptyReady) {
      this.emptyReady = this.loadEmpty().then((mod) => {
        this.empty = mod.mountCaseEmpty({
          host,
          onNewCase: (opener) => this.revealNewCase(opener),
          onOpenPath: (opener) => this.revealNewCase(opener, true),
          onSelectCase: (id) => this.pickCase(id),
        });
        this.syncCaseEmpty();
      });
      this.emptyReady.catch((err) => {
        this.emptyReady = null; // tried again at the next layout pass
        this.handlers.onMessage(S.cdLoadFailed + (err && err.message ? " " + err.message : ""), true);
      });
    }
  }

  focusGrid() {
    const target = typeof document !== "undefined" && document.getElementById ? document.getElementById("grid-viewport") : null;
    if (target && typeof target.focus === "function") {
      target.focus();
    }
  }

  // A recent case chosen in the empty state.
  pickCase(id) {
    if (this.queueBusy) {
      this.handlers.onMessage(S.cfQueueBusy, true);
      return Promise.resolve();
    }
    return this.selectCase(id);
  }

  // -- dialogs (New case, Add files) ---------------------------------------------------------

  // Loads and mounts case-dialogs.js once; concurrent calls share the promise, a failed load resets it. `quiet` (a
  // preload nobody asked for) does not report a failure.
  ensureDialogs(quiet = false) {
    if (this.dialogs) {
      return Promise.resolve(this.dialogs);
    }
    if (!this.dialogsReady) {
      const refs = this.refs;
      const ready = this.loadDialogs().then((mod) => {
        this.dialogs = mod.mountCaseDialogs({
          refs,
          caseFiles: this.caseFiles,
          addFiles: this.addFiles,
          fallback: () => (typeof document !== "undefined" && document.getElementById ? document.getElementById("grid-viewport") : null),
        });
        return this.dialogs;
      });
      this.dialogsReady = ready;
      ready.catch(() => {
        if (this.dialogsReady === ready) {
          this.dialogsReady = null;
        }
      });
    }
    return this.dialogsReady.catch((err) => {
      if (!quiet) {
        this.handlers.onMessage(S.cdLoadFailed + (err && err.message ? " " + err.message : ""), true);
      }
      return null;
    });
  }

  // Opens a dialog. Only the last request counts: a slow load never opens a dialog the user has since replaced.
  openDialog(kind, opener, focusPath) {
    const request = { kind, opener: opener || (typeof document !== "undefined" ? document.activeElement : null), focusPath: Boolean(focusPath) };
    this.dialogWanted = request;
    return this.ensureDialogs().then((dialogs) => {
      if (!dialogs || this.dialogWanted !== request) {
        return;
      }
      this.dialogWanted = null;
      if (kind === "add" && this.caseId === null) {
        this.handlers.onMessage(S.needCase, true);
        return;
      }
      dialogs.open(kind, request.opener, { focusPath: request.focusPath });
    });
  }

  closeDialog(kind) {
    if (this.dialogWanted && this.dialogWanted.kind === kind) {
      this.dialogWanted = null;
    }
    if (this.dialogs) {
      this.dialogs.close(kind);
    }
  }

  closeDialogs() {
    this.dialogWanted = null;
    if (this.dialogs) {
      this.dialogs.closeAll();
    }
  }

  revealNewCase(opener = null, focusPath = false) {
    return this.openDialog("new", opener, focusPath);
  }

  revealAddFiles(focusPath = false, opener = null) {
    return this.openDialog("add", opener, focusPath);
  }

  hideAdd() {
    this.closeDialog("add");
  }

  caseMenuItems() {
    return [
      { id: "case-new", label: S.caseMenuNew, run: () => this.revealNewCase(this.refs.caseMenu) },
      { id: "case-open-path", label: S.caseMenuOpenPath, run: () => this.revealAddFiles(true, this.refs.caseMenu) },
    ];
  }

  // The first click loads ui-menu.js, builds the menu and opens it; once built, the menu toggles itself on click.
  openCaseMenu(event) {
    if (this.menuReady) {
      return;
    }
    const where = event && event.detail === 0 ? "first" : null; // keyboard: first item; pointer: the menu itself
    const trigger = this.refs.caseMenu;
    this.menuReady = this.loadMenu().then((mod) => {
      const menu = mod.createMenu({ trigger, getItems: () => this.caseMenuItems(), label: S.caseMenuLabel });
      menu.open(where);
      return menu;
    });
    this.menuReady.catch((err) => {
      this.menuReady = null; // a failed load is tried again on the next click
      this.handlers.onMessage(err && err.message ? err.message : String(err), true);
    });
  }

  // -- cases --------------------------------------------------------------------------------

  async loadCases(preferredId) {
    // The first call of the opening was started by boot.js (prefetch.js); later ones ask the API.
    const data = await (takeCases(() => api.cases()) || api.cases());
    if (this.handlers.onWatching) {
      this.handlers.onWatching(Boolean(data.watching));
    }
    const select = this.refs.caseSelect;
    select.textContent = "";
    const none = document.createElement("option");
    none.value = "";
    none.textContent = S.caseNone;
    select.appendChild(none);
    this.caseNames = new Map(data.cases.map((item) => [item.id, item.name]));
    this.recentCases = data.cases;
    for (const item of data.cases) {
      const option = document.createElement("option");
      option.value = item.id;
      option.textContent = item.name;
      select.appendChild(option);
    }
    const wanted = preferredId || (data.cases.length > 0 ? data.cases[0].id : null);
    select.value = wanted || "";
    await this.selectCase(wanted || null);
  }

  setQueueBusy(busy) {
    this.queueBusy = busy;
    for (const node of [this.refs.caseSelect, this.refs.openForm]) {
      if (busy) {
        node.setAttribute("aria-disabled", "true");
      } else {
        node.removeAttribute("aria-disabled");
      }
    }
  }

  // A waiting runCollection() belongs to one case: if the case changes, it ends with an error.
  cancelWaiter(message) {
    const waiter = this.waiter;
    if (!waiter) {
      return;
    }
    this.waiter = null;
    waiter.cancelled = true;
    waiter.resolve({ status: "failed", message });
  }

  async selectCase(caseId) {
    if (this.waiter && this.waiter.caseId !== caseId) {
      this.cancelWaiter(S.cfCaseChanged);
    }
    this.closeEvents();
    if (caseId !== this.caseId) {
      if (this.addFiles) {
        this.addFiles.state.resetResults(); // results belong to the case they were opened in
      }
      this.resetRows(); // source ids are per case: no row of the old case is reused
      // Every switch drops the pending requests and closes Add files (it belongs to the case it was opened in). Only the
      // New case dialog of a case being created stays: its progress, results and Retry must remain in view.
      const keepNew = this.queueBusy;
      if (!(keepNew && this.dialogWanted && this.dialogWanted.kind === "new")) {
        this.dialogWanted = null;
      }
      if (this.dialogs) {
        this.dialogs.close("add");
        if (!keepNew) {
          this.dialogs.close("new");
        }
      }
      this.focusEmpty = caseId === null && this.caseId !== null; // a case was closed: the empty state takes the focus
    }
    this.caseId = caseId;
    this.settled = true;
    this.applyLayout();
    this.selected.clear();
    this.refs.caseSelect.value = caseId || "";
    this.handlers.onCase(caseId);
    await this.refreshSources();
  }

  async createCase() {
    if (this.caseFiles) {
      if (this.queueBusy) {
        this.handlers.onMessage(S.cfQueueBusy, true);
        return;
      }
      let boundCase = null;
      let result = null;
      this.setQueueBusy(true); // the dialog holds the progress, the per-file results and Retry: it stays open
      try {
        result = await this.caseFiles.submit({
          createCase: (name) => api.createCase(name),
          selectCase: async (id) => {
            await this.loadCases(id);
            boundCase = id;
          },
          openPath: (path) => this.runCollection(path, this.currentScope(), boundCase),
        });
      } finally {
        this.setQueueBusy(false);
      }
      if (result && result.state === "ok") {
        this.closeDialog("new"); // all files opened: done. Anything else stays in view with its per-file results and Retry
      }
      return;
    }
    const name = this.refs.newCaseName.value.trim();
    if (name === "") {
      setFieldError(this.refs.newCaseName, S.cfNeedName);
      return;
    }
    setFieldError(this.refs.newCaseName, null);
    try {
      const created = await api.createCase(name);
      this.refs.newCaseName.value = "";
      await this.loadCases(created.id);
      this.closeDialog("new");
    } catch (err) {
      setFieldError(this.refs.newCaseName, err.message); // the name was refused (duplicate, invalid): under the field
    }
  }

  // Opens every file of the "add files" list in the open case, one after the other.
  async addFilesToCase() {
    if (!this.addFiles) {
      return;
    }
    if (this.queueBusy) {
      this.handlers.onMessage(S.cfQueueBusy, true);
      return;
    }
    if (this.caseId === null) {
      this.handlers.onMessage(S.needCase, true);
      return;
    }
    const boundCase = this.caseId;
    this.setQueueBusy(true);
    let result = null;
    try {
      result = await this.addFiles.submitAdd({
        openPath: (path) => this.runCollection(path, this.currentScope(), boundCase),
      }, boundCase);
    } finally {
      this.setQueueBusy(false);
    }
    if (result && result.state === "ok" && this.caseId === boundCase) {
      this.closeDialog("add");
    }
  }

  // -- sources ------------------------------------------------------------------------------

  async refreshSources(quiet = false) {
    if (this.caseId === null) {
      this.sources = [];
      this.renderSources();
      this.handlers.onSources(this.sources, quiet);
      return;
    }
    try {
      const data = await (takeSources(this.caseId, () => api.sources(this.caseId)) || api.sources(this.caseId));
      this.sources = data.sources;
    } catch (err) {
      this.sources = [];
      this.handlers.onMessage(err.message, true);
    }
    this.selected = new Set([...this.selected].filter((id) => this.sources.some((s) => s.id === id)));
    this.renderSources();
    this.handlers.onSources(this.sources, quiet);
  }

  selectedIds() {
    return [...this.selected];
  }

  // Puts a selection back (a view change that did not load goes back to the last one that did) without telling the
  // owner: it is the owner that is restoring.
  restoreSelection(ids) {
    this.selected = new Set((ids || []).filter((id) => this.sources.some((s) => s.id === id)));
    this.renderSources();
  }

  // The strip's "x" on the sources chip: nothing selected again (the owner applies the view).
  clearSelection() {
    if (this.selected.size === 0) {
      return;
    }
    this.selected = new Set();
    this.renderSources();
    this.handlers.onSelection([]);
  }

  setQuery(query) {
    this.query = String(query);
    this.renderSources();
  }

  // Keyboard: from the filter box into the list (the list has one tab stop), and Enter in the box toggles the first match.
  focusList() {
    const row = this.rows.get(this.stopId());
    if (row) {
      row.box.focus();
    }
  }

  toggleFirst() {
    const row = this.rows.get(this.order[0]);
    if (row) {
      row.box.click();
    }
  }

  // -- the list: rows reconciled by source id -----------------------------------------------

  // The id of the row a node of the list belongs to (the checkbox carries it), or null.
  idOf(node) {
    if (!node || !node.dataset || node.dataset.sourceId === undefined) {
      return null;
    }
    const id = Number(node.dataset.sourceId);
    return Number.isFinite(id) && this.rows.has(id) ? id : null;
  }

  // One listener per kind on the <ul>, never one per row.
  bindList() {
    const list = this.refs.sourceList;
    list.addEventListener("change", (event) => {
      const id = this.idOf(event.target);
      if (id === null) {
        return;
      }
      if (event.target.checked) {
        this.selected.add(id);
      } else {
        this.selected.delete(id);
      }
      this.handlers.onSelection(this.selectedIds());
    });
    list.addEventListener("keydown", (event) => {
      const id = this.idOf(event.target);
      if (id === null) {
        return;
      }
      if (event.key === "Enter") {
        event.preventDefault();
        event.target.click();
      } else if (event.key === "Escape" && this.refs.filterInput) {
        this.refs.filterInput.focus();
      } else {
        const next = listKeyTarget(this.order, id, event.key);
        if (next !== null) {
          event.preventDefault();
          this.rows.get(next).box.focus();
        }
      }
    });
    list.addEventListener("focusin", (event) => {
      const id = this.idOf(event.target);
      if (id !== null && id !== this.tabStop) {
        this.tabStop = id;
        this.syncTabStops();
      }
    });
  }

  stopId() {
    return this.rows.has(this.tabStop) ? this.tabStop : this.order.length > 0 ? this.order[0] : null;
  }

  // Roving tabindex: exactly one checkbox of the list is in the Tab order.
  syncTabStops() {
    const stop = this.stopId();
    for (const [id, row] of this.rows) {
      const want = id === stop ? "0" : "-1";
      if (row.box.getAttribute("tabindex") !== want) {
        row.box.setAttribute("tabindex", want);
      }
    }
  }

  resetRows() {
    this.refs.sourceList.textContent = "";
    this.rows = new Map();
    this.order = [];
    this.emptyNode = null;
    this.tabStop = null;
  }

  makeRow(id) {
    const li = document.createElement("li");
    const label = document.createElement("label");
    const box = document.createElement("input");
    box.type = "checkbox";
    box.dataset.sourceId = String(id);
    const text = document.createElement("span");
    text.className = "source-text";
    const name = document.createElement("span");
    name.className = "source-name";
    const count = document.createElement("span");
    count.className = "source-count";
    count.setAttribute("aria-hidden", "true"); // the meta line below says it in full
    const meta = document.createElement("span");
    meta.className = "source-meta visually-hidden"; // screen readers and the tooltip: family, pid and records
    text.appendChild(name);
    text.appendChild(count);
    text.appendChild(meta);
    label.appendChild(box);
    label.appendChild(text);
    li.appendChild(label);
    return { li, label, box, text, name, count, meta, mark: null, badge: null, sig: null };
  }

  // Writes what the row says now; a node is touched only when its text changed.
  updateRow(row, entry) {
    const { source, name: fileName, range } = entry;
    const checked = this.selected.has(source.id);
    if (row.box.checked !== checked) {
      row.box.checked = checked;
    }
    const filtering = isFiltering(this.query);
    const sig = fileName + "\u0000" + (filtering ? range.start + ":" + range.end : "");
    if (row.sig !== sig) {
      row.sig = sig;
      row.name.textContent = "";
      if (filtering) {
        for (const part of highlightParts(fileName, range)) {
          if (part.match) {
            const mark = document.createElement("mark");
            mark.textContent = part.text;
            row.name.appendChild(mark);
          } else {
            row.name.appendChild(document.createTextNode(part.text));
          }
        }
      } else {
        row.name.textContent = fileName;
      }
    }
    if (row.name.title !== source.name) {
      row.name.title = source.name;
    }
    const meta = sourceMeta(source);
    if (row.meta.textContent !== meta) {
      row.meta.textContent = meta;
      row.label.title = meta; // the family and pid do not fit on the one line
    }
    const count = safeCount(source.records).toLocaleString("en-US");
    if (row.count.textContent !== count) {
      row.count.textContent = count;
    }
    const unreadable = isUnreadable(source);
    if (unreadable) {
      if (!row.mark) {
        row.mark = document.createElement("span");
        row.mark.className = "source-unreadable";
        row.mark.textContent = "!";
        row.mark.setAttribute("role", "img");
        row.mark.setAttribute("aria-label", S.sourceUnreadable);
        row.label.insertBefore(row.mark, row.text);
      }
      const title = unreadableTitle(source);
      if (row.mark.title !== title) {
        row.mark.title = title;
      }
    } else if (row.mark) {
      row.mark.remove();
      row.mark = null;
    }
    const rowClass = unreadable ? "is-unreadable" : "";
    if (row.li.className !== rowClass) {
      row.li.className = rowClass;
    }
    if (source.sensitive && !row.badge) {
      row.badge = document.createElement("span");
      row.badge.className = "badge";
      row.badge.textContent = S.sourceSensitive;
      row.label.appendChild(row.badge);
    } else if (!source.sensitive && row.badge) {
      row.badge.remove();
      row.badge = null;
    }
  }

  syncEmpty(text) {
    const list = this.refs.sourceList;
    if (text === null) {
      if (this.emptyNode) {
        this.emptyNode.remove();
        this.emptyNode = null;
      }
      return;
    }
    if (!this.emptyNode) {
      this.emptyNode = document.createElement("li");
      this.emptyNode.className = "empty";
      list.appendChild(this.emptyNode);
    }
    if (this.emptyNode.textContent !== text) {
      this.emptyNode.textContent = text;
    }
  }

  // Same set of sources: only the texts that changed are written. A different set: only the rows that left are
  // removed and only the new ones inserted, so the focus, the scroll and the checkboxes of the others stay.
  renderSources() {
    const list = this.refs.sourceList;
    const shown = this.sources.length === 0 ? [] : filterSources(this.sources, this.query);
    const entries = [];
    const seen = new Set();
    for (const entry of shown) {
      if (!seen.has(entry.source.id)) { // an id twice would be one row
        seen.add(entry.source.id);
        entries.push(entry);
      }
    }
    const nextIds = entries.map((entry) => entry.source.id);
    this.notifyRendered(shown.length, this.sources.length);

    const active = typeof document !== "undefined" ? document.activeElement : null;
    const focusedId = this.idOf(active);
    const plan = diffIds(this.order, nextIds);
    const rescue = focusedId !== null && !seen.has(focusedId) ? neighborAfterRemoval(this.order, focusedId, seen) : null;

    for (const id of plan.removed) {
      this.rows.get(id).li.remove();
      this.rows.delete(id);
    }
    for (const entry of entries) {
      let row = this.rows.get(entry.source.id);
      if (!row) {
        row = this.makeRow(entry.source.id);
        this.rows.set(entry.source.id, row);
      }
      this.updateRow(row, entry);
    }
    if (!plan.identical) {
      const current = this.order.filter((id) => seen.has(id));
      for (let i = 0; i < nextIds.length; i += 1) {
        if (current[i] === nextIds[i]) {
          continue;
        }
        const row = this.rows.get(nextIds[i]);
        const ahead = current[i] === undefined ? null : this.rows.get(current[i]).li;
        const old = current.indexOf(nextIds[i]);
        if (old >= 0) {
          current.splice(old, 1);
        }
        if (ahead && typeof list.insertBefore === "function") {
          list.insertBefore(row.li, ahead);
        } else {
          list.appendChild(row.li);
        }
        current.splice(i, 0, nextIds[i]);
      }
    }
    this.order = nextIds;
    this.syncEmpty(this.sources.length === 0 ? S.sourcesEmpty : shown.length === 0 ? S.sourceFilterNone : null);
    this.syncTabStops();

    // A node that moved or left loses the focus: give it back to the same row, or to the nearest one.
    if (focusedId !== null) {
      const row = this.rows.get(seen.has(focusedId) ? focusedId : rescue);
      if (row) {
        if (document.activeElement !== row.box) {
          row.box.focus();
        }
      } else if (this.refs.filterInput) {
        this.refs.filterInput.focus();
      }
    }
  }

  notifyRendered(visible, total) {
    if (this.onRendered) {
      this.onRendered(visible, total);
    }
  }

  currentScope() {
    return this.refs.scopeSelect.value === "all" ? "all" : "default";
  }

  // Opens one path in the current case and resolves when its job ends (one job per case at a time):
  // {status: "ok"|"failed"|"refused", code?, message?}.
  // expectedCaseId (optional) ties the run to the case it was created for.
  runCollection(path, scope, expectedCaseId) {
    return new Promise((resolve) => {
      if (this.caseId === null) {
        resolve({ status: "failed", message: S.needCase });
        return;
      }
      if (expectedCaseId !== undefined && expectedCaseId !== null && expectedCaseId !== this.caseId) {
        resolve({ status: "failed", message: S.cfCaseChanged });
        return;
      }
      const waiter = { jobId: null, caseId: this.caseId, resolve, seen: new Map() };
      this.waiter = waiter;
      this.watchProgress(this.caseId);
      api.addCollection(waiter.caseId, path, scope).then((started) => {
        if (waiter.cancelled) {
          return;
        }
        this.handlers.onMessage(S.collectionStarted, false);
        waiter.jobId = started ? started.job_id : null;
        if (waiter.jobId !== null && waiter.seen.has(waiter.jobId)) {
          this.finishJob(waiter.seen.get(waiter.jobId));
        }
      }).catch((err) => {
        if (waiter.cancelled) {
          return;
        }
        this.waiter = null;
        this.closeEvents();
        resolve(refusalResult(err, {
          duplicate: S.cfDuplicateCollection, busy: S.cfBusy,
        }));
      });
    });
  }

  async finishJob(data) {
    const waiter = this.waiter;
    this.waiter = null;
    this.closeEvents();
    if (data.status === "error") {
      this.handlers.onMessage(S.progressFailed + (data.message ? ": " + data.message : ""), true);
    } else {
      this.handlers.onMessage(doneText(data.records), false);
    }
    await this.refreshSources();
    this.handlers.onDone();
    if (waiter) {
      waiter.resolve(data.status === "error"
        ? { status: "failed", code: data.code || "", message: data.message || S.progressFailed }
        : { status: "ok" });
    }
  }

  // -- collections and progress -------------------------------------------------------------

  async openCollection() {
    if (this.queueBusy) {
      this.handlers.onMessage(S.cfQueueBusy, true);
      return;
    }
    if (this.caseId === null) {
      this.handlers.onMessage(S.needCase, true);
      return;
    }
    const path = this.refs.pathInput.value.trim();
    if (path === "") {
      setFieldError(this.refs.pathInput, S.needPath);
      return;
    }
    setFieldError(this.refs.pathInput, null);
    const scope = this.refs.scopeSelect.value === "all" ? "all" : "default";
    this.watchProgress(this.caseId);
    try {
      await api.addCollection(this.caseId, path, scope);
      this.handlers.onMessage(S.collectionStarted, false);
      this.refs.pathInput.value = "";
      this.closeDialog("add"); // the job runs on; its progress is in the status bar
    } catch (err) {
      this.closeEvents();
      if (err instanceof ApiError && err.status === 409) {
        this.handlers.onMessage(conflictMessage(err), true);
      } else if (err instanceof ApiError && PATH_FIELD_ERRORS.has(err.code)) {
        setFieldError(this.refs.pathInput, err.message); // "Path does not exist: ..." under the field
      } else {
        this.handlers.onMessage(err.message, true);
      }
    }
  }

  watchProgress(caseId) {
    this.closeEvents();
    if (typeof EventSource !== "function") {
      return;
    }
    this.reportProgress({ value: null, max: null, text: "" });
    const events = new EventSource(api.progressUrl(caseId));
    this.events = events;
    events.addEventListener("progress", (event) => {
      const data = parseEvent(event);
      if (!data) {
        return;
      }
      const total = safeCount(data.bytes_total);
      this.reportProgress({
        value: total > 0 ? Math.min(safeCount(data.bytes_done), total) : null,
        max: total > 0 ? total : null,
        text: indexingText(data),
      });
      this.refreshThrottled();
    });
    events.addEventListener("done", async (event) => {
      const data = parseEvent(event) || {};
      if (this.waiter) {
        // A replayed done of the previous job may arrive first: only this job's counts.
        this.waiter.seen.set(data.job_id, data);
        if (this.waiter.jobId !== null && this.waiter.jobId === data.job_id) {
          await this.finishJob(data);
        }
        return;
      }
      await this.finishJob(data);
    });
    events.addEventListener("error", () => {
      // The browser retries by itself; a closed stream just stops the bar.
      if (events.readyState === 2) {
        this.reportProgress(null);
        // Only the current stream matters; CONNECTING (0) keeps the automatic reconnection.
        if (this.events === events) {
          this.events = null;
          this.cancelWaiter(S.cfStreamClosed);
        }
      }
    });
  }

  refreshThrottled() {
    if (this.refreshTimer) {
      return;
    }
    this.refreshTimer = setTimeout(() => {
      this.refreshTimer = null;
      this.refreshSources(true);
    }, 1500);
  }

  closeEvents() {
    if (this.events) {
      this.events.close();
      this.events = null;
    }
    this.reportProgress(null);
  }

  reportProgress(value) {
    if (this.handlers.onProgress) {
      this.handlers.onProgress(value);
    }
  }
}

function parseEvent(event) {
  try {
    return JSON.parse(event.data);
  } catch (err) {
    return null;
  }
}

// ---- pure helpers of the source list (no DOM; tested without a page) -----------------------------------------
// Everything from the API is untrusted text: the DOM code writes it with textContent only.

export const META_SEPARATOR = " - ";

// A count from the API as a safe non-negative integer: null, undefined, NaN, negatives and text become 0.
export function safeCount(value) {
  const n = typeof value === "number" ? value : Number(value);
  return Number.isFinite(n) && n > 0 ? Math.floor(n) : 0;
}

function hasText(value) {
  return value !== null && value !== undefined && String(value).trim() !== "";
}

// "dms-core - pid 1234 - 1,200 records"; a missing family or pid leaves its segment out (never "-" or "pid -").
// pid 0 is a real value and is shown; null, undefined and NaN are missing.
export function sourceMeta(source) {
  const parts = [];
  if (hasText(source.family)) {
    parts.push(String(source.family));
  }
  const pid = source.pid;
  if (pid !== null && pid !== undefined && pid !== "" && !(typeof pid === "number" && !Number.isFinite(pid))) {
    parts.push(format(S.sourcePid, { pid }));
  }
  parts.push(countNoun("nRecord", safeCount(source.records)));
  return parts.join(META_SEPARATOR);
}

// A source the indexer could not read ends with status "error" and the reason in `error`.
export function isUnreadable(source) {
  return Boolean(source) && source.status === "error";
}

export function unreadableTitle(source) {
  const reason = hasText(source.error) ? String(source.error) : S.sourceUnreadableNoReason;
  return format(S.sourceUnreadableTitle, { reason });
}

// Unreadable sources overall, or only those of one collection group (sources carry `collection`).
export function unreadableCount(sources, group) {
  let n = 0;
  for (const source of sources || []) {
    if (isUnreadable(source) && (group === undefined || source.collection === group)) {
      n += 1;
    }
  }
  return n;
}

// "1 file unreadable" | "3 files unreadable" | "" (none).
export function unreadableLine(count) {
  const n = safeCount(count);
  return n === 0 ? "" : format(S[pluralKey("colUnreadable", n)], { count: n.toLocaleString("en-US") });
}

// Progress text of the status bar: "Indexing core.log: 1,200 records"; without a source name the colon
// follows "Indexing" (never a text that starts with ": ").
export function indexingText(data) {
  const records = safeCount(data && data.records).toLocaleString("en-US");
  const source = data && hasText(data.source) ? String(data.source) : "";
  const base = source === "" ? "statusIndexingBare" : "statusIndexing";
  return format(S[pluralKey(base, safeCount(data && data.records))], { source, records });
}

// "Indexed 1,200 records."
export function doneText(records) {
  const n = safeCount(records);
  return format(S[pluralKey("progressDone", n)], { records: n.toLocaleString("en-US") });
}

// ---- reconciliation ---------------------------------------------------------------------------

// What changes between the ids on screen and the ids that should be on screen (both in display order).
// {removed: ids gone, added: ids new, sameOrder: the common ids keep their relative order, identical: nothing to do}.
export function diffIds(previous, next) {
  const before = new Set(previous);
  const after = new Set(next);
  const removed = previous.filter((id) => !after.has(id));
  const added = next.filter((id) => !before.has(id));
  const kept = previous.filter((id) => after.has(id));
  const keptNext = next.filter((id) => before.has(id));
  const sameOrder = kept.length === keptNext.length && kept.every((id, i) => id === keptNext[i]);
  const identical = removed.length === 0 && added.length === 0 && sameOrder;
  return { removed, added, sameOrder, identical };
}

// The row that should take the focus when the focused one leaves the list: the next one that stays, else the
// previous one that stays, else null (the caller falls back to the filter box).
export function neighborAfterRemoval(previous, removedId, stays) {
  const at = previous.indexOf(removedId);
  if (at < 0) {
    return null;
  }
  for (let i = at + 1; i < previous.length; i += 1) {
    if (stays.has(previous[i])) {
      return previous[i];
    }
  }
  for (let i = at - 1; i >= 0; i -= 1) {
    if (stays.has(previous[i])) {
      return previous[i];
    }
  }
  return null;
}

// Arrow keys inside the list: the id to focus from `current` (null when the key is not a list key).
export function listKeyTarget(ids, current, key) {
  if (ids.length === 0) {
    return null;
  }
  const at = ids.indexOf(current);
  if (key === "Home") {
    return ids[0];
  }
  if (key === "End") {
    return ids[ids.length - 1];
  }
  if (key === "ArrowDown") {
    return ids[Math.min(at + 1, ids.length - 1)];
  }
  if (key === "ArrowUp") {
    return ids[Math.max(at < 0 ? 0 : at - 1, 0)];
  }
  return null;
}
