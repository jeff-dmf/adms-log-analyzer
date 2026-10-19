// UI-22: with no case open (body.no-case) the layout of UI-17 must not bring the side column, the rail, the detail or an
// implicit grid column back, at any width, collapsed or with a drawer open. A small cascade evaluator over the real
// stylesheets in the order index.html links them (ids, classes, > and descendant, @media (width < N px), var()).
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { SHEET_ORDER, inBundle, sheetIndex, pageLinksBundle } from "./fixtures/sheets.mjs";

const read = (name) => fs.readFileSync(new URL("../../src/dmslog/web/" + name, import.meta.url), "utf8").replace(/\r\n/g, "\n");
const strip = (text) => text.replace(/\/\*[\s\S]*?\*\//g, "");

// -- parsing
function blocks(text, media = null, out = []) {
  let i = 0;
  while (i < text.length) {
    const open = text.indexOf("{", i);
    if (open < 0) break;
    let depth = 0;
    let close = open;
    for (; close < text.length; close += 1) {
      if (text[close] === "{") depth += 1;
      else if (text[close] === "}" && --depth === 0) break;
    }
    const head = text.slice(i, open).trim();
    const body = text.slice(open + 1, close);
    if (head.startsWith("@media")) {
      const m = /\(width\s*<\s*(\d+)px\)/.exec(head);
      blocks(body, m ? Number(m[1]) : "other", out);
    } else if (!head.startsWith("@")) {
      const decls = {};
      for (const d of body.split(";")) {
        const k = d.indexOf(":");
        if (k > 0) decls[d.slice(0, k).trim()] = d.slice(k + 1).trim().replace(/\s+/g, " ");
      }
      for (const sel of head.split(",")) out.push({ sel: sel.trim(), decls, media, order: out.length });
    }
    i = close + 1;
  }
  return out;
}

function compound(src) {
  const c = { tag: null, id: null, classes: [] };
  for (const m of src.matchAll(/([#.]?)([\w-]+)/g)) {
    if (m[1] === "#") c.id = m[2];
    else if (m[1] === ".") c.classes.push(m[2]);
    else c.tag = m[2];
  }
  return c;
}
function complex(sel) {
  const parts = [];
  let comb = null;
  for (const token of sel.replace(/>/g, " > ").split(/\s+/).filter(Boolean)) {
    if (token === ">") comb = ">";
    else { parts.push({ c: compound(token), comb }); comb = null; }
  }
  return parts;
}
const spec = (parts) => parts.reduce((s, { c }) => [s[0] + (c.id ? 1 : 0), s[1] + c.classes.length, s[2] + (c.tag ? 1 : 0)], [0, 0, 0]);
const cmp = (a, b) => a[0] - b[0] || a[1] - b[1] || a[2] - b[2];
const fits = (c, el) => (!c.tag || c.tag === el.tag) && (!c.id || c.id === el.id) && c.classes.every((k) => el.classes.includes(k));
function matches(parts, chain, end = chain.length - 1, idx = parts.length - 1) {
  if (idx < 0) return true;
  if (end < 0 || !fits(parts[idx].c, chain[end])) return false;
  if (idx === 0) return true;
  if (parts[idx].comb === ">") return matches(parts, chain, end - 1, idx - 1);
  for (let k = end - 1; k >= 0; k -= 1) if (matches(parts, chain, k, idx - 1)) return true;
  return false;
}

const order = SHEET_ORDER.filter((name) => !name.startsWith(".."));
const rules = order.flatMap((name) => blocks(strip(read("css/" + name)))).map((r, i) => ({ ...r, order: i, parts: complex(r.sel) }))
  .map((r) => ({ ...r, spec: spec(r.parts) }));

// -- evaluation
function style(chain, width) {
  const mine = rules.filter((r) => (r.media === null || (typeof r.media === "number" && width < r.media)) && matches(r.parts, chain));
  const get = (prop) => {
    let best = null;
    for (const r of mine) {
      if (!(prop in r.decls)) continue;
      if (!best || cmp(r.spec, best.spec) > 0 || (cmp(r.spec, best.spec) === 0 && r.order > best.order)) best = r;
    }
    return best ? best.decls[prop] : undefined;
  };
  return get;
}
function resolve(value, vars, depth = 0) {
  if (value === undefined || depth > 8) return value;
  return value.replace(/var\((--[\w-]+)(?:,\s*([^)]+))?\)/g, (all, name, fallback) => {
    const found = vars(name);
    return found === undefined ? (fallback ?? all) : resolve(found, vars, depth + 1);
  });
}

function layout({ width, noCase, panelClasses = [] }) {
  const root = { tag: "html", id: null, classes: [] };
  const body = { tag: "body", id: null, classes: noCase ? ["no-case"] : [] };
  const app = { tag: "div", id: "app", classes: [] };
  const panels = { tag: "main", id: "panels", classes: panelClasses };
  const child = (tag, id, extra = []) => ({ tag, id, classes: extra });
  const upto = [root, body, app, panels];
  const panelsStyle = style(upto, width);
  const rootVars = style([root], width);
  const vars = (name) => panelsStyle(name) ?? rootVars(name);
  const out = { columns: resolve(panelsStyle("grid-template-columns"), vars) };
  for (const [key, el] of [["sources", child("aside", "sources-panel")], ["detail", child("aside", "detail-panel")],
    ["rail", child("button", "side-rail")], ["grid", child("section", "grid-panel")]]) {
    const get = style([...upto, el], width);
    out[key] = { display: resolve(get("display"), vars), area: resolve(get("grid-area"), vars) };
  }
  out.toggle = { display: resolve(style([...upto, child("section", "grid-panel"), child("button", "detail-toggle")], width)("display"), vars) };
  return out;
}

const WIDTHS = [["wide", 1700], ["rail", 1400], ["narrow", 1000]];
const STATES = [[], ["is-side-collapsed"], ["is-detail-collapsed"], ["is-side-collapsed", "is-detail-collapsed"],
  ["is-side-open"], ["is-detail-open"], ["is-side-collapsed", "is-side-open"], ["is-detail-collapsed", "is-detail-open"],
  ["is-side-collapsed", "is-detail-collapsed", "is-side-open", "is-detail-open"]];

test("the evaluator reads the real cascade: with a case the wide layout has three columns and the panels show", () => {
  const wide = layout({ width: 1700, noCase: false });
  assert.match(wide.columns, /^\S+ minmax\(0, 1fr\) \S+$/, "three tracks");
  assert.equal(wide.sources.display, "flex");
  assert.equal(wide.detail.display, "flex");
  assert.equal(wide.rail.display, "none");
  const narrow = layout({ width: 1000, noCase: false });
  assert.equal(narrow.sources.display, "none", "the rail layout hides the panel");
  assert.equal(narrow.rail.display, "flex");
  assert.equal(layout({ width: 1000, noCase: false, panelClasses: ["is-side-open"] }).sources.display, "flex", "an open drawer shows it");
});

for (const [name, width] of WIDTHS) {
  for (const classes of STATES) {
    test(`no case, ${name} (${width}px), #panels ${classes.join(" ") || "plain"}: one column, no side, rail, detail or toggle`, () => {
      const l = layout({ width, noCase: true, panelClasses: classes });
      assert.equal(l.columns, "minmax(0, 1fr)", "one track, no implicit column");
      assert.equal(l.sources.display, "none");
      assert.equal(l.detail.display, "none");
      assert.equal(l.rail.display, "none");
      assert.equal(l.toggle.display, "none");
      assert.equal(l.grid.area, "1 / 1", "the grid area is the one column, not 1 / 2");
    });
  }
}

test("the no-case rules beat the UI-17 rules by specificity, not by file order", () => {
  const css = strip(read("css/case-dialogs.css"));
  assert.ok(order.indexOf("responsive.css") > order.indexOf("case-dialogs.css"), "responsive.css loads after: order cannot be the argument");
  for (const sel of ["body.no-case #panels > #sources-panel", "body.no-case #panels.is-side-open > #sources-panel"]) {
    assert.ok(css.includes(sel), sel);
  }
});
