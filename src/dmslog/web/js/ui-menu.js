// Menu behavior for the .ui-menu component (UI-10) and a small chip builder.
// Pure model first (item normalization, roving focus, first-letter jump), then a thin DOM layer.
// It accepts the MenuItem contract of docs/notas/ui-contratos.md (section 1.4) plus `pressed` and `separator`.
// It never builds markup from strings: every label, shortcut and title is set through textContent or attributes,
// because a label can carry text from a log. The module has no visible text of its own.

/**
 * @typedef {Object} MenuItem
 * @property {string} id
 * @property {string} label
 * @property {string|null} [shortcut]    text on the right (already formatted)
 * @property {"menuitem"|"menuitemcheckbox"|"menuitemradio"} [role]
 * @property {boolean|null} [checked]
 * @property {boolean} [pressed]         shortcut for a checkbox item
 * @property {boolean} [disabled]
 * @property {string|null} [title]       reason shown when disabled
 * @property {MenuItem[]} [children]     submenu
 * @property {boolean} [separator]       draws a divider; the other fields are ignored
 * @property {() => void} [run]
 * @property {() => void} [preload]
 */

/** Fills the defaults of a MenuItem. */
export function normalizeItem(raw) {
  if (raw && raw.separator) return { separator: true, id: raw.id ?? "", disabled: true };
  let role = raw.role ?? "menuitem";
  let checked = raw.checked ?? null;
  if (raw.pressed !== undefined && raw.pressed !== null) {
    role = "menuitemcheckbox";
    checked = Boolean(raw.pressed);
  }
  checked = role === "menuitem" ? null : Boolean(checked);
  const children = Array.isArray(raw.children) && raw.children.length > 0 ? raw.children : null;
  return {
    separator: false,
    id: String(raw.id ?? ""),
    label: String(raw.label ?? ""),
    shortcut: raw.shortcut ? String(raw.shortcut) : null,
    role,
    checked,
    disabled: Boolean(raw.disabled),
    title: raw.title ? String(raw.title) : null,
    children,
    run: typeof raw.run === "function" ? raw.run : null,
    preload: typeof raw.preload === "function" ? raw.preload : null,
  };
}

const canFocus = (item) => !item.separator && !item.disabled;

/** Index of the next focusable item from `from` in direction `step` (+1 or -1), wrapping; -1 when none. */
export function stepIndex(items, from, step) {
  const n = items.length;
  for (let k = 1; k <= n; k++) {
    const i = (((from + step * k) % n) + n) % n;
    if (canFocus(items[i])) return i;
  }
  return -1;
}

export const firstIndex = (items) => stepIndex(items, -1, 1);
export const lastIndex = (items) => stepIndex(items, items.length, -1);

/** New active index for a navigation key, or null when the key does not navigate. */
export function navigate(items, current, key) {
  if (key === "ArrowDown") return stepIndex(items, current, 1);
  if (key === "ArrowUp") return stepIndex(items, current < 0 ? items.length : current, -1);
  if (key === "Home") return firstIndex(items);
  if (key === "End") return lastIndex(items);
  return null;
}

/** Next focusable item (after `current`, wrapping) whose label starts with `char`, case-insensitive; -1 when none. */
export function indexByInitial(items, current, char) {
  const wanted = String(char).toLocaleLowerCase();
  const n = items.length;
  for (let k = 1; k <= n; k++) {
    const i = (((current + k) % n) + n) % n;
    if (canFocus(items[i]) && items[i].label.toLocaleLowerCase().startsWith(wanted)) return i;
  }
  return -1;
}

const isInitialKey = (e) => e.key.length === 1 && e.key !== " " && !e.ctrlKey && !e.altKey && !e.metaKey;

/**
 * Binds a menu to its trigger button. `getItems()` is called at every opening, so enabled states are
 * computed on display. Returns { open, close, toggle, isOpen }.
 */
