// "Collections" panel of the left column: one row per collection of the case (an ERS capture, a
// folder or a file), a checkbox filter per row, the approximate-order warning and a two-step
// "Remove collection". Everything from the API reaches the page through textContent only.
import { ApiError } from "./api.js";
import {
  describe, pruneSelection, removeConfirmText, removeErrorMessage, removedMessage, selectionLine,
} from "./collections-model.js";
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
  constructor({ host, note, api, getCase, onSelection, onList, onRemoved, onMessage }) {
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
    this.heading = el("h3", "col-title", S.colTitle);
    this.heading.id = "collections-title";
    this.heading.setAttribute("tabindex", "-1");
    host.setAttribute("aria-labelledby", this.heading.id);
    this.list = el("ul", "col-list");
    this.list.setAttribute("aria-label", S.colListLabel);
    this.status = el("div", "source-meta col-status");
    this.status.setAttribute("role", "status");
    this.status.setAttribute("aria-live", "polite");
    this.allButton = button(S.colAll, () => this.clearSelection(), "col-all");
    this.orderText = el("p", "col-order-more", S.colApproxOrderMore);
    const foot = el("div", "col-foot");
    foot.append(this.allButton, this.status);
    host.append(this.heading, this.list, foot, this.orderText);
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
    this.confirming = null;
    this.removing = null;
    this.render();
    this.onList(this.collections, this.ordering);
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
    this.render();
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
    this.wanted = { group, act: "remove" };
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
    this.wanted = { group: item.group, act: "remove" };
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
      this.wanted = next ? { group: next.group, act: "remove" } : { group: null, act: "heading" };
      await this.refresh();
      await this.onRemoved(summary); // reloads sources, grid and panels (the grid reads selectedGroups())
    } catch (err) {
      if (gen !== this.gen) {
        return;
      }
      this.removing = null;
      const notFound = err instanceof ApiError && err.code === "collection_not_found";
      this.onMessage(removeErrorMessage(err, tag), true);
      this.wanted = { group: item.group, act: "remove" };
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
    const active = typeof document !== "undefined" ? document.activeElement : null;
    if (!active || !active.dataset || active.dataset.act === undefined) {
      return null;
    }
    return { group: active.dataset.group === undefined ? null : Number(active.dataset.group), act: active.dataset.act };
  }

  applyFocus(want) {
    let target = null;
    if (want.act !== "heading" && want.group !== null) {
      target = this.list.querySelector('[data-group="' + want.group + '"][data-act="' + want.act + '"]');
      if (!target && want.act === "cancel") {
        target = this.list.querySelector('[data-group="' + want.group + '"][data-act="remove"]');
      }
    }
    if (!target && want.act !== "pick") {
      target = this.heading;
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
    this.host.hidden = this.collections.length === 0;
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
    name.title = d.title;
    text.appendChild(name);
    if (d.guid !== "") {
      const guid = el("span", "source-meta col-guid", format(S.colGuidLine, { guid: d.guid }));
      guid.title = d.guidFull;
      text.appendChild(guid);
    }
    text.appendChild(el("span", "source-meta", d.counts));
    if (d.window !== "") {
      text.appendChild(el("span", "source-meta", d.window));
    }
    if (d.missing !== "") {
      text.appendChild(el("span", "col-missing", d.missing));
    }
    if (d.status !== "") {
      text.appendChild(el("span", "col-state col-state-" + d.statusCode, d.status));
    }
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
    li.appendChild(row);

    if (open) {
      const members = el("ul", "col-members");
      for (const member of d.members) {
        const m = el("li", "col-member");
        m.appendChild(el("span", "col-member-name", member.name));
        const bits = [member.variant, member.counts, member.status].filter((x) => x !== "");
        m.appendChild(el("span", "source-meta", bits.join(" - ")));
        members.appendChild(m);
      }
      li.appendChild(members);
    }

    const actions = el("div", "col-actions");
    if (this.confirming === item.group) {
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
    } else {
      const remove = button(S.colRemove, () => this.askRemove(item.group), "col-remove");
      remove.setAttribute("aria-label", format(S.colRemoveLabel, { tag: d.tag }));
      remove.dataset.group = String(item.group);
      remove.dataset.act = "remove";
      remove.disabled = this.removing !== null;
      actions.appendChild(remove);
    }
    li.appendChild(actions);
    return li;
  }
}
