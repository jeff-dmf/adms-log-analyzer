// Filter chips model (UI-18): live state -> chips, remove a chip -> state without the condition, fit and "Filtered".
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";

import { chipLabel, chipsFromState, createChipRemoval, fitChips, isFiltered, removeChip } from "../../src/dmslog/web/js/filter-chips-model.js";
import { emptyState } from "../../src/dmslog/web/js/views-stack-model.js";
import { composeFilter } from "../../src/dmslog/web/js/filters.js";
import { S as REAL } from "../../src/dmslog/web/js/strings.js";

const S = {
  severityNames: ["", "Critical", "Error", "Warning", "Info", "Noise"],
  levelNames: ["", "Fatal", "Error", "Warning", "Info", "Config", "Fine", "Finer", "Finest"],
  chipMore: "+{n} more", chipMoreValues: " +{n}",
  chipSourceOne: "Source {name}", chipSourceOther: "{count} sources",
  chipText: 'text "{text}"', chipTextSubstr: 'contains "{text}"', chipTextRegex: "matches /{text}/",
  chipSev: "Severity at least {name}", chipLevel: "Level at least {name}",
  chipTimeBoth: "{from} to {to}", chipTimeFrom: "from {from}", chipTimeUntil: "until {to}",
  chipNoise: "Hide noise", chipUnclassified: "Unclassified only", chipNot: "not {label}",
  chipTreeOne: "Filter tree (1 condition)", chipTreeOther: "Filter tree ({n} conditions)",
  chipSensitiveOn: "Sensitive records", chipSensitiveOff: "Normal records",
  chipKeyEq: "{type} = {value}", chipKeyIn: "{type} in {value}", chipKeyAny: "has {type}", chipField: "{name} = {value}",
  chipLeaf: {
    source: "Source {value}", collection: "Collection {value}", tpl: "Type {value}", tid: "Thread {value}", pid: "Process {value}",
    event: "Event {value}", stream: "Stream {value}", family: "Family {value}", event_status: "Event status {value}",
    event_name: "Event name {value}", event_kind: "Event kind {value}", flags: "Flag {value}", payload: "Payload {value}",
  },
};
const lookups = {
  sourceName: (id) => "src" + id,
  collectionTag: (g) => "C" + g,
  typeLabel: (id) => "Type" + id,
  formatTs: (ms) => "t" + ms,
};
const none = { sourceIds: [], collectionIds: [] };
const live = (patch) => ({ ...emptyState(), ...patch });
const ids = (chips) => chips.map((c) => c.id);

test("the strings.js of the app has every key the model uses", () => {
  for (const key of Object.keys(S)) {
    assert.ok(key in REAL, "missing in strings.js: " + key);
  }
  for (const key of Object.keys(S.chipLeaf)) {
    assert.ok(key in REAL.chipLeaf, "missing chipLeaf." + key);
  }
});

test("search, severity, level, interval, sources, collections, unclassified and hide noise become chips", () => {
  const state = live({
    searchText: "Validation",
    form: { sev: "3", level: "2", from: "a", to: "b" },
    formState: { sevMax: 3, levelMax: 2, from: 1000, to: 2000 },
    toggles: { hideNoise: true, unclassified: true },
  });
  const chips = chipsFromState(state, { sourceIds: [1, 2, 3], collectionIds: [2] }, lookups, S);
  assert.deepEqual(chips.map((c) => [c.id, c.label]), [
    ["sources", "3 sources"],
    ["collections", "C2"],
    ["text", 'text "Validation"'],
    ["sev", "Severity at least Warning"],
    ["level", "Level at least Error"],
    ["time", "t1000 to t2000"],
    ["noise", "Hide noise"],
    ["unclassified", "Unclassified only"],
  ]);
  assert.ok(chips.every((c) => c.removable));
  const one = chipsFromState(live({}), { sourceIds: [7], collectionIds: [] }, lookups, S);
  assert.equal(one[0].label, "Source src7");
});

test("an empty view has no chips; a blank search and null measures make none", () => {
  assert.deepEqual(chipsFromState(live({}), none, lookups, S), []);
  const blank = live({ searchText: "   ", formState: { sevMax: null, levelMax: null, from: null, to: NaN } });
  assert.deepEqual(chipsFromState(blank, none, lookups, S), []);
  assert.equal(isFiltered(blank, none, null), false);
});

