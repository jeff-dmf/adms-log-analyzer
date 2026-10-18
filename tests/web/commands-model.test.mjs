// UI-29: the pure rules of the command palette and of the record context menu (js/commands-model.js), and the
// guarantee that both come from the one action registry of the menus (app-actions.js registers the real actions here).
import test from "node:test";
import assert from "node:assert/strict";

import { createActionRegistry, toMenuItems } from "../../src/dmslog/web/js/actions.js";
import { registerAppActions } from "../../src/dmslog/web/js/app-actions.js";
import {
  CONTEXT_MENU_EXCLUDED, PAGE_STEP, PALETTE_EXCLUDED, adaptMenuItems, buildCommands, captureTarget, contextMenuDefs, filterCommands, firstEnabled,
  fold, hideTypeFilter, keepActive, onlySourceFilter, returnFocus, shortcutText, stepActive, targetContext, targetCurrent,
} from "../../src/dmslog/web/js/commands-model.js";
import { S } from "../../src/dmslog/web/js/strings.js";
import { makeDoc } from "./fixtures/fake-dom.mjs";

// A small catalogue, so the tests do not depend on the wording of the app.
const T = {
  keyNames: { ArrowLeft: "Left" },
  cmdGroups: { analyze: "Analyze", record: "Record", view: "View", help: "Help" },
  cmdChoice: "{action}: {choice}",
  needCase: "Create or select a case first.",
  needRecord: "Select a record first.",
  none: "Nothing to choose from.",
  actionUnavailable: "This feature could not be loaded.",
  aDistinct: "Distinct values",
  aDistinctNoise: "Distinct noise",
  aAggregate: "Aggregate",
  aResume: "R\u00e9sum\u00e9 of the case",
  aTheme: "Theme",
  aBookmark: "Bookmark",
  aPalette: "Command palette…",
};

function registry(state = {}) {
  const ctx = { caseId: () => (state.noCase ? null : "C1"), entry: () => (state.record ? { id: 1 } : null) };
  const actions = createActionRegistry({ strings: T, singleKeysOn: () => true });
  const run = () => {};
  actions.register({ id: "analyze.distinct", labelKey: "aDistinct", group: "analyze", run });
  actions.register({ id: "analyze.noise", labelKey: "aDistinctNoise", group: "analyze", enabled: () => false, run });
  actions.register({ id: "analyze.aggregate", labelKey: "aAggregate", group: "analyze", run });
  actions.register({ id: "view.resume", labelKey: "aResume", group: "view", run });
  actions.register({
    id: "view.theme", labelKey: "aTheme", group: "view", enabled: () => true, emptyKey: "none",
    children: () => (state.choices ?? [{ label: "Dark", args: "dark", checked: true }, { label: "Light", args: "light" }]), run,
  });
  actions.register({ id: "record.bookmark", labelKey: "aBookmark", group: "record", shortcut: "b", enabled: () => Boolean(ctx.entry()), disabledKey: "needRecord", run });
  actions.register({ id: "help.palette", labelKey: "aPalette", group: "help", shortcut: "Ctrl+k", enabled: () => true, run });
  return { actions, ctx };
}
const build = (state, options = {}) => {
  const { actions, ctx } = registry(state);
  return buildCommands(actions, ctx, { strings: T, ...options });
};
const labels = (list) => list.map((c) => c.label);

test("fold: any case, accents ignored, null safe", () => {
  assert.equal(fold("R\u00e9sum\u00e9 \u00dcn\u00ef"), "resume uni");
  assert.equal(fold(null), "");
  assert.equal(fold(undefined), "");
});

test("buildCommands: registration order, the palette itself left out, shortcut and group text", () => {
  const commands = build({ record: true });
  assert.deepEqual(labels(commands), [
    "Distinct values", "Distinct noise", "Aggregate", "R\u00e9sum\u00e9 of the case", "Theme: Dark", "Theme: Light", "Bookmark",
  ]);
  assert.ok(!commands.some((c) => c.actionId === "help.palette"));
  assert.deepEqual(PALETTE_EXCLUDED, ["help.palette"]);
  const bookmark = commands.find((c) => c.actionId === "record.bookmark");
  assert.equal(bookmark.shortcut, "b");
  assert.equal(bookmark.groupLabel, "Record");
  assert.deepEqual(commands.map((c) => c.index), [0, 1, 2, 3, 4, 5, 6]);
  assert.equal(new Set(commands.map((c) => c.key)).size, commands.length, "keys are unique");
});

