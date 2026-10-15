// UI-12: css/topbar.css takes its sizes from tokens.css, and the hidden-until-ready bar has a way out
// (the 3 s reveal, and the failure state of main.js). CRLF is normalized so autocrlf on Windows does not matter.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const read = (name) => readFileSync(new URL("../../src/dmslog/web/" + name, import.meta.url), "utf8").replace(/\r\n/g, "\n");
const css = read("css/topbar.css").replace(/\/\*[\s\S]*?\*\//g, "");
const tokens = read("css/tokens.css");

test("topbar.css has no literal sizes other than hairlines and the caret mark", () => {
  const literals = css.split("\n").filter((line) => /\d+px/.test(line) && !/border(-\w+)?:\s*1px|border-(left|right|top):\s*[45]px|@media/.test(line));
  assert.deepEqual(literals, []);
});

test("every --topbar-* variable used is defined in tokens.css", () => {
  const used = new Set([...css.matchAll(/var\((--topbar-[\w-]+)\)/g)].map((m) => m[1]));
  assert.ok(used.size >= 6);
  for (const name of used) {
    assert.ok(new RegExp(name + ":\\s*\\d+px").test(tokens), name + " is defined in tokens.css");
  }
});

test("the bar hidden until ready reveals itself by CSS after a delay, and a failed start shows an alert", () => {
  assert.match(css, /\.topbar:not\(\.is-ready\) > \*\s*\{[^}]*visibility:\s*hidden[^}]*animation:\s*topbar-reveal 0s 3s forwards/);
  assert.match(css, /@keyframes topbar-reveal\s*\{\s*to\s*\{\s*visibility:\s*visible/);
  const main = read("js/main.js");
  assert.match(main, /classList\.add\("is-ready", "is-failed"\)/);
  assert.match(main, /setAttribute\("role", "alert"\)/);
});