test("zero is a measure: from = 0 and sev 0 are chips, not 'nothing'", () => {
  const state = live({ formState: { sevMax: null, levelMax: null, from: 0, to: null } });
  assert.deepEqual(chipsFromState(state, none, lookups, S).map((c) => c.label), ["from t0"]);
  assert.equal(isFiltered(state, none, null), true);
  const until = live({ formState: { sevMax: null, levelMax: null, from: null, to: 5 } });
  assert.deepEqual(chipsFromState(until, none, lookups, S).map((c) => c.label), ["until t5"]);
});

test("simple conditions of the filter tree are chips; the rest is ONE tree chip without x", () => {
  const userFilter = {
    and: [
      { tid: [12, 13] },
      { not: { pid: [4] } },
      { tpl: [5] },
      { ts: { ge: 10, le: 20 } },
      { or: [{ tid: [1] }, { tid: [2] }] },
      { not: { and: [{ tid: [1] }, { pid: [2] }] } },
      { weird_future_leaf: 1 },
    ],
  };
  const chips = chipsFromState(live({ userFilter }), none, lookups, S);
  assert.deepEqual(chips.map((c) => [c.id, c.label, c.removable]), [
    ["node:0", "Thread 12, 13", true],
    ["node:1", "not Process 4", true],
    ["node:2", "Type Type5", true],
    ["node:3", "t10 to t20", true],
    ["tree", "Filter tree (5 conditions)", false],
  ]);
  const single = chipsFromState(live({ userFilter: { or: [{ tid: [1] }] } }), none, lookups, S);
  assert.equal(single[0].label, "Filter tree (1 condition)");
});

test("a single leaf userFilter (not wrapped in and) is still a chip", () => {
  const chips = chipsFromState(live({ userFilter: { tid: [9] } }), none, lookups, S);
  assert.deepEqual(ids(chips), ["node:0"]);
});

test("chipLabel: leaves, drill-down sources and collections, text modes, keys and unknown shapes", () => {
  assert.equal(chipLabel({ source: [3] }, lookups, S), "Source src3");
  assert.equal(chipLabel({ collection: 2 }, lookups, S), "Collection C2");
  assert.equal(chipLabel({ text: { fts: "boom" } }, lookups, S), 'text "boom"');
  assert.equal(chipLabel({ text: { substr: "x" } }, lookups, S), 'contains "x"');
  assert.equal(chipLabel({ key: { type: "gid", eq: "A1" } }, lookups, S), "gid = A1");
  assert.equal(chipLabel({ key: { type: "gid", in: ["a", "b", "c", "d", "e"] } }, lookups, S), "gid in a, b, c +2");
  assert.equal(chipLabel({ sev: { le: 2 } }, lookups, S), "Severity at least Error");
  assert.equal(chipLabel({ sev: { ge: 2 } }, lookups, S), null);
  assert.equal(chipLabel({ sev: 2 }, lookups, S), null);
  assert.equal(chipLabel({ field: { name: "a", eq: null } }, lookups, S), "a = null");
  assert.equal(chipLabel({ sensitive: true }, lookups, S), "Sensitive records");
  assert.equal(chipLabel({ ts: { ge: NaN } }, lookups, S), null);
  assert.equal(chipLabel({ ts: { eq: 1 } }, lookups, S), null);
  assert.equal(chipLabel({ nothing: 1 }, lookups, S), null);
  assert.equal(chipLabel({ tid: [] }, lookups, S), null);
  assert.equal(chipLabel({ tid: [1], pid: [2] }, lookups, S), null);
  assert.equal(chipLabel(null, lookups, S), null);
});

test("a long label is cut at 32 characters but the title keeps the whole text", () => {
  const text = "x".repeat(80);
  const [chip] = chipsFromState(live({ searchText: text }), none, lookups, S);
  assert.equal(chip.label.length, 32);
  assert.ok(chip.label.endsWith("…"));
  assert.equal(chip.title, 'text "' + text + '"');
});

