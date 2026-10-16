// "Collections" panel of the left column: one row per collection of the case (an ERS capture, a
// folder or a file) on one line, a checkbox filter per row, the approximate-order warning and, from the row's
// "..." menu, a two-step "Remove". The list folds away (open when the case has more than one collection).
// Everything from the API reaches the page through textContent only.
import { ApiError } from "./api.js";
import {
  describe, pruneSelection, removeConfirmText, removeErrorMessage, removedMessage, selectionLine,
} from "./collections-model.js";
import { unreadableCount, unreadableLine } from "./sources.js";
import { S, format } from "./strings.js";

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) {
    node.className = className;
  }
  if (text !== undefined) {
    node.textContent = text;
  }
  return node;
}

function button(label, handler, className) {
  const node = el("button", className, label);
  node.type = "button";
  node.addEventListener("click", handler);
  return node;
}

export class CollectionsPanel {
  // host: the <section> of the panel; note: the warning above the grid (both start hidden).
  // api: {collections(caseId, signal), removeCollection(caseId, group)}; getCase() -> case id | null.
  // onSelection(groups): the chosen groups changed; onList(collections, ordering): a new list arrived;
  // onRemoved(summary): a removal finished (lists of sources, grid and panels reload); onMessage(text, isError).
  // loadMenu() -> Promise of the ui-menu.js module (loaded on the first click of a row's menu; tests replace it).
  constructor({ host, note, api, getCase, onSelection, onList, onRemoved, onMessage, loadMenu }) {
    this.host = host;
    this.note = note;
    this.api = api;
    this.getCase = getCase;
    this.onSelection = onSelection || (() => {});
    this.onList = onList || (() => {});
    this.onRemoved = onRemoved || (() => Promise.resolve());
    this.onMessage = onMessage || (() => {});
    this.collections = [];
    this.ordering = "exact";
    this.selected = new Set();
    this.expanded = new Set();
    this.open = null; // the user's choice for the folding list; null = open when there is more than one collection
    this.sourceList = []; // the sources of the case (setSources), for the unreadable counts
    this.unreadable = { total: 0, byGroup: new Map() }; // what the rows last said about them
    this.loadMenu = loadMenu || (() => import("./ui-menu.js"));
    this.menus = new WeakMap(); // row menu button -> its menu (promise)
    this.activeMenu = null; // the menu that may be open
    this.activeMenuGroup = null; // the collection whose row it belongs to
    this.menuToken = 0; // the latest click that asked for a row menu: an older request that arrives later is dropped
    this.renderGen = 0; // counts the drawings of the rows: a menu that finished loading for an older drawing is dropped
    this.signature = null; // what the rows were last drawn from: an identical list is not drawn again
    this.confirming = null; // group waiting for the second step
    this.removing = null; // group being removed
    this.gen = 0; // case generation: answers of an older case never touch the current state
    this.wanted = null; // {group, act}: where the focus goes on the next render
    this.seq = 0;
    this.controller = null;
    this.build();
    this.render();
  }

  build() {
    const host = this.host;
    host.textContent = "";
    host.className = "collections-panel";
    this.heading = el("h3", "col-title");
    this.heading.id = "collections-title";
    host.setAttribute("aria-labelledby", this.heading.id);
    this.list = el("ul", "col-list");
    this.list.id = "collections-list";
    this.list.setAttribute("aria-label", S.colListLabel);
    this.fold = button("", () => this.setOpen(!this.isOpen()), "col-fold");
    this.fold.setAttribute("aria-controls", this.list.id);
    this.fold.dataset.act = "fold";
    this.heading.appendChild(this.fold);
    this.unreadableText = el("span", "col-unreadable");
    const head = el("div", "col-head");
    head.append(this.heading, this.unreadableText);
    this.status = el("div", "source-meta col-status");
    this.status.setAttribute("role", "status");
    this.status.setAttribute("aria-live", "polite");
    this.allButton = button(S.colAll, () => this.clearSelection(), "col-all");
    this.orderText = el("p", "col-order-more", S.colApproxOrderMore);
    const foot = el("div", "col-foot");
    foot.append(this.allButton, this.status);
    host.append(head, this.list, foot, this.orderText);
    this.note.className = "col-order-note";
    this.note.setAttribute("role", "status");
    this.note.setAttribute("aria-live", "polite");
    this.note.title = S.colApproxOrderMore;
    this.note.textContent = S.colApproxOrder;
  }

  // -- state --------------------------------------------------------------------------------

  selectedGroups() {
    return this.collections.map((c) => c.group).filter((g) => this.selected.has(g));
  }

