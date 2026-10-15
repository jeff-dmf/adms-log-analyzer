// UI-12: the top bar on a fake DOM, wired to the real registry (app-actions.js), the legacy dock and ui-menu.js.
// Every panel opens from its menu item or toggle, the states (pressed, disabled with a reason) follow the
// registry, an unavailable module is disabled (never hidden) and the menus work from the keyboard.
import test from "node:test";
import assert from "node:assert/strict";

import { createActionRegistry } from "../../src/dmslog/web/js/actions.js";
import { registerAppActions } from "../../src/dmslog/web/js/app-actions.js";
import { createDock } from "../../src/dmslog/web/js/dock-legacy.js";
import { mountMenubar } from "../../src/dmslog/web/js/menubar.js";
import { S } from "../../src/dmslog/web/js/strings.js";
import { makeDoc, menuEls, menuItems, tick, until } from "./fixtures/fake-dom.mjs";

const PANELS = ["filterTree", "highlights", "bookmarks", "alerts", "distinct", "groupByType", "aggregate", "operations",
  "dashboard", "payloads", "unknownTypes", "config", "export"];

function setup({ caseId = 1, ready = {}, theme = "system" } = {}) {
  const doc = makeDoc();
  const root = doc.createElement("section");
  doc.body.appendChild(root);
  const state = { caseId, follow: false, theme, cases: [{ id: "1", name: "Case one" }, { id: "2", name: "Case two" }] };
  const calls = { columns: 0, copy: 0, follow: 0, theme: [], select: [], closeCase: 0, newCase: 0, addFiles: 0, mounted: [] };
  const messages = [];
  const actions = createActionRegistry({ onError: (err) => messages.push(["error", err.message]) });
  const dock = createDock({ root, storage: null, onFocusOut: () => {}, doc });
  const ctx = Object.freeze({
    caseId: () => state.caseId, filter: () => null, userFilter: () => null, selectedRow: () => null, entry: () => null,
    setUserFilter: async () => {}, reveal: async () => {}, actions, dock,
    status: { message: (text, o) => { messages.push([o && o.kind, text]); return { dismiss() {} }; } },
  });
  dock.bind(ctx);
  for (const id of PANELS) {
    dock.register({
      id,
      titleKey: "actDistinct",
      mount: async (host) => {
        calls.mounted.push(id);
        const title = doc.createElement("h3");
        host.appendChild(title);
        return { refresh() {}, reset() {} };
      },
    });
  }
  registerAppActions(actions, {
    ready: { alerts: Promise.resolve(), followTail: Promise.resolve(), ...ready },
    loadCopyExport: async () => { calls.copyLoaded = (calls.copyLoaded || 0) + 1; },
    cases: () => state.cases,
    selectCase: (id) => calls.select.push(id),
    closeCase: () => { calls.closeCase += 1; },
    focusNewCase: () => { calls.newCase += 1; },
    focusAddFiles: () => { calls.addFiles += 1; },
    openColumns: () => { calls.columns += 1; },
    copyExportOpen: () => { calls.copy += 1; },
    followTailOn: () => state.follow,
    toggleFollowTail: () => { calls.follow += 1; state.follow = !state.follow; },
    theme: () => state.theme,
    setTheme: (choice) => { calls.theme.push(choice); state.theme = choice; },
    shortcutsText: () => "help text",
  });
  const el = (tag, extra = {}) => Object.assign(doc.createElement(tag), extra);
  const bar = doc.createElement("header");
  doc.body.appendChild(bar);
  const mk = (tag, id) => { const e = el(tag, { id }); bar.appendChild(e); return e; };
  const toggles = ["toggle.filterTree", "toggle.highlights", "toggle.bookmarks", "toggle.alerts"].map((action) => {
    const b = mk("button", action.replace("toggle.", "") + "-button");
    b.dataset.action = action;
    b.title = "";
    return b;
  });
  const refs = {
    caseButton: mk("button", "case-button"),
    caseLabel: mk("span", "case-label"),
    toggles,
    menus: { analyze: mk("button", "analyze-button"), export: mk("button", "export-button"), view: mk("button", "view-button") },
    caseControls: [mk("input", "search-input"), mk("button", "search-go"), mk("input", "goto-input")],
    alertsBadge: mk("span", "alerts-count"),
  };
  refs.menus.analyze.textContent = S.actGroupAnalyze;
  refs.menus.export.textContent = S.actGroupExport;
  refs.menus.view.textContent = S.actGroupView;
  refs.caseControls[0].title = "base title";
  const bridge = mountMenubar({ actions, ctx, refs, doc, getCaseLabel: () => (state.caseId === null ? "" : "Case one") });
  dock.on("open", bridge.refresh);
  dock.on("close", bridge.refresh);
  return { doc, state, calls, messages, actions, dock, refs, bridge, toggles };
}

