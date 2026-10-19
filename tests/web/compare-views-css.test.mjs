// 9.7 UI: css/compare-views.css uses only tokens, keeps the kind of difference readable without colour, and has a
// forced-colors block. The sheet is linked from index.html and shipped as a static asset.
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { SHEET_ORDER, inBundle, sheetIndex, pageLinksBundle } from "./fixtures/sheets.mjs";

const read = (rel) => fs.readFileSync(new URL("../../src/dmslog/web/" + rel, import.meta.url), "utf8").replace(/\r\n/g, "\n");
const css = read("css/compare-views.css");
const tokens = read("css/tokens.css") + read("css/app.css");
const rule = (selector) => {
  const start = css.indexOf(selector + " {");
  assert.ok(start >= 0, "no rule for " + selector);
  return css.slice(start, css.indexOf("}", start));
};

test("no colour literals and every variable is defined by the tokens or app.css", () => {
  assert.doesNotMatch(css.replace(/@media \(forced-colors: active\)[\s\S]*$/, ""), /#[0-9a-fA-F]{3,8}\b|rgb\(|hsl\(/);
  for (const [, name] of css.matchAll(/var\((--[a-z0-9-]+)/g)) {
    assert.ok(tokens.includes(name + ":"), name + " is not defined");
  }
});

test("the kind of difference has a border style of its own besides the written label", () => {
  const styles = ["only_a", "only_b", "changed"].map((op) => /border-left: 4px (\w+)/.exec(rule(".cv-op-" + op + " .cv-op"))[1]);
  assert.equal(new Set(styles).size, 3, styles.join(","));
  assert.match(rule(".cv-op"), /white-space: nowrap/);
});

test("forced colors: markers use system colours and focus stays visible", () => {
  const block = css.slice(css.indexOf("@media (forced-colors: active)"));
  assert.match(block, /border-left-color: CanvasText/);
  assert.match(block, /\.cv-open:focus-visible[\s\S]*outline: 2px solid Highlight/);
  assert.match(rule(".cv-open:focus-visible"), /outline: 2px solid var\(--focus-ring\)/);
});

test("[hidden] wins over the display of the groups and the row tools", () => {
  assert.match(css, /\.cv-group\[hidden\][\s\S]*display: none !important/);
});

test("the sheet is part of the page bundle", () => {
  assert.ok(pageLinksBundle(read("index.html")) && inBundle("compare-views.css"));
});
