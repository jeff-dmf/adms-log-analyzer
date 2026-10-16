// UI-21: the side column's stylesheet and the page that carries it. CRLF is normalized (autocrlf on Windows).
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";

const read = (name) => fs.readFileSync(new URL("../../src/dmslog/web/" + name, import.meta.url), "utf8").replace(/\r\n/g, "\n");
const css = read("css/sources.css").replace(/\/\*[\s\S]*?\*\//g, "");
const collections = read("css/collections.css");
const html = read("index.html");
const main = read("js/main.js");
const rule = (sheet, selector) => {
  const start = sheet.indexOf(selector + " {");
  assert.ok(start >= 0, "no rule for " + selector);
  return sheet.slice(start, sheet.indexOf("}", start));
};

test("the list keeps at least 240px and takes the free height; [hidden] wins over the rules that set display", () => {
  const list = rule(css, "#sources-panel > #source-list");
  assert.match(list, /flex: 1 0 240px/);
  assert.match(list, /min-height: 240px/);
  assert.match(rule(css, "#sources-panel [hidden]"), /display: none !important/);
});

test("the list is the containing block of the hidden meta, so many rows never make the whole column scroll", () => {
  assert.match(rule(css, ".source-list"), /position: relative/);
  assert.match(rule(css, ".source-list"), /overflow: auto/);
});

test("a source row is one line of --source-row, with the name cut by an ellipsis", () => {
  assert.match(css, /--source-row: 22px/);
  assert.match(rule(css, ".source-list label"), /min-height: var\(--source-row\)/);
  const name = rule(css, ".source-name");
  assert.match(name, /white-space: nowrap/);
  assert.match(name, /text-overflow: ellipsis/);
  assert.match(rule(css, ".source-text"), /flex-direction: row/);
});

test("the unreadable marker uses the error pair of tokens and the header is sticky", () => {
  const mark = rule(css, ".source-unreadable");
  assert.match(mark, /background: var\(--status-error-bg\)/);
  assert.match(mark, /color: var\(--status-error-ink\)/);
  assert.match(rule(css, ".case-header"), /position: sticky/);
});

test("collections: the list scrolls after about six rows, the remove item has the danger color, the fold shows its state", () => {
  assert.match(rule(collections, ".col-list"), /max-height: 9\.5rem/);
  assert.match(rule(collections, '.ui-menu-item[data-id="col-remove"]'), /color: var\(--status-error-ink\)/);
  assert.match(collections, /\.col-fold\[aria-expanded="true"\]::before/);
});

test("index.html loads sources.css after app.css and carries the case header the panel is given", () => {
  assert.ok(html.indexOf("/css/sources.css") > html.indexOf("/css/app.css"));
  for (const id of ["case-header", "case-header-name", "case-row", "case-add-files", "case-header-menu", "add-files-host"]) {
    assert.match(html, new RegExp('id="' + id + '"'), id);
    assert.match(main, new RegExp('byId\\("' + id + '"\\)'), id + " reaches the panel");
  }
  assert.match(html, /id="case-header"[^>]*\shidden/);
  assert.match(html, /id="new-case-form"[^>]*\shidden/); // hidden from the first paint: the case decides later
  assert.match(html, /id="open-form"[^>]*\shidden/);
  assert.match(html, /id="case-add-files"[^>]*aria-controls="open-form"/);
  assert.match(main, /collectionsPanel\.setSources\(list\)/);
  assert.match(main, /focusNewCase: \(\) => panel\.revealNewCase\(\)/);
});
