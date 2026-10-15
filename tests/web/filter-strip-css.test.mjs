// Filter strip CSS (UI-18): the Filtered state, the fixed height and the rules that keep chips from being cut.
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";

const css = fs.readFileSync(new URL("../../src/dmslog/web/css/filter-strip.css", import.meta.url), "utf8").replace(/\r\n/g, "\n");
const rule = (selector) => {
  const start = css.indexOf(selector + " {");
  assert.ok(start >= 0, "no rule for " + selector);
  return css.slice(start, css.indexOf("}", start));
};

test("the strip is --strip-height tall and the Filtered state uses the tokens", () => {
  assert.match(rule(".filter-strip"), /height: var\(--strip-height\)/);
  const filtered = rule(".filter-strip.is-filtered");
  assert.match(filtered, /background: var\(--filtered-bg\)/);
  assert.match(filtered, /box-shadow: inset 3px 0 0 var\(--accent\)/);
  assert.match(rule(".fs-state.is-none"), /color: var\(--ink-muted\)/);
});

test("chips never shrink or wrap (they hide behind +N more instead of being cut)", () => {
  assert.match(rule(".fs-chips > *"), /flex: none/);
  assert.match(rule(".fs-chips"), /overflow: hidden/);
  assert.match(rule(".fs-chips"), /flex: 1 1 0/);
});

test("[hidden] wins over the display of chips and popovers", () => {
  assert.match(rule(".filter-strip [hidden]"), /display: none !important/);
});

test("the filter form is a popover under the strip, above the grid", () => {
  const form = rule(".filter-strip > #filter-form");
  assert.match(form, /position: absolute/);
  assert.match(form, /top: 100%/);
  assert.match(form, /z-index: \d+/);
});

test("no colour literals: only tokens", () => {
  assert.doesNotMatch(css, /#[0-9a-fA-F]{3,8}\b|rgb\(/);
});
