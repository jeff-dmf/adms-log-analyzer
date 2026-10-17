// UI-28: the keyboard layer on a fake DOM, wired to the real registry, the real app-actions registrations, the real
// drawers (UI-17) and the real menu: one dispatcher, F6 regions (visible ones only, in Tab order), the skip link, the
// single-key preference and the shortcuts dialog. The fake page applies what the CSS does (css/responsive.css and
// css/case-dialogs.css), so "displayed" is the effective one, and refuses focus on a node that is not displayed.
import test from "node:test";
import assert from "node:assert/strict";

import { createActionRegistry } from "../../src/dmslog/web/js/actions.js";
import { registerAppActions } from "../../src/dmslog/web/js/app-actions.js";
import { createDrawers } from "../../src/dmslog/web/js/drawers.js";
import { createShortcuts, createRegions, firstFocusable, isVisible } from "../../src/dmslog/web/js/shortcuts.js";
import { REGION_ORDER } from "../../src/dmslog/web/js/shortcuts-model.js";
import { createMenu } from "../../src/dmslog/web/js/ui-menu.js";
import { S } from "../../src/dmslog/web/js/strings.js";
import { makeDoc, tick } from "./fixtures/fake-dom.mjs";

const has = (node, name) => node.classList.contains(name);

function page({ width = 1700, caseId = "C1", storage = null, withDrawers = true, dockOpen = false } = {}) {
  const doc = makeDoc();
  doc.getElementById = (id) => doc.body.all().find((n) => n.id === id) ?? null;
  doc.querySelectorAll = (sel) => doc.body.all().filter((n) => n.matches(sel));
  const make = (tag, id, parent, extra = {}) => {
    const node = doc.createElement(tag);
    if (id) node.id = id;
    Object.assign(node, extra);
    (parent || doc.body).appendChild(node);
    return node;
  };
  const skip = make("a", "skip-link", null, { className: "skip-link" });
  skip.setAttribute("href", "#grid-viewport");
  const app = make("div", "app");
  const toolbar = make("header", "toolbar", app);
  const caseButton = make("button", "case-button", toolbar);
  const search = make("input", "search-input", toolbar);
  const viewButton = make("button", "view-button", toolbar);
  const panels = make("main", "panels", app);
  const rail = make("button", "side-rail", panels);
  const sources = make("aside", "sources-panel", panels);
  const sideCollapse = make("button", "side-collapse", sources);
  const pathInput = make("input", "path-input", sources);
  const gridPanel = make("section", "grid-panel", panels);
  gridPanel.setAttribute("tabindex", "-1");
  make("h2", "grid-title", gridPanel);
  const timeline = make("div", "timeline", gridPanel);
  const timelineButton = make("button", "timeline-expand", timeline);
  const strip = make("div", "filter-strip", gridPanel);
  const chipA = make("button", "chip-a", strip);
  const chipB = make("button", "chip-b", strip);
  const detailToggle = make("button", "detail-toggle", strip);
  const viewport = make("div", "grid-viewport", gridPanel);
  viewport.setAttribute("tabindex", "0");
  const followNew = make("button", "follow-new", gridPanel);
  followNew.hidden = true;
  const empty = make("div", null, gridPanel, { className: "case-empty" });
  const newCase = make("button", "empty-new", empty);
  const dock = make("section", "dock", gridPanel);
  dock.hidden = !dockOpen;
  const tablist = make("div", null, dock);
  tablist.setAttribute("role", "tablist");
  const tabA = make("button", "tab-a", tablist);
  tabA.setAttribute("role", "tab");
  tabA.setAttribute("aria-selected", "false");
  const tabB = make("button", "tab-b", tablist);
  tabB.setAttribute("role", "tab");
  tabB.setAttribute("aria-selected", "true");
  const detail = make("aside", "detail-panel", panels);
  const detailTitle = make("h2", "detail-title", detail);
  const detailCollapse = make("button", "detail-collapse", detail);
  const footer = make("footer", "status-bar");
  const helpButton = make("button", "sb-help", footer);

  const win = {
    innerWidth: width,
    addEventListener() {},
    removeEventListener() {},
    matchMedia: (query) => {
      const limit = Number(/width < (\d+)px/.exec(query)[1]);
      return { get matches() { return win.innerWidth < limit; } };
    },
  };
  // What the CSS does: the drawers and the empty state without a case.
  const noCase = () => has(doc.body, "no-case");
  const rules = {
    "sources-panel": () => !noCase() && ((win.innerWidth >= 1600 && !has(panels, "is-side-collapsed")) || has(panels, "is-side-open")),
    "detail-panel": () => !noCase() && ((win.innerWidth >= 1280 && !has(panels, "is-detail-collapsed")) || has(panels, "is-detail-open")),
    "grid-viewport": () => !state.maximized, // dock.css hides the grid while the dock is maximized
    "side-rail": () => !noCase() && (win.innerWidth < 1600 || has(panels, "is-side-collapsed")),
    "detail-toggle": () => !noCase() && (win.innerWidth < 1280 || has(panels, "is-detail-collapsed")),
  };
  const effective = (node) => {
    for (let n = node; n; n = n.parent) {
      if (n.hidden || (rules[n.id] && !rules[n.id]())) return false;
      if (noCase() && n.parent && n.parent.id === "grid-panel" && !has(n, "case-empty") && n.tagName !== "H2") return false;
    }
    return node.isConnected;
  };
  const order = (node) => doc.body.all().indexOf(node);
  for (const node of doc.body.all()) {
    node.checkVisibility = () => effective(node);
    const focus = node.focus.bind(node);
    node.focus = () => { if (effective(node)) focus(); };
    node.compareDocumentPosition = (other) => (order(other) > order(node) ? 4 : 2);
  }
  if (!caseId) doc.body.classList.add("no-case");

  const calls = [];
  const state = { caseId, selected: null, messages: [], errors: [], dockOpen: new Set(), maximized: false, maximizedOnly: false };
  let shortcuts = null;
  const actions = createActionRegistry({
    onError: (err) => state.errors.push(err.message),
    singleKeysOn: () => (shortcuts === null ? true : shortcuts.singleKeys()),
  });
  const record = (name) => (...args) => { calls.push([name, ...args]); };
  const drawers = withDrawers ? createDrawers({ doc, win, storage: null }) : null;
  const ctx = Object.freeze({
    caseId: () => state.caseId, filter: () => null, userFilter: () => null, selectedRow: () => state.selected, entry: () => null,
    setUserFilter: async () => {}, reveal: async () => {}, actions,
    dock: { maximize: (on) => { calls.push(["dock.maximize", on]); if (!state.maximizedOnly) state.maximized = Boolean(on); }, isOpen: (id) => state.dockOpen.has(id), open: async (id) => { state.dockOpen.add(id); calls.push(["dock.open", id]); }, close: (id) => state.dockOpen.delete(id) },
    status: { message: (text) => state.messages.push(text) },
  });
  registerAppActions(actions, {
    ready: {},
    navigateRecord: record("navigateRecord"),
    navigateHighlight: record("navigateHighlight"),
    toggleBookmark: record("toggleBookmark"),
    compareSelected: record("compareSelected"),
    viewsBack: record("viewsBack"),
    viewsForward: record("viewsForward"),
    focusSearch: () => { if (!search.disabled) { search.focus(); calls.push(["focusSearch"]); } },
    moveRegion: async (dir) => shortcuts.regions.move(dir),
    openShortcuts: async () => { const opener = doc.activeElement; shortcuts.openDialog(opener); },
    drawerOpen: () => false,
    toggleDrawer: () => {},
  });
  shortcuts = createShortcuts({ actions, ctx, getCase: () => state.caseId, drawers: () => drawers, storage, doc });

  // A key press as the browser delivers it: the target and its ancestors first, then the document.
  const press = async (key, { target = doc.activeElement, ctrl = false, alt = false, shift = false, ...init } = {}) => {
    const event = target.dispatch("keydown", { key, ctrlKey: ctrl, altKey: alt, shiftKey: shift, ...init });
    for (const fn of (doc.listeners.keydown ?? []).slice()) fn(event);
    await tick();
    return event;
  };
  const focusedId = () => doc.activeElement.id;
  const page_ = {
    doc, win, calls, state, actions, ctx, shortcuts, drawers, press, focusedId, effective,
    nodes: { skip, toolbar, caseButton, search, viewButton, panels, rail, sources, sideCollapse, pathInput, gridPanel, timeline, timelineButton, strip, chipA, chipB, detailToggle, viewport, followNew, empty, newCase, dock, tabA, tabB, detail, detailTitle, detailCollapse, footer, helpButton },
    make,
    setCase(id) {
      state.caseId = id;
      doc.body.classList.toggle("no-case", id === null);
    },
    resize(w) {
      win.innerWidth = w;
      if (!effective(doc.activeElement)) doc.activeElement = doc.body;
      drawers && drawers.paint();
    },
  };
  return page_;
}

