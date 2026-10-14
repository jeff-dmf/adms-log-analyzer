// Design tokens (css/tokens.css): WCAG contrast of every text and component pair in the light and the dark
// theme, the two dark blocks stay identical, and --row-height matches ROW_HEIGHT of the grid model.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import { ROW_HEIGHT } from "../../src/dmslog/web/js/grid-model.js";
import { literalColors } from "./fixtures/css-named-colors.mjs";
import { compile, computed, contrast as contrastOf, parseRules, resolveColor } from "./fixtures/css-cascade.mjs";

const CSS = new URL("../../src/dmslog/web/css/", import.meta.url);
const tokensCss = readFileSync(new URL("tokens.css", CSS), "utf8");
const appCss = readFileSync(new URL("app.css", CSS), "utf8");
const componentsCss = readFileSync(new URL("components.css", CSS), "utf8");

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

// Light and dark token maps of a tokens.css text (also used on mutated copies by the component audit).
function themesOf(text) {
  const explicit = block(text, SEL_DARK + " {");
  // Everything outside the two dark blocks is the light theme (tokens that do not depend on the theme included).
  const lightText = text.replace(MEDIA_DARK + " {" + block(text, MEDIA_DARK) + "}", "").replace(SEL_DARK + " {" + explicit + "}", "");
  const light = declarations(lightText);
  return { light, dark: { ...light, ...declarations(explicit) } };
}
const THEMES = themesOf(tokensCss);
const { light: LIGHT, dark: DARK } = THEMES;

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

test("components.css has no literal colors (tokens.css is the only source)", () => {
  assert.deepEqual(literalColors(componentsCss), []);
});

// ---- Component audit: pairs come from the declarations components.css actually applies ----------------
// Every element/state below is run through a small cascade (specificity, source order, var(), color-mix(),
// inheritance, combined states such as pressed + hover) and the winning colors are checked. Editing a
// declaration in components.css or a token in tokens.css therefore changes what is tested; the mutation tests
// at the end prove the audit fails when text equals its background, when the primary hover mix is too dark and
// when a variant overrides the pressed state.

const el = (tag, classes = [], attrs = {}, states = []) => ({ tag, classes, attrs, states });
const SURFACES = ["--surface-panel"];

