// UI-10: keyboard behavior of the .ui-menu (js/ui-menu.js) with a fake DOM, the chip builder, and
// structural checks of css/components.css (tokens only, no opacity on disabled, 24 px targets).
// The CSS checks strip comments and normalize CRLF first, so autocrlf on Windows does not matter.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import { createChip, createMenu, indexByInitial, navigate, normalizeItem, stepIndex } from "../../src/dmslog/web/js/ui-menu.js";

// ---- Fake DOM -------------------------------------------------------------------------------------

class El {
  constructor(doc, tag) {
    this.doc = doc;
    this.tagName = tag;
    this.attrs = {};
    this.children = [];
    this.parent = null;
    this.listeners = {};
    this.style = {};
    this.className = "";
    this.textContent = "";
  }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  appendChild(c) { c.parent = this; this.children.push(c); return c; }
  remove() {
    if (this.parent) this.parent.children = this.parent.children.filter((c) => c !== this);
    this.parent = null;
    if (this.doc.activeElement === this) this.doc.activeElement = this.doc.body;
  }
  addEventListener(type, fn) { (this.listeners[type] ??= []).push(fn); }
  focus() { this.doc.activeElement = this; }
  contains(t) { return t === this || this.children.some((c) => c.contains(t)); }
  dispatch(type, init = {}) {
    const event = { type, target: this, key: "", detail: 0, ctrlKey: false, altKey: false, metaKey: false, prevented: false, ...init,
      preventDefault() { this.prevented = true; }, stopPropagation() {} };
    for (let n = this; n; n = n.parent) for (const fn of n.listeners[type] ?? []) fn(event);
    return event;
  }
}

function makeDoc() {
  const doc = { listeners: {}, defaultView: null };
  doc.createElement = (tag) => new El(doc, tag);
  doc.body = new El(doc, "body");
  doc.activeElement = doc.body;
  doc.addEventListener = (t, fn) => (doc.listeners[t] ??= []).push(fn);
  doc.removeEventListener = (t, fn) => (doc.listeners[t] = (doc.listeners[t] ?? []).filter((f) => f !== fn));
  return doc;
}

const press = (doc, key, init = {}) => doc.activeElement.dispatch("keydown", { key, ...init });
const menuEls = (doc) => doc.body.children.filter((c) => c.className === "ui-menu");
const items = (menu) => menu.children.filter((c) => c.attrs.role !== "separator");
const focusedLabel = (doc) => doc.activeElement.children[0]?.textContent;

function setup(list) {
  const doc = makeDoc();
  const trigger = doc.createElement("button");
  trigger.textContent = "Analyze";
  doc.body.appendChild(trigger);
  const ran = [];
  const wrap = (xs) => xs.map((x) => (x.separator ? x : { ...x, run: () => ran.push(x.id), children: x.children && wrap(x.children) }));
  const menu = createMenu({ trigger, getItems: () => wrap(list), doc });
  return { doc, trigger, menu, ran };
}

const LIST = [
  { id: "distinct", label: "Distinct values", shortcut: "D" },
  { id: "aggregate", label: "Aggregate" },
  { id: "ops", label: "Operations", disabled: true, title: "Open a case first" },
  { separator: true },
  { id: "dashboard", label: "Dashboard" },
  { id: "follow", label: "Follow tail", pressed: true },
  { id: "theme", label: "Theme", children: [
    { id: "sys", label: "System", role: "menuitemradio", checked: true },
    { id: "light", label: "Light", role: "menuitemradio", checked: false },
  ] },
];

// ---- Pure model -----------------------------------------------------------------------------------

test("normalizeItem fills the MenuItem contract and maps pressed to a checkbox", () => {
  const plain = normalizeItem({ id: "a", label: "A" });
  assert.deepEqual([plain.role, plain.checked, plain.disabled, plain.shortcut, plain.children], ["menuitem", null, false, null, null]);
  const toggled = normalizeItem({ id: "b", label: "B", pressed: false });
  assert.deepEqual([toggled.role, toggled.checked], ["menuitemcheckbox", false]);
  const radio = normalizeItem({ id: "c", label: "C", role: "menuitemradio", checked: true });
  assert.equal(radio.checked, true);
  assert.equal(normalizeItem({ id: "d", label: "D", children: [] }).children, null);
});