// ---- the dispatcher ---------------------------------------------------------------------------------------------------

test("every shortcut of the registry runs its action through the one dispatcher", async () => {
  const p = page();
  p.nodes.viewport.focus();
  const expected = [
    ["n", "navigateRecord", "error", "fwd"], ["N", "navigateRecord", "error", "back"],
    ["t", "navigateRecord", "template", "fwd"], ["T", "navigateRecord", "template", "back"],
    ["]", "navigateHighlight", "fwd"], ["[", "navigateHighlight", "back"],
    ["b", "toggleBookmark"], ["c", "compareSelected"],
  ];
  for (const [key, ...call] of expected) {
    p.calls.length = 0;
    const event = await p.press(key);
    assert.deepEqual(p.calls, [call], key);
    assert.equal(event.prevented, true, key + " is taken from the browser");
  }
  p.calls.length = 0;
  const back = await p.press("ArrowLeft", { alt: true });
  assert.deepEqual(p.calls, [["viewsBack"]]);
  assert.equal(back.prevented, true, "Alt+Left would otherwise leave the app");
  await p.press("ArrowRight", { alt: true });
  assert.deepEqual(p.calls.at(-1), ["viewsForward"]);
  // B opens the Bookmarks panel (toggle.bookmarks, registered by the UI-12 code, run through the same dispatcher).
  // Its loader is a real import(): wait for that promise, never for the clock (a slow disk made this test flaky).
  await p.actions.preload("toggle.bookmarks");
  await p.press("B", { shift: true });
  assert.deepEqual(p.calls.at(-1), ["dock.open", "bookmarks"]);
  // modified keys are not the one-letter shortcuts
  p.calls.length = 0;
  await p.press("n", { ctrl: true });
  await p.press("n", { alt: true });
  assert.deepEqual(p.calls, []);
});

test("keys that are not shortcuts (names that look like object properties included) do nothing", async () => {
  const p = page();
  p.nodes.viewport.focus();
  for (const key of ["x", "constructor", "__proto__", "toString", "Enter", "ArrowDown", "Escape", "Shift"]) {
    assert.equal((await p.press(key)).prevented, false, key);
  }
  assert.deepEqual(p.calls, []);
});

test("/ focuses the search and the key is not typed into it", async () => {
  const p = page();
  p.nodes.viewport.focus();
  const event = await p.press("/");
  assert.equal(p.focusedId(), "search-input");
  assert.equal(event.prevented, true, "the slash would otherwise be typed");
  // typing a slash in the field is just a character
  p.calls.length = 0;
  const typed = await p.press("/");
  assert.equal(typed.prevented, false);
  assert.deepEqual(p.calls, []);
});

