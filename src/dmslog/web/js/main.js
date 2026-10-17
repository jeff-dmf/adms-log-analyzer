// Orchestration: wires the panels together. Everything shown comes from strings.js or the API
// and reaches the page through textContent / DOM nodes only.
import { createActionRegistry } from "./actions.js";
import { registerAppActions, registerAppPanels } from "./app-actions.js";
import { api, isRetryable, onForbidden, onNetworkError } from "./api.js";
import { afterDrawerOpen, createButtonGate, loadSections, mountLate, openWhenLoaded, retryable } from "./late-init.js";
import { takeCollections } from "./prefetch.js";
import { createBookmarkStore } from "./bookmarks-model.js";
import { createHighlightStore } from "./highlight-model.js";
import { compareButtonLabel, createCompareSelection } from "./compare-model.js";
import { CaseFiles } from "./case-files.js";
import { Detail, adoptHeading } from "./detail.js";
import {
  andFilters, composeFilter, createLatestRequest, navPlan, stateFromForm,
  timeSpan, withTemplate, withTsRange,
} from "./filters.js";
import { initTimeline } from "./timeline.js";
import { createDock } from "./dock.js";
import { COLUMNS, Grid } from "./grid.js";
import { createViewController } from "./grid-model.js";
import { CollectionsPanel } from "./collections-panel.js";
import {
  columnShown, effectiveSourceIds, gridTags, isMulti, loadColumnPref, saveColumnPref, scopeApi, tagMap,
} from "./collections-model.js";
import { resetFormWithErrors, setFieldError } from "./field-error.js";
import { createStatusBar } from "./status-bar.js";
import { createFailureCenter, fallbackTarget } from "./status-bar-model.js";
import { KnowledgeSection } from "./knowledge.js";
import { mountMenubar } from "./menubar.js";
import { searchTerms } from "./search.js";
import { SourceFilter } from "./source-filter.js";
import { SourcesPanel } from "./sources.js";
import { S, format, pluralKey } from "./strings.js";
import { applyTheme, setThemePreference, themePreference } from "./theme.js";
import { formatTs, parseTs } from "./time.js";