test("stepIndex skips disabled items and separators and wraps", () => {
  const xs = LIST.map(normalizeItem);
  assert.equal(stepIndex(xs, 1, 1), 4); // Operations (disabled) and the separator are skipped
  assert.equal(stepIndex(xs, 4, -1), 1);
  assert.equal(stepIndex(xs, 6, 1), 0);
  assert.equal(stepIndex(xs, 0, -1), 6);
  assert.equal(stepIndex([normalizeItem({ id: "x", label: "X", disabled: true })], -1, 1), -1);
});

test("navigate handles arrows, Home and End; indexByInitial cycles through the same letter", () => {
  const xs = LIST.map(normalizeItem);
  assert.equal(navigate(xs, 0, "End"), 6);
  assert.equal(navigate(xs, 3, "Home"), 0);
  assert.equal(navigate(xs, -1, "ArrowUp"), 6);
  assert.equal(navigate(xs, 0, "x"), null);
  const ys = ["Alpha", "Beta", "Apple", "Avocado"].map((label) => normalizeItem({ id: label, label }));
  assert.equal(indexByInitial(ys, 0, "a"), 2);
  assert.equal(indexByInitial(ys, 2, "A"), 3);
  assert.equal(indexByInitial(ys, 3, "a"), 0);
  assert.equal(indexByInitial(ys, 0, "z"), -1);
});

// ---- DOM behavior ---------------------------------------------------------------------------------

test("the trigger gets haspopup/expanded, and ArrowDown opens the menu on the first item", () => {
  const { doc, trigger } = setup(LIST);
  assert.equal(trigger.attrs["aria-haspopup"], "menu");
  assert.equal(trigger.attrs["aria-expanded"], "false");
  trigger.focus();
  const e = trigger.dispatch("keydown", { key: "ArrowDown" });
  assert.ok(e.prevented);
  assert.equal(trigger.attrs["aria-expanded"], "true");
  assert.equal(menuEls(doc).length, 1);
  assert.equal(focusedLabel(doc), "Distinct values");
});

test("roving tabindex: only the active item is tabbable", () => {
  const { doc, trigger } = setup(LIST);
  trigger.dispatch("keydown", { key: "ArrowDown" });
  press(doc, "ArrowDown");
  const tabs = items(menuEls(doc)[0]).map((n) => n.attrs.tabindex);
  assert.deepEqual(tabs, ["-1", "0", "-1", "-1", "-1", "-1"]);
});

test("arrows move and skip the disabled item; Home and End jump; arrows wrap", () => {
  const { doc, trigger } = setup(LIST);
  trigger.dispatch("keydown", { key: "ArrowDown" });
  press(doc, "ArrowDown");
  assert.equal(focusedLabel(doc), "Aggregate");
  press(doc, "ArrowDown");
  assert.equal(focusedLabel(doc), "Dashboard"); // Operations is disabled
  press(doc, "End");
  assert.equal(focusedLabel(doc), "Theme");
  press(doc, "ArrowDown");
  assert.equal(focusedLabel(doc), "Distinct values");
  press(doc, "ArrowUp");
  assert.equal(focusedLabel(doc), "Theme");
  press(doc, "Home");
  assert.equal(focusedLabel(doc), "Distinct values");
});

test("ArrowUp on the trigger opens on the last item", () => {
  const { doc, trigger } = setup(LIST);
  trigger.dispatch("keydown", { key: "ArrowUp" });
  assert.equal(focusedLabel(doc), "Theme");
});

test("first letter jumps to the next item that starts with it, skipping disabled ones", () => {
  const { doc, trigger } = setup(LIST);
  trigger.dispatch("keydown", { key: "ArrowDown" });
  press(doc, "d");
  assert.equal(focusedLabel(doc), "Dashboard");
  press(doc, "d");
  assert.equal(focusedLabel(doc), "Distinct values");
  press(doc, "o"); // Operations is disabled: no jump
  assert.equal(focusedLabel(doc), "Distinct values");
  press(doc, "t", { ctrlKey: true }); // chords are not typeahead
  assert.equal(focusedLabel(doc), "Distinct values");
  press(doc, "T");
  assert.equal(focusedLabel(doc), "Theme");
});

