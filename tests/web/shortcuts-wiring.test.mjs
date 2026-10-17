// UI-28: what the page, main.js and the other modules must (not) contain so the keyboard layer is the only dispatcher.
// Reads the sources as text; CRLF is normalized (autocrlf on Windows).
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { createActionRegistry, isSingleKey } from "../../src/dmslog/web/js/actions.js";
import { registerAppActions } from "../../src/dmslog/web/js/app-actions.js";
import { S } from "../../src/dmslog/web/js/strings.js";

const WEB = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../src/dmslog/web");
const read = (file) => fs.readFileSync(path.join(WEB, file), "utf8").replace(/\r\n/g, "\n");
const html = read("index.html");
const jsFiles = fs.readdirSync(path.join(WEB, "js")).filter((name) => name.endsWith(".js"));

test("the skip link is the first control of the page, before the app, and points at the grid", () => {
  const body = html.slice(html.indexOf("<body>"));
  const first = /<(a|button|input|select|textarea|summary)\b[^>]*>/.exec(body);
  assert.ok(first, "the page has controls");
  assert.match(first[0], /^<a id="skip-link" class="skip-link" href="#grid-viewport" data-i18n="skipToGrid">$/);
  assert.ok(body.indexOf('id="skip-link"') < body.indexOf('id="app"'));
  assert.ok(!/tabindex="[1-9]/.test(html), "no positive tabindex: the Tab order is the DOM order");
  assert.match(html, /<a id="skip-link"[^>]*><\/a>/, "the text comes from strings.js");
  assert.ok(S.skipToGrid && S.skipToGrid.length > 0);
  assert.match(html, /<header id="toolbar"[^>]*data-i18n-aria-label="regionBar"/);
  assert.ok(S.regionBar);
});

test("the Tab order of the page follows the F6 regions: bar, side, strip, grid, dock, detail", () => {
  const at = (id) => html.indexOf(`id="${id}"`);
  const order = ["toolbar", "sources-panel", "filter-strip", "grid-viewport", "dock", "detail-panel"].map(at);
  assert.ok(order.every((n) => n > 0), "every region root is in the page");
  assert.deepEqual([...order].sort((a, b) => a - b), order);
});

test("the skip link shows only while focused; the sheet is linked before the responsive one", () => {
  const css = read("css/shortcuts.css").replace(/\/\*[\s\S]*?\*\//g, "");
  assert.match(css, /\.skip-link\s*\{[^}]*transform:\s*translateY\(-300%\)/);
  assert.match(css, /\.skip-link:focus[^{]*\{[^}]*transform:\s*none/);
  assert.ok(!/#[0-9a-fA-F]{3,8}\b/.test(css), "tokens only");
  assert.ok(html.indexOf("/css/shortcuts.css") > 0 && html.indexOf("/css/shortcuts.css") < html.indexOf("/css/responsive.css"));
});

test("main.js no longer listens for letters; the layer loads after the first view and is tried again after a failure", () => {
  const main = read("js/main.js");
  assert.ok(!/\b(navKey|bookmarkKey|ignoresShortcut)\b/.test(main));
  assert.match(main, /const loadShortcuts = retryable\(\(\) => import\("\.\/shortcuts\.js"\)/);
  assert.match(main, /afterFirstView\.then\(startShortcuts\)/);
  assert.match(main, /\{ once: true, capture: true \}/, "the next key press tries the load again");
  assert.match(main, /singleKeysOn: \(\) => \(shortcuts === null \? true : shortcuts\.singleKeys\(\)\)/);
  const listeners = [...main.matchAll(/document\.addEventListener\("keydown"/g)];
  assert.equal(listeners.length, 2, "the Columns popover's Escape and the one-shot retry; nothing else");
  assert.match(main, /openShortcuts: async \(\) => \{\s*const opener = document\.activeElement;\s*\(await loadShortcuts\(\)\)\.openDialog\(opener\);/);
});

test("no other module keeps a document-level key listener for the shortcuts (never two dispatchers)", () => {
  for (const name of ["highlight-panel.js", "views-stack.js", "filters.js", "highlight-model.js", "views-stack-model.js"]) {
    const text = read("js/" + name);
    assert.ok(!/document\.addEventListener\(\s*"keydown"/.test(text), name);
    assert.ok(!/\b(navKey|bookmarkKey|highlightNavKey|viewsKey)\b/.test(text), name + " keeps a key map of its own");
  }
  // only shortcuts.js reads keys for shortcuts through the registry
  const dispatchers = jsFiles.filter((name) => /\.match\(event/.test(read("js/" + name)));
  assert.deepEqual(dispatchers, ["shortcuts.js"]);
});

test("the registry holds every shortcut once, the single-key ones are the letters and punctuation of the issue", () => {
  const registry = createActionRegistry({ onError() {} });
  const never = () => { throw new Error("not called while registering"); };
  registerAppActions(registry, { ready: {}, cases: never, theme: never });
  const withKeys = registry.list({ withShortcut: true });
  const keys = Object.fromEntries(withKeys.map((d) => [d.id, d.shortcut]));
  assert.deepEqual(keys, {
    "toggle.bookmarks": "B",
    "navigate.nextError": "n", "navigate.prevError": "N", "navigate.nextType": "t", "navigate.prevType": "T",
    "navigate.nextHighlight": "]", "navigate.prevHighlight": "[", "navigate.focusSearch": "/",
    "navigate.nextRegion": "F6", "navigate.prevRegion": "Shift+F6",
    "record.bookmark": "b", "record.compare": "c",
    "views.back": "Alt+ArrowLeft", "views.forward": "Alt+ArrowRight",
    "help.shortcuts": "?",
  });
  assert.deepEqual(withKeys.filter((d) => !isSingleKey(d.shortcut)).map((d) => d.id).sort(),
    ["navigate.nextRegion", "navigate.prevRegion", "views.back", "views.forward"]);
  for (const d of withKeys) {
    assert.ok(S[d.labelKey], d.id);
  }
});