test("keys do nothing while the action is disabled (no case) and are left to the browser", async () => {
  const p = page({ caseId: null });
  p.nodes.viewport.focus();
  for (const key of ["n", "b", "]", "/"]) {
    const event = await p.press(key);
    assert.equal(event.prevented, false, key);
  }
  const alt = await p.press("ArrowLeft", { alt: true });
  assert.equal(alt.prevented, false, "history back stays with the browser");
  assert.deepEqual(p.calls, []);
});

test("a key typed in a text field, a select or an editable node is not a shortcut; F6 still works there", async () => {
  const p = page();
  const { search } = p.nodes;
  search.focus();
  for (const key of ["n", "?", "t", "]"]) {
    const event = await p.press(key);
    assert.equal(event.prevented, false, key);
  }
  assert.equal(p.calls.length, 0);
  const select = p.make("select", "sev", p.nodes.toolbar);
  select.focus();
  await p.press("n");
  const editable = p.make("div", "editable", p.nodes.toolbar, { isContentEditable: true });
  editable.focus();
  await p.press("n");
  assert.equal(p.calls.length, 0);
  p.nodes.search.focus();
  await p.press("F6");
  assert.equal(p.focusedId(), "side-collapse", "F6 left the field");
});

test("keys inside an open menu belong to the menu; F6 closes the menu and then moves on", async () => {
  const p = page();
  const { viewButton } = p.nodes;
  const menu = createMenu({
    trigger: viewButton,
    doc: p.doc,
    getItems: () => [{ id: "a", label: "Alpha", role: "menuitem" }, { id: "b", label: "Beta", role: "menuitem" }],
  });
  viewButton.focus();
  await p.press("ArrowDown");
  assert.equal(menu.isOpen(), true);
  const item = p.doc.activeElement;
  assert.equal(item.attrs.role, "menuitem");
  p.calls.length = 0;
  const letter = await p.press("n", { target: item });
  assert.equal(letter.prevented, false);
  assert.deepEqual(p.calls, []);
  assert.equal(menu.isOpen(), true, "a letter does not close the menu");
  await p.press("F6", { target: item });
  assert.equal(menu.isOpen(), false, "the menu is closed");
  assert.equal(p.focusedId(), "side-collapse", "from the bar to the sources panel");
});

test("a popover and the trigger that opened it close on F6 too", async () => {
  const p = page();
  const form = p.make("form", "filter-form", p.nodes.strip);
  const field = p.make("input", "from-input", form);
  const filters = p.make("button", "strip-filters", p.nodes.strip);
  filters.setAttribute("aria-controls", "filter-form");
  filters.setAttribute("aria-expanded", "true");
  let closed = 0;
  form.addEventListener("keydown", (event) => {
    if (event.key === "Escape") { closed += 1; form.hidden = true; filters.setAttribute("aria-expanded", "false"); filters.focus(); }
  });
  field.focus();
  await p.press("F6");
  assert.equal(closed, 1);
  assert.equal(form.hidden, true);
  assert.equal(p.focusedId(), "grid-viewport", "closed, the focus went to the trigger in the strip, then on to the grid");
  // the focus stayed on the trigger of an open popover: Escape reaches it as well
  form.hidden = false;
  filters.setAttribute("aria-expanded", "true");
  filters.addEventListener("keydown", (event) => {
    if (event.key === "Escape") { closed += 1; form.hidden = true; filters.setAttribute("aria-expanded", "false"); }
  });
  filters.focus();
  await p.press("F6");
  assert.equal(closed, 2);
  assert.equal(form.hidden, true);
});

test("an open dialog suspends every shortcut, even with the focus elsewhere", async () => {
  const p = page();
  p.nodes.viewport.focus();
  await p.press("?", { shift: true });
  assert.equal(p.shortcuts.dialogOpen(), true);
  p.calls.length = 0;
  const title = p.doc.activeElement;
  for (const key of ["n", "b", "]", "/"]) {
    assert.equal((await p.press(key, { target: title })).prevented, false, key);
  }
  await p.press("F6", { target: title });
  // a modal elsewhere on the page (focus not moved yet) suspends them as well
  const foreign = p.make("dialog", "other-dialog", null);
  foreign.open = true;
  p.shortcuts.closeDialog();
  assert.equal(p.shortcuts.dialogOpen(), false);
  p.nodes.viewport.focus();
  assert.equal((await p.press("n")).prevented, false, "another open dialog");
  assert.equal((await p.press("F6")).prevented, false);
  assert.deepEqual(p.calls, []);
  foreign.open = false;
  assert.equal((await p.press("n")).prevented, true, "closed again: the key works");
});

test("an event somebody already handled is left alone", async () => {
  const p = page();
  p.nodes.viewport.focus();
  const event = p.nodes.viewport.dispatch("keydown", { key: "n" });
  event.preventDefault();
  for (const fn of p.doc.listeners.keydown) fn(event);
  await tick();
  assert.deepEqual(p.calls, []);
  const composing = p.nodes.viewport.dispatch("keydown", { key: "n", isComposing: true }); // an input method is composing
  for (const fn of p.doc.listeners.keydown) fn(composing);
  await tick();
  assert.deepEqual(p.calls, []);
  assert.equal(composing.prevented, false);
});

// ---- single-key shortcuts (WCAG 2.1.4) -------------------------------------------------------------------------------