test("buildCommands: a one-letter shortcut is not shown while those are turned off; Ctrl combinations always are", () => {
  const off = build({ record: true }, { singleKeysOn: false, exclude: [] });
  assert.equal(off.find((c) => c.actionId === "record.bookmark").shortcut, null);
  assert.equal(off.find((c) => c.actionId === "help.palette").shortcut, "Ctrl+k");
  assert.equal(shortcutText("Alt+ArrowLeft", T, false), "Alt+Left");
  assert.equal(shortcutText("", T, true), null);
  assert.equal(shortcutText("Ctrl+k", S, false), "Ctrl+K", "the palette key reads as a capital, like the keys on the keyboard");
  assert.equal(shortcutText("Shift+F10", S, true), "Shift+F10");
});

test("buildCommands: disabled commands keep their reason; choices become one command each, with the args", () => {
  const commands = build({ record: false });
  const bookmark = commands.find((c) => c.actionId === "record.bookmark");
  assert.equal(bookmark.enabled, false);
  assert.equal(bookmark.reason, "Select a record first.");
  const noise = commands.find((c) => c.actionId === "analyze.noise");
  assert.equal(noise.enabled, false);
  assert.equal(noise.reason, T.needCase, "the default reason when the action names none");
  const dark = commands.find((c) => c.label === "Theme: Dark");
  assert.deepEqual([dark.enabled, dark.args, dark.checked], [true, "dark", true]);
  assert.equal(commands.find((c) => c.label === "Theme: Light").checked, false);
});

test("buildCommands: an action with no choices stays as one disabled command with the empty reason", () => {
  const commands = build({ choices: [] });
  const theme = commands.filter((c) => c.actionId === "view.theme");
  assert.equal(theme.length, 1);
  assert.deepEqual([theme[0].label, theme[0].enabled, theme[0].reason], ["Theme", false, "Nothing to choose from."]);
});

test("buildCommands: a case that is not open disables the actions that need one", () => {
  const commands = build({ noCase: true });
  assert.ok(commands.filter((c) => c.actionId.startsWith("analyze.")).every((c) => !c.enabled && c.reason === T.needCase));
});

test("filterCommands: empty query lists everything, available commands first, registry order inside each part", () => {
  const commands = build({ record: false });
  const all = filterCommands(commands, "");
  assert.equal(all.length, commands.length);
  const firstOff = all.findIndex((c) => !c.enabled);
  assert.ok(all.slice(firstOff).every((c) => !c.enabled), "no available command after a disabled one");
  assert.deepEqual(all.filter((c) => c.enabled).map((c) => c.index), all.filter((c) => c.enabled).map((c) => c.index).sort((a, b) => a - b));
  assert.deepEqual(filterCommands(commands, "   "), all);
});

test("filterCommands: starts with, then a word starts with, then inside a word; the registry order breaks ties", () => {
  const commands = [
    { key: "a", index: 0, label: "Reveal", folded: "reveal", enabled: true },
    { key: "b", index: 1, label: "Open view", folded: "open view", enabled: true },
    { key: "c", index: 2, label: "View columns", folded: "view columns", enabled: true },
    { key: "d", index: 3, label: "Preview", folded: "preview", enabled: true },
    { key: "e", index: 4, label: "View filters", folded: "view filters", enabled: true },
  ];
  assert.deepEqual(filterCommands(commands, "view").map((c) => c.key), ["c", "e", "b", "d"]);
  assert.deepEqual(filterCommands(commands, "VIEW").map((c) => c.key), ["c", "e", "b", "d"]);
});

