// The keyboard layer (UI-28): the ONE keydown listener of the viewer (every shortcut comes from the action registry),
// F6 / Shift+F6 between the regions of the page, the skip link, the remembered "single-key shortcuts" preference
// (WCAG 2.1.4) and the shortcuts dialog (`?`). The rules are pure and live in shortcuts-model.js.
// It loads after the first view (import() in main.js, tried again after a failure); the page works without it.
// Texts come from strings.js and are written with textContent only; element names are ours, never log text.
import { isSingleKey, keySpec, shortcutLabel } from "./actions.js";
import { isInDialog, isInMenu, isTypingTarget } from "./filters.js";
import { createDialogShell, node } from "./panel-shell.js";
import {
  allowsRepeat, hasAltGraph, isComposingEvent, loadSingleKeys, nextRegion, saveSingleKeys, shouldDispatch,
} from "./shortcuts-model.js";
import { S } from "./strings.js";

// ---- DOM helpers ---------------------------------------------------------------------------------------------------

// Effectively displayed: connected, no [hidden] on the node or an ancestor, and not hidden by CSS (a closed drawer, a
// collapsed panel, body.no-case). The attribute walk alone serves where there is no layout engine.
export function isVisible(el) {
  if (!el || el.isConnected === false) {
    return false;
  }
  for (let n = el; n; n = n.parentElement || n.parent || null) {
    if (n.hidden === true) {
      return false;
    }
  }
  if (typeof el.checkVisibility === "function") {
    try {
      return el.checkVisibility({ visibilityProperty: true }) !== false;
    } catch {
      return true;
    }
  }
  return true;
}

const FOCUS_TAGS = new Set(["BUTTON", "INPUT", "SELECT", "TEXTAREA", "SUMMARY"]);

function isFocusable(el) {
  const tag = String(el.tagName || "").toUpperCase();
  const attr = (name) => (typeof el.getAttribute === "function" ? el.getAttribute(name) : null);
  const tabindex = attr("tabindex");
  if ((tabindex !== null && tabindex !== "" && Number(tabindex) < 0) || el.disabled === true) {
    return false;
  }
  if (tag === "INPUT" && String(attr("type") || el.type || "").toLowerCase() === "hidden") {
    return false;
  }
  const tabbable = FOCUS_TAGS.has(tag) || (tag === "A" && attr("href") !== null) || (tabindex !== null && tabindex !== "" && Number.isFinite(Number(tabindex)));
  return tabbable && isVisible(el);
}

// First control in Tab order inside `root` (document order; hidden subtrees are skipped), or null.
export function firstFocusable(root) {
  if (!root || root.hidden === true) {
    return null;
  }
  for (const child of Array.from(root.children || [])) {
    if (child.hidden === true) {
      continue;
    }
    if (isFocusable(child)) {
      return child;
    }
    const inner = firstFocusable(child);
    if (inner !== null) {
      return inner;
    }
  }
  return null;
}

function findNode(root, test) {
  if (!root) {
    return null;
  }
  for (const child of Array.from(root.children || [])) {
    if (test(child)) {
      return child;
    }
    const inner = findNode(child, test);
    if (inner !== null) {
      return inner;
    }
  }
  return null;
}

const within = (root, target) => Boolean(root && target && typeof root.contains === "function" && root.contains(target));
const hasClass = (el, name) => String(el.className || "").split(/\s+/).includes(name);

// ---- overlays ------------------------------------------------------------------------------------------------------

// What F6 closes before it moves: an open menu (role="menu"), the popovers of the strip, the help list of the status bar
// and the Columns popover. Each one closes on Escape and gives the focus back to its trigger.
const OVERLAY_CLASSES = ["columns-popover", "sb-help-pop", "fs-overflow"];
const OVERLAY_IDS = ["filter-form", "strip-overflow", "sb-help-pop"];

function isOverlay(el) {
  if (typeof el.getAttribute === "function" && el.getAttribute("role") === "menu") {
    return true;
  }
  return OVERLAY_IDS.includes(el.id) || OVERLAY_CLASSES.some((name) => hasClass(el, name));
}

