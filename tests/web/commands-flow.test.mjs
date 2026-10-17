// UI-29: the command palette on a fake DOM, over the real action registry and the real registrations of app-actions.js:
// combobox + listbox with aria-activedescendant, filtering, the keys, disabled commands, Enter, Esc and the focus going back
// to the opener, input methods, held keys, and the ways the opening can be overtaken.
import test from "node:test";
import assert from "node:assert/strict";

import { createActionRegistry } from "../../src/dmslog/web/js/actions.js";
import { registerAppActions } from "../../src/dmslog/web/js/app-actions.js";
import { createPalette } from "../../src/dmslog/web/js/commands.js";
import { S } from "../../src/dmslog/web/js/strings.js";
import { makeDoc, tick } from "./fixtures/fake-dom.mjs";

function page({ record = true, cases = [{ id: "1", name: "Case one" }], singleKeysOn = true, restoreFocus = null, extra = null } = {}) {
  const doc = makeDoc();
  doc.getElementById = (id) => doc.body.all().find((n) => n.id === id) ?? null;
  const make = (tag, id, parent = doc.body) => {
    const node = doc.createElement(tag);
    node.id = id;
    parent.appendChild(node);
    return node;
  };
  const caseButton = make("button", "case-button");
  const viewport = make("div", "grid-viewport");
  viewport.setAttribute("tabindex", "0");
  const opener = make("button", "view-button");
  const st = { record, focusAtRun: [], calls: [] };
  const deps = new Proxy({}, {
    get: (target, name) => (name in target ? target[name] : (...args) => {
      st.calls.push([name, ...args]);
      st.focusAtRun.push(doc.activeElement);
      return null;
    }),
  });
  deps.ready = new Proxy({}, { get: () => Promise.resolve() });
  deps.cases = () => cases;
  deps.theme = () => "dark";
  deps.drawerOpen = () => false;
  deps.repeats = () => null;
  deps.followTailOn = () => false;
  deps.recordShown = () => st.record;
  deps.hasType = () => st.record;
  deps.highlightReady = () => true;
  const ctx = {
    caseId: () => "C1", entry: () => (st.record ? { id: 1, source_id: 1, tpl_id: 1, keys: [] } : null), selectedRow: () => (st.record ? { id: 1 } : null),
    userFilter: () => null, setUserFilter() {}, dock: { isOpen: () => false },
  };
  const errors = [];
  const actions = createActionRegistry({ strings: S, singleKeysOn: () => singleKeysOn, onError: (err, id) => errors.push([id, err.message]) });
  registerAppActions(actions, deps);
  if (extra) extra(actions);
  const focusAtInvoke = [];
  const seen = Object.create(actions);
  seen.invoke = (...args) => {
    focusAtInvoke.push(doc.activeElement); // synchronously: before any await of the registry
    return actions.invoke(...args);
  };
  const palette = createPalette({ actions: seen, ctx, doc, singleKeysOn: () => singleKeysOn, restoreFocus, onError: (err) => errors.push(err) });
  const parts = () => {
    const dialog = doc.body.children.find((c) => c.tagName === "DIALOG");
    const all = dialog ? dialog.all() : [];
    return {
      dialog,
      field: all.find((n) => n.tagName === "INPUT"),
      listbox: all.find((n) => n.attrs.role === "listbox"),
      options: all.filter((n) => n.attrs.role === "option"),
      status: all.filter((n) => n.attrs["aria-live"] !== undefined || n.attrs.role === "status"),
    };
  };
  return { doc, st, palette, focusAtInvoke, parts, opener, viewport, caseButton, errors, actions };
}

const text = (node) => node.children[0].textContent;
const type = (p, value) => {
  const { field } = p.parts();
  field.value = value;
  field.dispatch("input");
};
const press = (p, key, init = {}) => p.parts().field.dispatch("keydown", { key, ...init });
const optionLabels = (p) => p.parts().options.map(text);
const activeOption = (p) => {
  const { field, options } = p.parts();
  const id = field.getAttribute("aria-activedescendant");
  return id === null ? null : options.find((o) => o.id === id) ?? null;
};
const open = async (p, from = p.opener) => {
  from.focus();
  await p.palette.open(from);
};

