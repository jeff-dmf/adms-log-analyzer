import { readFileSync } from "node:fs";
import { test } from "node:test";
import assert from "node:assert/strict";

const source = readFileSync(new URL("../../src/dmslog/web/js/main.js", import.meta.url), "utf8");

// main.js is not importable in node, so a misspelt map name would only fail at click time
// (issue #178: "Highlight this template" threw a ReferenceError). Every `template*` map the file reads
// with `.get(` must be declared in the file.
test("every template map read in main.js is declared", () => {
  const used = new Set([...source.matchAll(/\b(template[A-Za-z]*)\.get\(/g)].map((m) => m[1]));
  assert.ok(used.size > 0, "expected main.js to read at least one template map");
  for (const name of used) {
    const declared = new RegExp(`\\b(?:let|const|var)\\s+${name}\\b`).test(source);
    assert.ok(declared, `main.js reads ${name}.get(...) but never declares ${name}`);
  }
});

// UI-24: the same-key panel never shows a template code. main.js is not importable, so the wiring is pinned in text.
test("the same-key panel reads titles only: no fallback to the template code", () => {
  const fill = source.match(/templateTitles = new Map\(\(data\.templates[^\n]*/);
  assert.ok(fill, "templateTitles is filled from the catalogue");
  assert.match(fill[0], /filter\(\(t\) => t\.title\)/, "templates without a title are left out");
  assert.doesNotMatch(fill[0], /t\.code/);
  assert.match(source, /typeLabel: \(id\) => templateTitles\.get\(id\) \|\| ""/);
  assert.doesNotMatch(source, /typeLabel: \(id\) => templateLabels\.get\(id\) \|\| ""/, "the panel's lookup must not read the code-falling-back map");
});

test("Show in grid builds the child view holding the key, so the view loads once", () => {
  assert.match(source, /userFilter = childFilter !== null \? childFilter : state\.userFilter;/);
  const narrowWith = source.match(/narrowWith: \(filter\) => \{[\s\S]*?return viewsStack\.depth !== before;/);
  assert.ok(narrowWith);
  assert.match(narrowWith[0], /childFilter = filter;[\s\S]*viewsUi\.narrow\(\)[\s\S]*finally \{\s*childFilter = null;/);
  assert.doesNotMatch(narrowWith[0], /setUserFilter|applyView/, "no second reload after narrow()");
});