test("filterCommands: every word must be found; accents and case are ignored; no match gives an empty list", () => {
  const commands = build({ record: true });
  assert.deepEqual(labels(filterCommands(commands, "dist val")), ["Distinct values"]);
  assert.deepEqual(labels(filterCommands(commands, "resume")), ["R\u00e9sum\u00e9 of the case"]);
  assert.deepEqual(labels(filterCommands(commands, "R\u00c9SUM\u00c9 CASE")), ["R\u00e9sum\u00e9 of the case"]);
  assert.deepEqual(filterCommands(commands, "zzzz"), []);
  assert.deepEqual(filterCommands(commands, "dist zzzz"), []);
  assert.deepEqual(labels(filterCommands(commands, "theme dark")), ["Theme: Dark"]);
});

test("filterCommands: a disabled command ranks after an available one with the same match, never above a better match", () => {
  const commands = build({ record: true });
  const hits = filterCommands(commands, "distinct");
  assert.deepEqual(labels(hits), ["Distinct values", "Distinct noise"]);
  assert.equal(hits[0].enabled, true);
  assert.equal(hits[1].enabled, false);
  const mixed = [
    { key: "x", index: 0, label: "Zoom", folded: "zoom", enabled: false },
    { key: "y", index: 1, label: "Big zoom", folded: "big zoom", enabled: true },
  ];
  assert.deepEqual(filterCommands(mixed, "zoom").map((c) => c.key), ["x", "y"], "the better match stays first even when disabled");
});

test("filterCommands does not change the list it is given and gives the same order twice", () => {
  const commands = build({ record: true });
  const before = commands.map((c) => c.key);
  const one = filterCommands(commands, "d");
  assert.deepEqual(commands.map((c) => c.key), before);
  assert.deepEqual(filterCommands(commands, "d").map((c) => c.key), one.map((c) => c.key));
});

test("keepActive / firstEnabled: keeps the same available command, else the first available one, else -1", () => {
  const list = [{ key: "a", enabled: false }, { key: "b", enabled: true }, { key: "c", enabled: true }];
  assert.equal(firstEnabled(list), 1);
  assert.equal(keepActive(list, "c"), 2);
  assert.equal(keepActive(list, "a"), 1, "a disabled one is never kept");
  assert.equal(keepActive(list, "zzz"), 1);
  assert.equal(keepActive(list, null), 1);
  assert.equal(keepActive([{ key: "a", enabled: false }], null), -1);
  assert.equal(keepActive([], "a"), -1);
});

test("stepActive: arrows wrap and skip disabled commands; paging clamps; other keys do not move", () => {
  const e = (enabled = true) => ({ enabled });
  const list = [e(), e(false), e(), e(), e(false), e()];
  assert.equal(stepActive(list, 0, "ArrowDown"), 2);
  assert.equal(stepActive(list, 3, "ArrowDown"), 5);
  assert.equal(stepActive(list, 5, "ArrowDown"), 0, "wraps");
  assert.equal(stepActive(list, 0, "ArrowUp"), 5, "wraps");
  assert.equal(stepActive(list, 2, "ArrowUp"), 0);
  assert.equal(stepActive(list, -1, "ArrowDown"), 0);
  assert.equal(stepActive(list, -1, "ArrowUp"), 5);
  assert.equal(stepActive(list, 0, "PageDown"), 5, "clamped at the end");
  assert.equal(stepActive(list, 5, "PageUp"), 0);
  assert.equal(stepActive(list, 5, "PageDown"), 5);
  for (const key of ["Home", "End", "Enter", "a", "ArrowLeft", "Tab", "Escape"]) {
    assert.equal(stepActive(list, 0, key), null, key);
  }
  assert.equal(stepActive([], -1, "ArrowDown"), -1);
  assert.equal(stepActive([e(false), e(false)], -1, "ArrowDown"), -1);
  assert.equal(PAGE_STEP, 8);
  const long = Array.from({ length: 30 }, () => e());
  assert.equal(stepActive(long, 0, "PageDown"), 8);
  assert.equal(stepActive(long, 20, "PageUp"), 12);
  assert.equal(stepActive(long, -1, "PageDown"), 7);
  assert.equal(stepActive(long, -1, "PageUp"), 22, "from no active option, PageUp starts at the end");
});

// ---- the real registry: the menus and the palette read the same one ----------------------------------------------------

