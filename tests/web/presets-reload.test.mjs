// Presets panel (9.10 review): a reload that starts while a save or a delete is in flight must not leave the list
// older than the file (the reload may read before the write; the mutation then triggers one more read).
import test from "node:test";
import assert from "node:assert/strict";

import { installGlobalDom, tick } from "./fixtures/fake-dom.mjs";

const doc = installGlobalDom();
const { initPresets } = await import("../../src/dmslog/web/js/presets.js");

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

function setup(initial = []) {
  const container = doc.createElement("div");
  doc.body.appendChild(container);
  const lists = [];
  const writes = [];
  const api = {
    presets: {
      list() { const d = deferred(); lists.push(d); return d.promise; },
      create(body) { const d = deferred(); writes.push({ kind: "create", body, d }); return d.promise; },
      remove() { const d = deferred(); writes.push({ kind: "remove", d }); return d.promise; },
    },
  };
  const panel = initPresets(container, { api, caseId: "c1", getFilter: () => ({ op: "and", children: [] }), onApply() {}, onError() {} });
  return { container, lists, writes, panel, initial };
}
const names = (container) => container.all().filter((n) => n.className.split(/\s+/).includes("pr-apply")).map((n) => n.textContent);
const user = (name) => ({ name, family: null, description: "", filter: { op: "and", children: [] }, builtin: false });

test("a reload that read before a pending Save wrote is followed by one more read", async () => {
  const t = setup();
  t.lists[0].resolve({ presets: [] });
  await tick();
  const input = t.container.all().find((n) => n.tagName === "INPUT" && n.type === "text");
  input.value = "Mine";
  t.container.all().find((n) => n.tagName === "FORM").dispatch("submit", { preventDefault() {} });
  assert.equal(t.writes.length, 1);
  t.panel.refresh(); // the import reload, while the save is still in flight
  assert.equal(t.lists.length, 2);
  t.lists[1].resolve({ presets: [] }); // read before the write
  await tick();
  t.writes[0].d.resolve({});
  await tick();
  assert.equal(t.lists.length, 3, "the save asks for a new read");
  t.lists[2].resolve({ presets: [user("Mine")] });
  await tick();
  assert.deepEqual(names(t.container), ["Mine"]);
});

test("a reload that read before a pending delete finished is followed by one more read", async () => {
  const t = setup();
  t.lists[0].resolve({ presets: [user("Old")] });
  await tick();
  const del = () => t.container.all().find((n) => n.className.split(/\s+/).includes("pr-delete"));
  del().click(); // asks
  del().click(); // confirms
  assert.equal(t.writes.length, 1);
  t.panel.refresh();
  t.lists[1].resolve({ presets: [user("Old")] });
  await tick();
  t.writes[0].d.resolve({});
  await tick();
  assert.equal(t.lists.length, 3);
  t.lists[2].resolve({ presets: [] });
  await tick();
  assert.deepEqual(names(t.container), []);
});
