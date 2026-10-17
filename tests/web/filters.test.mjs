import test from "node:test";
import assert from "node:assert/strict";
import {
  NAV_ERROR_FILTER, TPL_LEAF, andFilters, buildFilter, composeFilter, ignoresShortcut, isInMenu, isTypingTarget, navRequest,
  parseTplList, stateFromForm, timeSpan, withTemplate, withTsRange,
} from "../../src/dmslog/web/js/filters.js";

test("no criteria gives no filter", () => {
  assert.equal(buildFilter({ sourceIds: [], sevMax: null, levelMax: null, from: null, to: null, text: "  " }), null);
});

test("all criteria build an AND tree in the API shape", () => {
  const tree = buildFilter({ sourceIds: [3, 4], sevMax: 2, levelMax: 3, from: 10, to: 20, text: " timeout " });
  assert.deepEqual(tree, {
    and: [
      { source: [3, 4] },
      { sev: { le: 2 } },
      { level: { le: 3 } },
      { ts: { ge: 10, le: 20 } },
      { text: { fts: "timeout" } },
    ],
  });
});

test("one-sided time windows and zero values are kept", () => {
  assert.deepEqual(buildFilter({ sourceIds: [], from: 0, to: null }), { and: [{ ts: { ge: 0 } }] });
  assert.deepEqual(buildFilter({ sourceIds: [], to: 5 }), { and: [{ ts: { le: 5 } }] });
});

test("search text is passed as data, never interpreted", () => {
  const nasty = "x\"; DROP TABLE entry; -- <script>";
  assert.deepEqual(buildFilter({ sourceIds: [], text: nasty }), { and: [{ text: { fts: nasty } }] });
});

test("stateFromForm parses numbers and log times, reporting bad fields", () => {
  const ok = stateFromForm({ sevMax: "2", levelMax: "", from: "2026-10-08 12:00:00", to: "", text: "a" });
  assert.deepEqual(ok.errors, []);
  assert.equal(ok.state.sevMax, 2);
  assert.equal(ok.state.levelMax, null);
  assert.equal(ok.state.from, Date.UTC(2026, 9, 8, 12));
  const bad = stateFromForm({ from: "yesterday", to: "2026-10-08" });
  assert.deepEqual(bad.errors, ["from"]);
});

test("timeSpan unions the selected sources and clips to the window", () => {
  const sources = [
    { id: 1, ts_min: 100, ts_max: 200 },
    { id: 2, ts_min: 150, ts_max: 400 },
    { id: 3, ts_min: null, ts_max: null },
  ];
  assert.deepEqual(timeSpan(sources, [], null, null), { min: 100, max: 400 });
  assert.deepEqual(timeSpan(sources, [1], null, null), { min: 100, max: 200 });
  assert.deepEqual(timeSpan(sources, [], 120, 300), { min: 120, max: 300 });
  assert.equal(timeSpan(sources, [3], null, null), null);
  assert.equal(timeSpan(sources, [1], 500, null), null);
});

test("withTsRange adds, replaces and removes the top-level ts condition", () => {
  const base = { and: [{ sev: { le: 2 } }] };
  const added = withTsRange(base, { from: 10, to: 20 });
  assert.deepEqual(added, { and: [{ sev: { le: 2 } }, { ts: { ge: 10, le: 20 } }] });
  assert.deepEqual(withTsRange(added, { from: 30, to: 40 }), { and: [{ sev: { le: 2 } }, { ts: { ge: 30, le: 40 } }] });
  assert.deepEqual(withTsRange(added, null), { and: [{ sev: { le: 2 } }] });
  assert.equal(withTsRange({ ts: { ge: 1 } }, null), null);
  assert.deepEqual(withTsRange(null, { from: 5, to: null }), { and: [{ ts: { ge: 5 } }] });
  assert.deepEqual(base, { and: [{ sev: { le: 2 } }] }); // input untouched
});

