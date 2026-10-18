// UI-12: the action registry (js/actions.js) is pure: registration rules, KeySpec, availability, loaders that
// fail without rejecting, shortcut matching and the bridge to ui-menu.js items.
import test from "node:test";
import assert from "node:assert/strict";

import {
  createActionRegistry, isKeySpec, isSingleKey, keySpec, shortcutLabel, toMenuItems,
} from "../../src/dmslog/web/js/actions.js";
import { S } from "../../src/dmslog/web/js/strings.js";

const withCase = { caseId: () => 1 };
const noCase = { caseId: () => null };

function make(extra = {}) {
  const errors = [];
  const registry = createActionRegistry({ onError: (err, id) => errors.push([String(err.message), id]), ...extra });
  return { registry, errors };
}

const def = (over = {}) => ({ id: "analyze.distinct", labelKey: "actDistinct", group: "analyze", run: () => {}, ...over });

test("register refuses a repeated id, an unknown group, a missing label and a bad shortcut", () => {
  const { registry } = make();
  registry.register(def());
  assert.throws(() => registry.register(def()), /duplicate action id/);
  assert.throws(() => registry.register(def({ id: "x", group: "nowhere" })), /unknown action group/);
  assert.throws(() => registry.register(def({ id: "y", labelKey: "doesNotExist" })), /labelKey/);
  assert.throws(() => registry.register(def({ id: "z", disabledKey: "doesNotExist" })), /disabledKey/);
  assert.throws(() => registry.register(def({ id: "w", shortcut: "Shift+b" })), /invalid shortcut/);
  assert.throws(() => registry.register(def({ id: "v", run: undefined })), /run/);
});

test("a shortcut cannot be used twice in the same scope, but can in another", () => {
  const { registry } = make();
  registry.register(def({ id: "a", shortcut: "B" }));
  assert.throws(() => registry.register(def({ id: "b", shortcut: "B" })), /already used by a/);
  registry.register(def({ id: "c", shortcut: "B", scope: "grid" }));
  assert.deepEqual(registry.list({ withShortcut: true }).map((d) => d.id), ["a", "c"]);
});

test("keySpec is canonical: modifier order, no Shift on a printable key, Meta and bare modifiers are not shortcuts", () => {
  assert.equal(keySpec({ key: "B", shiftKey: true }), "B");
  assert.equal(keySpec({ key: "?", shiftKey: true }), "?");
  assert.equal(keySpec({ key: "F6", shiftKey: true }), "Shift+F6");
  assert.equal(keySpec({ key: "ArrowLeft", altKey: true }), "Alt+ArrowLeft");
  assert.equal(keySpec({ key: "k", ctrlKey: true }), "Ctrl+k");
  assert.equal(keySpec({ key: "F10", ctrlKey: true, altKey: true, shiftKey: true }), "Ctrl+Alt+Shift+F10");
  assert.equal(keySpec({ key: "k", metaKey: true }), null);
  assert.equal(keySpec({ key: "Shift", shiftKey: true }), null);
  assert.equal(isKeySpec("Ctrl+k"), true);
  assert.equal(isKeySpec("Meta+k"), false);
  assert.equal(isKeySpec(""), false);
  assert.equal(isSingleKey("b"), true);
  assert.equal(isSingleKey("Ctrl+b"), false);
  assert.equal(isSingleKey("F6"), false);
  assert.equal(shortcutLabel("Alt+ArrowLeft", S.keyNames), "Alt+Left");
  assert.equal(shortcutLabel("B", S.keyNames), "B");
});

