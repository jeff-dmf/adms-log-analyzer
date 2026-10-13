// Pure model of the collections panel: what a row says, the selection leaf, the grid tags and the
// plain-language outcomes of removing a collection. Data is synthetic.
import test from "node:test";
import assert from "node:assert/strict";

import { buildFilter } from "../../src/dmslog/web/js/filters.js";
import { fieldOptions, valueText } from "../../src/dmslog/web/js/distinct-model.js";
import {
  colorIndex, columnShown, describe, effectiveSourceIds, formatCapture, gridTags, isMulti, loadColumnPref,
  missingLine, pruneSelection, removeConfirmText, removeErrorMessage, removedMessage, saveColumnPref,
  selectionLeaf, selectionLine, shortGuid, tagMap, title,
} from "../../src/dmslog/web/js/collections-model.js";

const GUID = "0A1B2C3D-0000-1111-2222-333344445555";

function ers(group, number, extra = {}) {
  return {
    id: group, group, number, tag: "C" + number, kind: "ers", guid: GUID, capture: "20260101120000",
    server: "zoneA", machine: null, system: "zoneA", label: "", variants: ["enterprise", "sensitive"],
    missing: null, status: "ready", sources: 12, records: 3456, first_ts: Date.UTC(2026, 0, 1, 12, 0, 0),
    last_ts: Date.UTC(2026, 0, 1, 13, 0, 0),
    members: [
      { id: group, kind: "ers", variant: "enterprise", name: "ERS_CL_x_Enterprise.zip", sources: 7, records: 2000, status: "ready" },
      { id: group + 1, kind: "ers", variant: "sensitive", name: "ERS_CL_x_Sensitive_Enterprise.zip", sources: 5, records: 1456, status: "ready" },
    ],
    ...extra,
  };
}

const LIST = [ers(4, 1), ers(9, 2, { kind: "folder", guid: null, capture: null, members: [{ id: 9, name: "Log" }] })];

test("colors repeat every eight collections and never fail", () => {
  assert.equal(colorIndex(1), 1);
  assert.equal(colorIndex(8), 8);
  assert.equal(colorIndex(9), 1);
  assert.equal(colorIndex(undefined), 1);
});

test("isMulti and the tag maps follow the list", () => {
  assert.equal(isMulti([]), false);
  assert.equal(isMulti([ers(1, 1)]), false);
  assert.equal(isMulti(LIST), true);
  assert.deepEqual([...tagMap(LIST)], [[4, "C1"], [9, "C2"]]);
  assert.deepEqual(gridTags(LIST).get(9), { tag: "C2", color: 2 });
});

test("titles name the kind, the capture and the server", () => {
  assert.equal(formatCapture("20260101120000"), "2026-01-01 12:00:00");
  assert.equal(title(LIST[0]), "ERS 2026-01-01 12:00:00 (zoneA)");
  assert.equal(title(LIST[1]), "Folder Log");
  assert.equal(title({ kind: "file", members: [{ name: "a.log" }], server: null }), "File a.log");
  assert.equal(title({ kind: "zip", members: [{ name: "a.zip" }], server: null }), "Zip a.zip");
  assert.equal(shortGuid(GUID), "0A1B2C3D...");
});

test("a half capture says what is there and what is missing", () => {
  const half = ers(4, 1, { variants: ["sensitive"], missing: "enterprise" });
  assert.equal(missingLine(half), "Sensitive only: Enterprise zip not added yet");
  assert.equal(missingLine(ers(4, 1)), "");
  const d = describe(half);
  assert.equal(d.tag, "C1");
  assert.equal(d.counts, "3,456 records in 12 sources");
  assert.equal(d.window, "2026-01-01 12:00:00.000 to 2026-01-01 13:00:00.000");
  assert.equal(d.members[0].variant, "Enterprise");
  assert.equal(describe(ers(4, 1, { status: "indexing" })).status, "Indexing...");
  assert.equal(describe(ers(4, 1, { status: "error" })).status, "Some sources failed");
});

test("selection becomes the collection leaf and drops vanished groups", () => {
  assert.equal(selectionLeaf(new Set()), null);
  assert.deepEqual(selectionLeaf(new Set([9, 4])), { collection: [9, 4] });
  assert.deepEqual(pruneSelection(new Set([4, 77]), LIST), [4]);
  assert.equal(selectionLine(new Set(), LIST), "Showing all collections.");
  assert.equal(selectionLine(new Set([4, 9]), LIST), "Showing only C1, C2.");
});

test("the filter bar and the sources narrow by collection", () => {
  assert.deepEqual(buildFilter({ sourceIds: [], collectionIds: [4, 9] }), { and: [{ collection: [4, 9] }] });
  assert.equal(buildFilter({ sourceIds: [], collectionIds: [] }), null);
  const sources = [{ id: 1, collection: 4 }, { id: 2, collection: 9 }, { id: 3, collection: 9 }];
  assert.deepEqual(effectiveSourceIds(sources, [], [9]), [2, 3]);
  assert.deepEqual(effectiveSourceIds(sources, [1, 2], [9]), [2]);
  assert.deepEqual(effectiveSourceIds(sources, [1], []), [1]);
});

test("removal names the collection and what goes with it, and explains each 409", () => {
  const text = removeConfirmText(LIST[0]);
  assert.match(text, /C1/);
  assert.match(text, /ERS 2026-01-01 12:00:00 \(zoneA\)/);
  assert.match(text, /12 sources and 3,456 records/);
  assert.match(text, /bookmarks/);
  assert.match(text, /other collections stay/);
  assert.match(removeErrorMessage({ status: 409, code: "job_running", message: "x" }, "C2"), /Cannot remove C2 while the case is indexing/);
  assert.match(removeErrorMessage({ status: 409, code: "collection_followed", message: "x" }, "C2"), /following this folder/);
  assert.match(removeErrorMessage({ status: 404, code: "collection_not_found", message: "x" }, "C2"), /no longer in the case/);
  assert.equal(removeErrorMessage({ status: 500, code: "x", message: "Server said no" }, "C2"), "Server said no");
  assert.equal(
    removedMessage("C2", { sources: 5, records: 1200, bookmarks: 2 }),
    "Removed C2: 5 sources, 1,200 records and 2 bookmarks.",
  );
});

test("the Collection column shows only for several collections and when not hidden", () => {
  const store = new Map();
  const storage = { getItem: (k) => (store.has(k) ? store.get(k) : null), setItem: (k, v) => store.set(k, v), removeItem: (k) => store.delete(k) };
  assert.equal(loadColumnPref(storage), true);
  assert.equal(columnShown(LIST, true), true);
  assert.equal(columnShown([LIST[0]], true), false);
  saveColumnPref(false, storage);
  assert.equal(loadColumnPref(storage), false);
  assert.equal(columnShown(LIST, loadColumnPref(storage)), false);
  saveColumnPref(true, storage);
  assert.equal(loadColumnPref(storage), true);
  assert.equal(loadColumnPref(null), true);
});

test("the distinct panel offers Collection only for several collections, labelled C<n>", () => {
  const without = fieldOptions([], []).map((o) => o.value);
  assert.equal(without.includes("collection"), false);
  const withIt = fieldOptions([], [], true).find((o) => o.value === "collection");
  assert.deepEqual([withIt.label, withIt.group], ["Collection", "column"]);
  assert.equal(valueText("collection", 9, "C2"), "C2");
  assert.equal(valueText("collection", 9, undefined), "9");
});
