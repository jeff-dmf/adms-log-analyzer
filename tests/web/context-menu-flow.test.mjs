// UI-29: the record context menu on a fake DOM, over the real action registry and the real registrations of app-actions.js:
// opening (pointer and keyboard), the keys of the menu, disabled items, the Same key sub menu, the focus going back to the
// grid, and every way the target can go stale (case, selection, scroll, focus elsewhere, a second open).
import test from "node:test";
import assert from "node:assert/strict";

import { createActionRegistry } from "../../src/dmslog/web/js/actions.js";
import { registerAppActions } from "../../src/dmslog/web/js/app-actions.js";
import { createContextMenu } from "../../src/dmslog/web/js/context-menu.js";
import { S } from "../../src/dmslog/web/js/strings.js";
import { makeDoc, menuEls, tick } from "./fixtures/fake-dom.mjs";

const RECORD = () => ({ id: 7, source_id: 3, tpl_id: 9, keys: [{ key_type: "GUID", value: "abc" }, { key_type: "Session", value: "s1" }] });

function page({ entry = RECORD(), prepare = null, singleKeysOn = true, withAnchor = true } = {}) {
  const doc = makeDoc();
  doc.getElementById = (id) => doc.body.all().find((n) => n.id === id) ?? null;
  const make = (tag, id, parent = doc.body) => {
    const node = doc.createElement(tag);
    node.id = id;
    parent.appendChild(node);
    return node;
  };
  const caseButton = make("button", "case-button");
  const viewport = make("div", "grid-viewport");
  viewport.setAttribute("tabindex", "0");
  const outside = make("button", "outside");
  const st = { caseId: "C1", selected: { id: 7 }, entry, calls: [], filters: [] };
  const calls = st.calls;
  const deps = new Proxy({}, {
    get: (target, name) => (name in target ? target[name] : (...args) => {
      calls.push([name, ...args]);
      return null;
    }),
  });
  deps.ready = new Proxy({}, { get: () => Promise.resolve() });
  deps.cases = () => [];
  deps.theme = () => "dark";
  deps.drawerOpen = () => false;
  deps.repeats = () => null;
  deps.recordShown = () => st.entry !== null;
  deps.hasType = () => st.entry !== null && st.entry.tpl_id > 0;
  deps.highlightReady = () => true;
  const ctx = {
    caseId: () => { if (st.afterCheck) { const later = st.afterCheck; st.afterCheck = null; queueMicrotask(later); } return st.caseId; },
    entry: () => st.entry,
    selectedRow: () => st.selected,
    userFilter: () => null,
    setUserFilter: (filter) => {
      st.filters.push(filter);
    },
    dock: { isOpen: () => false },
  };
  const actions = createActionRegistry({ strings: S, singleKeysOn: () => singleKeysOn });
  registerAppActions(actions, deps);
  const events = [];
  const menu = createContextMenu({
    actions, ctx, doc, singleKeysOn: () => singleKeysOn,
    prepare: prepare ?? (async () => true),
    anchorFor: withAnchor ? () => ({ x: 40, y: 90 }) : null,
    onOpenChange: (open) => events.push(open),
  });
  return { doc, st, calls, menu, viewport, caseButton, outside, events, actions, make };
}

const labelOf = (node) => node.children[0].textContent;
const itemsOf = (doc) => menuEls(doc).flatMap((m) => m.children.filter((c) => c.attrs.role !== "separator"));
const labelsOf = (doc, level = 0) => menuEls(doc)[level].children.filter((c) => c.attrs.role !== "separator").map(labelOf);
const focusedLabel = (doc) => doc.activeElement.children[0]?.textContent;
const press = (doc, key, init = {}) => doc.activeElement.dispatch("keydown", { key, ...init });
const request = (extra = {}) => ({ row: { id: 7 }, x: 15, y: 25, keyboard: false, ...extra });

