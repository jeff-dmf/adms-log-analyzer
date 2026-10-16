// Row selection (UI-07): the selection stays visible under highlights and abnormal rows, and has an
// outline-based variant for Windows high contrast (forced-colors), where box-shadow and fills are replaced.
// The checks resolve the winning declaration (simple specificity, then source order across the style sheets
// in load order), so a later or stronger rule that undoes the selection makes them fail. Line endings (CRLF
// from autocrlf on Windows) and spacing do not matter. Mutation tests prove the checks can fail.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const DIR = new URL("../../src/dmslog/web/css/", import.meta.url);
// Load order: index.html links these in this order; highlight.css is injected last by the highlight panel.
const FILES = ["app.css", "bookmarks.css", "collections.css", "highlight.css"];
const read = (name) => readFileSync(new URL(name, DIR), "utf8");

// ---- Parser ------------------------------------------------------------------------------------------

const clean = (text) => text.replace(/\r\n?/g, "\n").replace(/\/\*[\s\S]*?\*\//g, "");

function matchBrace(text, open) {
  let depth = 0;
  for (let i = open; i < text.length; i++) {
    if (text[i] === "{") depth++;
    else if (text[i] === "}" && --depth === 0) return i;
  }
  throw new Error("unbalanced braces");
}

// Flat rule list: { selector, decls: Map(prop -> value), media: null | "forced" | "other" }.
function parseRules(text, media = null, out = []) {
  const css = clean(text);
  let i = 0;
  while (i < css.length) {
    const open = css.indexOf("{", i);
    if (open < 0) break;
    const head = css.slice(i, open).trim();
    const close = matchBrace(css, open);
    const body = css.slice(open + 1, close);
    if (head.startsWith("@media")) {
      const forced = /\(\s*forced-colors\s*:\s*active\s*\)/.test(head);
      parseRules(body, forced ? "forced" : "other", out);
    } else if (!head.startsWith("@")) {
      const decls = new Map();
      for (const part of body.split(";")) {
        const colon = part.indexOf(":");
        if (colon < 0) continue;
        decls.set(part.slice(0, colon).trim().toLowerCase(), part.slice(colon + 1).replace(/\s+/g, " ").trim());
      }
      for (const selector of head.split(",")) out.push({ selector: selector.replace(/\s+/g, " ").trim(), decls, media });
    }
    i = close + 1;
  }
  return out;
}

// Simple selectors of a (possibly descendant) selector: classes, attributes, pseudo-classes/elements, tags.
const tokens = (selector) =>
  selector.match(/\[[^\]]+\]|::?[\w-]+|\.[\w-]+|#[\w-]+|(?<![\w-])[a-z][\w-]*/g)?.map((t) => t.replace(/\s+/g, "")) ?? [];

function specificity(selector) {
  let a = 0, b = 0, c = 0;
  for (const t of tokens(selector)) {
    if (t.startsWith("#")) a++;
    else if (t.startsWith("::") || /^[a-z]/.test(t)) c++;
    else b++;
  }
  return a * 10000 + b * 100 + c;
}

function sheets(files) {
  const rules = [];
  FILES.forEach((name) => parseRules(files[name], null, rules));
  return rules;
}

// Winning value of `prop` for an element described by its selector (state attributes included), among the
// rules whose simple selectors all occur in it; `forced` also lets the forced-colors rules apply.
function winner(rules, target, prop, { forced = false } = {}) {
  const targetParts = target.split(" ");
  const lastHave = new Set(tokens(targetParts.at(-1)));
  const upHave = new Set(tokens(targetParts.slice(0, -1).join(" ")));
  let best = null;
  rules.forEach((rule, order) => {
    if (rule.media === "other" || (rule.media === "forced" && !forced)) return;
    if (!rule.decls.has(prop)) return;
    // Descendant combinators only: the last compound styles the target itself, earlier ones its ancestors.
    const parts = rule.selector.split(" ");
    if (!tokens(parts.at(-1)).every((tk) => lastHave.has(tk))) return;
    if (!tokens(parts.slice(0, -1).join(" ")).every((tk) => upHave.has(tk))) return;
    const rank = [specificity(rule.selector), order];
    if (!best || rank[0] > best.rank[0] || (rank[0] === best.rank[0] && rank[1] > best.rank[1])) {
      best = { rank, value: rule.decls.get(prop) };
    }
  });
  return best?.value;
}

// ---- Checks (throw on violation, so mutations can be asserted) ------------------------------------------

const ROW_STATES = [
  ".grid-row.selected",
  ".grid-row.selected:hover",
  '.grid-row.selected[data-hl="yellow"][data-hl]',
  '.grid-row.selected[data-hl="yellow"][data-hl]:hover',
  '.grid-row.selected[data-hl][data-hl="yellow"][data-sev="1"]',
  '.grid-row.selected[data-sev="2"]',
];
const LAYOUT_PROPS = ["padding", "margin", "border", "border-left", "width", "height", "min-width", "line-height"];

function checkNormal(rules) {
  for (const row of ROW_STATES) {
    const shadow = winner(rules, row, "box-shadow");
    assert.match(shadow ?? "", /inset 3px 0 0 var\(--accent\)/, `${row}: box-shadow keeps the 3 px accent bar (${shadow})`);
    if (row.includes("data-hl")) {
      assert.match(shadow, /inset 7px 0 0 var\(--hl-bar\)/, `${row}: the highlight band sits beside the bar`);
      assert.equal(winner(rules, row, "background"), "var(--row-selected)", `${row}: the selection fill wins over the highlight`);
    } else {
      assert.equal(winner(rules, row, "background"), "var(--row-selected)", `${row}: selection fill`);
    }
    for (const prop of LAYOUT_PROPS) assert.equal(winner(rules, row, prop), winner(rules, ".grid-row", prop), `${row}: no ${prop} change`);
  }
  assert.match(winner(rules, '.grid-row[data-hl="yellow"][data-hl]', "box-shadow"), /inset 4px 0 0 var\(--hl-bar\)/);
  for (const sev of ["1", "2"]) {
    assert.equal(winner(rules, `.grid-row[data-sev="${sev}"]`, "background"), "var(--abn-row)");
    assert.equal(winner(rules, `.grid-row[data-sev="${sev}"] .grid-cell.col-level`, "font-weight"), "600");
  }
  assert.match(winner(rules, ".grid-viewport:focus-visible .grid-row.selected", "outline"), /var\(--focus-ring\)/);
}

const CELLS = [
  ".grid-cell",
  ".grid-cell.col-level",
  ".grid-cell.col-msg",
  '[data-sev="1"] .grid-cell.col-level',
  '[data-sev="2"] .grid-cell.col-msg',
  "[data-bm] .grid-cell:first-child",
  '[data-bm][data-bm="red"] .grid-cell:first-child',
];

function checkForced(rules) {
  const f = { forced: true };
  const selectedRows = [".grid-row.selected", ".grid-row.selected:hover", '.grid-row.selected[data-hl="yellow"][data-hl]', '.grid-row.selected[data-hl="yellow"][data-hl]:hover'];
  for (const row of selectedRows) {
    assert.equal(winner(rules, row, "background", f), "Highlight", `${row}: Highlight fill`);
    assert.equal(winner(rules, row, "box-shadow", f), "none", `${row}: the normal box-shadows are cleared`);
    assert.equal(winner(rules, row, "forced-color-adjust", f), "none");
    assert.match(winner(rules, row, "outline", f), /^2px solid Highlight$/);
    assert.match(winner(rules, `.grid-viewport:focus-visible ${row}`, "outline", f), /^2px solid HighlightText$/, `${row}: focus ring in a system color`);
    for (const cell of CELLS) {
      const target = `${row} ${cell}`.replace(/\.grid-row(\S*) \[/, ".grid-row$1[");
      assert.equal(winner(rules, target, "color", f), "HighlightText", `${target}: HighlightText`);
    }
    assert.equal(winner(rules, `${row} .grid-cell.col-collection[data-tag][data-tag="1"]`, "background", f), "transparent");
    assert.equal(winner(rules, `${row} .grid-cell.col-collection[data-tag][data-tag="1"]`, "color", f), "HighlightText");
    assert.equal(winner(rules, `${row} .grid-cell.col-source[data-badge]::before`, "border-color", f), "HighlightText", `${row}: S badge border`);
    assert.equal(winner(rules, `${row} mark`, "background", f), "HighlightText");
    assert.equal(winner(rules, `${row} mark`, "color", f), "Highlight");
    for (const prop of LAYOUT_PROPS) assert.equal(winner(rules, row, prop, f), winner(rules, ".grid-row", prop), `${row}: no ${prop} in forced colors`);
  }
  const hlRow = '.grid-row[data-hl="yellow"][data-hl]';
  assert.equal(winner(rules, hlRow, "box-shadow", f), "none");
  assert.equal(winner(rules, hlRow, "background", f), "Canvas");
  assert.match(winner(rules, hlRow, "background-image", f), /Mark 0, Mark 4px/);
  for (const cell of CELLS) {
    const target = `${hlRow} ${cell}`.replace(/\.grid-row(\S*) \[/, ".grid-row$1[");
    assert.equal(winner(rules, target, "color", f), "CanvasText", `${target}: CanvasText`);
  }
  assert.equal(winner(rules, `${hlRow} mark`, "background", f), "Mark");
  assert.equal(winner(rules, `${hlRow} mark`, "color", f), "MarkText");
  assert.equal(winner(rules, `${hlRow} .grid-cell.col-collection[data-tag][data-tag="1"]`, "background", f), "transparent");
  assert.ok(winner(rules, '[aria-pressed="true"]', "border", f)?.includes("ButtonText"));
}

const load = () => Object.fromEntries(FILES.map((n) => [n, read(n)]));
const crlf = (s) => s.replace(/\r?\n/g, "\r\n");

// ---- Tests ---------------------------------------------------------------------------------------------

test("normal mode: selection bar, fill and highlight band survive the cascade", () => {
  checkNormal(sheets(load()));
});

test("forced-colors: system pairs on the row and on every relevant descendant", () => {
  checkForced(sheets(load()));
});

test("the same checks pass on a CRLF copy (Windows checkout with autocrlf)", () => {
  const files = Object.fromEntries(Object.entries(load()).map(([n, s]) => [n, crlf(s)]));
  const rules = sheets(files);
  checkNormal(rules);
  checkForced(rules);
});

test("the parser handles CRLF, spacing and nested @media like the LF original", () => {
  const lf = sheets(load()).map((r) => [r.selector, r.media, [...r.decls]]);
  const win = sheets(Object.fromEntries(Object.entries(load()).map(([n, s]) => [n, crlf(s)]))).map((r) => [r.selector, r.media, [...r.decls]]);
  assert.deepEqual(win, lf);
  assert.ok(lf.some(([, media]) => media === "forced"));
});

// Mutations: each one must break at least one check (names say what the mutation breaks).
const mutations = {
  "padding added to the selection with a highlight": (f) => ({ ...f, "highlight.css": f["highlight.css"].replace(/(\.grid-row\.selected\[data-hl\],\s*\.grid-row\.selected\[data-hl\]:hover \{)/, "$1\n  padding: 12px;") }),
  "box-shadow none after the bar": (f) => ({ ...f, "highlight.css": f["highlight.css"] + "\n.grid-row.selected { box-shadow: none; }\n" }),
  "box-shadow none on the highlight selection": (f) => ({ ...f, "highlight.css": f["highlight.css"] + "\n.grid-row.selected[data-hl] { box-shadow: none; }\n" }),
  "selection fill lost under the highlight": (f) => ({ ...f, "highlight.css": f["highlight.css"] + '\n.grid-row.selected[data-hl="yellow"] { background: var(--hl-yellow); }\n' }),
  "selection bar removed": (f) => ({ ...f, "app.css": f["app.css"].replace(/box-shadow: inset 3px 0 0 var\(--accent\);/g, "") }),
  "forced-colors block removed from app.css": (f) => ({ ...f, "app.css": f["app.css"].replace("@media (forced-colors: active)", "@media (forced-colors: none)") }),
  "forced box-shadow not cleared": (f) => ({ ...f, "app.css": f["app.css"].replace(/(@media \(forced-colors: active\)[\s\S]*?)box-shadow: none;/, "$1") }),
  "forced focus ring overridden": (f) => ({ ...f, "app.css": f["app.css"].replace("outline: 2px solid HighlightText;", "outline: 1px solid var(--focus-ring);") }),
  "bookmark color wins in the forced selection": (f) => ({ ...f, "bookmarks.css": f["bookmarks.css"] + '\n@media (forced-colors: active) { .grid-row.selected[data-bm="red"] .grid-cell.grid-cell:first-child { color: red; } }\n' }),
  "S badge keeps its author border in the forced selection": (f) => ({ ...f, "app.css": f["app.css"].replace(/\.grid-row\.selected \.col-source\[data-badge\]::before \{[^}]*\}/, "") }),
  "mark keeps the yellow fill in the forced selection": (f) => ({ ...f, "app.css": f["app.css"].replace(/\.grid-row\.selected mark \{[^}]*\}/, "") }),
};

for (const [name, mutate] of Object.entries(mutations)) {
  for (const [label, wrap] of [["LF", (s) => s], ["CRLF", crlf]]) {
    test(`mutation (${label}): ${name} is detected`, () => {
      const files = Object.fromEntries(Object.entries(mutate(load())).map(([n, s]) => [n, wrap(s)]));
      assert.throws(() => {
        const rules = sheets(files);
        checkNormal(rules);
        checkForced(rules);
      }, `the mutation "${name}" must fail a check`);
    });
  }
}
