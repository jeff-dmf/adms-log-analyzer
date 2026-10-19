// The sources as a tree of the ERS folders (css/sources.css, js/source-tree-model.js is the pure part). A folder row
// checks every source under it (all, some = mixed, none) and drives the same selection as the flat list, so the grid
// filters the same way. Names come from the logs: text nodes and <mark> only, never HTML. Text comes from strings.js.
import { S, format } from "./strings.js";
import { highlightParts, isFiltering, matchRange } from "./source-filter-model.js";
import { isUnreadable, sourceMeta, unreadableTitle } from "./sources.js";
import {
  STORAGE_PREFIX, buildTree, checkState, closed, defaultOpen, idsOf, matchedIds, parseOpen, serializeOpen, toggled, treeKeyTarget,
  visibleRows,
} from "./source-tree-model.js";

function sessionStore() {
  try {
    return window.sessionStorage;
  } catch (err) {
    return null;
  }
}

// deps: {host, panel, storage?}. `panel` is the SourcesPanel: sources, selected, query, caseId, applySelection(set).
export class SourceTree {
  constructor({ host, panel, storage }) {
    this.host = host;
    this.panel = panel;
    this.store = storage === undefined ? sessionStore() : storage;
    this.tree = { roots: [], ids: [] };
    this.open = new Set();
    this.openFor = undefined; // the case whose open folders are in `open`
    this.rows = new Map(); // node key -> {el, ...}: the rows on screen
    this.list = []; // visibleRows() of the last render
    this.matched = null;
    this.stopKey = null; // the row that is the tree's one tab stop (roving tabindex)
    this.sig = "";
    this.empty = document.createElement("div");
    this.empty.className = "st-empty";
    host.setAttribute("role", "tree");
    host.setAttribute("aria-multiselectable", "true");
    host.addEventListener("click", (event) => this.onClick(event));
    host.addEventListener("keydown", (event) => this.onKey(event));
    host.addEventListener("focusin", (event) => {
      const key = this.keyOf(event.target);
      if (key !== null && key !== this.stopKey) {
        this.stopKey = key;
        this.syncTabStops();
      }
    });
    panel.treeHook = () => this.update();
    this.update();
  }

  destroy() {
    this.panel.treeHook = null;
    this.host.textContent = "";
    this.rows.clear();
  }

  keyOf(node) {
    for (let n = node; n && n !== this.host; n = n.parentNode) {
      if (n.dataset && n.dataset.key !== undefined) {
        return n.dataset.key;
      }
    }
    return null;
  }

  loadOpen() {
    this.openFor = this.panel.caseId;
    let saved = null;
    try {
      saved = this.store ? parseOpen(this.store.getItem(STORAGE_PREFIX + this.openFor)) : null;
    } catch (err) {
      saved = null;
    }
    this.open = saved === null ? defaultOpen(this.tree) : saved;
  }

  saveOpen() {
    try {
      if (this.store) {
        this.store.setItem(STORAGE_PREFIX + this.openFor, serializeOpen(this.open));
      }
    } catch (err) {
      // Not remembered; the tree works the same.
    }
  }

  // Rebuilds from the panel's sources, then writes only what changed.
  update() {
    const sources = this.panel.sources;
    const sig = sources.map((s) => s.id + ":" + (s.member || s.name) + ":" + s.records + ":" + s.status).join("|");
    const caseChanged = this.openFor !== this.panel.caseId;
    if (sig !== this.sig || caseChanged) {
      this.sig = sig;
      this.tree = buildTree(sources);
      if (caseChanged) {
        this.loadOpen();
      }
    }
    this.matched = matchedIds(this.tree, this.panel.query, sources);
    this.list = visibleRows(this.tree, this.open, this.matched);
    this.render();
    if (this.panel.treeActive) {
      // The count under the filter box is the tree's: the sources whose path matches, not the flat list's name matches.
      this.panel.notifyRendered(this.matched === null ? sources.length : this.matched.size, sources.length);
    }
  }

  // The filter box: ArrowDown goes to the tree, Enter in the box toggles the first row.
  focusFirst() {
    const entry = this.rows.get(this.stopKey) || (this.list[0] ? this.rows.get(this.list[0].node.key) : null);
    if (entry) {
      entry.el.focus();
    }
  }

  toggleFirst() {
    if (this.list[0]) {
      this.toggleCheck(this.list[0].node.key);
    }
  }

  render() {
    const selected = this.panel.selected;
    const keep = new Set();
    const focused = this.keyOf(document.activeElement); // before any row leaves: a row that goes takes the focus with it
    const query = isFiltering(this.panel.query) ? this.panel.query : "";
    for (const row of this.list) {
      keep.add(row.node.key);
    }
    for (const [key, entry] of this.rows) {
      if (!keep.has(key)) {
        entry.el.remove();
        this.rows.delete(key);
      }
    }
    if (this.empty.parentNode === this.host) {
      this.empty.remove();
    }
    this.list.forEach((row, index) => {
      let entry = this.rows.get(row.node.key);
      if (!entry) {
        entry = this.makeRow(row.node);
        this.rows.set(row.node.key, entry);
      }
      this.fill(entry, row, selected, query);
      const want = entry.el;
      const at = this.host.children[index];
      if (at !== want) {
        this.host.insertBefore(want, at || null);
      }
    });
    if (this.list.length === 0) {
      this.empty.textContent = this.panel.sources.length === 0 ? S.sourcesEmpty : S.sourceFilterNone;
      this.host.appendChild(this.empty);
    }
    this.host.setAttribute("aria-rowcount", String(this.list.length));
    if (!this.rows.has(this.stopKey)) {
      this.stopKey = this.list.length > 0 ? this.list[0].node.key : null;
    }
    this.syncTabStops();
    if (focused !== null) {
      // The row that had the focus, or the one that took its place (the folder it was merged into), or the first.
      const row = this.rows.has(focused) ? focused : (this.list.find((r) => focused.startsWith(r.node.key)) || this.list[0] || { node: { key: null } }).node.key;
      if (row !== null && this.rows.has(row)) {
        this.stopKey = row;
        this.syncTabStops();
        if (document.activeElement !== this.rows.get(row).el) {
          this.rows.get(row).el.focus();
        }
      } else if (this.panel.refs.filterInput) {
        this.panel.refs.filterInput.focus();
      }
    }
  }

