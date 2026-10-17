// Integration point (single owner, see docs/notas/ui-contratos.md, section 0): registers, synchronously and in one
// place, every dock panel and every action of the viewer. main.js calls registerAppPanels() and
// registerAppActions() once at the opening, before the top bar is drawn, so every menu item exists from the first
// paint. No panel module registers itself and none touches the top bar or a host it was not given.
//
// `deps` is built in main.js and is visible only here (the AppContext stays small). What it holds:
//   api, derivedApi            the plain API and the one whose queries carry the chosen collection
//   highlightStore, bookmarkStore
//   fieldNames(), hasCollections(), sourceName(id), typeLabel(tplId) ("" when unknown)
//   showKeyInGrid({type, value, filter})   "Show in grid" of the same-key panel: a chained view with the key filter
//   showEntry(id), reveal(row), showEventInGrid({from, to, entryId}), exportContext()
//   filterTreeOptions()        { onChange, getExtraFilter, onSavedApplied } of the filter tree
//   onFilterTree(tree), onHighlightPanel(panel), onBookmarksPanel(panel)   main keeps these to talk to them
//   alerts()                   the object startAlerts() returned, or null
//   ready                      { alerts, followTail }: promises of the modules loaded at the opening
//   loadCopyExport()           loads (once) and mounts the "Copy as" popover; resolves when it can open
//   cases()                    [{id, name}] known cases;  selectCase(id), closeCase(), focusNewCase(), focusAddFiles()
//   drawerOpen(id), toggleDrawer(id)   the sources rail and drawers of UI-17 ("side" | "detail"); loaded on demand, retried on failure
//   openColumns(), copyExportOpen(), followTailOn(), toggleFollowTail(), setTheme(choice), theme()
//   recordShown(), hasType(), copyRecord(), loadContext(same), filterType(), highlightType(), highlightReady()   the record actions
//   navigateRecord(kind, dir), navigateHighlight(dir), toggleBookmark(), compareSelected(), viewsBack(), viewsForward(),
//   focusSearch(), moveRegion(dir), openShortcuts()   the keyboard actions (UI-28), see "Keys" below
import { S } from "./strings.js";
import { THEME_CHOICES } from "./theme.js";

// While a panel is being reset its getCase() answers null: the panels without a reset() of their own
// (operations, dashboard, unknown types, configuration) clear everything of the old case synchronously and ask the
// server for nothing when refreshed without a case. UI-15 gives them a real reset() and this goes away.
function withoutCase(getCase) {
  let resetting = false;
  return {
    getCase: () => (resetting ? null : getCase()),
    clear(refresh) {
      resetting = true;
      try {
        refresh();
      } finally {
        resetting = false;
      }
    },
  };
}

// The tree and its template leaf (tpl-leaf.js registers the leaf type and editor) load together, on demand.
const loadFilterTree = () => Promise.all([import("./filter-tree.js"), import("./tpl-leaf.js")]);

