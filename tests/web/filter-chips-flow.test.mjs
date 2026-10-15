// Filter strip DOM (UI-18) on a small fake DOM: chips, Filtered state, "+N more", popovers, removal. No wall-clock waits.
import test from "node:test";
import assert from "node:assert/strict";

class FakeElement {
  constructor(tag) {
    this.tagName = tag.toUpperCase();
    this.children = [];
    this.parent = null;
    this.attrs = {};
    this.listeners = {};
    this.hidden = false;
    this.className = "";
    this.title = "";
    this._text = "";
    this.clientWidth = 0;
    this.focused = false;
    this.classes = new Set();
    this.classList = {
      toggle: (c, on) => (on ? this.classes.add(c) : this.classes.delete(c)),
      contains: (c) => this.classes.has(c),
    };
  }
  set innerHTML(v) { throw new Error("innerHTML must not be used"); }
  set textContent(v) { this.children = []; this._text = String(v); }
  get textContent() { return this._text + this.children.map((c) => c.textContent).join(""); }
  get offsetWidth() { reads.count += 1; return this.className.includes("fs-more") ? 60 : this.className.includes("ui-chip") ? 100 : 0; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return this.attrs[k]; }
  appendChild(c) { c.parent = this; this.children.push(c); return c; }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  fire(type, event = {}) { for (const fn of this.listeners[type] || []) { fn({ target: this, ...event }); } }
  click() { this.fire("click"); }
  focus() { this.focused = true; globalThis.focusedNode = this; }
  all() { return [this, ...this.children.flatMap((c) => c.all())]; }
  byClass(c) { return this.all().filter((n) => n.className.split(" ").includes(c)); }
}
const reads = { count: 0 };
const docListeners = {};
const doc = {
  createElement: (tag) => new FakeElement(tag),
  addEventListener: (type, fn) => { (docListeners[type] ||= new Set()).add(fn); },
  removeEventListener: (type, fn) => { (docListeners[type] ||= new Set()).delete(fn); },
};
globalThis.document = doc;

const { mountFilterChips } = await import("../../src/dmslog/web/js/filter-chips.js");
const { emptyState } = await import("../../src/dmslog/web/js/views-stack-model.js");
const { S } = await import("../../src/dmslog/web/js/strings.js");

const lookups = { sourceName: (i) => "s" + i, collectionTag: (g) => "C" + g, typeLabel: (i) => "T" + i, formatTs: (m) => "t" + m };
const HOSTILE = "<img src=x onerror=alert(1)>";

function setup({ width = 1000 } = {}) {
  const strip = new FakeElement("div");
  const chipsHost = new FakeElement("div");
  chipsHost.clientWidth = width;
  const stateLabel = new FakeElement("span");
  const filtersButton = new FakeElement("button");
  const popover = new FakeElement("form");
  strip.appendChild(chipsHost);
  const state = { live: emptyState(), selection: { sourceIds: [], collectionIds: [] }, parentsFilter: null };
  const removed = [];
  let treeOpened = 0;
  const ui = mountFilterChips({
    doc, strip, chipsHost, stateLabel, filtersButton, popover, lookups,
    getState: () => state,
    onRemove: (id) => removed.push(id),
    onOpenTree: () => { treeOpened += 1; },
  });
  const chipNodes = () => chipsHost.children.filter((n) => n.className.includes("ui-chip") && !n.className.includes("fs-more"));
  const more = () => chipsHost.children.find((n) => n.className.includes("fs-more"));
  const overflow = () => strip.children.find((n) => n.className === "fs-overflow");
  const labels = () => chipNodes().map((n) => (n.byClass("ui-chip-label")[0] || n).textContent);
  return { ui, state, strip, chipsHost, stateLabel, filtersButton, popover, removed, chipNodes, more, overflow, labels, tree: () => treeOpened };
}

const withText = (t, text, extra = {}) => { t.state.live = { ...emptyState(), searchText: text, ...extra }; t.ui.refresh(); };

test("no filter: 'No filters' label, no chips, strip not marked as filtered", () => {
  const t = setup();
  t.ui.refresh();
  assert.equal(t.stateLabel.textContent, S.stripNoFilters);
  assert.equal(t.strip.classList.contains("is-filtered"), false);
  assert.equal(t.chipNodes().length, 0);
  assert.equal(t.more().hidden, true);
});

test("a condition shows 'Filtered' and its chip; hostile text stays text", () => {
  const t = setup();
  withText(t, HOSTILE);
  assert.equal(t.stateLabel.textContent, S.stripFiltered);
  assert.equal(t.strip.classList.contains("is-filtered"), true);
  assert.equal(t.labels()[0], 'text "' + HOSTILE.slice(0, 25) + "…");
  assert.equal(t.chipNodes()[0].byClass("ui-chip-label")[0].attrs.title, 'text "' + HOSTILE + '"');
});

test("the ancestors' filter alone shows 'Filtered' with no chip (View 2 with an empty toolbar)", () => {
  const t = setup();
  t.state.parentsFilter = { and: [{ tid: [1] }] };
  t.ui.refresh();
  assert.equal(t.stateLabel.textContent, S.stripFiltered);
  assert.equal(t.chipNodes().length, 0);
});