test("Enter and Space run the item, close the menu and return the focus to the trigger", () => {
  for (const key of ["Enter", " "]) {
    const { doc, trigger, ran } = setup(LIST);
    trigger.dispatch("keydown", { key: "ArrowDown" });
    press(doc, "ArrowDown");
    press(doc, key);
    assert.deepEqual(ran, ["aggregate"], key);
    assert.equal(menuEls(doc).length, 0);
    assert.equal(trigger.attrs["aria-expanded"], "false");
    assert.equal(doc.activeElement, trigger);
  }
});

test("a disabled item never runs: aria-disabled and the reason as title", () => {
  const { doc, trigger, ran } = setup(LIST);
  trigger.dispatch("keydown", { key: "ArrowDown" });
  const ops = items(menuEls(doc)[0])[2];
  assert.equal(ops.attrs["aria-disabled"], "true");
  assert.equal(ops.attrs.title, "Open a case first");
  ops.dispatch("click");
  assert.deepEqual(ran, []);
  assert.equal(menuEls(doc).length, 1);
});

test("Esc closes the menu and returns the focus to the trigger", () => {
  const { doc, trigger, ran } = setup(LIST);
  trigger.focus();
  trigger.dispatch("keydown", { key: "ArrowDown" });
  press(doc, "ArrowDown");
  const e = press(doc, "Escape");
  assert.ok(e.prevented);
  assert.equal(menuEls(doc).length, 0);
  assert.equal(trigger.attrs["aria-expanded"], "false");
  assert.equal(doc.activeElement, trigger);
  assert.deepEqual(ran, []);
});

test("Tab closes the menu without stealing the focus", () => {
  const { doc, trigger } = setup(LIST);
  trigger.dispatch("keydown", { key: "ArrowDown" });
  const e = press(doc, "Tab");
  assert.equal(menuEls(doc).length, 0);
  assert.equal(e.prevented, false);
  assert.notEqual(doc.activeElement, trigger);
});

test("checkbox and radio items expose aria-checked; separators have role separator", () => {
  const { doc, trigger } = setup(LIST);
  trigger.dispatch("keydown", { key: "ArrowDown" });
  const all = menuEls(doc)[0].children;
  assert.equal(all[3].attrs.role, "separator");
  const follow = all.find((n) => n.attrs["data-id"] === "follow");
  assert.equal(follow.attrs.role, "menuitemcheckbox");
  assert.equal(follow.attrs["aria-checked"], "true");
  assert.equal(all.find((n) => n.attrs["data-id"] === "distinct").attrs["aria-checked"], undefined);
});

test("submenu: ArrowRight opens it on the first item, Esc and ArrowLeft return to the parent item", () => {
  const { doc, trigger, ran } = setup(LIST);
  trigger.dispatch("keydown", { key: "ArrowUp" }); // Theme
  press(doc, "ArrowRight");
  assert.equal(menuEls(doc).length, 2);
  assert.equal(focusedLabel(doc), "System");
  assert.equal(items(menuEls(doc)[0]).at(-1).attrs["aria-expanded"], "true");
  press(doc, "ArrowDown");
  assert.equal(focusedLabel(doc), "Light");
  press(doc, "Escape");
  assert.equal(menuEls(doc).length, 1);
  assert.equal(focusedLabel(doc), "Theme");
  assert.equal(items(menuEls(doc)[0]).at(-1).attrs["aria-expanded"], "false");
  press(doc, "Enter"); // Enter on a parent opens the submenu too
  assert.equal(menuEls(doc).length, 2);
  press(doc, "ArrowLeft");
  assert.equal(menuEls(doc).length, 1);
  press(doc, "ArrowRight");
  press(doc, "Enter"); // runs "System": closes everything and focuses the trigger
  assert.deepEqual(ran, ["sys"]);
  assert.equal(menuEls(doc).length, 0);
  assert.equal(doc.activeElement, trigger);
});

