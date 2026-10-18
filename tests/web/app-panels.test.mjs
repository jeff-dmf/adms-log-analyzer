// UI-12: the panel catalogue of app-actions.js follows docs/notas/ui-contratos.md (2.6): ids and visual order,
// the reasons each panel listens to, the panels tied to the case, and a registered title for each one.
import test from "node:test";
import assert from "node:assert/strict";

import { registerAppPanels } from "../../src/dmslog/web/js/app-actions.js";
import { S } from "../../src/dmslog/web/js/strings.js";

function catalogue() {
  const defs = [];
  registerAppPanels({ register: (def) => defs.push(def) }, { api: {}, derivedApi: {} });
  return defs;
}

test("the panels are registered in the visual order of the old hosts, then the four that sat before the filter bar", () => {
  assert.deepEqual(catalogue().map((d) => d.id), [
    "filterTree", "unknownTypes", "config", "operations", "payloads", "dashboard", "missingSteps", "sideBySide", "sameKey",
    "bookmarks", "compare", "alerts", "highlights", "distinct", "groupByType", "aggregate", "extract", "compareViews",
  ]);
});

test("every panel has a title in strings.js and a mount()", () => {
  for (const def of catalogue()) {
    assert.ok(S[def.titleKey], def.id + " title");
    assert.equal(typeof def.mount, "function", def.id);
  }
});

test("listens and closeOnCase follow the contract table", () => {
  const by = Object.fromEntries(catalogue().map((d) => [d.id, d]));
  const listens = (id) => by[id].listens || [];
  assert.deepEqual(listens("distinct"), ["view"]);
  assert.deepEqual(listens("groupByType"), ["view"]);
  assert.deepEqual(listens("aggregate"), ["view"]);
  assert.deepEqual(listens("extract"), ["view"]);
  assert.deepEqual(listens("compareViews"), ["view"]);
  assert.deepEqual(listens("operations"), ["derived"]);
  assert.deepEqual(listens("dashboard"), ["derived"]);
  assert.deepEqual(listens("missingSteps"), ["derived"]);
  assert.deepEqual(listens("unknownTypes"), ["case", "indexed"]);
  assert.deepEqual(listens("config"), ["case", "indexed"]);
  assert.deepEqual(listens("bookmarks"), ["case", "indexed"]);
  for (const id of ["filterTree", "payloads", "sideBySide", "sameKey", "compare", "alerts", "highlights"]) {
    assert.deepEqual(listens(id), [], id);
  }
  assert.deepEqual(Object.keys(by).filter((id) => by[id].closeOnCase), ["payloads", "sideBySide", "sameKey", "compare"]);
});
