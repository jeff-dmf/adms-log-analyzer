// Highlights UI on a small fake DOM: the dock panel (list and quick form), "Highlight this key" in the detail, the
// the ]/[ navigation. Rule names and log values are written only as text.
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
const { mountHighlightPanel, mountHighlights } = await import("../../src/dmslog/web/js/highlight-panel.js");
const { createHighlightStore } = await import("../../src/dmslog/web/js/highlight-model.js");
const { S } = await import("../../src/dmslog/web/js/strings.js");

const tick = async () => { for (let i = 0; i < 10; i += 1) { await Promise.resolve(); } };
let nextHold = null; // a list while a test answers the ]/[ lookups by hand
const HOSTILE = "<img src=x onerror=alert(1)>";

function setup(extraRules = 0) {
  const calls = { create: [], key: [], next: [], update: [], order: [], remove: [], reveal: [], messages: [] };
  let rules = [
    { id: 1, pos: 0, name: HOSTILE, color: "red", enabled: true, cond: { text: { substr: HOSTILE } } },
    { id: 2, pos: 1, name: "gid", color: "blue", enabled: false, cond: { key: { type: "gid", eq: "7" } } },
  ];
  for (let k = 0; k < extraRules; k += 1) {
    rules.push({ id: 100 + k, pos: 2 + k, name: "r" + k, color: "red", enabled: true, cond: { sev: 1 } });
  }
  const api = {
    highlights: {
      list: async () => ({ rules: rules.slice() }),
      mark: async (caseId, conds, ids) => ({ marks: Object.fromEntries(ids.filter((i) => i === 5).map((i) => [String(i), 0])) }),
      create: async (caseId, rule) => { calls.create.push(rule); rules.push({ ...rule, id: 9, pos: 2 }); return {}; },
      update: async (caseId, id, changes) => { calls.update.push([id, changes]); return {}; },
      order: async (caseId, ids) => { calls.order.push(ids); return {}; },
      remove: async (caseId, id) => { calls.remove.push(id); rules = rules.filter((r) => r.id !== id); return {}; },
      key: async (...args) => { calls.key.push(args.slice(1)); return {}; },
      next: async (...args) => {
        calls.next.push(args.slice(1));
        if (nextHold) {
          const id = await new Promise((resolve) => nextHold.push(resolve)); // answered by the test, in any order
          return { row: { id, ts: 5 }, rule: 0 };
        }
        return { row: { id: 77, ts: 5 }, rule: 0 };
      },
    },
  };
  let ui = null;
  let panelUi = null;
  const store = createHighlightStore({
    api, getCase: () => "C1", onChange: () => { ui && ui.storeChanged(); panelUi && panelUi.storeChanged(); },
  });
  const host = new FakeElement("section"); // the dock gives the panel its host
  const detailParent = new FakeElement("aside");
  const anchor = detailParent.appendChild(new FakeElement("section"));
  let selected = { id: 3, ts: 2 };
  panelUi = mountHighlightPanel(host, { store, getCase: () => "C1", getFilter: () => ({ sev: { le: 3 } }) });
  ui = mountHighlights({
    detailAnchor: anchor, store,
    getCase: () => "C1", getFilter: () => ({ sev: { le: 3 } }), getSelectedRow: () => selected,
    getTemplateCode: () => "TST-001", reveal: async (row) => calls.reveal.push(row.id),
    showMessage: (text, isError) => calls.messages.push([text, Boolean(isError)]),
  });
  return { calls, store, ui, host, panelUi, detailParent, setSelected: (row) => { selected = row; } };
}

test("panel lists the rules as text, toggles, recolors, moves and deletes them", async () => {
  const { calls, host: panel, panelUi } = setup();
  panelUi.refresh(); // the dock opened the panel
  await tick();
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
  const { calls, host: panel, panelUi } = setup();
  panelUi.refresh();
  await tick();
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
  assert.equal(calls.create[1].name, S.hlDescSev.replace("{value}", S.severityNames[3]));
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

test("Highlight this key at the rule limit still asks the server, and shows the limit only when it answers so", async () => {
  const { calls, store, ui, detailParent } = setup(18); // 20 rules: the limit
  await store.load();
  assert.equal(store.rules().length, 20);
  const section = detailParent.children[1];
  ui.showEntry({ id: 5, tpl_id: 0, keys: [{ key_type: "gid", value: "7" }] });
  await new Promise((resolve) => setTimeout(resolve, 60));
  await tick();
  section.byClass("hl-chip")[0].click(); // the key already has a rule: the server recolors it
  await tick();
  assert.deepEqual(calls.key[0], ["gid", "7", "yellow"]);
  assert.equal(calls.messages.some(([text]) => text === S.hlLimitOther.replace("{max}", "20")), false);
});

test("navigate('fwd'|'back') walks the highlighted records; the keys themselves belong to the action registry", async () => {
  const { calls, store, ui, setSelected } = setup();
  assert.deepEqual(docListeners.keydown || [], [], "no key listener of its own (UI-28: shortcuts.js dispatches ] and [)");
  await ui.navigate("fwd");
  assert.deepEqual(calls.messages.at(-1), [S.hlNavNoRules, false]); // rules not loaded yet
  await store.load();
  await ui.navigate("fwd");
  assert.deepEqual(calls.next[0], [{ sev: { le: 3 } }, [{ text: { substr: HOSTILE } }], "2:3", "fwd"].concat(calls.next[0].slice(4)));
  assert.deepEqual(calls.reveal, [77]);
  await ui.navigate("back");
  assert.equal(calls.next[1][3], "back");
  setSelected(null);
  await ui.navigate("fwd");
  assert.deepEqual(calls.messages.at(-1), [S.navNoSelection, false]);
  assert.equal(calls.next.length, 2);
});

test("navigate: only the newest lookup reveals a record (a slow older answer is dropped)", async () => {
  const { calls, store, ui } = setup();
  await store.load();
  nextHold = [];
  try {
    const older = ui.navigate("fwd");
    const newer = ui.navigate("back");
    await tick();
    assert.equal(nextHold.length, 2);
    nextHold[1](72); // the newer answers first
    await newer;
    nextHold[0](71); // then the older one, too late
    await older;
    assert.deepEqual(calls.reveal, [72]);
  } finally {
    nextHold = null;
  }
});

test("reset forgets the old case's rules and status without asking the server", async () => {
  const { calls, host, panelUi } = setup();
  panelUi.refresh();
  await tick();
  assert.equal(host.byClass("hl-rule").length, 2);
  const before = JSON.stringify(calls.create);
  panelUi.reset();
  assert.equal(host.byClass("hl-rule").length, 0);
  assert.equal(host.byClass("hl-status")[0].textContent, "");
  assert.equal(JSON.stringify(calls.create), before);
});
