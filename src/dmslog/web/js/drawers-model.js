// Pure model of the side rail and the drawers (UI-17, no DOM): which layout the width gives, what the user's own
// collapse choices add, which drawer is open, and the remembered choices. The DOM layer lives in drawers.js.
import { RAIL_MIN_WIDTH, WIDE_MIN_WIDTH, layoutModeFor } from "./column-resize-model.js";

export const STORAGE_KEY = "dmslog.layout";
// The same breakpoints as css/responsive.css (a test keeps the two in step): true = the sources panel is a rail.
export const SIDE_RAIL_QUERY = `(width < ${WIDE_MIN_WIDTH}px)`;
export const DETAIL_DRAWER_QUERY = `(width < ${RAIL_MIN_WIDTH}px)`;

export { layoutModeFor };

// What is on screen for a layout mode and the user's choices.
//   sideRail: the sources panel is the 44 px rail (forced below 1600 px, or collapsed by the user)
//   detailDrawer: the detail is a drawer (forced below 1280 px, or collapsed by the user)
//   canCollapseSide / canCollapseDetail: the user's choice has an effect (wide layout); below that the layout forces it
export function effectiveLayout(mode, prefs) {
  const p = normalizePrefs(prefs);
  const wide = mode === "wide";
  const narrow = mode === "narrow";
  return {
    mode,
    sideRail: !wide || p.sideCollapsed,
    detailDrawer: narrow || p.detailCollapsed,
    canCollapseSide: wide,
    canCollapseDetail: mode !== "narrow",
  };
}

// "3/14" for the rail; not a finite number of sources = "" (the rail shows only its name). The selection is clamped
// to the total.
export function railCount(selected, total) {
  if (!Number.isFinite(total) || total < 0) {
    return "";
  }
  const sel = Number.isFinite(selected) ? Math.min(Math.max(0, Math.trunc(selected)), Math.trunc(total)) : 0;
  return `${sel}/${Math.trunc(total)}`;
}

export function normalizePrefs(value) {
  const v = value && typeof value === "object" ? value : {};
  return { sideCollapsed: v.sideCollapsed === true, detailCollapsed: v.detailCollapsed === true };
}

// Reads the remembered choices; storage that is missing, throws or holds anything else gives the defaults.
export function loadPrefs(storage) {
  try {
    const raw = storage ? storage.getItem(STORAGE_KEY) : null;
    return normalizePrefs(raw ? JSON.parse(raw) : null);
  } catch {
    return normalizePrefs(null);
  }
}

// Returns false when the choices could not be stored (private window, quota): the page works the same without.
export function savePrefs(storage, prefs) {
  try {
    if (!storage) {
      return false;
    }
    storage.setItem(STORAGE_KEY, JSON.stringify(normalizePrefs(prefs)));
    return true;
  } catch {
    return false;
  }
}

// Which drawer is open: at most one, and only while the layout makes that panel a drawer.
export class DrawerState {
  constructor() {
    this.open = null; // "side" | "detail" | null
  }

  // Opens `which` if the layout makes it a drawer; the other one closes. Returns whether it is open afterwards.
  show(which, layout) {
    const allowed = which === "side" ? layout.sideRail : which === "detail" ? layout.detailDrawer : false;
    if (!allowed) {
      return false;
    }
    this.open = which;
    return true;
  }

  hide(which = null) {
    if (which === null || this.open === which) {
      this.open = null;
    }
  }

  toggle(which, layout) {
    if (this.open === which) {
      this.hide(which);
      return false;
    }
    return this.show(which, layout);
  }

  // The layout changed: a drawer whose panel is back in the flow closes. Returns the one that closed, or null.
  sync(layout) {
    if ((this.open === "side" && !layout.sideRail) || (this.open === "detail" && !layout.detailDrawer)) {
      const closed = this.open;
      this.open = null;
      return closed;
    }
    return null;
  }
}
