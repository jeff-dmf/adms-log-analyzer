// Highlights UI on a small fake DOM: panel list and quick form, "Highlight this key" in the detail, the
// footer help and the ]/[ navigation. Rule names and log values are written only as text.
import test from "node:test";
import assert from "node:assert/strict";

class FakeElement {
  constructor(tag) {
    this.tagName = tag.toUpperCase();
    this.children = [];
    this.parent = null;
    this.attrs = {};
    this.dataset = {};
    this.listeners = {};
    this.hidden = false;
    this.disabled = false;
    this.checked = false;
    this.className = "";
    this.id = "";
    this.value = "";
    this._text = "";
    const classes = new Set();
    this.classList = { toggle: (c, on) => (on ? classes.add(c) : classes.delete(c)), contains: (c) => classes.has(c) };
  }
  set innerHTML(v) { throw new Error("innerHTML must not be used"); }
  set textContent(v) { this.children = []; this._text = String(v); }
  get textContent() { return this._text + this.children.map((c) => c.textContent).join(""); }
  get parentNode() { return this.parent; }
  get nextSibling() {
    if (!this.parent) { return null; }
    const siblings = this.parent.children;
    return siblings[siblings.indexOf(this) + 1] || null;
  }
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
  fire(type, event = {}) { for (const fn of this.listeners[type] || []) { fn({ preventDefault() {}, target: this, ...event }); } }
  click() { this.fire("click"); }
  all() { return [this, ...this.children.flatMap((c) => c.all())]; }
  find(pred) { return this.all().filter(pred); }
  byClass(c) { return this.find((n) => n.className.split(" ").includes(c)); }
  byId(id) { return this.find((n) => n.id === id)[0]; }
}

const docListeners = {};
globalThis.document = {
  head: new FakeElement("head"),
  createElement: (tag) => new FakeElement(tag),
  querySelector: () => null,
  addEventListener: (type, fn) => { (docListeners[type] ||= []).push(fn); },
};
const keydown = async (key, target = new FakeElement("div")) => {
  let prevented = false;
  for (const fn of docListeners.keydown || []) {
    await fn({ key, target, preventDefault() { prevented = true; } });
  }
  return prevented;
};

const { mountHighlights } = await import("../../src/dmslog/web/js/highlight-panel.js");
const { createHighlightStore } = await import("../../src/dmslog/web/js/highlight-model.js");
const { S } = await import("../../src/dmslog/web/js/strings.js");

const tick = async () => { for (let i = 0; i < 10; i += 1) { await Promise.resolve(); } };
const HOSTILE = "<img src=x onerror=alert(1)>";

function setup() {
  const calls = { create: [], key: [], next: [], update: [], order: [], remove: [], reveal: [], messages: [] };
  let rules = [
    { id: 1, pos: 0, name: HOSTILE, color: "red", enabled: true, cond: { text: { substr: HOSTILE } } },
    { id: 2, pos: 1, name: "gid", color: "blue", enabled: false, cond: { key: { type: "gid", eq: "7" } } },
  ];
  const api = {
    highlights: {
      list: async () => ({ rules: rules.slice() }),
      mark: async (caseId, conds, ids) => ({ marks: Object.fromEntries(ids.filter((i) => i === 5).map((i) => [String(i), 0])) }),
      create: async (caseId, rule) => { calls.create.push(rule); rules.push({ ...rule, id: 9, pos: 2 }); return {}; },
      update: async (caseId, id, changes) => { calls.update.push([id, changes]); return {}; },
      order: async (caseId, ids) => { calls.order.push(ids); return {}; },
      remove: async (caseId, id) => { calls.remove.push(id); rules = rules.filter((r) => r.id !== id); return {}; },
      key: async (...args) => { calls.key.push(args.slice(1)); return {}; },
      next: async (...args) => { calls.next.push(args.slice(1)); return { row: { id: 77, ts: 5 }, rule: 0 }; },
    },
  };
  let ui = null;
  const store = createHighlightStore({ api, getCase: () => "C1", onChange: () => ui && ui.storeChanged() });
  const toolbar = new FakeElement("header");
  const theme = toolbar.appendChild(new FakeElement("button"));
  const panelParent = new FakeElement("section");
  const filterForm = panelParent.appendChild(new FakeElement("form"));
  const footerParent = new FakeElement("section");
  const footer = footerParent.appendChild(new FakeElement("div"));
  const detailParent = new FakeElement("aside");
  const anchor = detailParent.appendChild(new FakeElement("section"));
  let selected = { id: 3, ts: 2 };
  ui = mountHighlights({
    toolbar, beforeButton: theme, panelParent, beforePanel: filterForm, footer, detailAnchor: anchor, store,
    getCase: () => "C1", getFilter: () => ({ sev: { le: 3 } }), getSelectedRow: () => selected,
    getTemplateCode: () => "TST-001", reveal: async (row) => calls.reveal.push(row.id),
    showMessage: (text, isError) => calls.messages.push([text, Boolean(isError)]),
  });
  return { calls, store, ui, toolbar, panelParent, footerParent, detailParent, setSelected: (row) => { selected = row; } };
}