test("match: one-letter shortcuts obey the preference and never fire while typing; grid scope needs the grid", () => {
  let on = true;
  const { registry } = make({ singleKeysOn: () => on });
  registry.register(def({ id: "toggle.bookmarks", group: "toggle", labelKey: "actBookmarks", shortcut: "B" }));
  registry.register(def({ id: "help.palette", group: "help", labelKey: "actShortcuts", shortcut: "Ctrl+k" }));
  registry.register(def({ id: "record.menu", group: "record", labelKey: "actCompare", shortcut: "Shift+F10", scope: "grid" }));
  const bookmarks = { key: "B", shiftKey: true };
  assert.equal(registry.match(bookmarks, { typing: false, inGrid: false }), "toggle.bookmarks");
  assert.equal(registry.match(bookmarks, { typing: true, inGrid: false }), null);
  assert.equal(registry.match({ key: "k", ctrlKey: true }, { typing: true }), "help.palette"); // not a one-letter key
  assert.equal(registry.match({ key: "F10", shiftKey: true }, { inGrid: false }), null);
  assert.equal(registry.match({ key: "F10", shiftKey: true }, { inGrid: true }), "record.menu");
  on = false;
  assert.equal(registry.match(bookmarks, {}), null);
  assert.equal(registry.match({ key: "k", ctrlKey: true }, {}), "help.palette");
});

test("state: enabled needs a case by default, with a reason; enabled() and disabledKey override", () => {
  const { registry } = make();
  registry.register(def());
  registry.register(def({ id: "view.columns", group: "view", labelKey: "actColumns", enabled: () => true }));
  registry.register(def({ id: "export.copyAs", group: "export", labelKey: "actCopyAs", enabled: (c) => c.caseId() === 2, disabledKey: "actionUnavailable" }));
  assert.deepEqual(registry.state("analyze.distinct", noCase), { enabled: false, pressed: null, reasonKey: "needCase", loaded: true, failed: false });
  assert.equal(registry.state("analyze.distinct", withCase).enabled, true);
  assert.equal(registry.state("view.columns", noCase).enabled, true);
  assert.equal(registry.state("export.copyAs", withCase).reasonKey, "actionUnavailable");
  assert.equal(registry.state("nope", withCase).enabled, false);
});

test("a throwing enabled() or pressed() reads as off instead of breaking the menu", () => {
  const { registry } = make();
  registry.register(def({ enabled: () => { throw new Error("boom"); }, pressed: () => { throw new Error("boom"); } }));
  const now = registry.state("analyze.distinct", withCase);
  assert.equal(now.enabled, false);
  assert.equal(now.pressed, false);
});

test("preload calls load once; a failure makes the action unavailable, reports once and never rejects", async () => {
  let calls = 0;
  const { registry, errors } = make();
  let changes = 0;
  registry.onChange(() => { changes += 1; });
  registry.register(def({ load: async () => { calls += 1; throw new Error("module missing"); } })); // 1 change
  await registry.preload("analyze.distinct");
  await registry.preload("analyze.distinct");
  assert.equal(calls, 1);
  assert.deepEqual(errors, [["module missing", "analyze.distinct"]]);
  assert.equal(changes, 2); // register + the failure
  const now = registry.state("analyze.distinct", withCase);
  assert.deepEqual([now.enabled, now.failed, now.reasonKey], [false, true, "actionUnavailable"]);
  assert.deepEqual(await registry.invoke("analyze.distinct", withCase), { ok: false, reason: "loadFailed" });
});

test("invoke: runs when enabled, refuses when disabled, reports a failing run and never rejects", async () => {
  const ran = [];
  const { registry, errors } = make();
  registry.register(def({ run: (ctx, args) => { ran.push(args); } }));
  registry.register(def({ id: "view.columns", group: "view", labelKey: "actColumns", enabled: () => true, run: async () => { throw new Error("nope"); } }));
  assert.deepEqual(await registry.invoke("analyze.distinct", noCase), { ok: false, reason: "disabled" });
  assert.deepEqual(await registry.invoke("analyze.distinct", withCase, "x"), { ok: true });
  assert.deepEqual(ran, ["x"]);
  assert.deepEqual(await registry.invoke("view.columns", withCase), { ok: false, reason: "error" });
  assert.deepEqual(errors, [["nope", "view.columns"]]);
  assert.deepEqual(await registry.invoke("missing", withCase), { ok: false, reason: "error" });
});