const LABELS = [
  S.actSameKey, S.actOnlySource, S.actOnlyThisType, S.actHideType, S.actHighlightType, S.actContext,
  S.actBookmark, S.actCompare, S.actCopyRecord,
];

test("a right click opens the menu at the pointer: the record actions with their keys, in sections, the menu itself focused", async () => {
  const { doc, menu, events } = page();
  assert.equal(await menu.open(request()), true);
  const [el] = menuEls(doc);
  assert.equal(el.attrs.role, "menu");
  assert.equal(el.attrs["aria-label"], S.ctxMenuLabel);
  assert.deepEqual(labelsOf(doc), LABELS);
  assert.deepEqual(el.children.map((c) => c.attrs.role).filter((r) => r === "separator").length, 1, "one divider between the two sections");
  assert.equal(el.style.left, "15px");
  assert.equal(el.style.top, "25px");
  assert.deepEqual(itemsOf(doc).filter((n) => n.children[1]?.className === "ui-menu-shortcut").map((n) => [labelOf(n), n.children[1].textContent]),
    [[S.actBookmark, "b"], [S.actCompare, "c"]]);
  assert.equal(doc.activeElement, el, "opened by the pointer: the menu has the focus, no item is active yet");
  assert.equal(menu.isOpen(), true);
  assert.deepEqual(events, [true]);
  assert.ok(!itemsOf(doc).some((n) => labelOf(n) === S.actContextMenu), "the launcher is not in its own menu");
});

test("opened from the keyboard the first item has the focus; arrows, Home, End and wrapping work", async () => {
  const { doc, menu } = page();
  await menu.open(request({ keyboard: true }));
  assert.equal(focusedLabel(doc), S.actSameKey);
  press(doc, "ArrowDown");
  assert.equal(focusedLabel(doc), S.actOnlySource);
  press(doc, "End");
  assert.equal(focusedLabel(doc), S.actCopyRecord);
  press(doc, "ArrowDown");
  assert.equal(focusedLabel(doc), S.actSameKey, "wraps");
  press(doc, "ArrowUp");
  assert.equal(focusedLabel(doc), S.actCopyRecord, "wraps backward");
  press(doc, "Home");
  assert.equal(focusedLabel(doc), S.actSameKey);
});

test("first-letter jump (typeahead) cycles through the items that start with it", async () => {
  const { doc, menu } = page();
  await menu.open(request({ keyboard: true }));
  press(doc, "c");
  assert.equal(focusedLabel(doc), S.actContext);
  press(doc, "c");
  assert.equal(focusedLabel(doc), S.actCompare);
  press(doc, "c");
  assert.equal(focusedLabel(doc), S.actCopyRecord);
  press(doc, "h");
  assert.equal(focusedLabel(doc), S.actHideType);
});

test("Enter runs the item through the registry, closes the menu and the focus is back on the grid", async () => {
  const { doc, menu, calls, viewport, events } = page();
  await menu.open(request({ keyboard: true }));
  press(doc, "End");
  press(doc, "ArrowUp"); // Compare
  press(doc, "Enter");
  await tick();
  assert.deepEqual(calls.filter(([name]) => name === "compareSelected"), [["compareSelected"]]);
  assert.equal(menuEls(doc).length, 0);
  assert.equal(doc.activeElement, viewport);
  assert.equal(menu.isOpen(), false);
  assert.deepEqual(events, [true, false]);
});

test("Escape closes the menu and gives the focus back to the grid; Tab closes it too", async () => {
  const { doc, menu, viewport } = page();
  await menu.open(request({ keyboard: true }));
  press(doc, "Escape");
  assert.equal(menuEls(doc).length, 0);
  assert.equal(doc.activeElement, viewport);
  await menu.open(request({ keyboard: true }));
  press(doc, "Tab");
  assert.equal(menuEls(doc).length, 0);
  assert.equal(doc.activeElement, viewport, "Tab moves on from the grid");
});

