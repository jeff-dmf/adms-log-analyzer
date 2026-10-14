// Orchestration: wires the panels together. Everything shown comes from strings.js or the API
// and reaches the page through textContent / DOM nodes only.
import { api, onForbidden } from "./api.js";
import { createBookmarkStore } from "./bookmarks-model.js";
import { createHighlightStore } from "./highlight-model.js";
import { compareButtonLabel, createCompareSelection } from "./compare-model.js";
import { CaseFiles } from "./case-files.js";
import { Detail } from "./detail.js";
import {
  NAV_ERROR_FILTER, TPL_LEAF, andFilters, bookmarkKey, composeFilter, createLatestRequest, isTypingTarget, navKey, navRequest, parseTplList, stateFromForm,
  timeSpan, withTemplate, withTsRange,
} from "./filters.js";
import { registerLeafType } from "./filter-tree-model.js";
import { initFilterTree, registerLeafEditor } from "./filter-tree.js";
import { initTimeline } from "./timeline.js";
import { COLUMNS, Grid } from "./grid.js";
import { CollectionsPanel } from "./collections-panel.js";
import {
  columnShown, effectiveSourceIds, gridTags, isMulti, loadColumnPref, saveColumnPref, scopeApi,
} from "./collections-model.js";
import { KnowledgeSection } from "./knowledge.js";
import { searchTerms } from "./search.js";
import { SourceFilter } from "./source-filter.js";
import { SourcesPanel } from "./sources.js";
import { S, format } from "./strings.js";
import { applyTheme, currentTheme, toggleTheme } from "./theme.js";
import { parseTs } from "./time.js";

const COLUMNS_KEY = "dmslog.columns";
const FIELD_COLUMNS_KEY = "dmslog.fieldColumns";
const FIELD_NAMES_KEY = "dmslog.fieldNames";
const MAX_FIELD_NAMES = 200;

// Template leaf of the filter tree: {tpl: [id | code, ...]}.
try {
  registerLeafType("tpl", TPL_LEAF);
} catch (err) {
  // Already registered (module evaluated twice): keep the existing definition.
}
let tplEditorUid = 0;
registerLeafEditor("tpl", {
  label: () => S.ftTypeTpl,
  render(leaf, ctx) {
    tplEditorUid += 1;
    const wrap = document.createElement("span");
    wrap.className = "ft-fields ft-grow";
    const label = document.createElement("label");
    const input = document.createElement("input");
    input.id = "ft-tpl-" + tplEditorUid;
    label.htmlFor = input.id;
    label.textContent = S.ftTplLabel;
    input.type = "text";
    input.autocomplete = "off";
    input.placeholder = S.ftTplPlaceholder;
    input.value = leaf.data.ids.join(", ");
    input.addEventListener("input", () => {
      const ids = parseTplList(input.value);
      ctx.update({ ids: ids.includes(-1) ? [-1] : ids }, "ids"); // [-1] fails validation
    });
    wrap.append(label, input);
    return wrap;
  },
});

function readList(key) {
  try {
    const stored = JSON.parse(window.localStorage.getItem(key));
    return Array.isArray(stored) ? stored.filter((v) => typeof v === "string") : [];
  } catch (err) {
    return [];
  }
}

function writeList(key, list) {
  try {
    window.localStorage.setItem(key, JSON.stringify(list));
  } catch (err) {
    // Not persisted; the view still works.
  }
}

