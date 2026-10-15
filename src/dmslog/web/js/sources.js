// Sources panel: case selection, opening a collection with live progress (SSE), source list.
import { ApiError, api } from "./api.js";
import { filterSources, highlightParts, isFiltering } from "./source-filter-model.js";
import { refusalResult } from "./case-files-model.js";
import { setFieldError } from "./field-error.js";
import { S, format, pluralKey } from "./strings.js";

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
    refs.openForm.addEventListener("submit", (event) => {
      event.preventDefault();
      this.openCollection();
    });
  }

  // -- cases --------------------------------------------------------------------------------

  async loadCases(preferredId) {
    const data = await api.cases();
    if (this.handlers.onWatching) {
      this.handlers.onWatching(Boolean(data.watching));
    }
    const select = this.refs.caseSelect;
    select.textContent = "";
    const none = document.createElement("option");
    none.value = "";
    none.textContent = S.caseNone;
    select.appendChild(none);
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
    if (this.addFiles && caseId !== this.caseId) {
      this.addFiles.state.resetResults(); // results belong to the case they were opened in
    }
    this.caseId = caseId;
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
      this.setQueueBusy(true);
      try {
        await this.caseFiles.submit({
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
      return;
    }
    const name = this.refs.newCaseName.value.trim();
    if (name === "") {
      return;
    }
    try {
      const created = await api.createCase(name);
      this.refs.newCaseName.value = "";
      await this.loadCases(created.id);
    } catch (err) {
      this.handlers.onMessage(err.message, true);
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
    try {
      await this.addFiles.submitAdd({
        openPath: (path) => this.runCollection(path, this.currentScope(), boundCase),
      }, boundCase);
    } finally {
      this.setQueueBusy(false);
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
      const data = await api.sources(this.caseId);
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

  setQuery(query) {
    this.query = String(query);
    this.renderSources();
  }

  // Keyboard: from the filter box into the list, and Enter in the box toggles the first match.
  focusList() {
    const box = this.refs.sourceList.querySelector("input[type=checkbox]");
    if (box) {
      box.focus();
    }
  }

  toggleFirst() {
    const box = this.refs.sourceList.querySelector("input[type=checkbox]");
    if (box) {
      box.click();
    }
  }

  renderSources() {
    const list = this.refs.sourceList;
    list.textContent = "";
    if (this.sources.length === 0) {
      const empty = document.createElement("li");
      empty.className = "empty";
      empty.textContent = S.sourcesEmpty;
      list.appendChild(empty);
      this.notifyRendered(0, 0);
      return;
    }
    const shown = filterSources(this.sources, this.query);
    if (shown.length === 0) {
      const empty = document.createElement("li");
      empty.className = "empty";
      empty.textContent = S.sourceFilterNone;
      list.appendChild(empty);
    }
    this.notifyRendered(shown.length, this.sources.length);
    for (const { source, name: fileName, range } of shown) {
      const item = document.createElement("li");
      const label = document.createElement("label");
      const box = document.createElement("input");
      box.type = "checkbox";
      box.checked = this.selected.has(source.id);
      box.addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
          event.preventDefault();
          box.click();
        } else if (event.key === "Escape" && this.refs.filterInput) {
          this.refs.filterInput.focus();
        }
      });
      box.addEventListener("change", () => {
        if (box.checked) {
          this.selected.add(source.id);
        } else {
          this.selected.delete(source.id);
        }
        this.handlers.onSelection(this.selectedIds());
      });
      const text = document.createElement("span");
      text.className = "source-text";
      const name = document.createElement("span");
      name.className = "source-name";
      if (isFiltering(this.query)) {
        for (const part of highlightParts(fileName, range)) {
          if (part.match) {
            const mark = document.createElement("mark");
            mark.textContent = part.text;
            name.appendChild(mark);
          } else {
            name.appendChild(document.createTextNode(part.text));
          }
        }
      } else {
        name.textContent = fileName;
      }
      name.title = source.name;
      const meta = document.createElement("span");
      meta.className = "source-meta";
      meta.textContent = format(S[pluralKey("sourceMeta", source.records)], {
        family: source.family === null || source.family === undefined ? "" : source.family,
        pid: source.pid === null || source.pid === undefined ? "-" : source.pid,
        records: Number(source.records).toLocaleString("en-US"),
      });
      text.appendChild(name);
      text.appendChild(meta);
      label.appendChild(box);
      label.appendChild(text);
      if (source.sensitive) {
        const badge = document.createElement("span");
        badge.className = "badge";
        badge.textContent = S.sourceSensitive;
        label.appendChild(badge);
      }
      item.appendChild(label);
      list.appendChild(item);
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
      this.handlers.onMessage(
        format(S[pluralKey("progressDone", data.records || 0)], { records: Number(data.records || 0).toLocaleString("en-US") }), false);
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
      this.reportProgress({
        value: data.bytes_total > 0 ? data.bytes_done : null,
        max: data.bytes_total > 0 ? data.bytes_total : null,
        text: format(S[pluralKey("statusIndexing", data.records || 0)], {
          source: data.source === null || data.source === undefined ? "" : data.source,
          records: Number(data.records || 0).toLocaleString("en-US"),
        }),
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