test("open: a modal dialog with a combobox and a listbox; the focus is in the field; the first available command is active", async () => {
  const p = page();
  await open(p);
  const { dialog, field, listbox, options, status } = p.parts();
  assert.ok(dialog.classList.contains("ui-dialog"));
  assert.equal(dialog.attrs["aria-labelledby"] !== undefined, true);
  assert.equal(dialog.open, true);
  assert.equal(p.doc.activeElement, field);
  assert.equal(field.attrs.role, "combobox");
  assert.equal(field.attrs["aria-controls"], listbox.id);
  assert.equal(field.attrs["aria-autocomplete"], "list");
  assert.equal(field.attrs["aria-expanded"], "true");
  assert.equal(field.attrs["aria-label"], S.cmdPlaceholder);
  assert.equal(listbox.attrs.role, "listbox");
  assert.equal(listbox.attrs["aria-label"], S.cmdListLabel);
  assert.ok(options.length > 20);
  assert.equal(new Set(options.map((o) => o.id)).size, options.length, "unique ids for aria-activedescendant");
  assert.equal(activeOption(p), options[0]);
  assert.equal(options[0].attrs["aria-selected"], "true");
  assert.equal(options.filter((o) => o.attrs["aria-selected"] === "true").length, 1);
  assert.equal(status.length, 1, "one live region");
  assert.equal(status[0].attrs["aria-live"], "polite");
  assert.equal(status[0].textContent, S.cmdCountOther.replace("{count}", String(options.length)));
  assert.ok(!optionLabels(p).includes(S.actPalette), "the palette does not list itself");
  assert.equal(p.palette.isOpen(), true);
});

test("typing filters by label, resets the active command to the best match and says how many match", async () => {
  const p = page();
  await open(p);
  type(p, "distinct");
  assert.deepEqual(optionLabels(p), [S.actDistinct]);
  assert.equal(activeOption(p), p.parts().options[0]);
  assert.equal(p.parts().status[0].textContent, S.cmdCountOne.replace("{count}", "1"));
  type(p, "THEME");
  assert.deepEqual(optionLabels(p), [S.actTheme + ": " + S.themeSystem, S.actTheme + ": " + S.themeLight, S.actTheme + ": " + S.themeDark]);
  type(p, "");
  assert.ok(p.parts().options.length > 20);
});

test("no match: a static message in the one live region, the list hidden, no active descendant, Enter does nothing", async () => {
  const p = page();
  await open(p);
  type(p, "zzzzzz");
  const { field, listbox, options, status } = p.parts();
  assert.equal(options.length, 0);
  assert.equal(listbox.hidden, true);
  assert.equal(field.attrs["aria-expanded"], "false");
  assert.equal(field.getAttribute("aria-activedescendant"), null);
  assert.equal(status.length, 1);
  assert.equal(status[0].textContent, S.cmdEmpty);
  press(p, "Enter");
  press(p, "ArrowDown");
  assert.equal(p.palette.isOpen(), true);
  assert.deepEqual(p.st.calls, []);
  type(p, "dist");
  assert.equal(p.parts().listbox.hidden, false);
  assert.equal(p.parts().field.attrs["aria-expanded"], "true");
});

test("arrows move the active option and wrap, skipping the commands that do not apply; Home and End stay with the text field", async () => {
  const p = page({ record: false });
  await open(p);
  const { options } = p.parts();
  const enabled = options.filter((o) => o.attrs["aria-disabled"] === undefined);
  const disabled = options.filter((o) => o.attrs["aria-disabled"] === "true");
  assert.ok(disabled.length > 0 && enabled.length > 0);
  assert.equal(activeOption(p), enabled[0]);
  const down = press(p, "ArrowDown");
  assert.equal(down.prevented, true);
  assert.equal(activeOption(p), enabled[1]);
  for (let i = 0; i < enabled.length - 2; i += 1) press(p, "ArrowDown");
  assert.equal(activeOption(p), enabled[enabled.length - 1]);
  press(p, "ArrowDown");
  assert.equal(activeOption(p), enabled[0], "wraps");
  press(p, "ArrowUp");
  assert.equal(activeOption(p), enabled[enabled.length - 1], "wraps backward");
  for (const key of ["Home", "End", "ArrowLeft", "ArrowRight"]) {
    assert.equal(press(p, key).prevented, false, key + " is not taken from the field");
  }
  press(p, "PageUp");
  assert.ok(enabled.indexOf(activeOption(p)) < enabled.length - 1);
  assert.equal(options.filter((o) => o.attrs["aria-selected"] === "true").length, 1);
});

test("a command that does not apply is listed with its reason, never active, and does not run (key or click)", async () => {
  const p = page({ record: false });
  await open(p);
  type(p, "copy text");
  const [option] = p.parts().options;
  assert.equal(text(option), S.actCopyRecord);
  assert.equal(option.attrs["aria-disabled"], "true");
  assert.ok(option.textContent.includes(S.cmpNeedRecord), "the reason is part of the option");
  assert.equal(p.parts().field.getAttribute("aria-activedescendant"), null);
  press(p, "Enter");
  option.dispatch("click");
  await tick();
  assert.equal(p.palette.isOpen(), true);
  assert.deepEqual(p.st.calls, []);
});

