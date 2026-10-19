// Pure rules of the two column dividers (sources | records | detail): limits, keys and the remembered widths. No DOM.
// The DOM layer is panel-resize.js.
export const STORAGE_KEY = "dmslog.panelWidths";
export const KEY_STEP_PX = 16;
export const GRID_MIN_PX = 360; // the records keep at least this much, whatever the panels ask for
export const LIMITS = {
  side: { min: 180, max: 480, initial: 248 },
  detail: { min: 280, max: 800, initial: 380 },
};

const clamp = (value, low, high) => Math.min(Math.max(value, low), high);

// total: width of the three-column area; other: width of the other column now (0 when it is not a column).
export function bounds(kind, { total, other }) {
  const { min, max } = LIMITS[kind];
  return { min, max: Math.max(min, Math.min(max, total - other - GRID_MIN_PX)) };
}

export function clampWidth(kind, px, room) {
  const { min, max } = bounds(kind, room);
  return Math.round(clamp(px, min, max));
}

// Arrow keys move the edge (ArrowRight moves it right: the sources column grows, the detail column shrinks); Home goes
// back to the default (null). Anything else is not a divider key (undefined).
export function keyWidth(kind, width, key, room) {
  if (key === "Home") {
    return null;
  }
  if (key !== "ArrowLeft" && key !== "ArrowRight") {
    return undefined;
  }
  const toRight = key === "ArrowRight";
  const step = (kind === "side") === toRight ? KEY_STEP_PX : -KEY_STEP_PX;
  return clampWidth(kind, width + step, room);
}

// {side?, detail?}: only whole numbers inside the limits survive; the rest is ignored (a stale or edited value).
export function normalizeWidths(value) {
  const out = {};
  for (const kind of Object.keys(LIMITS)) {
    const px = value && typeof value === "object" ? value[kind] : undefined;
    if (Number.isInteger(px) && px >= LIMITS[kind].min && px <= LIMITS[kind].max) {
      out[kind] = px;
    }
  }
  return out;
}

export function loadWidths(storage) {
  try {
    const raw = storage ? storage.getItem(STORAGE_KEY) : null;
    return normalizeWidths(raw ? JSON.parse(raw) : null);
  } catch {
    return {};
  }
}

// false when the widths could not be stored (private window, quota): the page works the same without.
export function saveWidths(storage, widths) {
  try {
    if (!storage) {
      return false;
    }
    storage.setItem(STORAGE_KEY, JSON.stringify(normalizeWidths(widths)));
    return true;
  } catch {
    return false;
  }
}
