// The dividers between the sources panel, the records and the record detail (a drag, the arrow keys, a double click to
// go back to the default). The layout is css/responsive.css; this file only writes the widths into the variables it
// reads (--side-w, --detail-w, --detail-w-rail) and remembers them. A divider is shown by the CSS only where its column
// is a column of the layout (not as a drawer, not collapsed). Text comes from strings.js.
import { S } from "./strings.js";
import { LIMITS, bounds, clampWidth, keyWidth, loadWidths, saveWidths } from "./panel-resize-model.js";

function storageOf(win) {
  try {
    return win.localStorage || null;
  } catch {
    return null;
  }
}

// deps: {doc, win, storage?}. A missing element is skipped, never fatal.
export function createPanelResize({ doc = document, win = window, storage } = {}) {
  const panels = doc.getElementById("panels");
  const store = storage === undefined ? storageOf(win) : storage;
  const widths = loadWidths(store);
  const cleanups = [];
  const kinds = {
    side: { handle: doc.getElementById("side-resize"), panel: doc.getElementById("sources-panel"), label: S.resizeSources, other: "detail" },
    detail: { handle: doc.getElementById("detail-resize"), panel: doc.getElementById("detail-panel"), label: S.resizeDetail, other: "side" },
  };

  const widthOf = (kind) => {
    const panel = kinds[kind].panel;
    return panel && panel.offsetWidth ? panel.offsetWidth : 0;
  };
  const roomFor = (kind) => ({ total: panels ? panels.clientWidth : 0, other: widthOf(kinds[kind].other) });

  function paint(kind) {
    const { handle } = kinds[kind];
    if (!handle) {
      return;
    }
    const limit = bounds(kind, roomFor(kind));
    handle.setAttribute("aria-valuemin", String(limit.min));
    handle.setAttribute("aria-valuemax", String(limit.max));
    handle.setAttribute("aria-valuenow", String(widths[kind] !== undefined ? widths[kind] : LIMITS[kind].initial));
  }

  // px = null: back to the default (the CSS value).
  function apply(kind, px, { persist }) {
    if (!panels) {
      return;
    }
    const names = kind === "side" ? ["--side-w"] : ["--detail-w", "--detail-w-rail"];
    if (px === null) {
      delete widths[kind];
      names.forEach((name) => panels.style.removeProperty(name));
    } else {
      widths[kind] = px;
      names.forEach((name) => panels.style.setProperty(name, px + "px"));
    }
    paint(kind);
    if (persist) {
      saveWidths(store, widths);
    }
  }

  const on = (target, type, handler) => {
    if (target) {
      target.addEventListener(type, handler);
      cleanups.push(() => target.removeEventListener(type, handler));
    }
  };

  for (const kind of Object.keys(kinds)) {
    const { handle, label } = kinds[kind];
    if (!handle) {
      continue;
    }
    handle.setAttribute("role", "separator");
    handle.setAttribute("aria-orientation", "vertical");
    handle.setAttribute("aria-label", label);
    handle.title = label;
    handle.tabIndex = 0;
    if (widths[kind] !== undefined) {
      apply(kind, clampWidth(kind, widths[kind], roomFor(kind)), { persist: false });
    }
    paint(kind);

    on(handle, "pointerdown", (event) => {
      if (event.button !== undefined && event.button !== 0) {
        return;
      }
      event.preventDefault();
      const rect = panels.getBoundingClientRect();
      const room = roomFor(kind);
      const move = (e) => apply(kind, clampWidth(kind, kind === "side" ? e.clientX - rect.left : rect.right - e.clientX, room), { persist: false });
      const stop = () => {
        handle.classList.remove("dragging");
        handle.removeEventListener("pointermove", move);
        handle.removeEventListener("pointerup", stop);
        handle.removeEventListener("pointercancel", stop);
        saveWidths(store, widths);
      };
      handle.classList.add("dragging");
      if (typeof handle.setPointerCapture === "function") {
        handle.setPointerCapture(event.pointerId);
      }
      handle.addEventListener("pointermove", move);
      handle.addEventListener("pointerup", stop);
      handle.addEventListener("pointercancel", stop);
    });
    on(handle, "keydown", (event) => {
      const now = widthOf(kind) || LIMITS[kind].initial;
      const next = keyWidth(kind, widths[kind] !== undefined ? widths[kind] : now, event.key, roomFor(kind));
      if (next !== undefined) {
        event.preventDefault();
        apply(kind, next, { persist: true });
      }
    });
    on(handle, "dblclick", () => apply(kind, null, { persist: true }));
  }

  // A narrower window: a width that no longer leaves the records their room is held back (the remembered one stays).
  on(win, "resize", () => {
    for (const kind of Object.keys(kinds)) {
      if (widths[kind] !== undefined) {
        apply(kind, clampWidth(kind, widths[kind], roomFor(kind)), { persist: false });
      }
    }
  });

  return { destroy: () => cleanups.forEach((fn) => fn()), widths: () => ({ ...widths }) };
}