const open = (doc, trigger) => { trigger.focus(); trigger.dispatch("keydown", { key: "ArrowDown" }); return menuEls(doc)[0]; };
const labels = (menu) => menuItems(menu).map((n) => n.children[0].textContent);
const itemByLabel = (menu, label) => menuItems(menu).find((n) => n.children[0].textContent === label);

test("without a case only the case menu, the View menu and Keyboard shortcuts work; the rest says why", async () => {
  const { refs, toggles, state, bridge } = setup({ caseId: null });
  assert.equal(refs.caseLabel.textContent, S.caseNone);
  for (const t of toggles) {
    assert.equal(t.disabled, true, t.id);
    assert.equal(t.title, S.needCase);
    assert.equal(t.attrs["aria-pressed"], "false");
  }
  assert.equal(refs.menus.analyze.disabled, true);
  assert.equal(refs.menus.export.disabled, true);
  assert.equal(refs.menus.analyze.title, S.needCase);
  assert.equal(refs.menus.view.disabled, false); // Theme, Columns and Keyboard shortcuts need no case
  for (const c of refs.caseControls) {
    assert.equal(c.disabled, true);
    assert.equal(c.title, S.needCase); // the hint
  }
  state.caseId = 1;
  bridge.refresh();
  assert.equal(refs.caseControls[0].title, "base title"); // the fixed title comes back
  assert.equal(refs.caseControls[0].disabled, false);
  assert.equal(refs.caseLabel.textContent, "Case one");
  assert.ok(toggles.every((t) => !t.disabled && t.title === ""));
});

test("the Analyze menu lists the eight analyses in order and each one opens its panel (a second click closes it)", async () => {
  const { doc, refs, dock, calls } = setup();
  const menu = open(doc, refs.menus.analyze);
  assert.deepEqual(labels(menu), [
    S.actDistinct, S.actGroupByType, S.actAggregate, S.actOperations, S.actDashboard, S.actPayloads, S.actUnknownTypes, S.actConfig,
  ]);
  assert.equal(refs.menus.analyze.attrs["aria-expanded"], "true");
  const ids = ["distinct", "groupByType", "aggregate", "operations", "dashboard", "payloads", "unknownTypes", "config"];
  for (let i = 0; i < ids.length; i += 1) {
    const m = open(doc, refs.menus.analyze);
    assert.equal(menuItems(m)[i].attrs["aria-checked"], "false");
    menuItems(m)[i].dispatch("click");
    await until(() => dock.isOpen(ids[i]), ids[i]);
    assert.equal(menuEls(doc).length, 0); // choosing an item closes the menu
    assert.equal(refs.menus.analyze.attrs["aria-expanded"], "false");
  }
  assert.deepEqual(calls.mounted, ids);
  const again = open(doc, refs.menus.analyze);
  assert.equal(menuItems(again)[0].attrs["aria-checked"], "true"); // open panels are checked
  menuItems(again)[0].dispatch("click");
  await until(() => !dock.isOpen("distinct"), "distinct closed");
});

test("the Export menu opens the report panel and the Copy as popover", async () => {
  const { doc, refs, dock, calls } = setup();
  const menu = open(doc, refs.menus.export);
  assert.deepEqual(labels(menu), [S.actExportReport, S.actCopyAs]);
  itemByLabel(menu, S.actCopyAs).dispatch("click");
  await until(() => calls.copy === 1, "copy");
  const again = open(doc, refs.menus.export);
  itemByLabel(again, S.actExportReport).dispatch("click");
  await until(() => dock.isOpen("export"), "export");
});

test("the toggles open and close their panel and carry aria-pressed", async () => {
  const { toggles, dock } = setup();
  const byId = Object.fromEntries(toggles.map((t) => [t.dataset.action, t]));
  for (const [action, panel] of [["toggle.filterTree", "filterTree"], ["toggle.highlights", "highlights"],
    ["toggle.bookmarks", "bookmarks"], ["toggle.alerts", "alerts"]]) {
    const button = byId[action];
    assert.equal(button.attrs["aria-pressed"], "false");
    button.click();
    await until(() => dock.isOpen(panel), panel);
    assert.equal(button.attrs["aria-pressed"], "true", panel);
    button.click();
    await until(() => !dock.isOpen(panel), panel + " closed");
    assert.equal(button.attrs["aria-pressed"], "false");
  }
});

