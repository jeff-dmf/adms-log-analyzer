import assert from "node:assert/strict";
import { test } from "node:test";
import {
  buildTree, checkState, closed, defaultOpen, impliedOpen, idsOf, matchedIds, parseOpen, pathParts, serializeOpen, toggled, treeKeyTarget, visibleRows,
} from "../../src/dmslog/web/js/source-tree-model.js";

const src = (id, member, extra = {}) => ({ id, name: member.split("/").pop(), member, records: 10, status: "done", sensitive: false, collection: 1, ...extra });
const SOURCES = [
  src(1, "SchneiderElectricData/ADMS/Log/wfm.log"),
  src(2, "SchneiderElectricData/ADMS/Log/ont.log", { sensitive: true }),
  src(3, "SchneiderElectricData/OASyS/Log/srv.log", { status: "error", records: 0 }),
  src(4, "SchneiderElectricData/OASyS/Cfg/a.cfg"),
  src(5, "Deployment/Logs/install.log"),
];

test("path parts accept both separators and drop empty parts", () => {
  assert.deepEqual(pathParts({ member: "A\\B//C.log" }), ["A", "B", "C.log"]);
  assert.deepEqual(pathParts({ name: "x.log" }), ["x.log"]);
  assert.deepEqual(pathParts({ id: 7 }), ["7"]);
});

test("the tree follows the folders of the ERS, folders first, with a chain of single folders merged", () => {
  const tree = buildTree(SOURCES);
  assert.deepEqual(tree.roots.map((n) => n.name), ["Deployment/Logs", "SchneiderElectricData"]);
  const data = tree.roots[1];
  assert.deepEqual(data.children.map((n) => n.name), ["ADMS/Log", "OASyS"]);
  assert.equal(data.files, 4);
  assert.equal(data.records, 30);
  assert.equal(data.unreadable, 1);
  assert.equal(data.sensitive, 1);
  assert.deepEqual(data.ids.sort(), [1, 2, 3, 4]);
  const oasys = data.children[1];
  assert.deepEqual(oasys.children.map((n) => n.name), ["Cfg", "Log"]);
});

test("several collections get one top folder each, so equal paths do not mix", () => {
  const tree = buildTree([src(1, "A/x.log", { collection: 1 }), src(2, "A/x.log", { collection: 2 })]);
  assert.deepEqual(tree.roots.map((n) => n.name), ["C1/A", "C2/A"]);
  assert.deepEqual(tree.roots.map((n) => n.ids), [[1], [2]]);
});

test("the first level is open by default and the rows carry level, size and position", () => {
  const tree = buildTree(SOURCES);
  const rows = visibleRows(tree, defaultOpen(tree), null);
  assert.deepEqual(rows.map((r) => [r.node.name, r.level]), [
    ["Deployment/Logs", 1], ["install.log", 2], ["SchneiderElectricData", 1], ["ADMS/Log", 2], ["OASyS", 2],
  ]);
  assert.equal(rows[0].setSize, 2);
  assert.equal(rows[2].posInSet, 2);
  assert.equal(rows[3].open, false);
});

test("a filter shows only the paths to a match, all open, and matches folder names too", () => {
  const tree = buildTree(SOURCES);
  const matched = matchedIds(tree, "oasys", SOURCES);
  assert.deepEqual([...matched].sort(), [3, 4]);
  const rows = visibleRows(tree, new Set(), matched);
  assert.deepEqual(rows.map((r) => r.node.name), ["SchneiderElectricData", "OASyS", "Cfg", "a.cfg", "Log", "srv.log"]);
  assert.equal(matchedIds(tree, "  ", SOURCES), null);
});

test("a folder checks everything under it, or only what the filter shows", () => {
  const tree = buildTree(SOURCES);
  const data = tree.roots[1];
  assert.equal(checkState(data.ids, new Set()), "none");
  assert.equal(checkState(data.ids, new Set([1])), "some");
  assert.equal(checkState(data.ids, new Set([1, 2, 3, 4])), "all");
  assert.deepEqual([...toggled(new Set([1]), data.ids)].sort(), [1, 2, 3, 4], "some -> all");
  assert.deepEqual([...toggled(new Set([1, 2, 3, 4, 5]), data.ids)], [5], "all -> none, the rest stays");
  assert.deepEqual(idsOf(data, new Set([3, 5])), [3]);
});

test("arrow keys follow the ARIA tree pattern", () => {
  const tree = buildTree(SOURCES);
  const open = new Set(defaultOpen(tree));
  const rows = visibleRows(tree, open, null); // Deployment/Logs, install.log, Schneider.., ADMS/Log, OASyS
  assert.deepEqual(treeKeyTarget(rows, 0, "ArrowDown"), { focus: 1 });
  assert.equal(treeKeyTarget(rows, 4, "ArrowDown"), null);
  assert.deepEqual(treeKeyTarget(rows, 3, "ArrowRight"), { open: rows[3].node.key });
  assert.deepEqual(treeKeyTarget(rows, 2, "ArrowRight"), { focus: 3 }, "an open folder: first child");
  assert.deepEqual(treeKeyTarget(rows, 2, "ArrowLeft"), { close: rows[2].node.key });
  assert.deepEqual(treeKeyTarget(rows, 1, "ArrowLeft"), { focus: 0 }, "a file: its folder");
  assert.equal(treeKeyTarget(rows, 1, "ArrowRight"), null);
  assert.deepEqual(treeKeyTarget(rows, 2, "Home"), { focus: 0 });
  assert.deepEqual(treeKeyTarget(rows, 2, "End"), { focus: 4 });
});

