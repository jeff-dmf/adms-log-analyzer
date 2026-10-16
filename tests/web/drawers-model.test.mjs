// UI-17: the model of the rail and the drawers (no DOM): layout by width, the user's choices, one open drawer, the count
// on the rail and the remembered choices. Plus the Message width the acceptance asks for.
import test from "node:test";
import assert from "node:assert/strict";

import {
  DrawerState, STORAGE_KEY, effectiveLayout, layoutModeFor, loadPrefs, normalizePrefs, railCount, savePrefs,
} from "../../src/dmslog/web/js/drawers-model.js";
import { LAYOUT_BY_MODE, layoutWidths, messageChars } from "../../src/dmslog/web/js/column-resize-model.js";

const KEYS = ["ts", "pid", "tid", "level", "source", "msg"]; // the columns of the default view

test("the layout mode follows the breakpoints and falls back to wide for nothing measurable", () => {
  assert.equal(layoutModeFor(1920), "wide");
  assert.equal(layoutModeFor(1600), "wide");
  assert.equal(layoutModeFor(1599), "rail");
  assert.equal(layoutModeFor(1536), "rail"); // a very common laptop resolution
  assert.equal(layoutModeFor(1280), "rail");
  assert.equal(layoutModeFor(1279), "narrow");
  assert.equal(layoutModeFor(800), "narrow");
  for (const bad of [0, -5, NaN, Infinity, undefined, null, "abc"]) {
    assert.equal(layoutModeFor(bad), "wide", String(bad));
  }
});

test("rail below 1600, detail drawer below 1280, and the user's choices only act in the layouts that allow them", () => {
  assert.deepEqual(effectiveLayout("wide", null), { mode: "wide", sideRail: false, detailDrawer: false, canCollapseSide: true, canCollapseDetail: true });
  const rail = effectiveLayout("rail", null);
  assert.equal(rail.sideRail, true);
  assert.equal(rail.detailDrawer, false);
  assert.equal(rail.canCollapseSide, false);
  const narrow = effectiveLayout("narrow", null);
  assert.equal(narrow.sideRail && narrow.detailDrawer, true);
  assert.equal(narrow.canCollapseDetail, false);
  const chosen = effectiveLayout("wide", { sideCollapsed: true, detailCollapsed: true });
  assert.equal(chosen.sideRail && chosen.detailDrawer, true);
  assert.equal(effectiveLayout("rail", { detailCollapsed: true }).detailDrawer, true);
});

test("one drawer at a time, only where the panel is a drawer; a layout change closes the one that no longer is", () => {
  const state = new DrawerState();
  const wide = effectiveLayout("wide", null);
  const narrow = effectiveLayout("narrow", null);
  assert.equal(state.show("side", wide), false);
  assert.equal(state.open, null);
  assert.equal(state.show("side", narrow), true);
  assert.equal(state.show("detail", narrow), true);
  assert.equal(state.open, "detail"); // the other one closed
  assert.equal(state.show("nope", narrow), false);
  assert.equal(state.sync(narrow), null);
  assert.equal(state.sync(effectiveLayout("rail", null)), "detail");
  assert.equal(state.open, null);
  assert.equal(state.toggle("side", narrow), true);
  assert.equal(state.toggle("side", narrow), false);
  state.show("side", narrow);
  state.hide("detail");
  assert.equal(state.open, "side", "hiding another drawer leaves this one");
  state.hide();
  assert.equal(state.open, null);
});

test("the rail count is selected/total, clamped, and empty when the total is not a number", () => {
  assert.equal(railCount(3, 14), "3/14");
  assert.equal(railCount(0, 0), "0/0");
  assert.equal(railCount(20, 14), "14/14");
  assert.equal(railCount(-4, 14), "0/14");
  assert.equal(railCount(NaN, 14), "0/14");
  assert.equal(railCount(2, NaN), "");
  assert.equal(railCount(2, -1), "");
  assert.equal(railCount(2, undefined), "");
});