test("Enter runs the active command through the registry; the palette is closed and the focus is back on the opener BEFORE it runs", async () => {
  const p = page();
  await open(p);
  type(p, "keyboard shortcuts");
  assert.equal(text(activeOption(p)), S.actShortcuts);
  const e = press(p, "Enter");
  assert.equal(e.prevented, true);
  await tick();
  assert.deepEqual(p.st.calls, [["openShortcuts"]]);
  assert.equal(p.st.focusAtRun[0], p.opener, "actions that open a dialog read the focused control");
  assert.deepEqual(p.focusAtInvoke, [p.opener], "the focus was back before invoke() was even called");
  assert.equal(p.parts().dialog.open, false);
  assert.equal(p.palette.isOpen(), false);
  assert.equal(p.doc.activeElement, p.opener);
});

test("a choice runs its action with the choice as argument (Theme: Light)", async () => {
  const p = page();
  await open(p);
  type(p, "theme light");
  press(p, "Enter");
  await tick();
  assert.deepEqual(p.st.calls, [["setTheme", "light"]]);
});

test("a click runs an available command; the pointer moving over one makes it active but never a disabled one", async () => {
  const p = page({ record: false });
  await open(p);
  const { options, listbox } = p.parts();
  const enabled = options.filter((o) => o.attrs["aria-disabled"] === undefined);
  const disabled = options.find((o) => o.attrs["aria-disabled"] === "true");
  const down = listbox.dispatch("pointerdown");
  assert.equal(down.prevented, true, "the field keeps the focus");
  options[options.indexOf(enabled[2])].children[0].dispatch("pointermove");
  assert.equal(activeOption(p), enabled[2]);
  disabled.dispatch("pointermove");
  assert.equal(activeOption(p), enabled[2]);
  type(p, "keyboard shortcuts");
  p.parts().options[0].children[0].dispatch("click");
  await tick();
  assert.deepEqual(p.st.calls, [["openShortcuts"]]);
  assert.equal(p.palette.isOpen(), false);
});

test("Escape closes and gives the focus back to the opener; it works when the opener was focused by a menu closing", async () => {
  const p = page();
  await open(p);
  press(p, "Escape");
  assert.equal(p.palette.isOpen(), false);
  assert.equal(p.parts().dialog.open, false);
  assert.equal(p.doc.activeElement, p.opener);
});

test("the opener is gone or hidden: the focus goes to the grid, then to the case button", async () => {
  const p = page();
  await open(p);
  p.opener.remove();
  press(p, "Escape");
  assert.equal(p.doc.activeElement, p.viewport);
  await p.palette.open(p.viewport);
  p.viewport.hidden = true;
  press(p, "Escape");
  assert.equal(p.doc.activeElement, p.caseButton);
});

test("the owner decides where the focus goes back (restoreFocus); the default runs when it cannot", async () => {
  const log = [];
  const p = page({ restoreFocus: (el) => { log.push(el.id); return false; } });
  await open(p);
  press(p, "Escape");
  assert.deepEqual(log, ["view-button"]);
  assert.equal(p.doc.activeElement, p.opener, "fallback");
  const q = page({ restoreFocus: () => { throw new Error("boom"); } });
  await open(q);
  press(q, "Escape");
  assert.equal(q.doc.activeElement, q.opener);
});

test("an input method composing keeps its keys: Enter and arrows are not taken (isComposing, keyCode 229)", async () => {
  const p = page();
  await open(p);
  type(p, "keyboard shortcuts");
  for (const init of [{ isComposing: true }, { keyCode: 229 }]) {
    assert.equal(press(p, "Enter", init).prevented, false);
    assert.equal(press(p, "ArrowDown", init).prevented, false);
  }
  await tick();
  assert.deepEqual(p.st.calls, []);
  assert.equal(p.palette.isOpen(), true);
  press(p, "Enter");
  await tick();
  assert.equal(p.st.calls.length, 1);
});

test("a held Enter runs once; Enter with a modifier does not run", async () => {
  const p = page();
  await open(p);
  type(p, "keyboard shortcuts");
  press(p, "Enter", { ctrlKey: true });
  press(p, "Enter", { altKey: true });
  press(p, "Enter", { shiftKey: true });
  press(p, "Enter", { repeat: true });
  await tick();
  assert.deepEqual(p.st.calls, []);
  press(p, "Enter");
  press(p, "Enter", { repeat: true });
  await tick();
  assert.equal(p.st.calls.length, 1);
});

