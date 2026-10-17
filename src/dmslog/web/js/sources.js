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
    this.sourcesSeq = 0; // one per /sources query: only the latest one may publish (an older answer is stale data)
    this.caseGen = 0; // counts the case selections (A -> B -> A is three): a late window of an earlier one is dropped
    this.opSeq = 0; // ids of the operations (an opening or a resume) the panel runs: this.waiter is the current one
    this.addSession = 0; // counts the openings of the Add files dialog and the case switches: an operation started in an
    // earlier session must not touch the dialog (or the fields) of a later one
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
      if (kind === "add") {
        this.addSession += 1; // a new session of the dialog: answers to an older one are dropped
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
    this.caseGen += 1;
    if (this.waiter && this.waiter.caseId !== caseId) {
      this.cancelWaiter(S.cfCaseChanged);
    }
    this.closeEvents();
    if (caseId !== this.caseId) {
      this.pendingToken += 1; // a pending answer of the old case must not show in the new one
      this.addSession += 1;
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
      if (this.entryBusy()) {
        this.handlers.onMessage(this.queueBusy ? S.cfQueueBusy : S.cfBusy, true);
        return;
      }
      let boundCase = null;
      let result = null;
      const scope = this.currentScope(); // one scope for the whole queue, whatever the select says meanwhile
      this.setQueueBusy(true); // the dialog holds the progress, the per-file results and Retry: it stays open
      try {
        result = await this.caseFiles.submit({
          createCase: (name) => api.createCase(name),
          selectCase: async (id) => {
            await this.loadCases(id);
            boundCase = id;
          },
          openPath: (path) => this.runCollection(path, scope, boundCase),
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

  // True while an opening, a queue of openings or a resume owns the panel: a second one must wait (one job per case, and
  // an operation never takes the progress stream of another).
  entryBusy() {
    return this.queueBusy || this.starting || this.resuming || Boolean(this.waiter);
  }

  // Opens every file of the "add files" list in the open case, one after the other.
  async addFilesToCase() {
    if (!this.addFiles) {
      return;
    }
    if (this.entryBusy()) {
      this.handlers.onMessage(this.queueBusy ? S.cfQueueBusy : S.cfBusy, true);
      return;
    }
    if (this.caseId === null) {
      this.handlers.onMessage(S.needCase, true);
      return;
    }
    const boundCase = this.caseId;
    const session = this.addSession;
    const scope = this.currentScope(); // frozen with the selection: every file of the queue opens with it
    // The files this action opens: the list (not those already opened) and the path typed in the list's field. The
    // path of the "Folder or single file" field belongs to the other action.
    const paths = this.addFiles.state.items.filter((item) => item.status !== "ok").map((item) => item.path);
    const typed = this.addFiles.pathInput ? this.addFiles.pathInput.value.trim() : "";
    if (typed !== "") {
      paths.push(typed);
    }
    this.setQueueBusy(true);
    let result = null;
    try {
      const partial = await this.preparePartial(paths, scope);
      if (partial === null || this.caseId !== boundCase || session !== this.addSession) {
        return; // a field to fix (reported), or the case or the dialog changed while the options were checked
      }
      result = await this.addFiles.submitAdd({
        openPath: async (path) => {
          const outcome = await this.runCollection(path, scope, boundCase, partial.firstFor(path), {
            onFirstReady: () => this.afterFirstReady(boundCase, session),
          });
          // The server refused the window or the sources: shown in the section, but only of the dialog session that sent it.
          const err = outcome.error;
          if (err && err.code === "bad_first" && this.caseId === boundCase && session === this.addSession && this.partialSlot()) {
            this.partialSlot().showServerError(err.message);
          }
          return outcome;
        },
      }, boundCase);
    } finally {
      this.setQueueBusy(false);
    }
    if (result && result.state === "ok" && this.caseId === boundCase && session === this.addSession) {
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

  // Open only part first, for the files `paths` that the action about to start will open, with `scope` (frozen by the
  // action). Resolves to {scope, firstFor(path)} (null when nothing narrows that file), or to null when the user has to
  // fix something first (shown next to the field and in the status bar). Without the option: no narrowing.
  async preparePartial(paths, scope) {
    const slot = this.partialSlot();
    const none = { scope, firstFor: () => null };
    if (!slot || !slot.wanted()) {
      return none;
    }
    const ready = await slot.ready();
    if (!ready) {
      this.handlers.onMessage(S.poNotReady, true);
      return null;
    }
    const check = slot.validate(paths, scope);
    if (!check.ok) {
      this.handlers.onMessage(check.message, true);
      return null;
    }
    const snapshot = slot.snapshot(paths, scope);
    return snapshot ? { scope, firstFor: (path) => snapshot.firstFor(path, scope) } : none;
  }

  // The first part of the last file of a queue is ready: the dialog gets out of the way so the viewer can be used while
  // the rest indexes. With more files still to open it stays, showing their progress. Only the session that started it.
  afterFirstReady(caseId, session) {
    const more = this.addFiles && this.addFiles.state.items.some((item) => item.status === "pending");
    if (!more && this.caseId === caseId && session === this.addSession) {
      this.closeDialog("add");
    }
  }

  // -- sources ------------------------------------------------------------------------------

  async refreshSources(quiet = false) {
    if (this.caseId === null) {
      this.sourcesSeq += 1;
      this.sources = [];
      this.renderSources();
      this.handlers.onSources(this.sources, quiet);
      return;
    }
    // The answer belongs to the case selection that asked: if another one (or the same case again) was selected while it
    // was on the way, it touches nothing, neither this.sources, nor the list, nor onSources, nor the message.
    const caseId = this.caseId;
    const gen = this.caseGen;
    const seq = ++this.sourcesSeq;
    let sources;
    let failure = null;
    try {
      const data = await (takeSources(caseId, () => api.sources(caseId)) || api.sources(caseId));
      sources = data.sources;
    } catch (err) {
      failure = err;
    }
    if (this.caseId !== caseId || this.caseGen !== gen || seq !== this.sourcesSeq) {
      return; // another case, or a newer query of this one is (or was) in charge: its answer is the one that counts
    }
    if (failure) {
      this.sources = [];
      this.handlers.onMessage(failure.message, true);
    } else {
      this.sources = sources;
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
  // expectedCaseId (optional) ties the run to the case it was created for. `first` (optional): open only part first
  // ({from, to, sources}, see partial-open-model.js); hooks: onFirstReady(data) when its first part is ready,
  // onStarted() when the server accepted the job (only if this operation is still the current one).
  runCollection(path, scope, expectedCaseId, first = null, hooks = null) {
    return this.runJob((caseId) => api.addCollection(caseId, path, scope, first), expectedCaseId, { first, hooks });
  }

  // One operation of the open case (an opening or a resume), from its start request to its done event. It owns this.waiter
  // and the progress stream until it ends; a second one is refused, never allowed to replace them. `start(caseId)` posts
  // it and resolves to {job_id}; a null job_id means there was nothing to run. options: {first, hooks, resume}.
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
      if (this.waiter) {
        resolve({ status: "refused", code: "job_running", message: S.cfBusy });
        return;
      }
      const waiter = {
        id: ++this.opSeq, jobId: null, caseId: this.caseId, resolve, seen: new Map(), firstSeen: new Map(), hooks, first,
        resume, stream: null,
      };
      this.waiter = waiter;
      waiter.stream = this.watchProgress(this.caseId);
      const current = () => !waiter.cancelled && this.waiter === waiter;
      let request;
      try {
        request = Promise.resolve(start(waiter.caseId));
      } catch (err) {
        request = Promise.reject(err); // a start that throws is a refused start, never an unhandled rejection
      }
      request.then((started) => {
        if (!current()) {
          return; // the case changed (or the operation ended) while the server was answering
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
        if (hooks && typeof hooks.onStarted === "function") {
          hooks.onStarted();
        }
        if (waiter.jobId !== null && waiter.firstSeen.has(waiter.jobId)) {
          this.acceptFirstReady(waiter.stream, waiter.firstSeen.get(waiter.jobId), waiter);
        }
        if (waiter.jobId !== null && waiter.seen.has(waiter.jobId)) {
          this.finishJob(waiter.seen.get(waiter.jobId));
        }
      }).catch((err) => {
        if (!current()) {
          return;
        }
        this.waiter = null;
        this.closeEvents();
        const result = refusalResult(err, {
          duplicate: S.cfDuplicateCollection, busy: S.cfBusy,
        });
        result.error = err; // the opener of a path shows some refusals in its own field
        resolve(result);
      });
    });
  }

  async finishJob(data) {
    const waiter = this.waiter;
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
    if (caseId !== null && this.caseId === caseId && (left > 0 || (this.resumeUi && this.resumeUi.isVisible()) || (waiter && (waiter.first || waiter.resume)))) {
      this.checkPending(caseId).then((answered) => {
        if (!answered) {
          this.restoreOffer(caseId, ["busy", "running"]); // the job is over: an offer left waiting for it must act again
        }
      });
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

  // The "Folder or single file" form: opens the typed path as an operation like any other (same waiter, same job binding,
  // same first_ready and done handling). The dialog closes and the field clears when the server accepts the job, but only
  // if the dialog is still the one the user submitted from.
  async openCollection() {
    if (this.entryBusy()) {
      this.handlers.onMessage(this.queueBusy ? S.cfQueueBusy : S.cfBusy, true);
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
    const caseId = this.caseId;
    const session = this.addSession;
    const scope = this.refs.scopeSelect.value === "all" ? "all" : "default";
    const still = () => this.caseId === caseId && session === this.addSession;
    this.starting = true;
    let started = false;
    let result;
    try {
      const partial = await this.preparePartial([path], scope);
      if (partial === null || !still()) {
        return; // a field to fix (reported), or the case or the dialog changed while the options were checked
      }
      result = await this.runCollection(path, scope, caseId, partial.firstFor(path), {
        onStarted: () => {
          started = true;
          if (still()) {
            this.refs.pathInput.value = "";
            this.closeDialog("add"); // the job runs on; its progress is in the status bar
            if (this.partialSlot()) {
              this.partialSlot().reset();
            }
          }
        },
        onFirstReady: () => this.afterFirstReady(caseId, session),
      });
    } finally {
      this.starting = false;
    }
    if (started || !still() || result.status === "ok") {
      return; // the job's own end is reported by finishJob; an old session shows nothing in the current dialog
    }
    const err = result.error;
    if (err instanceof ApiError && err.code === "bad_first" && this.partialSlot()) {
      this.partialSlot().showServerError(err.message);
      this.handlers.onMessage(err.message, true);
    } else if (err instanceof ApiError && err.status === 409) {
      this.handlers.onMessage(conflictMessage(err), true);
    } else if (err instanceof ApiError && PATH_FIELD_ERRORS.has(err.code)) {
      setFieldError(this.refs.pathInput, err.message); // "Path does not exist: ..." under the field
    } else {
      this.handlers.onMessage(result.message || S.progressFailed, true);
    }
  }

  // Watches the progress of the open case. Returns the state of this stream (the jobs whose first_ready it handled).
  watchProgress(caseId) {
    this.closeEvents();
    if (typeof EventSource !== "function") {
      return null;
    }
    this.reportProgress({ value: null, max: null, text: "" });
    const events = new EventSource(api.progressUrl(caseId));
    this.events = events;
    const stream = { firstJobs: new Set() };
    events.addEventListener("progress", (event) => {
      if (this.events !== events) {
        return; // a stream that was replaced or closed
      }
      const data = parseEvent(event);
      if (!data) {
        return;
      }
      if (data.status === "first_ready") {
        this.onFirstReadyEvent(data, events, caseId, stream);
        return;
      }
      // Progress of an operation is the progress of its own job: events of another job (a replay of an earlier one) and
      // those before the start request answered (the job is not known yet) do not touch the bar.
      if (this.waiter && (this.waiter.jobId === null || this.waiter.jobId !== data.job_id)) {
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
      if (this.events !== events) {
        return;
      }
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
    return stream;
  }

  // progress with status "first_ready": the first part is indexed, derived and correlated. Only the current stream and
  // this operation's job count (a replay of an earlier job is ignored), and each job once.
  onFirstReadyEvent(data, events, caseId, stream) {
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
    this.acceptFirstReady(stream, data, waiter);
  }

  // The one place where a first_ready is accepted, whichever way it arrived (live, or kept until the start answered):
  // a job's first_ready runs once per stream, so a reconnection that replays it does not reload twice.
  acceptFirstReady(stream, data, waiter) {
    if (stream) {
      if (stream.firstJobs.has(data.job_id)) {
        return;
      }
      stream.firstJobs.add(data.job_id);
    } else if (waiter) {
      if (waiter.firstHandled) {
        return;
      }
      waiter.firstHandled = true;
    }
    this.handleFirstReady(data, waiter);
  }

  // The status bar says the rest goes on in the background; sources, grid, timeline and panels reload as at the end of a
  // job (main.js narrows the view to the window); the dialog of the last file steps aside. The window belongs to this
  // operation, so it is applied even when the job is already over by the time the reload ends (a fast job): the case
  // having changed is the only reason to skip it.
  async handleFirstReady(data, waiter) {
    const caseId = this.caseId;
    const gen = this.caseGen;
    const records = safeCount(data.records);
    const active = waiter ? this.waiter === waiter : this.events !== null; // false when the job already ended
    try {
      if (active) {
        this.reportProgress({ value: null, max: null, text: S.poStatusRestBare });
        this.handlers.onMessage(format(S[pluralKey("poFirstReady", records)], { records: records.toLocaleString("en-US") }), false);
      }
      await this.refreshSources(true);
      // The window belongs to this case selection and to this operation: if the case changed (also A -> B -> A) or a
      // later operation started meanwhile, a late reload must not put its window back over the newer one.
      if (this.caseId !== caseId || this.caseGen !== gen || (waiter && waiter.id !== this.opSeq)) {
        return;
      }
      const first = (waiter && waiter.first) || null;
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
  // counts (token) and only for the case that is still open. Resolves to false only when the request itself failed (the
  // caller must then restore the offer on its own); true for any answer, including one that is no longer current.
  async checkPending(caseId) {
    if (caseId === null || caseId === undefined) {
      return true;
    }
    const token = ++this.pendingToken;
    let data;
    try {
      data = await api.partial.pending(caseId);
    } catch (err) {
      return token !== this.pendingToken || this.caseId !== caseId; // an old check failing is not news
    }
    if (token !== this.pendingToken || this.caseId !== caseId) {
      return true;
    }
    const list = data && Array.isArray(data.pending) ? data.pending : [];
    if (list.length === 0) {
      this.hideResume();
      return true;
    }
    const ui = await this.ensureResume();
    if (!ui || token !== this.pendingToken || this.caseId !== caseId) {
      return true;
    }
    this.pendingItems = this.resumeMod.normalizePending(data).items;
    const busy = Boolean(this.waiter) || this.resuming;
    const running = Boolean(data.running);
    if (busy) {
      ui.setState(this.resuming ? "busy" : "running", this.pendingItems, () => this.resumeFallback());
    } else if (running) {
      ui.setState("running", this.pendingItems, () => this.resumeFallback());
      if (!this.events) {
        this.watchProgress(caseId); // a job runs: show its progress, end with its done
      }
    } else {
      ui.setState("ready", this.pendingItems, () => this.resumeFallback());
    }
    if (this.pendingAnnounced !== caseId) {
      this.pendingAnnounced = caseId;
      this.handlers.onMessage(S.poPendingMessage, false);
    }
    return true;
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

  // The offer goes back to "ready" (button on) when it was left in "busy" and nothing else will settle it.
  restoreOffer(caseId, from = ["busy"]) {
    if (this.resuming || this.waiter) {
      return; // an operation owns the offer now (its own end settles it): an old failure must not enable the button
    }
    if (this.caseId === caseId && this.resumeUi && from.includes(this.resumeUi.getState())) {
      this.resumeUi.setState("ready", this.pendingItems, () => this.resumeFallback());
    }
  }

  // Resume now: POST .../resume, then follow the job like any other. One operation at a time (entryBusy).
  async resumeIndexing() {
    const caseId = this.caseId;
    if (caseId === null || this.resuming) {
      return;
    }
    if (this.entryBusy()) {
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
    if (result.status === "ok" && result.nothing) {
      this.hideResume(); // the server said nothing is pending: no further question needed
      return;
    }
    if (result.status !== "ok") {
      if (result.code === "job_running") {
        this.handlers.onMessage(S.poResumeBusy, true);
        if (!this.events) {
          this.watchProgress(caseId);
        }
        if (this.resumeUi) {
          this.resumeUi.setState("running", this.pendingItems, () => this.resumeFallback());
        }
        return;
      }
      this.handlers.onMessage(format(S.poResumeFailed, { message: result.message || S.progressFailed }), true);
      this.restoreOffer(caseId); // retry stays possible whatever the next /pending says
    }
    const answered = await this.checkPending(caseId); // the banner goes away when nothing is left, or comes back with what is
    if (!answered) {
      this.restoreOffer(caseId);
    }
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
