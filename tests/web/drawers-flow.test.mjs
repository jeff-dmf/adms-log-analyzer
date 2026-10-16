// UI-17: js/drawers.js on a fake DOM: the rail and the drawers by keyboard (Enter, Esc, focus back), aria-expanded, the
// count, resizing with a drawer open, the collapse choices and their storage. The layout itself is CSS (responsive-css test).
import test from "node:test";
import assert from "node:assert/strict";

import { createDrawers } from "../../src/dmslog/web/js/drawers.js";
import { S } from "../../src/dmslog/web/js/strings.js";
import { makeDoc } from "./fixtures/fake-dom.mjs";

function setup({ width = 1400, stored = null, throwingStorage = false, noMatchMedia = false } = {}) {
  const doc = makeDoc();
  doc.getElementById = (id) => doc.body.all().find((n) => n.id === id) ?? null;
  const make = (tag, id, parent) => {
    const node = doc.createElement(tag);
    node.id = id;
    (parent || doc.body).appendChild(node);
    return node;
  };
  const panels = make("main", "panels");
  const top = make("header", "toolbar", doc.body);
  const search = make("input", "search-input", top);
  const rail = make("button", "side-rail", panels);
  const count = make("span", "side-rail-count", rail);
  const sources = make("aside", "sources-panel", panels);
  const sideCollapse = make("button", "side-collapse", sources);
  const sourceInput = make("input", "path-input", sources);
  const gridPanel = make("section", "grid-panel", panels);
  const detailToggle = make("button", "detail-toggle", gridPanel);
  const viewport = make("div", "grid-viewport", gridPanel);
  const detail = make("aside", "detail-panel", panels);
  const detailCollapse = make("button", "detail-collapse", detail);
  const detailText = make("pre", "detail-text", detail);
  // What the CSS does (css/responsive.css), so "visible" is the effective one: ancestors and media queries, not `hidden`.
  const css = { viewportHidden: false };
  const has = (node, name) => node.classList.contains(name);
  const rules = {
    "sources-panel": () => (win.innerWidth >= 1600 && !has(panels, "is-side-collapsed")) || has(panels, "is-side-open"),
    "detail-panel": () => (win.innerWidth >= 1280 && !has(panels, "is-detail-collapsed")) || has(panels, "is-detail-open"),
    "side-rail": () => win.innerWidth < 1600 || has(panels, "is-side-collapsed"),
    "detail-toggle": () => win.innerWidth < 1280 || has(panels, "is-detail-collapsed"),
    "grid-viewport": () => !css.viewportHidden,
  };
  const effective = (node) => {
    for (let n = node; n; n = n.parent) {
      if (n.hidden || (rules[n.id] && !rules[n.id]())) return false;
    }
    return node.isConnected;
  };
  for (const node of doc.body.all()) {
    node.checkVisibility = () => effective(node);
    const focus = node.focus.bind(node);
    node.focus = () => { if (effective(node)) focus(); }; // the browser does not focus what is not displayed
  }
  const mem = new Map(stored ? [["dmslog.layout", JSON.stringify(stored)]] : []);
  const storage = throwingStorage
    ? { getItem() { throw new Error("blocked"); }, setItem() { throw new Error("quota"); } }
    : { getItem: (k) => mem.get(k) ?? null, setItem: (k, v) => mem.set(k, v) };
  const resizers = [];
  const win = {
    innerWidth: width,
    addEventListener: (type, fn) => type === "resize" && resizers.push(fn),
    removeEventListener: (type, fn) => type === "resize" && resizers.splice(resizers.indexOf(fn), 1),
  };
  if (!noMatchMedia) {
    win.matchMedia = (query) => {
      const limit = Number(/width < (\d+)px/.exec(query)[1]);
      return { get matches() { return win.innerWidth < limit; } };
    };
  }
  const drawers = createDrawers({ doc, win, storage });
  const resize = (w) => {
    win.innerWidth = w;
    if (!effective(doc.activeElement)) doc.activeElement = doc.body; // the browser's focus fixup: a hidden node loses it silently
    resizers.slice().forEach((fn) => fn());
  };
  const key = (target, k, init = {}) => target.dispatch("keydown", { key: k, ...init });
  return { search, css, effective, doc, panels, top, rail, count, sources, sideCollapse, sourceInput, gridPanel, detailToggle, viewport, detail, detailCollapse, detailText, mem, drawers, resize, key, win };
}

