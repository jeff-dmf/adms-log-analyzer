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
  const widths = loadWidths(store); // what the user chose; the CSS gets it held back to what fits now (relayout)
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

  // Writes what the user chose into the CSS, held back to what fits now: the records keep their room whatever is
  // chosen or expanded, and the choice itself is not lost when the window gets narrow (it comes back with the room).
  function relayout() {
    if (!panels) {
      return;
    }
    for (const kind of Object.keys(kinds)) {
      const names = kind === "side" ? ["--side-w"] : ["--detail-w", "--detail-w-rail"];
      if (widths[kind] === undefined) {
        names.forEach((name) => panels.style.removeProperty(name));
      } else {
        const px = clampWidth(kind, widths[kind], roomFor(kind)) + "px";
        names.forEach((name) => panels.style.setProperty(name, px));
      }
      paint(kind);
    }
  }

  // px = null: back to the default (the CSS value).
  function apply(kind, px, { persist }) {
    if (px === null) {
      delete widths[kind];
    } else {
      widths[kind] = px;
    }
    relayout();
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

  // A narrower window, or a panel collapsed or expanded: the widths are fitted again (the chosen ones stay).
  relayout();
  on(win, "resize", relayout);
  const Observer = win.ResizeObserver || globalThis.ResizeObserver;
  if (typeof Observer === "function") {
    const observer = new Observer(relayout);
    [panels, kinds.side.panel, kinds.detail.panel].forEach((el) => el && observer.observe(el));
    cleanups.push(() => observer.disconnect());
  }

  return { destroy: () => cleanups.forEach((fn) => fn()), widths: () => ({ ...widths }) };
}
