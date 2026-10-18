// Action registry (UI-11 contract, section 1): one place that knows every action of the viewer, its group,
// shortcut, availability and loader. Pure: no DOM, no timers. The menus, the toolbar toggles and (later) the
// shortcut dialog, the context menu and the palette are all built from it.
// Labels are keys of strings.js (S), resolved here only to validate them and to build menu items.
import { S as defaultStrings } from "./strings.js";

export const GROUPS = ["case", "toggle", "analyze", "export", "view", "views", "navigate", "record", "help"];

const MODIFIERS = ["Ctrl", "Alt", "Shift"];
const KEY_SPEC = /^(Ctrl\+)?(Alt\+)?(Shift\+)?(.+)$/;

/** KeyboardEvent-like -> canonical KeySpec ("Ctrl+Alt+Shift+" order; a single printable key never carries Shift). */
export function keySpec(event) {
  if (!event || typeof event.key !== "string" || event.key === "" || event.metaKey) {
    return null;
  }
  let key = event.key;
  // With Ctrl or Alt the case of a letter is the Shift key, not Caps Lock: "Ctrl+k" also arrives as "K" with Caps Lock on.
  if (key.length === 1 && (event.ctrlKey || event.altKey) && key.toLowerCase() !== key.toUpperCase()) {
    key = event.shiftKey ? key.toUpperCase() : key.toLowerCase();
  }
  if (key === "Control" || key === "Alt" || key === "Shift" || key === "Meta") {
    return null; // a modifier on its own is not a shortcut
  }
  let prefix = "";
  if (event.ctrlKey) {
    prefix += "Ctrl+";
  }
  if (event.altKey) {
    prefix += "Alt+";
  }
  if (event.shiftKey && key.length !== 1) {
    prefix += "Shift+";
  }
  return prefix + key;
}

/** True when `spec` is a well-formed KeySpec. */
export function isKeySpec(spec) {
  if (typeof spec !== "string") {
    return false;
  }
  const m = KEY_SPEC.exec(spec);
  if (!m) {
    return false;
  }
  const key = m[4];
  if (MODIFIERS.includes(key) || key === "Meta" || spec.includes("Meta")) {
    return false;
  }
  if (key.length === 1 && m[3]) {
    return false; // "Shift+b" must be written "B"
  }
  return true;
}

/** A one-letter shortcut: no Ctrl or Alt and a one-character key. */
export function isSingleKey(spec) {
  const m = KEY_SPEC.exec(spec || "");
  return Boolean(m) && !m[1] && !m[2] && m[4].length === 1;
}

/** "Alt+ArrowLeft" -> "Alt+Left" (names from S.keyNames). */
export function shortcutLabel(spec, keyNames = {}) {
  const m = KEY_SPEC.exec(spec || "");
  if (!m) {
    return "";
  }
  const key = Object.prototype.hasOwnProperty.call(keyNames, m[4]) ? keyNames[m[4]] : m[4];
  return (m[1] || "") + (m[2] || "") + (m[3] || "") + key;
}

function text(strings, key) {
  return typeof key === "string" && Object.prototype.hasOwnProperty.call(strings, key) ? strings[key] : undefined;
}

/**
 * onError(err, id): reports a failed run() or load(). singleKeysOn(): the "one-letter shortcuts" preference.
 * strings: the catalogue the keys are checked against (tests pass a small one).
 */