export function registerAppPanels(dock, deps) {
  const { api, derivedApi } = deps;

  // The tabs of the dock appear in the order the panels are opened, not in the order they are registered here.
  dock.register({
    id: "filterTree",
    titleKey: "actFilterTree",
    mount: async (host, ctx) => {
      const [module] = await loadFilterTree();
      deps.onFilterTree(module.initFilterTree(host, {
        api,
        caseId: ctx.caseId(),
        initial: ctx.userFilter(),
        ...deps.filterTreeOptions(),
      }));
      return {}; // main talks to the tree directly (setFilter, setCase) and invalidates it on a case change
    },
  });

  dock.register({
    id: "unknownTypes",
    titleKey: "actUnknownTypes",
    listens: ["case", "indexed"],
    mount: async (host, ctx) => {
      const module = await import("./unknown-panel.js");
      const guard = withoutCase(ctx.caseId);
      const panel = module.initUnknownPanel(host, {
        api, getCase: guard.getCase, getFamily: () => null, onOpenEntry: (id) => deps.showEntry(id),
        onRequestClose: () => ctx.dock.close("unknownTypes"),
      });
      return { refresh: () => panel.refresh(), reset: () => guard.clear(() => panel.refresh()) };
    },
  });

  dock.register({
    id: "config",
    titleKey: "actConfig",
    listens: ["case", "indexed"],
    mount: async (host, ctx) => {
      const module = await import("./config-panel.js");
      const guard = withoutCase(ctx.caseId);
      const panel = module.initConfigPanel(host, {
        api, getCase: guard.getCase, onOpenEntry: (id) => deps.showEntry(id),
        onRequestClose: () => ctx.dock.close("config"),
      });
      return { refresh: () => panel.refresh(), reset: () => guard.clear(() => panel.refresh()) };
    },
  });

  dock.register({
    id: "operations",
    titleKey: "actOperations",
    listens: ["derived"],
    mount: async (host, ctx) => {
      const module = await import("./events-panel.js");
      const guard = withoutCase(ctx.caseId);
      const panel = module.initEventsPanel(host, {
        api: derivedApi, getCase: guard.getCase, onShowInGrid: deps.showEventInGrid,
        onRequestClose: () => ctx.dock.close("operations"),
      });
      let stale = true; // a closed panel loads its list again when it is shown
      return {
        refresh() {
          stale = false;
          return panel.refresh();
        },
        // "Go to operation" (payload section): show the panel and open that event in its detail pane.
        async show({ eventId }) {
          if (stale) {
            stale = false;
            await panel.refresh();
          }
          return panel.open(eventId);
        },
        closed() {
          stale = true;
        },
        reset: () => guard.clear(() => panel.refresh()),
      };
    },
  });

  dock.register({
    id: "payloads",
    titleKey: "actPayloads",
    closeOnCase: true,
    mount: async (host, ctx) => {
      const module = await import("./payload-list.js");
      const panel = module.initPayloadList(host, {
        api, getCase: ctx.caseId, onOpen: ctx.reveal, onRequestClose: () => ctx.dock.close("payloads"),
      });
      return { refresh: () => panel.refresh(), closed: () => panel.close(), reset: () => panel.close() };
    },
  });

  dock.register({
    id: "dashboard",
    titleKey: "actDashboard",
    listens: ["derived"],
    mount: async (host, ctx) => {
      const module = await import("./dashboard.js");
      const guard = withoutCase(ctx.caseId);
      const panel = module.initDashboard(host, {
        api: derivedApi, getCase: guard.getCase, onShowInGrid: deps.showEventInGrid,
        onRequestClose: () => ctx.dock.close("dashboard"),
      });
      return { refresh: () => panel.refresh(), reset: () => guard.clear(() => panel.refresh()) };
    },
  });

  dock.register({
    id: "sideBySide",
    titleKey: "actSideBySide",
    closeOnCase: true,
    mount: async (host, ctx) => {
      const module = await import("./side-by-side.js");
      const panel = module.initSideBySide(host, { getCase: ctx.caseId, onRequestClose: () => ctx.dock.close("sideBySide") });
      return { show: ({ entryId }) => panel.open(entryId), closed: () => panel.close(), reset: () => panel.reset() };
    },
  });

  dock.register({
    id: "sameKey",
    titleKey: "actSameKey",
    closeOnCase: true,
    mount: async (host, ctx) => {
      const module = await import("./key-panel.js");
      const panel = module.initKeyPanel(host, {
        getCase: ctx.caseId, onSelect: ctx.reveal, onRequestClose: () => ctx.dock.close("sameKey"),
        onShowInGrid: deps.showKeyInGrid, getSourceName: deps.sourceName, getTypeLabel: deps.typeLabel,
      });
      return { show: ({ type, value }) => panel.open(type, value), closed: () => panel.close(), reset: () => panel.reset() };
    },
  });

  dock.register({
    id: "bookmarks",
    titleKey: "actBookmarks",
    listens: ["case", "indexed"],
    mount: async (host, ctx) => {
      const module = await import("./bookmarks-panel.js");
      const panel = module.initBookmarksPanel(host, {
        getCase: ctx.caseId, store: deps.bookmarkStore, onReveal: ctx.reveal, onError: deps.onError,
        onRequestClose: () => ctx.dock.close("bookmarks"),
      });
      deps.onBookmarksPanel(panel);
      return { refresh: () => panel.open(), update: () => panel.refresh(), closed: () => panel.close(), reset: () => panel.reset() };
    },
  });

  dock.register({
    id: "compare",
    titleKey: "actCompare",
    closeOnCase: true,
    mount: async (host, ctx) => {
      const module = await import("./compare.js");
      const panel = module.initCompare(host, {
        getCase: ctx.caseId, onRequestClose: () => ctx.dock.close("compare"),
      });
      return { show: ({ a, b }) => panel.open(a, b), closed: () => panel.close(), reset: () => panel.reset() };
    },
  });

  dock.register({
    id: "alerts",
    titleKey: "actAlerts",
    mount: async (host, ctx) => {
      const module = await import("./alerts-panel.js");
      const alerts = deps.alerts();
      if (alerts === null) {
        throw new Error(S.actionUnavailable);
      }
      const panel = module.mountAlertsPanel(host, {
        alerts, getCase: ctx.caseId, getFilter: () => ctx.userFilter(), onError: deps.onError,
        onRequestClose: () => ctx.dock.close("alerts"),
      });
      return { refresh: () => panel.open(), closed: () => panel.close() };
    },
  });

  dock.register({
    id: "highlights",
    titleKey: "actHighlights",
    mount: async (host, ctx) => {
      const module = await import("./highlight-panel.js");
      const panel = module.mountHighlightPanel(host, {
        store: deps.highlightStore, getCase: ctx.caseId, getFilter: () => ctx.filter(),
        onRequestClose: () => ctx.dock.close("highlights"),
      });
      deps.onHighlightPanel(panel);
      return { refresh: () => panel.refresh(), reset: () => panel.reset() };
    },
  });

  dock.register({
    id: "distinct",
    titleKey: "actDistinct",
    listens: ["view"],
    mount: async (host, ctx) => {
      const [module, model] = await Promise.all([import("./distinct-panel.js"), import("./distinct-model.js")]);
      const panel = module.mountDistinctPanel(host, {
        api,
        getCase: ctx.caseId,
        getFilter: () => ctx.filter(),
        getFieldNames: deps.fieldNames,
        hasCollections: deps.hasCollections,
        onDrill: (leaf, exclude) => ctx.setUserFilter(model.drillFilter(ctx.userFilter(), leaf, exclude)),
        onRequestClose: () => ctx.dock.close("distinct"),
      });
      return { refresh: () => panel.refresh(), closed: () => panel.reset(), reset: () => panel.reset() };
    },
  });

  dock.register({
    id: "groupByType",
    titleKey: "actGroupByType",
    listens: ["view"],
    mount: async (host, ctx) => {
      const module = await import("./distinct-panel.js");
      const panel = module.mountGroupPanel(host, {
        api,
        getCase: ctx.caseId,
        getFilter: () => ctx.filter(),
        getSourceName: deps.sourceName,
        onReveal: ctx.reveal,
        onRequestClose: () => ctx.dock.close("groupByType"),
      });
      return { refresh: () => panel.refresh(), closed: () => panel.reset(), reset: () => panel.reset() };
    },
  });

  dock.register({
    id: "aggregate",
    titleKey: "actAggregate",
    listens: ["view"],
    mount: async (host, ctx) => {
      const [module, model] = await Promise.all([import("./aggregate-panel.js"), import("./aggregate-model.js")]);
      return module.mountAggregatePanel(host, {
        api,
        getCase: ctx.caseId,
        getFilter: () => ctx.filter(),
        getFieldNames: deps.fieldNames,
        onDrill: (drill) => ctx.setUserFilter(model.drillFilter(ctx.userFilter(), drill)),
        onRequestClose: () => ctx.dock.close("aggregate"),
      });
    },
  });

  dock.register({
    id: "extract",
    titleKey: "actExtract",
    listens: ["view"],
    mount: async (host, ctx) => {
      const [module, model] = await Promise.all([import("./extract-panel.js"), import("./extract-model.js")]);
      const panel = module.mountExtractPanel(host, {
        api,
        getCase: ctx.caseId,
        getFilter: () => ctx.filter(),
        onDrill: (leaf) => ctx.setUserFilter(model.drillFilter(ctx.userFilter(), leaf)),
        onRequestClose: () => ctx.dock.close("extract"),
      });
      return { refresh: () => panel.refresh(), closed: () => panel.closed(), reset: () => panel.reset() };
    },
  });
}