  makeRow(node) {
    const el = document.createElement("div");
    el.className = "st-row" + (node.folder ? " is-folder" : " is-file");
    el.setAttribute("role", "treeitem");
    el.dataset.key = node.key;
    const twisty = document.createElement("span");
    twisty.className = "st-twisty";
    twisty.setAttribute("aria-hidden", "true");
    const box = document.createElement("span");
    box.className = "st-box";
    box.setAttribute("aria-hidden", "true");
    const mark = document.createElement("span");
    mark.className = "source-unreadable";
    mark.textContent = "!";
    mark.setAttribute("role", "img");
    mark.setAttribute("aria-label", S.sourceUnreadable);
    mark.hidden = true;
    const name = document.createElement("span");
    name.className = "st-name";
    const count = document.createElement("span");
    count.className = "st-count";
    count.setAttribute("aria-hidden", "true");
    const badge = document.createElement("span");
    badge.className = "badge";
    badge.textContent = S.sourceSensitive;
    badge.hidden = true;
    el.append(twisty, box, mark, name, count, badge);
    return { el, twisty, box, mark, name, count, badge, sig: null };
  }

  fill(entry, row, selected, query) {
    const { node } = row;
    const { el } = entry;
    el.setAttribute("aria-level", String(row.level));
    el.setAttribute("aria-setsize", String(row.setSize));
    el.setAttribute("aria-posinset", String(row.posInSet));
    el.style.paddingInlineStart = (row.level - 1) * 14 + 2 + "px";
    if (node.folder) {
      el.setAttribute("aria-expanded", row.open ? "true" : "false");
    }
    const ids = idsOf(node, this.matched);
    const state = checkState(ids, selected);
    el.setAttribute("aria-checked", state === "all" ? "true" : state === "some" ? "mixed" : "false");
    el.dataset.state = state;
    const sig = node.name + "\u0000" + query;
    if (entry.sig !== sig) {
      entry.sig = sig;
      entry.name.textContent = "";
      const range = query === "" ? null : matchRange(node.name, query);
      for (const part of highlightParts(node.name, range)) {
        if (part.match) {
          const m = document.createElement("mark");
          m.textContent = part.text;
          entry.name.appendChild(m);
        } else {
          entry.name.appendChild(document.createTextNode(part.text));
        }
      }
    }
    const records = (node.records || 0).toLocaleString("en-US");
    const count = node.folder ? format(S.treeFiles, { n: node.files.toLocaleString("en-US") }) : records;
    if (entry.count.textContent !== count) {
      entry.count.textContent = count;
    }
    const title = node.folder
      ? format(S.treeFolderTitle, { path: node.name, files: node.files.toLocaleString("en-US"), records })
      : sourceMeta(node.source);
    if (el.title !== title) {
      el.title = title;
    }
    const bad = node.folder ? node.unreadable > 0 : isUnreadable(node.source);
    entry.mark.hidden = !bad;
    if (bad) {
      entry.mark.title = node.folder ? format(S.treeUnreadable, { n: node.unreadable }) : unreadableTitle(node.source);
    }
    entry.badge.hidden = node.folder || !node.source.sensitive;
  }

  syncTabStops() {
    for (const [key, entry] of this.rows) {
      const want = key === this.stopKey ? "0" : "-1";
      if (entry.el.getAttribute("tabindex") !== want) {
        entry.el.setAttribute("tabindex", want);
      }
    }
  }

  setOpen(key, isOpen) {
    if (isOpen) {
      this.open.add(key);
    } else {
      this.open = closed(this.open, key);
    }
    this.saveOpen();
    this.update();
  }

  toggleCheck(key) {
    const row = this.list.find((r) => r.node.key === key);
    if (!row) {
      return;
    }
    this.panel.applySelection(toggled(this.panel.selected, idsOf(row.node, this.matched)));
  }

  onClick(event) {
    const key = this.keyOf(event.target);
    if (key === null) {
      return;
    }
    const row = this.list.find((r) => r.node.key === key);
    if (!row) {
      return;
    }
    this.stopKey = key;
    if (row.node.folder && event.target.closest(".st-twisty")) {
      this.setOpen(key, !row.open);
    } else {
      this.toggleCheck(key);
    }
  }

  onKey(event) {
    const key = this.keyOf(event.target);
    if (key === null) {
      return;
    }
    const index = this.list.findIndex((r) => r.node.key === key);
    if (event.key === " " || event.key === "Enter") {
      event.preventDefault();
      this.toggleCheck(key);
      return;
    }
    if (event.key === "Escape" && this.panel.refs.filterInput) {
      this.panel.refs.filterInput.focus();
      return;
    }
    const target = treeKeyTarget(this.list, index, event.key);
    if (target === null) {
      return;
    }
    event.preventDefault();
    if (target.focus !== undefined) {
      this.rows.get(this.list[target.focus].node.key).el.focus();
    } else if (target.open !== undefined) {
      this.setOpen(target.open, true);
    } else if (target.close !== undefined) {
      this.setOpen(target.close, false);
    }
  }
}