export function createActionRegistry({ onError = () => {}, singleKeysOn = () => true, strings = defaultStrings } = {}) {
  const defs = new Map();
  const scopes = new Map(); // `${scope}\u0000${shortcut}` -> id
  const loads = new Map(); // id -> { promise, loaded, failed }
  const listeners = new Set();

  const emit = () => {
    for (const fn of Array.from(listeners)) {
      try {
        fn();
      } catch (err) {
        // A broken listener never breaks the registry.
      }
    }
  };
  const report = (err, id) => {
    try {
      onError(err, id);
    } catch (inner) {
      // Same: reporting must not throw.
    }
  };

  function register(def) {
    if (!def || typeof def.id !== "string" || def.id === "") {
      throw new TypeError("action needs an id");
    }
    if (defs.has(def.id)) {
      throw new TypeError("duplicate action id: " + def.id);
    }
    if (!GROUPS.includes(def.group)) {
      throw new TypeError("unknown action group: " + def.group + " (" + def.id + ")");
    }
    if (text(strings, def.labelKey) === undefined || text(strings, def.labelKey) === "") {
      throw new TypeError("labelKey is not in strings.js: " + def.labelKey + " (" + def.id + ")");
    }
    if (def.disabledKey !== undefined && !text(strings, def.disabledKey)) {
      throw new TypeError("disabledKey is not in strings.js: " + def.disabledKey + " (" + def.id + ")");
    }
    if (typeof def.run !== "function") {
      throw new TypeError("action needs run(): " + def.id);
    }
    const scope = def.scope === "grid" ? "grid" : "global";
    const shortcut = def.shortcut || null;
    if (shortcut !== null) {
      if (!isKeySpec(shortcut)) {
        throw new TypeError("invalid shortcut " + JSON.stringify(shortcut) + " (" + def.id + ")");
      }
      const slot = scope + "\u0000" + shortcut;
      if (scopes.has(slot)) {
        throw new TypeError("shortcut " + shortcut + " of " + def.id + " is already used by " + scopes.get(slot));
      }
      scopes.set(slot, def.id);
    }
    defs.set(def.id, { ...def, scope, shortcut });
    emit();
  }

  const get = (id) => defs.get(id);

  function list({ group, withShortcut } = {}) {
    return Array.from(defs.values()).filter((d) =>
      (group === undefined || d.group === group) && (!withShortcut || d.shortcut !== null));
  }

  function guard(fn, ctx, fallback) {
    try {
      return fn(ctx);
    } catch (err) {
      return fallback;
    }
  }

  function state(id, ctx) {
    const def = defs.get(id);
    if (!def) {
      return { enabled: false, pressed: null, reasonKey: "actionUnavailable", loaded: false, failed: false };
    }
    const load = loads.get(id);
    const failed = Boolean(load && load.failed);
    const loaded = !def.load || Boolean(load && load.loaded);
    const pressed = def.pressed ? Boolean(guard(def.pressed, ctx, false)) : null;
    if (failed) {
      return { enabled: false, pressed, reasonKey: "actionUnavailable", loaded, failed };
    }
    const allowed = def.enabled ? Boolean(guard(def.enabled, ctx, false)) : guard((c) => c.caseId() !== null, ctx, false);
    return { enabled: allowed, pressed, reasonKey: allowed ? null : def.disabledKey || "needCase", loaded, failed };
  }

  /** Calls load() once (memoized). A failure marks the action as failed; it never rejects. */
  function preload(id) {
    const def = defs.get(id);
    if (!def || !def.load) {
      return Promise.resolve();
    }
    const known = loads.get(id);
    if (known) {
      return known.promise;
    }
    const entry = { loaded: false, failed: false, promise: null };
    loads.set(id, entry);
    entry.promise = Promise.resolve()
      .then(() => def.load())
      .then(
        () => {
          entry.loaded = true;
        },
        (err) => {
          entry.failed = true;
          report(err, id);
          emit();
        },
      );
    return entry.promise;
  }

  /**
   * Runs an action. `isCurrent` (optional) is asked synchronously after the LAST wait (the loader) and right before run():
   * false gives { ok: false, reason: "stale" } and run() is not called, so nothing can change in between.
   */
  async function invoke(id, ctx, args, isCurrent) {
    const def = defs.get(id);
    if (!def) {
      return { ok: false, reason: "error" };
    }
    await preload(id);
    const now = state(id, ctx);
    if (now.failed) {
      return { ok: false, reason: "loadFailed" };
    }
    if (!now.enabled) {
      return { ok: false, reason: "disabled" };
    }
    if (typeof isCurrent === "function" && !guard(isCurrent, undefined, false)) {
      return { ok: false, reason: "stale" };
    }
    try {
      await def.run(ctx, args);
      return { ok: true };
    } catch (err) {
      report(err, id);
      return { ok: false, reason: "error" };
    }
  }

  /** Id of the action whose shortcut the event matches, or null. */
  function match(event, { typing = false, inGrid = false } = {}) {
    const spec = keySpec(event);
    if (spec === null) {
      return null;
    }
    const scopesToTry = inGrid ? ["grid", "global"] : ["global"];
    for (const scope of scopesToTry) {
      const id = scopes.get(scope + "\u0000" + spec);
      if (id === undefined) {
        continue;
      }
      if (isSingleKey(spec) && (typing || !singleKeysOn())) {
        return null;
      }
      return id;
    }
    return null;
  }

  function onChange(fn) {
    listeners.add(fn);
    return () => listeners.delete(fn);
  }

  return { register, get, list, state, preload, invoke, match, onChange };
}

/** The `.ui-menu` item of one child (a theme choice, a case, ...). */
function childItem(def, child, index, ctx, registry, isCurrent) {
  const item = {
    id: def.id + ":" + index,
    label: String(child.label),
    run: () => registry.invoke(def.id, ctx, child.args, isCurrent),
  };
  if (child.checked !== undefined) {
    item.role = "menuitemradio";
    item.checked = Boolean(child.checked);
  }
  return item;
}

/**
 * Bridge to ui-menu.js: MenuItem[] for `defs` (ActionDefs, or {separator: true}). enabled/pressed are computed
 * now, when the menu opens. A disabled item stays in the menu with the reason as its title. `isCurrent` (optional) is handed
 * to invoke() so an item never runs once its target is gone.
 */
export function toMenuItems(registry, defs, ctx, strings = defaultStrings, isCurrent = undefined) {
  const items = [];
  for (const def of defs) {
    if (def.separator) {
      items.push({ separator: true, id: "" });
      continue;
    }
    const now = registry.state(def.id, ctx);
    const item = {
      id: def.id,
      label: strings[def.labelKey],
      shortcut: def.shortcut ? shortcutLabel(def.shortcut, strings.keyNames || {}) : null,
      disabled: !now.enabled,
      title: now.enabled ? null : strings[now.reasonKey] || null,
      run: () => registry.invoke(def.id, ctx, undefined, isCurrent),
      preload: () => registry.preload(def.id),
    };
    if (now.pressed !== null) {
      item.pressed = now.pressed;
    }
    if (def.children && now.enabled) {
      let children = [];
      try {
        children = def.children(ctx);
      } catch (err) {
        children = [];
      }
      item.children = children.map((child, i) => childItem(def, child, i, ctx, registry, isCurrent));
      item.run = undefined;
      if (children.length === 0) {
        item.disabled = true; // a submenu with nothing in it is not offered
        item.title = (def.emptyKey && strings[def.emptyKey]) || null;
      }
    }
    items.push(item);
  }
  return items;
}
