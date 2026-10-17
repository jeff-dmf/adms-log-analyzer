// UI-17: the responsive stylesheet and the page that carries it. CRLF is normalized (autocrlf on Windows).
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";

import { DETAIL_DRAWER_QUERY, SIDE_RAIL_QUERY } from "../../src/dmslog/web/js/drawers-model.js";
import { LAYOUT_BY_MODE, RAIL_MIN_WIDTH, WIDE_MIN_WIDTH } from "../../src/dmslog/web/js/column-resize-model.js";

const read = (name) => fs.readFileSync(new URL("../../src/dmslog/web/" + name, import.meta.url), "utf8").replace(/\r\n/g, "\n");
const strip = (text) => text.replace(/\/\*[\s\S]*?\*\//g, "");
const css = strip(read("css/responsive.css"));
const app = strip(read("css/app.css"));
const html = read("index.html");
const main = read("js/main.js");

// Body of the first block that starts with `head` (a media query or a selector), nested blocks included for @media.
function block(head, from = 0) {
  const start = css.indexOf(head + " {", from);
  assert.ok(start >= 0, "no block for " + head);
  let depth = 0;
  for (let i = css.indexOf("{", start); i < css.length; i += 1) {
    if (css[i] === "{") depth += 1;
    if (css[i] === "}" && --depth === 0) return css.slice(css.indexOf("{", start) + 1, i);
  }
  throw new Error("unclosed " + head);
}
const decls = (body) => body.replace(/#panels(\.[\w-]+)?\s*\{/g, "").replace(/[{}]/g, "").split(";").map((d) => d.trim().replace(/\s+/g, " ")).filter(Boolean);
const only = (list, prefix) => list.filter((d) => d.startsWith(prefix));

test("the breakpoints in the stylesheet are those of the model", () => {
  assert.equal(SIDE_RAIL_QUERY, `(width < ${WIDE_MIN_WIDTH}px)`);
  assert.equal(DETAIL_DRAWER_QUERY, `(width < ${RAIL_MIN_WIDTH}px)`);
  assert.ok(css.includes("@media " + SIDE_RAIL_QUERY + " {"));
  assert.ok(css.includes("@media " + DETAIL_DRAWER_QUERY + " {"));
  assert.equal(WIDE_MIN_WIDTH, 1600);
  assert.equal(RAIL_MIN_WIDTH, 1280);
});

test("the media query and the user's collapse class set the same variables", () => {
  const side = decls(block("@media " + SIDE_RAIL_QUERY));
  const sideClass = decls(block("#panels.is-side-collapsed"));
  const sideVars = side.filter((d) => /^--(side|rail)-/.test(d));
  assert.ok(sideVars.length >= 9, "the side layout is a set of variables");
  assert.deepEqual(sideClass, sideVars);
  const detail = decls(block("@media " + DETAIL_DRAWER_QUERY));
  const detailClass = decls(block("#panels.is-detail-collapsed"));
  assert.deepEqual(detailClass, detail);
});

test("the three layouts use the widths of the proposal and of the column model", () => {
  const base = block("#panels");
  assert.match(base, /--side-col: var\(--side-w\)/);
  assert.match(base, /--detail-col: var\(--detail-w\)/);
  const tokens = strip(read("css/tokens.css"));
  assert.match(tokens, new RegExp(`--side-w: ${LAYOUT_BY_MODE.wide.side}px`));
  assert.match(tokens, new RegExp(`--detail-w: ${LAYOUT_BY_MODE.wide.detail}px`));
  assert.match(css, new RegExp(`--rail-w: ${LAYOUT_BY_MODE.rail.side}px`));
  const rail = block("@media " + SIDE_RAIL_QUERY);
  assert.match(rail, new RegExp(`--detail-col: ${LAYOUT_BY_MODE.rail.detail}px`));
  assert.equal(LAYOUT_BY_MODE.rail.detail, 340, "20 px less than the proposal: Message must reach 50 characters at 1280 with the Collection column");
  assert.match(css, /--drawer-side-w: 280px/);
  assert.match(css, /--drawer-detail-w: 420px/);
  assert.match(block("@media " + DETAIL_DRAWER_QUERY), /--detail-col: 0px/);
  assert.match(base, /grid-template-columns: var\(--side-col\) minmax\(0, 1fr\) var\(--detail-col\)/);
});

test("drawers are overlays over the grid: absolute, above the dock, with the overlay surface and shadow", () => {
  const rail = block("@media " + SIDE_RAIL_QUERY);
  const detail = block("@media " + DETAIL_DRAWER_QUERY);
  assert.match(rail, /--side-pos: absolute/);
  assert.match(detail, /--detail-pos: absolute/);
  for (const text of [rail, detail]) {
    assert.match(text, /z-index|--(side|detail)-z: var\(--z-drawer\)/);
    assert.match(text, /var\(--surface-overlay\)/);
    assert.match(text, /var\(--shadow-overlay\)/);
  }
  assert.match(block("#panels"), /position: relative/);
  assert.match(block("#panels.is-side-open > #sources-panel,\n#panels.is-detail-open > #detail-panel"), /display: flex/);
  assert.doesNotMatch(css, /#[0-9a-fA-F]{3,6}\b/, "tokens only: no literal colours");
});

test("every panel has an explicit grid area, so a drawer leaving the flow never moves the grid", () => {
  assert.match(block("#panels > #grid-panel"), /grid-area: 1 \/ 2/);
  assert.match(block("#panels > #sources-panel"), /grid-area: var\(--side-area\)/);
  assert.match(block("#panels > #detail-panel"), /grid-area: var\(--detail-area\)/);
});

test("the old breakpoints that let the grid run under the detail are gone", () => {
  assert.doesNotMatch(app, /max-width: 1100px/);
  assert.doesNotMatch(app, /max-width: 720px/);
  assert.equal(app.includes("grid-column: 1 / -1"), false);
});

test("forced colors: the drawers and the rail are drawn with borders", () => {
  const forced = block("@media (forced-colors: active)");
  assert.match(forced, /#panels > #sources-panel/);
  assert.match(forced, /border: 1px solid CanvasText/);
});

test("the page carries the rail, the buttons and the stylesheet, last; the module loads with its failure handled", () => {
  assert.match(html, /<button[^>]*id="side-rail"[^>]*aria-controls="sources-panel"[^>]*aria-expanded="false"/);
  assert.match(html, /id="side-rail-count"/);
  assert.match(html, /<button[^>]*id="detail-toggle"[^>]*aria-controls="detail-panel"[^>]*aria-expanded="false"/);
  assert.match(html, /id="side-collapse"/);
  assert.match(html, /id="detail-collapse"/);
  const links = [...html.matchAll(/<link rel="stylesheet" href="([^"]+)"/g)].map((m) => m[1]);
  assert.equal(links.at(-1), "/css/responsive.css");
  assert.match(main, /const loadDrawers = retryable\(\(\) => import\("\.\/drawers\.js"\)/, "a failed import is forgotten and tried again");
  assert.match(main, /focusNewCase: \(\) => withSources\(\(\) => panel\.revealNewCase\(\)\)/);
  assert.match(main, /focusAddFiles: \(\) => withSources\(\(\) => panel\.revealAddFiles\(\)\)/);
  assert.match(main, /openWhenLoaded\(\{ current: \(\) => drawers, load: loadDrawers/);
  assert.doesNotMatch(html, /id="detail-toggle"[^>]*\bhidden\b/, "visible from the first paint where CSS shows it, so a failed module leaves no dead end");
  assert.match(css, /#detail-toggle \{\s*display: var\(--detail-toggle-display\)/);
  assert.doesNotMatch(main.split("\n").filter((l) => /^import /.test(l)).join("\n"), /drawers/, "not in the static graph of the opening");
});
