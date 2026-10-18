// Preferences (9.10): how the dialog is wired into the app (action, api, strings, css, reload hook). Static checks
// read the real files; the action runs the real module on the fake DOM.
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";

import { installGlobalDom, tick, until } from "./fixtures/fake-dom.mjs";

const doc = installGlobalDom();
const read = (name) => fs.readFileSync(new URL("../../src/dmslog/web/" + name, import.meta.url), "utf8").replace(/\r\n/g, "\n");
const { createActionRegistry } = await import("../../src/dmslog/web/js/actions.js");
const { registerAppActions } = await import("../../src/dmslog/web/js/app-actions.js");
const { api } = await import("../../src/dmslog/web/js/api.js");
const { S } = await import("../../src/dmslog/web/js/strings.js");

function deferred() {
  let resolve;
  const promise = new Promise((res) => { resolve = res; });
  return { promise, resolve };
}
const has = (n, cls) => n.className.split(/\s+/).includes(cls);

test("the action is in the Export group, needs no case and loads its module on demand", async () => {
  const registry = createActionRegistry();
  registerAppActions(registry, { ready: {} });
  const def = registry.get("export.preferences");
  assert.equal(def.group, "export");
  assert.equal(def.labelKey, "pfActPreferences");
  assert.equal(S.pfActPreferences, "Preferences: export / import…");
  assert.equal(registry.state("export.preferences", { caseId: () => null }).enabled, true);
  const source = read("js/app-actions.js");
  assert.match(source, /import\("\.\/preferences-dialog\.js"\)/);
  assert.doesNotMatch(source, /^import .*preferences/m, "never a static import");
});

test("running it opens one dialog (also the second time), from the active element, and an Apply reloads the app", async () => {
  doc.body.children = [];
  const opener = doc.createElement("button");
  doc.body.appendChild(opener);
  opener.focus();
  const registry = createActionRegistry();
  const reloads = [];
  const imports = [];
  registerAppActions(registry, {
    ready: {},
    preferencesApplied: () => reloads.push(true),
    api: { preferences: { export: () => deferred().promise, import: (body) => { const d = deferred(); imports.push({ body, d }); return d.promise; } } },
  });
  const ctx = { caseId: () => null };
  assert.deepEqual(await registry.invoke("export.preferences", ctx), { ok: true });
  assert.deepEqual(await registry.invoke("export.preferences", ctx), { ok: true });
  const dialogs = doc.body.all().filter((n) => n.tagName === "DIALOG");
  assert.equal(dialogs.length, 1);
  assert.equal(dialogs[0].getAttribute("open"), "");
  const input = dialogs[0].all().find((n) => n.type === "file");
  input.files = { length: 1, 0: { name: "p.json", size: 5, text: async () => '{"schema":1}' } };
  input.dispatch("change");
  const check = dialogs[0].all().find((n) => has(n, "pf-check"));
  await until(() => !check.disabled, "file read");
  check.click();
  imports[0].d.resolve({
    outcome: "applied", ok: true, applied: false, dry_run: true, schema: {}, errors: [], summary: {},
    sections: { presets: { status: "changed", counts: {}, items: [], removed: [], warnings: [], error: null } },
  });
  await tick();
  dialogs[0].all().find((n) => has(n, "pf-apply")).click();
  imports[1].d.resolve({
    outcome: "applied", ok: true, applied: true, dry_run: false, schema: {}, errors: [], summary: {},
    sections: { presets: { status: "changed", counts: {}, items: [], removed: [], warnings: [], error: null } },
  });
  await tick();
  assert.deepEqual(reloads, [true]);
  dialogs[0].all().find((n) => has(n, "dialog-close")).click();
  assert.equal(doc.activeElement, opener, "the focus returns to the element that opened it");
});

