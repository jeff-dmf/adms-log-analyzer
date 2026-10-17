// UI-29: how the pieces are wired into the page: the modules load on first use (not at the opening), the owner closes the
// record menu when the case, the view or the selection change, the stylesheet is linked and uses tokens only, and the new
// modules write no markup from strings.
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";

import { literalColors } from "./fixtures/css-named-colors.mjs";

const WEB = new URL("../../src/dmslog/web/", import.meta.url);
const read = (file) => fs.readFileSync(new URL(file, WEB), "utf8").replace(/\r\n/g, "\n");
const stripComments = (css) => css.replace(/\/\*[\s\S]*?\*\//g, "");

test("main.js loads the palette and the record menu on first use, with a retry after a failure", () => {
  const main = read("js/main.js");
  assert.match(main, /const loadRowMenu = retryable\(\(\) => import\("\.\/context-menu\.js"\)/);
  assert.match(main, /const loadPalette = retryable\(\(\) => import\("\.\/commands\.js"\)/);
  assert.ok(!/^import .*(commands|context-menu)/m.test(main), "not in the static graph");
});

test("the grid hands the record menu its request; the owner closes the menu on a selection, a view change and a case change", () => {
  const main = read("js/main.js");
  assert.match(main, /onContextMenu: \(request\) => \{\s*openRowMenu\(request\)\.catch\(showError\);/);
  assert.match(main, /onSelect: \(row\) => \{\s*closeRowMenu\(\);/);
  assert.match(main, /const applyView = \(\) => \{\s*closeRowMenu\(\);/);
  assert.match(main, /caseId = id;\s*closeRowMenu\(\);\s*if \(palette !== null\) \{\s*palette\.close\(\);/);
});

test("the palette reads the opener before its module loads", () => {
  const main = read("js/main.js");
  assert.match(main, /openPalette: async \(\) => \{\s*const opener = document\.activeElement;\s*\(await loadPalette\(\)\)\.open\(opener\);/);
});

test("commands.css is linked, uses tokens only and has a forced-colors block", () => {
  assert.match(read("index.html"), /<link rel="stylesheet" href="\/css\/commands\.css">/);
  const css = stripComments(read("css/commands.css"));
  assert.deepEqual(literalColors(css.replace(/@media \(forced-colors: active\)[\s\S]*$/, "")), []);
  assert.match(css, /@media \(forced-colors: active\)/);
  assert.match(css, /\.cmd-option\[aria-selected="true"\]/);
  assert.match(css, /\.cmd-option\[aria-disabled="true"\]/);
  assert.ok(!/opacity\s*:/.test(css), "disabled is not told by opacity");
});

test("the new modules write nothing from strings as markup and keep every visible text in strings.js", () => {
  for (const file of ["js/commands.js", "js/commands-model.js", "js/context-menu.js"]) {
    const source = read(file);
    assert.ok(!/innerHTML|outerHTML|insertAdjacentHTML|document\.write|eval\(/.test(source), file);
    assert.ok(!/(textContent|placeholder|aria-label"?,)\s*[=,]\s*"[A-Za-z]/.test(source), file + ": a literal visible text");
  }
});
