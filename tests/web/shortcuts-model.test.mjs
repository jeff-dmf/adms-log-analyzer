// UI-28: the pure rules of the keyboard layer (shortcuts-model.js) and the n/N anchor (filters.js navAnchor).
import test from "node:test";
import assert from "node:assert/strict";

import {
  REGION_ORDER, REPEATABLE_ACTIONS, SINGLE_KEYS_STORAGE_KEY, allowedWhileTyping, allowsRepeat, hasAltGraph, isComposingEvent, isRegionKey, loadSingleKeys, navAnchor, nextRegion, parseSingleKeys,
  saveSingleKeys, shouldDispatch,
} from "../../src/dmslog/web/js/shortcuts-model.js";
import { S } from "../../src/dmslog/web/js/strings.js";
import { keySpec } from "../../src/dmslog/web/js/actions.js";
import { NAV_ERROR_FILTER, navPlan } from "../../src/dmslog/web/js/filters.js";

const S_HAS = (key) => typeof S[key] === "string" && S[key] !== "";
const memory = (initial = {}) => {
  const data = new Map(Object.entries(initial));
  return { data, getItem: (k) => (data.has(k) ? data.get(k) : null), setItem: (k, v) => data.set(k, String(v)) };
};
const throwing = { getItem() { throw new Error("blocked"); }, setItem() { throw new Error("blocked"); } };

test("the preference is on by default and off only when it says so", () => {
  assert.equal(SINGLE_KEYS_STORAGE_KEY, "dmslog.singleKeys");
  for (const raw of [null, undefined, "", "on", "1", "true", "garbage"]) {
    assert.equal(parseSingleKeys(raw), true, String(raw));
  }
  for (const raw of ["off", "0", "false"]) {
    assert.equal(parseSingleKeys(raw), false, raw);
  }
});

test("the preference round-trips through storage; blocked or missing storage never throws", () => {
  const storage = memory();
  assert.equal(loadSingleKeys(storage), true);
  assert.equal(saveSingleKeys(storage, false), true);
  assert.equal(storage.data.get("dmslog.singleKeys"), "off");
  assert.equal(loadSingleKeys(storage), false);
  assert.equal(saveSingleKeys(storage, true), true);
  assert.equal(loadSingleKeys(storage), true);
  assert.equal(loadSingleKeys(throwing), true);
  assert.equal(saveSingleKeys(throwing, false), false);
  assert.equal(loadSingleKeys(null), true);
  assert.equal(saveSingleKeys(null, false), false);
});

test("region keys and Ctrl combinations are the only ones that act in a text field", () => {
  assert.equal(isRegionKey("F6"), true);
  assert.equal(isRegionKey("Shift+F6"), true);
  assert.equal(isRegionKey("Ctrl+F6"), false);
  assert.equal(allowedWhileTyping("F6"), true);
  assert.equal(allowedWhileTyping("Ctrl+k"), true);
  for (const spec of ["n", "?", "/", "Alt+ArrowLeft", "B"]) {
    assert.equal(allowedWhileTyping(spec), false, spec);
  }
});

test("shouldDispatch: single keys obey the preference, typing, menus and dialogs", () => {
  const on = { singleKeysOn: true };
  assert.equal(shouldDispatch("n", on), true);
  assert.equal(shouldDispatch("?", on), true);
  // letters off (WCAG 2.1.4): every one-character key stops, the others stay
  assert.equal(shouldDispatch("n", { singleKeysOn: false }), false);
  assert.equal(shouldDispatch("?", { singleKeysOn: false }), false);
  assert.equal(shouldDispatch("]", { singleKeysOn: false }), false);
  assert.equal(shouldDispatch("F6", { singleKeysOn: false }), true);
  assert.equal(shouldDispatch("Alt+ArrowLeft", { singleKeysOn: false }), true);
  assert.equal(shouldDispatch("Ctrl+k", { singleKeysOn: false }), true);
  // the focus is in a text field: typing wins
  assert.equal(shouldDispatch("n", { ...on, typing: true }), false);
  assert.equal(shouldDispatch("Alt+ArrowLeft", { ...on, typing: true }), false);
  assert.equal(shouldDispatch("F6", { ...on, typing: true }), true);
  assert.equal(shouldDispatch("Ctrl+k", { ...on, typing: true }), true);
  // menus own their keys, but F6 still gets out of them
  assert.equal(shouldDispatch("n", { ...on, inMenu: true }), false);
  assert.equal(shouldDispatch("F6", { ...on, inMenu: true }), true);
  // a dialog owns every key, F6 included
  assert.equal(shouldDispatch("n", { ...on, inDialog: true }), false);
  assert.equal(shouldDispatch("F6", { ...on, inDialog: true }), false);
  // somebody already handled the event
  assert.equal(shouldDispatch("n", { ...on, defaultPrevented: true }), false);
  assert.equal(shouldDispatch("F6", { ...on, defaultPrevented: true }), false);
  assert.equal(shouldDispatch(null, on), false);
  assert.equal(shouldDispatch("", on), false);
  assert.equal(shouldDispatch("n"), true, "the defaults: on, not typing, not in a menu or dialog");
});