test("main.js reloads the presets and the alert rules after an Apply", () => {
  const main = read("js/main.js");
  assert.match(main, /preferencesApplied:\s*\(\)\s*=>\s*\{[\s\S]*filterTree\.refreshPresets\(\)[\s\S]*alertsUi\.refresh\(\)/);
  assert.match(read("js/filter-tree.js"), /refreshPresets:\s*\(\)\s*=>\s*presetsPanel\.refresh\(\)/);
});

// ---- api
test("the api asks for the chosen sections only and refuses an empty list (it would export all of them)", async () => {
  const calls = [];
  const saved = globalThis.fetch;
  globalThis.fetch = async (url, options) => {
    calls.push([url, options]);
    return { status: 200, ok: true, json: async () => ({ ok: 1 }) };
  };
  try {
    await api.preferences.export(["presets", "alert_rules"]);
    assert.equal(calls[0][0], "/api/preferences/export?sections=presets%2Calert_rules");
    assert.equal(calls[0][1].method, "GET");
    await assert.rejects(api.preferences.export([]), TypeError);
    await assert.rejects(api.preferences.export(undefined), TypeError);
    assert.equal(calls.length, 1, "no request for an empty list");
    const body = { package: "{}", dry_run: true, mode: "merge", on_item_error: "reject_section" };
    await api.preferences.import(body);
    assert.equal(calls[1][0], "/api/preferences/import");
    assert.equal(calls[1][1].method, "POST");
    assert.deepEqual(JSON.parse(calls[1][1].body), body);
  } finally {
    globalThis.fetch = saved;
  }
});

// ---- strings: every code the backend can send has a text
test("every item, section and structural code of store/preferences.py has a static text", () => {
  const source = fs.readFileSync(new URL("../../src/dmslog/store/preferences.py", import.meta.url), "utf8");
  const keysOf = (name) => {
    const body = new RegExp(name + " = \\{([\\s\\S]*?)\\n\\}").exec(source)[1];
    return [...body.matchAll(/^\s+"(\w+)":/gm)].map((m) => m[1]);
  };
  const items = keysOf("ITEM_MESSAGES");
  const errors = keysOf("ERROR_MESSAGES");
  assert.ok(items.length > 20 && errors.length > 20);
  for (const code of items) {
    assert.ok(S.pfReason[code], "item code " + code);
  }
  const structural = ["package_too_large", "package_too_complex", "bad_json", "bad_value", "not_object", "schema_missing", "bad_schema",
    "schema_too_new", "bad_kind", "unknown_section", "migration_failed", "bad_option"];
  for (const code of errors) {
    assert.ok(structural.includes(code) ? S.pfStructural[code] : S.pfReason[code], "error code " + code);
  }
  for (const code of ["existing_items_dropped", "stored_profile_invalid"]) {
    assert.ok(source.includes(`"${code}"`) && S.pfReason[code], code);
  }
  assert.ok(S.pfReason.unknown && S.pfStructural.unknown);
});

test("the limit the dialog uses is the one of the backend", () => {
  const source = fs.readFileSync(new URL("../../src/dmslog/store/preferences.py", import.meta.url), "utf8");
  assert.match(source, /MAX_PACKAGE_BYTES = 8 \* 1024 \* 1024/);
});

// ---- css and page
test("the stylesheet is linked, uses tokens for colors and has a forced-colors block", () => {
  assert.match(read("index.html"), /<link rel="stylesheet" href="\/css\/preferences\.css">/);
  const css = read("css/preferences.css").replace(/\/\*[\s\S]*?\*\//g, "");
  const outside = css.replace(/@media \(forced-colors: active\) \{[\s\S]*$/, "");
  assert.doesNotMatch(outside, /#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(/, "no literal color");
  assert.match(outside, /var\(--status-error-ink\)/);
  assert.match(css, /@media \(forced-colors: active\)/);
  assert.match(css, /\.pf-warning[\s\S]*?border-color: ButtonText/);
});

test("the warning about the redaction terms is static, plain and mentions what is exposed", () => {
  assert.match(S.pfRedactionWarning, /plain text/);
  assert.match(S.pfRedactionWarning, /Anyone who gets the file can read them/);
});