test("withTemplate merges into the existing tpl condition without duplicates", () => {
  const one = withTemplate(null, 7);
  assert.deepEqual(one, { and: [{ tpl: [7] }] });
  assert.deepEqual(withTemplate(one, 9), { and: [{ tpl: [7, 9] }] });
  assert.deepEqual(withTemplate(one, 7), { and: [{ tpl: [7] }] });
});

test("composeFilter ANDs simple state, the tree filter and the quick toggles", () => {
  const state = { sourceIds: [1], sevMax: null, levelMax: null, from: null, to: null, text: "" };
  assert.equal(composeFilter({ ...state, sourceIds: [] }, null, { hideNoise: false, unclassified: false }), null);
  assert.deepEqual(
    composeFilter(state, { and: [{ tpl: [3] }] }, { hideNoise: true, unclassified: true }),
    { and: [{ source: [1] }, { tpl: [3] }, { sev: { le: 4 } }, { tpl: [0] }] },
  );
  assert.deepEqual(andFilters(null, { sev: { le: 1 } }), { and: [{ sev: { le: 1 } }] });
});

test("navRequest builds a one-row keyset query from the selected row", () => {
  const row = { id: 42, ts: 1000 };
  assert.deepEqual(navRequest({ and: [{ source: [1] }] }, NAV_ERROR_FILTER, row, "fwd"), {
    filter: { and: [{ source: [1] }, { sev: { le: 2 } }] }, cursor: "1000:42", dir: "fwd", limit: 1,
  });
  assert.equal(navRequest(null, { tpl: [5] }, { id: 3, ts: null }, "back").cursor, "null:3");
  assert.deepEqual(navRequest(null, { tpl: [5] }, row, "back").filter, { and: [{ tpl: [5] }] });
});

test("isTypingTarget: fields, selects and editable nodes", () => {
  assert.equal(isTypingTarget({ tagName: "input" }), true);
  assert.equal(isTypingTarget({ tagName: "DIV", isContentEditable: true }), true);
  assert.equal(isTypingTarget({ tagName: "DIV" }), false);
});

test("template leaf validates and round-trips ids and codes", () => {
  assert.equal(TPL_LEAF.validate({ ids: [] }), "emptyList");
  assert.equal(TPL_LEAF.validate({ ids: [-1] }), "badValue");
  assert.equal(TPL_LEAF.validate({ ids: [0, "ABC_1"] }), null);
  assert.deepEqual(TPL_LEAF.toJson({ ids: [1, "X"] }), { tpl: [1, "X"] });
  assert.deepEqual(TPL_LEAF.fromJson({ tpl: [4] }), { ids: [4] });
  assert.equal(TPL_LEAF.fromJson({ tpl: [{}] }), null);
  assert.deepEqual(parseTplList("12, ABC_1,,"), [12, "ABC_1"]);
  assert.deepEqual(parseTplList("a b"), [-1]);
});

// A tiny node with a role and a parent, like the elements of ui-menu.js.
const node = (role, parent = null, tagName = "DIV") => ({
  tagName, parent, getAttribute: (k) => (k === "role" && role ? role : null),
});

test("keys typed inside an open menu are not global shortcuts (c and B stay with the menu)", () => {
  const menu = node("menu");
  const item = node("menuitem", menu);
  assert.equal(isInMenu(item), true);
  assert.equal(isInMenu(menu), true);
  assert.equal(isInMenu(node(null, node("banner"))), false);
  assert.equal(isInMenu(null), false);
  assert.equal(ignoresShortcut({ key: "c", target: item }), true);
  assert.equal(ignoresShortcut({ key: "B", target: item }), true);
  assert.equal(ignoresShortcut({ key: "c", target: node(null) }), false);
});

test("an event somebody already handled, or typed in a field, is not a global shortcut", () => {
  assert.equal(ignoresShortcut({ key: "c", target: node(null), defaultPrevented: true }), true);
  assert.equal(ignoresShortcut({ key: "c", target: node(null, null, "INPUT") }), true);
});
