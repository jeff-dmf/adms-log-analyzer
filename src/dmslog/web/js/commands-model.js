// Pure rules of the command palette and of the record context menu (UI-29): no timers, no globals.
// Both are built from the action registry (actions.js), the same one the menus use, so an action added there shows up
// in the palette without any change here. The DOM halves are commands.js (palette) and context-menu.js.
// Labels come from strings.js; the text of a choice (a case name, a key value) can come from a log and is only ever
// shaped here, never marked up.
import { isSingleKey, shortcutLabel } from "./actions.js";
import { topNodes, wrapNodes } from "./filters.js";
import { S as defaultStrings, format } from "./strings.js";

// ---- the target of a menu or of the palette ------------------------------------------------------------------------------

const sameId = (a, b) => a !== null && a !== undefined && b !== null && b !== undefined && String(a) === String(b);

/**
 * What a record menu or the palette acts on, captured ONCE when it opens: the case, the selected record (by id) and the
 * detail that belongs to it (null when the detail shows another record). Everything the actions need (source, message type,
 * keys) comes from this object, never from a second look at the live detail.
 */
export function captureTarget(ctx) {
  const row = ctx.selectedRow() || null;
  const entry = ctx.entry() || null;
  return {
    caseId: ctx.caseId(),
    row,
    rowId: row ? row.id : null,
    entry: row && entry && sameId(entry.id, row.id) ? entry : null,
  };
}

/** The target is still what the user sees: same case, same selected record, and the detail still shows the captured one. */
export function targetCurrent(ctx, target) {
  try {
    const row = ctx.selectedRow() || null;
    if (ctx.caseId() !== target.caseId || (row ? row.id : null) !== target.rowId) {
      return false;
    }
    if (target.entry !== null) {
      const entry = ctx.entry();
      return Boolean(entry) && sameId(entry.id, target.entry.id);
    }
    return true;
  } catch {
    return false;
  }
}

/**
 * The AppContext the actions of a menu or of the palette run with: the captured case, record and detail instead of the live
 * ones, and a setUserFilter() that does nothing once the target is gone (an action that waited for its module).
 */
export function targetContext(ctx, target) {
  return Object.freeze({
    ...ctx,
    caseId: () => target.caseId,
    selectedRow: () => target.row,
    entry: () => target.entry,
    setUserFilter: (filter) => (targetCurrent(ctx, target) ? ctx.setUserFilter(filter) : undefined),
  });
}

// ---- palette -------------------------------------------------------------------------------------------------------

// The palette lists every action of the registry except the one that opens it.
export const PALETTE_EXCLUDED = Object.freeze(["help.palette"]);

// How far PageUp / PageDown move in the list.
export const PAGE_STEP = 8;

/** Lower case without accents, so a label with accents is found by the plain letters and the filter never depends on the locale of the page. */
export function fold(text) {
  return String(text === null || text === undefined ? "" : text).normalize("NFKD").replace(/\p{M}/gu, "").toLowerCase();
}

function textOf(strings, key) {
  return typeof key === "string" && Object.prototype.hasOwnProperty.call(strings, key) && typeof strings[key] === "string" ? strings[key] : "";
}

function safeChildren(def, ctx) {
  try {
    const list = def.children(ctx);
    return Array.isArray(list) ? list : [];
  } catch {
    return [];
  }
}

/** The shortcut to show next to an action: nothing for a one-letter key while those are turned off. */
export function shortcutText(spec, strings, singleKeysOn) {
  if (typeof spec !== "string" || spec === "" || (isSingleKey(spec) && !singleKeysOn)) {
    return null;
  }
  return shortcutLabel(spec, strings.keyNames || {});
}

/**
 * One command per action of the registry, in registration order (the order of the menus). An action with choices (Theme,
 * a case, the repeat window) gives one command per choice, "Theme: Dark"; when it has none, or is not available, it
 * stays as one disabled command with the reason. `enabled` is computed now: call this when the palette opens.
 *   registry   the action registry        ctx   the AppContext
 *   options    { strings, singleKeysOn, exclude }
 * Command: { key, index, actionId, args, group, groupLabel, label, folded, shortcut, enabled, reason, checked }.
 */
