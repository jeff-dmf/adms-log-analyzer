// Pure rules of the bottom dock (UI-15): heights and their limits, the divider keys, the remembered height and
// the tab list. No DOM. The DOM layer is dock.js; docs/notas/ui-contratos.md, section 2.3, is the contract.
import { ROW_HEIGHT } from "./grid-model.js";

export const DOCK_MIN_PX = 160;
export const GRID_MIN_ROWS = 8; // plus the grid header
export const DOCK_DEFAULT_FRACTION = 0.4;
export const DOCK_STEP_FRACTION = 0.05; // arrow keys on the divider
export const SMALL_WINDOW_PX = 640; // below this the dock opens maximized
export const STORAGE_KEY = "dmslog.dockHeight";
export const GRID_CHROME_PX = 20; // grid viewport border plus its horizontal scrollbar (as css/app.css reserves it)

const clamp = (value, low, high) => Math.min(Math.max(value, low), high);

// available: height shared by the grid (header + rows) and the dock. The grid keeps GRID_MIN_ROWS rows.
export function limits({ available, rowHeight = ROW_HEIGHT, gridHead }) {
  const max = Math.max(0, Math.floor(available - (gridHead + GRID_MIN_ROWS * rowHeight)));
  return { min: DOCK_MIN_PX, max: Math.max(max, DOCK_MIN_PX), maximizedOnly: max < DOCK_MIN_PX };
}

// A remembered fraction wins; without one the height follows the content (VR-09): clamp(160, content, 40%).
export function initialHeight({ storedFraction, contentHeight, available, limits: bounds }) {
  if (typeof storedFraction === "number") {
    return Math.round(clamp(storedFraction * available, bounds.min, bounds.max));
  }
  const wanted = clamp(contentHeight, DOCK_MIN_PX, Math.max(DOCK_MIN_PX, DOCK_DEFAULT_FRACTION * available));
  return Math.round(clamp(wanted, bounds.min, bounds.max));
}

// The divider: ArrowUp grows the dock, ArrowDown shrinks it (the divider sits on top), Home = smallest, End = largest.
export function keyStep(height, key, { available, limits: bounds }) {
  const step = DOCK_STEP_FRACTION * available;
  let next = height;
  if (key === "ArrowUp") {
    next = height + step;
  } else if (key === "ArrowDown") {
    next = height - step;
  } else if (key === "Home") {
    next = bounds.min;
  } else if (key === "End") {
    next = bounds.max;
  }
  return Math.round(clamp(next, bounds.min, bounds.max));
}

// Dragging the divider up (smaller clientY) makes the dock taller.
export function dragHeight({ startHeight, startY, y, limits: bounds }) {
  return Math.round(clamp(startHeight + (startY - y), bounds.min, bounds.max));
}

export function valueNow(height, available) {
  if (!(available > 0)) {
    return 0;
  }
  return Math.round(clamp((height / available) * 100, 0, 100));
}

export function fractionOf(height, available) {
  return available > 0 ? clamp(height / available, 0, 1) : DOCK_DEFAULT_FRACTION;
}

// The stored text is a fraction "0.42"; anything else (or out of 0.1..1) counts as nothing stored.
export function parseStored(text) {
  if (typeof text !== "string" || !/^\d*\.?\d+$/.test(text.trim())) {
    return null;
  }
  const value = Number(text);
  return value >= 0.1 && value <= 1 ? value : null;
}

export function formatStored(fraction) {
  return String(Math.round(fraction * 1000) / 1000);
}

export function shouldStartMaximized(windowHeight) {
  return windowHeight < SMALL_WINDOW_PX;
}

// Double click on the divider: maximized -> the default 40% (kept inside the limits); otherwise -> maximized.
export function doubleClickTarget({ maximized, available, limits: bounds }) {
  if (maximized) {
    return { maximized: false, height: Math.round(clamp(DOCK_DEFAULT_FRACTION * available, bounds.min, bounds.max)) };
  }
  return { maximized: true, height: null };
}

// Closing a tab activates its right neighbour, else the left one; closing another tab keeps the active one.
export function tabsAfterClose(tabs, active, id) {
  const at = tabs.indexOf(id);
  if (at < 0) {
    return { tabs: tabs.slice(), active };
  }
  const next = tabs.filter((tab) => tab !== id);
  if (active !== id) {
    return { tabs: next, active };
  }
  return { tabs: next, active: next.length === 0 ? null : (next[at] !== undefined ? next[at] : next[next.length - 1]) };
}

// Roving tabindex on the tab list: the tab that gets the focus after `key` (null: not a navigation key).
export function nextTab(tabs, current, key) {
  if (tabs.length === 0) {
    return null;
  }
  const at = Math.max(0, tabs.indexOf(current));
  if (key === "ArrowRight") {
    return tabs[(at + 1) % tabs.length];
  }
  if (key === "ArrowLeft") {
    return tabs[(at - 1 + tabs.length) % tabs.length];
  }
  if (key === "Home") {
    return tabs[0];
  }
  if (key === "End") {
    return tabs[tabs.length - 1];
  }
  return null;
}
