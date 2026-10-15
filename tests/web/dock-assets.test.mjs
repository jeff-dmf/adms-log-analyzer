// UI-15: the dock's host in index.html, its stylesheet and the numbers CSS and JS must agree on.
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { DOCK_MIN_PX, GRID_MIN_ROWS } from "../../src/dmslog/web/js/dock-model.js";
import { ROW_HEIGHT } from "../../src/dmslog/web/js/grid-model.js";

const WEB = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../src/dmslog/web");
const read = (file) => fs.readFileSync(path.join(WEB, file), "utf8").replace(/\r\n/g, "\n");

test("#dock sits in #grid-panel after the grid and before the detail, hidden, with its label from strings.js", () => {
  const html = read("index.html");
  const dock = html.indexOf('<section id="dock"');
  assert.ok(dock > html.indexOf('id="grid-viewport"'), "after the grid");
  assert.ok(dock > html.indexOf('id="follow-new"'));
  assert.ok(dock < html.indexOf('<aside id="detail-panel"'), "before the detail: F6 and Tab order");
  assert.ok(dock < html.indexOf("</section>", html.indexOf('id="grid-panel"') + 1) + 1);
  const tag = html.slice(dock, html.indexOf(">", dock));
  assert.match(tag, /class="ui-dock"/);
  assert.match(tag, /data-i18n-aria-label="dockLabel"/);
  assert.match(tag, /\bhidden\b/);
  assert.match(html, /<link rel="stylesheet" href="\/css\/dock\.css">/);
});

test("main.js creates the dock over #dock and the provisional dock is gone", () => {
  const main = read("js/main.js");
  assert.match(main, /import \{ createDock \} from "\.\/dock\.js"/);
  assert.match(main, /createDock\(\{\s*root: byId\("dock"\)/);
  assert.ok(!fs.existsSync(path.join(WEB, "js/dock-legacy.js")));
  assert.ok(!read("index.html").includes("dock-legacy"));
});

test("the CSS minimums match the model: dock 160 px, grid 8 rows of the row height", () => {
  assert.match(read("css/dock.css"), new RegExp(`min-height: ${DOCK_MIN_PX}px`));
  const app = read("css/app.css");
  assert.match(app, new RegExp(`--row-height\\) \\* ${GRID_MIN_ROWS} \\+`));
  assert.match(read("css/tokens.css"), new RegExp(`--row-height: ${ROW_HEIGHT}px`));
});

test("the dock stylesheet beats #grid-panel > * (flex: none) and hides the grid only when maximized", () => {
  const css = read("css/dock.css");
  assert.match(css, /#grid-panel > \.ui-dock/);
  assert.match(css, /#grid-panel:has\(> \.ui-dock\.is-maximized\) > \.grid-viewport/);
  assert.match(css, /\.dock-panel:not\(\.is-active\)|\.dock-panel \{\s*display: none;/);
});