test("shouldDispatch agrees with the KeySpec of real events", () => {
  assert.equal(shouldDispatch(keySpec({ key: "F6", shiftKey: true }), {}), true);
  assert.equal(shouldDispatch(keySpec({ key: "?", shiftKey: true }), { singleKeysOn: false }), false);
  assert.equal(shouldDispatch(keySpec({ key: "Shift" }), {}), false, "a modifier on its own is nothing");
});

test("nextRegion walks the available regions in order, wraps around and skips the unavailable ones", () => {
  assert.deepEqual([...REGION_ORDER], ["bar", "side", "strip", "grid", "dock", "detail"], "the dock comes before the detail");
  const all = [...REGION_ORDER];
  assert.equal(nextRegion(REGION_ORDER, all, "bar", 1), "side");
  assert.equal(nextRegion(REGION_ORDER, all, "dock", 1), "detail");
  assert.equal(nextRegion(REGION_ORDER, all, "detail", 1), "bar", "wraps to the start");
  assert.equal(nextRegion(REGION_ORDER, all, "bar", -1), "detail", "wraps to the end");
  assert.equal(nextRegion(REGION_ORDER, all, "grid", -1), "strip");
  // the dock and the detail are not there
  const noPanels = ["bar", "side", "strip", "grid"];
  assert.equal(nextRegion(REGION_ORDER, noPanels, "grid", 1), "bar");
  assert.equal(nextRegion(REGION_ORDER, noPanels, "bar", -1), "grid");
  // only the current one is available: it stays
  assert.equal(nextRegion(REGION_ORDER, ["grid"], "grid", 1), "grid");
  assert.equal(nextRegion(REGION_ORDER, ["grid"], "grid", -1), "grid");
  // nothing available
  assert.equal(nextRegion(REGION_ORDER, [], "grid", 1), null);
  assert.equal(nextRegion([], all, "grid", 1), null);
  assert.equal(nextRegion(null, null, "grid", 1), null);
});

test("nextRegion without a current region starts at the first (forward) or the last (backward) available one", () => {
  const some = ["side", "grid", "dock"];
  for (const current of [null, undefined, "nowhere"]) {
    assert.equal(nextRegion(REGION_ORDER, some, current, 1), "side");
    assert.equal(nextRegion(REGION_ORDER, some, current, -1), "dock");
  }
  // a current region that is no longer available still gives the right neighbour
  assert.equal(nextRegion(REGION_ORDER, ["bar", "grid"], "strip", 1), "grid");
  assert.equal(nextRegion(REGION_ORDER, ["bar", "grid"], "strip", -1), "bar");
  assert.equal(nextRegion(REGION_ORDER, some, "grid", NaN), "dock", "an unreadable direction means forward");
});

