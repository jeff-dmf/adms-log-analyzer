// The command palette (UI-29): Ctrl+K and View > Command palette…. A modal .ui-dialog with a combobox (the text field)
// and a listbox (the commands); the focus stays in the field and aria-activedescendant names the active option. It lists
// every action of the registry (the one the menus use), filtered by label; Enter runs the active one, Esc closes and
// gives the focus back to what opened it. The rules (filter, order, disabled commands) are pure and live in
// commands-model.js. It loads on the first use (import() in main.js, tried again after a failure).
// Texts come from strings.js; labels (a case name, a key value) can come from a log: textContent only, never markup.
import { isComposingEvent } from "./shortcuts-model.js";
import {
  buildCommands, filterCommands, keepActive, returnFocus, stepActive,
} from "./commands-model.js";
import { createDialogShell, node } from "./panel-shell.js";
import { S as defaultStrings, format, pluralKey } from "./strings.js";

let uid = 0;

/**
 * Options: actions (the registry), ctx (AppContext), doc, strings, singleKeysOn() (the one-letter shortcut preference),
 * restoreFocus(el) -> boolean (gives the focus back; the default tries `el`, then the grid and the case button),
 * onError(err).
 * Returns { open(opener), close(), isOpen(), destroy() }.
 */
export function createPalette({
  actions, ctx, doc = document, strings = defaultStrings, singleKeysOn = () => true, restoreFocus = null, onError = () => {},
} = {}) {
  const id = "cmd" + String(++uid);
  let shell = null;
  let field = null;
  let listbox = null;
  let status = null;
  let commands = [];
  let list = [];
  let nodes = []; // the option elements, parallel to `list`
  let active = -1;
  let from = null; // what had the focus when the palette opened
  let opening = 0; // generation of open(): a close or a second open while the choices load drops the first

  const giveBack = (el) => {
    let landed = false;
    if (typeof restoreFocus === "function") {
      try {
        landed = restoreFocus(el) === true;
      } catch {
        landed = false;
      }
    }
    if (!landed) {
      returnFocus(doc, el);
    }
  };

  function setActive(next) {
    const before = active;
    active = next;
    if (before >= 0 && nodes[before]) {
      nodes[before].setAttribute("aria-selected", "false");
    }
    if (next >= 0 && nodes[next]) {
      nodes[next].setAttribute("aria-selected", "true");
      field.setAttribute("aria-activedescendant", nodes[next].id);
      if (typeof nodes[next].scrollIntoView === "function") {
        nodes[next].scrollIntoView({ block: "nearest" });
      }
    } else {
      field.removeAttribute("aria-activedescendant");
    }
  }

  function option(command, position) {
    const item = node(doc, "li", "cmd-option");
    item.id = id + "-option-" + String(command.index);
    item.setAttribute("role", "option");
    item.setAttribute("aria-selected", "false");
    if (!command.enabled) {
      item.setAttribute("aria-disabled", "true");
    }
    item.dataset.action = command.actionId;
    item.dataset.position = String(position);
    item.appendChild(node(doc, "span", "cmd-label", command.label));
    if (command.groupLabel !== "") {
      item.appendChild(node(doc, "span", "cmd-group", command.groupLabel));
    }
    if (!command.enabled && command.reason !== "") {
      item.appendChild(node(doc, "span", "cmd-reason", command.reason));
    }
    if (command.shortcut !== null) {
      item.appendChild(node(doc, "kbd", "cmd-key", command.shortcut));
    }
    return item;
  }

  // Redraws the list for the text in the field. The one live region says how many commands match, or that none does.
  function refresh() {
    const keep = active >= 0 && list[active] ? list[active].key : null;
    list = filterCommands(commands, field.value);
    listbox.textContent = "";
    nodes = list.map((command, position) => option(command, position));
    for (const item of nodes) {
      listbox.appendChild(item);
    }
    listbox.hidden = list.length === 0;
    field.setAttribute("aria-expanded", list.length === 0 ? "false" : "true");
    active = -1;
    setActive(keepActive(list, keep));
    status.textContent = list.length === 0
      ? strings.cmdEmpty
      : format(strings[pluralKey("cmdCount", list.length)], { count: list.length });
  }

  function run(command) {
    if (!command || !command.enabled) {
      return; // a disabled command never runs, from the keyboard or the pointer
    }
    // Close first: the focus is back where it was before the action runs (the actions that open a dialog read it).
    close();
    Promise.resolve(actions.invoke(command.actionId, ctx, command.args)).catch(onError);
  }

  function onKeydown(event) {
    if (isComposingEvent(event)) {
      return; // an input method owns the key (Enter confirms the composition)
    }
    const key = event.key;
    if (event.ctrlKey && !event.altKey && !event.metaKey && String(key).toLowerCase() === "k") {
      event.preventDefault(); // the palette is open already; the browser must not take the key either
      return;
    }
    if (key === "Enter" && !event.ctrlKey && !event.altKey && !event.metaKey && !event.shiftKey) {
      event.preventDefault();
      if (event.repeat !== true) {
        run(list[active]);
      }
      return;
    }
    if (event.ctrlKey || event.altKey || event.metaKey) {
      return;
    }
    const next = stepActive(list, active, key);
    if (next !== null) {
      event.preventDefault();
      if (next >= 0 && next !== active) {
        setActive(next);
      }
    }
  }

  function build() {
    shell = createDialogShell(doc, { title: strings.cmdTitle, className: "command-palette" });
    field = node(doc, "input", "ui-input cmd-field");
    field.type = "text";
    field.id = id + "-field";
    field.setAttribute("role", "combobox");
    field.setAttribute("aria-autocomplete", "list");
    field.setAttribute("aria-expanded", "true");
    field.setAttribute("aria-label", strings.cmdPlaceholder);
    field.setAttribute("autocomplete", "off");
    field.setAttribute("spellcheck", "false");
    field.setAttribute("placeholder", strings.cmdPlaceholder);
    listbox = node(doc, "ul", "cmd-list");
    listbox.id = id + "-list";
    listbox.setAttribute("role", "listbox");
    listbox.setAttribute("aria-label", strings.cmdListLabel);
    field.setAttribute("aria-controls", listbox.id);
    status = node(doc, "div", "cmd-status ui-status");
    status.setAttribute("role", "status");
    status.setAttribute("aria-live", "polite");
    field.addEventListener("input", refresh);
    field.addEventListener("keydown", onKeydown);
    listbox.addEventListener("pointerdown", (event) => {
      event.preventDefault(); // the field keeps the focus
    });
    listbox.addEventListener("click", (event) => {
      const item = event.target && typeof event.target.closest === "function" ? event.target.closest(".cmd-option") : null;
      const at = item ? nodes.indexOf(item) : -1;
      if (at >= 0) {
        run(list[at]);
      }
    });
    listbox.addEventListener("pointermove", (event) => {
      const item = event.target && typeof event.target.closest === "function" ? event.target.closest(".cmd-option") : null;
      const at = item ? nodes.indexOf(item) : -1;
      if (at >= 0 && at !== active && list[at].enabled) {
        setActive(at);
      }
    });
    shell.body.append(field, listbox, status);
    shell.foot.appendChild(node(doc, "span", "cmd-hint", strings.cmdHint));
    doc.body.appendChild(shell.dialog);
  }

  // The choices of an action with choices (a case, the repeat window) exist only after its loader ran: wait for those.
  const loadChoices = () => Promise.all(actions.list().filter((def) => def.children && def.load).map((def) => actions.preload(def.id)));

  async function open(opener) {
    const origin = opener || doc.activeElement || null;
    if (shell !== null && shell.isOpen()) {
      return;
    }
    const mine = ++opening;
    await loadChoices();
    if (mine !== opening || (shell !== null && shell.isOpen())) {
      return; // closed or opened again meanwhile
    }
    from = origin;
    if (shell === null) {
      build();
    }
    commands = buildCommands(actions, ctx, { strings, singleKeysOn: singleKeysOn() });
    field.value = "";
    refresh();
    // The shell gives the focus back through this object: the opener if it still takes it, else the grid.
    shell.open({ focus: () => giveBack(from) });
    field.focus();
  }

  function close() {
    opening += 1;
    if (shell !== null) {
      shell.close();
    }
  }

  return {
    open,
    close,
    isOpen: () => shell !== null && shell.isOpen(),
    destroy() {
      close();
      if (shell !== null) {
        shell.dialog.remove();
        shell = null;
      }
    },
  };
}
