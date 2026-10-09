import test from "node:test";
import assert from "node:assert/strict";
import {
  buildRows, buildTomlSketch, cleanSignature, escapeRegex, filterRows, formatShare, signatureExample,
  signatureToRegex, sortRows,
} from "../../src/dmslog/web/js/unknown-panel-model.js";

test("escapeRegex escapes every metacharacter", () => {
  assert.equal(escapeRegex("a.b*c+d?(e)[f]{g}|h^i$j\\k"), "a\\.b\\*c\\+d\\?\\(e\\)\\[f\\]\\{g\\}\\|h\\^i\\$j\\\\k");
});

test("signatureToRegex builds named groups for markers", () => {
  const { regex, fields } = signatureToRegex("Request <guid> failed after <n> ms (code <n>)");
  assert.ok(regex.startsWith("^Request (?P<guid>"));
  assert.ok(regex.includes("(?P<n>") && regex.includes("(?P<n_2>"));
  assert.ok(regex.endsWith("\\(code (?P<n_2>\\d{1,18}(?:\\.\\d{1,18})?)\\)$"));
  assert.deepEqual(fields, { guid: "guid", n: "int", n_2: "int" });
  assert.ok(!/\(\?<|\(\?P=/.test(regex));
});

test("long signatures are not anchored at the end", () => {
  assert.ok(!signatureToRegex("x ".repeat(130).trim()).regex.endsWith("$"));
});

test("regex stays within the engine limits", () => {
  const sig = Array(40).fill("<s>").join(" ");
  const { regex } = signatureToRegex(sig);
  assert.ok(regex.length <= 1000);
  assert.ok((regex.match(/\(\?P</g) || []).length <= 24);
});

test("example substitutes synthetic values", () => {
  assert.equal(signatureExample("id <guid> at <ip>"), "id 00000000-0000-4000-8000-000000000000 at 10.0.0.1");
});

test("cleanSignature drops control characters", () => {
  assert.equal(cleanSignature("a\u0000b\n c"), "a b c");
});

test("sketch has family, placeholder id, class other and the example", () => {
  const toml = buildTomlSketch({ signature: "Done <n> items", n: 5, family: "AWFM" });
  assert.match(toml, /family = "AWFM"/);
  assert.match(toml, /code = "AWFM-NEW-001"/);
  assert.match(toml, /class = "other"/);
  assert.match(toml, /examples = \['Done 1 items'\]/);
  assert.match(toml, /fields = \{ n = "int" \}/);
});

test("sketch falls back to a basic string when the regex has a quote", () => {
  const toml = buildTomlSketch({ signature: "can't <n>", n: 1 }, "AWFM");
  assert.match(toml, /^regex = ".*can't/m);
  assert.match(toml, /examples = \["can't 1"\]/);
});

test("invalid family becomes FAMILY", () => {
  assert.match(buildTomlSketch({ signature: "x", n: 1 }, "a b\nc"), /family = "FAMILY"/);
});

test("rows: share, filter and sort", () => {
  const rows = buildRows([
    { family: "A", signature: "beta <n>", n: 30, first_entry_id: 3 },
    { family: "A", signature: "alpha", n: 70, first_entry_id: 1 },
  ], 200);
  assert.equal(rows[1].share, 0.35);
  assert.equal(formatShare(0.35), "35%");
  assert.equal(formatShare(0.0004), "<0.1%");
  assert.deepEqual(filterRows(rows, "BET").map((r) => r.firstEntryId), [3]);
  assert.deepEqual(sortRows(rows, "count", true).map((r) => r.n), [70, 30]);
  assert.deepEqual(sortRows(rows, "signature", false).map((r) => r.signature), ["alpha", "beta <n>"]);
});

test("fallback regex always matches its own example (many timestamps, mixed markers)", () => {
  const sigs = [
    Array(35).fill("<ts>").join(" "),
    Array(12).fill("<ts> <guid> <n> <s> <path>").join(" ").slice(0, 230),
    Array(8).fill("<ts>").join(" "),
  ];
  for (const sig of sigs) {
    const { regex } = signatureToRegex(sig);
    assert.ok(regex.length <= 1000, regex.length);
    const js = regex.replace(/\(\?P</g, "(?<");
    assert.ok(new RegExp(js).test(signatureExample(sig)), sig);
  }
});

test("no bound above 64 and no quantified group holds a repeat", () => {
  const sig = "<ts> <guid> <url> <path> <ip> <hex> <s> <n>";
  const { regex } = signatureToRegex(sig);
  for (const m of regex.matchAll(/\{(\d+)(?:,(\d+))?\}/g)) {
    assert.ok(Number(m[2] || m[1]) <= 64, m[0]);
  }
  assert.ok(!/\{[^}]*\}[^()]*\)[*+?{]/.test(regex.replace(/\(\?:[^()]*\)\?/g, "")));
  assert.ok(new RegExp(regex.replace(/\(\?P</g, "(?<")).test(signatureExample(sig)));
});