test("removing a chip removes the condition from the raw form AND the applied value", () => {
  const state = live({
    searchText: "abc",
    form: { sev: "3", level: "2", from: "x", to: "y" },
    formState: { sevMax: 3, levelMax: 2, from: 1, to: 2 },
    toggles: { hideNoise: true, unclassified: true },
  });
  const sel = { sourceIds: [1], collectionIds: [4] };
  const without = (id) => removeChip(state, sel, id);
  assert.equal(without("text").live.searchText, "");
  assert.deepEqual([without("sev").live.formState.sevMax, without("sev").live.form.sev], [null, ""]);
  assert.deepEqual([without("level").live.formState.levelMax, without("level").live.form.level], [null, ""]);
  const t = without("time").live;
  assert.deepEqual([t.formState.from, t.formState.to, t.form.from, t.form.to], [null, null, "", ""]);
  assert.equal(without("noise").live.toggles.hideNoise, false);
  assert.equal(without("noise").live.toggles.unclassified, true);
  assert.equal(without("unclassified").live.toggles.unclassified, false);
  assert.deepEqual(without("sources").selection, { sourceIds: [], collectionIds: [4] });
  assert.deepEqual(without("collections").selection, { sourceIds: [1], collectionIds: [] });
  assert.equal(without("sev").live.searchText, "abc"); // the other conditions stay
});

test("removing the last chip of each kind leaves no chip, and the composed filter loses the condition", () => {
  const state = live({
    searchText: "abc",
    form: { sev: "3", level: "", from: "", to: "" },
    formState: { sevMax: 3, levelMax: null, from: null, to: null },
    toggles: { hideNoise: true, unclassified: false },
    userFilter: { and: [{ tid: [1] }, { tid: [2] }] },
  });
  let current = { live: state, selection: { sourceIds: [9], collectionIds: [] } };
  for (const id of ["sources", "text", "sev", "noise", "node:0", "node:0"]) {
    current = removeChip(current.live, current.selection, id);
  }
  assert.deepEqual(chipsFromState(current.live, current.selection, lookups, S), []);
  const composed = composeFilter({ sourceIds: current.selection.sourceIds, text: current.live.searchText, ...current.live.formState }, current.live.userFilter, current.live.toggles);
  assert.equal(composed, null);
});

test("removing a tree node takes only that top-level node; others keep their place", () => {
  const userFilter = { and: [{ tid: [1] }, { or: [{ pid: [1] }, { pid: [2] }] }, { tid: [3] }] };
  const state = live({ userFilter });
  const out = removeChip(state, none, "node:2").live.userFilter;
  assert.deepEqual(out, { and: [{ tid: [1] }, { or: [{ pid: [1] }, { pid: [2] }] }] });
  const chips = chipsFromState(live({ userFilter: out }), none, lookups, S);
  assert.deepEqual(ids(chips), ["node:0", "tree"]);
  assert.equal(removeChip(live({ userFilter: { tid: [1] } }), none, "node:0").live.userFilter, null);
});

test("removeChip never mutates its input; the tree chip, unknown ids and bad indexes change nothing", () => {
  const userFilter = { and: [{ tid: [1] }, { or: [{ tid: [1] }, { tid: [2] }] }] };
  const state = live({
    searchText: "a",
    form: { sev: "2", level: "3", from: "x", to: "y" },
    formState: { sevMax: 2, levelMax: 3, from: 1, to: 2 },
    toggles: { hideNoise: true, unclassified: true },
    userFilter,
  });
  const sel = { sourceIds: [1], collectionIds: [2] };
  const snapshot = JSON.stringify([state, sel]);
  for (const id of ["text", "sev", "level", "time", "noise", "unclassified", "sources", "collections", "node:0"]) {
    removeChip(state, sel, id);
  }
  assert.equal(JSON.stringify([state, sel]), snapshot);
  for (const id of ["tree", "nope", "node:99", "node:x"]) {
    const result = removeChip(state, sel, id);
    assert.deepEqual(result.live, state);
    assert.deepEqual(result.selection, sel);
  }
});