  // The case changed (or none): forget the list and the selection. The list of the new case is loaded by
  // refresh(), which the owner calls once the sources of the case are known.
  setCase() {
    this.gen += 1;
    this.wanted = null;
    this.seq += 1;
    if (this.controller) {
      this.controller.abort();
      this.controller = null;
    }
    this.collections = [];
    this.ordering = "exact";
    this.selected = new Set();
    this.expanded = new Set();
    this.open = null;
    this.signature = null;
    this.confirming = null;
    this.removing = null;
    this.sourceList = [];
    this.unreadable = { total: 0, byGroup: new Map() };
    this.render();
    this.onList(this.collections, this.ordering);
  }

  // Sources that could not be read: in total and per collection (their `collection` is the group id).
  countUnreadable() {
    const byGroup = new Map();
    for (const item of this.collections) {
      const n = unreadableCount(this.sourceList, item.group);
      if (n > 0) {
        byGroup.set(item.group, n);
      }
    }
    return { total: unreadableCount(this.sourceList), byGroup };
  }

  // The sources of the open case (called before the list of collections is reloaded, so it keeps them).
  setSources(sources) {
    this.sourceList = Array.isArray(sources) ? sources : [];
    const next = this.countUnreadable();
    const same = next.total === this.unreadable.total && next.byGroup.size === this.unreadable.byGroup.size
      && [...next.byGroup].every(([group, n]) => this.unreadable.byGroup.get(group) === n);
    if (!same) {
      this.render();
    }
  }

  isOpen() {
    return this.open === null ? this.collections.length > 1 : this.open;
  }

  setOpen(on) {
    this.open = Boolean(on);
    this.wanted = { group: null, act: "fold" };
    this.render();
  }

  // Reloads the list. Resolves to true when the chosen collections changed (a chosen one vanished).
  async refresh() {
    const caseId = this.getCase();
    if (caseId === null || caseId === undefined) {
      return false;
    }
    this.seq += 1;
    const mine = this.seq;
    if (this.controller) {
      this.controller.abort();
    }
    const controller = new AbortController();
    this.controller = controller;
    let data;
    try {
      data = await this.api.collections(caseId, controller.signal);
    } catch (err) {
      if (mine === this.seq && !(err && err.name === "AbortError")) {
        this.status.textContent = format(S.colLoadError, { message: err && err.message ? err.message : String(err) });
      }
      return false;
    }
    if (mine !== this.seq || caseId !== this.getCase()) {
      return false;
    }
    this.controller = null;
    this.collections = Array.isArray(data.collections) ? data.collections : [];
    this.ordering = data.ordering === "approximate" ? "approximate" : "exact";
    const kept = pruneSelection(this.selected, this.collections);
    const changed = kept.length !== this.selected.size;
    this.selected = new Set(kept);
    if (this.confirming !== null && !this.collections.some((c) => c.group === this.confirming)) {
      this.confirming = null;
    }
    const signature = JSON.stringify([this.ordering, this.collections]);
    if (signature !== this.signature || changed) {
      this.signature = signature;
      this.render();
    }
    this.onList(this.collections, this.ordering);
    if (changed) {
      this.onSelection(this.selectedGroups());
    }
    return changed;
  }

  toggle(group, on) {
    if (on) {
      this.selected.add(group);
    } else {
      this.selected.delete(group);
    }
    this.renderStatus();
    this.onSelection(this.selectedGroups());
  }

  clearSelection() {
    if (this.selected.size === 0) {
      return;
    }
    this.selected = new Set();
    this.render();
    this.onSelection([]);
  }

  // -- removal ------------------------------------------------------------------------------

  askRemove(group) {
    if (this.removing !== null) {
      return;
    }
    this.confirming = group;
    this.wanted = { group, act: "cancel" };
    this.render();
  }

  cancelRemove(group) {
    this.confirming = null;
    this.wanted = { group, act: "menu" };
    this.render();
  }

  async remove(item) {
    const caseId = this.getCase();
    if (this.removing !== null || caseId === null) {
      return;
    }
    const tag = describe(item).tag;
    const gen = this.gen;
    const index = this.collections.findIndex((c) => c.group === item.group);
    this.removing = item.group;
    this.confirming = null;
    this.wanted = { group: item.group, act: "menu" };
    this.render();
    this.onMessage(format(S.colRemoving, { tag }), false);
    try {
      const summary = await this.api.removeCollection(caseId, item.group);
      if (gen !== this.gen) {
        return; // another case is open now: this answer is not about it
      }
      this.removing = null;
      this.expanded.delete(item.group);
      this.selected.delete(item.group);
      this.onMessage(removedMessage(tag, summary), false);
      // The removed row is gone: the focus goes to the next collection (or the previous, or the heading).
      const rest = this.collections.filter((c) => c.group !== item.group);
      const next = rest[Math.min(Math.max(index, 0), rest.length - 1)];
      this.wanted = next ? { group: next.group, act: "menu" } : { group: null, act: "heading" };
      await this.refresh();
      await this.onRemoved(summary); // reloads sources, grid and panels (the grid reads selectedGroups())
    } catch (err) {
      if (gen !== this.gen) {
        return;
      }
      this.removing = null;
      const notFound = err instanceof ApiError && err.code === "collection_not_found";
      this.onMessage(removeErrorMessage(err, tag), true);
      this.wanted = { group: item.group, act: "menu" };
      this.render();
      if (notFound) {
        await this.refresh();
      }
    } finally {
      if (gen === this.gen) {
        this.removing = null;
        this.render();
      }
    }
  }

