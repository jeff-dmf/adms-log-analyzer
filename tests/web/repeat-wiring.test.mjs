// 9.12 UI: how main.js, index.html and the styles wire the repeat features (the pieces are tested on their own).
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { SHEET_ORDER, inBundle, sheetIndex, pageLinksBundle } from "./fixtures/sheets.mjs";

const WEB = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../src/dmslog/web");
const read = (file) => fs.readFileSync(path.join(WEB, file), "utf8").replace(/\r\n/g, "\n");

test("main.js loads the repeats late and retryable, feeds the grid, the detail and the case/index changes", () => {
  const main = read("js/main.js");
  assert.match(main, /import\("\.\/repeat-model\.js"\)/);
  assert.doesNotMatch(main, /^import .*repeat-model/m, "never a static import: the opening graph is at its budget");
  assert.match(main, /const loadRepeats = retryable\(/);
  assert.match(main, /ready\.repeats = settled\(afterFirstView\.then\(\(\) => loadRepeats\(\)\)\)/);
  assert.match(main, /getRepeat: \(id\) => \(repeats \? repeats\.get\(id, grid\.caseId\) : null\)/);
  assert.match(main, /if \(repeats\) \{\s*repeats\.ensure\(ids, grid\.caseId\);/);
  assert.match(main, /repeatDetail\.show\(entry\)/);
  assert.match(main, /import\("\.\/repeat-detail\.js"\)/);
  assert.equal((main.match(/repeats\.invalidate\(\)/g) || []).length, 2, "a new case and a new indexing");
  assert.match(main, /repeats\.invalidate\(\); \/\/ the ids moved\s*\}\s*if \(repeatDetail\) \{\s*repeatDetail\.close\(\)/);
  assert.match(main, /repeats: \(\) => repeats,/);
  assert.match(main, /setUserFilter: \(filter\) => setUserFilter\(filter\)\.catch\(showError\)/);
});

test("index.html has the detail section right after the text (reading order) and loads the stylesheet", () => {
  const html = read("index.html");
  assert.ok(html.indexOf('id="detail-text"') < html.indexOf('id="detail-repeat"'));
  assert.ok(html.indexOf('id="detail-repeat"') < html.indexOf('id="detail-knowledge"'));
  assert.match(html, /<section id="detail-repeat"[^>]* hidden>/);
  assert.ok(pageLinksBundle(html) && inBundle("repeats.css"));
  assert.doesNotMatch(html, /modulepreload" href="\/js\/(repeat|missing)/, "late modules are not preloaded");
});

test("repeats.css: a glyph marks the row, no reorder, and every colour is a token that exists", () => {
  const css = read("css/repeats.css");
  assert.match(css, /\.grid-row\[data-rp\] \.col-msg::before/);
  assert.equal(/(^|[\s;{])order:\s*\d/m.test(css), false);
  const defined = new Set();
  for (const file of fs.readdirSync(path.join(WEB, "css"))) {
    for (const match of read("css/" + file).matchAll(/(--[a-z0-9-]+)\s*:/g)) {
      defined.add(match[1]);
    }
  }
  for (const match of css.matchAll(/var\((--[a-z0-9-]+)/g)) {
    assert.ok(defined.has(match[1]), match[1] + " is not defined");
  }
});

test("the modules write text only", () => {
  for (const file of ["repeat-model.js", "repeat-detail.js", "missing-model.js", "missing-panel.js"]) {
    assert.doesNotMatch(read("js/" + file), /innerHTML|outerHTML|insertAdjacentHTML|document\.write|eval\(/, file);
  }
});

test("repeats.css: the glyph follows the cell colour in the selected row, with a system colour in forced colours", () => {
  const css = read("css/repeats.css");
  assert.match(css, /\.grid-row\.selected \.col-msg::before\s*\{\s*color: inherit;/);
  assert.match(css, /forced-colors: active\)[^}]*\{[^}]*\.grid-row\.selected \.col-msg::before\s*\{\s*color: HighlightText;/s);
});
