// Highlight palette: every text color a highlighted row can show keeps a WCAG contrast of at least 4.5:1 on
// every highlight color, in the light and in the dark theme (tokens read from the CSS files).
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import { PALETTE } from "../../src/dmslog/web/js/highlight-model.js";

const CSS = new URL("../../src/dmslog/web/css/", import.meta.url);
const app = readFileSync(new URL("app.css", CSS), "utf8");
const hl = readFileSync(new URL("highlight.css", CSS), "utf8");

// Custom properties of the first block that starts with `selector {` (no nested braces inside).
function tokens(css, selector) {
  const start = css.indexOf(selector + " {");
  assert.ok(start >= 0, selector);
  const body = css.slice(css.indexOf("{", start) + 1, css.indexOf("}", start));
  const out = {};
  for (const match of body.matchAll(/(--[\w-]+)\s*:\s*(#[0-9a-fA-F]{6})\s*;/g)) {
    out[match[1]] = match[2].toLowerCase();
  }
  return out;
}

function luminance(hex) {
  const channel = (i) => {
    const c = parseInt(hex.slice(1 + 2 * i, 3 + 2 * i), 16) / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * channel(0) + 0.7152 * channel(1) + 0.0722 * channel(2);
}

function contrast(a, b) {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

const TEXT_TOKENS = ["--text", "--sev-1", "--sev-2", "--muted"];
const THEMES = {
  light: { app: tokens(app, ":root"), hl: tokens(hl, ":root") },
  dark: { app: tokens(app, ':root[data-theme="dark"]'), hl: tokens(hl, ':root[data-theme="dark"]') },
};

test("contrast helper agrees with known WCAG values", () => {
  assert.equal(Math.round(contrast("#000000", "#ffffff") * 10) / 10, 21);
  assert.equal(contrast("#777777", "#777777"), 1);
});

for (const [theme, set] of Object.entries(THEMES)) {
  test(`every highlight color has enough contrast in the ${theme} theme`, () => {
    for (const color of PALETTE) {
      const background = set.hl["--hl-" + color];
      assert.ok(background, `${theme}: --hl-${color} is defined`);
      for (const token of TEXT_TOKENS) {
        const fg = set.app[token];
        assert.ok(fg, `${theme}: ${token} is defined`);
        const ratio = contrast(fg, background);
        assert.ok(ratio >= 4.5, `${theme}: ${token} ${fg} on --hl-${color} ${background} is ${ratio.toFixed(2)}:1`);
      }
    }
  });
}

test("the OS dark preference uses the same colors as the explicit dark theme", () => {
  const media = hl.slice(hl.indexOf("@media (prefers-color-scheme: dark)"));
  assert.deepEqual(tokens(media, ':root:not([data-theme="light"])'), THEMES.dark.hl);
  assert.equal(Object.keys(THEMES.dark.hl).length, PALETTE.length);
  assert.equal(Object.keys(THEMES.light.hl).length, PALETTE.length);
});

test("every palette color has a row rule and a swatch class", () => {
  for (const color of PALETTE) {
    assert.ok(hl.includes(`.grid-row[data-hl="${color}"], .hl-c-${color} { background: var(--hl-${color}); }`), color);
  }
});
