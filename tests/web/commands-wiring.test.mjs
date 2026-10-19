// UI-29: how the pieces are wired into the page: the modules load on first use (not at the opening), the owner closes the
// record menu when the case, the view or the selection change, the stylesheet is linked and uses tokens only, and the new
// modules write no markup from strings.
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";

import { literalColors } from "./fixtures/css-named-colors.mjs";
import { SHEET_ORDER, inBundle, sheetIndex, pageLinksBundle } from "./fixtures/sheets.mjs";

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
  assert.match(main, /onContextMenu: \(request\) => \{\s*requestRowMenu\(request\)\.catch\(showError\);/);
  assert.match(main, /onSelect: \(row\) => \{\s*if \(rowMenus === null \|\| rowMenus\.targetId\(\) === null \|\| String\(rowMenus\.targetId\(\)\) !== String\(row\.id\)\) \{\s*closeRowMenu\(\);/);
  assert.match(main, /const closeRowMenu = \(\) => \{\s*if \(rowMenus !== null\) \{\s*rowMenus\.close\(\);/);
  assert.match(main, /const applyView = \(\) => \{\s*closeRowMenu\(\);/);
  assert.match(main, /caseId = id;\s*closeRowMenu\(\);\s*if \(palette !== null\) \{\s*palette\.close\(\);/);
});

test("the palette reads the opener and its region before its module loads and hands both to open()", () => {
  const main = read("js/main.js");
  const body = /openPalette: async \(\) => \{([\s\S]*?)\n    \},/.exec(main)[1];
  assert.ok(body.indexOf("document.activeElement") < body.indexOf("await loadPalette()"));
  assert.ok(body.indexOf("regionOf(opener)") < body.indexOf("await loadPalette()"));
  assert.match(body, /module\.open\(opener, region\)/);
  assert.match(main, /restoreFocus: \(el, region\) => \(shortcuts === null \? false : shortcuts\.regions\.restore\(el, region\)\)/);
});

test("the palette gets the record loader (prepare) so its record commands act on the selected record", () => {
  const main = read("js/main.js");
  const block = /const loadPalette = retryable\([\s\S]*?\n  \}\)\);/.exec(main)[0];
  assert.match(block, /prepare: showRecordFor,/);
});

test("main.js builds the menu requests from the pieces of the page (the behavior is tested in record-loader.test.mjs)", () => {
  const main = read("js/main.js");
  const block = /rowMenus = createMenuRequests\(\{([\s\S]*?)\n  \}\);/.exec(main)[1];
  for (const piece of ["doc: document", "win: window", "getCase: () => caseId", "selectedRow: () => grid.selectedRow()",
    "holdSelection: () => grid.holdSelection()", "show: (row) => showRecordFor(row)", "load: () => loadRowMenu()", "getMenu: () => rowMenu"]) {
    assert.ok(block.includes(piece), piece);
  }
  assert.match(main, /const requestRowMenu = \(request\) => rowMenus\.request\(request\);/);
});

test("the selection, the menu and the palette show a record through the one loader", () => {
  const main = read("js/main.js");
  assert.match(main, /return recordLoader\.ensure\(row, \{ refresh: true \}\);/);
  assert.match(main, /const showRecordFor = \(row\) => recordLoader\.ensure\(row\);/);
  assert.ok(!/onSelect: \(row\) => \{[\s\S]{0,300}detail\.show\(/.test(main), "onSelect no longer calls detail.show directly");
});

test("the palette opened in one case does nothing when the module arrives in another", () => {
  const main = read("js/main.js");
  const body = /openPalette: async \(\) => \{([\s\S]*?)\n    \},/.exec(main)[1];
  assert.ok(body.indexOf("const forCase = caseId") < body.indexOf("await loadPalette()"));
  assert.match(body, /if \(caseId !== forCase\) \{\s*return;/);
});

test("Shift+F10 goes through the same request (generation, shown record, selection held) as the right click", () => {
  const main = read("js/main.js");
  assert.match(main, /openRecordMenu: async \(\) => \{[\s\S]*?await requestRowMenu\(\{ row, x: at\.x, y: at\.y, keyboard: true \}\);/);
});

test("commands.css is linked, uses tokens only and has a forced-colors block", () => {
  assert.ok(pageLinksBundle(read("index.html")) && inBundle("commands.css"));
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
