// Presets panel model: grouping, what the save button does, import/export helpers.
import test from "node:test";
import assert from "node:assert/strict";

import {
  exportFile, formatFromFileName, groupByFamily, importPayload, importSummary, planPresetSave, presetBody,
  sameIdentity,
} from "../../src/dmslog/web/js/presets-model.js";
import { MAX_NAME } from "../../src/dmslog/web/js/filter-tree-model.js";

const F = { sev: 2 };
const presets = [
  { name: "Errors", family: null, builtin: true, filter: F },
  { name: "Errors", family: "AWFM", builtin: true, filter: F },
  { name: "Mine", family: "AWFM", builtin: false, filter: F },
  { name: "Zeta", family: "AOSR", builtin: false, filter: F },
  { name: "Alpha", family: "AWFM", builtin: true, filter: F },
];

test("identity ignores letter case and compares the family", () => {
  assert.ok(sameIdentity({ name: "ERRORS", family: "AWFM" }, presets[1]));
  assert.ok(!sameIdentity({ name: "Errors", family: "AOSR" }, presets[1]));
  assert.ok(sameIdentity({ name: "errors", family: null }, { name: "Errors" }));
});

test("groups: all adapters first, families alphabetical, curated before user, then by name", () => {
  const groups = groupByFamily(presets);
  assert.deepEqual(groups.map((g) => g.family), [null, "AOSR", "AWFM"]);
  assert.deepEqual(groups[2].items.map((p) => p.name), ["Alpha", "Errors", "Mine"]);
});

test("new name creates; curated name is reserved; existing user name asks, then overwrites", () => {
  assert.deepEqual(planPresetSave({ raw: " New  one ", family: "AWFM", presets }), { action: "create", name: "New one", family: "AWFM" });
  assert.equal(planPresetSave({ raw: "errors", family: "AWFM", presets }).error, "reserved");
  assert.equal(planPresetSave({ raw: "errors", family: "AOSR", presets }).action, "create");
  const ask = planPresetSave({ raw: "mine", family: "AWFM", presets });
  assert.equal(ask.action, "confirmOverwrite");
  const done = planPresetSave({ raw: "MINE", family: "AWFM", presets, confirmed: { name: "mine", family: "AWFM" } });
  assert.equal(done.action, "overwrite");
  assert.equal(done.target.name, "Mine");
  assert.equal(planPresetSave({ raw: "mine", family: "AWFM", presets, confirmed: { name: "other", family: "AWFM" } }).action, "confirmOverwrite");
});

test("rename keeps the family and avoids clashes", () => {
  const target = presets[2];
  assert.deepEqual(planPresetSave({ raw: "Mine 2", presets, renaming: target }), { action: "rename", name: "Mine 2", family: "AWFM", target });
  assert.equal(planPresetSave({ raw: "mine", presets, renaming: target }).action, "rename"); // only the case changes
  assert.equal(planPresetSave({ raw: "errors", presets, renaming: target }).error, "reserved");
  assert.equal(planPresetSave({ raw: "zeta", presets: [...presets, { name: "Zeta", family: "AWFM" }], renaming: target }).error, "duplicate");
});

test("name problems and the user limit", () => {
  assert.equal(planPresetSave({ raw: "  ", presets }).error, "empty");
  assert.equal(planPresetSave({ raw: "x".repeat(MAX_NAME + 1), presets }).error, "tooLong");
  const many = Array.from({ length: 200 }, (_, i) => ({ name: "p" + i, family: null, builtin: false }));
  assert.equal(planPresetSave({ raw: "fresh", presets: many }).error, "tooMany");
});

test("request body, file helpers and import summary", () => {
  assert.deepEqual(presetBody({ name: "a", filter: F }), { name: "a", family: null, description: "", filter: F });
  assert.equal(formatFromFileName("x.TOML"), "toml");
  assert.equal(formatFromFileName("x.json"), "json");
  assert.equal(formatFromFileName("x.txt"), null);
  assert.deepEqual(importPayload("json", '﻿{"schema":1}'), { schema: 1 });
  assert.equal(importPayload("toml", "schema = 1"), "schema = 1");
  assert.deepEqual(importSummary({ added: 2, updated: 1, ignored: [{ name: "x", message: "bad" }] }),
    { added: 2, updated: 1, ignored: 1, ignoredItems: [{ name: "x", message: "bad" }] });
  assert.deepEqual(importSummary(null), { added: 0, updated: 0, ignored: 0, ignoredItems: [] });
  assert.equal(exportFile({ format: "toml", text: "a" }).text, "a");
  assert.equal(JSON.parse(exportFile({ format: "json", data: { a: 1 } }).text).a, 1);
});