test("with the single-key shortcuts off the letters stop and F6, Alt+Arrow and Ctrl keys stay; the choice is remembered", async () => {
  const data = new Map();
  const storage = { getItem: (k) => data.get(k) ?? null, setItem: (k, v) => data.set(k, v) };
  const p = page({ storage });
  p.nodes.viewport.focus();
  assert.equal(p.shortcuts.singleKeys(), true, "on by default");
  p.shortcuts.setSingleKeys(false);
  assert.equal(data.get("dmslog.singleKeys"), "off");
  p.calls.length = 0;
  for (const key of ["n", "b", "]", "/", "t"]) {
    assert.equal((await p.press(key)).prevented, false, key);
  }
  assert.equal((await p.press("?", { shift: true })).prevented, false, "the ? key is a single key too");
  assert.equal(p.shortcuts.dialogOpen(), false);
  assert.deepEqual(p.calls, []);
  await p.press("ArrowLeft", { alt: true });
  assert.deepEqual(p.calls.at(-1), ["viewsBack"]);
  await p.press("F6");
  assert.equal(p.focusedId() !== "grid-viewport", true, "F6 still moves");
  // the registry sees the same preference (the toolbar and the palette ask it)
  assert.equal(p.actions.match({ key: "n" }, {}), null);
  p.shortcuts.setSingleKeys(true);
  assert.equal(p.actions.match({ key: "n" }, {}), "navigate.nextError");
  assert.equal(data.get("dmslog.singleKeys"), "on");
  // a new page load reads it back
  p.shortcuts.setSingleKeys(false);
  const again = page({ storage });
  assert.equal(again.shortcuts.singleKeys(), false);
  again.nodes.viewport.focus();
  assert.equal((await again.press("n")).prevented, false);
});

test("blocked storage keeps the keys working and the choice for the page", async () => {
  const storage = { getItem() { throw new Error("blocked"); }, setItem() { throw new Error("quota"); } };
  const p = page({ storage });
  assert.equal(p.shortcuts.singleKeys(), true);
  p.shortcuts.setSingleKeys(false);
  assert.equal(p.shortcuts.singleKeys(), false);
  p.nodes.viewport.focus();
  assert.equal((await p.press("n")).prevented, false);
  p.shortcuts.setSingleKeys(true);
  assert.equal((await p.press("n")).prevented, true);
});

test("the change event fires once per real change and the unsubscribe works", () => {
  const p = page();
  const seen = [];
  const off = p.shortcuts.on("change", (value) => seen.push(value));
  p.shortcuts.setSingleKeys(true); // already on
  p.shortcuts.setSingleKeys(false);
  p.shortcuts.setSingleKeys(false);
  off();
  p.shortcuts.setSingleKeys(true);
  assert.deepEqual(seen, [false]);
  assert.equal(typeof p.shortcuts.on("other", () => {}), "function");
});

// ---- regions ---------------------------------------------------------------------------------------------------------

const walk = async (p, key, shift, steps) => {
  const seen = [];
  for (let i = 0; i < steps; i += 1) {
    await p.press(key, { shift });
    seen.push(p.focusedId());
  }
  return seen;
};

test("the regions are those of the model, in its order", () => {
  const p = page();
  assert.deepEqual(p.shortcuts.regions.ids, [...REGION_ORDER]);
});

test("F6 walks the regions in Tab order on the wide layout, skipping the closed dock, and wraps", async () => {
  const p = page({ width: 1700 });
  p.nodes.caseButton.focus();
  assert.deepEqual(await walk(p, "F6", false, 6), ["side-collapse", "chip-a", "grid-viewport", "detail-title", "case-button", "side-collapse"]);
  // backward from the first one wraps to the end
  p.nodes.caseButton.focus();
  assert.deepEqual(await walk(p, "F6", true, 3), ["detail-title", "grid-viewport", "chip-a"]);
});

test("the dock is a region only while it is open, and the detail comes after it", async () => {
  const p = page({ width: 1700, dockOpen: true });
  p.nodes.viewport.focus();
  await p.press("F6");
  assert.equal(p.focusedId(), "tab-b", "the selected tab, not the first one");
  await p.press("F6");
  assert.equal(p.focusedId(), "detail-title");
  await p.press("F6", { shift: true });
  assert.equal(p.focusedId(), "tab-b");
  p.nodes.dock.hidden = true;
  p.nodes.viewport.focus();
  await p.press("F6");
  assert.equal(p.focusedId(), "detail-title", "closed dock: skipped");
});

test("closed drawers are not regions; the rail stands for the sources panel", async () => {
  const p = page({ width: 1400 }); // rail layout: the sources panel is a closed drawer
  p.nodes.caseButton.focus();
  assert.deepEqual(p.shortcuts.regions.available(), ["bar", "side", "strip", "grid", "detail"]);
  await p.press("F6");
  assert.equal(p.focusedId(), "side-rail", "the rail button");
  const narrow = page({ width: 1000 }); // the detail is a closed drawer as well
  assert.deepEqual(narrow.shortcuts.regions.available(), ["bar", "side", "strip", "grid"]);
  narrow.nodes.viewport.focus();
  await narrow.press("F6");
  assert.equal(narrow.focusedId(), "case-button", "no detail region: back to the bar");
  await narrow.press("F6", { shift: true });
  assert.equal(narrow.focusedId(), "grid-viewport");
});

test("an open drawer is a region and F6 lands in it; when it closes the rail takes over", async () => {
  const p = page({ width: 1400 });
  p.drawers.open("side", p.nodes.rail);
  assert.equal(p.doc.activeElement, p.nodes.sources, "the drawer took the focus");
  assert.ok(p.shortcuts.regions.available().includes("side"));
  await p.press("F6");
  assert.equal(p.focusedId(), "chip-a", "from the drawer on to the strip");
  await p.press("F6", { shift: true });
  assert.equal(p.doc.activeElement, p.nodes.sources, "back to where the focus was in the drawer");
  p.drawers.close("side", { restore: true });
  assert.equal(p.focusedId(), "side-rail");
  p.nodes.viewport.focus();
  await p.press("F6", { shift: true });
  await p.press("F6", { shift: true });
  assert.equal(p.focusedId(), "side-rail", "the remembered sources panel is hidden again: the rail, not a dead focus");
});

