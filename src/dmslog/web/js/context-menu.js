// The context menu of a grid record (UI-29): right click, Shift+F10 or the Menu key. It is the same .ui-menu component as
// the top bar menus (ui-menu.js: arrows, Home/End, Enter, Esc, first-letter jump, sub menus) over the same action registry:
// the items are the actions of the "record" group, with their shortcut beside them, and an action that does not apply
// stays in the menu, disabled, with the reason as its title.
// The record is the target by its id, never by a row element (the grid recycles its rows). The record actions work on the
// shown record, so `prepare(row)` makes the detail show it first; a menu is opened only while that record is still the
// selected one, in the same case, and it closes (focus back to the grid) when the case, the view or the selection change.
// Labels (a key value, a case name) can come from a log: ui-menu.js writes them with textContent only.
import { toMenuItems } from "./actions.js";
import {
  adaptMenuItems, captureTarget, contextMenuDefs, returnFocus, targetContext, targetCurrent,
} from "./commands-model.js";
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
  let pending = null; // { rowId } while the record loads, before the menu is on screen
  let target = null; // the captured target (commands-model.js captureTarget) while a menu is open
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

  // Anything that moves the page under the menu, or the focus away from it, closes it; this holds from the moment the
  // request is made (the record may still be loading) until the menu is closed.
  const onMove = (event) => {
    if (!isInMenu(event && event.target)) {
      close();
    }
  };
  const onFocusIn = (event) => {
    if (!isInMenu(event && event.target)) {
      cancel(false);
    }
  };
  const onPointerDown = (event) => {
    if (!isInMenu(event && event.target)) {
      cancel(false);
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
    doc.addEventListener("pointerdown", onPointerDown, true);
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
    doc.removeEventListener("pointerdown", onPointerDown, true);
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

  // Drops the request that is loading and closes the menu that is open.
  function cancel(restore) {
    seq += 1;
    pending = null;
    if (menu.isOpen()) {
      closeMenu(restore);
    } else {
      unlisten();
    }
  }
  const close = () => cancel(true);

  // The loaders of the actions with choices (Same key): their choices exist only after them.
  const loadChoices = () => Promise.all(actions.list({ group: "record" }).filter((def) => def.children && def.load).map((def) => actions.preload(def.id)));

  async function open({ row, x = 0, y = 0, keyboard = false } = {}) {
    const mine = ++seq;
    closeMenu(false);
    pending = null;
    if (!row || row.id === undefined || row.id === null) {
      return false;
    }
    const caseId = caseNow();
    const selected = (() => {
      try {
        return ctx.selectedRow();
      } catch {
        return null;
      }
    })();
    // The record is the selected one, in this case, before anything is loaded for it.
    if (caseId === null || !selected || !sameId(selected.id, row.id)) {
      return false;
    }
    pending = { rowId: row.id };
    listen();
    const abort = () => {
      if (mine === seq) {
        pending = null;
        unlisten();
      }
      return false;
    };
    let ready = false;
    try {
      ready = (await prepare(row)) === true;
      if (ready && mine === seq) {
        await loadChoices();
      }
    } catch {
      ready = false;
    }
    // Revalidated after every wait: this request is the latest, and the case, the selection and the detail are still it.
    if (!ready || mine !== seq) {
      return abort();
    }
    const record = captureTarget(ctx);
    if (record.caseId !== caseId || record.entry === null || !sameId(record.rowId, row.id) || !targetCurrent(ctx, record)) {
      return abort();
    }
    // The actions run with the captured record (its source, message type and keys), never with a second look at the detail.
    const bound = targetContext(ctx, record);
    // Checked when the item is chosen and AGAIN by invoke(), synchronously after its last wait and right before run().
    const current = () => targetCurrent(ctx, record);
    const wrap = (run) => () => (current() ? run() : undefined);
    const built = adaptMenuItems(toMenuItems(actions, contextMenuDefs(actions), bound, strings, current), actions, {
      singleKeysOn: singleKeysOn(), strings, wrap,
    });
    if (built.length === 0) {
      return abort();
    }
    pending = null;
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
    // The record a menu is open or loading for (null when none): the owner keeps the menu when that record is selected.
    targetId: () => (target ? target.rowId : pending ? pending.rowId : null),
    destroy() {
      close();
    },
  };
}
