// Orchestration: wires the panels together. Everything shown comes from strings.js or the API
// and reaches the page through textContent / DOM nodes only.
import { api, onForbidden } from "./api.js";
import { Detail } from "./detail.js";
import { buildFilter, stateFromForm, timeSpan } from "./filters.js";
import { COLUMNS, Grid } from "./grid.js";
import { searchTerms } from "./search.js";
import { SourcesPanel } from "./sources.js";
import { S, format } from "./strings.js";
import { applyTheme, currentTheme, toggleTheme } from "./theme.js";
import { parseTs } from "./time.js";

const COLUMNS_KEY = "dmslog.columns";

function byId(id) {
  return document.getElementById(id);
}

function applyStaticStrings() {
  for (const el of document.querySelectorAll("[data-i18n]")) {
    el.textContent = S[el.dataset.i18n];
  }
  for (const el of document.querySelectorAll("[data-i18n-placeholder]")) {
    el.placeholder = S[el.dataset.i18nPlaceholder];
  }
  for (const el of document.querySelectorAll("[data-i18n-title]")) {
    el.title = S[el.dataset.i18nTitle];
  }
  for (const el of document.querySelectorAll("[data-i18n-aria-label]")) {
    el.setAttribute("aria-label", S[el.dataset.i18nAriaLabel]);
  }
}

function fillSelect(select, options) {
  select.textContent = "";
  for (const [value, label] of options) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = label;
    select.appendChild(option);
  }
}

function loadColumns() {
  try {
    const stored = JSON.parse(window.localStorage.getItem(COLUMNS_KEY));
    if (Array.isArray(stored) && stored.length > 0) {
      return COLUMNS.map((c) => c.key).filter((k) => stored.includes(k));
    }
  } catch (err) {
    // Missing or blocked storage: use the defaults.
  }
  return COLUMNS.map((c) => c.key);
}

function saveColumns(keys) {
  try {
    window.localStorage.setItem(COLUMNS_KEY, JSON.stringify(keys));
  } catch (err) {
    // Not persisted; the view still works.
  }
}

