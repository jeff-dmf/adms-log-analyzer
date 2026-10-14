import { readFileSync } from "node:fs";
import { test } from "node:test";
import assert from "node:assert/strict";

const source = readFileSync(new URL("../../src/dmslog/web/js/main.js", import.meta.url), "utf8");

// main.js is not importable in node, so a misspelt map name would only fail at click time
// (issue #178: "Highlight this template" threw a ReferenceError). Every `template*` map the file reads
// with `.get(` must be declared in the file.
test("every template map read in main.js is declared", () => {
  const used = new Set([...source.matchAll(/\b(template[A-Za-z]*)\.get\(/g)].map((m) => m[1]));
  assert.ok(used.size > 0, "expected main.js to read at least one template map");
  for (const name of used) {
    const declared = new RegExp(`\\b(?:let|const|var)\\s+${name}\\b`).test(source);
    assert.ok(declared, `main.js reads ${name}.get(...) but never declares ${name}`);
  }
});
