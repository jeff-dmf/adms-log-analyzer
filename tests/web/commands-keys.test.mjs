// UI-29: the two keyboard entries, through the real dispatcher of the keyboard layer (shortcuts.js) and the real registry:
// Ctrl+K opens the palette from anywhere (a text field included, whatever the one-letter shortcut preference says) except
// where a dialog or a menu already owns the keys; Shift+F10 opens the record menu only in the grid and only with a record.
import test from "node:test";
import assert from "node:assert/strict";

import { createActionRegistry } from "../../src/dmslog/web/js/actions.js";
import { registerAppActions } from "../../src/dmslog/web/js/app-actions.js";
import { createShortcuts } from "../../src/dmslog/web/js/shortcuts.js";
import { S } from "../../src/dmslog/web/js/strings.js";
import { makeDoc, tick } from "./fixtures/fake-dom.mjs";

function page({ singleKeys = true, selected = { id: 1 } } = {}) {
  const doc = makeDoc();
  doc.getElementById = (id) => doc.body.all().find((n) => n.id === id) ?? null;
  doc.querySelectorAll = (sel) => doc.body.all().filter((n) => n.matches(sel));
  const make = (tag, id, parent = doc.body) => {
    const node = doc.createElement(tag);
    node.id = id;
    parent.appendChild(node);
    return node;
  };
  const viewport = make("div", "grid-viewport");
  viewport.setAttribute("tabindex", "0");
  const search = make("input", "search-input");
  const elsewhere = make("button", "elsewhere");
  const calls = [];
  const deps = new Proxy({}, {
    get: (target, name) => (name in target ? target[name] : (...args) => {
      calls.push(name);
      return null;
    }),
  });
  deps.ready = new Proxy({}, { get: () => Promise.resolve() });
  deps.cases = () => [];
  deps.theme = () => "dark";
  deps.drawerOpen = () => false;
  deps.repeats = () => null;
  deps.followTailOn = () => false;
  deps.recordShown = () => selected !== null;
  const ctx = { caseId: () => "C1", selectedRow: () => selected, entry: () => (selected ? { id: 1 } : null), dock: { isOpen: () => false } };
  const storage = { getItem: () => (singleKeys ? "on" : "off"), setItem() {} };
  const actions = createActionRegistry({ strings: S, singleKeysOn: () => singleKeys });
  registerAppActions(actions, deps);
  const shortcuts = createShortcuts({ actions, ctx, getCase: () => "C1", storage, doc });
  const press = (target, key, init = {}) => {
    const event = { type: "keydown", target, key, ctrlKey: false, altKey: false, shiftKey: false, metaKey: false, defaultPrevented: false, repeat: false,
      prevented: false, preventDefault() { this.prevented = true; this.defaultPrevented = true; }, getModifierState: () => false, ...init };
    for (const fn of doc.listeners.keydown ?? []) fn(event);
    return event;
  };
  return { doc, calls, press, viewport, search, elsewhere, shortcuts, make };
}

test("Ctrl+K opens the palette from the grid, from a control and from a text field", async () => {
  const p = page();
  for (const target of [p.viewport, p.elsewhere, p.search]) {
    p.calls.length = 0;
    const e = p.press(target, "k", { ctrlKey: true });
    await tick();
    assert.equal(e.prevented, true, "the browser does not get Ctrl+K");
    assert.deepEqual(p.calls, ["openPalette"]);
  }
});

test("Ctrl+K works with the one-letter shortcuts turned off", async () => {
  const p = page({ singleKeys: false });
  p.press(p.search, "k", { ctrlKey: true });
  await tick();
  assert.deepEqual(p.calls, ["openPalette"]);
  p.calls.length = 0;
  p.press(p.viewport, "?"); // a one-letter shortcut, off
  p.press(p.viewport, "/");
  await tick();
  assert.deepEqual(p.calls, []);
});

