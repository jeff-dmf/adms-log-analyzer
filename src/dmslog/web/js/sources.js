// Sources panel: case selection, opening a collection with live progress (SSE), source list.
import { ApiError, api } from "./api.js";
import { S, format } from "./strings.js";

export function conflictMessage(error) {
  const key = "conflict_" + error.code;
  return Object.prototype.hasOwnProperty.call(S, key) ? S[key] : S.conflict_generic + " " + error.message;
}

export class SourcesPanel {
  constructor(refs, handlers) {
    this.refs = refs;
    this.handlers = handlers; // {onCase(caseId), onSources(sources, quiet), onSelection(ids), onDone(), onMessage(text, isError)}
    this.caseId = null;
    this.sources = [];
    this.selected = new Set();
    this.events = null;
    refs.caseSelect.addEventListener("change", () => this.selectCase(refs.caseSelect.value || null));
    refs.newCaseForm.addEventListener("submit", (event) => {
      event.preventDefault();
      this.createCase();
    });
    refs.openForm.addEventListener("submit", (event) => {
      event.preventDefault();
      this.openCollection();
    });
  }

  // -- cases --------------------------------------------------------------------------------

  async loadCases(preferredId) {
    const data = await api.cases();
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

  async selectCase(caseId) {
    this.closeEvents();
    this.caseId = caseId;
    this.selected.clear();
    this.refs.caseSelect.value = caseId || "";
    this.handlers.onCase(caseId);
    await this.refreshSources();
  }

  async createCase() {
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

  renderSources() {
    const list = this.refs.sourceList;
    list.textContent = "";
    if (this.sources.length === 0) {
      const empty = document.createElement("li");
      empty.className = "empty";
      empty.textContent = S.sourcesEmpty;
      list.appendChild(empty);
      return;
    }
    for (const source of this.sources) {
      const item = document.createElement("li");
      const label = document.createElement("label");
      const box = document.createElement("input");
      box.type = "checkbox";
      box.checked = this.selected.has(source.id);
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
      name.textContent = source.name;
      const meta = document.createElement("span");
      meta.className = "source-meta";
      meta.textContent = format(S.sourceMeta, {
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

  // -- collections and progress -------------------------------------------------------------

  async openCollection() {
    if (this.caseId === null) {
      this.handlers.onMessage(S.needCase, true);
      return;
    }
    const path = this.refs.pathInput.value.trim();
    if (path === "") {
      this.handlers.onMessage(S.needPath, true);
      return;
    }
    const scope = this.refs.scopeSelect.value === "all" ? "all" : "default";
    this.watchProgress(this.caseId);
    try {
      await api.addCollection(this.caseId, path, scope);
      this.handlers.onMessage(S.collectionStarted, false);
    } catch (err) {
      this.closeEvents();
      if (err instanceof ApiError && err.status === 409) {
        this.handlers.onMessage(conflictMessage(err), true);
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
    const bar = this.refs.progressBar;
    bar.hidden = false;
    bar.removeAttribute("value");
    this.refs.progressText.textContent = "";
    const events = new EventSource(api.progressUrl(caseId));
    this.events = events;
    events.addEventListener("progress", (event) => {
      const data = parseEvent(event);
      if (!data) {
        return;
      }
      if (data.bytes_total > 0) {
        bar.max = data.bytes_total;
        bar.value = data.bytes_done;
      }
      this.refs.progressText.textContent = format(S.progressLine, {
        source: data.source === null || data.source === undefined ? "" : data.source,
        records: Number(data.records || 0).toLocaleString("en-US"),
      });
      this.refreshThrottled();
    });
    events.addEventListener("done", async (event) => {
      const data = parseEvent(event) || {};
      this.closeEvents();
      bar.hidden = true;
      if (data.status === "error") {
        this.handlers.onMessage(S.progressFailed + (data.message ? ": " + data.message : ""), true);
      } else {
        this.handlers.onMessage(S.progressDone, false);
      }
      await this.refreshSources();
      this.handlers.onDone();
    });
    events.addEventListener("error", () => {
      // The browser retries by itself; a closed stream just stops the bar.
      if (events.readyState === 2) {
        bar.hidden = true;
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
    this.refs.progressBar.hidden = true;
  }
}

function parseEvent(event) {
  try {
    return JSON.parse(event.data);
  } catch (err) {
    return null;
  }
}