function inOverlay(target) {
  for (let n = target; n; n = n.parentElement || n.parent || null) {
    if (isOverlay(n)) {
      return true;
    }
  }
  return false;
}

// The trigger of an open popover keeps the focus when it was opened with the mouse.
function opensOverlay(target) {
  if (!target || typeof target.getAttribute !== "function" || target.getAttribute("aria-expanded") !== "true") {
    return false;
  }
  return OVERLAY_IDS.includes(target.getAttribute("aria-controls"));
}

function escapeEvent(doc) {
  const view = doc.defaultView;
  if (view && typeof view.KeyboardEvent === "function") {
    return new view.KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true });
  }
  return { type: "keydown", key: "Escape" };
}

// Escape on the element that has the focus, until it is no longer inside an overlay (menus nest: a sub menu first).
function dismissOverlays(doc) {
  for (let i = 0; i < 8; i += 1) {
    const active = doc.activeElement;
    if (!active || !(inOverlay(active) || opensOverlay(active))) {
      return;
    }
    active.dispatchEvent(escapeEvent(doc));
    if (doc.activeElement === active && (inOverlay(active) || opensOverlay(active))) {
      return; // nothing handled it: do not spin
    }
  }
}

// ---- regions -------------------------------------------------------------------------------------------------------

/**
 * The regions F6 walks (docs/notas/ui-contratos.md, 5.1), in DOM and Tab order. Options:
 *   doc, hasCase() (no case: only the bar and the empty state), drawers() (the UI-17 drawers or null: a closed drawer is
 *   not a region even where the DOM cannot tell).
 * Returns { ids, available(), current(), regionOf(node), focus(id), restore(node, region), move(dir), destroy() }.
 */
