import test from "node:test";
import assert from "node:assert/strict";
import {
  History, MAX_DEPTH, MAX_NODES, addChild, buildFilter, changeLeafType, checkViewName, duplicateNode,
  emptyTree, findNode, fromJson, makeGroup, makeLeaf, measureJson, moveNode, registerLeafType, removeNode,
  sanitizeName, setGroupOp, ungroup, updateLeaf, wrapNode,
} from "../../src/dmslog/web/js/filter-tree-model.js";
import { buildFilter as simpleFilter } from "../../src/dmslog/web/js/filters.js";

const sev = (value) => makeLeaf("sev", { op: "le", value });

test("empty tree serializes to null", () => {
  assert.deepEqual(buildFilter(emptyTree()), { filter: null, errors: [], limit: null, nodes: 0, depth: 0 });
});

test("single leaf collapses the root group", () => {
  const root = addChild(emptyTree(), emptyTree().id, sev(2));
  assert.equal(buildFilter(root).filter, null); // wrong group id: unchanged
  const r = emptyTree();
  const tree = addChild(r, r.id, sev(2));
  assert.deepEqual(buildFilter(tree).filter, { sev: { le: 2 } });
});

test("AND/OR/NOT nesting serializes to the contract", () => {
  const root = emptyTree();
  const orGroup = makeGroup("or", [makeLeaf("source", { ids: [3, 4] }), makeLeaf("text", { fts: " timeout " })]);
  const notGroup = makeGroup("not", [sev(3)]);
  let tree = addChild(root, root.id, orGroup);
  tree = addChild(tree, root.id, notGroup);
  tree = addChild(tree, root.id, makeLeaf("ts", { from: 10, to: 20 }));
  assert.deepEqual(buildFilter(tree).filter, {
    and: [
      { or: [{ source: [3, 4] }, { text: { fts: "timeout" } }] },
      { not: { sev: { le: 3 } } },
      { ts: { ge: 10, le: 20 } },
    ],
  });
});

test("NOT with several children negates their AND; empty groups vanish", () => {
  const root = emptyTree();
  const tree = addChild(addChild(addChild(root, root.id, makeGroup("not", [sev(1), makeLeaf("level", { op: "ge", value: 3 })])),
    root.id, makeGroup("or")), root.id, sev(5));
  assert.deepEqual(buildFilter(tree).filter, {
    and: [{ not: { and: [{ sev: { le: 1 } }, { level: { ge: 3 } }] } }, { sev: { le: 5 } }],
  });
});

test("nested groups of the same operator flatten and double negation cancels", () => {
  const inner = makeGroup("and", [sev(1), sev(2)]);
  const r = emptyTree();
  const tree = addChild(addChild(r, r.id, inner), r.id, makeGroup("not", [makeGroup("not", [sev(4)])]));
  assert.deepEqual(buildFilter(tree).filter, { and: [{ sev: { le: 1 } }, { sev: { le: 2 } }, { sev: { le: 4 } }] });
});

test("round trip of JSON, including leaves without a form", () => {
  const json = {
    and: [
      { source: [1] },
      { not: { or: [{ tid: 17604 }, { tpl: ["WFM-081"] }] } },
      { or: [{ event_status: "fail" }, { flags: "partial" }] },
      { not: { and: [{ sev: { le: 2 } }, { text: { fts: "x" } }] } },
      { sev: { le: 2, ge: 1 } },
    ],
  };
  const tree = fromJson(json);
  const back = buildFilter(tree);
  assert.deepEqual(back.errors, []);
  assert.deepEqual(back.filter, json);
  assert.equal(findNode(tree, tree.children[4].id).type, "raw"); // two operators: kept verbatim
  assert.equal(tree.children[0].type, "source");
});

test("a non-AND top node is wrapped in the root group", () => {
  const tree = fromJson({ or: [{ sev: { le: 1 } }, { sev: { ge: 5 } }] });
  assert.equal(tree.op, "and");
  assert.equal(tree.children[0].op, "or");
  assert.deepEqual(buildFilter(tree).filter, { or: [{ sev: { le: 1 } }, { sev: { ge: 5 } }] });
});