  // -- rendering ----------------------------------------------------------------------------

  // The focused control of the list as {group, act}, so a re-render can give the focus back.
  captureFocus() {
    if (this.activeMenu && this.activeMenuGroup !== null && this.activeMenu.isOpen()) {
      return { group: this.activeMenuGroup, act: "menu" }; // the focus is on an item of the open menu, which is about to go
    }
    const active = typeof document !== "undefined" ? document.activeElement : null;
    if (!active || !active.dataset || active.dataset.act === undefined) {
      return null;
    }
    return { group: active.dataset.group === undefined ? null : Number(active.dataset.group), act: active.dataset.act };
  }

  applyFocus(want) {
    let target = null;
    if (want.act === "fold") {
      target = this.fold;
    } else if (want.act !== "heading" && want.group !== null && !this.list.hidden) {
      target = this.list.querySelector('[data-group="' + want.group + '"][data-act="' + want.act + '"]');
      if (!target && want.act === "cancel") {
        target = this.list.querySelector('[data-group="' + want.group + '"][data-act="menu"]');
      }
      if (!target && want.act === "menu") {
        target = this.list.querySelector('[data-group="' + want.group + '"][data-act="pick"]');
      }
    }
    if (!target && (want.act !== "pick" || this.list.hidden)) {
      target = this.fold; // the folding header is the panel's way in (a row control cannot be focused in a folded list)
    }
    if (target && !target.disabled) {
      target.focus();
    }
  }

  render() {
    const want = this.wanted || this.captureFocus();
    this.wanted = null;
    this.renderList();
    if (want) {
      this.applyFocus(want);
    }
  }

  renderList() {
    const multi = this.collections.length > 1;
    if (this.activeMenu && this.activeMenu.isOpen()) {
      this.activeMenu.close(false); // its row is about to be replaced
    }
    this.activeMenu = null;
    this.activeMenuGroup = null;
    this.renderGen += 1;
    this.host.hidden = this.collections.length === 0;
    this.unreadable = this.countUnreadable();
    const open = this.isOpen();
    this.list.hidden = !open;
    this.fold.setAttribute("aria-expanded", open ? "true" : "false");
    this.fold.textContent = format(S.colTitleCount, { count: this.collections.length });
    this.fold.title = open ? S.colFoldHide : S.colFoldShow;
    this.unreadableText.textContent = unreadableLine(this.unreadable.total);
    this.unreadableText.hidden = this.unreadable.total === 0;
    this.note.hidden = !(this.ordering === "approximate" && multi);
    this.orderText.hidden = this.note.hidden;
    this.allButton.hidden = !multi;
    this.allButton.disabled = this.selected.size === 0;
    this.list.textContent = "";
    for (const item of this.collections) {
      this.list.appendChild(this.renderItem(item));
    }
    this.renderStatus();
  }

  renderStatus() {
    this.status.textContent = this.collections.length > 1 ? selectionLine(this.selected, this.collections) : "";
  }