test("isFiltered: own conditions, side selection or ancestors' filter; ancestors alone count", () => {
  assert.equal(isFiltered(live({}), none, null), false);
  assert.equal(isFiltered(live({}), none, {}), false);
  assert.equal(isFiltered(live({}), none, { and: [] }), false);
  assert.equal(isFiltered(live({}), none, { and: [{ tid: [1] }] }), true); // "View 2" with an empty toolbar
  assert.equal(isFiltered(live({ searchText: "x" }), none, null), true);
  assert.equal(isFiltered(live({}), { sourceIds: [1], collectionIds: [] }, null), true);
  assert.equal(isFiltered(live({}), { sourceIds: [], collectionIds: [1] }, null), true);
  assert.equal(isFiltered(live({ userFilter: { tid: [1] } }), none, null), true);
  assert.equal(isFiltered(live({ toggles: { hideNoise: false, unclassified: true } }), none, null), true);
});

test("isFiltered agrees with the chips for a spread of states", () => {
  const samples = [
    live({}), live({ searchText: "a" }), live({ userFilter: { or: [{ tid: [1] }] } }),
    live({ formState: { sevMax: 0, levelMax: null, from: null, to: null } }),
    live({ formState: { sevMax: null, levelMax: null, from: NaN, to: null } }),
  ];
  for (const state of samples) {
    assert.equal(isFiltered(state, none, null), chipsFromState(state, none, lookups, S).length > 0);
  }
});

test("fitChips: how many chips fit before '+N more'", () => {
  assert.equal(fitChips([100, 100, 100], 1000, 60), 3);
  assert.equal(fitChips([100, 100, 100], 300, 60), 3); // exactly full: all fit, no "+N more" needed
  assert.equal(fitChips([100, 100, 100], 250, 60), 1); // 100 + 60 = 160; 200 + 60 > 250
  assert.equal(fitChips([100, 100, 100], 160, 60), 1);
  assert.equal(fitChips([100, 100, 100], 159, 60), 0);
  assert.equal(fitChips([], 300, 60), 0);
});

test("fitChips never hides what it could not measure (0, null, NaN)", () => {
  assert.equal(fitChips([100, 100], 0, 60), 2);
  assert.equal(fitChips([100, 100], NaN, 60), 2);
  assert.equal(fitChips([100, 100], null, 60), 2);
  assert.equal(fitChips([0, 0], 50, 60), 2);
  assert.equal(fitChips([NaN, NaN], 50, 60), 2);
  assert.equal(fitChips([100, 100, 100], 250, NaN), 2);
  assert.equal(fitChips(null, 100, 10), 0);
});

test("filter-chips-model.js has no DOM", () => {
  const source = fs.readFileSync(new URL("../../src/dmslog/web/js/filter-chips-model.js", import.meta.url), "utf8");
  assert.ok(!/document|window|innerHTML/.test(source));
});

test("fitChips: a mixed list with one unmeasurable width (0, null, NaN) shows everything", () => {
  assert.equal(fitChips([100, NaN, 100], 100, 60), 3);
  assert.equal(fitChips([100, 0, 100], 100, 60), 3);
  assert.equal(fitChips([100, null, 100], 100, 60), 3);
  assert.equal(fitChips([100, undefined, 100], 100, 60), 3);
  assert.equal(fitChips([100, -5, 100], 100, 60), 3);
  assert.equal(fitChips([100, 100, 100], 100, 60), 0); // all measured: the rule applies
});

// The same wiring as main.js: the side panels' own callbacks apply the view unless a removal is under way.
function removalHarness({ sources = [1, 2], groups = [3] } = {}) {
  const calls = { apply: 0, restore: 0 };
  let live = live_({ searchText: "a" });
  let sourceIds = sources.slice();
  let groupIds = groups.slice();
  let busy = false;
  const guarded = () => { if (!busy) { calls.apply += 1; } };
  const removal = createChipRemoval({
    getLive: () => live,
    getSelection: () => ({ sourceIds: sourceIds.slice(), collectionIds: groupIds.slice() }),
    restoreLive: (next) => { live = next; calls.restore += 1; },
    clearSources: () => { sourceIds = []; guarded(); }, // SourcesPanel.clearSelection -> onSelection -> applyView
    clearCollections: () => { groupIds = []; guarded(); },
    setBusy: (value) => { busy = value; },
    applyView: () => { calls.apply += 1; return Promise.resolve(); },
  });
  return { removal, calls, state: () => ({ live, sourceIds, groupIds }) };
}
function live_(patch) { return { ...emptyState(), ...patch }; }