function auditComponents(cssText, themes) {
  const compiled = compile(parseRules(cssText));
  const failures = [];
  let checks = 0;
  const need = (ok, msg) => { checks++; if (!ok) failures.push(msg); };

  for (const [themeName, theme] of Object.entries(themes)) {
    const hex = (name) => resolveColor(`var(${name})`, theme);
    const run = (label, chain, surfaceToken = "--surface-panel") => {
      const c = computed(compiled, chain, theme, hex(surfaceToken));
      if (c.color === null) c.color = hex("--ink"); // no rule sets it: the page color of app.css (body { color: var(--ink) })
      return { ...c, label: `${themeName}: ${label}`, surface: hex(surfaceToken) };
    };
    const text = (c, min = 4.5) => need(c.color !== null && contrastOf(c.color, c.background) >= min, `${c.label}: text ${c.color} on ${c.background} is below ${min}:1`);
    const edge = (c, color, against, what, min = 3) => need(color !== null && contrastOf(color, against) >= min, `${c.label}: ${what} ${color} on ${against} is below ${min}:1`);

    // Buttons: variant x state. Focus and pressed combine with hover; disabled is checked separately.
    for (const variant of [[], ["is-primary"], ["is-ghost"], ["is-danger"]]) {
      const name = variant[0] ?? "secondary";
      for (const pressedAttr of [null, "aria-pressed", "aria-expanded"]) {
        for (const states of [[], ["hover"], ["focus-visible"], ["hover", "focus-visible"]]) {
          const attrs = pressedAttr ? { [pressedAttr]: "true" } : {};
          const c = run(`button ${name} ${pressedAttr ?? "rest"} ${states.join("+")}`, [el("button", ["ui-button", ...variant], attrs, states)]);
          text(c);
          if (pressedAttr) {
            // The state prevails over every variant and over hover.
            need(c.background === hex("--row-selected"), `${c.label}: background ${c.background} is not --row-selected`);
            need(c.border === hex("--accent"), `${c.label}: border ${c.border} is not --accent`);
            need(c.color === hex("--ink"), `${c.label}: text ${c.color} is not --ink`);
            edge(c, c.border, c.background, "pressed border");
            edge(c, c.border, c.surface, "pressed border vs surface");
          } else if (name !== "is-ghost" || states.includes("hover")) {
            // Visible edge or fill against the surface (ghost at rest is borderless by design).
            const edgeColor = c.border && c.border !== c.background ? c.border : c.background;
            if (name === "is-primary") edge(c, c.background, c.surface, "primary fill");
            else edge(c, edgeColor, c.surface, "border", name === "is-ghost" ? 1 : 3);
          }
          if (states.includes("focus-visible")) {
            edge(c, c.outline, c.surface, "focus ring");
            // The ring has a 1 px offset: it is drawn on the surface, not on the fill, so only the surface counts.
          }
        }
      }
      // Disabled: no hover response, readable enough, border flat (--line), never the enabled colors.
      const rest = run(`button ${name} disabled`, [el("button", ["ui-button", ...variant], {}, ["disabled"])]);
      const hover = run(`button ${name} disabled hover`, [el("button", ["ui-button", ...variant], {}, ["disabled", "hover"])]);
      need(JSON.stringify([rest.color, rest.background, rest.border]) === JSON.stringify([hover.color, hover.background, hover.border]), `${rest.label}: hover changes a disabled button`);
      need(rest.color === hex("--ink-disabled"), `${rest.label}: text is ${rest.color}, not --ink-disabled`);
      need(contrastOf(rest.color, rest.background) >= 2, `${rest.label}: disabled text below 2:1`);
    }

    // Fields.
    for (const tag of ["input", "select"]) {
      const cls = `ui-${tag}`;
      for (const [what, attrs, states] of [["rest", {}, []], ["hover", {}, ["hover"]], ["focus", {}, ["focus", "focus-visible"]], ["invalid", { "aria-invalid": "true" }, []]]) {
        const c = run(`${tag} ${what}`, [el(tag, [cls], attrs, states)]);
        text(c);
        edge(c, c.border, c.background, "border");
        edge(c, c.border, c.surface, "border vs surface");
        if (what === "invalid") need(c.border === hex("--sev-error"), `${c.label}: invalid border is not --sev-error`);
        if (what === "focus") edge(c, c.outline, c.surface, "focus ring");
      }
    }
    for (const [cls, token] of [["ui-field-error", "--surface-panel"], ["ui-field-label", "--surface-panel"], ["ui-status", "--surface-panel"], ["ui-empty", "--surface-panel"], ["ui-estimate", "--surface-panel"], ["ui-panel-summary", "--surface-panel"]]) {
      text(run(cls, [el("div", ["ui-panel"]), el("p", [cls])], token));
    }
    const err = run("ui-error", [el("div", ["ui-error"])]);
    text(err);
    edge(err, err.border, err.surface, "border");

    // Badges and chips.
    for (const variant of [[], ["is-sensitive"], ["is-curated"], ["is-estimate"]]) {
      const c = run(`badge ${variant[0] ?? "plain"}`, [el("span", ["ui-badge", ...variant])]);
      text(c);
      edge(c, c.border, c.surface, "border");
    }
    for (const states of [[], ["hover"]]) {
      const chip = run(`chip ${states.join("+")}`, [el("span", ["ui-chip"], {}, states)]);
      text(chip);
      edge(chip, chip.border, chip.surface, "border");
    }
    const chipPressed = run("chip button pressed", [el("button", ["ui-chip"], { "aria-pressed": "true" })]);
    text(chipPressed);
    edge(chipPressed, chipPressed.border, chipPressed.surface, "border");
    for (const states of [[], ["hover"], ["focus-visible"]]) {
      const c = run(`chip remove ${states.join("+")}`, [el("span", ["ui-chip"]), el("button", ["ui-chip-remove"], {}, states)]);
      text(c);
      if (states.includes("focus-visible")) edge(c, c.outline, c.surface, "focus ring");
    }

    // Menu: item states, shortcut, border. The text sits on the overlay, hover and selection fills.
    const menu = el("div", ["ui-menu"]);
    const menuBox = run("menu", [menu], "--surface-overlay");
    text(menuBox);
    edge(menuBox, menuBox.border, menuBox.surface, "border");
    for (const [what, attrs, states] of [["rest", {}, []], ["hover", {}, ["hover"]], ["focus", {}, ["focus"]], ["focus-visible", {}, ["focus", "focus-visible"]], ["checked", { "aria-checked": "true" }, []]]) {
      const item = run(`menu item ${what}`, [menu, el("div", ["ui-menu-item"], attrs, states)], "--surface-overlay");
      text(item);
      const key = run(`menu shortcut ${what}`, [menu, el("div", ["ui-menu-item"], attrs, states), el("span", ["ui-menu-shortcut"])], "--surface-overlay");
      text(key);
      if (states.includes("focus-visible")) {
        edge(item, item.outline, item.background, "focus ring on the item");
      }
    }
    for (const states of [[], ["hover"]]) {
      const item = run(`menu item disabled ${states.join("+")}`, [menu, el("div", ["ui-menu-item"], { "aria-disabled": "true" }, states)], "--surface-overlay");
      need(item.color === hex("--ink-disabled"), `${item.label}: text is ${item.color}, not --ink-disabled`);
      need(item.background === hex("--surface-overlay"), `${item.label}: hover fills a disabled item (${item.background})`);
      need(contrastOf(item.color, item.background) >= 2, `${item.label}: below 2:1`);
    }

    // Pseudo-elements: the check and radio marks and the submenu arrow are drawn with ::before.
    const pseudo = (host) => ({ ...host, pseudo: "before", host });
    for (const [what, states] of [["rest", []], ["hover", ["hover"]], ["focus-visible", ["focus", "focus-visible"]]]) {
      const item = el("div", ["ui-menu-item"], { "aria-checked": "true" }, states);
      text(run(`menu check mark ${what}`, [menu, item, pseudo(item)], "--surface-overlay"));
      const radio = el("div", ["ui-menu-item"], { role: "menuitemradio", "aria-checked": "true" }, states);
      text(run(`menu radio mark ${what}`, [menu, radio, pseudo(radio)], "--surface-overlay"));
      const parent = el("div", ["ui-menu-item"], {}, states);
      const arrow = el("span", ["ui-menu-arrow"]);
      text(run(`menu arrow ${what}`, [menu, parent, arrow, pseudo(arrow)], "--surface-overlay"));
    }

    // Table, dialog.
    const table = el("table", ["ui-table"]);
    const th = run("table header", [table, el("thead"), el("tr"), el("th")]);
    text(th);
    text(run("table cell", [table, el("tbody"), el("tr"), el("td")]));
    text(run("table cell on a hovered row", [table, el("tbody"), el("tr", [], {}, ["hover"]), el("td")]));
    const dialog = run("dialog", [el("dialog", ["ui-dialog"])], "--surface-overlay");
    text(dialog);
    edge(dialog, dialog.border, dialog.surface, "border");
  }
  return { failures, checks };
}