test("a menu opened by the pointer closes with Escape as well (the menu itself has the focus)", async () => {
  const { doc, menu, viewport } = page();
  await menu.open(request());
  press(doc, "Escape");
  assert.equal(menuEls(doc).length, 0);
  assert.equal(doc.activeElement, viewport);
});

test("Same key opens a sub menu with the keys of the record; Enter opens the same-key panel with that key; Escape goes back to the parent item", async () => {
  const { doc, menu, calls } = page();
  await menu.open(request({ keyboard: true }));
  press(doc, "ArrowRight");
  assert.equal(menuEls(doc).length, 2);
  assert.deepEqual(labelsOf(doc, 1), ["GUID: abc", "Session: s1"]);
  assert.equal(focusedLabel(doc), "GUID: abc");
  press(doc, "Escape");
  assert.equal(menuEls(doc).length, 1);
  assert.equal(focusedLabel(doc), S.actSameKey);
  press(doc, "ArrowRight");
  press(doc, "ArrowDown");
  press(doc, "Enter");
  await tick();
  assert.deepEqual(calls.filter(([name]) => name === "showSameKey"), [["showSameKey", { type: "Session", value: "s1" }]]);
  assert.equal(menuEls(doc).length, 0);
});

test("Same key is disabled, with the reason as its title, when the record has no keys", async () => {
  const { doc, menu } = page({ entry: { id: 7, source_id: 3, tpl_id: 9, keys: [] } });
  await menu.open(request({ keyboard: true }));
  const same = itemsOf(doc).find((n) => labelOf(n) === S.actSameKey);
  assert.equal(same.attrs["aria-disabled"], "true");
  assert.equal(same.attrs.title, S.actSameKeyNone);
  assert.equal(focusedLabel(doc), S.actOnlySource, "a disabled item is skipped by the keyboard");
});

test("items that do not apply stay in the menu, disabled with their reason; the keyboard skips them and neither Enter nor a click runs them", async () => {
  const { doc, menu, calls, st } = page({ entry: { id: 7, source_id: 3, tpl_id: 0, keys: [] } });
  await menu.open(request({ keyboard: true }));
  const byLabel = (label) => itemsOf(doc).find((n) => labelOf(n) === label);
  for (const label of [S.actOnlyThisType, S.actHideType, S.actHighlightType]) {
    assert.equal(byLabel(label).attrs["aria-disabled"], "true", label);
  }
  assert.equal(focusedLabel(doc), S.actOnlySource, "Same key (no keys) is skipped");
  press(doc, "ArrowDown");
  assert.equal(focusedLabel(doc), S.actContext, "Only this message type, Hide and Highlight are skipped");
  byLabel(S.actHideType).click();
  byLabel(S.actOnlyThisType).click();
  await tick();
  assert.equal(menuEls(doc).length, 1, "a click on a disabled item does not close the menu");
  assert.deepEqual(calls.filter(([name]) => ["filterType", "highlightType"].includes(name)), []);
  assert.deepEqual(st.filters, []);
});

test("Only this source needs the source of the record", async () => {
  const { doc, menu } = page({ entry: { id: 7, tpl_id: 9, keys: [] } });
  await menu.open(request({ keyboard: true }));
  assert.equal(itemsOf(doc).find((n) => labelOf(n) === S.actOnlySource).attrs["aria-disabled"], "true");
});

test("Only this source and Hide this message type narrow the view through the user filter", async () => {
  const { doc, menu, st } = page();
  await menu.open(request({ keyboard: true }));
  press(doc, "ArrowDown");
  press(doc, "Enter"); // Only this source
  await tick();
  await new Promise((resolve) => setTimeout(resolve, 5));
  assert.deepEqual(st.filters, [{ and: [{ source: [3] }] }]);
  await menu.open(request({ keyboard: true }));
  press(doc, "ArrowDown");
  press(doc, "ArrowDown");
  press(doc, "ArrowDown"); // Hide this message type
  assert.equal(focusedLabel(doc), S.actHideType);
  press(doc, "Enter");
  await new Promise((resolve) => setTimeout(resolve, 5));
  assert.deepEqual(st.filters[1], { and: [{ not: { tpl: [9] } }] });
});