function realRegistry() {
  const deps = new Proxy({}, { get: (target, name) => (name in target ? target[name] : () => null) });
  deps.ready = new Proxy({}, { get: () => Promise.resolve() });
  deps.cases = () => [{ id: "1", name: "Case one" }, { id: "2", name: "Case two" }];
  deps.theme = () => "dark";
  deps.recordShown = () => true;
  deps.hasType = () => true;
  deps.highlightReady = () => true;
  deps.drawerOpen = () => false;
  deps.repeats = () => null;
  const ctx = {
    caseId: () => "1", entry: () => ({ id: 7, source_id: 3, tpl_id: 9, keys: [{ key_type: "GUID", value: "abc" }] }),
    selectedRow: () => ({ id: 7 }), userFilter: () => null, setUserFilter: () => {},
    dock: { isOpen: () => false },
  };
  const actions = createActionRegistry({ strings: S, singleKeysOn: () => true });
  registerAppActions(actions, deps);
  return { actions, ctx };
}

test("every action of the registry, the ones of every menu included, is in the palette (one registry), but the palette itself", () => {
  const { actions, ctx } = realRegistry();
  const commands = buildCommands(actions, ctx, { strings: S });
  const inPalette = new Set(commands.map((c) => c.actionId));
  const missing = actions.list().map((d) => d.id).filter((id) => !inPalette.has(id));
  assert.deepEqual(missing, ["help.palette"], "only the launcher of the palette is not listed");
  for (const group of ["case", "toggle", "analyze", "export", "view", "record", "help", "navigate", "views"]) {
    const menu = toMenuItems(actions, actions.list({ group }), ctx, S).filter((item) => !item.separator);
    for (const item of menu) {
      if (item.id !== "help.palette") {
        assert.ok(inPalette.has(item.id), group + ": " + item.id + " is in a menu but not in the palette");
      }
    }
  }
  assert.ok(actions.list({ group: "help" }).some((d) => d.id === "help.palette" && d.shortcut === "Ctrl+k"), "View > Command palette… comes from the registry");
});

test("the palette shows the choices of an action (Theme), every label comes from strings.js, and no command text is empty", () => {
  const { actions, ctx } = realRegistry();
  const commands = buildCommands(actions, ctx, { strings: S });
  assert.ok(labels(commands).includes("Theme: Dark"));
  assert.ok(labels(commands).includes(S.actCaseOpen + ": Case one"), "a case is a choice of Case > Open");
  for (const c of commands) {
    assert.notEqual(c.label.trim(), "", c.key);
    assert.notEqual(c.groupLabel, "", c.key + ": group name");
  }
});

test("the record context menu lists the record actions in sections, without its own launcher", () => {
  const { actions, ctx } = realRegistry();
  const defs = contextMenuDefs(actions);
  const ids = defs.map((d) => (d.separator ? "|" : d.id));
  assert.ok(!ids.includes("record.contextMenu"));
  assert.deepEqual(CONTEXT_MENU_EXCLUDED, ["record.contextMenu"]);
  assert.deepEqual(ids, [
    "record.sameKey", "record.onlySource", "record.filterType", "record.hideType", "record.highlightType", "record.context",
    "|", "record.bookmark", "record.compare", "record.copy",
  ]);
  // Every record action but the launcher is there (a new one would be appended after the sections).
  const record = actions.list({ group: "record" }).map((d) => d.id).filter((id) => id !== "record.contextMenu");
  assert.deepEqual(ids.filter((id) => id !== "|").sort(), record.sort());
  const items = toMenuItems(actions, defs, ctx, S);
  assert.equal(items.find((i) => i.id === "record.bookmark").shortcut, "b");
});

test("contextMenuDefs: an action of the group that is not listed goes last; sections that came out empty leave no divider", () => {
  const actions = createActionRegistry({ strings: T, singleKeysOn: () => true });
  const run = () => {};
  actions.register({ id: "record.zeta", labelKey: "aBookmark", group: "record", run });
  actions.register({ id: "record.bookmark", labelKey: "aBookmark", group: "record", run });
  actions.register({ id: "record.contextMenu", labelKey: "aBookmark", group: "record", scope: "grid", shortcut: "Shift+F10", run });
  actions.register({ id: "analyze.distinct", labelKey: "aDistinct", group: "analyze", run });
  assert.deepEqual(contextMenuDefs(actions).map((d) => (d.separator ? "|" : d.id)), ["record.bookmark", "|", "record.zeta"]);
  const empty = createActionRegistry({ strings: T, singleKeysOn: () => true });
  assert.deepEqual(contextMenuDefs(empty), []);
});