test("the open folders are remembered as a list of keys and bad text is ignored", () => {
  assert.deepEqual([...parseOpen(serializeOpen(new Set(["a", "b"])))], ["a", "b"]);
  assert.equal(parseOpen("{nope"), null);
  assert.equal(parseOpen('{"a":1}'), null);
  assert.deepEqual([...parseOpen('["a", 3]')], ["a"]);
});

test("a folder with an open folder inside is open, and closing it closes what is inside", () => {
  assert.deepEqual([...impliedOpen(new Set(["A/B/C/"]))].sort(), ["A/", "A/B/", "A/B/C/"]);
  assert.deepEqual([...impliedOpen(new Set(["C1|A/B/"]))].sort(), ["C1|", "C1|A/", "C1|A/B/"]);
  assert.deepEqual([...closed(new Set(["A/", "A/B/", "X/"]), "A/")], ["X/"]);
  // A chain A/B that later splits into A and A/B keeps both open.
  const before = buildTree([src(1, "A/B/x.log")]);
  assert.equal(before.roots[0].name, "A/B");
  const open = defaultOpen(before);
  const after = buildTree([src(1, "A/B/x.log"), src(2, "A/y.log")]);
  const rows = visibleRows(after, open, null);
  assert.deepEqual(rows.map((r) => [r.node.name, r.open]), [["A", true], ["B", true], ["x.log", false], ["y.log", false]]);
});

test("a chain inside a collection that splits keeps the collection folder open too", () => {
  const two = (extra) => [src(1, "A/B/x.log", { collection: 1 }), src(2, "Z/q.log", { collection: 2 }), ...extra];
  const before = buildTree(two([]));
  assert.equal(before.roots[0].name, "C1/A/B");
  const open = defaultOpen(before);
  const after = buildTree(two([src(3, "Other/y.log", { collection: 1 })]));
  const names = visibleRows(after, open, null).map((r) => r.node.name);
  assert.deepEqual(names.slice(0, 4), ["C1", "A/B", "x.log", "Other"]);
  assert.deepEqual([...impliedOpen(new Set(["C1|A/B/"]))].sort(), ["C1|", "C1|A/", "C1|A/B/"]);
});

const fhSrc = (id, n, extra = {}) =>
  src(id, "Svc/Log/DMS_FunctionHost_" + n + ".log", { family: "DMSFH", pool: "DMS_FunctionHost", instance: n, ...extra });

test("pool instances go into one virtual folder in numeric order, with its own key and counters", () => {
  const sources = [10, 2, 1, 11].map((n, i) => fhSrc(i + 1, n)).concat([src(9, "Svc/Log/other.log")]);
  const tree = buildTree(sources);
  const folder = tree.roots[0];
  assert.equal(folder.name, "Svc/Log");
  assert.deepEqual(folder.children.map((n) => n.name), ["DMS_FunctionHost (4 instances)", "other.log"]);
  const pool = folder.children[0];
  assert.equal(pool.folder, true);
  assert.equal(pool.key, "Svc/Log/\u0000pool:DMS_FunctionHost/");
  assert.deepEqual(pool.children.map((n) => n.source.instance), [1, 2, 10, 11]);
  assert.equal(pool.files, 4);
  assert.equal(pool.records, 40);
  assert.deepEqual(pool.children.map((n) => n.label), [
    "DMS_FunctionHost #1, DMS_FunctionHost_1.log", "DMS_FunctionHost #2, DMS_FunctionHost_2.log",
    "DMS_FunctionHost #10, DMS_FunctionHost_10.log", "DMS_FunctionHost #11, DMS_FunctionHost_11.log",
  ]);
  assert.equal(folder.children[1].label, null);
});

test("a pool with one instance also groups, and a folder holding only the pool is not merged with it", () => {
  const tree = buildTree([fhSrc(1, 3)]);
  assert.equal(tree.roots[0].name, "Svc/Log");
  assert.equal(tree.roots[0].children[0].name, "DMS_FunctionHost (1 instance)");
});

test("the pool folder keeps the check state and the keys of any folder; one pool per collection", () => {
  const sources = [fhSrc(1, 1), fhSrc(2, 2), fhSrc(3, 1, { collection: 2 })];
  const tree = buildTree(sources);
  assert.deepEqual(tree.roots.map((n) => n.name), ["C1/Svc/Log", "C2/Svc/Log"]);
  const pool = tree.roots[0].children[0];
  assert.equal(pool.key, "C1|Svc/Log/\u0000pool:DMS_FunctionHost/");
  assert.notEqual(pool.key, tree.roots[1].children[0].key);
  assert.equal(checkState(pool.ids, new Set([1])), "some");
  assert.deepEqual([...toggled(new Set(), pool.ids)].sort(), [1, 2]);
  assert.ok(impliedOpen(new Set([pool.key])).has("C1|Svc/Log/"));
  assert.ok(!closed(new Set([pool.key, "C1|Svc/Log/"]), "C1|Svc/Log/").has(pool.key));
  const rows = visibleRows(tree, new Set(["C1|", "C1|Svc/Log/", pool.key]), null);
  assert.deepEqual(rows.filter((r) => r.node.source).map((r) => r.level), [3, 3]);
});

test("sources without a pool build the same tree as before", () => {
  const tree = buildTree([src(1, "A/x.log", { pool: null, instance: null })]);
  assert.equal(tree.roots[0].children[0].label, null);
  assert.equal(tree.roots[0].children[0].folder, false);
});