export function buildCommands(registry, ctx, { strings = defaultStrings, singleKeysOn = true, exclude = PALETTE_EXCLUDED } = {}) {
  const commands = [];
  const groups = strings.cmdGroups || {};
  for (const def of registry.list()) {
    if (exclude.includes(def.id)) {
      continue;
    }
    const now = registry.state(def.id, ctx);
    const name = textOf(strings, def.labelKey);
    const common = {
      actionId: def.id,
      group: def.group,
      groupLabel: Object.prototype.hasOwnProperty.call(groups, def.group) ? groups[def.group] : "",
    };
    const choices = def.children && now.enabled ? safeChildren(def, ctx) : null;
    if (choices !== null && choices.length > 0) {
      choices.forEach((choice, i) => {
        const label = format(textOf(strings, "cmdChoice"), { action: name, choice: String(choice.label) });
        commands.push({
          ...common, key: def.id + "#" + i, index: commands.length, args: choice.args, label, folded: fold(label),
          shortcut: null, enabled: true, reason: "", checked: choice.checked === true,
        });
      });
      continue;
    }
    let reason = "";
    if (!now.enabled) {
      reason = textOf(strings, now.reasonKey);
    } else if (def.children) {
      reason = textOf(strings, def.emptyKey); // enabled, but there is nothing to choose from
    }
    commands.push({
      ...common, key: def.id, index: commands.length, args: undefined, label: name, folded: fold(name),
      shortcut: shortcutText(def.shortcut, strings, singleKeysOn), enabled: now.enabled && !def.children, reason, checked: false,
    });
  }
  return commands;
}

// How well a word is found in a label: 0 at the start, 1 at the start of a word, 2 inside a word; null when absent.
function tokenRank(label, token) {
  let at = label.indexOf(token);
  if (at < 0) {
    return null;
  }
  if (at === 0) {
    return { rank: 0, at };
  }
  const first = at;
  while (at >= 0) {
    if (!/[\p{L}\p{N}]/u.test(label[at - 1])) {
      return { rank: 1, at };
    }
    at = label.indexOf(token, at + 1);
  }
  return { rank: 2, at: first };
}

/**
 * The commands whose label holds every word of `query` (any case, accents ignored). Order: the best match first (label
 * starts with the word, then a word starts with it, then it is inside a word), an available command before a disabled
 * one of the same match, then where the word sits in the label, then the order of the registry. An empty query lists all
 * of them, available ones first. Never changes `commands`.
 */
export function filterCommands(commands, query) {
  const tokens = fold(query).split(/\s+/).filter(Boolean);
  const ranked = [];
  for (const command of commands) {
    let worst = 0;
    let first = 0;
    let all = true;
    tokens.forEach((token, i) => {
      const hit = all ? tokenRank(command.folded, token) : null;
      if (hit === null) {
        all = false;
        return;
      }
      worst = Math.max(worst, hit.rank);
      if (i === 0) {
        first = hit.at;
      }
    });
    if (all) {
      ranked.push({ command, worst, off: command.enabled ? 0 : 1, first });
    }
  }
  ranked.sort((a, b) => a.worst - b.worst || a.off - b.off || a.first - b.first || a.command.index - b.command.index);
  return ranked.map((entry) => entry.command);
}

/** Index of the first available command of `list`, or -1. */
export function firstEnabled(list) {
  return list.findIndex((command) => command.enabled);
}

/** The active index after the list changed: the same command when it is still there and available, else the first available. */
export function keepActive(list, key) {
  const same = key === null || key === undefined ? -1 : list.findIndex((command) => command.key === key && command.enabled);
  return same >= 0 ? same : firstEnabled(list);
}

/**
 * The new active index for ArrowDown / ArrowUp (wrapping) and PageDown / PageUp (clamped at the ends); a disabled command
 * is never active. null when the key does not move the selection. Home and End are not here: in a combobox they move the
 * caret of the text field.
 */
export function stepActive(list, current, key) {
  const n = list.length;
  const vertical = key === "ArrowDown" || key === "ArrowUp";
  const paging = key === "PageDown" || key === "PageUp";
  if (!vertical && !paging) {
    return null;
  }
  if (n === 0 || firstEnabled(list) < 0) {
    return -1;
  }
  const dir = key === "ArrowDown" || key === "PageDown" ? 1 : -1;
  if (vertical) {
    let i = current < 0 || current >= n ? (dir > 0 ? -1 : n) : current;
    for (let tried = 0; tried < n; tried += 1) {
      i = (((i + dir) % n) + n) % n;
      if (list[i].enabled) {
        return i;
      }
    }
    return -1;
  }
  const start = Math.min(n - 1, Math.max(0, (current < 0 ? (dir > 0 ? -1 : n) : current) + dir * PAGE_STEP));
  for (const way of [dir, -dir]) {
    for (let i = start; i >= 0 && i < n; i += way) {
      if (list[i].enabled) {
        return i;
      }
    }
  }
  return current;
}

// ---- record context menu -------------------------------------------------------------------------------------------

// The launcher is not in the menu it opens.
export const CONTEXT_MENU_EXCLUDED = Object.freeze(["record.contextMenu"]);

