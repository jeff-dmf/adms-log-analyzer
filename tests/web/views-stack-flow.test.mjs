// "Distinct values" panel and "Group by message type" view mounted on a small fake DOM: buttons,
// drill-down, expansion by keyset, and log text written only as text (never as markup).
import test from "node:test";
import assert from "node:assert/strict";

class FakeElement {
  constructor(tag) {
    this.tagName = tag.toUpperCase();
    this.children = [];
    this.parent = null;
    this.attrs = {};
    this.dataset = {};
    this.style = {};
    this.listeners = {};
    this.hidden = false;
    this.disabled = false;
    this.className = "";
    this.id = "";
    this.title = "";
    this.value = "";
    this._text = "";
    const classes = new Set();
    this.classList = { toggle: (c, on) => (on ? classes.add(c) : classes.delete(c)), contains: (c) => classes.has(c) };
  }
  set innerHTML(v) { throw new Error("innerHTML must not be used"); }
  set textContent(v) { this.children = []; this._text = String(v); }
  get textContent() { return this._text + this.children.map((c) => c.textContent).join(""); }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return this.attrs[k]; }
  appendChild(c) { c.parent = this; this.children.push(c); return c; }
  append(...cs) {
    for (const c of cs) {
      if (typeof c === "string") {
        const text = new FakeElement("#text");
        text.textContent = c;
        this.appendChild(text);
      } else {
        this.appendChild(c);
      }
    }
  }
  insertBefore(c, ref) {
    c.parent = this;
    const i = ref ? this.children.indexOf(ref) : -1;
    if (i < 0) { this.children.push(c); } else { this.children.splice(i, 0, c); }
    return c;
  }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  click() { for (const fn of this.listeners.click || []) { fn({ preventDefault() {} }); } }
  focus() { globalThis.document.activeElement = this; }
  all() { return [this, ...this.children.flatMap((c) => c.all())]; }
  find(pred) { return this.all().filter(pred); }
  byClass(c) { return this.find((n) => n.className.split(" ").includes(c)); }
}

const docListeners = {};
globalThis.document = {
  head: new FakeElement("head"),
  activeElement: null,
  createElement: (tag) => new FakeElement(tag),
  querySelector: () => null,
  addEventListener: (type, fn) => { (docListeners[type] ||= []).push(fn); },
};

const { mountViewsStack } = await import("../../src/dmslog/web/js/views-stack.js");
const { createViewsStack, emptyState } = await import("../../src/dmslog/web/js/views-stack-model.js");
const { composeFilter, andFilters } = await import("../../src/dmslog/web/js/filters.js");
const { S, format } = await import("../../src/dmslog/web/js/strings.js");

const tick = async () => { for (let i = 0; i < 6; i += 1) { await Promise.resolve(); } };
const ownFilter = (s) => composeFilter({ sourceIds: [], text: s.searchText, ...s.formState }, s.userFilter, s.toggles);
const HOSTILE = "<img src=x onerror=alert(1)>";

function setup({ caseId = "C1", selected = null, group = null } = {}) {
  const menus = [];
  docListeners.keydown = []; // only this setup's handler is live
  let live = emptyState();
  const stack = createViewsStack({ ownFilter, getLive: () => live });
  const trailHost = new FakeElement("div");
  const actionsHost = new FakeElement("div");
  const parent = trailHost;
  const calls = { apply: [], reveal: [], messages: [], restore: [] };
  const store = new Map();
  const storage = { getItem: (k) => store.get(k) ?? null, setItem: (k, v) => store.set(k, v), removeItem: (k) => store.delete(k) };
  const ctx = { caseId, selected };
  const ui = mountViewsStack({
    trailHost, actionsHost, stack, storage, group,
    createMenu: (config) => { const menu = { config, open() { config.onOpenChange(true); }, close() { config.onOpenChange(false); } }; menus.push(menu); return menu; },
    getCase: () => ctx.caseId,
    restoreState: (state) => { live = state; calls.restore.push(state); },
    applyView: async () => {
      calls.apply.push(andFilters(stack.parentsFilter(), ownFilter(live)));
    },
    getSelectedRow: () => ctx.selected,
    reveal: async (row) => { calls.reveal.push(row); },
    showMessage: (text, isError) => calls.messages.push([text, isError]),
  });
  const bar = new FakeElement("div"); // the two ends of the strip, searched as one
  bar.children = [trailHost, actionsHost];
  const btn = (cls) => bar.byClass(cls)[0];
  const crumbs = () => bar.byClass("vs-crumb");
  const key = (event) => {
    const e = { preventDefault() { e.prevented = true; }, target: new FakeElement("div"), ...event };
    for (const fn of docListeners.keydown || []) { fn(e); }
    return e;
  };
  return { menus, ui, stack, calls, ctx, btn, crumbs, key, store, setLive: (patch) => { live = { ...live, ...patch }; }, bar };
}