test("one-letter shortcuts are not shown beside the items while those are turned off", async () => {
  const { doc, menu } = page({ singleKeysOn: false });
  await menu.open(request());
  assert.deepEqual(itemsOf(doc).filter((n) => n.children[1]?.className === "ui-menu-shortcut"), []);
});

// ---- the target goes stale ----------------------------------------------------------------------------------------

test("a record that cannot be shown opens no menu; prepare gets the row it must show", async () => {
  const seen = [];
  const refuse = page({ prepare: async () => false });
  assert.equal(await refuse.menu.open(request()), false);
  assert.equal(menuEls(refuse.doc).length, 0);
  const throwing = page({ prepare: async () => { throw new Error("offline"); } });
  assert.equal(await throwing.menu.open(request()), false);
  const spy = page({ prepare: async (row) => { seen.push(row.id); return true; } });
  await spy.menu.open(request());
  assert.deepEqual(seen, [7]);
  assert.equal(await spy.menu.open({ row: null }), false);
  assert.equal(await spy.menu.open({ row: { id: null } }), false);
});

test("no menu without a case, or when the case, the selection or the shown record changed while the record loaded", async () => {
  const noCase = page();
  noCase.st.caseId = null;
  assert.equal(await noCase.menu.open(request()), false);

  for (const change of [
    (st) => { st.caseId = "C2"; },
    (st) => { st.selected = { id: 8 }; },
    (st) => { st.entry = { ...RECORD(), id: 8 }; },
    (st) => { st.entry = null; },
  ]) {
    let release;
    const gate = new Promise((resolve) => { release = resolve; });
    const ctxPage = page({ prepare: async () => { await gate; return true; } });
    const pending = ctxPage.menu.open(request());
    change(ctxPage.st);
    release();
    assert.equal(await pending, false);
    assert.equal(menuEls(ctxPage.doc).length, 0);
  }
});

test("close() while the record loads drops the pending menu; a second open overtakes the first", async () => {
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  const p = page({ prepare: async () => { await gate; return true; } });
  const first = p.menu.open(request());
  p.menu.close();
  release();
  assert.equal(await first, false);
  assert.equal(menuEls(p.doc).length, 0);

  const q = page();
  const a = q.menu.open(request({ x: 1 }));
  const b = q.menu.open(request({ x: 2 }));
  assert.deepEqual([await a, await b], [false, true]);
  assert.equal(menuEls(q.doc).length, 1);
  assert.equal(menuEls(q.doc)[0].style.left, "2px");
});

test("opening again while a menu is open replaces it without taking the focus to the grid in between", async () => {
  const { doc, menu, events } = page();
  await menu.open(request({ x: 1, keyboard: true }));
  await menu.open(request({ x: 2, keyboard: true }));
  assert.equal(menuEls(doc).length, 1);
  assert.equal(menuEls(doc)[0].style.left, "2px");
  assert.deepEqual(events, [true, false, true]);
});

test("an action refuses to run when the record is no longer the target (selection moved after the menu opened)", async () => {
  const { doc, menu, calls, st } = page();
  await menu.open(request({ keyboard: true }));
  press(doc, "End");
  st.selected = { id: 99 };
  press(doc, "Enter"); // Copy text
  await tick();
  assert.deepEqual(calls.filter(([name]) => name === "copyRecord"), []);
  assert.equal(menuEls(doc).length, 0);
});