test("removing the sources chip restores the state, clears the selection and applies the view ONCE", async () => {
  const h = removalHarness();
  await h.removal.remove("sources");
  assert.equal(h.calls.apply, 1);
  assert.equal(h.calls.restore, 1);
  assert.deepEqual(h.state().sourceIds, []);
  assert.deepEqual(h.state().groupIds, [3]); // the other selection is left alone
});

test("removing the collections chip applies the view once too", async () => {
  const h = removalHarness();
  await h.removal.remove("collections");
  assert.equal(h.calls.apply, 1);
  assert.deepEqual(h.state().groupIds, []);
  assert.deepEqual(h.state().sourceIds, [1, 2]);
});

test("a chip that touches no selection applies once and clears no panel; the busy flag is released", async () => {
  const h = removalHarness();
  await h.removal.remove("text");
  assert.equal(h.calls.apply, 1);
  assert.equal(h.state().live.searchText, "");
  assert.deepEqual(h.state().sourceIds, [1, 2]);
  assert.equal(h.removal.isBusy(), false);
  await h.removal.remove("sources");
  assert.equal(h.removal.isBusy(), false);
});

test("an error while restoring still releases the busy flag", async () => {
  let busy = false;
  const removal = createChipRemoval({
    getLive: () => live_({ searchText: "a" }),
    getSelection: () => ({ sourceIds: [], collectionIds: [] }),
    restoreLive: () => { throw new Error("boom"); },
    clearSources() {}, clearCollections() {},
    setBusy: (v) => { busy = v; },
    applyView: () => Promise.resolve(),
  });
  assert.throws(() => removal.remove("text"), /boom/);
  assert.equal(busy, false);
  assert.equal(removal.isBusy(), false);
});

// ---- repeated records (9.12) -----------------------------------------------------------------------------------------

test("the hide-repeats filter is a removable chip with the window in words; other repeat leaves are left to the tree chip", () => {
  const hide = { not: { repeat: { window_ms: 1000 } } };
  assert.equal(chipLabel(hide, lookups, REAL), "Hide repeats within 1 s");
  assert.equal(chipLabel({ not: { repeat: { window_ms: 250 } } }, lookups, REAL), "Hide repeats within 250 ms");
  assert.equal(chipLabel({ not: { repeat: {} } }, lookups, REAL), "Hide repeats within 1 s");
  assert.equal(chipLabel({ not: { repeat: { window_ms: 0 } } }, lookups, REAL), "Hide repeats within 0 ms");
  assert.equal(chipLabel({ repeat: { window_ms: 5000 } }, lookups, REAL), "Repeats within 5 s");
  assert.equal(chipLabel({ not: { repeat: { window_ms: 5, fields: ["id"] } } }, lookups, REAL), null);
  assert.equal(chipLabel({ not: { repeat: { window_ms: -1 } } }, lookups, REAL), null);
  assert.equal(chipLabel({ repeat: { tid: true } }, lookups, REAL), null);
  const state = live({ userFilter: { and: [{ sev: { le: 3 } }, hide] } });
  const chips = chipsFromState(state, none, lookups, REAL);
  assert.deepEqual(chips.map((c) => [c.id, c.label, c.removable]), [
    ["node:0", "Severity at least Warning", true],
    ["node:1", "Hide repeats within 1 s", true],
  ]);
});

test("removing the hide-repeats chip takes only that node out and the composed filter loses it", () => {
  const hide = { not: { repeat: { window_ms: 1000 } } };
  const state = live({ userFilter: { and: [hide, { sev: { le: 3 } }] } });
  const result = removeChip(state, none, "node:0");
  assert.deepEqual(result.live.userFilter, { and: [{ sev: { le: 3 } }] });
  assert.equal(removeChip(live({ userFilter: { and: [hide] } }), none, "node:0").live.userFilter, null);
  assert.equal(JSON.stringify(composeFilter({ sourceIds: [], text: "" }, result.live.userFilter, { hideNoise: false, unclassified: false })).includes("repeat"), false);
});