export function createMenu({ trigger, getItems, label = null, onOpenChange = null, doc = trigger.ownerDocument }) {
  /** @type {{el: any, items: any[], nodes: any[], active: number, parent: any, anchor: any}[]} */
  let levels = [];
  let listening = false;

  trigger.setAttribute("aria-haspopup", "menu");
  trigger.setAttribute("aria-expanded", "false");

  const isOpen = () => levels.length > 0;
  const current = () => levels[levels.length - 1];

  function setActive(level, index, focus = true) {
    level.active = index;
    level.nodes.forEach((node, i) => {
      if (!node) return;
      node.setAttribute("tabindex", i === index ? "0" : "-1");
    });
    if (focus && index >= 0) {
      level.nodes[index].focus();
      const item = level.items[index];
      if (item.preload) item.preload();
    }
  }

  function place(level, parentNode) {
    const view = doc.defaultView;
    const style = level.el.style;
    style.position = "fixed";
    if (!parentNode.getBoundingClientRect || !view) return;
    const a = parentNode.getBoundingClientRect();
    const sub = level.parent !== null;
    const width = level.el.offsetWidth || 0;
    const height = level.el.offsetHeight || 0;
    let left = sub ? a.right : a.left;
    let top = sub ? a.top : a.bottom;
    if (left + width > view.innerWidth) left = Math.max(0, (sub ? a.left : a.right) - width);
    if (top + height > view.innerHeight) top = Math.max(0, (sub ? a.bottom : a.top) - height);
    style.left = `${left}px`;
    style.top = `${top}px`;
  }

  function buildLevel(rawItems, anchor, parent) {
    const items = rawItems.map(normalizeItem);
    const el = doc.createElement("div");
    el.className = "ui-menu";
    el.setAttribute("role", "menu");
    el.setAttribute("tabindex", "-1");
    const name = label ?? trigger.getAttribute("aria-label") ?? trigger.textContent;
    if (name && !parent) el.setAttribute("aria-label", name);
    const level = { el, items, nodes: [], active: -1, parent, anchor };

    items.forEach((item, index) => {
      if (item.separator) {
        const hr = doc.createElement("div");
        hr.className = "ui-menu-separator";
        hr.setAttribute("role", "separator");
        el.appendChild(hr);
        level.nodes.push(null);
        return;
      }
      const node = doc.createElement("div");
      node.className = "ui-menu-item";
      node.setAttribute("role", item.role);
      node.setAttribute("tabindex", "-1");
      if (item.id) node.setAttribute("data-id", item.id);
      if (item.checked !== null) node.setAttribute("aria-checked", String(item.checked));
      if (item.disabled) node.setAttribute("aria-disabled", "true");
      if (item.title) node.setAttribute("title", item.title);
      if (item.children) {
        node.setAttribute("aria-haspopup", "menu");
        node.setAttribute("aria-expanded", "false");
      }
      const text = doc.createElement("span");
      text.className = "ui-menu-label";
      text.textContent = item.label;
      node.appendChild(text);
      if (item.shortcut) {
        const key = doc.createElement("span");
        key.className = "ui-menu-shortcut";
        key.textContent = item.shortcut;
        node.appendChild(key);
      }
      if (item.children) {
        const arrow = doc.createElement("span");
        arrow.className = "ui-menu-arrow";
        arrow.setAttribute("aria-hidden", "true");
        node.appendChild(arrow);
      }
      node.addEventListener("click", (e) => {
        e.stopPropagation?.();
        activate(level, index);
      });
      node.addEventListener("pointerenter", () => {
        if (item.preload) item.preload();
        if (!item.disabled && current() === level) setActive(level, index);
      });
      el.appendChild(node);
      level.nodes.push(node);
    });

    el.addEventListener("keydown", (e) => onKeydown(level, e));
    doc.body.appendChild(el);
    levels.push(level);
    place(level, anchor);
    return level;
  }

  function closeLevelsAbove(level) {
    while (levels.length > 0 && current() !== level) {
      const top = levels.pop();
      top.el.remove();
      const parentNode = top.parent?.nodes[top.parent.active];
      if (parentNode) parentNode.setAttribute("aria-expanded", "false");
    }
  }

  function openSub(level, index, focusFirst) {
    const item = level.items[index];
    if (!item.children || item.disabled) return;
    closeLevelsAbove(level);
    const node = level.nodes[index];
    node.setAttribute("aria-expanded", "true");
    const sub = buildLevel(item.children, node, level);
    const first = firstIndex(sub.items);
    if (focusFirst && first >= 0) setActive(sub, first);
    else sub.el.focus();
  }

  function activate(level, index) {
    const item = level.items[index];
    if (!item || item.separator || item.disabled) return;
    if (item.children) {
      openSub(level, index, true);
      return;
    }
    close(true);
    if (item.run) item.run();
  }

  function onKeydown(level, e) {
    const key = e.key;
    if (key === "Escape") {
      e.preventDefault();
      e.stopPropagation?.();
      if (level.parent) {
        const parent = level.parent;
        closeLevelsAbove(parent);
        setActive(parent, parent.active);
      } else close(true);
      return;
    }
    if (key === "Tab") {
      close(false);
      return;
    }
    if (key === "ArrowLeft" && level.parent) {
      e.preventDefault();
      const parent = level.parent;
      closeLevelsAbove(parent);
      setActive(parent, parent.active);
      return;
    }
    if (key === "ArrowRight") {
      e.preventDefault();
      if (level.active >= 0) openSub(level, level.active, true);
      return;
    }
    if (key === "Enter" || key === " ") {
      e.preventDefault();
      if (level.active >= 0) activate(level, level.active);
      return;
    }
    const target = navigate(level.items, level.active, key);
    if (target !== null) {
      e.preventDefault();
      if (target >= 0) setActive(level, target);
      return;
    }
    if (isInitialKey(e)) {
      const i = indexByInitial(level.items, level.active, key);
      if (i >= 0) {
        e.preventDefault();
        setActive(level, i);
      }
    }
  }

  function onOutside(e) {
    const t = e.target;
    if (trigger.contains?.(t) || trigger === t) return;
    if (levels.some((l) => l.el.contains?.(t) || l.el === t)) return;
    close(false);
  }

  /** where: "first" | "last" | null (focus the menu itself, for pointer opening). */
  function open(where = "first") {
    if (isOpen()) return;
    const level = buildLevel(getItems(), trigger, null);
    trigger.setAttribute("aria-expanded", "true");
    if (!listening) {
      doc.addEventListener("pointerdown", onOutside, true);
      listening = true;
    }
    if (where === "first") setActive(level, firstIndex(level.items));
    else if (where === "last") setActive(level, lastIndex(level.items));
    else level.el.focus();
    onOpenChange?.(true);
  }

  function close(restoreFocus = true) {
    if (!isOpen()) return;
    for (const l of levels) l.el.remove();
    levels = [];
    trigger.setAttribute("aria-expanded", "false");
    if (listening) {
      doc.removeEventListener("pointerdown", onOutside, true);
      listening = false;
    }
    if (restoreFocus) trigger.focus();
    onOpenChange?.(false);
  }

  const toggle = (where) => (isOpen() ? close(true) : open(where));

  trigger.addEventListener("click", (e) => toggle(e.detail === 0 ? "first" : null));
  trigger.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      open("first");
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      open("last");
    }
  });

  return { open, close, toggle, isOpen };
}

/**
 * Chip with an optional remove button. `removeLabel` is the already formatted accessible name of the
 * remove button ("Remove {label}" from strings.js); the module has no text of its own.
 * Returns the chip element: a <span class="ui-chip"> holding the label and the remove <button>.
 */
export function createChip({ doc, label, removeLabel = null, onRemove = null, title = null }) {
  const chip = doc.createElement("span");
  chip.className = "ui-chip";
  const text = doc.createElement("span");
  text.className = "ui-chip-label";
  text.textContent = label;
  if (title) text.setAttribute("title", title);
  chip.appendChild(text);
  if (removeLabel !== null) {
    const button = doc.createElement("button");
    button.className = "ui-chip-remove";
    button.setAttribute("type", "button");
    button.setAttribute("aria-label", removeLabel);
    button.textContent = "×";
    if (onRemove) button.addEventListener("click", () => onRemove());
    chip.appendChild(button);
  }
  return chip;
}