test("remembered choices survive bad storage: throwing, missing, corrupt or the wrong type", () => {
  const mem = new Map();
  const storage = { getItem: (k) => mem.get(k) ?? null, setItem: (k, v) => mem.set(k, v) };
  assert.equal(savePrefs(storage, { sideCollapsed: true, detailCollapsed: false }), true);
  assert.deepEqual(loadPrefs(storage), { sideCollapsed: true, detailCollapsed: false });
  assert.match(mem.get(STORAGE_KEY), /sideCollapsed/);
  const boom = { getItem() { throw new Error("blocked"); }, setItem() { throw new Error("quota"); } };
  assert.deepEqual(loadPrefs(boom), { sideCollapsed: false, detailCollapsed: false });
  assert.equal(savePrefs(boom, {}), false);
  assert.equal(savePrefs(null, {}), false);
  assert.deepEqual(loadPrefs(null), { sideCollapsed: false, detailCollapsed: false });
  mem.set(STORAGE_KEY, "{not json");
  assert.deepEqual(loadPrefs(storage), { sideCollapsed: false, detailCollapsed: false });
  mem.set(STORAGE_KEY, JSON.stringify({ sideCollapsed: "yes", detailCollapsed: 1 }));
  assert.deepEqual(loadPrefs(storage), { sideCollapsed: false, detailCollapsed: false });
  assert.deepEqual(normalizePrefs(7), { sideCollapsed: false, detailCollapsed: false });
});

test("acceptance: Message keeps at least 50 characters from 1280 to 1600 and beyond (1536 included), with 1 or 2 collections, one or several days", () => {
  const withCollection = [...KEYS.slice(0, -1), "collection", "msg"]; // two collections: the Collection column shows
  for (const width of [1280, 1300, 1366, 1440, 1500, 1536, 1599, 1600, 1700]) {
    const layout = layoutWidths(layoutModeFor(width));
    for (const [name, keys] of [["1 collection", KEYS], ["2 collections", withCollection]]) {
      for (const multiDay of [false, true]) {
        for (const charPx of [6.6, 7.2, 7.25]) { // Consolas (Windows) and the Linux monospace fonts (7.22)
          const chars = messageChars(width, keys, { charPx, multiDay, layout });
          assert.ok(chars >= 50, `${width}px, ${name}, multiDay=${multiDay}, ${charPx}px: ${chars} characters`);
        }
      }
    }
  }
  assert.ok(messageChars(1366, KEYS, { charPx: 7.2 }) < 50, "the old fixed layout was the problem");
});

test("acceptance: the wide layout from 1600 px keeps 60 characters in every combination", () => {
  const withCollection = [...KEYS.slice(0, -1), "collection", "msg"];
  for (const keys of [KEYS, withCollection]) {
    for (const multiDay of [false, true]) {
      assert.ok(messageChars(1600, keys, { multiDay, layout: layoutWidths("wide") }) >= 60);
    }
  }
});

test("layout widths: collapsing frees the columns, and nonsense falls back instead of poisoning the sum", () => {
  assert.deepEqual(layoutWidths("wide"), LAYOUT_BY_MODE.wide);
  assert.deepEqual(layoutWidths("wide", { sideCollapsed: true, detailCollapsed: true }), { side: 44, detail: 0 });
  assert.deepEqual(layoutWidths("narrow", { sideCollapsed: true }), { side: 44, detail: 0 });
  assert.deepEqual(layoutWidths("bogus"), LAYOUT_BY_MODE.wide);
  const same = messageChars(1600, KEYS, { charPx: 7.2 });
  assert.equal(messageChars(1600, KEYS, { charPx: 7.2, layout: { side: NaN, detail: -1 } }), same);
  assert.equal(messageChars(1600, KEYS, { charPx: 7.2, layout: null }), same);
  assert.equal(messageChars(0, KEYS, { layout: layoutWidths("rail") }), 0);
  assert.equal(messageChars(NaN, KEYS, { layout: layoutWidths("rail") }), 0);
  assert.equal(messageChars(300, KEYS, { layout: layoutWidths("narrow") }), 0);
});