test("the detail drawer: a region only when it is open", async () => {
  const p = page({ width: 1000 });
  const { viewport, detailToggle } = p.nodes;
  p.drawers.open("detail", detailToggle);
  assert.ok(p.shortcuts.regions.available().includes("detail"));
  viewport.focus();
  await p.press("F6", { shift: true }); // the grid's previous region is the strip
  await p.press("F6");
  await p.press("F6");
  assert.equal(p.focusedId(), "detail-panel", "the drawer opened with the focus in it: that is where F6 returns");
  p.drawers.close("detail");
  assert.ok(!p.shortcuts.regions.available().includes("detail"));
});

test("a drawer the DOM cannot tell about: the drawers object alone decides", () => {
  const p = page({ width: 1700, withDrawers: false });
  // no CSS here: the sources panel looks displayed; a drawers object that says it is closed wins
  const closed = createRegions({ doc: p.doc, hasCase: () => true, drawers: () => ({ isOpen: () => false }) });
  assert.deepEqual(closed.available(), ["bar", "side", "strip", "grid"].filter((id) => id !== "side" || p.effective(p.nodes.rail)));
  assert.ok(!closed.available().includes("detail"));
  const open = createRegions({ doc: p.doc, hasCase: () => true, drawers: () => ({ isOpen: () => true }) });
  assert.ok(open.available().includes("detail") && open.available().includes("side"));
  closed.destroy();
  open.destroy();
});

test("combined layout states: a drawer left open when the case closes is not a region; collapsed + opened is", async () => {
  const p = page({ width: 1400 });
  p.drawers.open("side", p.nodes.rail);
  assert.ok(p.shortcuts.regions.available().includes("side"));
  p.setCase(null);
  p.drawers.paint();
  assert.ok(!p.shortcuts.regions.available().includes("side"), "body.no-case hides the drawer");
  assert.ok(!p.shortcuts.regions.available().includes("detail"));
  p.setCase("C1");
  const wide = page({ width: 1700 });
  wide.nodes.sideCollapse.click(); // collapsed by choice: the rail
  wide.nodes.rail.click(); // and opened from the rail
  assert.equal(wide.doc.activeElement, wide.nodes.sources);
  wide.nodes.viewport.focus();
  await wide.press("F6", { shift: true });
  await wide.press("F6", { shift: true });
  assert.equal(wide.doc.activeElement, wide.nodes.sources, "back into the open drawer");
});

test("a button of the empty state remembered from the time without a case is not a landing place once the case is open", async () => {
  const p = page({ caseId: null });
  p.nodes.caseButton.focus();
  await p.press("F6");
  assert.equal(p.focusedId(), "empty-new");
  p.setCase("C1");
  p.nodes.empty.hidden = true; // case-empty.js hides it when a case opens
  p.nodes.caseButton.focus();
  await p.press("F6"); // side
  await p.press("F6"); // strip
  await p.press("F6"); // grid
  assert.equal(p.focusedId(), "grid-viewport");
});

test("the collapsed sources panel (the user's choice on the wide layout) is the rail", async () => {
  const p = page({ width: 1700 });
  p.nodes.sideCollapse.click();
  assert.equal(has(p.nodes.panels, "is-side-collapsed"), true);
  p.nodes.caseButton.focus();
  await p.press("F6");
  assert.equal(p.focusedId(), "side-rail");
});

test("without a case F6 goes between the bar and the empty state only", async () => {
  const p = page({ caseId: null });
  assert.deepEqual(p.shortcuts.regions.available(), ["bar", "grid"]);
  p.nodes.caseButton.focus();
  await p.press("F6");
  assert.equal(p.focusedId(), "empty-new", "the New case… button of the empty state");
  await p.press("F6");
  assert.equal(p.focusedId(), "case-button");
  await p.press("F6", { shift: true });
  assert.equal(p.focusedId(), "empty-new");
});

test("a case opened while the focus is in the bar makes the regions appear", async () => {
  const p = page({ caseId: null });
  assert.deepEqual(p.shortcuts.regions.available(), ["bar", "grid"]);
  p.setCase("C2");
  assert.deepEqual(p.shortcuts.regions.available(), ["bar", "side", "strip", "grid", "detail"]);
});

test("coming back to a region lands on the control that had the focus, unless it is gone or hidden", async () => {
  const p = page({ width: 1700 });
  p.nodes.chipB.focus();
  await p.press("F6"); // grid
  assert.equal(p.focusedId(), "grid-viewport");
  await p.press("F6", { shift: true });
  assert.equal(p.focusedId(), "chip-b", "not the first chip");
  p.nodes.chipB.remove();
  p.nodes.viewport.focus();
  await p.press("F6", { shift: true });
  assert.equal(p.focusedId(), "chip-a", "the remembered chip is gone: the first control of the strip");
  p.nodes.chipA.focus();
  p.nodes.chipB = null;
  const hidden = p.make("button", "chip-c", p.nodes.strip);
  hidden.focus();
  p.nodes.viewport.focus();
  hidden.hidden = true;
  await p.press("F6", { shift: true });
  assert.equal(p.focusedId(), "chip-a", "the remembered chip is hidden: not a dead focus");
});

test("a focus between two regions (the timeline) goes to the next one, or back to the one before it", async () => {
  const p = page({ width: 1700 });
  p.nodes.timelineButton.focus();
  await p.press("F6");
  assert.equal(p.focusedId(), "chip-a", "forward: the strip");
  p.nodes.timelineButton.focus();
  await p.press("F6", { shift: true });
  assert.equal(p.focusedId(), "side-collapse", "backward: the sources, not the bar");
});