test("Same key choices are the keys of the record, as the chips of the detail show them", async () => {
  const { actions, ctx } = realRegistry();
  await actions.preload("record.sameKey");
  const items = toMenuItems(actions, contextMenuDefs(actions), ctx, S);
  const same = items.find((i) => i.id === "record.sameKey");
  assert.equal(same.disabled, false);
  assert.deepEqual(same.children.map((c) => c.label), ["GUID: abc"]);
});

test("adaptMenuItems: one-letter shortcuts hide while turned off, runs are wrapped, children included, input untouched", () => {
  const { actions, ctx } = realRegistry();
  const items = toMenuItems(actions, contextMenuDefs(actions), ctx, S);
  const runs = [];
  const adapted = adaptMenuItems(items, actions, { singleKeysOn: false, strings: S, wrap: (fn) => () => { runs.push("wrapped"); return fn(); } });
  assert.equal(adapted.find((i) => i.id === "record.bookmark").shortcut, null);
  assert.equal(items.find((i) => i.id === "record.bookmark").shortcut, "b", "the input is not changed");
  assert.equal(adaptMenuItems(items, actions, { singleKeysOn: true, strings: S }).find((i) => i.id === "record.bookmark").shortcut, "b");
  assert.ok(adapted.some((i) => i.separator));
  const copy = adapted.find((i) => i.id === "record.copy");
  copy.run();
  assert.deepEqual(runs, ["wrapped"]);
  const withKids = adaptMenuItems([{ id: "x", label: "X", children: [{ id: "x:0", label: "c", run: () => runs.push("child") }] }], actions, { wrap: (fn) => () => { runs.push("w2"); return fn(); } });
  withKids[0].children[0].run();
  assert.deepEqual(runs.slice(1), ["w2", "child"]);
});

test("onlySourceFilter replaces an earlier source condition and keeps the others; hideTypeFilter joins earlier hides", () => {
  assert.deepEqual(onlySourceFilter(null, 3), { and: [{ source: [3] }] });
  assert.deepEqual(onlySourceFilter({ and: [{ sev: { le: 2 } }, { source: [1, 2] }] }, 3), { and: [{ sev: { le: 2 } }, { source: [3] }] });
  assert.deepEqual(onlySourceFilter({ tpl: [4] }, 3), { and: [{ tpl: [4] }, { source: [3] }] });
  assert.deepEqual(hideTypeFilter(null, 9), { and: [{ not: { tpl: [9] } }] });
  assert.deepEqual(hideTypeFilter({ and: [{ source: [1] }] }, 9), { and: [{ source: [1] }, { not: { tpl: [9] } }] });
  assert.deepEqual(hideTypeFilter({ and: [{ not: { tpl: [9] } }] }, 9), { and: [{ not: { tpl: [9] } }] }, "already hidden: nothing added");
  assert.deepEqual(hideTypeFilter({ and: [{ not: { tpl: [9] } }] }, 5), { and: [{ not: { tpl: [9, 5] } }] });
  const given = { and: [{ source: [1] }] };
  onlySourceFilter(given, 2);
  hideTypeFilter(given, 2);
  assert.deepEqual(given, { and: [{ source: [1] }] }, "the filter given is not changed");
});