// Until the tabs of UI-15 a panel has no tab to close, so the menu item and the toggle button open it when it is
// closed and close it when it is open (pressed shows which).
const togglePanel = (id) => (ctx) => (ctx.dock.isOpen(id) ? ctx.dock.close(id) : ctx.dock.open(id).then(() => undefined));
const panelOpen = (id) => (ctx) => ctx.dock.isOpen(id);

export function registerAppActions(actions, deps) {
  const always = () => true;

  // ---- case menu
  actions.register({
    id: "case.open",
    labelKey: "actCaseOpen",
    group: "case",
    enabled: always,
    emptyKey: "caseNone",
    children: (ctx) => deps.cases().map((c) => ({ label: c.name, args: c.id, checked: String(c.id) === String(ctx.caseId()) })),
    run: (ctx, id) => deps.selectCase(id),
  });
  actions.register({ id: "case.new", labelKey: "actCaseNew", group: "case", enabled: always, run: () => deps.focusNewCase() });
  actions.register({ id: "case.addFiles", labelKey: "actCaseAddFiles", group: "case", run: () => deps.focusAddFiles() });
  actions.register({ id: "case.close", labelKey: "actCaseClose", group: "case", run: () => deps.closeCase() });

  // ---- toggles of the bar
  const toggle = (id, labelKey, panelId, extra = {}) => actions.register({
    id, labelKey, group: "toggle", pressed: panelOpen(panelId), run: togglePanel(panelId), ...extra,
  });
  toggle("toggle.filterTree", "actFilterTree", "filterTree", { load: loadFilterTree });
  toggle("toggle.highlights", "actHighlights", "highlights", { load: () => import("./highlight-panel.js") });
  toggle("toggle.bookmarks", "actBookmarks", "bookmarks", { shortcut: "B", load: () => import("./bookmarks-panel.js") });
  toggle("toggle.alerts", "actAlerts", "alerts", { load: () => deps.ready.alerts });

  // ---- Analyze
  const analyze = (id, labelKey, panelId, specifier) => actions.register({
    id, labelKey, group: "analyze", pressed: panelOpen(panelId), run: togglePanel(panelId), load: () => import(specifier),
  });
  analyze("analyze.distinct", "actDistinct", "distinct", "./distinct-panel.js");
  analyze("analyze.groupByType", "actGroupByType", "groupByType", "./distinct-panel.js");
  analyze("analyze.aggregate", "actAggregate", "aggregate", "./aggregate-panel.js");
  analyze("analyze.extract", "actExtract", "extract", "./extract-panel.js");
  analyze("analyze.operations", "actOperations", "operations", "./events-panel.js");
  analyze("analyze.dashboard", "actDashboard", "dashboard", "./dashboard.js");
  analyze("analyze.payloads", "actPayloads", "payloads", "./payload-list.js");
  analyze("analyze.unknownTypes", "actUnknownTypes", "unknownTypes", "./unknown-panel.js");
  analyze("analyze.config", "actConfig", "config", "./config-panel.js");

  // ---- Export
  // "Export report..." is a modal dialog (UI-23), not a dock tab: export-dialogs.js loads on the first use. main.js
  // calls the returned onCase() on every case change, so a closed dialog forgets the old case's report too.
  let exportDialogs = null;
  actions.register({
    id: "export.report",
    labelKey: "actExportReport",
    group: "export",
    load: () => import("./export-dialogs.js"),
    run: async (ctx) => {
      const module = await import("./export-dialogs.js");
      if (exportDialogs === null) {
        exportDialogs = module.mountExportDialogs(document.body, { getContext: () => deps.exportContext() });
      }
      exportDialogs.open(document.activeElement);
    },
  });
  actions.register({
    id: "export.copyAs",
    labelKey: "actCopyAs",
    group: "export",
    load: () => deps.loadCopyExport(),
    run: () => deps.copyExportOpen(),
  });

  // ---- View
  actions.register({ id: "view.columns", labelKey: "actColumns", group: "view", enabled: always, run: () => deps.openColumns() });
  // The sources panel and the record detail (UI-17): in a drawer they open and close; in the flow they collapse.
  actions.register({ id: "view.sources", labelKey: "actSources", group: "view", enabled: always, pressed: () => deps.drawerOpen("side"), run: () => deps.toggleDrawer("side") });
  actions.register({ id: "view.detail", labelKey: "actDetail", group: "view", enabled: always, pressed: () => deps.drawerOpen("detail"), run: () => deps.toggleDrawer("detail") });
  actions.register({
    id: "view.followTail",
    labelKey: "actFollowTail",
    group: "view",
    pressed: () => deps.followTailOn(),
    load: () => deps.ready.followTail,
    run: () => deps.toggleFollowTail(),
  });
  actions.register({
    id: "view.theme",
    labelKey: "actTheme",
    group: "view",
    enabled: always,
    children: () => THEME_CHOICES.map((choice) => ({
      label: S[{ system: "themeSystem", light: "themeLight", dark: "themeDark" }[choice]],
      args: choice,
      checked: deps.theme() === choice,
    })),
    run: (ctx, choice) => deps.setTheme(choice),
  });

  // ---- Record (UI-20): the actions of the detail pane. The buttons of the pane run them through the registry.
  //   deps.recordShown(), deps.hasType()     is there a record in the detail / does it have a message type
  //   deps.copyRecord(), deps.loadContext(same), deps.filterType(), deps.highlightType(), deps.highlightReady()
  const recordShown = () => deps.recordShown();
  const recordAction = (def) => actions.register({ group: "record", enabled: recordShown, disabledKey: "cmpNeedRecord", ...def });
  recordAction({ id: "record.copy", labelKey: "actCopyRecord", run: () => deps.copyRecord() });
  recordAction({
    id: "record.context",
    labelKey: "actContext",
    children: () => [["source", "contextSameSource"], ["tid", "contextSameTid"], ["merge", "contextSameMerge"]]
      .map(([value, key]) => ({ label: S[key], args: value })),
    run: (ctx, same) => deps.loadContext(same),
  });
  recordAction({
    id: "record.filterType",
    labelKey: "actOnlyThisType",
    enabled: () => recordShown() && deps.hasType(),
    run: () => deps.filterType(),
  });
  recordAction({
    id: "record.highlightType",
    labelKey: "actHighlightType",
    enabled: () => recordShown() && deps.hasType() && deps.highlightReady(),
    run: () => deps.highlightType(),
  });

  // ---- Keys (UI-28). shortcuts.js is the only dispatcher: it matches the KeySpec below, checks enabled() and calls run().
  //   deps.navigateRecord(kind, dir)  "error" | "template", "fwd" | "back"      deps.navigateHighlight(dir)
  //   deps.toggleBookmark(), deps.compareSelected(), deps.viewsBack(), deps.viewsForward(), deps.focusSearch()
  //   deps.moveRegion(dir)            F6 (1) / Shift+F6 (-1): loads shortcuts.js if it is not in
  //   deps.openShortcuts()            the shortcuts dialog; the opener is read before the module loads
  const key = (def) => actions.register({ group: "navigate", ...def });
  key({ id: "navigate.nextError", labelKey: "actNextError", shortcut: "n", run: () => deps.navigateRecord("error", "fwd") });
  key({ id: "navigate.prevError", labelKey: "actPrevError", shortcut: "N", run: () => deps.navigateRecord("error", "back") });
  key({ id: "navigate.nextType", labelKey: "actNextType", shortcut: "t", run: () => deps.navigateRecord("template", "fwd") });
  key({ id: "navigate.prevType", labelKey: "actPrevType", shortcut: "T", run: () => deps.navigateRecord("template", "back") });
  key({ id: "navigate.nextHighlight", labelKey: "actNextHighlight", shortcut: "]", run: () => deps.navigateHighlight("fwd") });
  key({ id: "navigate.prevHighlight", labelKey: "actPrevHighlight", shortcut: "[", run: () => deps.navigateHighlight("back") });
  key({ id: "navigate.focusSearch", labelKey: "actFocusSearch", shortcut: "/", run: () => deps.focusSearch() });
  // F6 also works without a case (the bar and the empty state are regions).
  key({ id: "navigate.nextRegion", labelKey: "actNextRegion", shortcut: "F6", enabled: always, run: () => deps.moveRegion(1) });
  key({ id: "navigate.prevRegion", labelKey: "actPrevRegion", shortcut: "Shift+F6", enabled: always, run: () => deps.moveRegion(-1) });
  actions.register({ id: "record.bookmark", labelKey: "actBookmark", group: "record", shortcut: "b", run: () => deps.toggleBookmark() });
  actions.register({ id: "record.compare", labelKey: "actCompare", group: "record", shortcut: "c", run: () => deps.compareSelected() });
  actions.register({ id: "views.back", labelKey: "actViewBack", group: "views", shortcut: "Alt+ArrowLeft", run: () => deps.viewsBack() });
  actions.register({ id: "views.forward", labelKey: "actViewForward", group: "views", shortcut: "Alt+ArrowRight", run: () => deps.viewsForward() });

  // ---- Help
  actions.register({
    id: "help.shortcuts",
    labelKey: "actShortcuts",
    group: "help",
    shortcut: "?",
    enabled: always,
    run: () => deps.openShortcuts(),
  });

  return {
    // A case change (also back to a case seen before): the export dialog forgets its report, open or not.
    onCase() {
      if (exportDialogs !== null) {
        exportDialogs.reset();
      }
    },
  };
}
