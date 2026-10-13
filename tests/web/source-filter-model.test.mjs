// Pure model of the source filter: case-insensitive substring match, ranges and persistence.
import test from "node:test";
import assert from "node:assert/strict";

import {
  STORAGE_KEY, baseName, filterSources, highlightParts, loadQuery, matchRange, saveQuery,
} from "../../src/dmslog/web/js/source-filter-model.js";

const sources = ["AdapterOne.log", "adapter_two.log.0", "ADAPTER-3.log", "Other.log", "Éclat.log", "a+b.log"].map(
  (name, id) => ({ id, name }));

function fakeStorage() {
  const data = new Map();
  return {
    data,
    getItem: (k) => (data.has(k) ? data.get(k) : null),
    setItem: (k, v) => data.set(k, String(v)),
    removeItem: (k) => data.delete(k),
  };
}

test("matches regardless of case", () => {
  for (const q of ["adapter", "Adapter", "ADAPTER", "ADapter"]) {
    assert.deepEqual(filterSources(sources, q).map((m) => m.source.id), [0, 1, 2], q);
  }
});

test("empty or blank query keeps every source", () => {
  assert.equal(filterSources(sources, "").length, sources.length);
  assert.equal(filterSources(sources, "   ").length, sources.length);
});

test("no match gives an empty list", () => {
  assert.deepEqual(filterSources(sources, "zzz"), []);
});

test("unicode: accented letters fold by case, accents stay significant", () => {
  assert.deepEqual(filterSources(sources, "éCLAT").map((m) => m.source.id), [4]);
  assert.deepEqual(filterSources(sources, "eclat"), []);
});

test("regex characters are literals", () => {
  assert.deepEqual(filterSources(sources, "a+b").map((m) => m.source.id), [5]);
  assert.deepEqual(filterSources(sources, ".*"), []);
  assert.deepEqual(filterSources(sources, "(").length, 0);
});

test("the file name is the base name, directories do not match", () => {
  assert.equal(baseName("C:\\logs\\sub\\a.log"), "a.log");
  assert.equal(baseName("x/y/a.log"), "a.log");
  assert.deepEqual(filterSources([{ id: 1, name: "logs/adapter.log" }], "logs"), []);
});

test("match range and highlight parts", () => {
  assert.deepEqual(matchRange("MyAdapter.log", "adapt"), { start: 2, end: 7 });
  assert.deepEqual(highlightParts("MyAdapter.log", { start: 2, end: 7 }), [
    { text: "My", match: false }, { text: "Adapt", match: true }, { text: "er.log", match: false },
  ]);
  assert.deepEqual(highlightParts("abc", { start: 0, end: 3 }), [{ text: "abc", match: true }]);
  assert.deepEqual(highlightParts("abc", null), [{ text: "abc", match: false }]);
});

test("length-changing case folding still maps back to the original", () => {
  // "İ" lowercases to two code units; the range must still cover the original letter.
  const range = matchRange("xİy.log", "y.");
  assert.deepEqual(range, { start: 2, end: 4 });
});

test("persistence tolerates blocked storage", () => {
  const storage = fakeStorage();
  saveQuery("adapter", storage);
  assert.equal(loadQuery(storage), "adapter");
  saveQuery("  ", storage);
  assert.equal(storage.data.has(STORAGE_KEY), false);
  const broken = { getItem() { throw new Error("x"); }, setItem() { throw new Error("x"); }, removeItem() { throw new Error("x"); } };
  assert.equal(loadQuery(broken), "");
  saveQuery("a", broken);
});

test("whole-string folding: Greek final sigma matches", () => {
  assert.deepEqual(filterSources([{ id: 1, name: "\u039f\u03a3-1.log" }], "\u039f\u03a3").map((m) => m.source.id), [1]);
  assert.deepEqual(matchRange("\u039f\u03a3-1.log", "\u039f\u03a3"), { start: 0, end: 2 });
});

test("ranges cover whole code points (surrogate pairs)", () => {
  assert.deepEqual(matchRange("a\u{1F600}.log", "\u{1F600}"), { start: 1, end: 3 });
  const parts = highlightParts("a\u{1F600}.log", { start: 1, end: 3 });
  assert.equal(parts[1].text, "\u{1F600}");
});