export function createRegions({ doc = document, hasCase = () => true, drawers = () => null } = {}) {
  const byId = (id) => (typeof doc.getElementById === "function" ? doc.getElementById(id) : null);
  const remembered = new Map(); // region id -> the last element that had the focus in it
  const caseOpen = () => {
    try {
      return Boolean(hasCase());
    } catch {
      return false;
    }
  };
  const drawerShows = (which) => {
    const d = drawers();
    return !d || typeof d.isOpen !== "function" || d.isOpen(which) !== false;
  };
  // A direct child of the grid panel (case-empty.js); looked at on every focus change, so no walk of the whole panel.
  const emptyState = () => {
    const panel = byId("grid-panel");
    return panel ? Array.from(panel.children || []).find((el) => hasClass(el, "case-empty")) || null : null;
  };
  const sourcesShown = () => isVisible(byId("sources-panel")) && drawerShows("side");
  const detailShown = () => isVisible(byId("detail-panel")) && drawerShows("detail");
  const gridShown = () => isVisible(byId("grid-viewport"));
  const dockTab = () => {
    const dock = byId("dock");
    return findNode(dock, (el) => typeof el.getAttribute === "function" && el.getAttribute("role") === "tab" && el.getAttribute("aria-selected") === "true" && isVisible(el)) ||
      firstFocusable(dock);
  };
  const detailTitle = () => {
    const title = byId("detail-title");
    if (title && typeof title.setAttribute === "function" && title.getAttribute("tabindex") === null) {
      title.setAttribute("tabindex", "-1"); // a heading the F6 can land on
    }
    return title;
  };

  const defs = [
    { id: "bar", roots: () => [byId("toolbar")], available: () => isVisible(byId("toolbar")), target: () => byId("case-button") },
    {
      id: "side",
      roots: () => [byId("sources-panel"), byId("side-rail")],
      available: () => caseOpen() && (sourcesShown() || isVisible(byId("side-rail"))),
      // The panel itself when it is shown (its first control); the rail button where the panel is a closed drawer.
      target: () => (sourcesShown() ? firstFocusable(byId("sources-panel")) || byId("sources-panel") : byId("side-rail")),
    },
    {
      id: "strip",
      roots: () => [byId("filter-strip")],
      available: () => caseOpen() && isVisible(byId("filter-strip")),
      target: () => firstFocusable(byId("filter-strip")),
    },
    {
      id: "grid",
      roots: () => [byId("grid-viewport"), emptyState()],
      // Without a case the grid is hidden and the empty state (New case…, Open a path…) is its content.
      available: () => (caseOpen() && gridShown()) || isVisible(emptyState()),
      target: () => (caseOpen() && gridShown() ? byId("grid-viewport") : firstFocusable(emptyState())),
    },
    {
      id: "dock",
      roots: () => [byId("dock")],
      available: () => caseOpen() && isVisible(byId("dock")),
      target: dockTab,
    },
    {
      id: "detail",
      roots: () => [byId("detail-panel")],
      available: () => caseOpen() && detailShown(),
      target: detailTitle,
    },
  ];
  const ids = defs.map((d) => d.id);
  const defOf = (id) => defs.find((d) => d.id === id);

  const safe = (fn, fallback) => {
    try {
      return fn();
    } catch {
      return fallback;
    }
  };
  const isAvailable = (def) => safe(() => Boolean(def.available()), false);
  const rootsOf = (def) => safe(() => def.roots().filter(Boolean), []);
  const regionOf = (target) => {
    const hit = defs.find((def) => rootsOf(def).some((root) => within(root, target)));
    return hit ? hit.id : null;
  };
  const available = () => defs.filter(isAvailable).map((d) => d.id);

  // Document position for a focus that is in no region (the timeline, the "new records" button): the last region
  // that starts before it.
  const precedingRegion = (target) => {
    let found = null;
    for (const def of defs) {
      const root = rootsOf(def)[0];
      if (root && target && typeof root.compareDocumentPosition === "function" && (root.compareDocumentPosition(target) & 4) !== 0) {
        found = def.id;
      }
    }
    return found;
  };

  const onFocusIn = (event) => {
    const id = regionOf(event && event.target);
    if (id !== null) {
      remembered.set(id, event.target);
    }
  };
  if (typeof doc.addEventListener === "function") {
    doc.addEventListener("focusin", onFocusIn);
  }

  const current = () => regionOf(doc.activeElement);

  // A control the focus may go to: connected, displayed, and not disabled.
  const usable = (el) => Boolean(el) && el.isConnected !== false && isVisible(el) && el.disabled !== true &&
    !(typeof el.getAttribute === "function" && el.getAttribute("aria-disabled") === "true");
  // Focuses `el` and checks that the focus really moved there (the browser refuses what it does not display).
  function land(el) {
    if (!usable(el) || typeof el.focus !== "function") {
      return false;
    }
    el.focus();
    return doc.activeElement === el;
  }

  // Lands in region `id`: the element that had the focus in it last time if it is still usable (displayed and enabled:
  // a Back button that became disabled is not a place to stop), else the region's own target. False when nothing takes it.
  function focus(id) {
    const def = defOf(id);
    if (!def) {
      return false;
    }
    const last = remembered.get(id);
    if (last && rootsOf(def).some((root) => within(root, last)) && land(last)) {
      return true;
    }
    return land(safe(() => def.target(), null));
  }

  // Gives the focus back to `el` (a dialog's opener); if that is gone, hidden or disabled, to the region it was in
  // (`region`, read while it was still there), else to the grid, else to the first region that takes it.
  function restore(el, region = null) {
    if (land(el)) {
      return true;
    }
    const order = [region, "grid", ...ids].filter((id, i, all) => id && all.indexOf(id) === i);
    return order.some((id) => focus(id));
  }

  // dir > 0 next region, dir < 0 previous. A region that cannot take the focus is skipped. Returns the id it landed in.
  function move(dir) {
    dismissOverlays(doc);
    const open = available();
    const inside = current();
    let from = inside;
    let toward = dir;
    if (inside === null) {
      const before = precedingRegion(doc.activeElement);
      if (before !== null && dir < 0) {
        // Between two regions, backward goes to the one just before the focus, not the one before that.
        from = ids[ids.indexOf(before) + 1] === undefined ? null : ids[ids.indexOf(before) + 1];
        toward = -1;
      } else {
        from = before;
      }
    }
    let rest = open.slice();
    while (rest.length > 0) {
      const to = nextRegion(ids, rest, from, toward);
      if (to === null) {
        return null;
      }
      if (focus(to)) {
        return to;
      }
      rest = rest.filter((id) => id !== to);
    }
    return null;
  }

  return {
    ids,
    available,
    current,
    regionOf,
    focus,
    restore,
    move,
    destroy() {
      if (typeof doc.removeEventListener === "function") {
        doc.removeEventListener("focusin", onFocusIn);
      }
    },
  };
}