test("navAnchor: the selected record, or one id outside the first / last visible record", () => {
  const selected = { id: 40, ts: 900 };
  const rows = [{ id: 11, ts: 100 }, { id: 12, ts: 100 }, { id: 13, ts: 101 }];
  assert.equal(navAnchor(selected, rows, "fwd"), selected, "a selection always wins");
  assert.equal(navAnchor(selected, rows, "back"), selected);
  // n: the search starts just before the first visible record, so that record can be the answer
  assert.deepEqual(navAnchor(null, rows, "fwd"), { id: 10, ts: 100 });
  // N: just after the last visible record
  assert.deepEqual(navAnchor(null, rows, "back"), { id: 14, ts: 101 });
  assert.deepEqual(navAnchor(undefined, rows, undefined), { id: 10, ts: 100 }, "no direction means forward");
  // the input is not touched
  assert.deepEqual(rows[0], { id: 11, ts: 100 });
  // nothing to start from: the whole view
  assert.equal(navAnchor(null, [], "fwd"), null);
  assert.equal(navAnchor(null, null, "back"), null);
  assert.equal(navAnchor(null, [null, { id: NaN }, { ts: 5 }], "fwd"), null, "rows without a usable id are ignored");
  assert.deepEqual(navAnchor(null, [null, { id: 7, ts: null }], "fwd"), { id: 6, ts: null });
});

test("navPlan: n/N with nothing selected start at the first / last visible record instead of asking for a selection", () => {
  const rows = [{ id: 11, ts: 100 }, { id: 12, ts: 100 }];
  const filter = { sev: { le: 4 } };
  const fwd = navPlan("error", "fwd", null, rows, filter);
  assert.equal(fwd.problem, undefined);
  assert.deepEqual(fwd.request, { filter: { and: [filter, NAV_ERROR_FILTER] }, cursor: "100:10", dir: "fwd", limit: 1 });
  const back = navPlan("error", "back", null, rows, filter);
  assert.equal(back.request.cursor, "100:13");
  assert.equal(back.request.dir, "back");
  // nothing rendered: the search covers the whole view
  assert.equal(navPlan("error", "fwd", null, [], filter).request.cursor, null);
  // a selection is the start, visible rows or not
  assert.equal(navPlan("error", "fwd", { id: 40, ts: 900 }, rows, filter).request.cursor, "900:40");
  assert.equal(navPlan("error", "back", { id: 40, ts: null }, [], null).request.cursor, "null:40");
});

test("navPlan: t/T still need a selected record that has a message type", () => {
  const rows = [{ id: 11, ts: 100 }];
  assert.deepEqual(navPlan("template", "fwd", null, rows, null), { problem: "navNoSelection" });
  assert.deepEqual(navPlan("template", "back", { id: 3, ts: 1, tpl_id: 0 }, rows, null), { problem: "navNoTemplate" });
  assert.deepEqual(navPlan("template", "back", { id: 3, ts: 1 }, rows, null), { problem: "navNoTemplate" });
  const plan = navPlan("template", "fwd", { id: 3, ts: 1, tpl_id: 9 }, rows, { sev: { le: 2 } });
  assert.deepEqual(plan.request, { filter: { and: [{ sev: { le: 2 } }, { tpl: [9] }] }, cursor: "1:3", dir: "fwd", limit: 1 });
  assert.ok(S_HAS("navNoSelection") && S_HAS("navNoTemplate"));
});

test("composition, AltGr and repeat rules", () => {
  assert.equal(isComposingEvent({ isComposing: true }), true);
  assert.equal(isComposingEvent({ keyCode: 229 }), true);
  assert.equal(isComposingEvent({ keyCode: 78 }), false);
  assert.equal(isComposingEvent(null), false);
  assert.equal(hasAltGraph({ getModifierState: (k) => k === "AltGraph" }), true);
  assert.equal(hasAltGraph({ getModifierState: () => false }), false);
  assert.equal(hasAltGraph({ getModifierState() { throw new Error("x"); } }), false);
  assert.equal(hasAltGraph({}), false);
  assert.equal(shouldDispatch("[", { altGraph: true }), false);
  assert.equal(shouldDispatch("F6", { altGraph: true }), false);
  assert.equal(shouldDispatch("n", { composing: true }), false);
  assert.equal(shouldDispatch("F6", { composing: true }), false);
  assert.deepEqual([...REPEATABLE_ACTIONS].sort(), [
    "navigate.nextError", "navigate.nextHighlight", "navigate.nextType", "navigate.prevError", "navigate.prevHighlight", "navigate.prevType"]);
  assert.equal(allowsRepeat("navigate.nextError"), true);
  for (const id of ["record.bookmark", "record.compare", "toggle.bookmarks", "navigate.nextRegion", "help.shortcuts", "views.back", "navigate.focusSearch"]) {
    assert.equal(allowsRepeat(id), false, id);
  }
});