test("the skip link and the page start: forward goes to the bar, backward to the last region", async () => {
  const p = page({ width: 1700 });
  p.nodes.skip.focus();
  await p.press("F6");
  assert.equal(p.focusedId(), "case-button");
  p.nodes.skip.focus();
  await p.press("F6", { shift: true });
  assert.equal(p.focusedId(), "detail-title");
});

test("a region with nothing to focus is skipped", async () => {
  const p = page({ width: 1700 });
  for (const node of [p.nodes.chipA, p.nodes.chipB, p.nodes.detailToggle]) node.disabled = true;
  p.nodes.caseButton.focus();
  await p.press("F6");
  await p.press("F6");
  assert.equal(p.focusedId(), "grid-viewport", "the strip has nothing to focus");
});

test("only one region and no region at all do not break F6", async () => {
  const p = page({ width: 1700, caseId: null });
  p.nodes.empty.hidden = true;
  p.nodes.caseButton.focus();
  assert.deepEqual(p.shortcuts.regions.available(), ["bar"]);
  await p.press("F6");
  assert.equal(p.focusedId(), "case-button");
  p.nodes.toolbar.hidden = true;
  assert.deepEqual(p.shortcuts.regions.available(), []);
  assert.equal(p.shortcuts.regions.move(1), null);
});

// ---- skip link -------------------------------------------------------------------------------------------------------

test("the skip link takes the focus to the grid in one step, or to the empty state without a case", async () => {
  const p = page();
  p.nodes.skip.focus();
  const click = p.nodes.skip.dispatch("click");
  assert.equal(click.prevented, true);
  assert.equal(p.focusedId(), "grid-viewport");
  const none = page({ caseId: null });
  none.nodes.skip.focus();
  none.nodes.skip.dispatch("click");
  assert.equal(none.focusedId(), "empty-new");
  // nothing to land on: the link keeps its native behaviour
  none.nodes.empty.hidden = true;
  none.nodes.skip.focus();
  assert.equal(none.nodes.skip.dispatch("click").prevented, false);
});

// ---- the dialog ------------------------------------------------------------------------------------------------------

const dialogOf = (p) => p.doc.body.all().filter((n) => n.tagName === "DIALOG");
const rowsOf = (dlg) => dlg.all().filter((n) => n.tagName === "TR" && n.dataset.action);
const textOf = (node) => node.textContent;