test("toMenuItems: labels from strings, shortcut text, disabled items keep their reason, pressed becomes a checkbox", () => {
  const { registry } = make();
  registry.register(def({ shortcut: "Alt+ArrowLeft" }));
  registry.register(def({ id: "toggle.alerts", group: "toggle", labelKey: "actAlerts", pressed: () => true }));
  const items = toMenuItems(registry, [registry.get("analyze.distinct"), { separator: true }, registry.get("toggle.alerts")], noCase, S);
  assert.deepEqual(items.map((i) => i.id), ["analyze.distinct", "", "toggle.alerts"]);
  assert.equal(items[0].label, S.actDistinct);
  assert.equal(items[0].shortcut, "Alt+Left");
  assert.equal(items[0].disabled, true);
  assert.equal(items[0].title, S.needCase);
  assert.equal(items[1].separator, true);
  assert.equal(items[2].pressed, true);
  assert.equal(typeof items[0].preload, "function");
});

test("toMenuItems: children become radio items that run with their args; an empty submenu is disabled", async () => {
  const picked = [];
  const { registry } = make();
  registry.register(def({
    id: "view.theme", group: "view", labelKey: "actTheme", enabled: () => true,
    children: () => [{ label: "System", args: "system", checked: true }, { label: "Dark", args: "dark", checked: false }],
    run: (ctx, args) => { picked.push(args); },
  }));
  registry.register(def({ id: "case.open", group: "case", labelKey: "actCaseOpen", enabled: () => true, emptyKey: "caseNone", children: () => [], run: () => {} }));
  const [theme, cases] = toMenuItems(registry, registry.list(), withCase, S);
  assert.deepEqual(theme.children.map((c) => [c.label, c.role, c.checked]), [["System", "menuitemradio", true], ["Dark", "menuitemradio", false]]);
  assert.equal(theme.run, undefined);
  theme.children[1].run();
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.deepEqual(picked, ["dark"]);
  assert.equal(cases.disabled, true);
  assert.equal(cases.title, S.caseNone);
});

test("every label, title and disabled key used by the registered actions exists in strings.js", async () => {
  const { registerAppActions } = await import("../../src/dmslog/web/js/app-actions.js");
  const { registry } = make();
  const never = () => { throw new Error("not called while registering"); };
  registerAppActions(registry, {
    ready: {}, cases: never, selectCase: never, closeCase: never, focusNewCase: never, focusAddFiles: never,
    openColumns: never, copyExportOpen: never, followTailOn: never, toggleFollowTail: never, theme: never,
    setTheme: never, shortcutsText: never,
  });
  const ids = registry.list().map((d) => d.id);
  for (const id of ["case.open", "case.new", "case.addFiles", "case.close", "toggle.filterTree", "toggle.highlights",
    "toggle.bookmarks", "toggle.alerts", "analyze.distinct", "analyze.groupByType", "analyze.aggregate", "analyze.extract", "analyze.compareViews", "analyze.operations",
    "analyze.dashboard", "analyze.payloads", "analyze.unknownTypes", "analyze.config", "export.report", "export.copyAs",
    "view.columns", "view.followTail", "view.theme", "help.shortcuts", "record.copy", "record.context", "record.filterType",
    "record.highlightType", "navigate.nextError", "navigate.prevError", "navigate.nextType", "navigate.prevType",
    "navigate.nextHighlight", "navigate.prevHighlight", "navigate.focusSearch", "navigate.nextRegion", "navigate.prevRegion",
    "record.bookmark", "record.compare", "views.back", "views.forward"]) {
    assert.ok(ids.includes(id), id + " is registered");
  }
  for (const d of registry.list()) {
    assert.ok(typeof S[d.labelKey] === "string" && S[d.labelKey] !== "", d.id + " label");
    if (d.disabledKey) {
      assert.ok(S[d.disabledKey], d.id + " disabledKey");
    }
    if (d.emptyKey) {
      assert.ok(S[d.emptyKey], d.id + " emptyKey");
    }
  }
});
