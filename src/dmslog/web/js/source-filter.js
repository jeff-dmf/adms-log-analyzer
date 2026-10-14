// Source filter box above the source list: type part of a log file name and the list below narrows
// down as you type (case-insensitive, no server calls). Only text nodes and <mark> built by the DOM.
import { DEBOUNCE_MS, isFiltering, loadQuery, saveQuery } from "./source-filter-model.js";
import { S, format, pluralKey } from "./strings.js";

export class SourceFilter {
  // host: element that receives the box; panel: SourcesPanel (setQuery / focusList / toggleFirst).
  constructor({ host, panel }) {
    this.panel = panel;
    this.timer = null;
    const label = document.createElement("label");
    label.className = "visually-hidden";
    label.htmlFor = "source-filter-input";
    label.textContent = S.sourceFilterLabel;
    this.input = document.createElement("input");
    this.input.type = "search";
    this.input.id = "source-filter-input";
    this.input.autocomplete = "off";
    this.input.spellcheck = false;
    this.input.placeholder = S.sourceFilterPlaceholder;
    this.clearButton = document.createElement("button");
    this.clearButton.type = "button";
    this.clearButton.className = "source-filter-clear";
    this.clearButton.textContent = "×";
    this.clearButton.title = S.sourceFilterClear;
    this.clearButton.setAttribute("aria-label", S.sourceFilterClear);
    this.clearButton.hidden = true;
    const row = document.createElement("div");
    row.className = "source-filter-row";
    row.appendChild(this.input);
    row.appendChild(this.clearButton);
    this.status = document.createElement("div");
    this.status.className = "source-meta source-filter-status";
    this.status.setAttribute("role", "status");
    this.status.setAttribute("aria-live", "polite");
    host.appendChild(label);
    host.appendChild(row);
    host.appendChild(this.status);

    this.input.addEventListener("input", () => {
      this.clearButton.hidden = this.input.value === "";
      clearTimeout(this.timer);
      this.timer = setTimeout(() => this.apply(), DEBOUNCE_MS);
    });
    this.input.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && this.input.value !== "") {
        event.preventDefault();
        event.stopPropagation();
        this.clear();
      } else if (event.key === "ArrowDown") {
        event.preventDefault();
        this.apply();
        this.panel.focusList();
      } else if (event.key === "Enter") {
        event.preventDefault();
        this.apply();
        this.panel.toggleFirst();
      }
    });
    this.clearButton.addEventListener("click", () => {
      this.clear();
      this.input.focus();
    });
    panel.refs.filterInput = this.input;
    panel.onRendered = (visible, total) => this.showCount(visible, total);

    const saved = loadQuery();
    if (saved !== "") {
      this.input.value = saved;
      this.clearButton.hidden = false;
      panel.setQuery(saved);
    }
  }

  apply() {
    clearTimeout(this.timer);
    this.timer = null;
    saveQuery(this.input.value);
    this.panel.setQuery(this.input.value);
  }

  clear() {
    this.input.value = "";
    this.clearButton.hidden = true;
    this.apply();
  }

  showCount(visible, total) {
    if (!isFiltering(this.input.value) || total === 0) {
      this.status.textContent = total === 0 ? "" : format(S[pluralKey("sourceFilterAll", total)], { total });
    } else {
      this.status.textContent = format(S[pluralKey("sourceFilterCount", total)], { visible, total });
    }
  }
}
