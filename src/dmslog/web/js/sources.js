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
    this.loadPartial = null; // loader of partial-open.js for the Add files dialog; null = the dialog's own import()
    this.loadEmpty = () => import("./case-empty.js"); // replaced by tests
    this.menuReady = null; // promise of the case menu (ui-menu.js loads on first use)
    this.loadMenu = () => import("./ui-menu.js"); // replaced by tests
    this.run = null; // {caseId, first, resume} of the job whose progress is watched (the window of first_ready, the follow-up)
    this.starting = false; // the typed path is being validated/started: a second submit is ignored
    this.resuming = false; // Resume now was pressed and its job has not ended
    this.pendingToken = 0; // one per pending check (and per case switch): an older answer is dropped
    this.pendingItems = []; // the last pending list shown by the banner (normalized)
    this.pendingAnnounced = null; // case whose pending work was already announced in the status bar
    this.resumeUi = null; // the "Resume indexing" banner (resume-banner.js), after the first pending work
    this.resumeMod = null; // the loaded resume-banner.js
    this.resumeReady = null; // promise of its load; reset by a failed load so the next check tries again
    this.loadResume = () => import("./resume-banner.js"); // replaced by tests
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
          getCaseId: () => this.caseId,
          getScope: () => this.currentScope(),
          onMessage: (text, isError) => this.handlers.onMessage(text, isError),
          ...(this.loadPartial ? { loadPartial: this.loadPartial } : {}),
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
      this.pendingToken += 1; // a pending answer of the old case must not show in the new one
      this.hideResume();
      if (this.partialSlot()) {
        this.partialSlot().reset(); // the window, the preview and the chosen sources belong to the case
      }
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
    if (caseId !== null && this.caseId === caseId) {
      this.checkPending(caseId); // best effort, not awaited: the offer to resume appears when it answers
    }
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
      const partial = await this.preparePartial();
      if (partial === null || this.caseId !== boundCase) {
        return; // a field to fix (reported), or the case changed while the options were checked
      }
      result = await this.addFiles.submitAdd({
        openPath: (path) => {
          const scope = this.currentScope();
          return this.runCollection(path, scope, boundCase, partial.firstFor(path, scope), {
            onFirstReady: () => this.afterFirstReady(boundCase),
          });
        },
      }, boundCase);
    } finally {
      this.setQueueBusy(false);
    }
    if (result && result.state === "ok" && this.caseId === boundCase) {
      this.closeDialog("add");
      if (this.partialSlot()) {
        this.partialSlot().reset();
      }
    }
  }

  // The "Open only part first" section of the Add files dialog, once the dialogs are mounted (null before, or when a
  // test mounts a dialog without it).
  partialSlot() {
    return this.dialogs && this.dialogs.partial ? this.dialogs.partial : null;
  }

  // Open only part first: resolves to {firstFor(path, scope)} (null when nothing narrows that file), or to null when the
  // user has to fix something first (shown next to the field and in the status bar). Without the option: no narrowing.
  async preparePartial() {
    const slot = this.partialSlot();
    if (!slot || !slot.wanted()) {
      return NO_PARTIAL;
    }
    const ready = await slot.ready();
    if (!ready) {
      this.handlers.onMessage(S.poNotReady, true);
      return null;
    }
    const check = slot.validate();
    if (!check.ok) {
      this.handlers.onMessage(check.message, true);
      return null;
    }
    return slot.snapshot() || NO_PARTIAL;
  }

  // The first part of the last file of a queue is ready: the dialog gets out of the way so the viewer can be used while
  // the rest indexes. With more files still to open it stays, showing their progress.
  afterFirstReady(caseId) {
    const more = this.addFiles && this.addFiles.state.items.some((item) => item.status === "pending");
    if (!more && this.caseId === caseId) {
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
  // expectedCaseId (optional) ties the run to the case it was created for. `first` (optional): open only part first
  // ({from, to, sources}, see partial-open-model.js); hooks.onFirstReady(data) runs when its first part is ready.
  runCollection(path, scope, expectedCaseId, first = null, hooks = null) {
    return this.runJob((caseId) => api.addCollection(caseId, path, scope, first), expectedCaseId, { first, hooks });
  }

  // One job of the open case, from its start request to its done event. `start(caseId)` posts it and resolves to
  // {job_id}; a null job_id means there was nothing to run. options: {first, hooks, resume}.
  runJob(start, expectedCaseId, { first = null, hooks = null, resume = false } = {}) {
    return new Promise((resolve) => {
      if (this.caseId === null) {
        resolve({ status: "failed", message: S.needCase });
        return;
      }
      if (expectedCaseId !== undefined && expectedCaseId !== null && expectedCaseId !== this.caseId) {
        resolve({ status: "failed", message: S.cfCaseChanged });
        return;
      }
      const waiter = { jobId: null, caseId: this.caseId, resolve, seen: new Map(), firstSeen: new Map(), hooks, first, resume };
      this.waiter = waiter;
      this.watchProgress(this.caseId, { first, resume });
      let request;
      try {
        request = Promise.resolve(start(waiter.caseId));
      } catch (err) {
        request = Promise.reject(err); // a start that throws is a refused start, never an unhandled rejection
      }
      request.then((started) => {
        if (waiter.cancelled) {
          return;
        }
        waiter.jobId = started ? started.job_id : null;
        if (waiter.jobId === null && resume) {
          // 200 {job_id: null}: nothing is pending any more
          this.waiter = null;
          this.closeEvents();
          this.handlers.onMessage(S.poResumeNothing, false);
          resolve({ status: "ok", nothing: true });
          return;
        }
        this.handlers.onMessage(resume ? S.poResumeStarted : S.collectionStarted, false);
        if (waiter.jobId !== null && waiter.firstSeen.has(waiter.jobId)) {
          this.handleFirstReady(waiter.firstSeen.get(waiter.jobId), waiter);
        }
        if (waiter.jobId !== null && waiter.seen.has(waiter.jobId)) {
          this.finishJob(waiter.seen.get(waiter.jobId));
        }
      }).catch((err) => {
        if (waiter.cancelled) {
          return;
        }
        this.waiter = null;
        this.closeEvents();
        if (err && err.code === "bad_first" && this.partialSlot()) {
          this.partialSlot().showServerError(err.message); // the server refused the window or the sources
        }
        resolve(refusalResult(err, {
          duplicate: S.cfDuplicateCollection, busy: S.cfBusy,
        }));
      });
    });
  }

  async finishJob(data) {
    const waiter = this.waiter;
    const run = this.run;
    this.waiter = null;
    this.closeEvents();
    const left = data.status === "error" ? 0 : partialLeftOf(data);
    const unmatched = data.status === "error" ? [] : unmatchedNamesOf(data);
    const notes = [];
    if (unmatched.length > 0) {
      notes.push(unmatchedText(unmatched));
    }
    if (left > 0) {
      notes.push(format(S[pluralKey("poLeft", left)], { count: left.toLocaleString("en-US") }));
    }
    if (data.status === "error") {
      this.handlers.onMessage(S.progressFailed + (data.message ? ": " + data.message : ""), true);
    } else {
      this.handlers.onMessage([doneText(data.records), ...notes].join(" "), left > 0);
    }
    const caseId = waiter ? waiter.caseId : this.caseId;
    await this.refreshSources();
    this.handlers.onDone();
    // A partial opening or a resume may leave work: ask the server (the banner follows the answer).
    if (caseId !== null && this.caseId === caseId && (left > 0 || (this.resumeUi && this.resumeUi.isVisible()) || (run && (run.first || run.resume)) || (waiter && (waiter.first || waiter.resume)))) {
      this.checkPending(caseId);
    }
    if (waiter) {
      const note = notes.join(" ");
      if (data.status === "error") {
        waiter.resolve({ status: "failed", code: data.code || "", message: data.message || S.progressFailed });
      } else {
        waiter.resolve(note === "" ? { status: "ok" } : { status: "ok", message: note });
      }
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
    if (this.starting) {
      return; // a second submit while the first one is still being checked or posted
    }
    const path = this.refs.pathInput.value.trim();
    if (path === "") {
      setFieldError(this.refs.pathInput, S.needPath);
      return;
    }
    setFieldError(this.refs.pathInput, null);
    const caseId = this.caseId;
    const scope = this.refs.scopeSelect.value === "all" ? "all" : "default";
    this.starting = true;
    try {
      const partial = await this.preparePartial();
      if (partial === null || this.caseId !== caseId) {
        return; // a field to fix (reported), or the case changed while the options were checked
      }
      const first = partial.firstFor(path, scope);
      this.watchProgress(caseId, { first, resume: false });
      try {
        await api.addCollection(caseId, path, scope, first);
        this.handlers.onMessage(S.collectionStarted, false);
        this.refs.pathInput.value = "";
        this.closeDialog("add"); // the job runs on; its progress is in the status bar
        if (this.partialSlot()) {
          this.partialSlot().reset();
        }
      } catch (err) {
        this.closeEvents();
        if (err instanceof ApiError && err.code === "bad_first" && this.partialSlot()) {
          this.partialSlot().showServerError(err.message);
          this.handlers.onMessage(err.message, true);
        } else if (err instanceof ApiError && err.status === 409) {
          this.handlers.onMessage(conflictMessage(err), true);
        } else if (err instanceof ApiError && PATH_FIELD_ERRORS.has(err.code)) {
          setFieldError(this.refs.pathInput, err.message); // "Path does not exist: ..." under the field
        } else {
          this.handlers.onMessage(err.message, true);
        }
      }
    } finally {
      this.starting = false;
    }
  }

  // run: {first, resume} of the job being watched (the window of first_ready, the follow-up after done).
  watchProgress(caseId, run = null) {
    this.closeEvents();
    if (typeof EventSource !== "function") {
      return;
    }
    this.run = run ? { caseId, first: run.first || null, resume: Boolean(run.resume) } : null;
    this.reportProgress({ value: null, max: null, text: "" });
    const events = new EventSource(api.progressUrl(caseId));
    this.events = events;
    const shown = { firstJob: undefined }; // the job whose first_ready was already handled on this stream
    events.addEventListener("progress", (event) => {
      const data = parseEvent(event);
      if (!data) {
        return;
      }
      if (data.status === "first_ready") {
        this.onFirstReadyEvent(data, events, caseId, shown);
        return;
      }
      const total = safeCount(data.bytes_total);
      this.reportProgress({
        value: total > 0 ? Math.min(safeCount(data.bytes_done), total) : null,
        max: total > 0 ? total : null,
        text: stagedIndexingText(data),
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

  // progress with status "first_ready": the first part is indexed, derived and correlated. Only the current stream and
  // this run's job count (a replay of an earlier job is ignored), and each job once.
  onFirstReadyEvent(data, events, caseId, shown) {
    if (this.events !== events || this.caseId !== caseId) {
      return;
    }
    const waiter = this.waiter;
    if (waiter) {
      if (waiter.jobId === null) {
        waiter.firstSeen.set(data.job_id, data); // the start request has not answered yet: decided when it does
        return;
      }
      if (waiter.jobId !== data.job_id) {
        return;
      }
    }
    if (shown.firstJob !== undefined && shown.firstJob === data.job_id) {
      return;
    }
    shown.firstJob = data.job_id;
    this.handleFirstReady(data, waiter);
  }

  // The status bar says the rest goes on in the background; sources, grid, timeline and panels reload as at the end of a
  // job (main.js narrows the view to the window); the dialog of the last file steps aside.
  async handleFirstReady(data, waiter) {
    const caseId = this.caseId;
    const events = this.events;
    const run = this.run;
    const records = safeCount(data.records);
    try {
      this.reportProgress({ value: null, max: null, text: S.poStatusRestBare });
      this.handlers.onMessage(format(S[pluralKey("poFirstReady", records)], { records: records.toLocaleString("en-US") }), false);
      await this.refreshSources(true);
      if (this.caseId !== caseId || this.events !== events) {
        return; // the case changed, or the job ended meanwhile (done reloads everything)
      }
      const first = (waiter && waiter.first) || (run && run.first) || null;
      const info = {
        from: first && Number.isSafeInteger(first.from) ? first.from : null,
        to: first && Number.isSafeInteger(first.to) ? first.to : null,
        sources: first && Array.isArray(first.sources) ? first.sources.slice() : null,
        records,
      };
      if (typeof this.handlers.onFirstReady === "function") {
        await this.handlers.onFirstReady(info);
      } else {
        await this.handlers.onDone();
      }
    } catch (err) {
      this.handlers.onMessage(err && err.message ? err.message : String(err), true);
    }
    if (waiter && waiter.hooks && typeof waiter.hooks.onFirstReady === "function") {
      waiter.hooks.onFirstReady(data);
    }
  }

  // -- pending work of earlier partial openings (GET .../pending) and Resume now -----------------------------------

  // Asks the server what an earlier partial opening left. Best effort: a failure shows nothing. Only the latest check
  // counts (token) and only for the case that is still open.
  async checkPending(caseId) {
    if (caseId === null || caseId === undefined) {
      return;
    }
    const token = ++this.pendingToken;
    let data;
    try {
      data = await api.partial.pending(caseId);
    } catch (err) {
      return;
    }
    if (token !== this.pendingToken || this.caseId !== caseId) {
      return;
    }
    const list = data && Array.isArray(data.pending) ? data.pending : [];
    if (list.length === 0) {
      this.hideResume();
      return;
    }
    const ui = await this.ensureResume();
    if (!ui || token !== this.pendingToken || this.caseId !== caseId) {
      return;
    }
    this.pendingItems = this.resumeMod.normalizePending(data).items;
    const busy = Boolean(this.waiter) || this.resuming;
    const running = Boolean(data.running);
    if (busy) {
      ui.setState(this.resuming ? "busy" : "running", this.pendingItems, () => this.resumeFallback());
    } else if (running) {
      ui.setState("running", this.pendingItems, () => this.resumeFallback());
      if (!this.events) {
        this.watchProgress(caseId, { first: null, resume: true }); // a job runs: show its progress, end with its done
      }
    } else {
      ui.setState("ready", this.pendingItems, () => this.resumeFallback());
    }
    if (this.pendingAnnounced !== caseId) {
      this.pendingAnnounced = caseId;
      this.handlers.onMessage(S.poPendingMessage, false);
    }
  }

  // Loads resume-banner.js once and mounts the banner; a failed load is reported and tried again at the next check.
  ensureResume() {
    if (this.resumeUi) {
      return Promise.resolve(this.resumeUi);
    }
    if (!this.resumeReady) {
      const ready = this.loadResume().then((mod) => {
        const anchor = this.refs.caseHeader || this.refs.caseRow || null;
        const parent = anchor && anchor.parentNode ? anchor.parentNode : (this.refs.sourceList ? this.refs.sourceList.parentNode : null);
        if (!parent) {
          throw new Error(S.poResumeLoadFailed);
        }
        this.resumeMod = mod;
        this.resumeUi = mod.mountResumeBanner({
          parent,
          before: anchor && anchor.parentNode === parent ? anchor.nextSibling : null,
          onResume: () => this.resumeIndexing(),
        });
        return this.resumeUi;
      });
      this.resumeReady = ready;
      ready.catch((err) => {
        if (this.resumeReady === ready) {
          this.resumeReady = null;
        }
        this.handlers.onMessage(S.poResumeLoadFailed, true);
      });
    }
    return this.resumeReady.catch(() => null);
  }

  hideResume() {
    if (this.resumeUi) {
      this.resumeUi.setState("hidden", [], () => this.resumeFallback());
    }
  }

  // Where the focus goes when the banner (and its button) goes away: the case header's button, else the sources list.
  resumeFallback() {
    for (const node of [this.refs.caseAddFiles, this.refs.caseMenu, this.refs.caseSelect]) {
      if (node && typeof node.focus === "function" && !isHiddenNode(node)) {
        return node;
      }
    }
    return null;
  }

  // Resume now: POST .../resume, then follow the job like any other. One at a time.
  async resumeIndexing() {
    const caseId = this.caseId;
    if (caseId === null || this.resuming) {
      return;
    }
    if (this.queueBusy || this.waiter) {
      this.handlers.onMessage(S.poResumeBusy, true);
      return;
    }
    this.resuming = true;
    this.pendingToken += 1; // a pending answer already on its way must not flip the banner back
    if (this.resumeUi) {
      this.resumeUi.setState("busy", this.pendingItems, () => this.resumeFallback());
    }
    let result;
    try {
      result = await this.runJob((id) => api.partial.resume(id), caseId, { resume: true });
    } finally {
      this.resuming = false;
    }
    if (this.caseId !== caseId) {
      return; // another case is open now: its own check decides what to show
    }
    if (result.status !== "ok") {
      if (result.code === "job_running") {
        this.handlers.onMessage(S.poResumeBusy, true);
        if (!this.events) {
          this.watchProgress(caseId, { first: null, resume: true });
        }
        if (this.resumeUi) {
          this.resumeUi.setState("running", this.pendingItems, () => this.resumeFallback());
        }
        return;
      }
      this.handlers.onMessage(format(S.poResumeFailed, { message: result.message || S.progressFailed }), true);
    }
    this.checkPending(caseId); // the banner goes away when nothing is left, or comes back with what is
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

// Progress text of a two-stage opening (stage "first" / "rest"); the plain text without a stage.
// "Indexing first part… core.log: 1,200 records" / "First part ready — indexing the rest in the background: core.log, 1,200 records".
export function stagedIndexingText(data) {
  const stage = data && data.stage;
  if (stage !== "first" && stage !== "rest") {
    return indexingText(data);
  }
  const n = safeCount(data && data.records);
  const records = n.toLocaleString("en-US");
  const source = data && hasText(data.source) ? String(data.source) : "";
  if (stage === "first") {
    return format(S[pluralKey(source === "" ? "poStatusFirstBare" : "poStatusFirst", n)], { source, records });
  }
  return source === "" ? S.poStatusRestBare : format(S[pluralKey("poStatusRest", n)], { source, records });
}

// done.partial_left as a safe count; done.first_unmatched as a list of non-empty names.
export function partialLeftOf(data) {
  return safeCount(data && data.partial_left);
}

export function unmatchedNamesOf(data) {
  const list = data && Array.isArray(data.first_unmatched) ? data.first_unmatched : [];
  return list.filter((name) => typeof name === "string" && name !== "");
}

const UNMATCHED_SHOWN = 3;

// "1 source chosen to open first was not found among the indexed sources: a.log." (names beyond 3 are counted).
export function unmatchedText(names) {
  const shown = names.slice(0, UNMATCHED_SHOWN).join(", ");
  const rest = names.length - UNMATCHED_SHOWN;
  const list = rest > 0 ? shown + " " + format(S.poAndMore, { count: rest.toLocaleString("en-US") }) : shown;
  return format(S[pluralKey("poUnmatched", names.length)], { count: names.length.toLocaleString("en-US"), names: list });
}

// True when the node or an ancestor has the `hidden` attribute (a hidden element cannot take the focus).
function isHiddenNode(node) {
  for (let item = node; item; item = item.parentNode || item.parent) {
    if (item.hidden === true) {
      return true;
    }
  }
  return false;
}

const NO_PARTIAL = Object.freeze({ firstFor: () => null });

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