const COMPONENTS_CSS = readFileSync(new URL("components.css", CSS), "utf8");

test("component states: the colors components.css applies meet 4.5:1 for text and 3:1 for edges, in both themes", () => {
  const { failures, checks } = auditComponents(COMPONENTS_CSS, THEMES);
  assert.deepEqual(failures, []);
  assert.ok(checks > 250, `the audit is not vacuous (${checks} checks)`);
});

test("component states: the audit reads CRLF stylesheets the same way", () => {
  const { failures } = auditComponents(COMPONENTS_CSS.replace(/\r?\n/g, "\r\n"), THEMES);
  assert.deepEqual(failures, []);
});

test("--accent-hover is --accent mixed 8% with black, in both themes", () => {
  for (const [name, theme] of Object.entries(THEMES)) {
    const accent = resolve(theme, "--accent");
    const expected = "#" + [1, 3, 5].map((i) => Math.round(parseInt(accent.slice(i, i + 2), 16) * 0.92).toString(16).padStart(2, "0")).join("");
    assert.equal(resolve(theme, "--accent-hover"), expected, name);
  }
});

test("components.css mixes colors only with tokens or transparent (no color keyword inside color-mix)", () => {
  const css = COMPONENTS_CSS.replace(/\r\n?/g, "\n").replace(/\/\*[\s\S]*?\*\//g, "");
  for (const m of css.matchAll(/color-mix\(([^;]*)\)/g)) {
    const words = m[1].match(/(?<![-\w(])[a-zA-Z]+(?![-\w(])/g) ?? [];
    for (const w of words) assert.ok(["in", "srgb", "transparent"].includes(w), `color-mix uses ${w}`);
  }
});

test("mutations: the audit fails when text equals its background, when the primary hover mix is too dark, and when a variant overrides pressed", () => {
  // A: menu text drawn in the menu background color.
  const a = COMPONENTS_CSS.replace(/(\.ui-menu \{[^}]*?)color: var\(--ink\);/, "$1color: var(--surface-overlay);");
  assert.notEqual(a, COMPONENTS_CSS);
  assert.ok(auditComponents(a, THEMES).failures.some((f) => f.includes("menu item rest")), "A");
  // B: --accent-hover at 95% black (dark theme: --accent-ink on it falls under 4.5:1).
  const tokensB = tokensCss.replaceAll("--accent-hover: #709aeb;", "--accent-hover: #06080d;");
  assert.notEqual(tokensB, tokensCss);
  assert.ok(auditComponents(COMPONENTS_CSS, themesOf(tokensB)).failures.some((f) => f.includes("dark: button is-primary rest hover")), "B");
  // C: the pressed block removed, so variants and hover override the state.
  const c = COMPONENTS_CSS.replace(/\.ui-button:is\(\[aria-pressed="true"\][^{]*\{[^}]*\}/, "");
  assert.notEqual(c, COMPONENTS_CSS);
  const failC = auditComponents(c, THEMES).failures;
  assert.ok(failC.some((f) => f.includes("is-primary aria-pressed")) && failC.some((f) => f.includes("is-danger aria-pressed")), "C");
  // D: the pressed block loses the accent border.
  const d = COMPONENTS_CSS.replace(/(\.ui-button:is\(\[aria-pressed="true"\][^{]*\{[^}]*?)border-color: var\(--accent\);/, "$1border-color: var(--line);");
  assert.notEqual(d, COMPONENTS_CSS);
  assert.ok(auditComponents(d, THEMES).failures.some((f) => f.includes("is not --accent")), "D");
  // E: a menu hover fill equal to the text.
  const e = COMPONENTS_CSS.replace(/(\.ui-menu-item:hover,\s*\.ui-menu-item:focus \{\s*)background: var\(--row-hover\);/, "$1background: var(--ink);");
  assert.notEqual(e, COMPONENTS_CSS);
  assert.ok(auditComponents(e, THEMES).failures.some((f) => f.includes("menu item hover")), "E");
  // F: hovering a disabled button changes it.
  const f = COMPONENTS_CSS.replace(/\.ui-button:disabled:hover,\s*\.ui-button\[aria-disabled="true"\]:hover \{[^}]*\}/, "");
  assert.notEqual(f, COMPONENTS_CSS);
  assert.ok(auditComponents(f, THEMES).failures.some((m) => m.includes("hover changes a disabled button")), "F");
});

// ---- Forced colors (Windows high contrast): the same cascade with the forced-colors block applied --------
const FORCED_PALETTES = {
  "high contrast black": { Canvas: "#000000", CanvasText: "#ffffff", ButtonFace: "#000000", ButtonText: "#ffffff", Highlight: "#1aebff", HighlightText: "#000000", GrayText: "#3ff23f" },
  "high contrast white": { Canvas: "#ffffff", CanvasText: "#000000", ButtonFace: "#ffffff", ButtonText: "#000000", Highlight: "#37006e", HighlightText: "#ffffff", GrayText: "#600000" },
};

function auditForced(cssText) {
  const compiled = compile(parseRules(cssText), { forced: true });
  const failures = [];
  let checks = 0;
  const need = (ok, msg) => { checks++; if (!ok) failures.push(msg); };
  for (const [palette, theme] of Object.entries(FORCED_PALETTES)) {
    const run = (label, chain) => ({ ...computed(compiled, chain, theme, theme.Canvas, true), label: `${palette}: ${label}` });
    for (const variant of [[], ["is-primary"], ["is-ghost"], ["is-danger"]]) {
      const name = variant[0] ?? "secondary";
      const rest = run(`button ${name} rest`, [el("button", ["ui-button", ...variant])]);
      for (const attr of ["aria-pressed", "aria-expanded"]) {
        for (const states of [[], ["hover"], ["focus-visible"], ["hover", "focus-visible"]]) {
          const c = run(`button ${name} ${attr} ${states.join("+")}`, [el("button", ["ui-button", ...variant], { [attr]: "true" }, states)]);
          // The state is a Highlight border in every combination, distinct from a resting button and from the page.
          need(c.border === theme.Highlight, `${c.label}: border is ${c.border}, not Highlight`);
          need(c.border !== rest.border, `${c.label}: border does not differ from the resting button`);
          need(contrastOf(c.border, theme.Canvas) >= 3, `${c.label}: Highlight border below 3:1 on Canvas`);
          need(contrastOf(c.color, c.background) >= 4.5, `${c.label}: text ${c.color} on ${c.background} below 4.5:1`);
          // Focus is a separate outline, so a focused pressed button keeps the border and adds the ring.
          if (states.includes("focus-visible")) need(c.outline === theme.Highlight && contrastOf(c.outline, theme.Canvas) >= 3, `${c.label}: focus ring is ${c.outline}`);
          else need(c.outline === null || c.outline === c.border && false, `${c.label}: an outline without focus (${c.outline})`);
        }
      }
      const focus = run(`button ${name} focus`, [el("button", ["ui-button", ...variant], {}, ["focus-visible"])]);
      need(focus.outline === theme.Highlight, `${focus.label}: no Highlight ring`);
      for (const states of [[], ["hover"]]) {
        const d = run(`button ${name} disabled ${states.join("+")}`, [el("button", ["ui-button", ...variant], {}, ["disabled", ...states])]);
        need(d.color === theme.GrayText && d.border !== theme.Highlight, `${d.label}: not GrayText`);
      }
    }
    const menu = el("div", ["ui-menu"]);
    for (const [what, states] of [["hover", ["hover"]], ["focus", ["focus"]], ["focus-visible", ["focus", "focus-visible"]]]) {
      const c = run(`menu item ${what}`, [menu, el("div", ["ui-menu-item"], {}, states)]);
      need(c.background === theme.Highlight && contrastOf(c.color, c.background) >= 4.5, `${c.label}: text ${c.color} on ${c.background} below 4.5:1`);
      if (states.includes("focus-visible")) need(c.outline !== null && contrastOf(c.outline, c.background) >= 3, `${c.label}: inner ring ${c.outline} on ${c.background} below 3:1`);
    }
    for (const states of [[], ["hover"]]) {
      const c = run(`menu item disabled ${states.join("+")}`, [menu, el("div", ["ui-menu-item"], { "aria-disabled": "true" }, states)]);
      need(c.color === theme.GrayText && c.background === theme.Canvas, `${c.label}: ${c.color} on ${c.background}`);
    }
  }
  return { failures, checks };
}

test("forced colors: pressed, expanded, focus and menu states keep distinct system-color indicators", () => {
  const { failures, checks } = auditForced(COMPONENTS_CSS);
  assert.deepEqual(failures, []);
  assert.ok(checks > 100, `${checks} checks`);
  assert.deepEqual(auditForced(COMPONENTS_CSS.replace(/\r?\n/g, "\r\n")).failures, []);
});

test("mutations (forced colors, pseudo-elements, literal colors)", () => {
  // G1: the pressed block loses its :hover selector in the forced block: the normal hover rule wins again.
  const g1 = COMPONENTS_CSS.replace(/(@media \(forced-colors: active\)[\s\S]*?)\.ui-button:is\(\[aria-pressed="true"\], \[aria-expanded="true"\]\):not\(:disabled, \[aria-disabled="true"\]\):hover,\n/, "$1");
  assert.notEqual(g1, COMPONENTS_CSS);
  assert.ok(auditForced(g1).failures.some((f) => f.includes("pressed") && f.includes("hover") && f.includes("not Highlight")), "G1");
  // G2: the focused menu item ring is Highlight on a Highlight fill.
  const g2 = COMPONENTS_CSS.replace("outline: 2px solid HighlightText;", "outline: 2px solid Highlight;");
  assert.notEqual(g2, COMPONENTS_CSS);
  assert.ok(auditForced(g2).failures.some((f) => f.includes("inner ring")), "G2");
  // G3: pressed + focus loses the border (focus rule overrides the state).
  const g3 = COMPONENTS_CSS.replace("    border-color: Highlight;\n    border-width: 3px;", "    border-width: 3px;");
  assert.notEqual(g3, COMPONENTS_CSS);
  assert.ok(auditForced(g3).failures.some((f) => f.includes("focus-visible") && f.includes("Highlight")), "G3");
  // P: the submenu arrow drawn in the menu background color (pseudo-element rule).
  const p = COMPONENTS_CSS.replace(/(\.ui-menu-arrow::before \{[^}]*?)color: var\(--ink-muted\);/, "$1color: var(--surface-overlay);");
  assert.notEqual(p, COMPONENTS_CSS);
  assert.ok(auditComponents(p, THEMES).failures.some((f) => f.includes("menu arrow")), "P");
  // P2: the check mark colored like the hover fill.
  const p2 = COMPONENTS_CSS.replace(/(\.ui-menu-item\[aria-checked="true"\]::before \{)/, "$1\n  color: var(--row-hover);");
  assert.notEqual(p2, COMPONENTS_CSS);
  assert.ok(auditComponents(p2, THEMES).failures.some((f) => f.includes("check mark")), "P2");
  // L: any CSS named color or color function reaches the literal-color guard.
  for (const bad of ["coral", "rebeccapurple", "Tomato", "oklch(0.5 0.1 20)", "#abc"]) {
    assert.ok(literalColors(COMPONENTS_CSS + `\n.x { color: ${bad}; }`).length > 0, bad);
  }
});

for (const theme of Object.keys(THEMES)) {
  test(`${theme}: a disabled control stays distinguishable from an enabled one without opacity`, () => {
    // Disabled text is exempt from the 4.5:1 rule (WCAG 1.4.3), but it must not equal enabled text, and the
    // not-allowed cursor, the flat border and the missing hover response carry the state as well.
    const t = THEMES[theme];
    assert.notEqual(resolve(t, "--ink-disabled"), resolve(t, "--ink"));
    assert.notEqual(resolve(t, "--line"), resolve(t, "--line-strong"));
    assert.ok(contrast(resolve(t, "--ink-disabled"), resolve(t, "--surface-raised")) >= 2, "readable enough to read the label");
  });

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
    check(theme, "--hl-bar", "--row-selected", 3); // the highlight band beside the selection bar
    check(theme, "--hl-bar", "--surface-panel", 3);
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