test("a panel that closes itself updates the toggle too", async () => {
  const { toggles, dock, bridge } = setup();
  toggles[1].click();
  await until(() => dock.isOpen("highlights"), "highlights");
  assert.equal(toggles[1].attrs["aria-pressed"], "true");
  dock.close("highlights");
  assert.equal(toggles[1].attrs["aria-pressed"], "false");
  void bridge;
});

test("the View menu: Columns, Follow tail (pressed), Theme with System, Light and Dark, then Keyboard shortcuts", async () => {
  const { doc, refs, calls, state, messages } = setup({ theme: "dark" });
  let menu = open(doc, refs.menus.view);
  assert.deepEqual(labels(menu), [S.actColumns, S.actFollowTail, S.actTheme, S.actShortcuts]);
  assert.equal(menu.children[3].attrs.role, "separator");
  assert.equal(itemByLabel(menu, S.actFollowTail).attrs.role, "menuitemcheckbox");
  assert.equal(itemByLabel(menu, S.actFollowTail).attrs["aria-checked"], "false");
  assert.equal(itemByLabel(menu, S.actShortcuts).children[1].textContent, "?");
  itemByLabel(menu, S.actColumns).dispatch("click");
  await until(() => calls.columns === 1, "columns");
  menu = open(doc, refs.menus.view);
  itemByLabel(menu, S.actFollowTail).dispatch("click");
  await until(() => calls.follow === 1, "follow");
  assert.equal(state.follow, true);
  menu = open(doc, refs.menus.view);
  assert.equal(itemByLabel(menu, S.actFollowTail).attrs["aria-checked"], "true");
  // Theme submenu: ArrowRight opens it, the current choice is checked
  const theme = itemByLabel(menu, S.actTheme);
  assert.equal(theme.attrs["aria-haspopup"], "menu");
  theme.dispatch("pointerenter");
  theme.dispatch("keydown", { key: "ArrowRight" });
  const sub = menuEls(doc)[1];
  assert.deepEqual(labels(sub), [S.themeSystem, S.themeLight, S.themeDark]);
  assert.deepEqual(menuItems(sub).map((n) => n.attrs["aria-checked"]), ["false", "false", "true"]);
  assert.ok(menuItems(sub).every((n) => n.attrs.role === "menuitemradio"));
  itemByLabel(sub, S.themeLight).dispatch("click");
  await until(() => calls.theme.length === 1, "theme");
  assert.deepEqual(calls.theme, ["light"]);
  menu = open(doc, refs.menus.view);
  itemByLabel(menu, S.actShortcuts).dispatch("click");
  await until(() => messages.length > 0, "message");
  assert.deepEqual(messages.at(-1), ["info", "help text"]);
});

test("the case menu: the cases (current one checked), then New case, Add files and Close case", async () => {
  const { doc, refs, calls } = setup();
  const menu = open(doc, refs.caseButton);
  assert.deepEqual(labels(menu), ["Case one", "Case two", S.actCaseNew, S.actCaseAddFiles, S.actCaseClose]);
  assert.equal(menu.children[2].attrs.role, "separator");
  assert.equal(menuItems(menu)[0].attrs.role, "menuitemradio");
  itemByLabel(menu, "Case two").dispatch("click");
  await until(() => calls.select.length === 1, "select");
  assert.deepEqual(calls.select, ["2"]);
  itemByLabel(open(doc, refs.caseButton), S.actCaseNew).dispatch("click");
  itemByLabel(open(doc, refs.caseButton), S.actCaseClose).dispatch("click");
  await until(() => calls.newCase === 1 && calls.closeCase === 1, "new and close");
  assert.deepEqual([calls.newCase, calls.closeCase], [1, 1]);
});

test("no case open: the known cases and New case stay; Add files and Close case are disabled with a reason", async () => {
  const { doc, refs } = setup({ caseId: null });
  const menu = open(doc, refs.caseButton);
  assert.deepEqual(labels(menu), ["Case one", "Case two", S.actCaseNew, S.actCaseAddFiles, S.actCaseClose]);
  assert.deepEqual(menuItems(menu).slice(0, 2).map((n) => n.attrs["aria-checked"]), ["false", "false"]);
  const add = itemByLabel(menu, S.actCaseAddFiles);
  assert.equal(add.attrs["aria-disabled"], "true");
  assert.equal(add.attrs.title, S.needCase);
});