test("an action refuses to run when the case changed, or the detail shows another record, after the menu opened", async () => {
  for (const change of [(st) => { st.caseId = "C2"; }, (st) => { st.entry = { ...RECORD(), id: 8 }; }, (st) => { st.entry = null; }]) {
    const { doc, menu, calls, st } = page();
    await menu.open(request({ keyboard: true }));
    press(doc, "End");
    change(st);
    press(doc, "Enter"); // Copy text
    await tick();
    assert.deepEqual(calls.filter(([name]) => name === "copyRecord"), []);
  }
});

test("close() from outside (case, view or selection change) gives the focus back to the grid only if it was in the menu", async () => {
  const { doc, menu, viewport, outside } = page();
  await menu.open(request({ keyboard: true }));
  menu.close();
  assert.equal(menuEls(doc).length, 0);
  assert.equal(doc.activeElement, viewport);

  await menu.open(request({ keyboard: true }));
  doc.activeElement = outside; // the focus is elsewhere (no focusin: it was moved quietly)
  menu.close();
  assert.equal(doc.activeElement, outside, "the focus is not taken from where it is");

  menu.close(); // closing a closed menu does nothing
  assert.equal(menuEls(doc).length, 0);
});

test("a click outside closes the menu; so does a scroll or a wheel outside it, but not one inside it", async () => {
  const { doc, menu, viewport } = page();
  await menu.open(request({ keyboard: true }));
  for (const fn of doc.listeners.pointerdown) fn({ type: "pointerdown", target: viewport });
  assert.equal(menuEls(doc).length, 0, "pointer down outside");

  await menu.open(request({ keyboard: true }));
  const inside = menuEls(doc)[0];
  for (const fn of doc.listeners.wheel) fn({ type: "wheel", target: inside.children[0] });
  assert.equal(menuEls(doc).length, 1, "a wheel inside the menu keeps it");
  for (const fn of doc.listeners.wheel) fn({ type: "wheel", target: viewport });
  assert.equal(menuEls(doc).length, 0, "a wheel over the grid closes it");
  assert.equal(doc.activeElement, viewport);

  await menu.open(request({ keyboard: true }));
  for (const fn of doc.listeners.scroll) fn({ type: "scroll", target: viewport });
  assert.equal(menuEls(doc).length, 0, "the grid scrolled");
});

test("the focus moving to another control closes the menu and leaves it there", async () => {
  const { doc, menu, outside } = page();
  await menu.open(request({ keyboard: true }));
  outside.focus();
  assert.equal(menuEls(doc).length, 0);
  assert.equal(doc.activeElement, outside);
});

test("closing removes every listener the menu added to the document", async () => {
  const { doc, menu } = page();
  const count = () => ["scroll", "wheel", "focusin", "pointerdown"].map((type) => (doc.listeners[type] ?? []).length);
  const before = count();
  for (let i = 0; i < 3; i += 1) {
    await menu.open(request({ keyboard: true }));
    assert.deepEqual(count(), before.map((n, at) => n + (at === 3 ? 2 : 1)), "added while open (pointerdown: the menu's own and the one that watches the request)");
    press(doc, "Escape");
    assert.deepEqual(count(), before, "removed on close, round " + i);
  }
});

test("the focus goes to the first thing that takes it when the grid is hidden or gone", async () => {
  const { doc, menu, viewport, caseButton } = page();
  await menu.open(request({ keyboard: true }));
  viewport.hidden = true;
  press(doc, "Escape");
  assert.equal(doc.activeElement, caseButton);
  viewport.hidden = false;
  await menu.open(request({ keyboard: true }));
  viewport.remove();
  press(doc, "Escape");
  assert.equal(doc.activeElement, caseButton);
});

test("a focusRow() given by the owner is used, and the fallback runs when it fails", async () => {
  const p = page();
  const log = [];
  const menu = createContextMenu({
    actions: p.actions, ctx: { caseId: () => p.st.caseId, entry: () => p.st.entry, selectedRow: () => p.st.selected, userFilter: () => null, setUserFilter() {}, dock: { isOpen: () => false } },
    doc: p.doc, focusRow: (id) => { log.push(id); return false; },
  });
  await menu.open(request({ keyboard: true }));
  press(p.doc, "Escape");
  assert.deepEqual(log, [7]);
  assert.equal(p.doc.activeElement, p.viewport, "fell back to the grid");
});

