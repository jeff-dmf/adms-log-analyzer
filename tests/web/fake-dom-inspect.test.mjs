// Guard for a hazard that took the whole machine down (systemd-oomd, 2026-10-16): when an assertion that compares two
// fake-DOM nodes FAILS, node's assert formats both with util.inspect(depth 1000, customInspect off). If the nodes expose
// their cyclic links (parent, children, doc...) as enumerable properties that walks the entire document.
import test from "node:test";
import assert from "node:assert/strict";

import { installGlobalDom } from "./fixtures/fake-dom.mjs";

test("the links that make the node graph cyclic are not enumerable", () => {
  const doc = installGlobalDom();
  const node = doc.createElement("li");
  doc.body.appendChild(node);
  node.appendChild(doc.createElement("input"));
  for (const key of ["doc", "ownerDocument", "children", "parent", "listeners"]) {
    assert.equal(Object.keys(node).includes(key), false, key + " must not be enumerable");
    assert.equal(key in node, true, key + " must still exist");
  }
});

test("a failing assert.equal on two nodes of a big document produces a small message, quickly", () => {
  const doc = installGlobalDom();
  const list = doc.createElement("ul");
  doc.body.appendChild(list);
  const inputs = [];
  for (let i = 0; i < 300; i += 1) {
    const row = doc.createElement("li");
    list.appendChild(row);
    const input = doc.createElement("input");
    row.appendChild(input);
    inputs.push(input);
  }
  inputs[0].focus();
  let message = "";
  try {
    assert.equal(doc.activeElement, inputs[1]);
  } catch (error) {
    message = String(error.message);
  }
  assert.notEqual(message, "", "the assertion must fail");
  assert.ok(message.length < 20000, "the failure message stays small, got " + message.length);
});