// The sections of the menu, separated by a line: what narrows the view, then what is done with the record. Actions of the
// "record" group that are not listed come last, in registration order.
export const CONTEXT_MENU_ORDER = Object.freeze([
  Object.freeze(["record.sameKey", "record.onlySource", "record.filterType", "record.hideType", "record.highlightType", "record.context"]),
  Object.freeze(["record.bookmark", "record.compare", "record.copy"]),
]);

/** ActionDefs (and {separator: true}) of the record context menu, for toMenuItems(). */
export function contextMenuDefs(registry) {
  const all = registry.list({ group: "record" }).filter((def) => !CONTEXT_MENU_EXCLUDED.includes(def.id));
  const byId = new Map(all.map((def) => [def.id, def]));
  const placed = new Set();
  const sections = [];
  for (const ids of CONTEXT_MENU_ORDER) {
    const section = ids.map((id) => byId.get(id)).filter(Boolean);
    section.forEach((def) => placed.add(def.id));
    if (section.length > 0) {
      sections.push(section);
    }
  }
  const rest = all.filter((def) => !placed.has(def.id));
  if (rest.length > 0) {
    sections.push(rest);
  }
  const defs = [];
  sections.forEach((section, i) => {
    if (i > 0) {
      defs.push({ separator: true });
    }
    defs.push(...section);
  });
  return defs;
}

/**
 * MenuItems of toMenuItems() made ready for the context menu: a one-letter shortcut is not shown while those are turned
 * off, and every run() goes through `wrap(run, item)` (which can refuse it when the record is no longer the target). Children
 * (Same key choices) get the same treatment. Pure: returns new items.
 */
export function adaptMenuItems(items, registry, { singleKeysOn = true, strings = defaultStrings, wrap = (fn) => fn } = {}) {
  return items.map((item) => {
    if (item.separator) {
      return item;
    }
    const def = registry.get(item.id);
    const next = { ...item };
    if (def && def.shortcut) {
      next.shortcut = shortcutText(def.shortcut, strings, singleKeysOn);
    }
    if (typeof item.run === "function") {
      next.run = wrap(item.run, item);
    }
    if (Array.isArray(item.children)) {
      next.children = adaptMenuItems(item.children, registry, { singleKeysOn, strings, wrap });
    }
    return next;
  });
}

// ---- filters of the record actions ---------------------------------------------------------------------------------

const isNode = (node, key) => Boolean(node) && typeof node === "object" && Object.keys(node).length === 1 && key in node;

/** "Only this source": the user filter with the one condition on the source of the record (it replaces an earlier one). */
export function onlySourceFilter(userFilter, sourceId) {
  const nodes = topNodes(userFilter).filter((node) => !(isNode(node, "source") && Array.isArray(node.source)));
  nodes.push({ source: [sourceId] });
  return wrapNodes(nodes);
}

/** "Hide this message type": the user filter AND NOT that message type (joins an earlier "hide" condition). */
export function hideTypeFilter(userFilter, tplId) {
  const nodes = topNodes(userFilter);
  const at = nodes.findIndex((node) => isNode(node, "not") && isNode(node.not, "tpl") && Array.isArray(node.not.tpl));
  if (at < 0) {
    nodes.push({ not: { tpl: [tplId] } });
  } else if (!nodes[at].not.tpl.includes(tplId)) {
    nodes[at] = { not: { tpl: [...nodes[at].not.tpl, tplId] } };
  }
  return wrapNodes(nodes);
}

// ---- focus ---------------------------------------------------------------------------------------------------------

function displayed(el) {
  if (!el || el.isConnected === false) {
    return false;
  }
  for (let n = el; n; n = n.parentElement || n.parent || null) {
    if (n.hidden === true) {
      return false;
    }
  }
  return true;
}

const takesFocus = (el) => displayed(el) && typeof el.focus === "function" && el.disabled !== true &&
  !(typeof el.getAttribute === "function" && el.getAttribute("aria-disabled") === "true");

/**
 * Gives the focus back to `el` (what opened a menu or the palette). If it is gone, hidden or disabled, to the first of
 * `fallbackIds` that takes it. True when the focus landed somewhere.
 */
export function returnFocus(doc, el, fallbackIds = ["grid-viewport", "case-button"]) {
  const lookup = (id) => (typeof doc.getElementById === "function" ? doc.getElementById(id) : null);
  const candidates = [el, ...fallbackIds.map(lookup)];
  for (const candidate of candidates) {
    if (takesFocus(candidate)) {
      candidate.focus();
      if (doc.activeElement === candidate) {
        return true;
      }
    }
  }
  return false;
}
