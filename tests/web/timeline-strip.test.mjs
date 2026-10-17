// Timeline alarm strip: stylesheet and source guards that need no browser. Colours are audited in
// tokens-contrast.test.mjs (every --chart-* token is at least 3:1 on the panel).
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const WEB = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../src/dmslog/web");
const read = (rel) => fs.readFileSync(path.join(WEB, rel), "utf8").replace(/\r\n/g, "\n");

test("the severity series use the chart tokens, not literal colours", () => {
  const css = read("css/timeline.css");
  for (const [slot, token] of [["c0", "critical"], ["c1", "error"], ["c2", "warning"], ["c3", "normal"], ["c4", "noise"]]) {
    assert.match(css, new RegExp(`--tl-${slot}: var\\(--chart-${token}\\);`));
  }
  assert.doesNotMatch(css, /--tl-c\d+: #/);
});

test("the strip height comes from the tokens: 40 px, expandable to 120 px", () => {
  const tokens = read("css/tokens.css");
  assert.match(tokens, /--timeline-h: 40px;/);
  assert.match(tokens, /--timeline-h-expanded: 120px;/);
  const css = read("css/timeline.css");
  assert.match(css, /\.timeline-host \{\s*min-height: calc\(var\(--timeline-h\) \+ 30px\);/);
  assert.doesNotMatch(css, /min-height: 120px/);
  // The expanded strip reserves its taller height from the first paint too.
  assert.match(css, /\.timeline-host:has\(\.tl-expanded\) \{\s*min-height: calc\(var\(--timeline-h-expanded\) \+ 30px\);\s*\}/);
});

test("timeline.js writes text only and keeps its labels in strings.js", () => {
  const source = read("js/timeline.js");
  assert.doesNotMatch(source, /innerHTML|insertAdjacentHTML|outerHTML/);
  const strings = read("js/strings.js");
  for (const key of ["timelineErrorsOnly", "timelineExpand", "timelineCollapse", "timelineUndatedOne", "timelineUndatedOther"]) {
    assert.match(strings, new RegExp(`\\b${key}:`));
  }
  assert.match(source, /aria-pressed/);
  assert.match(source, /aria-expanded/);
});

test("the ? dialog carries the timeline hint (UI-28)", () => {
  assert.match(read("js/shortcuts.js"), /S\.timelineHint/);
});
