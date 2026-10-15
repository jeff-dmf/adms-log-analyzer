// Orchestration: wires the panels together. Everything shown comes from strings.js or the API
// and reaches the page through textContent / DOM nodes only.
import { createActionRegistry } from "./actions.js";
import { registerAppActions, registerAppPanels } from "./app-actions.js";
import { api, onForbidden } from "./api.js";
import { createBookmarkStore } from "./bookmarks-model.js";
import { createHighlightStore } from "./highlight-model.js";
import { compareButtonLabel, createCompareSelection } from "./compare-model.js";
import { CaseFiles } from "./case-files.js";
import { Detail } from "./detail.js";
import {
  NAV_ERROR_FILTER, TPL_LEAF, andFilters, bookmarkKey, composeFilter, createLatestRequest, ignoresShortcut, navKey, navRequest, parseTplList, stateFromForm,
  timeSpan, withTemplate, withTsRange,
} from "./filters.js";
import { registerLeafType } from "./filter-tree-model.js";
import { registerLeafEditor } from "./filter-tree.js";
import { initTimeline } from "./timeline.js";
import { createDock } from "./dock-legacy.js";
import { COLUMNS, Grid } from "./grid.js";
import { CollectionsPanel } from "./collections-panel.js";
import {
  columnShown, effectiveSourceIds, gridTags, isMulti, loadColumnPref, saveColumnPref, scopeApi,
} from "./collections-model.js";
import { KnowledgeSection } from "./knowledge.js";
import { mountMenubar } from "./menubar.js";
import { searchTerms } from "./search.js";
import { SourceFilter } from "./source-filter.js";
import { SourcesPanel } from "./sources.js";
import { S, format } from "./strings.js";
import { applyTheme, setThemePreference, themePreference } from "./theme.js";
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

  // Until the status bar of UI-13 exists, ctx.status is a small adapter over #message with the final API.
  const status = {
    message: (text, { kind = "info" } = {}) => {
      showMessage(text, kind === "error");
      return { dismiss: () => showMessage("", false) };
    },
    count() {}, position() {}, progress() {}, connection() {}, slot: () => null,
  };
  const actions = createActionRegistry({ onError: (err) => showError(err), singleKeysOn: () => true });
  // Step 1 of the dock: it exists before the context that holds it; bind(ctx) is step 2.
  const dock = createDock({
    root: byId("grid-panel"),
    before: byId("filter-form"),
    storage: safeLocalStorage(),
    onFocusOut: () => byId("grid-viewport").focus(),
  });
  // The optional modules loaded at the opening: their actions depend on these promises (a failure disables the item).
  const settled = (promise) => {
    promise.catch(() => {}); // the registry reports the failure; this only avoids an unhandled rejection
    return promise;
  };
  const ready = { alerts: null, followTail: null, copyExport: null };

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
  let followTail = null;
  let clockBanner = null;
  let knowledge = null;
  let linksSection = null;
  let payloadSection = null;
  let copyExport = null;
  let menubar = null;
  let sourceFamilies = new Map();
  // API for the panels fed by derived events: their queries carry the chosen collection.
  const derivedApi = scopeApi(api, () => (collectionsPanel ? collectionsPanel.selectedGroups() : []));
  const seenFields = new Set(readList(FIELD_NAMES_KEY));
  let fieldColumns = readList(FIELD_COLUMNS_KEY);
  const navRequests = createLatestRequest(); // n/N/t/T lookups; a view change makes them stale
  let bookmarksPanel = null;
  let detailBookmark = null;
  let alertsUi = null; // local alerts: banner, stream and counter (alerts-panel.js startAlerts)
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
      if (kind === "edit" && bookmarksPanel && dock.isOpen("bookmarks")) {
        bookmarksPanel.refresh();
      }
    },
  });

  // Highlight rules of the case: colors of the grid rows (highlight-model.js).
  let highlightUi = null; // detail section and ]/[ navigation
  let highlightPanel = null; // the dock panel
  const highlightStore = createHighlightStore({
    api,
    getCase: () => caseId,
    onChange: () => {
      grid.render();
      if (highlightUi) {
        highlightUi.storeChanged();
      }
      if (highlightPanel) {
        highlightPanel.storeChanged();
      }
    },
  });

  // Panels fed by the derived events: open ones reload, closed ones load when opened (dock.notify).
  const refreshDerived = () => {
    if (clockBanner) {
      clockBanner.refresh();
    }
    dock.notify("derived");
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
    dock.notify("view"); // Distinct values, Group by message type, Aggregate
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
      dock.caseChanged(); // every mounted panel forgets the old case; the ones tied to it close
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
      if (copyExport) {
        copyExport.reset();
      }
      dock.notify("case");
      refreshDerived();
      if (followTail) {
        followTail.onCase();
      }
      if (alertsUi) {
        alertsUi.onCase();
      }
      if (menubar) {
        menubar.refresh();
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
      loadTemplates(caseId);
      knowledge.reset(); // the ERS configuration may have been read again
      dock.notify("indexed"); // Bookmarks, Unknown message types, Effective configuration
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
  // View > Columns… opens this popover under the View button; Escape or a click outside closes it.
  const viewButton = byId("view-button");
  const closeColumns = (focusBack) => {
    if (menu.hidden) {
      return;
    }
    menu.hidden = true;
    if (focusBack) {
      viewButton.focus();
    }
  };
  const openColumns = () => {
    const box = viewButton.getBoundingClientRect();
    menu.hidden = false;
    menu.style.top = Math.round(box.bottom) + "px";
    menu.style.left = Math.max(0, Math.min(Math.round(box.left), window.innerWidth - menu.offsetWidth - 8)) + "px";
    const first = menu.querySelector("input");
    if (first) {
      first.focus();
    }
  };
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      closeColumns(true);
    }
  });
  document.addEventListener("pointerdown", (event) => {
    if (!menu.hidden && !menu.contains(event.target) && !viewButton.contains(event.target)) {
      closeColumns(false);
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

  // The panels (filter tree, Analyze, Export, ...) mount in the dock the first time they open; see app-actions.js.

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
  // record is filtered out), then the Links section (its side-by-side and "same key" panels are dock panels).
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
  import("./links.js").then((links) => {
    linksSection = new links.LinksSection(byId("detail-links"), {
      getCase: () => caseId,
      onReveal: revealEntry,
      onSideBySide: (id) => dock.open("sideBySide", { entryId: id }),
      onSameKey: (type, value) => dock.open("sameKey", { type, value }),
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
      onGoOperation: (eventId) => dock.open("operations", { eventId }),
      onError: showError,
    });
    if (detail.entry) {
      payloadSection.show(detail.entry);
    }
  }).catch(() => {
    // Module missing or failing: the detail pane simply has no Payload section.
  });
  // Highlight rules (detail section, ]/[ navigation): optional module; the store is above. The panel is a dock panel.
  import("./highlight-panel.js").then((module) => {
    highlightUi = module.mountHighlights({
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
    // Module missing or failing: rules still color the grid, without the detail section.
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
  // Bookmarks (detail section, shortcuts) and "Compare with...": optional modules. The panel is a dock panel.
  import("./bookmarks-panel.js").then((module) => {
    detailBookmark = module.initDetailBookmark(byId("detail-bookmark"), { store: bookmarkStore, onError: showError });
    if (detail.entry) {
      detailBookmark.show(detail.entry);
    }
  }).catch(() => {
    // Module missing or failing: no bookmark section in the detail pane.
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
    } else if (step.action === "compare") {
      showMessage("", false);
      dock.open("compare", { a: step.a, b: step.b });
    }
    paintCompareButton(detail.entry);
  };
  compareButton.addEventListener("click", compareCurrent);

  // Shortcuts: b toggles the bookmark of the selected record, B opens the panel, c compares.
  document.addEventListener("keydown", async (event) => {
    const action = bookmarkKey(event);
    if (!action || ignoresShortcut(event) || caseId === null) {
      return;
    }
    if (action === "panel") {
      event.preventDefault();
      actions.invoke("toggle.bookmarks", ctx);
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

  ready.followTail = settled(import("./follow-tail.js").then((module) => {
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
  }));

  // Local alerts (9.13): banner, stream and counter run from the opening; the panel is a dock panel.
  ready.alerts = settled(import("./alerts-panel.js").then((module) => {
    alertsUi = module.startAlerts({
      banner: byId("alert-banner"),
      getCase: () => caseId,
      onShowRecords: (filter) => setUserFilter(filter).catch(showError),
    });
    alertsUi.on("count", (n) => {
      if (menubar) {
        menubar.setAlertsCount(n);
      }
    });
    alertsUi.onCase();
  }));

  // Export report and "Copy as": the report is a dock panel; the "Copy as" popover hangs under the Export menu.
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
  ready.copyExport = settled(import("./copy-export.js").then((module) => {
    copyExport = module.initCopyExport(byId("export-button"), {
      getContext: () => ({
        caseId,
        filter: currentFilter,
        columns: grid.visibleColumns,
        fieldColumns: grid.fieldColumns,
        recordId: detail.entry ? detail.entry.id : null,
        entry: detail.entry,
      }),
    });
  }));

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
    if (!nav || ignoresShortcut(event) || caseId === null) {
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

  // ---- The action registry, the dock and the top bar. Everything is registered here, synchronously, so every
  // item of every menu exists from the first paint (see app-actions.js).
  const ctx = Object.freeze({
    caseId: () => caseId,
    filter: () => currentFilter,
    userFilter: () => userFilter,
    selectedRow: () => grid.selectedRow(),
    entry: () => detail.entry,
    setUserFilter: (filter) => setUserFilter(filter),
    reveal: (row) => revealEntry(row),
    actions,
    dock,
    status,
  });
  dock.bind(ctx); // step 2 of the dock
  const caseSelect = byId("case-select");
  const deps = {
    api,
    derivedApi,
    highlightStore,
    bookmarkStore,
    ready,
    onError: showError,
    fieldNames: () => Array.from(seenFields),
    hasCollections: () => isMulti(collections),
    sourceName: (id) => sourceNames.get(id) || String(id),
    showEntry: (id) => detail.show(id),
    reveal: (row) => revealEntry(row),
    showEventInGrid,
    exportContext,
    filterTreeOptions: () => ({
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
    }),
    onFilterTree: (tree) => {
      filterTree = tree;
    },
    onHighlightPanel: (highlights) => {
      highlightPanel = highlights;
    },
    onBookmarksPanel: (bookmarks) => {
      bookmarksPanel = bookmarks;
    },
    alerts: () => alertsUi,
    cases: () => Array.from(caseSelect.options).filter((o) => o.value !== "").map((o) => ({ id: o.value, name: o.textContent })),
    selectCase: (id) => {
      if (panel.queueBusy) {
        showMessage(S.cfQueueBusy, true);
        return undefined;
      }
      return panel.selectCase(id);
    },
    closeCase: () => deps.selectCase(null),
    focusNewCase: () => byId("new-case-name").focus(),
    focusAddFiles: () => {
      const target = byId("add-files-host").querySelector("button, input");
      (target || byId("path-input")).focus();
    },
    openColumns,
    copyExportOpen: () => copyExport && copyExport.open(),
    followTailOn: () => byId("follow-tail").checked,
    toggleFollowTail: () => {
      const box = byId("follow-tail");
      box.checked = !box.checked;
      box.dispatchEvent(new Event("change"));
    },
    theme: themePreference,
    setTheme: (choice) => setThemePreference(choice),
    shortcutsText: () => [S.shortcutsHelp, S.hlShortcutsHelp, S.vsShortcutsHelp].join(" "),
  };
  registerAppPanels(dock, deps);
  registerAppActions(actions, deps);
  const toggleButtons = Array.from(document.querySelectorAll("#toolbar [data-action]"));
  menubar = mountMenubar({
    actions,
    ctx,
    getCaseLabel: () => {
      const option = caseSelect.selectedOptions && caseSelect.selectedOptions[0];
      return caseId !== null && option ? option.textContent : "";
    },
    refs: {
      caseButton: byId("case-button"),
      caseLabel: byId("case-button-label"),
      toggles: toggleButtons,
      menus: { analyze: byId("analyze-button"), export: byId("export-button"), view: byId("view-button") },
      caseControls: [...byId("search-form").querySelectorAll("input, button"), ...byId("goto-form").querySelectorAll("input, button")],
      alertsBadge: byId("alerts-count"),
    },
  });
  byId("toolbar").classList.add("is-ready"); // texts and states are in place: show the bar (css/topbar.css)
  dock.on("open", menubar.refresh);
  dock.on("close", menubar.refresh);
  for (const control of toggleButtons) {
    actions.preload(control.dataset.action); // the modules behind the toggles are loaded at the opening anyway
  }

  panel.loadCases(null).catch(showError);
}

// If the opening fails the bar is not left invisible: it is shown, with an alert that says what happened
// (css/topbar.css also reveals it by itself after a few seconds if this script never ran).
try {
  start();
} catch (err) {
  const bar = byId("toolbar");
  if (bar) {
    bar.classList.add("is-ready", "is-failed");
    const alert = document.createElement("span");
    alert.className = "topbar-failed";
    alert.setAttribute("role", "alert");
    alert.textContent = S.errorStartup;
    bar.appendChild(alert);
  }
  throw err;
}
