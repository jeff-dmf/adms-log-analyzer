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
  "highlight-panel", "copy-export", "distinct-panel", "aggregate-panel", "extract-panel", "extract-model", "events-panel", "dashboard", "unknown-panel",
  "config-panel", "key-panel", "payload-list", "side-by-side", "compare", "export-panel", "redaction-panel",
  "alerts-panel", "follow-tail", "clock-banner", "dock-model", "case-dialogs", "case-empty", "shortcuts", "shortcuts-model",
];
const MAX_STATIC_MODULES = 40; // main.js + boot.js, uPlot included; the budget on open is 40 with everything else

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
  assert.equal([...boot.matchAll(/^import .*$/gm)].length, 2, "static imports: boot-failure.js and prefetch.js");
  assert.ok(boot.length < 2000, "boot.js stays tiny");
  assert.match(boot, /import\("\.\/main\.js"\)\.catch\(/, "a failing main.js is handled, not left unhandled");
});

test("the panels and sections left out of the opening are all loaded by import() where they are used", () => {
  const actions = read("js/app-actions.js");
  const main = read("js/main.js");
  for (const name of ["filter-tree", "distinct-panel", "aggregate-panel", "events-panel", "dashboard", "payload-list",
    "export-dialogs", "alerts-panel", "bookmarks-panel", "highlight-panel", "tpl-leaf"]) {
    assert.match(actions, new RegExp(`import\\("\\./${name}\\.js"\\)`), name + " (app-actions.js)");
  }
  for (const name of ["links", "payload", "highlight-panel", "bookmarks-panel", "copy-export", "follow-tail",
    "alerts-panel", "clock-banner"]) {
    assert.match(main, new RegExp(`import\\("\\./${name}\\.js"\\)`), name + " (main.js)");
  }
  // UI-22: the case dialogs and the empty state without a case load from sources.js, with a retry after a failure.
  const sources = read("js/sources.js");
  for (const name of ["case-dialogs", "case-empty"]) {
    assert.match(sources, new RegExp(`import\\("\\./${name}\\.js"\\)`), name + " (sources.js)");
  }
  // UI-28: the keyboard layer loads after the first view, with a retry after a failure.
  assert.match(main, /retryable\(\(\) => import\("\.\/shortcuts\.js"\)/);
  assert.match(main, /afterFirstView\.then\(startShortcuts\)/);
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

test("index.html starts at boot.js and preloads the whole static graph, with plain URLs", () => {
  assert.match(html, /<script type="module" src="\/js\/boot\.js"><\/script>/);
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

// ---- late initialisation (late-init.js) --------------------------------------------------------------------

const { mountLate, retryable, loadSections } = await import("../../src/dmslog/web/js/late-init.js");

function lateSetup(initialCase) {
  const state = { caseId: initialCase, told: [], mounted: 0 };
  const gate = {};
  gate.loaded = new Promise((resolve, reject) => { gate.resolve = resolve; gate.reject = reject; });
  const run = () => mountLate({
    wait: Promise.resolve(),
    load: () => gate.loaded,
    mount: () => { state.mounted += 1; return { onCase: () => state.told.push("onCase") }; },
    getCase: () => state.caseId,
    onCaseOpen: (instance) => instance.onCase(),
  });
  return { state, gate, run };
}

test("a module that arrives with a case already open is told about it", async () => {
  const { state, gate, run } = lateSetup("C1");
  const done = run();
  gate.resolve({});
  await done;
  assert.deepEqual(state.told, ["onCase"]);
});

test("a module that arrives with no case is not told; a case opened during the load counts, one closed does not", async () => {
  const none = lateSetup(null);
  const first = none.run();
  none.gate.resolve({});
  await first;
  assert.deepEqual(none.state.told, []);

  const opened = lateSetup(null);
  const second = opened.run();
  opened.state.caseId = "C2"; // the case opens while the module is loading
  opened.gate.resolve({});
  await second;
  assert.deepEqual(opened.state.told, ["onCase"]);

  const closed = lateSetup("C1");
  const third = closed.run();
  closed.state.caseId = null; // the case is closed while the module is loading
  closed.gate.resolve({});
  await third;
  assert.deepEqual(closed.state.told, []);
});

test("a rejected import() rejects the mount and mounts nothing", async () => {
  const { state, gate, run } = lateSetup("C1");
  const done = run();
  gate.reject(new Error("chunk failed"));
  await assert.rejects(done, /chunk failed/);
  assert.equal(state.mounted, 0);
  assert.deepEqual(state.told, []);
});

test("main.js mounts Follow tail through mountLate and tells it about an open case", () => {
  const main = read("js/main.js");
  assert.match(main, /ready\.followTail = settled\(mountLate\(\{[\s\S]*?import\("\.\/follow-tail\.js"\)[\s\S]*?onCaseOpen: \(instance\) => instance\.onCase\(\)/);
});

test("a section that failed to load is tried again, one that loaded is not loaded twice", async () => {
  let calls = 0;
  const flaky = retryable(async () => {
    calls += 1;
    if (calls === 1) {
      throw new Error("offline");
    }
    return "ok";
  });
  await assert.rejects(flaky(), /offline/);
  assert.equal(await flaky(), "ok");
  assert.equal(await flaky(), "ok");
  assert.equal(calls, 2);
});

test("loadSections reports the failures of a call without rejecting and keeps the sections that loaded", async () => {
  const seen = [];
  let good = 0;
  const loaders = [retryable(async () => { good += 1; }), retryable(async () => { throw new Error("x"); })];
  assert.equal(await loadSections(loaders, (errors) => seen.push(errors.length)), 1);
  assert.equal(await loadSections(loaders, (errors) => seen.push(errors.length)), 1);
  assert.deepEqual(seen, [1, 1]);
  assert.equal(good, 1);
  assert.equal(await loadSections([], () => seen.push(-1)), 0);
  assert.deepEqual(seen, [1, 1]);
});

test("main.js shows the unavailable-sections message once, from the detail loaders", () => {
  const main = read("js/main.js");
  assert.match(main, /loadSections\(detailLoaders[\s\S]*?detailFailureShown[\s\S]*?S\.detailSectionsUnavailable/);
  assert.ok(!/catch\(\(\) => \{\}\); \/\/ a missing or failing module only costs its section/.test(main));
});

// ---- boot failure ------------------------------------------------------------------------------------------

test("a failure to load main.js shows an alert, reveals the bar and leaves no unhandled rejection", async () => {
  const doc = installGlobalDom();
  const bar = doc.createElement("header");
  bar.id = "toolbar";
  doc.body.appendChild(bar);
  globalThis.fetch = () => Promise.reject(new Error("offline"));
  const unhandled = [];
  process.on("unhandledRejection", (err) => unhandled.push(err));
  const errors = [];
  const original = console.error;
  console.error = (...args) => errors.push(args);
  try {
    const { booted } = await import("../../src/dmslog/web/js/boot.js"); // main.js cannot start on this empty page
    await booted; // the failure handling has finished
    await new Promise((resolve) => setImmediate(resolve)); // lets a pending unhandledRejection surface
  } finally {
    console.error = original;
  }
  assert.ok(bar.classList.contains("is-ready") && bar.classList.contains("is-failed"));
  assert.deepEqual(unhandled, []);
  assert.ok(errors.length >= 1);
  const alerts = bar.all().filter((n) => n.attrs && n.attrs.role === "alert");
  assert.equal(alerts.length, 1, "main.js and boot.js must not both show the startup alert");
  assert.match(alerts[0].textContent, /could not start/);
});