test("labels, shortcuts and titles go through textContent only", () => {
  const { doc, trigger } = setup([{ id: "x", label: "<b>x</b>", shortcut: "<i>K</i>" }]);
  trigger.dispatch("keydown", { key: "ArrowDown" });
  const node = items(menuEls(doc)[0])[0];
  assert.equal(node.children[0].textContent, "<b>x</b>");
  assert.equal(node.children[1].textContent, "<i>K</i>");
});

test("a pointerdown outside closes the menu; inside it does not", () => {
  const { doc, trigger } = setup(LIST);
  trigger.dispatch("keydown", { key: "ArrowDown" });
  const outside = doc.createElement("div");
  doc.body.appendChild(outside);
  const fire = (target) => doc.listeners.pointerdown.forEach((fn) => fn({ target }));
  fire(items(menuEls(doc)[0])[0]);
  assert.equal(menuEls(doc).length, 1);
  fire(outside);
  assert.equal(menuEls(doc).length, 0);
  assert.equal(doc.listeners.pointerdown.length, 0);
});

test("preload runs on focus", () => {
  const doc = makeDoc();
  const trigger = doc.createElement("button");
  doc.body.appendChild(trigger);
  const seen = [];
  createMenu({ trigger, doc, getItems: () => [{ id: "a", label: "A", preload: () => seen.push("a") }, { id: "b", label: "B", preload: () => seen.push("b") }] });
  trigger.dispatch("keydown", { key: "ArrowDown" });
  press(doc, "ArrowDown");
  assert.deepEqual(seen, ["a", "b"]);
});

test("createChip sets the remove button name from the parameter, 24 px target comes from CSS", () => {
  const doc = makeDoc();
  let removed = 0;
  const chip = createChip({ doc, label: "Severity: Error", removeLabel: "Remove Severity: Error", onRemove: () => removed++ });
  assert.equal(chip.className, "ui-chip");
  const button = chip.children[1];
  assert.equal(button.tagName, "button");
  assert.equal(button.attrs["aria-label"], "Remove Severity: Error");
  assert.equal(button.attrs.type, "button");
  button.dispatch("click");
  assert.equal(removed, 1);
  assert.equal(createChip({ doc, label: "x" }).children.length, 1);
});

// ---- components.css -------------------------------------------------------------------------------