test("? opens the shortcuts dialog from the registry and the focus comes back to the opener", async () => {
  const p = page();
  p.nodes.viewport.focus();
  const event = await p.press("?", { shift: true });
  assert.equal(event.prevented, true);
  const [dlg] = dialogOf(p);
  assert.ok(dlg && p.shortcuts.dialogOpen());
  assert.equal(dlg.attrs.open, "");
  assert.equal(p.doc.activeElement.tagName, "H2", "the focus is in the dialog");
  assert.match(textOf(dlg), new RegExp(S.scTitle));
  const rows = rowsOf(dlg);
  const byId = Object.fromEntries(rows.map((r) => [r.dataset.action, textOf(r)]));
  assert.equal(byId["navigate.nextError"], "n" + S.actNextError);
  assert.equal(byId["navigate.prevError"], "N" + S.actPrevError);
  assert.equal(byId["navigate.nextRegion"], "F6" + S.actNextRegion);
  assert.equal(byId["navigate.prevRegion"], "Shift+F6" + S.actPrevRegion);
  assert.equal(byId["views.back"], "Alt+Left" + S.actViewBack);
  assert.equal(byId["navigate.focusSearch"], "/" + S.actFocusSearch);
  assert.equal(byId["help.shortcuts"], "?" + S.actShortcuts);
  assert.equal(byId["toggle.bookmarks"], "B" + S.actBookmarks);
  assert.deepEqual(rows.map((r) => r.dataset.action), p.actions.list({ withShortcut: true }).map((d) => d.id), "one row per shortcut, in registry order");
  assert.match(textOf(dlg), new RegExp(S.timelineHint.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")), "the timeline tip");
  assert.match(textOf(dlg), new RegExp(S.scGridMove));
  p.shortcuts.closeDialog();
  assert.equal(p.shortcuts.dialogOpen(), false);
  assert.equal(p.doc.activeElement, p.nodes.viewport);
});

test("the dialog opened from the status bar button gives the focus back to that button; opening twice keeps one dialog", async () => {
  const p = page();
  p.nodes.helpButton.focus();
  await p.actions.invoke("help.shortcuts", p.ctx);
  assert.equal(p.shortcuts.dialogOpen(), true);
  await p.actions.invoke("help.shortcuts", p.ctx);
  assert.equal(dialogOf(p).length, 1);
  p.shortcuts.closeDialog();
  assert.equal(p.doc.activeElement, p.nodes.helpButton);
  p.nodes.viewport.focus();
  p.shortcuts.openDialog();
  assert.equal(dialogOf(p).length, 1, "reopened: still one dialog");
  assert.equal(rowsOf(dialogOf(p)[0]).length, p.actions.list({ withShortcut: true }).length, "rows are rebuilt, not added");
  // Esc (the native cancel) closes it and gives the focus back
  dialogOf(p)[0].dispatch("cancel");
  assert.equal(p.shortcuts.dialogOpen(), false);
  assert.equal(p.doc.activeElement, p.nodes.viewport);
});

test("the dialog gives the focus back to the opener it was given, not to whatever has the focus when it opens", () => {
  const p = page();
  p.nodes.viewport.focus();
  p.shortcuts.openDialog(p.nodes.helpButton);
  p.shortcuts.closeDialog();
  assert.equal(p.doc.activeElement, p.nodes.helpButton);
});

test("the dialog is labelled: the checkbox is described by its hint and the tables have names and column headers", () => {
  const p = page();
  p.shortcuts.openDialog();
  const [dlg] = dialogOf(p);
  const box = dlg.all().find((n) => n.id === "sc-single-keys");
  const hint = dlg.all().find((n) => n.id === box.attrs["aria-describedby"]);
  assert.equal(hint.textContent, S.scSingleKeysHint);
  const [table] = dlg.all().filter((n) => n.tagName === "TABLE");
  assert.equal(table.attrs["aria-label"], S.scTitle);
  const heads = dlg.all().filter((n) => n.tagName === "TH");
  assert.deepEqual(heads.map((h) => [h.textContent, h.attrs.scope]), [[S.scColKey, "col"], [S.scColAction, "col"]]);
});

test("the dialog toggle turns the letters off and on, shows the state, marks the rows and is remembered", async () => {
  const data = new Map();
  const storage = { getItem: (k) => data.get(k) ?? null, setItem: (k, v) => data.set(k, v) };
  const p = page({ storage });
  p.nodes.viewport.focus();
  p.shortcuts.openDialog();
  const [dlg] = dialogOf(p);
  const box = dlg.all().find((n) => n.id === "sc-single-keys");
  assert.equal(box.type, "checkbox");
  assert.equal(box.checked, true);
  assert.match(textOf(dlg), new RegExp(S.scSingleKeys + S.scOn));
  box.checked = false;
  box.dispatch("change");
  assert.equal(p.shortcuts.singleKeys(), false);
  assert.equal(data.get("dmslog.singleKeys"), "off");
  assert.match(textOf(dlg), new RegExp(S.scSingleKeys + S.scOff));
  const byId = Object.fromEntries(rowsOf(dlg).map((r) => [r.dataset.action, r]));
  assert.match(byId["navigate.nextError"].className, /sc-off/);
  assert.match(textOf(byId["navigate.nextError"]), /\(off\)/);
  assert.match(textOf(byId["help.shortcuts"]), /\(off\)/, "? is a single key");
  assert.doesNotMatch(byId["navigate.nextRegion"].className, /sc-off/);
  assert.doesNotMatch(textOf(byId["views.back"]), /\(off\)/);
  box.checked = true;
  box.dispatch("change");
  assert.equal(data.get("dmslog.singleKeys"), "on");
  assert.doesNotMatch(textOf(rowsOf(dlg)[0]), /\(off\)/);
  // turned off from the dialog, then reopened from the status bar button: the dialog still opens and shows it
  p.shortcuts.setSingleKeys(false);
  assert.equal(box.checked, false, "the open dialog follows a change made elsewhere");
});

test("the dialog says which shortcuts need a case", () => {
  const p = page({ caseId: null });
  p.shortcuts.openDialog();
  const rows = Object.fromEntries(rowsOf(dialogOf(p)[0]).map((r) => [r.dataset.action, r]));
  assert.match(rows["navigate.nextError"].className, /sc-unavailable/);
  assert.equal(rows["navigate.nextError"].title, S.needCase);
  assert.doesNotMatch(rows["navigate.nextRegion"].className, /sc-unavailable/, "F6 needs no case");
});

test("destroy removes every listener and the dialog", async () => {
  const p = page();
  const before = { keydown: p.doc.listeners.keydown.length, focusin: p.doc.listeners.focusin.length };
  p.shortcuts.openDialog();
  p.shortcuts.destroy();
  assert.equal(p.doc.listeners.keydown.length, before.keydown - 1);
  assert.equal(p.doc.listeners.focusin.length, before.focusin - 1);
  assert.equal(dialogOf(p).length, 0);
  p.nodes.viewport.focus();
  p.calls.length = 0;
  await p.press("n");
  p.nodes.skip.dispatch("click");
  assert.deepEqual(p.calls, []);
  assert.equal(p.focusedId(), "grid-viewport", "the skip link no longer moves the focus");
});

// ---- helpers -----------------------------------------------------------------------------------------------------------

test("isVisible and firstFocusable follow hidden ancestors, checkVisibility, tabindex and disabled", () => {
  const p = page({ width: 1700 });
  const { sources, sideCollapse, pathInput } = p.nodes;
  assert.equal(isVisible(sideCollapse), true);
  assert.equal(firstFocusable(sources), sideCollapse);
  sideCollapse.disabled = true;
  assert.equal(firstFocusable(sources), pathInput, "disabled controls are skipped");
  pathInput.setAttribute("tabindex", "-1");
  assert.equal(firstFocusable(sources), null, "tabindex -1 is not in the Tab order");
  pathInput.removeAttribute("tabindex");
  const hiddenInput = p.make("input", "hid", sources);
  hiddenInput.setAttribute("type", "hidden");
  pathInput.disabled = true;
  assert.equal(firstFocusable(sources), null, "a hidden input is not a control");
  sources.hidden = true;
  assert.equal(isVisible(pathInput), false, "a hidden ancestor");
  assert.equal(firstFocusable(sources), null);
  sources.hidden = false;
  assert.equal(isVisible(null), false);
  const lonely = p.doc.createElement("button");
  assert.equal(isVisible(lonely), false, "not connected");
  const css = p.doc.createElement("button");
  p.nodes.toolbar.appendChild(css);
  css.checkVisibility = () => false;
  assert.equal(isVisible(css), false, "hidden by CSS");
  css.checkVisibility = () => { throw new Error("nope"); };
  assert.equal(isVisible(css), true, "a failing checkVisibility does not hide it");
  const link = p.make("a", "lnk", p.nodes.toolbar);
  assert.equal(firstFocusable(p.make("div", "wrap1", p.nodes.toolbar)), null);
  link.setAttribute("href", "#x");
  const wrap = p.make("div", "wrap2", p.nodes.toolbar);
  wrap.appendChild(link);
  assert.equal(firstFocusable(wrap), link, "an anchor with href");
});

// ---- review round: disabled landing places, dialog return, skip link with the dock maximized, AltGr, repeat, IME -----------

test("a remembered control that became disabled, or that refuses the focus, is not a landing place", async () => {
  const p = page({ width: 1700 });
  p.nodes.chipA.focus();
  await p.press("F6"); // grid
  p.nodes.chipA.disabled = true; // the Back button of the root view
  await p.press("F6", { shift: true });
  assert.equal(p.focusedId(), "chip-b", "the first enabled control of the strip, not a stuck grid");
  p.nodes.viewport.focus();
  p.nodes.chipB.focus = () => {}; // the browser would not take it
  await p.press("F6", { shift: true });
  assert.equal(p.focusedId() !== "grid-viewport", true, "F6 moved on to a region that takes the focus");
  p.nodes.chipA.setAttribute("aria-disabled", "true");
  assert.equal(p.shortcuts.regions.restore(p.nodes.chipA), true);
  assert.notEqual(p.doc.activeElement, p.nodes.chipA);
});

test("the dialog returns the focus to a visible control: window shrunk, opener removed or disabled", async () => {
  const p = page({ width: 1700 });
  p.nodes.detailTitle.focus();
  await p.press("?", { shift: true });
  p.resize(1000); // the detail is a closed drawer now
  p.shortcuts.closeDialog();
  assert.equal(p.effective(p.doc.activeElement), true, "never a hidden opener");
  assert.equal(p.doc.activeElement, p.nodes.viewport, "the grid");
  const q = page({ width: 1700 });
  const gone = q.make("button", "gone", q.nodes.strip);
  gone.focus();
  q.shortcuts.openDialog();
  gone.remove();
  q.shortcuts.closeDialog();
  assert.equal(q.focusedId(), "chip-a", "the region the opener was in");
  const r = page({ width: 1700 });
  r.nodes.chipB.focus();
  r.shortcuts.openDialog();
  r.nodes.chipB.disabled = true;
  r.shortcuts.closeDialog();
  assert.equal(r.focusedId(), "chip-a");
  const t = page({ width: 1700 });
  t.nodes.chipB.focus();
  t.shortcuts.openDialog();
  t.shortcuts.closeDialog();
  assert.equal(t.focusedId(), "chip-b", "the normal case still returns to the opener");
});

test("the skip link restores a maximized dock to reach the grid, or lands on the dock when there is no room", async () => {
  const p = page({ width: 1700, dockOpen: true });
  p.state.maximized = true;
  p.nodes.skip.focus();
  const click = p.nodes.skip.dispatch("click");
  assert.deepEqual(p.calls.filter(([n]) => n === "dock.maximize"), [["dock.maximize", false]]);
  assert.equal(click.prevented, true);
  assert.equal(p.focusedId(), "grid-viewport");
  const q = page({ width: 1700, dockOpen: true });
  q.state.maximized = true;
  q.state.maximizedOnly = true;
  q.nodes.empty.hidden = true; // case-empty.js hides the empty state while a case is open
  q.nodes.skip.focus();
  assert.equal(q.nodes.skip.dispatch("click").prevented, true);
  assert.equal(q.focusedId(), "tab-b", "the dock is the visible place");
  // the grid is shown: the dock is left alone
  const r = page({ width: 1700, dockOpen: true });
  r.nodes.skip.focus();
  r.nodes.skip.dispatch("click");
  assert.deepEqual(r.calls.filter(([n]) => n === "dock.maximize"), []);
});

test("AltGr (AltGraph without Ctrl or Alt) types a character: it is never a shortcut", async () => {
  const p = page();
  p.nodes.viewport.focus();
  const altGr = { getModifierState: (name) => name === "AltGraph" };
  for (const key of ["[", "]", "n", "F6", "/"]) {
    assert.equal((await p.press(key, altGr)).prevented, false, key);
  }
  assert.deepEqual(p.calls, []);
  assert.equal(p.focusedId(), "grid-viewport");
  assert.equal((await p.press("[", { getModifierState: () => false })).prevented, true, "without AltGr it is the shortcut");
  const broken = { getModifierState() { throw new Error("nope"); } };
  assert.equal((await p.press("]", broken)).prevented, true, "a failing getModifierState does not break the keys");
});

test("holding a key repeats only the navigation; a held b does not toggle again", async () => {
  const p = page();
  p.nodes.viewport.focus();
  p.calls.length = 0;
  const held = await p.press("b", { repeat: true });
  assert.deepEqual(p.calls, []);
  assert.equal(held.prevented, true, "still taken from the browser");
  await p.press("b");
  assert.deepEqual(p.calls, [["toggleBookmark"]]);
  p.calls.length = 0;
  await p.press("c", { repeat: true });
  await p.press("F6", { repeat: true });
  await p.press("?", { repeat: true, shift: true });
  await p.press("ArrowLeft", { alt: true, repeat: true });
  assert.deepEqual(p.calls, []);
  assert.equal(p.focusedId(), "grid-viewport", "F6 did not run again either");
  await p.press("n", { repeat: true });
  await p.press("]", { repeat: true });
  assert.deepEqual(p.calls, [["navigateRecord", "error", "fwd"], ["navigateHighlight", "fwd"]]);
});

test("an input method that is composing (isComposing or keyCode 229) is left alone", async () => {
  const p = page();
  p.nodes.viewport.focus();
  for (const init of [{ isComposing: true }, { keyCode: 229 }]) {
    assert.equal((await p.press("n", init)).prevented, false);
    assert.equal((await p.press("F6", init)).prevented, false);
  }
  assert.deepEqual(p.calls, []);
  assert.equal((await p.press("n", { keyCode: 78 })).prevented, true);
});