  // One line per collection: tag, identity, count and the warnings. The rest (GUID, time window, members) opens
  // with "+"; removing is in the row's menu.
  renderItem(item) {
    const d = describe(item);
    const li = el("li", "col-item");
    li.dataset.group = String(item.group);
    const row = el("div", "col-row");
    const label = el("label", "col-pick");
    const box = document.createElement("input");
    box.type = "checkbox";
    box.checked = this.selected.has(item.group);
    box.setAttribute("aria-label", format(S.colSelectLabel, { tag: d.tag }));
    box.dataset.group = String(item.group);
    box.dataset.act = "pick";
    box.addEventListener("change", () => this.toggle(item.group, box.checked));
    const chip = el("span", "col-tag col-tag-" + d.color, d.tag);
    const text = el("span", "col-text");
    const name = el("span", "col-name", d.title);
    text.appendChild(name);
    text.appendChild(el("span", "source-meta col-counts", d.counts));
    const unreadable = unreadableLine(this.unreadable.byGroup.get(item.group) || 0);
    for (const [cls, value] of [["col-missing", d.missing], ["col-state col-state-" + d.statusCode, d.status],
      ["col-state col-state-error", unreadable]]) {
      if (value !== "") {
        text.appendChild(el("span", cls, value));
      }
    }
    label.title = [d.title, d.counts, d.window, d.guidFull].filter((x) => x !== "").join("\n");
    label.append(box, chip, text);
    row.appendChild(label);

    const open = this.expanded.has(item.group);
    const toggle = button(open ? "-" : "+", () => {
      if (open) {
        this.expanded.delete(item.group);
      } else {
        this.expanded.add(item.group);
      }
      this.wanted = { group: item.group, act: "expand" };
      this.render();
    }, "col-expand");
    toggle.dataset.group = String(item.group);
    toggle.dataset.act = "expand";
    toggle.setAttribute("aria-expanded", open ? "true" : "false");
    toggle.setAttribute("aria-label", format(open ? S.colCollapseLabel : S.colExpandLabel, { tag: d.tag }));
    row.appendChild(toggle);
    const menu = button("", (event) => this.openRowMenu(item, d.tag, menu, event), "col-menu");
    menu.dataset.group = String(item.group);
    menu.dataset.act = "menu";
    const menuLabel = format(S.colRowMenuLabel, { tag: d.tag });
    menu.setAttribute("aria-label", menuLabel);
    menu.title = menuLabel;
    menu.disabled = this.removing !== null;
    row.appendChild(menu);
    li.appendChild(row);

    if (open) {
      const members = el("ul", "col-members");
      if (d.window !== "") {
        const m = el("li", "col-member");
        m.appendChild(el("span", "source-meta", d.window));
        members.appendChild(m);
      }
      if (d.guid !== "") {
        const m = el("li", "col-member");
        const guid = el("span", "source-meta col-guid", format(S.colGuidLine, { guid: d.guid }));
        guid.title = d.guidFull;
        m.appendChild(guid);
        members.appendChild(m);
      }
      for (const member of d.members) {
        const m = el("li", "col-member");
        m.appendChild(el("span", "col-member-name", member.name));
        const bits = [member.variant, member.counts, member.status].filter((x) => x !== "");
        m.appendChild(el("span", "source-meta", bits.join(" - ")));
        members.appendChild(m);
      }
      li.appendChild(members);
    }

    if (this.confirming === item.group) {
      const actions = el("div", "col-actions");
      const confirm = el("div", "col-confirm");
      confirm.setAttribute("role", "alertdialog");
      confirm.setAttribute("aria-label", format(S.colRemoveLabel, { tag: d.tag }));
      confirm.appendChild(el("p", "col-confirm-text", removeConfirmText(item)));
      const yes = button(format(S.colRemoveYes, { tag: d.tag }), () => this.remove(item), "col-yes");
      const cancel = button(S.colRemoveCancel, () => this.cancelRemove(item.group), "col-cancel");
      yes.dataset.group = cancel.dataset.group = String(item.group);
      yes.dataset.act = "yes";
      cancel.dataset.act = "cancel";
      confirm.addEventListener("keydown", (event) => {
        if (event.key === "Escape") {
          event.preventDefault();
          event.stopPropagation();
          this.cancelRemove(item.group);
        }
      });
      confirm.append(yes, cancel);
      actions.appendChild(confirm);
      li.appendChild(actions);
    }
    return li;
  }

  // The row's "..." menu: loaded on the first click, then it toggles itself. Its one item starts the two-step removal.
  openRowMenu(item, tag, trigger, event) {
    if (this.menus.has(trigger)) {
      return; // built already: the menu handles this click
    }
    const where = event && event.detail === 0 ? "first" : null;
    const gen = this.renderGen;
    this.menuToken += 1;
    const token = this.menuToken;
    const ready = this.loadMenu().then((mod) => {
      if (token !== this.menuToken || gen !== this.renderGen || trigger.isConnected === false) {
        this.menus.delete(trigger); // a later click, a new drawing or a removed row: nothing to open (a click tries again)
        return null;
      }
      if (this.activeMenu && this.activeMenu.isOpen()) {
        this.activeMenu.close(false); // one row menu open at a time
      }
      const menu = mod.createMenu({
        trigger,
        label: format(S.colRowMenuLabel, { tag }),
        getItems: () => {
          this.activeMenu = menu; // asked at every opening, so a later opening of this menu is the active one
          this.activeMenuGroup = item.group;
          return [{
            id: "col-remove",
            label: format(S.colRemoveItem, { tag }),
            disabled: this.removing !== null,
            run: () => this.askRemove(item.group),
          }];
        },
      });
      menu.open(where);
      return menu;
    });
    this.menus.set(trigger, ready);
    ready.catch((err) => {
      this.menus.delete(trigger); // tried again on the next click
      this.onMessage(err && err.message ? err.message : String(err), true);
    });
  }
}
