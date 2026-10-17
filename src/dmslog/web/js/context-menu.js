// The context menu of a grid record (UI-29): right click, Shift+F10 or the Menu key. It is the same .ui-menu component as
// the top bar menus (ui-menu.js: arrows, Home/End, Enter, Esc, first-letter jump, sub menus) over the same action registry:
// the items are the actions of the "record" group, with their shortcut beside them, and an action that does not apply
// stays in the menu, disabled, with the reason as its title.
// The record is the target by its id, never by a row element (the grid recycles its rows). The record actions work on the
// shown record, so `prepare(row)` makes the detail show it first; a menu is opened only while that record is still the
// selected one, in the same case, and it closes (focus back to the grid) when the case, the view or the selection change.
// Labels (a key value, a case name) can come from a log: ui-menu.js writes them with textContent only.
import { toMenuItems } from "./actions.js";
import { adaptMenuItems, contextMenuDefs, returnFocus } from "./commands-model.js";
import { isInMenu } from "./filters.js";
import { S as defaultStrings } from "./strings.js";
import { createMenu } from "./ui-menu.js";

const sameId = (a, b) => a !== null && a !== undefined && b !== null && b !== undefined && String(a) === String(b);

/**
 * Options: actions (the registry), ctx (AppContext), doc, strings, singleKeysOn() (the one-letter shortcut preference),
 *   prepare(row) -> Promise<boolean>   makes the detail show `row`; false = it could not (no menu then)
 *   focusRow(rowId) -> boolean         gives the focus back to the grid (default: the grid viewport, then the case button)
 *   anchorFor() -> {x, y} | null       where a menu opened from the keyboard goes (default: the corner of the grid)
 *   onOpenChange(open)
 * Returns { open({row, x, y, keyboard}), openForSelection(), close(), isOpen(), destroy() }.
 */
