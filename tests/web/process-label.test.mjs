import assert from "node:assert/strict";
import { test } from "node:test";
import { compareInstance, familyProcessText, groupPoolItems, hasPool, processLabel } from "../../src/dmslog/web/js/process-label.js";

const fh = (n) => ({ family: "DMSFH", process: "DMS_FunctionHost_" + n, pool: "DMS_FunctionHost", instance: n });

test("a pool instance reads as the pool name and the number", () => {
  assert.equal(processLabel(fh(7)), "DMS_FunctionHost #7");
  assert.equal(familyProcessText(fh(7)), "DMSFH / DMS_FunctionHost #7");
  assert.equal(hasPool(fh(7)), true);
});

test("outside a pool the stored process name is kept", () => {
  const plain = { family: "DMSJOB", process: "DMS_JobLauncher", pool: null, instance: null };
  assert.equal(processLabel(plain), "DMS_JobLauncher");
  assert.equal(familyProcessText(plain), "DMSJOB / DMS_JobLauncher");
  assert.equal(familyProcessText({ family: "Wfm", process: "Wfm" }), "Wfm", "family equal to the process shows once");
  assert.equal(processLabel({ process: "X", pool: "P", instance: null }), "X", "a pool without a valid instance is no pool");
  assert.equal(processLabel({ process: "X", pool: "", instance: 3 }), "X");
  assert.equal(processLabel(null), "");
  assert.equal(familyProcessText(null), "");
});

test("log text in the label is kept literally", () => {
  assert.equal(processLabel({ process: "<b>x</b>" }), "<b>x</b>");
});

test("instances order by number, not by text", () => {
  const list = [10, 2, 1, 28, 11].map(fh);
  list.sort(compareInstance);
  assert.deepEqual(list.map((o) => o.instance), [1, 2, 10, 11, 28]);
  assert.equal(compareInstance({}, fh(1)), 1);
  assert.equal(compareInstance({}, {}), 0);
});

test("groupPoolItems gathers a pool where its first member is and sorts it", () => {
  const items = [{ n: "a" }, fh(10), { n: "b" }, fh(2), fh(1)];
  const out = groupPoolItems(items);
  assert.deepEqual(out.map((o) => o.n || o.instance), ["a", 1, 2, 10, "b"]);
});