const cls = (node, name) => node.classList.contains(name);

test("wide: no rail, no Detail button, nothing open; the collapse buttons are offered", () => {
  const t = setup({ width: 1600 });
  assert.equal(t.rail.hidden, true);
  assert.equal(t.detailToggle.hidden, true);
  assert.equal(t.sideCollapse.hidden, false);
  assert.equal(t.detailCollapse.hidden, false);
  assert.equal(t.sideCollapse.getAttribute("aria-label"), S.sideCollapse);
  assert.equal(t.sources.getAttribute("tabindex"), "-1");
  assert.equal(t.drawers.openWhich(), null);
});

test("rail layout: the rail shows with aria-expanded; click opens the drawer and moves the focus in, Esc closes and returns it", () => {
  const t = setup({ width: 1366 });
  assert.equal(t.rail.hidden, false);
  assert.equal(t.rail.getAttribute("aria-expanded"), "false");
  assert.equal(t.detailToggle.hidden, true, "the detail stays in the flow at 1366");
  assert.equal(t.sideCollapse.hidden, true, "the layout forces the rail: no choice to offer");
  t.rail.focus();
  t.rail.click();
  assert.equal(t.rail.getAttribute("aria-expanded"), "true");
  assert.ok(cls(t.panels, "is-side-open"));
  assert.equal(t.doc.activeElement, t.sources);
  t.key(t.sourceInput, "Escape");
  assert.equal(t.rail.getAttribute("aria-expanded"), "false");
  assert.ok(!cls(t.panels, "is-side-open"));
  assert.equal(t.doc.activeElement, t.rail, "the focus returns to the rail");
  t.rail.click();
  t.rail.click(); // the second click closes
  assert.equal(t.drawers.openWhich(), null);
  assert.equal(t.doc.activeElement, t.rail);
});

test("Escape with the focus out in the grid (drawer still open) closes it and puts the focus back on the opener", () => {
  const t = setup({ width: 1366 });
  t.rail.focus();
  t.rail.click();
  t.viewport.focus(); // Tab or a script moved the focus out without a click
  const event = t.key(t.viewport, "Escape");
  assert.equal(event.prevented, true);
  assert.equal(t.drawers.openWhich(), null);
  assert.equal(t.doc.activeElement, t.rail);
});

test("Escape that something inside already handled does not close the drawer", () => {
  const t = setup({ width: 1366 });
  t.rail.click();
  const event = t.sourceInput.dispatch("keydown", { key: "Escape", defaultPrevented: true });
  assert.equal(event.prevented, false);
  assert.equal(t.drawers.openWhich(), "side");
});

test("narrow: Enter in the grid opens the detail drawer, Esc returns the focus to the grid, the Detail button toggles it", () => {
  const t = setup({ width: 1100 });
  assert.equal(t.detailToggle.hidden, false);
  assert.equal(t.detailToggle.getAttribute("aria-expanded"), "false");
  t.viewport.focus();
  t.key(t.viewport, "Enter");
  assert.equal(t.drawers.openWhich(), "detail");
  assert.ok(cls(t.panels, "is-detail-open"));
  assert.equal(t.detailToggle.getAttribute("aria-expanded"), "true");
  assert.equal(t.doc.activeElement, t.detail);
  t.key(t.detailText, "Escape");
  assert.equal(t.drawers.openWhich(), null);
  assert.equal(t.detailToggle.getAttribute("aria-expanded"), "false");
  assert.equal(t.doc.activeElement, t.viewport, "the focus returns to the grid, where Enter was pressed");
  t.detailToggle.focus();
  t.detailToggle.click();
  assert.equal(t.drawers.openWhich(), "detail");
  t.key(t.detail, "Escape");
  assert.equal(t.doc.activeElement, t.detailToggle);
});

test("Enter with a modifier, or in the wide layout, opens nothing", () => {
  const narrow = setup({ width: 1100 });
  narrow.key(narrow.viewport, "Enter", { ctrlKey: true });
  narrow.key(narrow.viewport, "a");
  assert.equal(narrow.drawers.openWhich(), null);
  const wide = setup({ width: 1600 });
  wide.key(wide.viewport, "Enter");
  assert.equal(wide.drawers.openWhich(), null);
});

