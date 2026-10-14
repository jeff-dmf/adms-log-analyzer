// Design tokens (css/tokens.css): WCAG contrast of every text and component pair in the light and the dark
// theme, the two dark blocks stay identical, and --row-height matches ROW_HEIGHT of the grid model.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import { ROW_HEIGHT } from "../../src/dmslog/web/js/grid-model.js";

const CSS = new URL("../../src/dmslog/web/css/", import.meta.url);
const tokensCss = readFileSync(new URL("tokens.css", CSS), "utf8");
const appCss = readFileSync(new URL("app.css", CSS), "utf8");

const MEDIA_DARK = '@media (prefers-color-scheme: dark)';
const SEL_MEDIA = ':root:not([data-theme="light"])';
const SEL_DARK = ':root[data-theme="dark"]';

// Text between the `{` that follows `marker` (searched from `from`) and its matching `}`.
function block(css, marker, from = 0) {
  const start = css.indexOf(marker, from);
  assert.ok(start >= 0, `${marker} is present`);
  const open = css.indexOf("{", start);
  let depth = 0;
  for (let i = open; i < css.length; i++) {
    if (css[i] === "{") depth++;
    if (css[i] === "}" && --depth === 0) return css.slice(open + 1, i);
  }
  assert.fail(`${marker} is not closed`);
}

function declarations(body) {
  const out = {};
  for (const m of body.matchAll(/(--[\w-]+)\s*:\s*([^;]+);/g)) out[m[1]] = m[2].trim();
  return out;
}

const normalize = (body) => body.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\s+/g, " ").trim();

const darkMediaBody = block(block(tokensCss, MEDIA_DARK), SEL_MEDIA + " {");
const darkExplicitBody = block(tokensCss, SEL_DARK + " {");
// Everything outside the two dark blocks is the light theme (tokens that do not depend on the theme included).
const lightCss = tokensCss.replace(MEDIA_DARK + " {" + block(tokensCss, MEDIA_DARK) + "}", "").replace(
  SEL_DARK + " {" + darkExplicitBody + "}",
  "",
);
const LIGHT = declarations(lightCss);
const DARK = { ...LIGHT, ...declarations(darkExplicitBody) };
const THEMES = { light: LIGHT, dark: DARK };

// Resolves `var(--x)` chains; only hex values are accepted at the end.
function resolve(theme, name) {
  let value = theme[name];
  for (let i = 0; i < 5 && value && value.startsWith("var("); i++) value = theme[value.slice(4, -1).trim()];
  assert.match(value ?? "", /^#[0-9a-f]{6}$/i, `${name} resolves to a hex color`);
  return value.toLowerCase();
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

function check(themeName, fgName, bgName, min) {
  const theme = THEMES[themeName];
  const fg = resolve(theme, fgName);
  const bg = resolve(theme, bgName);
  const ratio = contrast(fg, bg);
  assert.ok(ratio >= min, `${themeName}: ${fgName} ${fg} on ${bgName} ${bg} is ${ratio.toFixed(2)}:1 (needs ${min}:1)`);
  return ratio;
}

const CATS = [1, 2, 3, 4, 5, 6, 7, 8];
const TEXT_ON = ["--surface-panel", "--row-selected", "--row-hover", "--abn-row"];
const TEXT = ["--ink", "--ink-muted", "--sev-critical", "--sev-error", "--sev-warning", "--sev-info", "--sev-noise"];

test("contrast helper agrees with known WCAG values", () => {
  assert.equal(Math.round(contrast("#000000", "#ffffff") * 10) / 10, 21);
  assert.equal(contrast("#777777", "#777777"), 1);
});

test("the two dark blocks are identical", () => {
  assert.ok(Object.keys(declarations(darkExplicitBody)).length > 20, "the dark block defines the theme");
  assert.deepEqual(declarations(darkMediaBody), declarations(darkExplicitBody));
  assert.equal(normalize(darkMediaBody), normalize(darkExplicitBody));
});

test("every color the light theme defines is redefined by the dark theme (or derived)", () => {
  const shared = new Set(["--sev-info", "--sev-noise", "--ink-disabled"]);
  for (const [name, value] of Object.entries(LIGHT)) {
    if (!/^#|^rgb/.test(value) || shared.has(name) || name.endsWith("-fg")) continue;
    assert.ok(name in declarations(darkExplicitBody), `${name} has a dark value`);
  }
});

test("--row-height matches ROW_HEIGHT of the grid model", () => {
  assert.equal(LIGHT["--row-height"], `${ROW_HEIGHT}px`);
  assert.equal(DARK["--row-height"], `${ROW_HEIGHT}px`);
});

test("app.css has no literal colors (tokens.css is the only source)", () => {
  const stripped = appCss.replace(/\/\*[\s\S]*?\*\//g, "");
  assert.doesNotMatch(stripped, /#[0-9a-fA-F]{3,8}\b/);
  assert.doesNotMatch(stripped, /\b(?:rgba?|hsla?)\(/);
});

for (const theme of Object.keys(THEMES)) {
  test(`${theme}: text has at least 4.5:1 on panel, selection, hover and abnormal rows`, () => {
    for (const bg of TEXT_ON) for (const fg of TEXT) check(theme, fg, bg, 4.5);
  });

  test(`${theme}: text on status, filter, mark and highlight surfaces has at least 4.5:1`, () => {
    check(theme, "--status-info-ink", "--status-info-bg", 4.5);
    check(theme, "--status-error-ink", "--status-error-bg", 4.5);
    check(theme, "--filtered-ink", "--filtered-bg", 4.5);
    check(theme, "--accent-ink", "--accent", 4.5);
    check(theme, "--ink", "--mark", 4.5);
    check(theme, "--ink", "--hl-bg", 4.5);
    check(theme, "--ink", "--surface-raised", 4.5);
    check(theme, "--ink", "--surface-overlay", 4.5);
    check(theme, "--ink", "--surface-field", 4.5);
  });

  test(`${theme}: categorical fills keep --ink at 4.5:1 and strokes keep 3:1 on the panel`, () => {
    for (const n of CATS) {
      for (const fg of ["--ink", "--ink-muted", "--sev-critical", "--sev-error", "--sev-warning"]) check(theme, fg, `--cat-${n}-bg`, 4.5);
      check(theme, `--cat-${n}`, "--surface-panel", 3);
      check(theme, `--cat-${n}-fg`, "--surface-panel", 3);
    }
  });

  test(`${theme}: borders, selection bar, focus ring and charts have at least 3:1`, () => {
    check(theme, "--line-strong", "--surface-panel", 3);
    check(theme, "--line-strong", "--surface-field", 3);
    check(theme, "--accent", "--row-selected", 3);
    check(theme, "--focus-ring", "--surface-panel", 3);
    for (const chart of ["critical", "error", "warning", "normal", "noise"]) check(theme, `--chart-${chart}`, "--surface-panel", 3);
  });
}

test("--chart-noise stays more discreet than --chart-normal on the panel", () => {
  for (const theme of Object.keys(THEMES)) {
    const panel = resolve(THEMES[theme], "--surface-panel");
    assert.ok(contrast(resolve(THEMES[theme], "--chart-noise"), panel) < contrast(resolve(THEMES[theme], "--chart-normal"), panel), theme);
  }
});

test("dark: the tightest pair (Critical on the selection) keeps at least 4.7:1", () => {
  assert.ok(check("dark", "--sev-critical", "--row-selected", 4.7) >= 4.7);
});
