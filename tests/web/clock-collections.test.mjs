// 9.15: the collection rows (badge "Clock +1.234s (from pair)", the Align / Reset items of the row menu, the order note that
// says the order uses adjusted time) and the Adjusted time column of the grid. Fake DOM of collections-flow; synthetic data.
import test from "node:test";
import assert from "node:assert/strict";

class FakeNode {
  constructor(tag) {
    this.tag = tag;
    this.children = [];
    this.attrs = {};
    this.listeners = {};
    this.dataset = {};
    this.className = "";
    this.hidden = false;
    this.disabled = false;
    this.checked = false;
    this.value = "";
    this._text = "";
    this.style = {};
    this.title = "";
  }
  get textContent() { return this._text + this.children.map((c) => c.textContent).join(""); }
  set textContent(value) { this._text = String(value); this.children = []; }
  appendChild(child) { child.parent = this; this.children.push(child); return child; }
  append(...nodes) { for (const node of nodes) { node.parent = this; this.children.push(node); } }
  setAttribute(name, value) { this.attrs[name] = String(value); }
  removeAttribute(name) { delete this.attrs[name]; }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  querySelector(selector) {
    const pairs = [...selector.matchAll(/\[data-(\w+)="([^"]*)"\]/g)];
    const found = this.find((n) => n !== this && pairs.length > 0 && pairs.every(([, k, v]) => n.dataset[k] === v));
    return found.length > 0 ? found[0] : null;
  }
  focus() {
    for (let n = this; n; n = n.parent) {
      if (n.hidden) { return; }
    }
    document.activeElement = this;
  }
  get classList() { return { toggle() {}, add() {} }; }
  fire(type, event = {}) { for (const fn of this.listeners[type] || []) { fn({ preventDefault() {}, stopPropagation() {}, key: "", ...event }); } }
  find(predicate, out = []) {
    if (predicate(this)) { out.push(this); }
    for (const child of this.children) { child.find(predicate, out); }
    return out;
  }
}

globalThis.document = {
  activeElement: null,
  body: new FakeNode("body"),
  createElement: (tag) => new FakeNode(tag),
  createTextNode: (text) => Object.assign(new FakeNode("#text"), { _text: text }),
};

const { CollectionsPanel } = await import("../../src/dmslog/web/js/collections-panel.js");
const { S } = await import("../../src/dmslog/web/js/strings.js");

const byClass = (root, cls) => root.find((n) => n.className.split(" ").includes(cls));
const tick = () => new Promise((r) => setTimeout(r, 0));

const menus = [];
const fakeMenuLoader = () => Promise.resolve({
  createMenu: (opts) => {
    const menu = { opts, open() { opts.getItems(); }, isOpen: () => false, close() {} };
    menus.push(menu);
    return menu;
  },
});
const group = (id, number, extra = {}) => ({
  id, group: id, number, tag: "C" + number, kind: "ers", guid: "0A1B2C3D-0000-1111-2222-333344445555",
  capture: "20260101120000", server: "zone" + number, variants: ["enterprise"], missing: null, status: "ready", sources: 3,
  records: 100, first_ts: 1000, last_ts: 2000, clock_offset_ms: 0, clock_source: null,
  members: [{ id, name: "a.zip", variant: "enterprise", sources: 3, records: 100, status: "ready" }], ...extra,
});

function setup(list, { clocks = "raw", clock = undefined } = {}) {
  const calls = { lists: [], aligned: [], resets: [] };
  let current = { list, clocks };
  const panel = new CollectionsPanel({
    host: new FakeNode("section"), note: new FakeNode("div"),
    api: { collections: async () => ({ ordering: current.list.length > 1 ? "approximate" : "exact", clocks: current.clocks, collections: current.list }) },
    getCase: () => "case1", loadMenu: fakeMenuLoader,
    onList: (l, ordering, clocksMode) => calls.lists.push([l.length, ordering, clocksMode]),
    clock: clock === null ? undefined : clock || {
      busy: () => calls.busy === true,
      align: async (g, opener) => { calls.aligned.push([g, opener]); },
      reset: async (g) => { calls.resets.push(g); },
    },
  });
  return { panel, calls, set: (next) => { current = next; } };
}

async function menuItems(panel, index) {
  byClass(panel.host, "col-menu")[index].fire("click");
  await tick();
  return menus.at(-1).opts.getItems();
}

test("a row with an offset says it: 'Clock +1.234s (from pair)' with the explanation in the tooltip", async () => {
  const { panel } = setup([group(1, 1), group(2, 2, { clock_offset_ms: 1234, clock_source: { method: "pair", set_at: "2026-10-18T10:00:00Z" } })], { clocks: "adjusted" });
  await panel.refresh();
  const badges = byClass(panel.host, "col-clock");
  assert.equal(badges.length, 1, "only the collection with an offset");
  assert.equal(badges[0].textContent, "Clock +1.234s (from pair)");
  assert.ok(badges[0].title.includes(S.ckBadgeTitle) && badges[0].title.includes("2026-10-18T10:00:00Z"));
});

test("the order note says the order uses adjusted time while an offset exists, and goes back after a reset", async () => {
  const { panel, set } = setup([group(1, 1), group(2, 2)]);
  await panel.refresh();
  assert.equal(panel.note.textContent, S.colApproxOrder);
  set({ list: [group(1, 1), group(2, 2, { clock_offset_ms: -50, clock_source: { method: "manual" } })], clocks: "adjusted" });
  await panel.refresh();
  assert.equal(panel.note.textContent, S.ckOrderNote);
  assert.ok(panel.note.title.includes("Events, exports, key search"));
  assert.equal(byClass(panel.host, "col-order-more")[0].textContent, S.ckOrderMore);
  assert.equal(panel.note.hidden, false);
  set({ list: [group(1, 1), group(2, 2)], clocks: "raw" });
  await panel.refresh();
  assert.equal(panel.note.textContent, S.colApproxOrder);
  assert.equal(byClass(panel.host, "col-clock").length, 0);
});

test("the order note shows with adjusted clocks even when only one collection is left", async () => {
  const { panel } = setup([group(1, 1, { clock_offset_ms: 5, clock_source: { method: "manual" } })], { clocks: "adjusted" });
  await panel.refresh();
  assert.equal(panel.note.hidden, false);
  assert.equal(panel.note.textContent, S.ckOrderNote);
  const raw = setup([group(1, 1)]);
  await raw.panel.refresh();
  assert.equal(raw.panel.note.hidden, true);
});

test("onList tells the owner whether the clocks are adjusted (the grid shows its Adjusted time column from it)", async () => {
  const { panel, calls, set } = setup([group(1, 1), group(2, 2)]);
  await panel.refresh();
  set({ list: [group(1, 1), group(2, 2, { clock_offset_ms: 5 })], clocks: "adjusted" });
  await panel.refresh();
  set({ list: [group(1, 1)], clocks: "bogus" });
  await panel.refresh();
  assert.deepEqual(calls.lists.map((l) => l[2]), ["raw", "adjusted", "raw"]);
  panel.setCase();
  assert.equal(calls.lists.at(-1)[2], "raw");
});

test("a change of the clock alone redraws the rows (the signature includes it)", async () => {
  const { panel, set } = setup([group(1, 1), group(2, 2)]);
  await panel.refresh();
  const before = panel.list.children[1];
  await panel.refresh();
  assert.equal(panel.list.children[1], before, "an identical list is not drawn again");
  set({ list: [group(1, 1), group(2, 2, { clock_offset_ms: 9, clock_source: { method: "manual" } })], clocks: "adjusted" });
  await panel.refresh();
  assert.notEqual(panel.list.children[1], before);
});

test("the row menu offers Align clock (and Reset only with an offset) before Remove", async () => {
  const { panel } = setup([group(1, 1), group(2, 2, { clock_offset_ms: 300, clock_source: { method: "manual" } })], { clocks: "adjusted" });
  await panel.refresh();
  assert.deepEqual((await menuItems(panel, 0)).map((i) => i.id), ["col-clock-align", "col-remove"]);
  const items = await menuItems(panel, 1);
  assert.deepEqual(items.map((i) => i.id), ["col-clock-align", "col-clock-reset", "col-remove"]);
  assert.equal(items[0].label, "Align clock of C2…");
  assert.equal(items[1].label, "Reset clock of C2");
});

test("without the clock option the row menu is as before", async () => {
  const { panel } = setup([group(1, 1), group(2, 2)], { clock: null });
  await panel.refresh();
  assert.deepEqual((await menuItems(panel, 0)).map((i) => i.id), ["col-remove"]);
});

test("Align clock is disabled with one collection and while a change runs; the reasons are in the title", async () => {
  const single = setup([group(1, 1)]);
  await single.panel.refresh();
  const only = (await menuItems(single.panel, 0))[0];
  assert.equal(only.disabled, true);
  assert.equal(only.title, S.ckAlignNeedTwo);
  const two = setup([group(1, 1), group(2, 2, { clock_offset_ms: 3 })]);
  await two.panel.refresh();
  two.calls.busy = true;
  const busy = await menuItems(two.panel, 1);
  assert.deepEqual(busy.slice(0, 2).map((i) => [i.disabled, i.title]), [[true, S.ckBusy], [true, S.ckBusy]]);
  two.calls.busy = false;
  const free = await menuItems(two.panel, 1);
  assert.deepEqual(free.slice(0, 2).map((i) => [i.disabled, i.title]), [[false, null], [false, null]]);
});

test("Align clock hands the group and a focus target to the dialog; Reset runs once and keeps the focus on the row", async () => {
  const { panel, calls } = setup([group(1, 1), group(2, 2, { clock_offset_ms: 3, clock_source: { method: "manual" } })]);
  await panel.refresh();
  const items = await menuItems(panel, 1);
  items[0].run();
  await tick();
  assert.equal(calls.aligned.length, 1);
  assert.equal(calls.aligned[0][0], 2);
  // the opener is a focus target that finds the row's menu button again, even after the rows were drawn anew
  const oldButton = byClass(panel.host, "col-menu")[1];
  panel.signature = null;
  await panel.refresh();
  const newButton = byClass(panel.host, "col-menu")[1];
  assert.notEqual(newButton, oldButton);
  document.activeElement = null;
  calls.aligned[0][1].focus();
  assert.equal(document.activeElement, newButton);
  items[1].run();
  await tick();
  assert.deepEqual(calls.resets, [2]);
});

test("Reset: when the rows were drawn anew and the focus is lost, it goes to the row's menu button", async () => {
  const { panel } = setup([group(1, 1), group(2, 2, { clock_offset_ms: 3 })], {
    clock: { busy: () => false, align: async () => {}, reset: async () => { document.activeElement = null; } },
  });
  await panel.refresh();
  const items = await menuItems(panel, 1);
  items[1].run();
  await tick();
  assert.equal(document.activeElement.dataset.act, "menu");
  assert.equal(document.activeElement.dataset.group, "2");
});

test("Reset does not take the focus when the user moved it elsewhere meanwhile", async () => {
  const elsewhere = new FakeNode("button");
  const { panel } = setup([group(1, 1), group(2, 2, { clock_offset_ms: 3 })], {
    clock: { busy: () => false, align: async () => {}, reset: async () => { elsewhere.focus(); } },
  });
  await panel.refresh();
  const items = await menuItems(panel, 1);
  items[1].run();
  await tick();
  assert.equal(document.activeElement, elsewhere);
});

test("a case change while Reset runs: the focus is not moved into the new case's list", async () => {
  let release;
  const { panel, set } = setup([group(1, 1), group(2, 2, { clock_offset_ms: 3 })], {
    clock: { busy: () => false, align: async () => {}, reset: () => new Promise((resolve) => { release = resolve; }) },
  });
  await panel.refresh();
  const items = await menuItems(panel, 1);
  items[1].run();
  panel.setCase();
  set({ list: [group(7, 1), group(8, 2)], clocks: "raw" });
  await panel.refresh(); // the new case's list is on the page
  document.activeElement = null;
  release();
  await tick();
  assert.equal(document.activeElement, null);
});

test("the order note is a live region: its text is written only when the sentence changes", async () => {
  const { panel } = setup([group(1, 1), group(2, 2)]);
  await panel.refresh();
  let writes = 0;
  let text = panel.note.textContent;
  Object.defineProperty(panel.note, "textContent", { get: () => text, set: (v) => { writes += 1; text = v; }, configurable: true });
  panel.render();
  panel.render();
  assert.equal(writes, 0);
});

test("a list that fails to load says it with a static sentence, never the server's text", async () => {
  const panel = new CollectionsPanel({
    host: new FakeNode("section"), note: new FakeNode("div"),
    api: { collections: async () => { throw Object.assign(new Error("database is locked: /secret/case.db"), { code: "database_error" }); } },
    getCase: () => "case1", loadMenu: fakeMenuLoader,
  });
  await panel.refresh();
  assert.equal(panel.status.textContent, S.colLoadError);
  assert.ok(!panel.status.textContent.includes("secret"));
});

test("reload() tells a failed list from an unchanged one; refresh() keeps answering true/false", async () => {
  const { panel, set } = setup([group(1, 1), group(2, 2)]);
  assert.equal(await panel.reload(), "same");
  let fail = true;
  panel.api.collections = async () => { if (fail) { throw new Error("boom"); } return { ordering: "exact", clocks: "raw", collections: [group(1, 1)] }; };
  assert.equal(await panel.reload(), "failed");
  assert.equal(panel.status.textContent, S.colLoadError);
  assert.equal(await panel.refresh(), false);
  fail = false;
  assert.equal(await panel.reload(), "same");
  void set;
});