test("only one drawer is open: opening the detail closes the sources", () => {
  const t = setup({ width: 1100 });
  t.rail.click();
  assert.equal(t.drawers.openWhich(), "side");
  t.detailToggle.click();
  assert.equal(t.drawers.openWhich(), "detail");
  assert.ok(!cls(t.panels, "is-side-open"));
  assert.equal(t.rail.getAttribute("aria-expanded"), "false");
});

test("a click on the grid dismisses the drawer and leaves the focus where the click put it", () => {
  const t = setup({ width: 1100 });
  t.rail.click();
  t.viewport.focus();
  t.viewport.dispatch("pointerdown");
  assert.equal(t.drawers.openWhich(), null);
  assert.equal(t.doc.activeElement, t.viewport);
});

test("resizing with a drawer open: it stays while it is still a drawer; back in the flow it closes and the focus stays on the field", () => {
  const t = setup({ width: 1300 });
  t.rail.focus();
  t.rail.click();
  t.sourceInput.focus();
  t.resize(1100);
  assert.equal(t.drawers.openWhich(), "side", "narrower: still a drawer");
  t.resize(1600);
  assert.equal(t.drawers.openWhich(), null);
  assert.ok(!cls(t.panels, "is-side-open"));
  assert.equal(t.rail.hidden, true);
  assert.equal(t.doc.activeElement, t.sourceInput, "the panel is in the flow and shows the field: the focus stays");
});

test("a resize that puts the detail back in the flow keeps the focus inside it, on a visible node", () => {
  const t = setup({ width: 1100 });
  t.viewport.focus();
  t.key(t.viewport, "Enter");
  t.detailText.focus();
  t.resize(1400);
  assert.equal(t.drawers.openWhich(), null);
  assert.equal(t.doc.activeElement, t.detailText);
  assert.equal(t.detailToggle.hidden, true);
});

test("resize hides the panel that holds the focus, with no drawer ever opened: the focus goes to the visible opener (sources)", () => {
  const t = setup({ width: 1600 });
  t.sourceInput.focus();
  t.resize(1599);
  assert.equal(t.effective(t.sources), false);
  assert.equal(t.doc.activeElement, t.rail, "not left on a hidden field");
  assert.ok(t.effective(t.doc.activeElement));
  assert.equal(t.drawers.openWhich(), null);
});

test("resize hides the detail with the focus inside it (1280 -> 1279): the focus goes to the Detail button", () => {
  const t = setup({ width: 1280 });
  t.detailText.focus();
  assert.equal(t.doc.activeElement, t.detailText);
  t.resize(1279);
  assert.equal(t.doc.activeElement, t.detailToggle);
  t.resize(1600);
  t.detailToggle.focus(); // hidden at 1600: refused, the focus stays
  assert.notEqual(t.doc.activeElement, t.detailToggle);
});

test("resize hides the rail or the Detail button that holds the focus: it goes to the panel that came back into the flow", () => {
  const t = setup({ width: 1400 });
  t.rail.focus();
  t.resize(1600);
  assert.equal(t.doc.activeElement, t.sources);
  const u = setup({ width: 1100 });
  u.detailToggle.focus();
  u.resize(1400);
  assert.equal(u.doc.activeElement, u.detail);
});

test("a click elsewhere forgets where the focus was: a later resize does not steal it", () => {
  const t = setup({ width: 1600 });
  t.sourceInput.focus();
  t.viewport.dispatch("pointerdown"); // lands outside the drawers
  t.doc.activeElement = t.doc.body; // the click on a blank area: the browser focuses the body, no focusin
  t.doc.listeners.pointerdown?.forEach((fn) => fn({ type: "pointerdown", target: t.viewport }));
  t.resize(1599);
  assert.equal(t.doc.activeElement, t.doc.body);
});