function safeLocalStorage() {
  try {
    return window.localStorage;
  } catch (err) {
    return null;
  }
}

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
  let userFilter = null; // the filter tree's filter (plus timeline range and template clicks)
  let toggles = { hideNoise: false, unclassified: false };
  let currentFilter = null; // what the grid and the timeline use: the ONE composed filter
  let templateLabels = new Map(); // template id -> readable title (code when the catalogue has none)
  let filterTree = null;
  let timeline = null;
  let unknownPanel = null;
  let configPanel = null;
  let eventsPanel = null;
  let dashboard = null;
  let followTail = null;
  let clockBanner = null;
  let knowledge = null;
  let linksSection = null;
  let sideBySide = null;
  let keyPanel = null;
  let payloadSection = null;
  let payloadList = null;
  let openOperation = () => {};
  let exportPanel = null;
  let copyExport = null;
  let sourceFamilies = new Map();
  // API for the panels fed by derived events: their queries carry the chosen collection.
  const derivedApi = scopeApi(api, () => (collectionsPanel ? collectionsPanel.selectedGroups() : []));
  const seenFields = new Set(readList(FIELD_NAMES_KEY));
  let fieldColumns = readList(FIELD_COLUMNS_KEY);
  const navRequests = createLatestRequest(); // n/N/t/T lookups; a view change makes them stale
  let bookmarksPanel = null;
  let detailBookmark = null;
  let compare = null;
  let alertsUi = null; // local alerts: banner and panel (alerts-panel.js)
  let distinctUi = null; // "Distinct values" and "Group by template" (distinct-panel.js)
  let aggregateUi = null; // "Aggregate" panel (aggregate-panel.js)
  let collectionsPanel = null; // the Collections panel (collections-panel.js)
  let collections = []; // collections of the open case (GET /collections)
  let collectionColumnPref = loadColumnPref(safeLocalStorage());
  let applyPending = false; // a full view reload follows: the Collection column need not reload the grid itself
  let viewsStack = null; // chained views: model (views-stack-model.js) and UI (views-stack.js)
  let viewsUi = null;
  const compareSelection = createCompareSelection();
  const compareButton = byId("compare-button");
  function paintCompareButton(entry) {
    compareButton.textContent = compareButtonLabel(compareSelection.first, entry ? entry.id : null);
  }

  // Bookmark markers shared by the grid, the detail pane and the panel.
  const bookmarkStore = createBookmarkStore({
    api,
    getCase: () => caseId,
    onChange: (kind) => {
      grid.render();
      if (detailBookmark) {
        detailBookmark.refresh();
      }
      if (kind === "edit" && bookmarksPanel && bookmarksPanel.isOpen()) {
        bookmarksPanel.refresh();
      }
    },
  });

  // Highlight rules of the case: colors of the grid rows (highlight-model.js).
  let highlightUi = null;
  const highlightStore = createHighlightStore({
    api,
    getCase: () => caseId,
    onChange: () => {
      grid.render();
      if (highlightUi) {
        highlightUi.storeChanged();
      }
    },
  });

  // Panels fed by the derived events: visible ones reload, hidden ones load when opened.
  const refreshDerived = () => {
    if (clockBanner) {
      clockBanner.refresh();
    }
    if (eventsPanel && !byId("events-panel-host").hidden) {
      eventsPanel.refresh();
    }
    if (dashboard && !byId("dashboard-host").hidden) {
      dashboard.refresh();
    }
  };

  const setUserFilter = (filter) => {
    userFilter = filter;
    if (filterTree) {
      filterTree.setFilter(userFilter);
    }
    return applyView();
  };
  const addTemplateFilter = (tplId) => setUserFilter(withTemplate(userFilter, tplId));

  const loadTemplates = async (forCase) => {
    if (forCase === null) {
      templateLabels = new Map();
      return;
    }
    try {
      const data = await api.templates(forCase, null);
      if (forCase === caseId) {
        templateLabels = new Map((data.templates || []).map((t) => [t.id, t.title || t.code]));
        if (detail.entry) {
          detail.renderTemplate(detail.entry);
        }
      }
    } catch (err) {
      if (forCase === caseId) {
        showError(err);
      }
    }
  };

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
    getMark: (id) => bookmarkStore.get(id),
    getHighlight: (id) => highlightStore.get(id),
    onVisible: (ids) => {
      bookmarkStore.ensure(ids);
      highlightStore.ensure(ids);
    },
    onView: () => followTail && followTail.viewChanged(),
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
    template: byId("detail-template"),
    fields: byId("detail-fields"),
  }, {
    onEntry: (entry) => {
      if (knowledge) {
        knowledge.show(entry);
      }
      if (linksSection) {
        linksSection.show(entry);
      }
      if (payloadSection) {
        payloadSection.show(entry);
      }
      if (detailBookmark) {
        detailBookmark.show(entry);
      }
      if (highlightUi) {
        highlightUi.showEntry(entry);
      }
      paintCompareButton(entry);
    },
    getTemplateCode: (id) => templateLabels.get(id) || "",
    onTemplate: (tplId) => addTemplateFilter(tplId),
    onFields: (names) => {
      let added = false;
      for (const name of names) {
        if (!seenFields.has(name) && seenFields.size < MAX_FIELD_NAMES && /^[A-Za-z_][A-Za-z0-9_]{0,63}$/.test(name)) {
          seenFields.add(name);
          added = true;
        }
      }
      if (added) {
        writeList(FIELD_NAMES_KEY, Array.from(seenFields));
        buildColumnMenu();
      }
    },
    getCase: () => caseId,
    getSourceName: (id) => sourceNames.get(id) || String(id),
    onError: showError,
  });

  knowledge = new KnowledgeSection(byId("detail-knowledge"), {
    getCase: () => caseId,
    getFamily: (sourceId) => sourceFamilies.get(sourceId) || "",
    onError: showError,
  });

  // Live conditions of the active view (toolbar + filter tree), for the stack of chained views.
  const captureLive = () => ({
    searchText,
    form: {
      sev: byId("sev-select").value, level: byId("level-select").value,
      from: byId("from-input").value, to: byId("to-input").value,
    },
    formState: { ...formState },
    toggles: { ...toggles },
    userFilter,
  });
  const restoreLive = (state) => {
    searchText = state.searchText;
    byId("search-input").value = state.searchText;
    byId("sev-select").value = state.form.sev;
    byId("level-select").value = state.form.level;
    byId("from-input").value = state.form.from;
    byId("to-input").value = state.form.to;
    formState = { ...state.formState };
    toggles = { ...state.toggles };
    byId("hide-noise").checked = toggles.hideNoise;
    byId("unclassified-only").checked = toggles.unclassified;
    userFilter = state.userFilter;
    if (filterTree) {
      filterTree.setFilter(userFilter);
    }
  };
  const ownFilterOf = (state) =>
    composeFilter({ sourceIds: [], text: state.searchText, ...state.formState }, state.userFilter, state.toggles);

  const applyView = () => {
    const ids = panel.selectedIds();
    const groups = collectionsPanel ? collectionsPanel.selectedGroups() : [];
    const own = composeFilter({ sourceIds: ids, collectionIds: groups, text: searchText, ...formState }, userFilter, toggles);
    // The effective view: the ancestors' filters AND the active view's own.
    const filter = viewsStack ? andFilters(viewsStack.parentsFilter(), own) : own;
    currentFilter = filter;
    navRequests.invalidate();
    const range = timeSpan(sources, effectiveSourceIds(sources, ids, groups), formState.from, formState.to);
    const terms = searchTerms(searchText);
    grid.setTerms(terms);
    detail.setTerms(terms);
    detail.clear();
    const loaded = grid.setView({ caseId, filter, range });
    if (timeline) {
      timeline.refresh();
    }
    if (distinctUi) {
      distinctUi.viewChanged();
    }
    if (aggregateUi) {
      aggregateUi.viewChanged();
    }
    if (viewsUi) {
      viewsUi.changed();
    }
    return loaded;
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
      applyPending = true; // refreshSources() follows and rebuilds the view
      if (collectionsPanel) {
        collectionsPanel.setCase();
      }
      byId("add-files-host").hidden = id === null;
      detail.clear(); // drop the old case's entry right away, before /sources answers
      bookmarkStore.reset();
      compareSelection.reset();
      if (bookmarksPanel) {
        bookmarksPanel.reset();
      }
      if (compare) {
        compare.reset();
      }
      userFilter = null; // conditions of the old case do not carry over
      if (filterTree) {
        filterTree.setCase(id);
        filterTree.setFilter(null);
      }
      if (viewsUi) {
        const saved = viewsUi.onCase(id);
        if (saved) {
          restoreLive(saved);
        }
      }
      loadTemplates(id);
      knowledge.reset();
      if (keyPanel) {
        keyPanel.reset();
      }
      if (exportPanel) {
        exportPanel.reset();
      }
      if (copyExport) {
        copyExport.reset();
      }
      if (sideBySide) {
        sideBySide.reset();
      }
      if (payloadList) {
        payloadList.close();
      }
      if (unknownPanel) {
        unknownPanel.refresh();
      }
      if (configPanel) {
        configPanel.refresh();
      }
      refreshDerived();
      if (followTail) {
        followTail.onCase();
      }
      if (alertsUi) {
        alertsUi.onCase();
      }
    },
    onSources: (list, quiet) => {
      sources = list;
      sourceNames = new Map(list.map((s) => [s.id, s.name]));
      sourceFamilies = new Map(list.map((s) => [s.id, s.family || ""]));
      grid.setSourceNames(sourceNames);
      // The collections load first so the Collection column and the filter are right in the first load.
      const listed = collectionsPanel ? collectionsPanel.refresh() : Promise.resolve();
      if (!quiet) {
        applyPending = true;
        listed.then(() => {
          applyPending = false;
          return applyView();
        });
      }
    },
    onSelection: () => applyView(),
    onDone: () => {
      bookmarkStore.invalidate(); // the records were (re)indexed: markers are looked up again
      highlightStore.invalidateMarks();
      if (bookmarksPanel && bookmarksPanel.isOpen()) {
        bookmarksPanel.refresh();
      }
      loadTemplates(caseId);
      knowledge.reset(); // the ERS configuration may have been read again
      if (unknownPanel) {
        unknownPanel.refresh();
      }
      if (configPanel) {
        configPanel.refresh();
      }
      refreshDerived();
      return applyView();
    },
    onMessage: showMessage,
  });
  new SourceFilter({ host: byId("source-filter-host"), panel });
  const syncCollectionColumn = () =>
    grid.setCollectionColumn(columnShown(collections, collectionColumnPref), !applyPending).catch(showError);
  collectionsPanel = new CollectionsPanel({
    host: byId("collections-host"),
    note: byId("collections-note"),
    api,
    getCase: () => caseId,
    onSelection: (groups) => {
      if (groups.length > 1) {
        showMessage(S.colPanelsOne, false);
      }
      refreshDerived(); // Operations, Dashboard and the clock banner follow the chosen collection
      return applyView();
    },
    onList: (list) => {
      const multiChanged = isMulti(list) !== isMulti(collections);
      collections = list;
      grid.setCollectionTags(gridTags(list));
      syncCollectionColumn();
      if (multiChanged) {
        buildColumnMenu(); // the menu is rebuilt only when it gains or loses the Collection item
      }
    },
    // A collection was removed: sources, records, links, bookmarks and the panels fed by them reload.
    onRemoved: async () => {
      await panel.refreshSources(true);
      await panel.handlers.onDone();
    },
    onMessage: showMessage,
  });
  panel.caseFiles = new CaseFiles({
    host: byId("case-files-host"),
    nameInput: byId("new-case-name"),
    onMessage: showMessage,
  });
  byId("add-files-host").hidden = true; // until a case is open
  panel.addFiles = new CaseFiles({
    host: byId("add-files-host"),
    nameInput: null,
    onMessage: showMessage,
    mode: "add",
    onAdd: () => panel.addFilesToCase(),
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
    toggles = { hideNoise: false, unclassified: false };
    applyView();
  });
  for (const [id, key] of [["hide-noise", "hideNoise"], ["unclassified-only", "unclassified"]]) {
    byId(id).addEventListener("change", (event) => {
      toggles = { ...toggles, [key]: event.target.checked };
      applyView();
    });
  }
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

  // Column chooser: the fixed columns, then the parsed-field names seen so far.
  const menu = byId("columns-menu");
  const columnsButton = byId("columns-button");
  let visibleColumns = loadColumns();
  const checkbox = (labelText, checked, onChange) => {
    const label = document.createElement("label");
    const box = document.createElement("input");
    box.type = "checkbox";
    box.checked = checked;
    box.addEventListener("change", () => onChange(box));
    label.appendChild(box);
    label.appendChild(document.createTextNode(" " + labelText));
    return label;
  };
  function buildColumnMenu() {
    menu.textContent = "";
    for (const column of COLUMNS) {
      menu.appendChild(checkbox(column.label(), visibleColumns.includes(column.key), (box) => {
        const keys = COLUMNS.map((c) => c.key).filter((key) =>
          key === column.key ? box.checked : visibleColumns.includes(key));
        if (keys.length === 0) {
          box.checked = true;
          return;
        }
        visibleColumns = keys;
        saveColumns(keys);
        grid.setColumns(keys);
      }));
    }
    if (isMulti(collections)) {
      menu.appendChild(checkbox(S.columnCollection, collectionColumnPref, (box) => {
        collectionColumnPref = box.checked;
        saveColumnPref(collectionColumnPref, safeLocalStorage());
        syncCollectionColumn();
      }));
    }
    const names = Array.from(new Set([...seenFields, ...fieldColumns])).sort();
    const heading = document.createElement("div");
    heading.className = "menu-heading";
    heading.textContent = S.columnsFieldsHeading;
    menu.appendChild(heading);
    if (names.length === 0) {
      const empty = document.createElement("div");
      empty.className = "source-meta";
      empty.textContent = S.columnsFieldsEmpty;
      menu.appendChild(empty);
    }
    for (const name of names) {
      menu.appendChild(checkbox(name, fieldColumns.includes(name), (box) => {
        fieldColumns = names.filter((n) => (n === name ? box.checked : fieldColumns.includes(n)));
        writeList(FIELD_COLUMNS_KEY, fieldColumns);
        grid.setFieldColumns(fieldColumns).catch(showError);
      }));
    }
    const reset = document.createElement("button");
    reset.type = "button";
    reset.className = "menu-action";
    reset.textContent = S.columnsResetWidths;
    reset.addEventListener("click", () => grid.resetColumnWidths());
    menu.appendChild(reset);
  }
  buildColumnMenu();
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
  grid.setFieldColumns(fieldColumns);

  // Timeline above the grid: a dragged range becomes the top-level ts condition.
  timeline = initTimeline(byId("timeline"), {
    api,
    getSourceName: (id) => sourceNames.get(id) || String(id),
    getFilter: () => (caseId === null ? null : { caseId, filter: currentFilter }),
    onRangeSelected: (range) => setUserFilter(withTsRange(userFilter, range)),
  });

  // Filter tree: created on first use (it needs a case); shares setUserFilter with the other paths.
  const treeHost = byId("filter-tree-host");
  const treeButton = byId("filter-tree-button");
  treeButton.addEventListener("click", () => {
    if (treeHost.hidden && caseId === null) {
      showMessage(S.needCase, true);
      return;
    }
    treeHost.hidden = !treeHost.hidden;
    treeButton.setAttribute("aria-expanded", treeHost.hidden ? "false" : "true");
    if (!treeHost.hidden && !filterTree) {
      filterTree = initFilterTree(treeHost, {
        api,
        caseId,
        initial: userFilter,
        onChange: (filter) => {
          userFilter = filter;
          applyView();
        },
        // Saved filters can include the toolbar filters; applying one replaces them.
        getExtraFilter: () => composeFilter({ sourceIds: [], text: searchText, ...formState }, null, toggles),
        onSavedApplied: () => {
          byId("filter-form").reset();
          byId("search-input").value = "";
          searchText = "";
          formState = { sevMax: null, levelMax: null, from: null, to: null };
          toggles = { hideNoise: false, unclassified: false };
        },
      });
    }
  });

  // Unknown-templates panel: optional module, the app works without it.
  const unknownHost = byId("unknown-panel-host");
  const unknownButton = byId("unknown-button");
  import("./unknown-panel.js").then((module) => {
    if (typeof module.initUnknownPanel !== "function") {
      return;
    }
    unknownButton.hidden = false;
    unknownButton.addEventListener("click", () => {
      unknownHost.hidden = !unknownHost.hidden;
      unknownButton.setAttribute("aria-expanded", unknownHost.hidden ? "false" : "true");
      if (!unknownHost.hidden) {
        if (!unknownPanel) {
          unknownPanel = module.initUnknownPanel(unknownHost, {
            api,
            getCase: () => caseId,
            getFamily: () => null,
            onOpenEntry: (entryId) => detail.show(entryId),
          });
        }
        unknownPanel.refresh();
      }
    });
  }).catch(() => {
    // Module missing or failing: the button stays hidden.
  });

  // Effective-configuration panel: optional module like the unknown-templates panel.
  const configHost = byId("config-panel-host");
  const configButton = byId("config-button");
  import("./config-panel.js").then((module) => {
    if (typeof module.initConfigPanel !== "function") {
      return;
    }
    configButton.hidden = false;
    configButton.addEventListener("click", () => {
      configHost.hidden = !configHost.hidden;
      configButton.setAttribute("aria-expanded", configHost.hidden ? "false" : "true");
      if (!configHost.hidden) {
        if (!configPanel) {
          configPanel = module.initConfigPanel(configHost, {
            api,
            getCase: () => caseId,
            onOpenEntry: (entryId) => detail.show(entryId),
          });
        }
        configPanel.refresh();
      }
    });
  }).catch(() => {
    // Module missing or failing: the button stays hidden.
  });

  // "Show in grid" of the derived-event panels: the grid's time filter becomes the event's window
  // and its first entry is selected.
  const showEventInGrid = async ({ from, to, entryId }) => {
    const forCase = caseId;
    await setUserFilter(withTsRange(userFilter, { from, to }));
    if (forCase !== caseId || !entryId) {
      return;
    }
    try {
      await grid.revealRow({ id: entryId, ts: from });
    } catch (err) {
      // The row may be filtered out (noise toggle): the detail pane still shows it.
    }
    const selected = grid.selectedRow();
    if (!selected || selected.id !== entryId) {
      detail.show(entryId);
    }
  };

  // Correlation: select a linked record in the grid (falls back to the detail pane when the
  // record is filtered out), then the optional modules of the Links section, the side-by-side
  // view and the "same key" panel.
  const revealEntry = async ({ id, ts }) => {
    const forCase = caseId;
    try {
      await grid.revealRow({ id, ts });
    } catch (err) {
      // The row may be filtered out: the detail pane still shows it.
    }
    if (forCase !== caseId) {
      return;
    }
    const selected = grid.selectedRow();
    if (!selected || selected.id !== id) {
      detail.show(id);
    }
  };
  Promise.all([import("./links.js"), import("./side-by-side.js"), import("./key-panel.js")]).then(([links, side, keys]) => {
    sideBySide = side.initSideBySide(byId("side-by-side-host"), { getCase: () => caseId });
    keyPanel = keys.initKeyPanel(byId("key-panel-host"), { getCase: () => caseId, onSelect: revealEntry });
    linksSection = new links.LinksSection(byId("detail-links"), {
      getCase: () => caseId,
      onReveal: revealEntry,
      onSideBySide: (id) => sideBySide.open(id),
      onSameKey: (type, value) => keyPanel.open(type, value),
      onError: showError,
    });
    if (detail.entry) {
      linksSection.show(detail.entry);
    }
  }).catch(() => {
    // Module missing or failing: the detail pane simply has no Links section.
  });

  // Payload section of the entry detail and the payload list panel: optional modules.
  import("./payload.js").then((module) => {
    payloadSection = new module.PayloadSection(byId("detail-payload"), {
      getCase: () => caseId,
      onReveal: revealEntry,
      onGoOperation: (eventId) => openOperation(eventId),
      onError: showError,
    });
    if (detail.entry) {
      payloadSection.show(detail.entry);
    }
  }).catch(() => {
    // Module missing or failing: the detail pane simply has no Payload section.
  });
  const payloadHost = byId("payload-list-host");
  const payloadButton = byId("payload-button");
  import("./payload-list.js").then((module) => {
    payloadButton.hidden = false;
    payloadHost.addEventListener("payload-list-closed", () => payloadButton.setAttribute("aria-expanded", "false"));
    payloadButton.addEventListener("click", () => {
      payloadHost.hidden = !payloadHost.hidden;
      payloadButton.setAttribute("aria-expanded", payloadHost.hidden ? "false" : "true");
      if (!payloadHost.hidden) {
        if (!payloadList) {
          payloadList = module.initPayloadList(payloadHost, { api, getCase: () => caseId, onOpen: revealEntry });
        }
        payloadList.refresh();
      }
    });
  }).catch(() => {
    // Module missing or failing: the button stays hidden.
  });
  // Highlight rules (panel, detail section, ]/[ navigation): optional module; the store is above.
  import("./highlight-panel.js").then((module) => {
    highlightUi = module.mountHighlights({
      toolbar: byId("toolbar"),
      beforeButton: byId("theme-button"),
      panelParent: byId("grid-panel"),
      beforePanel: byId("filter-form"),
      footer: document.querySelector(".shortcuts-help"),
      detailAnchor: byId("detail-bookmark"),
      store: highlightStore,
      getCase: () => caseId,
      getFilter: () => currentFilter,
      getSelectedRow: () => grid.selectedRow(),
      getTemplateCode: (id) => templateLabels.get(id) || "",
      reveal: (row) => grid.revealRow(row),
      showMessage,
    });
    highlightUi.showEntry(detail.entry);
  }).catch(() => {
    // Module missing or failing: rules still color the grid, without the panel.
  });
  // Distinct values of a field (drill-down into the filter) and "Group by template": optional module.
  Promise.all([import("./distinct-panel.js"), import("./distinct-model.js")]).then(([panelModule, model]) => {
    distinctUi = panelModule.mountDistinct({
      toolbar: byId("toolbar"),
      beforeButton: byId("theme-button"),
      panelParent: byId("grid-panel"),
      beforePanel: byId("filter-form"),
      api,
      getCase: () => caseId,
      getFilter: () => currentFilter,
      getFieldNames: () => Array.from(seenFields),
      hasCollections: () => isMulti(collections),
      getSourceName: (id) => sourceNames.get(id) || String(id),
      onDrill: (leaf, exclude) => setUserFilter(model.drillFilter(userFilter, leaf, exclude)),
      onReveal: revealEntry,
    });
  }).catch(() => {
    // Module missing or failing: no buttons.
  });
  // Declarative aggregation over the view (table, chart, CSV, drill-down): optional module.
  Promise.all([import("./aggregate-panel.js"), import("./aggregate-model.js")]).then(([panelModule, model]) => {
    aggregateUi = panelModule.mountAggregate({
      toolbar: byId("toolbar"),
      beforeButton: byId("theme-button"),
      panelParent: byId("grid-panel"),
      beforePanel: byId("filter-form"),
      api,
      getCase: () => caseId,
      getFilter: () => currentFilter,
      getFieldNames: () => Array.from(seenFields),
      onDrill: (drill) => setUserFilter(model.drillFilter(userFilter, drill)),
    });
  }).catch(() => {
    // Module missing or failing: no Aggregate button.
  });
  // Chained views (breadcrumbs, Narrow this view, Show in parent/root): optional modules.
  Promise.all([import("./views-stack.js"), import("./views-stack-model.js")]).then(([uiModule, model]) => {
    viewsStack = model.createViewsStack({ ownFilter: ownFilterOf, getLive: captureLive });
    viewsUi = uiModule.mountViewsStack({
      parent: byId("grid-panel"),
      beforePanel: byId("filter-form"),
      footer: document.querySelector(".shortcuts-help"),
      stack: viewsStack,
      storage: (() => {
        try {
          return window.sessionStorage;
        } catch (err) {
          return null;
        }
      })(),
      getCase: () => caseId,
      restoreState: restoreLive,
      applyView: () => applyView(),
      getSelectedRow: () => grid.selectedRow(),
      reveal: (row) => grid.revealRow(row),
      showMessage,
    });
    if (caseId !== null) {
      const saved = viewsUi.onCase(caseId);
      if (saved) {
        restoreLive(saved);
        applyView();
      }
    }
  }).catch(() => {
    // Module missing or failing: no chained views.
  });
  // Bookmarks (detail section, panel, shortcuts) and "Compare with...": optional modules.
  const bookmarksHost = byId("bookmarks-host");
  const bookmarksButton = byId("bookmarks-button");
  const toggleBookmarksPanel = () => {
    if (!bookmarksPanel) {
      return;
    }
    if (bookmarksPanel.isOpen()) {
      bookmarksPanel.close();
    } else if (caseId === null) {
      showMessage(S.needCase, true);
      return;
    } else {
      bookmarksPanel.open();
    }
    bookmarksButton.setAttribute("aria-expanded", bookmarksPanel.isOpen() ? "true" : "false");
  };
  import("./bookmarks-panel.js").then((module) => {
    detailBookmark = module.initDetailBookmark(byId("detail-bookmark"), { store: bookmarkStore, onError: showError });
    bookmarksPanel = module.initBookmarksPanel(bookmarksHost, {
      getCase: () => caseId,
      store: bookmarkStore,
      onReveal: revealEntry,
      onError: showError,
    });
    bookmarksButton.hidden = false;
    bookmarksButton.addEventListener("click", toggleBookmarksPanel);
    if (detail.entry) {
      detailBookmark.show(detail.entry);
    }
  }).catch(() => {
    // Module missing or failing: no bookmarks UI.
  });

  const compareCurrent = () => {
    const current = detail.entry;
    if (!current || caseId === null) {
      showMessage(S.cmpNeedRecord, false);
      return;
    }
    const step = compareSelection.pick(current.id);
    if (step.action === "mark") {
      showMessage(format(S.cmpPicked, { id: step.a }), false);
    } else if (step.action === "cancel") {
      showMessage(S.cmpCancelled, false);
    } else if (step.action === "compare" && compare) {
      showMessage("", false);
      compare.open(step.a, step.b);
    }
    paintCompareButton(detail.entry);
  };
  compareButton.addEventListener("click", compareCurrent);
  import("./compare.js").then((module) => {
    compare = module.initCompare(byId("compare-host"), { getCase: () => caseId });
  }).catch(() => {
    compareButton.hidden = true; // module missing or failing: no compare
  });

  // Shortcuts: b toggles the bookmark of the selected record, B opens the panel, c compares.
  document.addEventListener("keydown", async (event) => {
    const action = bookmarkKey(event);
    if (!action || isTypingTarget(event.target) || caseId === null) {
      return;
    }
    if (action === "panel") {
      event.preventDefault();
      toggleBookmarksPanel();
      return;
    }
    const row = grid.selectedRow();
    if (!row) {
      showMessage(S.navNoSelection, false);
      return;
    }
    event.preventDefault();
    if (action === "toggle") {
      try {
        await bookmarkStore.toggle(row.id);
      } catch (err) {
        showError(err);
      }
    } else if (detail.entry && detail.entry.id === row.id) {
      compareCurrent();
    } else {
      detail.show(row.id).then(() => compareCurrent());
    }
  });

  import("./follow-tail.js").then((module) => {
    followTail = module.initFollowTail({
      toggle: byId("follow-tail"),
      newButton: byId("follow-new"),
      grid,
      getCase: () => caseId,
      getFilter: () => currentFilter,
      onChanged: () => {
        refreshDerived();
        panel.refreshSources(true).catch(showError);
      },
      onError: showError,
    });
  }).catch(() => {
    // Module missing or failing: the toggle does nothing.
  });

  // Local alerts (9.13): banner, panel and optional browser notifications; optional module.
  const alertsHost = byId("alerts-host");
  const alertsButton = byId("alerts-button");
  import("./alerts-panel.js").then((module) => {
    alertsUi = module.initAlerts(alertsHost, {
      banner: byId("alert-banner"),
      getCase: () => caseId,
      getFilter: () => userFilter,
      onShowRecords: (filter) => setUserFilter(filter).catch(showError),
      onError: showError,
      onClose: () => alertsButton.setAttribute("aria-expanded", "false"),
    });
    alertsButton.hidden = false;
    alertsButton.addEventListener("click", () => {
      if (alertsUi.isOpen()) {
        alertsUi.close();
      } else if (caseId === null) {
        showMessage(S.needCase, true);
        return;
      } else {
        alertsUi.open();
      }
      alertsButton.setAttribute("aria-expanded", alertsUi.isOpen() ? "true" : "false");
    });
    alertsUi.onCase();
  }).catch(() => {
    // Module missing or failing: no alerts UI.
  });

  // Operations panel, adapter dashboard and clock-jump banner: optional modules like the panels above.
  const eventsHost = byId("events-panel-host");
  const eventsButton = byId("events-button");
  import("./events-panel.js").then((module) => {
    if (typeof module.initEventsPanel !== "function") {
      return;
    }
    eventsButton.hidden = false;
    const ensureEventsPanel = () => {
      if (!eventsPanel) {
        eventsPanel = module.initEventsPanel(eventsHost, {
          api: derivedApi,
          getCase: () => caseId,
          onShowInGrid: showEventInGrid,
        });
      }
    };
    eventsButton.addEventListener("click", () => {
      eventsHost.hidden = !eventsHost.hidden;
      eventsButton.setAttribute("aria-expanded", eventsHost.hidden ? "false" : "true");
      if (!eventsHost.hidden) {
        ensureEventsPanel();
        eventsPanel.refresh();
      }
    });
    // "Go to operation" of the Payload section: shows the panel (loading its list when it was
    // hidden) and opens the event in its detail pane.
    openOperation = (eventId) => {
      const wasHidden = eventsHost.hidden;
      eventsHost.hidden = false;
      eventsButton.setAttribute("aria-expanded", "true");
      ensureEventsPanel();
      if (wasHidden) {
        eventsPanel.refresh();
      }
      eventsPanel.open(eventId);
    };
  }).catch(() => {
    // Module missing or failing: the button stays hidden.
  });

  // Export report and redaction settings: optional modules, the app works without them.
  const exportHost = byId("export-panel-host");
  const redactionHost = byId("redaction-panel-host");
  const exportButton = byId("export-button");
  const exportContext = () => {
    const selected = grid.selectedRow();
    const entry = detail.entry;
    const sameEntry = Boolean(selected && entry && entry.id === selected.id);
    return {
      caseId,
      filter: currentFilter,
      recordId: selected ? selected.id : null,
      eventId: sameEntry && entry.event ? entry.event.id : null,
    };
  };
  Promise.all([import("./export-panel.js"), import("./redaction-panel.js")]).then(([exporting, redacting]) => {
    let redactionPanel = null;
    const openRedaction = () => {
      redactionHost.hidden = false;
      if (!redactionPanel) {
        redactionPanel = redacting.initRedactionPanel(redactionHost);
      }
      redactionPanel.refresh();
    };
    exportButton.hidden = false;
    exportButton.addEventListener("click", () => {
      exportHost.hidden = !exportHost.hidden;
      exportButton.setAttribute("aria-expanded", exportHost.hidden ? "false" : "true");
      if (exportHost.hidden) {
        redactionHost.hidden = true;
        return;
      }
      if (!exportPanel) {
        exportPanel = exporting.initExportPanel(exportHost, {
          getContext: exportContext,
          onOpenRedaction: openRedaction,
        });
      }
      exportPanel.refresh();
    });
  }).catch(() => {
    // Module missing or failing: the button stays hidden.
  });

  // "Copy as" menu (lists, original lines, CSV): optional module, the app works without it.
  import("./copy-export.js").then((module) => {
    copyExport = module.initCopyExport(exportButton, {
      getContext: () => ({
        caseId,
        filter: currentFilter,
        columns: grid.visibleColumns,
        fieldColumns: grid.fieldColumns,
        recordId: detail.entry ? detail.entry.id : null,
        entry: detail.entry,
      }),
    });
  }).catch(() => {
    // Module missing or failing: no menu.
  });

  const dashboardHost = byId("dashboard-host");
  const dashboardButton = byId("dashboard-button");
  import("./dashboard.js").then((module) => {
    if (typeof module.initDashboard !== "function") {
      return;
    }
    dashboardButton.hidden = false;
    dashboardButton.addEventListener("click", () => {
      dashboardHost.hidden = !dashboardHost.hidden;
      dashboardButton.setAttribute("aria-expanded", dashboardHost.hidden ? "false" : "true");
      if (!dashboardHost.hidden) {
        if (!dashboard) {
          dashboard = module.initDashboard(dashboardHost, {
            api: derivedApi,
            getCase: () => caseId,
            onShowInGrid: showEventInGrid,
          });
        }
        dashboard.refresh();
      }
    });
  }).catch(() => {
    // Module missing or failing: the button stays hidden.
  });

  import("./clock-banner.js").then((module) => {
    if (typeof module.initClockBanner !== "function") {
      return;
    }
    clockBanner = module.initClockBanner(byId("clock-banner"), {
      api: derivedApi,
      getCase: () => caseId,
      onShowInGrid: showEventInGrid,
    });
    clockBanner.refresh();
  }).catch(() => {
    // Module missing or failing: no banner.
  });

  // Shortcuts: n/N next/previous error, t/T next/previous entry of the selected row's template.
  document.addEventListener("keydown", async (event) => {
    const nav = navKey(event);
    if (!nav || isTypingTarget(event.target) || caseId === null) {
      return;
    }
    const row = grid.selectedRow();
    if (!row) {
      showMessage(S.navNoSelection, false);
      return;
    }
    if (nav.kind === "template" && !(row.tpl_id > 0)) {
      showMessage(S.navNoTemplate, false);
      return;
    }
    event.preventDefault();
    const extra = nav.kind === "error" ? NAV_ERROR_FILTER : { tpl: [row.tpl_id] };
    const request = navRequest(currentFilter, extra, row, nav.dir);
    const { seq, signal } = navRequests.begin();
    const forCase = caseId;
    try {
      const page = await api.page(forCase, request.filter, request.cursor, request.dir, request.limit, signal);
      if (!navRequests.isCurrent(seq) || forCase !== caseId) {
        return;
      }
      const found = page.rows && page.rows[0];
      if (!found) {
        showMessage(S.navNone, false);
        return;
      }
      showMessage("", false);
      await grid.revealRow(found);
    } catch (err) {
      if (!(err && err.name === "AbortError") && navRequests.isCurrent(seq)) {
        showError(err);
      }
    }
  });

  panel.loadCases(null).catch(showError);
}

start();