const COLUMNS_KEY = "dmslog.columns";
const FIELD_COLUMNS_KEY = "dmslog.fieldColumns";
const FIELD_NAMES_KEY = "dmslog.fieldNames";
const MAX_FIELD_NAMES = 200;
const FIRST_VIEW_GRACE_MS = 4000; // the modules of the opening that wait for the first view do not wait longer

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

  // The status bar (UI-13). showMessage stays as the adapter the modules already call.
  const status = createStatusBar(byId("status-bar"), {
    S,
    // Retry / Dismiss disappeared while focused: a shown target (the grid; with no case, the empty state's button).
    focusFallback: () => {
      const target = fallbackTarget({
        query: (selector) => document.querySelector(selector),
        noCase: document.body.classList.contains("no-case"),
      });
      if (target) {
        target.focus();
      }
    },
    // The "?" button opens the shortcuts dialog (help.shortcuts); actions and ctx exist by the time it is clicked.
    onHelp: () => actions.invoke("help.shortcuts", ctx),
  });
  const showMessage = (text, isError) => status.message(text, { kind: isError ? "error" : "info" });
  // Connection errors already own the bar: do not repeat them as a message.
  const showError = (err) => {
    if (err && (err.code === "network" || err.code === "forbidden")) {
      return;
    }
    showMessage(err && err.message ? err.message : S.errorGeneric, true);
  };
  // UI-25: a failed request never empties the screen. The grid keeps its rows; the notice (with Retry, unless repeating
  // the request cannot help) sits in the status bar; a view change that did not load goes back to the last view that did.
  // The decisions live in status-bar-model.js (createFailureCenter) and grid-model.js (createViewController); here
  // they are only connected to the page.
  let casesLoaded = false; // the opening (GET /api/cases) finished: until then Retry reloads the cases
  const failures = createFailureCenter({
    status,
    retryLabel: S.sbRetry,
    genericMessage: S.errorGeneric,
    pageHint: S.gridPageHint,
    goOffline: (onRetry) => status.connection("offline", { onRetry }),
    health: () => api.health(),
    setOnline: () => status.connection("online"),
    openingPending: () => !casesLoaded,
    reloadCases: () => panel.loadCases(null).then(() => { casesLoaded = true; }),
    fallback: () => (caseId !== null ? applyView() : Promise.resolve()),
    onError: (err) => showError(err),
    retryView: () => retryView(),
    hasViewRetry: () => viewFlow.retryTarget() !== null,
    dropViewRetry: () => viewFlow.dropRetry(),
    retryGrid: () => grid.retry(),
    retryCount: () => grid.retryCount(),
    refreshTimeline: () => (timeline ? timeline.refreshNow() : Promise.resolve()),
    resumeStream: () => (followTail ? followTail.resume() : false),
    // Another case: the requests of the old one (rows, count, histogram) are cancelled now, not when the new sources
    // arrive; the stream is closed and reopened by followTail.onCase() further down.
    cancelRequests: () => {
      viewFlow.caseChanged();
      grid.closeCase();
      if (timeline) {
        timeline.invalidate();
      }
    },
  });
  onForbidden(() => status.connection("forbidden"));
  onNetworkError(() => failures.goOffline());

  // The keyboard layer (shortcuts.js: the one keydown listener, F6 regions, the shortcuts dialog) loads after the first
  // view; until then the single-key preference is not known and the keys do nothing.
  let shortcuts = null;
  const actions = createActionRegistry({
    onError: (err) => showError(err),
    singleKeysOn: () => (shortcuts === null ? true : shortcuts.singleKeys()),
  });
  // Step 1 of the dock: it exists before the context that holds it; bind(ctx) is step 2.
  const dock = createDock({
    root: byId("dock"),
    storage: safeLocalStorage(),
    onFocusOut: () => byId("grid-viewport").focus(),
  });
  // The optional modules loaded at the opening: their actions depend on these promises (a failure disables the item).
  const settled = (promise) => {
    promise.catch(() => {}); // the registry reports the failure; this only avoids an unhandled rejection
    return promise;
  };
  const ready = { alerts: null, followTail: null, drawers: null, repeats: null };
  // Sources rail and drawers (UI-17): the layout is CSS (css/responsive.css); drawers.js adds the open state, the focus,
  // Escape and the "selected/total" count on the rail. It loads at once (not after the first view): below 1600 px it is
  // the only way to the sources panel.
  let drawers = null;
  const updateRail = () => {
    if (drawers) {
      drawers.setSources(panel.selectedIds().length, sources.length);
    }
  };

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
  let templateTitles = new Map(); // template id -> title only (no fallback to the code): the same-key panel
  let filterTree = null;
  let timeline = null;
  let followTail = null;
  let repeats = null; // repeated records: marks, hide chip, window (repeat-model.js, loaded after the first view)
  let repeatDetail = null; // the "Repeat of #N" line of the detail
  let clockBanner = null;
  let knowledge = null;
  let linksSection = null;
  let payloadSection = null;
  let copyExport = null;
  let menubar = null;
  let appActions = null;
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
  let filterChips = null; // the chips of the filter strip (filter-chips.js)
  let removingChip = false; // a chip removal clears the side selection and applies ONE view afterwards
  const compareSelection = createCompareSelection();
  const compareButton = byId("compare-button");
  function paintCompareButton(entry) {
    detail.paintCompare(); // the detail asks getCompareLabel for the record it shows
  }

  // Bookmark markers shared by the grid, the detail pane and the panel.
  const bookmarkStore = createBookmarkStore({
    api,
    getCase: () => caseId,
    onChange: (kind) => {
      grid.render();
      detail.refreshBookmark();
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
  // "Show in grid" of the same-key panel: a chained view (a chip in the strip) with the key filter. A view with its own
  // conditions is narrowed first; an empty one just gets the chip; a full stack says so and changes nothing.
  const loadKeyModel = retryable(() => import("./key-panel-model.js"));
  let childFilter = null; // set only while viewsUi.narrow() runs: the new child view starts holding it (one reload)
  const showKeyInGrid = async (arg) => {
    const forCase = caseId;
    if (forCase === null) {
      return;
    }
    try {
      const model = await loadKeyModel();
      if (caseId !== forCase) {
        return; // the case changed while the module loaded
      }
      await model.createShowKeyInGrid({
        getCase: () => caseId,
        getUserFilter: () => userFilter,
        getParentsFilter: () => (viewsStack ? viewsStack.parentsFilter() : null),
        state: () => {
          const chained = viewsStack !== null && viewsUi !== null;
          return {
            chained,
            ownEmpty: ownFilterOf(captureLive()) === null,
            depth: chained ? viewsStack.depth : 0,
            maxDepth: chained ? viewsStack.maxDepth : 0,
          };
        },
        narrowWith: (filter) => {
          const before = viewsStack.depth;
          childFilter = filter;
          try {
            viewsUi.narrow(); // restoreLive() gives the child its filter; the single reload of narrow() loads the final state
          } finally {
            childFilter = null;
          }
          return viewsStack.depth !== before;
        },
        applyHere: (filter) => setUserFilter(andFilters(userFilter, filter)),
        message: showMessage,
        onError: showError,
      })(arg);
    } catch (err) {
      showError(err);
    }
  };
  const addTemplateFilter = (tplId) => setUserFilter(withTemplate(userFilter, tplId));

  const loadTemplates = async (forCase) => {
    if (forCase === null) {
      templateLabels = new Map();
      templateTitles = new Map();
      return;
    }
    templateTitles = new Map(); // titles of another case never show while this one loads
    try {
      const data = await api.templates(forCase, null);
      if (forCase === caseId) {
        templateLabels = new Map((data.templates || []).map((t) => [t.id, t.title || t.code]));
        templateTitles = new Map((data.templates || []).filter((t) => t.title).map((t) => [t.id, t.title]));
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
    onCount: (value, options) => status.count(value, options),
    onPosition: (ts) => status.position(ts),
    onError: showError,
    onFailure: (err, info) => failures.gridFailure(err, info),
    onRecovered: (scope) => failures.gridRecovered(scope),
    // The user navigated before the new view's first page: the controls go back to the view the rows belong to.
    onAbandon: () => viewFlow.abandon(),
    getMark: (id) => bookmarkStore.get(id),
    getHighlight: (id) => highlightStore.get(id),
    getRepeat: (id) => (repeats ? repeats.get(id, grid.caseId) : null), // the grid's rows belong to ITS case
    onVisible: (ids) => {
      bookmarkStore.ensure(ids);
      highlightStore.ensure(ids);
      if (repeats) {
        repeats.ensure(ids, grid.caseId);
      }
    },
    onView: () => followTail && followTail.viewChanged(),
  });
  const detail = new Detail({
    empty: byId("detail-empty"),
    meta: byId("detail-meta"),
    text: byId("detail-text"),
    actions: byId("detail-actions"),
    contextButton: byId("context-button"),
    contextClose: byId("context-close"),
    contextSame: byId("context-same"),
    contextBox: byId("context-box"),
    contextList: byId("context-list"),
    copyButton: byId("copy-button"),
    compareButton: byId("compare-button"),
    bookmarkButton: byId("bookmark-button"),
    highlightButton: byId("highlight-button"),
    copyStatus: byId("copy-status"),
    template: byId("detail-template"),
    fields: byId("detail-fields"),
  }, {
    onEntry: (entry) => {
      if (entry) {
        loadDetailModules();
      }
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
      if (repeatDetail) {
        repeatDetail.show(entry);
      }
    },
    getCompareLabel: (entry) => compareButtonLabel(compareSelection.first, entry ? entry.id : null),
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
    runAction: (id, args) => actions.invoke(id, ctx, args), // the record actions (app-actions.js); ctx exists by the first click
    getSourceInfo: (id) => {
      const source = sources.find((item) => item.id === id);
      const tags = isMulti(collections) ? gridTags(collections) : null; // the collection is said only when there are several
      const tag = source && tags && tags.get(source.collection);
      return { name: sourceNames.get(id) || String(id), sensitive: Boolean(source && source.sensitive), tag: tag ? tag.tag : "" };
    },
    isBookmarked: (id) => Boolean(bookmarkStore.get(id)),
    onBookmarkToggle: (id) => bookmarkStore.toggle(id).catch(showError),
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
    userFilter = childFilter !== null ? childFilter : state.userFilter;
    if (filterTree) {
      filterTree.setFilter(userFilter);
    }
  };
  const ownFilterOf = (state) =>
    composeFilter({ sourceIds: [], text: state.searchText, ...state.formState }, state.userFilter, state.toggles);

  // The modules that are not needed to paint the first rows (Follow tail, alerts, clock banner) load once the first
  // view has answered (or after a safety delay): the grid is never waiting for code it does not use.
  let firstViewDone = () => {};
  const afterFirstView = new Promise((resolve) => {
    firstViewDone = resolve;
    setTimeout(resolve, FIRST_VIEW_GRACE_MS);
  });

  // The view change did not load: the toolbar, the filter and the trail go back to the last view that did (the grid
  // still shows it); the attempt is kept for Retry unless the request itself is wrong (a 4xx cannot succeed twice).
  // The side panel's selection is part of the view: sources and collections go back with the toolbar.
  const restoreSelection = (selection) => {
    panel.restoreSelection(selection.ids);
    if (collectionsPanel) {
      collectionsPanel.restoreSelection(selection.groups);
    }
    updateRail();
  };
  // Which view the screen shows (grid-model.js createViewFlow): a change that does not load goes back to the last one
  // that did (toolbar, selection, trail, filter), the detail pane is cleared only when the new page is confirmed.
  const viewFlow = createViewController({
    getCase: () => caseId,
    captureLive,
    restoreLive,
    getSelection: () => ({ ids: panel.selectedIds(), groups: collectionsPanel ? collectionsPanel.selectedGroups() : [] }),
    setSelection: restoreSelection,
    getStack: () => viewsStack,
    refreshDerived, // Operations, Dashboard and the clock banner follow the restored collection too
    afterRollback: (to) => {
      currentFilter = to.filter;
      const terms = searchTerms(searchText);
      grid.setTerms(terms);
      detail.setTerms(terms); // the detail pane was kept: it still shows the record of the view that is back
      if (timeline) {
        timeline.refresh();
      }
      dock.notify("view");
      if (viewsUi) {
        viewsUi.changed();
      }
      if (filterChips) {
        filterChips.refresh();
      }
    },
    onConfirm: () => detail.clear(),
  });
  const retryView = () => (viewFlow.retryState(caseId) === null ? Promise.resolve() : applyView());

  const applyView = () => {
    const { attempt, token } = viewFlow.begin(); // replaces the failed view change, if any
    const { ids, groups } = attempt.selection;
    failures.viewApplied();
    updateRail();
    // The effective view: the ancestors' filters AND the active view's own.
    const own = composeFilter({ sourceIds: ids, collectionIds: groups, text: searchText, ...formState }, userFilter, toggles);
    const filter = viewsStack ? andFilters(viewsStack.parentsFilter(), own) : own;
    currentFilter = filter;
    attempt.filter = filter;
    navRequests.invalidate();
    const range = timeSpan(sources, effectiveSourceIds(sources, ids, groups), formState.from, formState.to);
    const terms = searchTerms(searchText);
    grid.setTerms(terms);
    detail.setTerms(terms); // the detail pane stays until the new page is confirmed (a failure must not blank it)
    const caseRange = timeSpan(sources, [], null, null); // the whole case: decides the date in the time column
    const loaded = grid.setView({ caseId, filter, range, caseRange });
    loaded.then(firstViewDone, firstViewDone);
    viewFlow.track(token, loaded, () => (grid.failure ? grid.failure.kind : "unknown"), isRetryable);
    if (timeline) {
      timeline.refresh();
    }
    dock.notify("view"); // Distinct values, Group by message type, Aggregate
    if (viewsUi) {
      viewsUi.changed();
    }
    if (filterChips) {
      filterChips.refresh();
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
    sourceList: byId("source-list"),
    caseHeader: byId("case-header"),
    caseHeaderName: byId("case-header-name"),
    caseRow: byId("case-row"),
    caseAddFiles: byId("case-add-files"),
    caseMenu: byId("case-header-menu"),
    addFilesHost: byId("add-files-host"),
    emptyHost: byId("grid-panel"), // the empty state without a case (case-empty.js, loaded on demand)
  }, {
    onCase: (id) => {
      caseId = id;
      failures.caseChanged(); // nothing of the old case is repeated, and its requests are cancelled now
      applyPending = true; // refreshSources() follows and rebuilds the view
      if (collectionsPanel) {
        collectionsPanel.setCase();
      }
      byId("add-files-host").hidden = id === null;
      detail.clear(); // drop the old case's entry right away, before /sources answers
      bookmarkStore.reset();
      if (repeats) {
        repeats.invalidate();
      }
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
      if (appActions) {
        appActions.onCase(); // the export dialog forgets the old case, open or closed
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
      updateRail();
      if (collectionsPanel) {
        collectionsPanel.setSources(list);
      }
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
    onSelection: () => (removingChip ? undefined : applyView()),
    onProgress: (value) => status.progress(value),
    onWatching: (on) => {
      if (menubar) {
        menubar.setWatching(on);
      }
    },
    onDone: () => {
      bookmarkStore.invalidate(); // the records were (re)indexed: markers are looked up again
      highlightStore.invalidateMarks();
      if (repeats) {
        repeats.invalidate(); // the ids moved
      }
      loadTemplates(caseId);
      knowledge.reset(); // the ERS configuration may have been read again
      dock.notify("indexed"); // Bookmarks, Unknown message types, Effective configuration
      refreshDerived();
      return applyView();
    },
    // Open only part first (M9 9.9): the first part is indexed. The view narrows to its time window (log time, [from, to)
    // = ge from, le to - 1) so the rest arriving in the background does not show as noise, then reloads as at the end of
    // a job. The user clears the window in the filter strip when the rest is in.
    onFirstReady: (info) => {
      if (info.from !== null || info.to !== null) {
        byId("from-input").value = info.from === null ? "" : formatTs(info.from);
        byId("to-input").value = info.to === null ? "" : formatTs(info.to - 1);
        formState = { ...formState, from: info.from, to: info.to === null ? null : info.to - 1 };
      }
      return panel.handlers.onDone();
    },
    onMessage: showMessage,
  });
  new SourceFilter({ host: byId("source-filter-host"), panel });
  const syncCollectionColumn = () =>
    grid.setCollectionColumn(columnShown(collections, collectionColumnPref), !applyPending).catch(showError);
  // The first GET /collections of the opening was started by boot.js together with /sources (prefetch.js).
  const collectionsApi = {
    ...api,
    collections: (forCase, signal) => takeCollections(forCase, () => api.collections(forCase, signal)) || api.collections(forCase, signal),
  };
  collectionsPanel = new CollectionsPanel({
    host: byId("collections-host"),
    note: byId("collections-note"),
    api: collectionsApi,
    getCase: () => caseId,
    onSelection: (groups) => {
      if (groups.length > 1) {
        showMessage(S.colPanelsOne, false);
      }
      refreshDerived(); // Operations, Dashboard and the clock banner follow the chosen collection
      return removingChip ? undefined : applyView();
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
    for (const [key, id] of [["from", "from-input"], ["to", "to-input"]]) {
      setFieldError(byId(id), errors.includes(key) ? S.filterBadTime : null); // under the field that is wrong
    }
    if (errors.length > 0) {
      // The popover stays open on the field that is wrong; the previous filter stays applied.
      byId(errors.includes("from") ? "from-input" : "to-input").focus();
      return;
    }
    formState = { sevMax: state.sevMax, levelMax: state.levelMax, from: state.from, to: state.to };
    if (filterChips) {
      filterChips.closePopovers(true); // only a valid Apply closes the form
    }
    applyView();
  });
  byId("filter-reset").addEventListener("click", () => {
    resetFormWithErrors(byId("filter-form"), [byId("from-input"), byId("to-input")]);
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
    const gotoInput = byId("goto-input");
    if (ms === null) {
      // The top bar has no room under the field: aria-invalid on the field, the text in the status bar.
      gotoInput.setAttribute("aria-invalid", "true");
      showMessage(S.filterBadTime, true);
      return;
    }
    gotoInput.removeAttribute("aria-invalid");
    showMessage("", false);
    grid.jumpToTime(ms);
  });
  for (const id of ["from-input", "to-input"]) {
    byId(id).addEventListener("input", () => setFieldError(byId(id), null));
  }
  byId("goto-input").addEventListener("input", (event) => event.target.removeAttribute("aria-invalid"));

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
    // A histogram that failed (also the first one, with no chart yet) is repeated by Retry, alone.
    onFailure: (err) => failures.timelineFailure(err),
    onRecovered: () => failures.timelineRecovered(),
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
  // The detail pane's optional sections (Links, Payload, Highlights, Bookmark) load when the first record is shown,
  // not at the opening (UI-14). Each one that loads shows the record that is already on screen.
  // Each section loads on its own and a failure is forgotten, so the next record tries again (retryable).
  let detailFailureShown = false;
  // Highlight rules (detail section, ]/[ navigation); the store is above, the panel is a dock panel.
  const highlightGate = createButtonGate(byId("highlight-button"), { loadingTitle: S.detailHighlightLoading, failedTitle: S.actionUnavailable });
  const loadHighlights = retryable(() => import("./highlight-panel.js").then((module) => {
    highlightUi = module.mountHighlights({
      detailAnchor: byId("detail-actions"),
      detailTrigger: byId("highlight-button"),
      runHighlightType: () => actions.invoke("record.highlightType", ctx),
      store: highlightStore,
      getCase: () => caseId,
      getFilter: () => currentFilter,
      getSelectedRow: () => grid.selectedRow(),
      getTemplateCode: (id) => templateLabels.get(id) || "",
      reveal: (row) => grid.revealRow(row),
      showMessage,
    });
    highlightUi.showEntry(detail.entry);
  }));
  const detailLoaders = [
    retryable(() => import("./links.js").then((links) => {
      linksSection = new links.LinksSection(byId("detail-links"), {
        getCase: () => caseId,
        onReveal: revealEntry,
        onSideBySide: (id) => dock.open("sideBySide", { entryId: id }),
        onSameKey: (type, value) => dock.open("sameKey", { type, value }),
        keysHost: byId("detail-keys"),
        onError: showError,
      });
      if (detail.entry) {
        linksSection.show(detail.entry);
      }
    })),
    // Payload section of the entry detail (the payload list is a dock panel).
    retryable(() => import("./payload.js").then((module) => {
      payloadSection = new module.PayloadSection(byId("detail-payload"), {
        getCase: () => caseId,
        onReveal: revealEntry,
        onGoOperation: (eventId) => dock.open("operations", { eventId }),
        onError: showError,
      });
      if (detail.entry) {
        payloadSection.show(detail.entry);
      }
    })),
    // Highlight rules: the button stays disabled (with its reason) while the module loads or after it failed.
    () => highlightGate.track(loadHighlights()),
    // "Repeat of #N, +Δ ms" (9.12): needs the repeats controller above.
    retryable(() => loadRepeats().then((controller) => import("./repeat-detail.js").then((module) => {
      repeatDetail = module.initRepeatDetail(byId("detail-repeat"), {
        repeats: controller, api, getCase: () => caseId, onReveal: revealEntry, onError: showError,
      });
      if (detail.entry) {
        repeatDetail.show(detail.entry);
      }
    }))),
    // Bookmarks (detail section; the shortcuts use the store and the panel is a dock panel).
    retryable(() => import("./bookmarks-panel.js").then((module) => {
      detailBookmark = module.initDetailBookmark(byId("detail-bookmark"), { store: bookmarkStore, onError: showError });
      adoptHeading(byId("detail-bookmark"), "bookmark");
      if (detail.entry) {
        detailBookmark.show(detail.entry);
      }
    })),
  ];
  const loadDetailModules = () => loadSections(detailLoaders, () => {
    if (!detailFailureShown) {
      detailFailureShown = true; // said once; the sections keep retrying silently
      showMessage(S.detailSectionsUnavailable, true);
    }
  });
  // Filter strip chips (filter-chips.js, filter-chips-model.js): optional module; without it the filter form stays open.
  const chipSelection = () => ({
    sourceIds: panel.selectedIds(),
    collectionIds: collectionsPanel ? collectionsPanel.selectedGroups() : [],
  });
  // The words of the chips and of the trail of views: the same lookups (ui-contratos.md 4.1).
  const chipLookups = {
    sourceName: (id) => sourceNames.get(id) || String(id),
    collectionTag: (group) => tagMap(collections).get(group) || String(group),
    typeLabel: (id) => templateLabels.get(id) || String(id),
    formatTs,
  };
  // One popover open at a time: the Filters form, "+N more" and the "..." menu join the same group.
  const popoverGroup = import("./popover-group.js").then((m) => m.createPopoverGroup()).catch(() => null);
  Promise.all([import("./filter-chips.js"), import("./filter-chips-model.js"), popoverGroup]).then(([chipsUi, chipsModel, group]) => {
    const removal = chipsModel.createChipRemoval({
      getLive: captureLive,
      getSelection: chipSelection,
      restoreLive,
      clearSources: () => panel.clearSelection(),
      clearCollections: () => (collectionsPanel ? collectionsPanel.clearSelection() : undefined),
      setBusy: (busy) => {
        removingChip = busy;
      },
      applyView: () => applyView(),
    });
    filterChips = chipsUi.mountFilterChips({
      strip: byId("filter-strip"),
      chipsHost: byId("strip-chips"),
      stateLabel: byId("strip-state"),
      filtersButton: byId("strip-filters"),
      popover: byId("filter-form"),
      group,
      getState: () => ({
        live: captureLive(),
        selection: chipSelection(),
        parentsFilter: viewsStack ? viewsStack.parentsFilter() : null,
      }),
      lookups: chipLookups,
      onRemove: (chipId) => removal.remove(chipId),
      onOpenTree: () => byId("filter-tree-button").click(),
    });
    filterChips.refresh();
  }).catch(() => {
    byId("filter-form").hidden = false; // no chips module: the filter form stays where the user can reach it
  });
  // Chained views (breadcrumbs, Narrow this view, Show in parent/root): optional modules.
  Promise.all([import("./views-stack.js"), import("./views-stack-model.js"), popoverGroup]).then(([uiModule, model, group]) => {
    viewsStack = model.createViewsStack({ ownFilter: ownFilterOf, getLive: captureLive, lookups: chipLookups });
    viewsUi = uiModule.mountViewsStack({
      trailHost: byId("strip-trail"),
      actionsHost: byId("strip-actions"),
      group,
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

  // b / c (record.bookmark, record.compare; the keys are dispatched by shortcuts.js).
  const toggleBookmark = async () => {
    const row = grid.selectedRow();
    if (!row) {
      showMessage(S.navNoSelection, false);
      return;
    }
    try {
      await bookmarkStore.toggle(row.id);
    } catch (err) {
      showError(err);
    }
  };
  const compareSelected = () => {
    const row = grid.selectedRow();
    if (!row) {
      showMessage(S.navNoSelection, false);
      return undefined;
    }
    if (detail.entry && detail.entry.id === row.id) {
      compareCurrent();
      return undefined;
    }
    return detail.show(row.id).then(() => compareCurrent());
  };

  // retryable(): a failed import() is forgotten, so the next use of a control tries again (the rail never stays dead).
  const loadDrawers = retryable(() => import("./drawers.js").then((module) => {
    if (drawers === null) {
      drawers = module.createDrawers({});
    }
    updateRail();
    return drawers;
  }));
  ready.drawers = settled(loadDrawers().catch((err) => {
    showError(err);
    throw err;
  }));
  // The controls work even while the module is not in (or failed to load): the first use loads it, then does its job.
  for (const [id, which] of [["side-rail", "side"], ["detail-toggle", "detail"]]) {
    byId(id).addEventListener("click", () => {
      openWhenLoaded({ current: () => drawers, load: loadDrawers, which, onError: showError });
    });
  }
  // Opens the sources drawer (below 1600 px) before something inside the panel is revealed or focused.
  const withSources = (action) => afterDrawerOpen({ current: () => drawers, load: loadDrawers, which: "side", action, onError: showError });

  ready.followTail = settled(mountLate({
    wait: afterFirstView,
    load: () => import("./follow-tail.js"),
    mount: (module) => {
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
        // The watch stream gave up after 3 failures in a row: say so in the bar; Retry starts it again.
        onStreamStopped: () => failures.streamStopped("stream", () => followTail.resume()),
      });
      return followTail;
    },
    getCase: () => caseId,
    onCaseOpen: (instance) => instance.onCase(), // a case was already open when the module arrived
  }));

  // Repeated records (9.12): loaded once the first view has answered; a failure is forgotten (retryable), so the next
  // record shown tries again. The controller owns the marks, the "Hide repeated records" chip and the window.
  const loadRepeats = retryable(() => import("./repeat-model.js").then((module) => {
    if (repeats === null) {
      repeats = module.createRepeats({
        api,
        getCase: () => caseId,
        getUserFilter: () => userFilter,
        setUserFilter: (filter) => setUserFilter(filter).catch(showError),
        storage: safeLocalStorage(),
        onChange: () => {
          grid.render();
          if (repeatDetail) {
            repeatDetail.refresh();
          }
        },
      });
      grid.render(); // the rows on screen ask for their marks
    }
    return repeats;
  }));
  ready.repeats = settled(afterFirstView.then(() => loadRepeats()));

  // Local alerts (9.13): banner, stream and counter run from the opening; the panel is a dock panel.
  ready.alerts = settled(afterFirstView.then(() => import("./alerts-panel.js")).then((module) => {
    alertsUi = module.startAlerts({
      slot: status.slot("alerts"),
      onAnnounce: (text) => status.message(text, { kind: "warning" }),
      onStreamStopped: () => failures.streamStopped("alerts", () => alertsUi.resumeStream()),
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
  // "Copy as" loads the first time the Export menu is hovered or the action is used (actions.preload / load()).
  let copyExportLoading = null;
  const loadCopyExport = () => {
    if (copyExportLoading === null) {
      copyExportLoading = import("./copy-export.js").then((module) => {
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
        return copyExport;
      });
      copyExportLoading.catch(() => { copyExportLoading = null; });
    }
    return copyExportLoading;
  };

  afterFirstView.then(() => import("./clock-banner.js")).then((module) => {
    if (typeof module.initClockBanner !== "function") {
      return;
    }
    clockBanner = module.initClockBanner(status.slot("clock"), {
      api: derivedApi,
      getCase: () => caseId,
      onShowInGrid: showEventInGrid,
      onAnnounce: (text) => status.message(text, { kind: "warning" }),
    });
    clockBanner.refresh();
  }).catch(() => {
    // Module missing or failing: no banner.
  });

  // n/N next/previous error, t/T next/previous entry of the selected row's message type (navigate.* actions). Without a
  // selected record n/N start at the first/last visible record.
  const visibleRows = () => {
    const w = grid.window;
    return w && Array.isArray(w.rows) ? w.rows.slice(w.top, w.top + Math.max(1, Number(grid.visible) || 1)) : [];
  };
  const navigateRecord = async (kind, dir) => {
    const plan = navPlan(kind, dir, grid.selectedRow(), visibleRows(), currentFilter);
    if (plan.problem) {
      showMessage(S[plan.problem], false);
      return;
    }
    const { request } = plan;
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
  };

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
    repeats: () => repeats,
    loadCopyExport,
    onError: showError,
    fieldNames: () => Array.from(seenFields),
    hasCollections: () => isMulti(collections),
    sourceName: (id) => sourceNames.get(id) || String(id),
    typeLabel: (id) => templateTitles.get(id) || "",
    showKeyInGrid,
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
        resetFormWithErrors(byId("filter-form"), [byId("from-input"), byId("to-input")]);
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
    // The opener is read before the drawer opens: opening it moves the focus into the sources panel, and the dialog must
    // give the focus back to the control the user used (the menu item's trigger), not to the panel.
    focusNewCase: () => {
      const opener = document.activeElement;
      return withSources(() => panel.revealNewCase(opener));
    },
    focusAddFiles: () => {
      const opener = document.activeElement;
      return withSources(() => panel.revealAddFiles(false, opener));
    },
    drawerOpen: (id) => (drawers ? drawers.isOpen(id) : false),
    toggleDrawer: async (id) => {
      try {
        (await loadDrawers()).toggle(id);
      } catch (err) {
        showError(err);
      }
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
    recordShown: () => detail.entry !== null,
    hasType: () => detail.entry !== null && detail.entry.tpl_id > 0,
    copyRecord: () => detail.copyText(),
    loadContext: (same) => detail.loadContext(same),
    filterType: () => detail.filterType(),
    highlightReady: () => highlightUi !== null,
    highlightType: () => {
      if (highlightUi === null) {
        throw new Error(S.actionUnavailable);
      }
      return highlightUi.highlightType();
    },
    // Keyboard (UI-28).
    navigateRecord,
    navigateHighlight: (dir) => (highlightUi === null ? showMessage(S.navNoSelection, false) : highlightUi.navigate(dir)),
    toggleBookmark,
    compareSelected,
    viewsBack: () => (viewsUi === null ? undefined : viewsUi.back()),
    viewsForward: () => (viewsUi === null ? undefined : viewsUi.forward()),
    focusSearch: () => {
      const input = byId("search-input");
      if (!input.disabled) {
        input.focus();
        input.select();
      }
    },
    moveRegion: async (dir) => (await loadShortcuts()).regions.move(dir),
    // The opener is read before the module loads: the dialog gives the focus back to the control the user used.
    openShortcuts: async () => {
      const opener = document.activeElement;
      (await loadShortcuts()).openDialog(opener);
    },
  };
  // import() with a retry: a failed load is forgotten, the next use (a key, the "?" button) tries again.
  const loadShortcuts = retryable(() => import("./shortcuts.js").then((module) => {
    if (shortcuts === null) {
      shortcuts = module.createShortcuts({
        actions, ctx, getCase: () => caseId, drawers: () => drawers, storage: safeLocalStorage(),
      });
    }
    return shortcuts;
  }));
  let shortcutsFailureShown = false;
  const startShortcuts = () => loadShortcuts().catch((err) => {
    if (!shortcutsFailureShown) {
      shortcutsFailureShown = true;
      showError(err);
    }
    // Until it loads the keys do nothing (the controls still work); the next key press tries again.
    document.addEventListener("keydown", () => { startShortcuts(); }, { once: true, capture: true });
  });
  afterFirstView.then(startShortcuts);
  registerAppPanels(dock, deps);
  appActions = registerAppActions(actions, deps);
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
  panel.loadCases(null).then(() => { casesLoaded = true; }).catch(showError);
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