test("the Detail button: pointerdown then click (a real mouse press) toggles once, never close-and-reopen", () => {
  const t = setup({ width: 1100 });
  const press = (node) => {
    node.dispatch("pointerdown"); // bubbles to the grid panel, where the button lives
    t.doc.listeners.pointerdown?.forEach((fn) => fn({ type: "pointerdown", target: node }));
    node.click();
  };
  press(t.detailToggle);
  assert.equal(t.drawers.openWhich(), "detail");
  assert.equal(t.detailToggle.getAttribute("aria-expanded"), "true");
  press(t.detailToggle);
  assert.equal(t.drawers.openWhich(), null, "the second press closes it");
  assert.equal(t.detailToggle.getAttribute("aria-expanded"), "false");
  press(t.rail); // the rail is outside the grid, but the same rule holds for it
  assert.equal(t.drawers.openWhich(), "side");
  press(t.rail);
  assert.equal(t.drawers.openWhich(), null);
});

test("focus goes back only to something displayed: an opener that was removed or sits in a hidden grid is skipped", () => {
  const t = setup({ width: 1100 });
  const opener = t.doc.createElement("button");
  opener.id = "elsewhere";
  opener.checkVisibility = () => t.effective(opener);
  t.top.appendChild(opener);
  opener.focus();
  t.detailToggle.focus();
  t.drawers.open("detail", opener);
  opener.remove(); // gone before Escape
  t.key(t.detail, "Escape");
  assert.equal(t.doc.activeElement, t.detailToggle, "removed opener: next visible control");
  // the grid is hidden (dock maximized): Enter-opened drawer cannot return to the grid
  t.viewport.focus();
  t.key(t.viewport, "Enter");
  t.css.viewportHidden = true;
  t.key(t.detail, "Escape");
  assert.notEqual(t.doc.activeElement, t.viewport);
  assert.ok(t.effective(t.doc.activeElement), "the focus is on something displayed");
  // a hidden ancestor (the whole grid area hidden) is seen too
  t.css.viewportHidden = false;
  t.viewport.focus();
  t.key(t.viewport, "Enter");
  t.gridPanel.hidden = true;
  t.key(t.detail, "Escape");
  assert.equal(t.doc.activeElement, t.search, "the whole grid area is hidden: the focus goes to the search box, never into the grid");
});

test("a node whose engine cannot tell (no checkVisibility) is still not focused when an ancestor is hidden", () => {
  const t = setup({ width: 1100 });
  for (const node of t.doc.body.all()) delete node.checkVisibility; // plain DOM: only the [hidden] chain is known
  t.viewport.focus();
  t.key(t.viewport, "Enter");
  t.gridPanel.hidden = true; // the whole grid area, viewport and Detail button with it
  t.key(t.detail, "Escape");
  assert.equal(t.doc.activeElement, t.search);
});

test("the public interface: isOpen, open, close, toggle and on(change)", () => {
  const t = setup({ width: 1100 });
  const seen = [];
  const off = t.drawers.on("change", (e) => seen.push(e.open));
  assert.equal(t.drawers.isOpen("side"), false);
  assert.equal(t.drawers.isOpen("detail"), false);
  assert.equal(t.drawers.toggle("side"), true);
  assert.equal(t.drawers.isOpen("side"), true);
  assert.equal(t.drawers.isOpen("nope"), false);
  t.drawers.open("detail");
  assert.deepEqual(seen, ["side", "detail"]);
  assert.equal(t.drawers.isOpen("side"), false, "one at a time");
  t.drawers.close("detail");
  assert.deepEqual(seen, ["side", "detail", null]);
  off();
  t.drawers.open("side");
  assert.equal(seen.length, 3, "unsubscribed");
  t.drawers.on("change", () => { throw new Error("listener bug"); });
  t.resize(1600); // the layout changed: listeners are told, a throwing one does not break it
  assert.equal(t.rail.hidden, true);
  assert.equal(t.drawers.isOpen("side"), true, "in the flow = on screen");
  assert.equal(t.drawers.on("other", () => {}) instanceof Function, true);
});

test("in the wide layout toggle collapses the panel (and back); the change event carries the new layout", () => {
  const t = setup({ width: 1700 });
  const layouts = [];
  t.drawers.on("change", (e) => layouts.push(e.sideRail));
  assert.equal(t.drawers.toggle("side"), false);
  assert.equal(t.drawers.isOpen("side"), false);
  assert.ok(cls(t.panels, "is-side-collapsed"));
  assert.equal(t.drawers.toggle("side"), true, "open again as a drawer");
  assert.equal(t.drawers.isOpen("side"), true);
  assert.deepEqual(layouts, [true, true]);
});