test("the model produces what the simple filter bar produces", () => {
  const state = { sourceIds: [3, 4], sevMax: 2, levelMax: 3, from: 10, to: 20, text: "timeout" };
  const tree = fromJson(simpleFilter(state));
  assert.deepEqual(buildFilter(tree).filter, simpleFilter(state));
});

test("leaf validation reports codes per node and blocks the filter", () => {
  const r = emptyTree();
  const bad = [
    makeLeaf("source", { ids: [] }),
    makeLeaf("sev", { op: "le", value: 9 }),
    makeLeaf("ts", { from: 5, to: 1 }),
    makeLeaf("ts", {}),
    makeLeaf("text", { fts: " - " }),
    makeLeaf("level", { op: "zz", value: 1 }),
  ];
  const tree = bad.reduce((t, leaf) => addChild(t, r.id, leaf), r);
  const out = buildFilter(tree);
  assert.equal(out.filter, null);
  assert.deepEqual(out.errors.map((e) => e.code), ["emptyList", "badValue", "badRange", "emptyRange", "emptyText", "badValue"]);
  assert.deepEqual(out.errors.map((e) => e.id), bad.map((l) => l.id));
});

test("depth and node limits match the backend", () => {
  const nest = (levels) => {
    let json = { sev: { le: 1 } };
    for (let i = 0; i < levels; i += 1) {
      json = { not: { or: [{ sev: { le: 2 } }, json] } }; // two levels per step, no flattening
    }
    return json;
  };
  assert.equal(measureJson(nest(7)).depth, 15);
  assert.equal(buildFilter(fromJson(nest(7))).limit, null);
  const deep = buildFilter(fromJson(nest(8)));
  assert.equal(measureJson(nest(8)).depth, MAX_DEPTH + 1);
  assert.equal(deep.limit, "depth");
  assert.equal(deep.filter, null);

  const r = emptyTree();
  const leaves = Array.from({ length: MAX_NODES }, (_, i) => sev(i % 5 + 1));
  const many = leaves.reduce((t, l) => addChild(t, r.id, l), r);
  const out = buildFilter(many);
  assert.equal(out.limit, "nodes"); // 200 leaves + the and node
  assert.equal(out.nodes, MAX_NODES + 1);
  const ok = buildFilter(removeNode(many, many.children[0].id));
  assert.equal(ok.limit, null);
  assert.equal(ok.nodes, MAX_NODES);
  assert.deepEqual(measureJson({ and: [{ not: { sev: { le: 1 } } }] }), { nodes: 3, depth: 3 });
});

test("wrap, duplicate, ungroup, set operator, move and remove", () => {
  const r = emptyTree();
  const a = sev(1);
  const b = sev(2);
  let tree = addChild(addChild(r, r.id, a), r.id, b);
  tree = wrapNode(tree, a.id, "not");
  assert.deepEqual(buildFilter(tree).filter, { and: [{ not: { sev: { le: 1 } } }, { sev: { le: 2 } }] });
  tree = duplicateNode(tree, b.id);
  assert.equal(tree.children.length, 3);
  assert.notEqual(tree.children[1].id, tree.children[2].id);
  tree = setGroupOp(tree, tree.id, "or");
  assert.equal(buildFilter(tree).filter.or.length, 3);
  tree = moveNode(tree, tree.children[2].id, -2);
  assert.equal(tree.children[0].data.value, 2);
  const wrapper = tree.children[1];
  tree = ungroup(tree, wrapper.id);
  assert.equal(tree.children.length, 3);
  assert.equal(tree.children[1].id, a.id);
  tree = removeNode(tree, a.id);
  assert.equal(tree.children.length, 2);
  assert.equal(removeNode(tree, tree.id), tree);
  assert.equal(wrapNode(tree, tree.id, "not"), tree);
});