// ---- the keyboard layer --------------------------------------------------------------------------------------------

/**
 * Options: actions (the registry), ctx (AppContext), getCase(), drawers() (UI-17 drawers or null), storage (localStorage
 * or null), doc.
 * Returns { singleKeys(), setSingleKeys(on), openDialog(opener), closeDialog(), dialogOpen(), regions, on(type, fn),
 * destroy() }. "change" fires with the new value of the single-key preference.
 */
export function createShortcuts({ actions, ctx, getCase = () => null, drawers = () => null, storage = null, doc = document } = {}) {
  const byId = (id) => (typeof doc.getElementById === "function" ? doc.getElementById(id) : null);
  let singleKeysOn = loadSingleKeys(storage);
  const listeners = new Set();
  const regions = createRegions({ doc, hasCase: () => getCase() !== null, drawers });

  const dialogIsOpen = () => Array.from(typeof doc.querySelectorAll === "function" ? doc.querySelectorAll("dialog") : [])
    .some((d) => d.open === true || (typeof d.getAttribute === "function" && d.getAttribute("open") !== null));

  // The one dispatcher. An action runs only if its shortcut matches, the rules of shortcuts-model.js allow the key where
  // the focus is, and the action is enabled (a disabled one leaves the key to the browser).
  function onKeydown(event) {
    const spec = keySpec(event);
    const target = event.target;
    const typing = isTypingTarget(target);
    const allowed = shouldDispatch(spec, {
      singleKeysOn,
      typing,
      inMenu: isInMenu(target),
      inDialog: isInDialog(target) || dialogIsOpen(),
      defaultPrevented: Boolean(event.defaultPrevented),
      composing: isComposingEvent(event),
      altGraph: hasAltGraph(event),
    });
    if (!allowed) {
      return;
    }
    const id = actions.match(event, { typing, inGrid: within(byId("grid-viewport"), target) });
    if (id === null || !actions.state(id, ctx).enabled) {
      return;
    }
    event.preventDefault();
    if (event.repeat === true && !allowsRepeat(id)) {
      return; // a held key does not toggle again (taken from the browser all the same)
    }
    actions.invoke(id, ctx); // never rejects: failures go to the registry's onError
  }
  doc.addEventListener("keydown", onKeydown);

  // The skip link: the grid, or the empty state without a case. Without this module the link is a plain #grid-viewport anchor.
  const skip = byId("skip-link");
  const onSkip = (event) => {
    const viewport = byId("grid-viewport");
    const dock = ctx && ctx.dock;
    // The maximized dock hides the grid: restore it first so the link has somewhere to go.
    if (getCase() !== null && !isVisible(viewport) && dock && typeof dock.maximize === "function") {
      dock.maximize(false);
    }
    // Still hidden (no room to restore): the next best place is the dock itself, which is displayed.
    if (regions.focus("grid") || (getCase() !== null && regions.focus("dock"))) {
      event.preventDefault();
    }
  };
  if (skip) {
    skip.addEventListener("click", onSkip);
  }

  // ---- the dialog
  let shell = null;
  let box = null;
  let state = null;
  let tbody = null;

  function row(keyText, actionText, className) {
    const tr = node(doc, "tr", className || "");
    const keys = node(doc, "td", "sc-key");
    keys.appendChild(node(doc, "kbd", "", keyText));
    const action = node(doc, "td", "sc-action", actionText);
    tr.append(keys, action);
    return { tr, action };
  }

  function paintRows() {
    tbody.textContent = "";
    for (const def of actions.list({ withShortcut: true })) {
      const now = actions.state(def.id, ctx);
      const off = isSingleKey(def.shortcut) && !singleKeysOn;
      const { tr, action } = row(shortcutLabel(def.shortcut, S.keyNames), S[def.labelKey], (off ? "sc-off " : "") + (now.enabled ? "" : "sc-unavailable"));
      tr.dataset.action = def.id;
      if (off) {
        action.append(" ", node(doc, "span", "sc-note", S.scRowOff));
      } else if (!now.enabled && now.reasonKey && S[now.reasonKey]) {
        tr.title = S[now.reasonKey];
      }
      tbody.appendChild(tr);
    }
  }

  function paint() {
    box.checked = singleKeysOn;
    state.textContent = singleKeysOn ? S.scOn : S.scOff;
    paintRows();
  }

  function build() {
    shell = createDialogShell(doc, { title: S.scTitle, className: "shortcuts-dialog" });
    const toggle = node(doc, "label", "sc-toggle");
    box = node(doc, "input");
    box.type = "checkbox";
    box.id = "sc-single-keys";
    state = node(doc, "span", "sc-state");
    state.setAttribute("aria-hidden", "true");
    toggle.append(box, node(doc, "span", "sc-toggle-label", S.scSingleKeys), state);
    const hint = node(doc, "p", "sc-hint", S.scSingleKeysHint);
    hint.id = "sc-single-keys-hint";
    box.setAttribute("aria-describedby", hint.id);
    box.addEventListener("change", () => setSingleKeys(box.checked));

    const table = node(doc, "table", "sc-table");
    table.setAttribute("aria-label", S.scTitle);
    const headRow = node(doc, "tr");
    for (const text of [S.scColKey, S.scColAction]) {
      const th = node(doc, "th", "", text);
      th.setAttribute("scope", "col");
      headRow.appendChild(th);
    }
    const thead = node(doc, "thead");
    thead.appendChild(headRow);
    tbody = node(doc, "tbody");
    table.append(thead, tbody);

    const grid = node(doc, "table", "sc-table sc-table-grid");
    grid.appendChild(node(doc, "caption", "sc-caption", S.scGridTitle));
    const gridBody = node(doc, "tbody");
    for (const [keys, text] of [[S.scGridMoveKeys, S.scGridMove], [S.scGridEdgeKeys, S.scGridEdge], [S.scGridEnterKeys, S.scGridEnter], [S.scEscKeys, S.scEsc]]) {
      gridBody.appendChild(row(keys, text).tr);
    }
    grid.appendChild(gridBody);

    const timeline = node(doc, "p", "sc-hint sc-timeline");
    timeline.append(node(doc, "strong", "", S.scTimelineTitle), " ", S.timelineHint);
    shell.body.append(toggle, hint, table, grid, timeline);
    doc.body.appendChild(shell.dialog);
  }

  function setSingleKeys(on) {
    const next = Boolean(on);
    if (next === singleKeysOn) {
      return;
    }
    singleKeysOn = next;
    saveSingleKeys(storage, next);
    if (shell !== null) {
      paint();
    }
    for (const fn of Array.from(listeners)) {
      try {
        fn(next);
      } catch {
        // a listener's failure never breaks the keyboard
      }
    }
  }

  // opener: the control the user used. Read it before anything moves the focus (a menu closing gives it back first).
  function openDialog(opener) {
    const from = opener || doc.activeElement || null;
    if (shell === null) {
      build();
    }
    if (shell.isOpen()) {
      return;
    }
    paint();
    const region = regions.regionOf(from);
    // The shell gives the focus back through this: the opener if it is still usable (the window may have shrunk and
    // hidden it, or it may be gone), else the region it was in or the grid.
    shell.open({ focus: () => regions.restore(from, region) });
  }

  return {
    singleKeys: () => singleKeysOn,
    setSingleKeys,
    openDialog,
    closeDialog: () => (shell === null ? undefined : shell.close()),
    dialogOpen: () => shell !== null && shell.isOpen(),
    regions,
    on(type, fn) {
      if (type !== "change" || typeof fn !== "function") {
        return () => {};
      }
      listeners.add(fn);
      return () => listeners.delete(fn);
    },
    destroy() {
      doc.removeEventListener("keydown", onKeydown);
      if (skip) {
        skip.removeEventListener("click", onSkip);
      }
      regions.destroy();
      listeners.clear();
      if (shell !== null) {
        shell.close();
        shell.dialog.remove();
        shell = null;
      }
    },
  };
}