test("the count on the rail follows setSources; bad numbers show the name alone", () => {
  const t = setup({ width: 1366 });
  t.drawers.setSources(3, 14);
  assert.equal(t.count.textContent, "3/14");
  assert.match(t.rail.title, /3 of 14 selected/);
  t.drawers.setSources(NaN, NaN);
  assert.equal(t.count.textContent, "");
  assert.equal(t.rail.title, S.railTitle);
  t.drawers.setSources(0, 0);
  assert.equal(t.count.textContent, "0/0");
});

test("collapsing the sources in the wide layout: rail appears, the choice is stored, focus goes to the rail; keep-open undoes it", () => {
  const t = setup({ width: 1700 });
  t.sideCollapse.focus();
  t.sideCollapse.click();
  assert.ok(cls(t.panels, "is-side-collapsed"));
  assert.equal(t.rail.hidden, false);
  assert.equal(t.doc.activeElement, t.rail);
  assert.deepEqual(JSON.parse(t.mem.get("dmslog.layout")), { sideCollapsed: true, detailCollapsed: false });
  t.rail.click();
  assert.equal(t.drawers.openWhich(), "side");
  assert.equal(t.sideCollapse.hidden, false);
  assert.equal(t.sideCollapse.getAttribute("aria-label"), S.sidePin);
  t.sideCollapse.click();
  assert.ok(!cls(t.panels, "is-side-collapsed"));
  assert.equal(t.drawers.openWhich(), null);
  assert.equal(t.rail.hidden, true);
  assert.equal(t.doc.activeElement, t.sources);
  assert.deepEqual(JSON.parse(t.mem.get("dmslog.layout")), { sideCollapsed: false, detailCollapsed: false });
});

test("collapsing the detail: the Detail button appears and the choice is kept for the next opening", () => {
  const t = setup({ width: 1700 });
  t.detailCollapse.click();
  assert.ok(cls(t.panels, "is-detail-collapsed"));
  assert.equal(t.detailToggle.hidden, false);
  assert.equal(t.doc.activeElement, t.detailToggle);
  const again = setup({ width: 1700, stored: { detailCollapsed: true } });
  assert.ok(cls(again.panels, "is-detail-collapsed"));
  assert.equal(again.detailToggle.hidden, false);
  assert.equal(again.detailCollapse.getAttribute("aria-label"), S.detailPin);
});

test("a remembered collapse does nothing harmful in the rail layout, and comes back when the window is wide again", () => {
  const t = setup({ width: 1366, stored: { sideCollapsed: true } });
  assert.equal(t.sideCollapse.hidden, true);
  t.resize(1800);
  assert.equal(t.rail.hidden, false, "still collapsed by choice");
  assert.equal(t.sideCollapse.hidden, false);
});

test("storage that throws or is missing never breaks the drawers", () => {
  const t = setup({ width: 1700, throwingStorage: true });
  t.sideCollapse.click();
  assert.ok(cls(t.panels, "is-side-collapsed"), "works for the session even if it cannot be remembered");
  const none = createDrawers({ doc: t.doc, win: { innerWidth: 1700, addEventListener() {} }, storage: null });
  none.destroy();
});

test("without matchMedia the width decides the same way; hostile widths fall back to wide", () => {
  const t = setup({ width: 1100, noMatchMedia: true });
  assert.equal(t.detailToggle.hidden, false);
  for (const w of [0, NaN, -1, undefined]) {
    t.resize(w);
    assert.equal(t.rail.hidden, true, String(w));
  }
});

test("destroy removes the listeners it added", () => {
  const t = setup({ width: 1100 });
  t.drawers.destroy();
  t.rail.click();
  t.key(t.viewport, "Enter");
  assert.equal(t.drawers.openWhich(), null);
  t.resize(1200);
});

test("missing elements are skipped, not fatal", () => {
  const doc = makeDoc();
  doc.getElementById = () => null;
  const drawers = createDrawers({ doc, win: { innerWidth: 1000, addEventListener() {} }, storage: null });
  assert.equal(drawers.open("side"), true);
  drawers.setSources(1, 2);
  assert.equal(drawers.close(), true);
});
