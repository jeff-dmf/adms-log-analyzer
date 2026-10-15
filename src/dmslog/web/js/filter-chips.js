// Filter strip (UI-18): the chips of the active view's filter, the "Filtered" / "No filters" state, "+N more" for
// the chips that do not fit and the "Filters" popover (the filter form). The rules are in filter-chips-model.js;
// this file only draws and measures. Everything written to the page is text (chip labels can hold log text).
import { chipsFromState, fitChips, isFiltered } from "./filter-chips-model.js";
import { S, format } from "./strings.js";
import { createChip } from "./ui-menu.js";

const GAP = 4; // px between chips (the same value as .fs-chips in filter-strip.css)

function within(root, node) {
  for (let n = node; n; n = n.parentNode || n.parent || null) {
    if (n === root) {
      return true;
    }
  }
  return false;
}

// options: strip (the whole strip), chipsHost, stateLabel, filtersButton, popover (the filter form),
//   group (optional, popover-group.js: one popover open at a time with the "..." menu),
//   getState() -> {live, selection, parentsFilter}, lookups (sourceName, collectionTag, typeLabel, formatTs),
//   onRemove(chipId), onOpenTree(), doc (default: document).
// Returns {refresh(), relayout(), closePopovers()}.
export function mountFilterChips(options) {
  const { strip, chipsHost, stateLabel, filtersButton, popover, getState, lookups, onRemove, onOpenTree } = options;
  const doc = options.doc || document;
  const member = options.group ? options.group.join("strip", () => closePopovers(false)) : null;

  const moreButton = doc.createElement("button");
  moreButton.type = "button";
  moreButton.className = "ui-chip fs-more";
  moreButton.setAttribute("aria-controls", "strip-overflow");
  moreButton.setAttribute("aria-expanded", "false");
  moreButton.hidden = true;
  const overflow = doc.createElement("div");
  overflow.className = "fs-overflow";
  overflow.id = "strip-overflow";
  overflow.setAttribute("role", "group");
  overflow.setAttribute("aria-label", S.stripOverflowLabel);
  overflow.hidden = true;
  strip.appendChild(overflow);

  filtersButton.textContent = S.stripFilters;
  filtersButton.setAttribute("aria-expanded", "false");
  filtersButton.title = S.stripFiltersTitle;
  popover.hidden = true;

  let chips = [];
  let signature = null; // "id|label" of the chips drawn: the DOM is rebuilt, and measured, only when it changes
  let nodes = [];
  let widths = [];
  let moreWidth = 0;
  let lastAvailable = null;

  // ---- popovers (the filter form and the overflow list): one open at a time, Escape and outside click close ----
  let open = null; // {el, button}
  const onPointerDown = (event) => {
    if (open && !within(open.el, event.target) && !within(open.button, event.target)) {
      closePopovers(false);
    }
  };
  function closePopovers(returnFocus) {
    if (!open) {
      return;
    }
    const { el, button } = open;
    open = null;
    el.hidden = true;
    button.setAttribute("aria-expanded", "false");
    doc.removeEventListener("pointerdown", onPointerDown);
    if (member) {
      member.closed();
    }
    if (returnFocus) {
      button.focus();
    }
  }
  function openPopover(el, button) {
    closePopovers(false);
    el.hidden = false;
    button.setAttribute("aria-expanded", "true");
    open = { el, button };
    doc.addEventListener("pointerdown", onPointerDown);
    if (member) {
      member.opened(); // closes the "..." menu if it is open
    }
  }
  function togglePopover(el, button) {
    if (open && open.el === el) {
      closePopovers(true);
    } else {
      openPopover(el, button);
    }
  }
  for (const [el, button] of [[popover, filtersButton], [overflow, moreButton]]) {
    button.addEventListener("click", () => togglePopover(el, button));
    el.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        closePopovers(true);
      }
    });
  }
  // Escape on either trigger closes its popover too (the popover itself handles it when the focus is inside).
  for (const button of [filtersButton, moreButton]) {
    button.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && open && open.button === button) {
        event.stopPropagation();
        closePopovers(true);
      }
    });
  }

  function makeChip(chip) {
    const label = format(S.removeChip, { label: chip.title });
    if (!chip.removable) {
      // The filter tree summary: no x; it opens the filter tree.
      const button = doc.createElement("button");
      button.type = "button";
      button.className = "ui-chip fs-tree";
      button.title = chip.title;
      button.textContent = chip.label;
      button.addEventListener("click", () => onOpenTree());
      return button;
    }
    const node = createChip({ doc, label: chip.label, title: chip.title, removeLabel: label, onRemove: () => onRemove(chip.id) });
    node.classList.toggle("fs-chip", true);
    return node;
  }

  function buildOverflow(hiddenChips) {
    overflow.textContent = "";
    for (const chip of hiddenChips) {
      const row = makeChip(chip);
      overflow.appendChild(row);
    }
  }

  // Shows the chips that fit and hides the rest behind "+N more". Measures nothing: it uses `widths`.
  function fit() {
    const available = chipsHost.clientWidth;
    lastAvailable = available;
    const shown = fitChips(widths, available, moreWidth);
    nodes.forEach((node, i) => {
      node.hidden = i >= shown;
    });
    const hiddenChips = chips.slice(shown);
    if (hiddenChips.length === 0) {
      moreButton.hidden = true;
      if (open && open.el === overflow) {
        closePopovers(false);
      }
      return;
    }
    moreButton.hidden = false;
    moreButton.textContent = format(S.chipMore, { n: hiddenChips.length });
    buildOverflow(hiddenChips);
  }

  function measure() {
    widths = nodes.map((node) => {
      const w = node.offsetWidth;
      return typeof w === "number" && Number.isFinite(w) && w > 0 ? w + GAP : 0; // 0 = not measurable
    });
    moreButton.textContent = format(S.chipMore, { n: chips.length });
    moreButton.hidden = false;
    const w = moreButton.offsetWidth;
    moreWidth = typeof w === "number" && Number.isFinite(w) && w > 0 ? w + GAP : 0;
  }

  function refresh() {
    const { live, selection, parentsFilter } = getState();
    const filtered = isFiltered(live, selection, parentsFilter);
    strip.classList.toggle("is-filtered", filtered);
    stateLabel.textContent = filtered ? S.stripFiltered : S.stripNoFilters;
    stateLabel.classList.toggle("is-none", !filtered);
    chips = chipsFromState(live, selection, lookups, S);
    const next = chips.map((c) => [c.id, c.label, c.title, c.removable].join("|")).join("\n");
    if (next === signature) {
      return; // same chips: nothing to redraw or to measure
    }
    signature = next;
    chipsHost.textContent = "";
    nodes = chips.map(makeChip);
    for (const node of nodes) {
      chipsHost.appendChild(node);
    }
    chipsHost.appendChild(moreButton);
    measure();
    fit();
  }

  // The strip changed width: fit again from the widths already measured.
  function relayout() {
    if (chipsHost.clientWidth !== lastAvailable) {
      fit();
    }
  }

  if (typeof ResizeObserver === "function") {
    try {
      new ResizeObserver(() => relayout()).observe(chipsHost);
    } catch (err) {
      // No observer: the strip fits again on the next filter change.
    }
  }

  return { refresh, relayout, closePopovers: (returnFocus) => closePopovers(Boolean(returnFocus)) };
}