export function createContextMenu({
  actions, ctx, doc = document, strings = defaultStrings, singleKeysOn = () => true,
  prepare = async () => true, focusRow = null, anchorFor = null, onOpenChange = null,
} = {}) {
  let seq = 0; // generation: an open() that is overtaken by another open() or by close() drops itself
  let target = null; // { rowId, caseId } while a menu is open
  let items = [];
  let anchor = { x: 0, y: 0 };
  let listening = false;

  const caseNow = () => {
    try {
      return ctx.caseId();
    } catch {
      return null;
    }
  };
  // The record is still the target: same case, still the selected row, and the detail shows it.
  const isCurrent = (mine) => {
    try {
      const row = ctx.selectedRow();
      const entry = ctx.entry();
      return caseNow() === mine.caseId && Boolean(row) && sameId(row.id, mine.rowId) && Boolean(entry) && sameId(entry.id, mine.rowId);
    } catch {
      return false;
    }
  };

  function restoreFocus() {
    let landed = false;
    if (typeof focusRow === "function") {
      try {
        landed = focusRow(target ? target.rowId : null) === true;
      } catch {
        landed = false;
      }
    }
    if (!landed) {
      returnFocus(doc, null);
    }
  }

  // The menu wants a trigger button: this stands in for one. It is a point (where the pointer was, or the row) and what
  // "give the focus back" means for a context menu.
  const attrs = {};
  const trigger = {
    ownerDocument: doc,
    textContent: strings.ctxMenuLabel,
    setAttribute: (name, value) => {
      attrs[name] = String(value);
    },
    getAttribute: (name) => (name in attrs ? attrs[name] : null),
    addEventListener() {},
    removeEventListener() {},
    contains: () => false,
    focus: restoreFocus,
    getBoundingClientRect: () => ({ left: anchor.x, right: anchor.x, top: anchor.y, bottom: anchor.y, width: 0, height: 0 }),
  };

  // Anything that moves the page under the menu, or the focus away from it, closes it.
  const onMove = (event) => {
    if (!isInMenu(event && event.target)) {
      close();
    }
  };
  const onFocusIn = (event) => {
    if (!isInMenu(event && event.target)) {
      closeMenu(false);
    }
  };
  const view = () => doc.defaultView || null;
  function listen() {
    if (listening) {
      return;
    }
    listening = true;
    doc.addEventListener("scroll", onMove, true);
    doc.addEventListener("wheel", onMove, true);
    doc.addEventListener("focusin", onFocusIn);
    const win = view();
    if (win && typeof win.addEventListener === "function") {
      win.addEventListener("resize", close);
      win.addEventListener("blur", close);
    }
  }
  function unlisten() {
    if (!listening) {
      return;
    }
    listening = false;
    doc.removeEventListener("scroll", onMove, true);
    doc.removeEventListener("wheel", onMove, true);
    doc.removeEventListener("focusin", onFocusIn);
    const win = view();
    if (win && typeof win.removeEventListener === "function") {
      win.removeEventListener("resize", close);
      win.removeEventListener("blur", close);
    }
  }

  const menu = createMenu({
    trigger,
    getItems: () => items,
    label: strings.ctxMenuLabel,
    doc,
    onOpenChange: (open) => {
      if (open) {
        listen();
        if (onOpenChange) {
          onOpenChange(true);
        }
        return;
      }
      unlisten();
      target = null;
      items = [];
      if (onOpenChange) {
        onOpenChange(false);
      }
    },
  });

  // restore: give the focus back to the grid, but only if it was in the menu (or lost); a menu replaced or closed because
  // the focus went elsewhere must not take it back.
  function closeMenu(restore) {
    if (!menu.isOpen()) {
      return;
    }
    const at = doc.activeElement;
    const lost = !at || at === doc.body;
    menu.close(restore && (lost || isInMenu(at)));
  }

  function close() {
    seq += 1;
    closeMenu(true);
  }

  // The loaders of the actions with choices (Same key): their choices exist only after them.
  const loadChoices = () => Promise.all(actions.list({ group: "record" }).filter((def) => def.children && def.load).map((def) => actions.preload(def.id)));

  async function open({ row, x = 0, y = 0, keyboard = false } = {}) {
    const mine = ++seq;
    closeMenu(false);
    if (!row || row.id === undefined || row.id === null) {
      return false;
    }
    const caseId = caseNow();
    if (caseId === null) {
      return false;
    }
    const overtaken = () => mine !== seq; // isCurrent() below also checks the case
    let ready = false;
    try {
      ready = (await prepare(row)) === true;
      if (ready) {
        await loadChoices();
      }
    } catch {
      ready = false;
    }
    const record = { rowId: row.id, caseId };
    if (!ready || overtaken() || !isCurrent(record)) {
      return false;
    }
    const wrap = (run) => () => (isCurrent(record) ? run() : undefined);
    const built = adaptMenuItems(toMenuItems(actions, contextMenuDefs(actions), ctx, strings), actions, {
      singleKeysOn: singleKeysOn(), strings, wrap,
    });
    if (built.length === 0) {
      return false;
    }
    target = record;
    items = built;
    anchor = { x: Number.isFinite(x) ? x : 0, y: Number.isFinite(y) ? y : 0 };
    menu.open(keyboard ? "first" : null);
    return true;
  }

  // From the keyboard (Shift+F10): the selected row, at the corner given by anchorFor().
  function openForSelection() {
    let row = null;
    try {
      row = ctx.selectedRow();
    } catch {
      row = null;
    }
    if (!row) {
      return Promise.resolve(false);
    }
    let at = null;
    try {
      at = typeof anchorFor === "function" ? anchorFor(row) : null;
    } catch {
      at = null;
    }
    if (!at) {
      const grid = typeof doc.getElementById === "function" ? doc.getElementById("grid-viewport") : null;
      const rect = grid && typeof grid.getBoundingClientRect === "function" ? grid.getBoundingClientRect() : null;
      at = { x: rect ? rect.left : 0, y: rect ? rect.top : 0 };
    }
    return open({ row, x: at.x, y: at.y, keyboard: true });
  }

  return {
    open,
    openForSelection,
    close,
    isOpen: () => menu.isOpen(),
    destroy() {
      close();
    },
  };
}