test("Ctrl+K inside the palette keeps it open and keeps the browser from taking the key", async () => {
  const p = page();
  await open(p);
  const e = press(p, "k", { ctrlKey: true });
  assert.equal(e.prevented, true);
  const upper = press(p, "K", { ctrlKey: true });
  assert.equal(upper.prevented, true);
  assert.equal(p.palette.isOpen(), true);
  assert.equal(p.parts().field.value, "");
});

test("fast typing: every keystroke redraws the whole list for the text at that moment, never a mix", async () => {
  const p = page();
  await open(p);
  for (const value of ["d", "di", "dis", "dist", "disti", "d", "dis", "distinct"]) type(p, value);
  assert.deepEqual(optionLabels(p), [S.actDistinct]);
  assert.equal(p.parts().options.length, p.parts().listbox.children.length);
  assert.equal(p.parts().options.filter((o) => o.attrs["aria-selected"] === "true").length, 1);
});

test("opening again: the text is cleared and the commands are asked again (a record became selected)", async () => {
  const p = page({ record: false });
  await open(p);
  type(p, "copy text");
  assert.equal(p.parts().options[0].attrs["aria-disabled"], "true");
  press(p, "Escape");
  p.st.record = true;
  await open(p);
  assert.equal(p.parts().field.value, "");
  type(p, "copy text");
  assert.equal(p.parts().options[0].attrs["aria-disabled"], undefined);
  assert.equal(p.doc.body.children.filter((c) => c.tagName === "DIALOG").length, 1, "one dialog, built once");
});

test("opening while open does nothing; close() while the choices load drops the opening", async () => {
  const p = page();
  await open(p);
  await p.palette.open(p.opener);
  assert.equal(p.doc.body.children.filter((c) => c.tagName === "DIALOG").length, 1);
  press(p, "Escape");

  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  const slow = page({
    extra: (actions) => actions.register({
      id: "view.slow", labelKey: "actTheme", group: "view", enabled: () => true, children: () => [{ label: "a", args: 1 }], load: () => gate, run() {},
    }),
  });
  slow.opener.focus();
  const pending = slow.palette.open(slow.opener);
  slow.palette.close();
  release();
  await pending;
  assert.equal(slow.palette.isOpen(), false);
  assert.equal(slow.doc.body.children.filter((c) => c.tagName === "DIALOG").length, 0);

  const again = slow.palette.open(slow.opener);
  const second = slow.palette.open(slow.opener);
  await Promise.all([again, second]);
  assert.equal(slow.doc.body.children.filter((c) => c.tagName === "DIALOG").length, 1);
});

test("a command text from a log (a case name with markup) is shown as text, never as elements", async () => {
  const name = "<img src=x onerror=alert(1)>";
  const p = page({ cases: [{ id: "9", name }] });
  await open(p);
  type(p, "img");
  const [option] = p.parts().options;
  assert.equal(text(option), S.actCaseOpen + ": " + name);
  assert.ok(option.all().every((n) => n.tagName !== "IMG"));
  assert.equal(option.children.length, 2, "label and group only");
});

test("one-letter shortcuts are not shown while those are turned off; the others are", async () => {
  const on = page();
  await open(on);
  type(on, "bookmark the selected");
  assert.equal(on.parts().options[0].textContent.includes("b"), true);
  const keyOf = (p) => p.parts().options[0].all().find((n) => n.className === "cmd-key");
  assert.equal(keyOf(on).textContent, "b");
  const off = page({ singleKeysOn: false });
  await open(off);
  type(off, "bookmark the selected");
  assert.equal(keyOf(off), undefined);
  type(off, "previous view");
  assert.equal(keyOf(off).textContent, "Alt+Left");
});

test("destroy closes and removes the dialog", async () => {
  const p = page();
  await open(p);
  p.palette.destroy();
  assert.equal(p.doc.body.children.filter((c) => c.tagName === "DIALOG").length, 0);
  assert.equal(p.palette.isOpen(), false);
});

test("a failing command is reported by the registry and the palette is already closed", async () => {
  const p = page({
    extra: (actions) => actions.register({ id: "view.boom", labelKey: "actTheme", group: "view", enabled: () => true, run() { throw new Error("boom"); } }),
  });
  await open(p);
  type(p, "theme");
  const boom = p.parts().options.find((o) => o.dataset.action === "view.boom");
  assert.ok(boom);
  boom.children[0].dispatch("click");
  await tick();
  assert.equal(p.palette.isOpen(), false);
  assert.deepEqual(p.errors, [["view.boom", "boom"]]);
  assert.equal(p.doc.activeElement, p.opener);
});
