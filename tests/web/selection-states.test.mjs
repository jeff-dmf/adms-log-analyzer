// Row selection (UI-07): the selection stays visible under highlights and abnormal rows, and has an
// outline-based variant for Windows high contrast (forced-colors), where box-shadow and fills are replaced.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const CSS = new URL("../../src/dmslog/web/css/", import.meta.url);
const strip = (s) => s.replace(/\/\*[\s\S]*?\*\//g, "");
const app = strip(readFileSync(new URL("app.css", CSS), "utf8"));
const hl = strip(readFileSync(new URL("highlight.css", CSS), "utf8"));

function forced(css) {
  const start = css.indexOf("@media (forced-colors: active)");
  assert.ok(start >= 0, "has a forced-colors block");
  const open = css.indexOf("{", start);
  let depth = 0;
  for (let i = open; i < css.length; i++) {
    if (css[i] === "{") depth++;
    if (css[i] === "}" && --depth === 0) return css.slice(open + 1, i);
  }
  assert.fail("unclosed forced-colors block");
}

function rule(css, selector) {
  const i = css.indexOf(selector + " {");
  assert.ok(i >= 0, `${selector} rule exists`);
  return css.slice(css.indexOf("{", i) + 1, css.indexOf("}", i));
}

test("normal mode: the selection is a 3 px inset bar, also on highlighted rows next to the band", () => {
  assert.match(rule(app, ".grid-row.selected,\n.grid-row.selected:hover"), /box-shadow:\s*inset 3px 0 0 var\(--accent\)/);
  const both = rule(hl, ".grid-row.selected[data-hl],\n.grid-row.selected[data-hl]:hover");
  assert.match(both, /inset 3px 0 0 var\(--accent\),\s*inset 7px 0 0 var\(--hl-bar\)/);
  assert.match(both, /background:\s*var\(--row-selected\)/);
  assert.match(rule(hl, ".grid-row[data-hl]"), /box-shadow:\s*inset 4px 0 0 var\(--hl-bar\)/);
});

test("the abnormal row fill uses --abn-row and comes before the selection rule", () => {
  assert.match(rule(app, '.grid-row[data-sev="1"],\n.grid-row[data-sev="2"]'), /background:\s*var\(--abn-row\)/);
  assert.ok(app.indexOf("--abn-row") < app.indexOf(".grid-row.selected,"));
});

test("Critical and Error have a non-chromatic cue (weight 600 in Level)", () => {
  assert.match(rule(app, '.grid-row[data-sev="1"] .col-level,\n.grid-row[data-sev="2"] .col-level'), /font-weight:\s*600/);
});

test("forced-colors: the selection is an outline with Highlight/HighlightText", () => {
  const block = forced(app);
  assert.match(block, /forced-color-adjust:\s*none/);
  assert.match(block, /background:\s*Highlight/);
  assert.match(block, /color:\s*HighlightText/);
  assert.match(block, /outline:\s*2px solid Highlight/);
  assert.doesNotMatch(block, /box-shadow/);
  assert.match(block, /\[aria-pressed="true"\]/);
});

test("forced-colors: highlighted rows use Mark for the band and keep the selection outline", () => {
  const block = forced(hl);
  assert.match(block, /Mark/);
  assert.match(block, /\.grid-row\.selected\[data-hl\]:hover/);
  assert.doesNotMatch(block, /box-shadow/);
});

test("no layout change: selection and highlight rules do not touch size, border or padding of the row", () => {
  for (const body of [rule(app, ".grid-row.selected,\n.grid-row.selected:hover"), rule(hl, ".grid-row[data-hl]")]) {
    assert.doesNotMatch(body, /\b(width|height|padding|margin|border)\b/);
  }
});
