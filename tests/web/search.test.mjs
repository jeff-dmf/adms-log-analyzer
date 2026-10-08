import test from "node:test";
import assert from "node:assert/strict";
import { highlightSegments, searchTerms } from "../../src/dmslog/web/js/search.js";
import { resolveTheme } from "../../src/dmslog/web/js/theme.js";

const joined = (segments) => segments.map((s) => s.text).join("");

test("searchTerms handles words, phrases and prefixes", () => {
  assert.deepEqual(searchTerms("timeout \"send works\" abc*"), ["timeout", "send works", "abc"]);
  assert.deepEqual(searchTerms("   "), []);
});

test("highlight is case-insensitive and concatenates back to the input", () => {
  const segments = highlightSegments("Timeout waiting; timeout again", ["timeout"]);
  assert.equal(joined(segments), "Timeout waiting; timeout again");
  assert.deepEqual(segments.filter((s) => s.mark).map((s) => s.text), ["Timeout", "timeout"]);
});

test("regex metacharacters in terms are literal", () => {
  const segments = highlightSegments("a.b(c) a+b", ["a.b(", "a+b"]);
  assert.deepEqual(segments.filter((s) => s.mark).map((s) => s.text), ["a.b(", "a+b"]);
});

test("HTML in log text stays plain text in the segments", () => {
  const text = "<script>alert(1)</script> boom";
  const segments = highlightSegments(text, ["script", "boom"]);
  assert.equal(joined(segments), text);
  assert.ok(segments.every((s) => typeof s.text === "string"));
  assert.deepEqual(segments.filter((s) => s.mark).map((s) => s.text), ["script", "script", "boom"]);
});

test("overlapping terms merge and no terms means one plain segment", () => {
  assert.deepEqual(highlightSegments("abcdef", ["abcd", "cdef"]), [{ text: "abcdef", mark: true }]);
  assert.deepEqual(highlightSegments("abc", []), [{ text: "abc", mark: false }]);
  assert.deepEqual(highlightSegments("", ["a"]), [{ text: "", mark: false }]);
});

test("resolveTheme prefers the stored choice, then the OS preference", () => {
  assert.equal(resolveTheme("light", true), "light");
  assert.equal(resolveTheme("dark", false), "dark");
  assert.equal(resolveTheme(null, true), "dark");
  assert.equal(resolveTheme("weird", false), "light");
});