test("a module that fails to load disables its item with a reason; nothing is ever hidden", async () => {
  const { refs, toggles, actions } = setup({ ready: { alerts: Promise.reject(new Error("alerts module missing")) } });
  toggles.find((t) => t.dataset.action === "toggle.alerts").dispatch("pointerenter");
  await tick();
  const alerts = toggles.find((t) => t.dataset.action === "toggle.alerts");
  assert.equal(alerts.disabled, true);
  assert.equal(alerts.title, S.actionUnavailable);
  assert.equal(alerts.hidden, false);
  assert.equal(actions.state("toggle.alerts", { caseId: () => 1 }).failed, true);
  for (const control of [...toggles, ...Object.values(refs.menus), refs.caseButton]) {
    assert.equal(control.hidden, false, control.id);
  }
});

test("keyboard: ArrowDown opens a menu on its first item, Escape closes it and gives the focus back", async () => {
  const { doc, refs } = setup();
  const trigger = refs.menus.analyze;
  trigger.focus();
  trigger.dispatch("keydown", { key: "ArrowDown" });
  assert.equal(doc.activeElement.children[0].textContent, S.actDistinct);
  doc.activeElement.dispatch("keydown", { key: "ArrowDown" });
  assert.equal(doc.activeElement.children[0].textContent, S.actGroupByType);
  doc.activeElement.dispatch("keydown", { key: "Escape" });
  assert.equal(menuEls(doc).length, 0);
  assert.equal(doc.activeElement, trigger);
  assert.equal(trigger.attrs["aria-expanded"], "false");
});

test("the Alerts indicator appears only with serve --watch on, with a count and an accessible name", () => {
  const { refs, bridge, toggles } = setup();
  const alerts = toggles.find((t) => t.dataset.action === "toggle.alerts");
  assert.equal(refs.alertsBadge.hidden, true);
  bridge.setAlertsCount(2); // alerts arrive, but the server is not following a folder: no indicator
  assert.equal(refs.alertsBadge.hidden, true);
  assert.equal(alerts.attrs["aria-label"], S.actAlerts);
  bridge.setAlertsCount(0);
  bridge.setWatching(true);
  bridge.setAlertsCount(1);
  assert.equal(refs.alertsBadge.hidden, false);
  assert.equal(refs.alertsBadge.textContent, "1");
  assert.equal(alerts.attrs["aria-label"], "Alerts, 1 new");
  bridge.setAlertsCount(3);
  assert.equal(alerts.attrs["aria-label"], "Alerts, 3 new");
  bridge.setAlertsCount(0);
  assert.equal(refs.alertsBadge.hidden, true);
  assert.equal(alerts.attrs["aria-label"], S.actAlerts);
});

test("UI-14: the module behind a toggle loads on hover or focus, never at the opening", async () => {
  const { toggles, actions } = setup();
  const original = toggles.map((t) => actions.state(t.dataset.action, { caseId: () => 1 }));
  assert.ok(original.every((state) => state.loaded === false), "nothing is loaded before the pointer arrives");
  const [filterTree, highlights] = toggles;
  filterTree.dispatch("pointerenter");
  highlights.dispatch("focus");
  await tick();
  assert.equal(actions.state(filterTree.dataset.action, { caseId: () => 1 }).loaded, true);
  assert.equal(actions.state(highlights.dataset.action, { caseId: () => 1 }).loaded, true);
  assert.equal(actions.state(toggles[2].dataset.action, { caseId: () => 1 }).loaded, false, "the others stay unloaded");
});

test("UI-14: Copy as loads its module on the first use of the item (hover or click), once", async () => {
  const { doc, refs, calls } = setup();
  const menu = open(doc, refs.menus.export);
  assert.equal(calls.copyLoaded || 0, 0);
  itemByLabel(menu, S.actCopyAs).dispatch("pointerenter");
  await tick();
  assert.equal(calls.copyLoaded, 1);
  itemByLabel(menu, S.actCopyAs).dispatch("click");
  await until(() => calls.copy === 1, "copy");
  assert.equal(calls.copyLoaded, 1);
});