test("Shift+F10 (openForSelection) opens the menu for the selected record at the anchor, from the keyboard", async () => {
  const { doc, menu, st } = page();
  assert.equal(await menu.openForSelection(), true);
  assert.equal(menuEls(doc)[0].style.left, "40px");
  assert.equal(menuEls(doc)[0].style.top, "90px");
  assert.equal(focusedLabel(doc), S.actSameKey);
  menu.close();
  st.selected = null;
  assert.equal(await menu.openForSelection(), false);
});

test("openForSelection without an anchor from the owner falls back to the corner of the grid", async () => {
  const { doc, menu } = page({ withAnchor: false });
  assert.equal(await menu.openForSelection(), true);
  assert.equal(menuEls(doc)[0].style.left, "0px");
});

test("destroy closes the menu", async () => {
  const { doc, menu } = page();
  await menu.open(request());
  menu.destroy();
  assert.equal(menuEls(doc).length, 0);
});

// ---- the target is captured once and revalidated after every wait --------------------------------------------------------

const settle = () => new Promise((resolve) => setTimeout(resolve, 5));

function slowPreload(p) {
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  const original = p.actions.preload;
  let armed = false;
  p.actions.preload = (id) => (armed ? gate.then(() => original(id)) : original(id)); // the loader of an action is still arriving
  const arm = () => { armed = true; };
  return Object.assign(() => release(), { arm });
}

test("an action waiting for its module does not hit another record: the selection moved before the filter was applied", async () => {
  const p = page();
  const release = slowPreload(p);
  await p.menu.open(request({ keyboard: true }));
  release.arm();
  press(p.doc, "ArrowDown");
  assert.equal(focusedLabel(p.doc), S.actOnlySource);
  press(p.doc, "Enter");
  p.st.selected = { id: 8 };
  p.st.entry = { ...RECORD(), id: 8, source_id: 5 };
  release();
  await settle();
  assert.deepEqual(p.st.filters, [], "nothing was applied, and certainly not the source of record 8");
});

test("the same wait with nothing moved applies the filter of the record the menu was opened on", async () => {
  const p = page();
  const release = slowPreload(p);
  await p.menu.open(request({ keyboard: true }));
  release.arm();
  press(p.doc, "ArrowDown");
  press(p.doc, "Enter");
  p.st.entry = { ...RECORD(), source_id: 77 }; // the detail reloaded the same record meanwhile
  release();
  await settle();
  assert.deepEqual(p.st.filters, [{ and: [{ source: [3] }] }], "the source read when the menu opened");
});

test("an action waiting for its module is dropped when the case changed", async () => {
  const p = page();
  const release = slowPreload(p);
  await p.menu.open(request({ keyboard: true }));
  release.arm();
  press(p.doc, "ArrowDown");
  press(p.doc, "Enter");
  p.st.caseId = "C2";
  release();
  await settle();
  assert.deepEqual(p.st.filters, []);
});

test("a filter that is applied late is dropped when the record is gone by then (setUserFilter of the bound context)", async () => {
  const p = page();
  await p.menu.open(request({ keyboard: true }));
  press(p.doc, "ArrowDown");
  const run = p.actions.invoke;
  p.actions.invoke = async (id, ctx, args) => { // the action runs, but the target changes while its module loads
    p.st.selected = { id: 8 };
    return run(id, ctx, args);
  };
  press(p.doc, "Enter");
  await settle();
  assert.deepEqual(p.st.filters, []);
});

test("the record must be the selected one before anything is loaded for it", async () => {
  const seen = [];
  const p = page({ prepare: async (row) => { seen.push(row.id); return true; } });
  p.st.selected = { id: 8 };
  assert.equal(await p.menu.open(request()), false);
  assert.deepEqual(seen, [], "no detail request for a record that is not selected");
  p.st.selected = null;
  assert.equal(await p.menu.open(request()), false);
});

