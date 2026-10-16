// UI-21 review: the row menus with the real ui-menu.js on the fake DOM: never two menus open, no document listener left behind.
import test from "node:test";
import assert from "node:assert/strict";

import { installGlobalDom, tick, menuEls } from "./fixtures/fake-dom.mjs";

const doc = installGlobalDom();
const { CollectionsPanel } = await import("../../src/dmslog/web/js/collections-panel.js");

const group = (id, n, extra = {}) => ({
  id, group: id, number: n, tag: "C" + n, kind: "ers", guid: "0A1B2C3D", capture: "20260101120000", server: "z" + n,
  variants: [], missing: null, status: "ready", sources: 1, records: 10 * n, members: [], ...extra,
});
const listeners = () => (doc.listeners.pointerdown || []).length;

async function setup() {
  for (const m of menuEls(doc)) m.remove(); // the previous test's leftovers
  doc.listeners.pointerdown = [];
  let list = [group(4, 1), group(9, 2)];
  const host = doc.createElement("section");
  doc.body.appendChild(host);
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  const panel = new CollectionsPanel({
    host, note: doc.createElement("div"), getCase: () => "c",
    api: { collections: async () => ({ ordering: "approximate", collections: list }) },
    loadMenu: () => gate.then(() => import("../../src/dmslog/web/js/ui-menu.js")),
  });
  await panel.refresh();
  const triggers = () => host.all().filter((n) => n.className === "ui-button col-menu" || n.className.split(" ").includes("col-menu"));
  return { host, panel, triggers, release, setList: (l) => { list = l; } };
}

test("two clicks on different row menus while the module loads open one menu, and a redraw leaves no menu or listener", async () => {
  const { panel, triggers, release, setList } = await setup();
  const [a, b] = triggers();
  let opened = 0;
  const add = doc.addEventListener;
  doc.addEventListener = (t, fn, c) => { if (t === "pointerdown") opened += 1; return add(t, fn, c); };
  a.dispatch("click", { detail: 0 });
  b.dispatch("click", { detail: 0 });
  release();
  await tick();
  await tick();
  doc.addEventListener = add;
  assert.equal(opened, 1); // the first click's request was dropped: its menu was never opened
  assert.equal(menuEls(doc).length, 1);
  assert.equal(listeners(), 1);
  assert.equal(panel.activeMenuGroup, 9); // the latest click wins
  setList([group(4, 1), group(9, 2, { records: 1 })]);
  await panel.refresh();
  assert.equal(menuEls(doc).length, 0);
  assert.equal(listeners(), 0);
  // the dropped first click did not leave its button dead
  triggers()[0].dispatch("click", { detail: 0 });
  await tick();
  await tick();
  assert.equal(menuEls(doc).length, 1);
});

test("a menu of one row opened after another row's menu closes the first", async () => {
  const { triggers, release } = await setup();
  release();
  await tick();
  const [a, b] = triggers();
  a.dispatch("click", { detail: 0 });
  await tick();
  await tick();
  assert.equal(menuEls(doc).length, 1);
  b.dispatch("click", { detail: 0 });
  await tick();
  await tick();
  assert.equal(menuEls(doc).length, 1);
  assert.equal(listeners(), 1);
});