test("narrow builds the stack, breadcrumbs show it and the effective filter is the AND", async () => {
  const t = setup();
  assert.equal(t.crumbs().length, 1);
  assert.equal(t.btn("vs-back").disabled, true);
  t.btn("vs-narrow").click();
  assert.deepEqual(t.calls.messages.at(-1), [S.vsNeedFilter, false]);
  t.setLive({ userFilter: { tid: [12] } });
  t.btn("vs-narrow").click();
  await tick();
  assert.deepEqual(t.crumbs().map((c) => c.textContent), ["Thread 12", "View 2"]);
  assert.equal(t.crumbs()[1].getAttribute("aria-current"), "page");
  assert.equal(t.crumbs()[0].getAttribute("aria-current"), undefined);
  assert.deepEqual(t.calls.apply.at(-1), { and: [{ tid: [12] }] });
  t.setLive({ userFilter: { key: { type: "gid", eq: HOSTILE } } });
  t.ui.changed();
  assert.equal(t.crumbs()[1].textContent, "gid = " + HOSTILE); // text only
  t.btn("vs-narrow").click();
  assert.deepEqual(t.crumbs().length, 3);
  assert.equal(t.calls.apply.at(-1).and.length, 2);
});

test("back/forward by buttons, crumbs and Alt+Left/Right; typing targets are left alone", async () => {
  const t = setup();
  t.setLive({ userFilter: { tid: [1] } });
  t.btn("vs-narrow").click();
  t.setLive({ userFilter: { tid: [2] } });
  t.ui.changed();
  t.btn("vs-back").click();
  assert.equal(t.stack.index, 0);
  assert.equal(t.btn("vs-forward").disabled, false);
  assert.equal(t.crumbs()[1].className.includes("is-future"), true);
  assert.deepEqual(t.calls.apply.at(-1), { and: [{ tid: [1] }] });
  const forward = t.key({ key: "ArrowRight", altKey: true });
  assert.equal(forward.prevented, true);
  assert.equal(t.stack.index, 1);
  assert.deepEqual(t.calls.apply.at(-1), { and: [{ tid: [1] }, { tid: [2] }] });
  t.key({ key: "ArrowLeft", altKey: true });
  assert.equal(t.stack.index, 0);
  t.crumbs()[1].click();
  assert.equal(t.stack.index, 1);
  const input = new FakeElement("input");
  const typed = t.key({ key: "ArrowLeft", altKey: true, target: input });
  assert.equal(typed.prevented, undefined);
  assert.equal(t.stack.index, 1);
  t.ctx.caseId = null;
  assert.equal(t.key({ key: "ArrowLeft", altKey: true }).prevented, undefined);
});

test("show in parent / root keeps the selected record and reveals it after the reload", async () => {
  const t = setup({ selected: { id: 77, ts: 5000 } });
  t.setLive({ userFilter: { tid: [1] } });
  t.btn("vs-narrow").click();
  t.setLive({ userFilter: { tid: [2] } });
  t.ui.changed();
  t.btn("vs-narrow").click();
  assert.equal(t.stack.index, 2);
  t.ui.showInParent();
  await tick();
  assert.equal(t.stack.index, 1);
  assert.deepEqual(t.calls.reveal, [{ id: 77, ts: 5000 }]);
  t.ui.showInRoot();
  await tick();
  assert.equal(t.stack.index, 0);
  assert.equal(t.calls.reveal.length, 2);
  t.ui.showInRoot(); // disabled in the browser; still harmless here
  assert.equal(t.calls.messages.at(-1)[0], S.vsAtRoot);
  t.ctx.selected = null;
  t.crumbs()[1].click();
  t.ui.showInParent();
  assert.equal(t.calls.messages.at(-1)[0], S.navNoSelection);
  assert.equal(t.stack.index, 1);
});

test("a newer view change cancels a pending reveal", async () => {
  const t = setup({ selected: { id: 5, ts: null } });
  t.setLive({ userFilter: { tid: [1] } });
  t.btn("vs-narrow").click();
  t.ui.showInParent(); // reveal waits one tick
  t.btn("vs-forward").click(); // newer change before the await finished
  await tick();
  assert.equal(t.calls.reveal.length, 0);
});