test("the x of a chip reports its id; the tree chip has no x and opens the filter tree", () => {
  const t = setup();
  withText(t, "abc", { toggles: { hideNoise: true, unclassified: false }, userFilter: { or: [{ tid: [1] }, { tid: [2] }] } });
  const [textChip, noiseChip, treeChip] = t.chipNodes();
  textChip.byClass("ui-chip-remove")[0].click();
  noiseChip.byClass("ui-chip-remove")[0].click();
  assert.deepEqual(t.removed, ["text", "noise"]);
  assert.equal(textChip.byClass("ui-chip-remove")[0].attrs["aria-label"], 'Remove text "abc"');
  assert.equal(treeChip.byClass("ui-chip-remove").length, 0);
  treeChip.click();
  assert.equal(t.tree(), 1);
});

test("chips that do not fit become '+N more'; the hidden ones are listed in the overflow popover", () => {
  const t = setup({ width: 250 }); // 4 chips of 104 do not fit; room for 1 chip + the "+N more" button (104 + 64)
  t.state.live = { ...emptyState(), searchText: "a", toggles: { hideNoise: true, unclassified: true } };
  t.state.selection = { sourceIds: [1, 2], collectionIds: [] };
  t.ui.refresh(); // sources, text, noise, unclassified: 4 chips
  assert.equal(t.chipNodes().filter((n) => !n.hidden).length, 1);
  assert.equal(t.more().hidden, false);
  assert.equal(t.more().textContent, "+3 more");
  t.more().click();
  assert.equal(t.overflow().hidden, false);
  assert.equal(t.more().attrs["aria-expanded"], "true");
  const rows = t.overflow().children;
  assert.equal(rows.length, 3);
  rows[0].byClass("ui-chip-remove")[0].click();
  assert.deepEqual(t.removed, ["text"]);
});

test("everything fits: no '+N more'", () => {
  const t = setup({ width: 1000 });
  withText(t, "a", { toggles: { hideNoise: true, unclassified: true } });
  assert.equal(t.more().hidden, true);
  assert.equal(t.chipNodes().every((n) => !n.hidden), true);
});

test("no layout (width 0 or unmeasurable) never hides a chip", () => {
  const t = setup({ width: 0 });
  withText(t, "a", { toggles: { hideNoise: true, unclassified: true } });
  assert.equal(t.chipNodes().every((n) => !n.hidden), true);
  assert.equal(t.more().hidden, true);
});

test("a width change fits again from the measured widths; same chips are neither redrawn nor measured", () => {
  const t = setup({ width: 1000 });
  withText(t, "a", { toggles: { hideNoise: true, unclassified: true } });
  const first = t.chipNodes();
  const before = reads.count;
  t.ui.refresh(); // same chips
  assert.equal(reads.count, before);
  assert.deepEqual(t.chipNodes(), first);
  t.chipsHost.clientWidth = 250;
  t.ui.relayout();
  assert.equal(reads.count, before, "relayout must not touch layout reads");
  assert.equal(t.chipNodes().filter((n) => !n.hidden).length, 1);
  assert.equal(t.more().textContent, "+2 more");
  t.chipsHost.clientWidth = 1000;
  t.ui.relayout();
  assert.equal(t.more().hidden, true);
  t.ui.relayout(); // unchanged width: nothing to do
  assert.equal(reads.count, before);
});

test("removing chips until none is left clears the state", () => {
  const t = setup();
  withText(t, "a");
  assert.equal(t.chipNodes().length, 1);
  t.state.live = emptyState();
  t.ui.refresh();
  assert.equal(t.chipNodes().length, 0);
  assert.equal(t.stateLabel.textContent, S.stripNoFilters);
});

test("Filters button opens the form popover; Escape closes and returns focus; outside click closes; Apply closes", () => {
  const t = setup();
  assert.equal(t.popover.hidden, true);
  t.filtersButton.click();
  assert.equal(t.popover.hidden, false);
  assert.equal(t.filtersButton.attrs["aria-expanded"], "true");
  t.popover.fire("keydown", { key: "Escape", stopPropagation() {} });
  assert.equal(t.popover.hidden, true);
  assert.equal(t.filtersButton.attrs["aria-expanded"], "false");
  assert.equal(globalThis.focusedNode, t.filtersButton);
  t.filtersButton.click();
  const outside = new FakeElement("div");
  for (const fn of [...docListeners.pointerdown]) { fn({ target: outside }); }
  assert.equal(t.popover.hidden, true);
  assert.equal(docListeners.pointerdown.size, 0, "the document listener is removed on close");
  t.filtersButton.click();
  for (const fn of [...docListeners.pointerdown]) { fn({ target: t.popover }); } // inside: stays open
  assert.equal(t.popover.hidden, false);
  t.popover.fire("submit");
  assert.equal(t.popover.hidden, true);
  t.filtersButton.click();
  t.filtersButton.click(); // second click toggles it closed
  assert.equal(t.popover.hidden, true);
});

test("only one popover is open at a time", () => {
  const t = setup({ width: 150 });
  withText(t, "a", { toggles: { hideNoise: true, unclassified: true } });
  t.filtersButton.click();
  t.more().click();
  assert.equal(t.popover.hidden, true);
  assert.equal(t.overflow().hidden, false);
  t.filtersButton.click();
  assert.equal(t.overflow().hidden, true);
  assert.equal(t.popover.hidden, false);
});