test("while the record loads the request is watched: a scroll, a click elsewhere or the focus moving drop it", async () => {
  const cases = [
    (p) => { for (const fn of p.doc.listeners.wheel ?? []) fn({ type: "wheel", target: p.viewport }); },
    (p) => { for (const fn of p.doc.listeners.scroll ?? []) fn({ type: "scroll", target: p.viewport }); },
    (p) => { for (const fn of p.doc.listeners.pointerdown ?? []) fn({ type: "pointerdown", target: p.outside }); },
    (p) => { p.outside.focus(); },
  ];
  for (const act of cases) {
    let release;
    const gate = new Promise((resolve) => { release = resolve; });
    const p = page({ prepare: async () => { await gate; return true; } });
    const pending = p.menu.open(request());
    assert.equal(p.menu.targetId(), 7, "the request is known while it loads");
    act(p);
    release();
    assert.equal(await pending, false);
    assert.equal(menuEls(p.doc).length, 0);
    assert.equal(p.menu.targetId(), null);
    for (const type of ["scroll", "wheel", "focusin", "pointerdown"]) {
      assert.deepEqual(p.doc.listeners[type] ?? [], [], type + " listeners are gone");
    }
  }
});

test("targetId names the record of the open menu", async () => {
  const p = page();
  assert.equal(p.menu.targetId(), null);
  await p.menu.open(request());
  assert.equal(p.menu.targetId(), 7);
  p.menu.close();
  assert.equal(p.menu.targetId(), null);
});

test("a request that failed or was refused leaves no listener behind", async () => {
  const p = page({ prepare: async () => false });
  await p.menu.open(request());
  for (const type of ["scroll", "wheel", "focusin", "pointerdown"]) {
    assert.deepEqual(p.doc.listeners[type] ?? [], [], type);
  }
});

test("an action that reads the live detail (Copy text) also waits for its module and is dropped if the record moved meanwhile", async () => {
  const p = page();
  const release = slowPreload(p);
  await p.menu.open(request({ keyboard: true }));
  release.arm();
  press(p.doc, "End"); // Copy text
  press(p.doc, "Enter");
  p.st.selected = { id: 8 };
  p.st.entry = { ...RECORD(), id: 8 };
  release();
  await settle();
  assert.deepEqual(p.calls.filter(([name]) => name === "copyRecord"), []);
});

test("the interval between the guard and run(): a selection or case change in a microtask right after Enter never lets the action run", async () => {
  for (const move of [(st) => { st.selected = { id: 8 }; st.entry = { ...RECORD(), id: 8 }; }, (st) => { st.caseId = "C2"; }]) {
    const p = page();
    await p.menu.open(request({ keyboard: true }));
    press(p.doc, "End"); // Copy text
    p.st.afterCheck = () => move(p.st); // queued by the item's own check: lands between that check and run()
    press(p.doc, "Enter");
    await settle();
    assert.deepEqual(p.calls.filter(([name]) => name === "copyRecord"), []);
  }
  const ok = page();
  await ok.menu.open(request({ keyboard: true }));
  press(ok.doc, "End");
  press(ok.doc, "Enter");
  await settle();
  assert.deepEqual(ok.calls.filter(([name]) => name === "copyRecord"), [["copyRecord"]], "and it runs when nothing moved");
});

test("the same guard stands in a Same key choice: a change right after the choice never opens the panel", async () => {
  const p = page();
  await p.menu.open(request({ keyboard: true }));
  press(p.doc, "ArrowRight");
  p.st.afterCheck = () => { p.st.selected = { id: 8 }; };
  press(p.doc, "Enter");
  await settle();
  assert.deepEqual(p.calls.filter(([name]) => name === "showSameKey"), []);
});