test("Ctrl+K does nothing inside a menu, a dialog, or while a modal dialog is open; an input method or AltGr keeps the key", async () => {
  const p = page();
  const menu = p.make("div", "m");
  menu.setAttribute("role", "menu");
  const item = p.make("div", "mi", menu);
  p.press(item, "k", { ctrlKey: true });
  const dialog = p.make("dialog", "d");
  const inDialog = p.make("button", "db", dialog);
  p.press(inDialog, "k", { ctrlKey: true });
  dialog.open = true;
  p.press(p.viewport, "k", { ctrlKey: true });
  dialog.open = false;
  p.press(p.viewport, "k", { ctrlKey: true, isComposing: true });
  p.press(p.viewport, "k", { ctrlKey: true, keyCode: 229 });
  p.press(p.viewport, "k", { ctrlKey: true, getModifierState: (name) => name === "AltGraph" });
  p.press(p.viewport, "k", { ctrlKey: true, defaultPrevented: true });
  await tick();
  assert.deepEqual(p.calls, []);
  p.press(p.viewport, "k", { ctrlKey: true });
  await tick();
  assert.deepEqual(p.calls, ["openPalette"], "and it works again once they are gone");
});

test("a held Ctrl+K opens the palette once; other chords do not open it", async () => {
  const p = page();
  p.press(p.viewport, "k", { ctrlKey: true });
  p.press(p.viewport, "k", { ctrlKey: true, repeat: true });
  p.press(p.viewport, "k", { ctrlKey: true, altKey: true });
  p.press(p.viewport, "k", { altKey: true });
  p.press(p.viewport, "k", { metaKey: true });
  p.press(p.viewport, "p", { ctrlKey: true });
  await tick();
  assert.deepEqual(p.calls, ["openPalette"]);
});

test("Shift+F10 in the grid opens the menu of the selected record; elsewhere it does not", async () => {
  const p = page();
  const e = p.press(p.viewport, "F10", { shiftKey: true });
  await tick();
  assert.equal(e.prevented, true);
  assert.deepEqual(p.calls, ["openRecordMenu"]);
  p.calls.length = 0;
  p.press(p.elsewhere, "F10", { shiftKey: true });
  p.press(p.search, "F10", { shiftKey: true });
  p.press(p.viewport, "F10");
  await tick();
  assert.deepEqual(p.calls, []);
});

test("Shift+F10 without a selected record is left to the browser", async () => {
  const p = page({ selected: null });
  const e = p.press(p.viewport, "F10", { shiftKey: true });
  await tick();
  assert.equal(e.prevented, false);
  assert.deepEqual(p.calls, []);
});

test("both entries are in the registry with their keys, so the shortcuts dialog and the menus show them", () => {
  const p = page();
  assert.ok(p.shortcuts);
  const actions = createActionRegistry({ strings: S });
  registerAppActions(actions, new Proxy({ ready: {} }, { get: (t, n) => (n in t ? t[n] : () => null) }));
  assert.equal(actions.get("help.palette").shortcut, "Ctrl+k");
  assert.equal(actions.get("help.palette").group, "help");
  assert.equal(actions.get("record.contextMenu").shortcut, "Shift+F10");
  assert.equal(actions.get("record.contextMenu").scope, "grid");
});

test("Ctrl+K with Caps Lock on (the key arrives as K, Shift not down) opens the palette; Ctrl+Shift+K does not", async () => {
  const p = page();
  const caps = p.press(p.viewport, "K", { ctrlKey: true, shiftKey: false });
  await tick();
  assert.equal(caps.prevented, true);
  assert.deepEqual(p.calls, ["openPalette"]);
  p.calls.length = 0;
  p.press(p.viewport, "K", { ctrlKey: true, shiftKey: true });
  await tick();
  assert.deepEqual(p.calls, []);
});

test("keySpec: a letter with Ctrl or Alt follows Shift, not Caps Lock; plain letters are unchanged", async () => {
  const { keySpec } = await import("../../src/dmslog/web/js/actions.js");
  assert.equal(keySpec({ key: "K", ctrlKey: true }), "Ctrl+k");
  assert.equal(keySpec({ key: "k", ctrlKey: true, shiftKey: true }), "Ctrl+K");
  assert.equal(keySpec({ key: "K", ctrlKey: true, shiftKey: true }), "Ctrl+K");
  assert.equal(keySpec({ key: "K", altKey: true }), "Alt+k");
  assert.equal(keySpec({ key: "N", shiftKey: true }), "N", "a plain capital stays (n / N are two shortcuts)");
  assert.equal(keySpec({ key: "n" }), "n");
  assert.equal(keySpec({ key: "F10", shiftKey: true }), "Shift+F10");
  assert.equal(keySpec({ key: "]", ctrlKey: true }), "Ctrl+]");
});
