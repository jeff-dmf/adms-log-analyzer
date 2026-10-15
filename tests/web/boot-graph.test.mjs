// UI-14: what the opening loads. The static import graph of main.js and boot.js is the code every opening pays for,
// so it must not grow back: no dock panel, no detail section, no filter tree. index.html lists that graph for
// <link rel="modulepreload"> (one round trip instead of one per import depth) and starts at boot.js.
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { installGlobalDom } from "./fixtures/fake-dom.mjs";

const WEB = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../src/dmslog/web");
const read = (file) => fs.readFileSync(path.join(WEB, file), "utf8").replace(/\r\n/g, "\n");

// Static dependencies (import ... from "./x.js", export ... from, import "./x.js"), as web paths ("/js/x.js").
function staticGraph(entries) {
  const seen = new Set();
  const walk = (web) => {
    if (seen.has(web)) {
      return;
    }
    seen.add(web);
    const source = read(web.slice(1));
    const re = /^\s*(?:import|export)\s[^"']*?from\s*["'](\.[^"']+)["']|^import\s*["'](\.[^"']+)["']/gm;
    for (const match of source.matchAll(re)) {
      walk("/" + path.posix.normalize(path.posix.join(path.posix.dirname(web.slice(1)), match[1] || match[2])));
    }
  };
  entries.forEach(walk);
  return seen;
}

// Loaded on demand (dock panels, the filter tree, the detail sections, Copy as, the folder browser) or after the
// first view (Follow tail, alerts, clock banner).
const NOT_AT_OPENING = [
  "filter-tree", "tpl-leaf", "presets", "saved-views", "folder-browser", "links", "payload", "bookmarks-panel",
  "highlight-panel", "copy-export", "distinct-panel", "aggregate-panel", "events-panel", "dashboard", "unknown-panel",
  "config-panel", "key-panel", "payload-list", "side-by-side", "compare", "export-panel", "redaction-panel",
  "alerts-panel", "follow-tail", "clock-banner",
];
const MAX_STATIC_MODULES = 38; // main.js + boot.js, uPlot included; the budget on open is 40 with everything else

test("the static graph of main.js and boot.js holds no panel, tree, section or deferred module", () => {
  const graph = staticGraph(["/js/main.js", "/js/boot.js"]);
  const names = [...graph].map((web) => path.posix.basename(web, ".js"));
  for (const name of NOT_AT_OPENING) {
    assert.ok(!names.includes(name), name + " is loaded statically");
  }
  assert.ok(graph.size <= MAX_STATIC_MODULES, `${graph.size} modules (max ${MAX_STATIC_MODULES})`);
});

test("boot.js only starts the opening requests and loads main.js", () => {
  const boot = read("js/boot.js");
  assert.match(boot, /startPrefetch\(\)/);
  assert.match(boot, /import\("\.\/main\.js"\)/);
  assert.equal([...boot.matchAll(/^import .*$/gm)].length, 1, "a single static import: prefetch.js");
  assert.ok(boot.length < 1500, "boot.js stays tiny");
});

test("the panels and sections left out of the opening are all loaded by import() where they are used", () => {
  const actions = read("js/app-actions.js");
  const main = read("js/main.js");
  for (const name of ["filter-tree", "distinct-panel", "aggregate-panel", "events-panel", "dashboard", "payload-list",
    "export-panel", "alerts-panel", "bookmarks-panel", "highlight-panel", "tpl-leaf"]) {
    assert.match(actions, new RegExp(`import\\("\\./${name}\\.js"\\)`), name + " (app-actions.js)");
  }
  for (const name of ["links", "payload", "highlight-panel", "bookmarks-panel", "copy-export", "follow-tail",
    "alerts-panel", "clock-banner"]) {
    assert.match(main, new RegExp(`import\\("\\./${name}\\.js"\\)`), name + " (main.js)");
  }
  // The four detail sections start together, on the first record shown, not at the opening.
  assert.match(main, /if \(entry\) \{\s*loadDetailModules\(\);/);
});

test("the tpl leaf registers with the filter tree, which main.js no longer imports", async () => {
  installGlobalDom();
  const model = await import("../../src/dmslog/web/js/filter-tree-model.js");
  await import("../../src/dmslog/web/js/tpl-leaf.js");
  assert.ok(model.leafTypeNames().includes("tpl"));
});

const html = read("index.html");
const usesBoot = /<script type="module" src="\/js\/boot\.js"><\/script>/.test(html);

// Skipped until index.html starts at boot.js (the integration of UI-14); then these are the guard of its contents.
test("index.html starts at boot.js and preloads the whole static graph, with plain URLs", { skip: !usesBoot }, () => {
  assert.ok(!/src="\/js\/main\.js"/.test(html), "main.js is loaded by boot.js only");
  const preloads = [...html.matchAll(/<link rel="modulepreload" href="([^"]+)">/g)].map((m) => m[1]);
  assert.equal(new Set(preloads).size, preloads.length, "no duplicates");
  for (const href of preloads) {
    assert.ok(!href.includes("?"), href + " has a query: preload and import() URLs must match exactly");
    assert.ok(fs.existsSync(path.join(WEB, href.slice(1))), href + " does not exist");
  }
  const missing = [...staticGraph(["/js/main.js", "/js/boot.js"])].filter((web) => web !== "/js/boot.js" && !preloads.includes(web));
  assert.deepEqual(missing, []);
  assert.ok(html.indexOf("modulepreload") < html.indexOf("/js/boot.js"), "the hints come before the script");
});
