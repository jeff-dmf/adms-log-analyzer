// The flat source list orders the instances of a pool by number; sources outside a pool keep their place.
import assert from "node:assert/strict";
import { test } from "node:test";
import { installGlobalDom } from "./fixtures/fake-dom.mjs";

installGlobalDom();
const { orderPoolEntries } = await import("../../src/dmslog/web/js/sources.js");

const entry = (name, pool = null, instance = null) => ({ source: { name, pool, instance } });
const names = (list) => list.map((e) => e.source.name);

test("pool instances go by number where the first member is, the rest keeps its order", () => {
  const input = [
    entry("a.log"),
    entry("Example_2", "Example", 2),
    entry("b.log"),
    entry("Example_10", "Example", 10),
    entry("Example_1", "Example", 1),
    entry("c.log"),
    entry("Example_3", "Example", 3),
    entry("Example_11", "Example", 11),
  ];
  assert.deepEqual(names(orderPoolEntries(input)), [
    "a.log", "Example_1", "Example_2", "Example_3", "Example_10", "Example_11", "b.log", "c.log",
  ]);
});

test("no pool means the same array; an instance that is not an integer keeps its place", () => {
  const plain = [entry("x.log"), entry("y.log")];
  assert.equal(orderPoolEntries(plain), plain);
  const odd = [entry("p_b", "P", "x"), entry("p_a", "P", null), entry("z.log")];
  assert.deepEqual(names(orderPoolEntries(odd)), ["p_b", "p_a", "z.log"]);
});

test("two pools are ordered separately and the result is stable on a second pass", () => {
  const input = [entry("A_2", "A", 2), entry("B_10", "B", 10), entry("A_1", "A", 1), entry("B_2", "B", 2)];
  const once = orderPoolEntries(input);
  assert.deepEqual(names(once), ["A_1", "A_2", "B_2", "B_10"]);
  assert.deepEqual(names(orderPoolEntries(once)), names(once));
});