const css = readFileSync(new URL("../../src/dmslog/web/css/components.css", import.meta.url), "utf8")
  .replace(/\r\n?/g, "\n")
  .replace(/\/\*[\s\S]*?\*\//g, "");

// [{selector, body}] for top-level and @media-nested rules.
function rules(text, out = []) {
  let i = 0;
  while (i < text.length) {
    const open = text.indexOf("{", i);
    if (open < 0) break;
    let depth = 0, close = open;
    for (; close < text.length; close++) {
      if (text[close] === "{") depth++;
      else if (text[close] === "}" && --depth === 0) break;
    }
    const head = text.slice(i, open).trim();
    const body = text.slice(open + 1, close);
    if (head.startsWith("@media") || head.startsWith("@keyframes")) rules(body, out);
    else out.push({ selector: head, body });
    i = close + 1;
  }
  return out;
}
const RULES = rules(css);
const rulesFor = (sel) => RULES.filter((r) => r.selector.split(",").map((s) => s.trim()).includes(sel));

test("components.css has no literal colors, only tokens (black appears only inside color-mix)", () => {
  assert.doesNotMatch(css, /#[0-9a-fA-F]{3,8}\b/);
  assert.doesNotMatch(css, /\b(?:rgba?|hsla?)\(/);
  const named = [...css.matchAll(/\b(black|white|red|blue|green|gray|grey|yellow|orange)(?![-\w])/g)].filter(
    (m) => !css.slice(Math.max(0, m.index - 40), m.index).includes("color-mix"),
  );
  assert.deepEqual(named.map((m) => m[0]), []);
});

test("every custom property components.css reads is defined in tokens.css", () => {
  const tokens = readFileSync(new URL("../../src/dmslog/web/css/tokens.css", import.meta.url), "utf8");
  for (const m of css.matchAll(/var\((--[\w-]+)/g)) assert.match(tokens, new RegExp(`${m[1]}\\s*:`), m[1]);
});

test("disabled controls never use opacity; the cursor is not-allowed", () => {
  assert.doesNotMatch(css, /\bopacity\s*:\s*0?\.\d+/);
  for (const sel of [".ui-button:disabled", '.ui-button[aria-disabled="true"]']) {
    const body = rulesFor(sel).map((r) => r.body).join(";");
    assert.match(body, /cursor:\s*not-allowed/, sel);
    assert.match(body, /var\(--ink-disabled\)/, sel);
    assert.match(body, /border-color:\s*var\(--line\)/, sel);
  }
});

test("pressed and expanded buttons use the selection fill and the accent border", () => {
  const body = rulesFor('.ui-button[aria-pressed="true"]').map((r) => r.body).join(";");
  assert.match(body, /background:\s*var\(--row-selected\)/);
  assert.match(body, /border-color:\s*var\(--accent\)/);
  assert.ok(rulesFor('.ui-button[aria-expanded="true"]').length > 0);
});

test("focus ring is 2 px with offset 1 on buttons and fields", () => {
  const body = rulesFor(".ui-button:focus-visible").map((r) => r.body).join(";");
  assert.match(body, /outline:\s*2px solid var\(--focus-ring\)/);
  assert.match(body, /outline-offset:\s*1px/);
});

test("chip is 24 px high and its remove button is a 24 x 24 target", () => {
  assert.match(rulesFor(".ui-chip")[0].body, /height:\s*24px/);
  const remove = rulesFor(".ui-chip-remove")[0].body;
  assert.match(remove, /width:\s*24px/);
  assert.match(remove, /height:\s*24px/);
  assert.match(rulesFor(".ui-menu-item")[0].body, /min-height:\s*24px/);
  assert.match(rulesFor(".ui-button")[0].body, /min-height:\s*var\(--control-height\)/);
});

test("table: sticky header, tabular numbers, minimum width on text columns", () => {
  assert.match(rulesFor(".ui-table th").map((r) => r.body).join(";"), /position:\s*sticky/);
  assert.match(rulesFor(".ui-table .is-num")[0].body, /font-variant-numeric:\s*tabular-nums/);
  assert.match(rulesFor(".ui-table .is-num")[0].body, /text-align:\s*right/);
  assert.match(rulesFor(".ui-table .is-text")[0].body, /min-width:\s*\d+ch/);
});

test("the estimate bar waits 400 ms and stops moving with reduced motion; forced colors are handled", () => {
  assert.match(rulesFor(".ui-loading::before")[0].body, /ui-bar-show 0s linear 400ms/);
  assert.ok(RULES.some((r) => r.selector.includes(".ui-loading::before") && /animation:\s*none/.test(r.body)));
  assert.match(css, /@media \(forced-colors: active\)/);
  assert.match(css, /@media \(prefers-reduced-motion: reduce\)/);
});

test("the menu uses overlay surface, shadow, radius and the menu layer", () => {
  const body = rulesFor(".ui-menu")[0].body;
  for (const token of ["--surface-overlay", "--shadow-overlay", "--radius-lg", "--z-menu"]) assert.ok(body.includes(`var(${token})`), token);
});

test("the rule parser gives the same rules for a CRLF copy of the file", () => {
  const raw = readFileSync(new URL("../../src/dmslog/web/css/components.css", import.meta.url), "utf8").replace(/\r\n?/g, "\n");
  const prep = (t) => t.replace(/\r\n?/g, "\n").replace(/\/\*[\s\S]*?\*\//g, "");
  const crlf = raw.replace(/\n/g, "\r\n");
  assert.deepEqual(rules(prep(crlf)), rules(prep(raw)));
  assert.ok(rules(prep(crlf)).length > 50);
});