test("panel lists the rules as text, toggles, recolors, moves and deletes them", async () => {
  const { calls, toolbar, panelParent } = setup();
  const button = toolbar.children[0];
  assert.equal(button.textContent, S.hlButton);
  assert.equal(button.getAttribute("aria-expanded"), "false");
  const panel = panelParent.byId("hl-panel");
  assert.equal(panelParent.children[0], panel); // before the filter bar
  button.click();
  await tick();
  assert.equal(panel.hidden, false);
  assert.equal(button.getAttribute("aria-expanded"), "true");
  const items = panel.byClass("hl-rule");
  assert.equal(items.length, 2);
  assert.equal(items[0].byClass("hl-name")[0].textContent, HOSTILE); // literal text
  assert.equal(items[0].byClass("hl-desc")[0].textContent, 'text: "' + HOSTILE + '"');
  assert.ok(items[1].className.includes("is-off"));
  assert.equal(items[0].byClass("hl-up")[0].disabled, true);
  assert.equal(items[1].byClass("hl-down")[0].disabled, true);
  const box = items[1].byClass("hl-enabled")[0];
  box.checked = true;
  box.fire("change");
  const select = items[0].byClass("hl-color")[0];
  select.value = "green";
  select.fire("change");
  items[1].byClass("hl-up")[0].click();
  items[1].byClass("hl-delete")[0].click();
  await tick();
  assert.deepEqual(calls.update, [[2, { enabled: true }], [1, { color: "green" }]]);
  assert.deepEqual(calls.order, [[2, 1]]);
  assert.deepEqual(calls.remove, [2]);
  assert.match(panel.find((n) => n.tagName === "SPAN" && n.textContent.startsWith("1 of 20"))[0].textContent, /^1 of 20 rules/);
});

test("quick form validates, builds the leaf and adds the current filter", async () => {
  const { calls, toolbar, panelParent } = setup();
  toolbar.children[0].click();
  await tick();
  const panel = panelParent.byId("hl-panel");
  const form = panel.byClass("hl-form")[0];
  const kind = panel.byId("hl-kind");
  const status = panel.byClass("hl-status")[0];
  form.fire("submit");
  assert.equal(status.textContent, S.hlBad.empty);
  kind.value = "key";
  kind.fire("change");
  assert.equal(panel.byId("hl-extra").parent.hidden, false);
  panel.byId("hl-extra").value = "gid";
  panel.byId("hl-value").value = "123";
  panel.byId("hl-new-color").value = "cyan";
  form.fire("submit");
  await tick();
  assert.deepEqual(calls.create[0], { name: "gid = 123", color: "cyan", cond: { key: { type: "gid", eq: "123" } }, enabled: true });
  kind.value = "sev";
  kind.fire("change");
  assert.equal(panel.byId("hl-value").parent.hidden, true);
  assert.equal(panel.byId("hl-sev").parent.hidden, false);
  panel.byClass("hl-add-filter")[0].click();
  await tick();
  assert.deepEqual(calls.create[1].cond, { sev: { le: 3 } });
  assert.equal(calls.create[1].name, "severity " + S.severityNames[3] + " or worse");
});

test("detail offers Highlight this key per key and tells which rule colors the record", async () => {
  const { calls, store, ui, detailParent } = setup();
  await store.load();
  const section = detailParent.children[1];
  assert.equal(section.hidden, true);
  ui.showEntry({ id: 5, tpl_id: 1, keys: [{ key_type: "msgid", value: HOSTILE }, { key_type: "msgid", value: HOSTILE }, { key_type: "gid", value: "7" }] });
  await new Promise((resolve) => setTimeout(resolve, 60)); // the store looks record 5 up (batch timer)
  await tick();
  assert.equal(section.hidden, false);
  const by = section.byClass("hl-by")[0];
  assert.equal(by.textContent, S.hlColorNames.red + " " + "Highlighted by " + HOSTILE);
  const chips = section.byClass("hl-chip");
  assert.deepEqual(chips.map((c) => c.textContent), [
    "Highlight msgid = " + HOSTILE, "Highlight gid = 7", S.hlTemplateChip,
  ]);
  chips[0].click();
  await tick();
  assert.deepEqual(calls.key[0], ["msgid", HOSTILE, "yellow"]);
  assert.match(calls.messages.at(-1)[0], /normal and sensitive/);
  chips[2].click();
  await tick();
  assert.deepEqual(calls.create.at(-1), { name: "TST-001", color: "yellow", cond: { tpl: [1] }, enabled: true });
  ui.showEntry(null);
  assert.equal(section.hidden, true);
});

test("footer documents ] and [, and the keys walk the highlighted records", async () => {
  const { calls, store, footerParent, setSelected } = setup();
  assert.equal(footerParent.children[1].textContent, S.hlShortcutsHelp);
  assert.equal(await keydown("]"), true);
  await tick();
  assert.deepEqual(calls.messages.at(-1), [S.hlNavNoRules, false]); // rules not loaded yet
  await store.load();
  await keydown("]");
  await tick();
  assert.deepEqual(calls.next[0], [{ sev: { le: 3 } }, [{ text: { substr: HOSTILE } }], "2:3", "fwd"].concat(calls.next[0].slice(4)));
  assert.deepEqual(calls.reveal, [77]);
  await keydown("[");
  await tick();
  assert.equal(calls.next[1][3], "back");
  const typing = new FakeElement("input");
  assert.equal(await keydown("]", typing), false); // typing in a field is not a shortcut
  assert.equal(calls.next.length, 2);
  assert.equal(await keydown("n"), false); // n/N stay the error navigation
  setSelected(null);
  await keydown("]");
  assert.deepEqual(calls.messages.at(-1), [S.navNoSelection, false]);
});