test("returnFocus: the element if it takes the focus, else the first fallback that does", () => {
  const doc = makeDoc();
  doc.getElementById = (id) => doc.body.all().find((n) => n.id === id) ?? null;
  const make = (id, parent = doc.body) => {
    const n = doc.createElement("button");
    n.id = id;
    parent.appendChild(n);
    return n;
  };
  const grid = make("grid-viewport");
  const caseButton = make("case-button");
  const opener = make("opener");
  assert.equal(returnFocus(doc, opener), true);
  assert.equal(doc.activeElement, opener);
  opener.remove();
  assert.equal(returnFocus(doc, opener), true, "removed: the grid");
  assert.equal(doc.activeElement, grid);
  grid.hidden = true;
  assert.equal(returnFocus(doc, opener), true, "grid hidden: the case button");
  assert.equal(doc.activeElement, caseButton);
  caseButton.disabled = true;
  assert.equal(returnFocus(doc, opener), false);
  assert.equal(returnFocus(doc, null, []), false);
});

test("captureTarget / targetCurrent / targetContext: the record, its detail and the case are taken once", () => {
  const live = { caseId: "C1", sel: { id: 7 }, entry: { id: 7, source_id: 3 }, filters: [] };
  const ctx = {
    caseId: () => live.caseId, selectedRow: () => live.sel, entry: () => live.entry, userFilter: () => null,
    setUserFilter: (f) => { live.filters.push(f); },
  };
  const target = captureTarget(ctx);
  assert.deepEqual([target.caseId, target.rowId, target.entry.source_id], ["C1", 7, 3]);
  assert.equal(targetCurrent(ctx, target), true);
  const bound = targetContext(ctx, target);
  live.entry = { id: 7, source_id: 99 };
  assert.equal(bound.entry().source_id, 3, "the captured detail, not the live one");
  assert.equal(bound.selectedRow().id, 7);
  live.caseId = "C2";
  assert.equal(bound.caseId(), "C1", "the captured case");
  live.caseId = "C1";
  bound.setUserFilter("kept");
  assert.deepEqual(live.filters, ["kept"]);
  for (const change of [() => { live.caseId = "C2"; }, () => { live.sel = { id: 8 }; }, () => { live.sel = null; }, () => { live.entry = { id: 8 }; }, () => { live.entry = null; }]) {
    live.caseId = "C1"; live.sel = { id: 7 }; live.entry = { id: 7 };
    change();
    assert.equal(targetCurrent(ctx, target), false);
    live.filters.length = 0;
    bound.setUserFilter("late");
    assert.deepEqual(live.filters, [], "a late setUserFilter is dropped");
  }
  // The detail shows another record than the selection: no entry in the target.
  live.caseId = "C1"; live.sel = { id: 9 }; live.entry = { id: 7, source_id: 3 };
  assert.equal(captureTarget(ctx).entry, null);
  live.sel = null; live.entry = null;
  const empty = captureTarget(ctx);
  assert.deepEqual([empty.rowId, empty.entry], [null, null]);
  assert.equal(targetCurrent(ctx, empty), true);
  live.sel = { id: 1 };
  assert.equal(targetCurrent(ctx, empty), false);
  assert.equal(targetCurrent({ caseId() { throw new Error("x"); } }, empty), false);
});

test("invoke(): the optional guard is asked after the last wait and right before run(); false means stale and run() is not called", async () => {
  let ran = 0;
  let allowed = true;
  const actions = createActionRegistry({ strings: T, singleKeysOn: () => true });
  actions.register({ id: "analyze.x", labelKey: "aDistinct", group: "analyze", enabled: () => true, load: () => Promise.resolve(), run: () => { ran += 1; } });
  const ctx = { caseId: () => "C1" };
  assert.deepEqual(await actions.invoke("analyze.x", ctx), { ok: true });
  assert.deepEqual(await actions.invoke("analyze.x", ctx, undefined, () => allowed), { ok: true });
  const pending = actions.invoke("analyze.x", ctx, undefined, () => allowed);
  allowed = false; // changes while invoke() waits
  assert.deepEqual(await pending, { ok: false, reason: "stale" });
  assert.equal(ran, 2);
  assert.deepEqual(await actions.invoke("analyze.x", ctx, undefined, () => { throw new Error("x"); }), { ok: false, reason: "stale" }, "a guard that throws is a no");
  const items = toMenuItems(actions, [actions.get("analyze.x")], ctx, T, () => false);
  assert.deepEqual(await items[0].run(), { ok: false, reason: "stale" }, "toMenuItems hands the guard to invoke()");
  assert.equal(ran, 2);
});