test("edits do not mutate the previous tree", () => {
  const r = emptyTree();
  const leaf = sev(2);
  const t1 = addChild(r, r.id, leaf);
  const t2 = updateLeaf(t1, leaf.id, { value: 4 });
  assert.equal(t1.children[0].data.value, 2);
  assert.equal(t2.children[0].data.value, 4);
  assert.equal(r.children.length, 0);
  const t3 = changeLeafType(t2, leaf.id, "text");
  assert.equal(t3.children[0].type, "text");
  assert.equal(t3.children[0].data.fts, "");
});

test("editing is capped so a runaway tree cannot be built", () => {
  const r = emptyTree();
  let tree = r;
  for (let i = 0; i < MAX_NODES * 3; i += 1) {
    tree = addChild(tree, r.id, sev(1));
  }
  assert.ok(tree.children.length <= MAX_NODES * 2);
});

test("history supports undo and redo and clears redo on a new edit", () => {
  const r = emptyTree();
  const h = new History(r);
  const t1 = h.push(addChild(r, r.id, sev(1)));
  const t2 = h.push(addChild(t1, r.id, sev(2)));
  assert.equal(h.push(t2), t2);
  assert.equal(h.past.length, 2);
  assert.equal(h.undo(), t1);
  assert.equal(h.undo(), r);
  assert.equal(h.canUndo, false);
  assert.equal(h.redo(), t1);
  h.push(addChild(t1, r.id, sev(3)));
  assert.equal(h.canRedo, false);
  const small = new History(r, 2);
  [1, 2, 3, 4].forEach((v) => small.push(addChild(r, r.id, sev(v))));
  assert.equal(small.past.length, 2);
});

test("extension point: registered leaf types serialize and parse", () => {
  registerLeafType("tpl", {
    defaults: () => ({ codes: [] }),
    validate: (d) => (d.codes.length === 0 ? "emptyList" : null),
    toJson: (d) => ({ tpl: d.codes.slice() }),
    fromJson: (j) => (Array.isArray(j.tpl) ? { codes: j.tpl.slice() } : null),
  });
  assert.throws(() => registerLeafType("tpl", {}));
  assert.throws(() => registerLeafType("raw", {}));
  const tree = fromJson({ and: [{ tpl: ["WFM-081"] }, { sev: { le: 2 } }] });
  assert.equal(tree.children[0].type, "tpl");
  assert.deepEqual(buildFilter(tree).filter, { and: [{ tpl: ["WFM-081"] }, { sev: { le: 2 } }] });
});

test("saved filter names are sanitized like the server does", () => {
  assert.equal(sanitizeName("  Errors \t only\n"), "Errors only");
  assert.equal(sanitizeName("a‮b\u0000c"), "abc");
  assert.deepEqual(checkViewName("  ", []), { name: "", error: "empty" });
  assert.equal(checkViewName("x".repeat(81), []).error, "tooLong");
  assert.equal(checkViewName("x".repeat(80), []).error, null);
  assert.equal(checkViewName("ERRORS", ["Errors"]).error, "duplicate");
  assert.equal(checkViewName("new", ["a", "b"], 2).error, "tooMany");
});

test("payload leaf round-trips the contract shape", async () => {
  const { fromJson, buildFilter, findNode } = await import("../../src/dmslog/web/js/filter-tree-model.js");
  const json = { payload: { roles: ["request"], fmt: ["xml", "json"], orphan: true } };
  const root = fromJson(json);
  const leaf = root.children[0];
  assert.equal(leaf.type, "payload");
  assert.deepEqual(buildFilter(root).filter, json);
  assert.equal(findNode(root, leaf.id), leaf);
  assert.deepEqual(buildFilter(fromJson({ payload: {} })).filter, { payload: {} });
  assert.deepEqual(buildFilter(fromJson({ payload: { orphan: false } })).filter, { payload: { orphan: false } });
  assert.equal(fromJson({ payload: { bogus: 1 } }).children[0].type, "raw");
});