test("rename shows the new name; limit message", () => {
  const t = setup();
  t.setLive({ userFilter: { tid: [1] } });
  t.ui.rename();
  const [input] = t.bar.byClass("vs-rename-input");
  input.value = HOSTILE;
  const [form] = t.bar.byClass("vs-rename-form");
  assert.equal(form.hidden, false);
  for (const fn of form.listeners.submit) { fn({ preventDefault() {} }); }
  assert.equal(t.crumbs()[0].textContent, HOSTILE.slice(0, 40));
  assert.equal(form.hidden, true);
  for (let i = 1; i < t.stack.maxDepth; i += 1) {
    t.setLive({ userFilter: { tid: [i] } });
    t.btn("vs-narrow").click();
  }
  t.setLive({ userFilter: { tid: [99] } });
  t.btn("vs-narrow").click();
  assert.deepEqual(t.calls.messages.at(-1), [format(S.vsFullOther, { n: t.stack.maxDepth }), true]);
});

test("the stack is saved per case and comes back on onCase", () => {
  const t = setup();
  t.setLive({ userFilter: { tid: [1] } });
  t.btn("vs-narrow").click();
  assert.equal(t.store.has("dmslog.viewsStack.C1"), true);
  const state = t.ui.onCase("C1"); // same tab, same case: the saved stack returns
  assert.equal(t.stack.size, 2);
  assert.deepEqual(state.userFilter, null);
  assert.equal(t.ui.onCase("C2"), null);
  assert.equal(t.stack.size, 1);
  assert.equal(t.crumbs().length, 1);
});

test("the trail is nav > ol > li with the active view as aria-current=page; back/forward are labelled icons", () => {
  const t = setup();
  t.setLive({ userFilter: { tid: [1] } });
  t.btn("vs-narrow").click();
  const [nav] = t.bar.all().filter((n) => n.tagName === "NAV");
  assert.equal(nav.getAttribute("aria-label"), S.vsCrumbsLabel);
  assert.equal(nav.children[0].tagName, "OL");
  assert.deepEqual(nav.children[0].children.map((n) => n.tagName), ["LI", "LI"]);
  assert.equal(nav.children[0].children[1].children[0].getAttribute("aria-current"), "page");
  assert.equal(t.btn("vs-back").getAttribute("aria-label"), S.vsBack);
  assert.equal(t.btn("vs-forward").getAttribute("aria-label"), S.vsForward);
  assert.equal(t.btn("vs-more").getAttribute("aria-label"), S.vsMoreLabel);
});

test("the '...' menu: Rename opens the form; Show in parent / root run and are disabled at the root", async () => {
  const t = setup({ selected: { id: 9, ts: 1 } });
  const items = () => t.menus[0].config.getItems();
  assert.deepEqual(items().map((i) => i.id), ["rename", "show-parent", "show-root"]);
  assert.deepEqual(items().map((i) => i.disabled || false), [false, true, true]);
  assert.equal(items()[1].title, S.vsAtRoot);
  items()[0].run();
  const [form] = t.bar.byClass("vs-rename-form");
  assert.equal(form.hidden, false);
  t.setLive({ userFilter: { tid: [1] } });
  t.btn("vs-narrow").click();
  t.setLive({ userFilter: { tid: [2] } });
  t.ui.changed();
  t.btn("vs-narrow").click();
  assert.deepEqual(items().map((i) => i.disabled || false), [false, false, false]);
  items()[1].run();
  await tick();
  assert.equal(t.stack.index, 1);
  assert.deepEqual(t.calls.reveal.at(-1), { id: 9, ts: 1 });
  items()[2].run();
  await tick();
  assert.equal(t.stack.index, 0);
});

test("opening the '...' menu closes an open Filters popover; opening Filters closes the menu", async () => {
  const { createPopoverGroup } = await import("../../src/dmslog/web/js/popover-group.js");
  const group = createPopoverGroup();
  const t = setup({ group });
  const other = group.join("strip", () => { other.closed(); closedStrip += 1; });
  let closedStrip = 0;
  other.opened(); // the Filters form is open
  t.menus[0].open(); // the menu opens by keyboard
  assert.equal(closedStrip, 1);
  let menuClosed = 0;
  const realClose = t.menus[0].close;
  t.menus[0].close = (...a) => { menuClosed += 1; return realClose(...a); };
  other.opened(); // Filters opens: the menu must be closed through the group
  assert.equal(menuClosed, 1);
});