function start() {
  applyStaticStrings();
  applyTheme();
  document.title = S.appName;

  const messageEl = byId("message");
  const showMessage = (text, isError) => {
    messageEl.textContent = text;
    messageEl.classList.toggle("error", Boolean(isError));
    messageEl.hidden = text === "";
  };
  const showError = (err) => showMessage(err && err.message ? err.message : S.errorGeneric, true);
  onForbidden(() => showMessage(S.errorForbidden, true));

  const themeButton = byId("theme-button");
  const paintTheme = () => {
    themeButton.textContent = currentTheme() === "dark" ? S.themeDark : S.themeLight;
  };
  paintTheme();
  themeButton.addEventListener("click", () => {
    toggleTheme();
    paintTheme();
  });

  fillSelect(byId("scope-select"), [["default", S.scopeDefault], ["all", S.scopeAll]]);
  fillSelect(byId("sev-select"), [["", S.filterAny]].concat(
    [1, 2, 3, 4, 5].map((n) => [String(n), format(S.severityAtLeast, { name: S.severityNames[n] })])));
  fillSelect(byId("level-select"), [["", S.filterAny]].concat(
    [1, 2, 3, 4, 5, 6, 7, 8].map((n) => [String(n), format(S.levelAtLeast, { name: S.levelNames[n] })])));
  fillSelect(byId("context-same"), [
    ["source", S.contextSameSource], ["tid", S.contextSameTid], ["merge", S.contextSameMerge],
  ]);

  let sources = [];
  let sourceNames = new Map();
  let caseId = null;
  let formState = { sevMax: null, levelMax: null, from: null, to: null };
  let searchText = "";

  const grid = new Grid({
    viewport: byId("grid-viewport"),
    rowsLayer: byId("grid-rows"),
    spacer: byId("grid-spacer"),
    header: byId("grid-header"),
    onSelect: (row) => detail.show(row.id),
    onStatus: (text) => {
      byId("grid-status").textContent = text;
    },
    onError: showError,
  });
  const detail = new Detail({
    meta: byId("detail-meta"),
    text: byId("detail-text"),
    actions: byId("detail-actions"),
    contextButton: byId("context-button"),
    contextClose: byId("context-close"),
    contextSame: byId("context-same"),
    contextBox: byId("context-box"),
    contextList: byId("context-list"),
    copyButton: byId("copy-button"),
    copyStatus: byId("copy-status"),
  }, {
    getCase: () => caseId,
    getSourceName: (id) => sourceNames.get(id) || String(id),
    onError: showError,
  });

  const applyView = () => {
    const ids = panel.selectedIds();
    const filter = buildFilter({ sourceIds: ids, text: searchText, ...formState });
    const range = timeSpan(sources, ids, formState.from, formState.to);
    const terms = searchTerms(searchText);
    grid.setTerms(terms);
    detail.setTerms(terms);
    detail.clear();
    return grid.setView({ caseId, filter, range });
  };

  const panel = new SourcesPanel({
    caseSelect: byId("case-select"),
    newCaseForm: byId("new-case-form"),
    newCaseName: byId("new-case-name"),
    openForm: byId("open-form"),
    pathInput: byId("path-input"),
    scopeSelect: byId("scope-select"),
    progressBar: byId("progress-bar"),
    progressText: byId("progress-text"),
    sourceList: byId("source-list"),
  }, {
    onCase: (id) => {
      caseId = id;
      detail.clear(); // drop the old case's entry right away, before /sources answers
    },
    onSources: (list, quiet) => {
      sources = list;
      sourceNames = new Map(list.map((s) => [s.id, s.name]));
      grid.setSourceNames(sourceNames);
      if (!quiet) {
        applyView();
      }
    },
    onSelection: () => applyView(),
    onDone: () => applyView(),
    onMessage: showMessage,
  });

  byId("search-form").addEventListener("submit", (event) => {
    event.preventDefault();
    searchText = byId("search-input").value;
    applyView();
  });
  byId("search-clear").addEventListener("click", () => {
    byId("search-input").value = "";
    searchText = "";
    applyView();
  });
  byId("filter-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const { state, errors } = stateFromForm({
      sourceIds: [],
      text: "",
      sevMax: byId("sev-select").value,
      levelMax: byId("level-select").value,
      from: byId("from-input").value,
      to: byId("to-input").value,
    });
    if (errors.length > 0) {
      showMessage(S.filterBadTime, true);
      return;
    }
    formState = { sevMax: state.sevMax, levelMax: state.levelMax, from: state.from, to: state.to };
    applyView();
  });
  byId("filter-reset").addEventListener("click", () => {
    byId("filter-form").reset();
    formState = { sevMax: null, levelMax: null, from: null, to: null };
    applyView();
  });
  byId("goto-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const ms = parseTs(byId("goto-input").value);
    if (ms === null) {
      showMessage(S.filterBadTime, true);
      return;
    }
    showMessage("", false);
    grid.jumpToTime(ms);
  });

  // Column chooser.
  const menu = byId("columns-menu");
  const columnsButton = byId("columns-button");
  let visibleColumns = loadColumns();
  for (const column of COLUMNS) {
    const label = document.createElement("label");
    const box = document.createElement("input");
    box.type = "checkbox";
    box.checked = visibleColumns.includes(column.key);
    box.addEventListener("change", () => {
      const keys = COLUMNS.map((c) => c.key).filter((key) =>
        key === column.key ? box.checked : visibleColumns.includes(key));
      if (keys.length === 0) {
        box.checked = true;
        return;
      }
      visibleColumns = keys;
      saveColumns(keys);
      grid.setColumns(keys);
    });
    label.appendChild(box);
    label.appendChild(document.createTextNode(" " + column.label()));
    menu.appendChild(label);
  }
  columnsButton.addEventListener("click", () => {
    menu.hidden = !menu.hidden;
    columnsButton.setAttribute("aria-expanded", menu.hidden ? "false" : "true");
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !menu.hidden) {
      menu.hidden = true;
      columnsButton.setAttribute("aria-expanded", "false");
      columnsButton.focus();
    }
  });
  grid.setColumns(visibleColumns);

  panel.loadCases(null).catch(showError);
}

start();
