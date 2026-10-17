// Pure rules of the keyboard layer (UI-28, no DOM): the remembered "single-key shortcuts" preference (WCAG 2.1.4), which
// keys the dispatcher may act on, the order of the F6 regions and where n/N start without a selected record.
// The DOM half (the one keydown listener, the regions, the shortcuts dialog) is shortcuts.js.
import { isSingleKey } from "./actions.js";

export { navAnchor } from "./filters.js";

export const SINGLE_KEYS_STORAGE_KEY = "dmslog.singleKeys";

// F6 order = DOM order = Tab order (docs/notas/ui-contratos.md, 5.1): the dock comes before the detail.
export const REGION_ORDER = Object.freeze(["bar", "side", "strip", "grid", "dock", "detail"]);

/** The stored value -> on/off. Anything that is not an explicit "off" is on (the default). */
export function parseSingleKeys(raw) {
  return !(raw === "off" || raw === "0" || raw === "false");
}

/** Reads the preference; storage that is missing or throws gives the default (on). */
export function loadSingleKeys(storage) {
  try {
    return parseSingleKeys(storage ? storage.getItem(SINGLE_KEYS_STORAGE_KEY) : null);
  } catch {
    return true;
  }
}

/** Remembers the preference. False when it could not be stored (private window, quota): it still holds for this page. */
export function saveSingleKeys(storage, on) {
  try {
    if (!storage) {
      return false;
    }
    storage.setItem(SINGLE_KEYS_STORAGE_KEY, on ? "on" : "off");
    return true;
  } catch {
    return false;
  }
}

/** F6 and Shift+F6 move between regions. */
export function isRegionKey(spec) {
  return spec === "F6" || spec === "Shift+F6";
}

/** Keys that still act while the focus is in a text field: the region keys and Ctrl combinations (never a typed character). */
export function allowedWhileTyping(spec) {
  return isRegionKey(spec) || (typeof spec === "string" && spec.startsWith("Ctrl+"));
}

/** An input method is composing (isComposing, or the legacy keyCode 229 some browsers send instead). */
export function isComposingEvent(event) {
  return Boolean(event) && (event.isComposing === true || event.keyCode === 229);
}

/** AltGr (Firefox on Linux reports it as AltGraph without Ctrl or Alt): the key typed a character, it is not a shortcut. */
export function hasAltGraph(event) {
  try {
    return Boolean(event) && typeof event.getModifierState === "function" && event.getModifierState("AltGraph") === true;
  } catch {
    return false;
  }
}

// The actions that may repeat while the key is held (walking records); everything else, bookmark toggles included, runs once.
export const REPEATABLE_ACTIONS = Object.freeze([
  "navigate.nextError", "navigate.prevError", "navigate.nextType", "navigate.prevType",
  "navigate.nextHighlight", "navigate.prevHighlight",
]);

export function allowsRepeat(id) {
  return REPEATABLE_ACTIONS.includes(id);
}

/**
 * Whether a key may run an action at all.
 *   spec            canonical KeySpec of the event (actions.js keySpec) or null
 *   singleKeysOn    the preference
 *   typing          the focus is in a text field, a select or a contenteditable
 *   inMenu          the focus is inside an open menu (it owns its keys; F6 still closes it)
 *   inDialog        the focus is in a dialog, or a modal dialog is open (it owns every key)
 *   defaultPrevented  a widget already handled the event
 *   composing / altGraph  an input method is composing / AltGr is down (isComposingEvent, hasAltGraph)
 */
export function shouldDispatch(spec, { singleKeysOn = true, typing = false, inMenu = false, inDialog = false, defaultPrevented = false, composing = false, altGraph = false } = {}) {
  if (typeof spec !== "string" || spec === "" || defaultPrevented || inDialog || composing || altGraph) {
    return false;
  }
  if (isRegionKey(spec)) {
    return true;
  }
  if (inMenu) {
    return false;
  }
  if (typing && !allowedWhileTyping(spec)) {
    return false;
  }
  if (isSingleKey(spec) && !singleKeysOn) {
    return false;
  }
  return true;
}

/**
 * The next (dir > 0) or previous (dir < 0) region that is available, wrapping around. `current` is the region the
 * focus is in, or null/unknown (the focus is on the page itself): forward then starts at the first region, backward at
 * the last. Returns null when no region is available.
 */
export function nextRegion(order, available, current, dir) {
  const ids = Array.isArray(order) ? order : [];
  const open = new Set(Array.isArray(available) ? available : []);
  const n = ids.length;
  if (n === 0 || open.size === 0) {
    return null;
  }
  const step = dir < 0 ? -1 : 1;
  const at = ids.indexOf(current);
  let i = at >= 0 ? at : step > 0 ? -1 : n;
  for (let tried = 0; tried < n; tried += 1) {
    i = (i + step + n) % n;
    if (open.has(ids[i])) {
      return ids[i];
    }
  }
  return null;
}
