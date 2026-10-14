// "Links" section of the entry detail: key chips ("Same key"), linked records with kind and
// confidence written as text, "Show in sensitive/normal", preview and "Side by side".
// Everything comes from the ERS (untrusted): textContent and class names only; ONE delegated
// listener on the host.
import { api } from "./api.js";
import { PREVIEW_LINES, buildKeyChips, createLinksFlow, revealLabel } from "./links-model.js";
import { S, format } from "./strings.js";

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) {
    node.className = className;
  }
  if (text !== undefined) {
    node.textContent = text;
  }
  return node;
}

// host: element to fill (hidden while there is nothing to show). Options:
//   getCase()                   current case id or null
//   onReveal({ id, ts })        select a linked record in the grid
//   onSideBySide(entryId)       open the side-by-side view for the shown entry
//   onSameKey(type, value)      open the "same key" panel
//   onError(err)
export class LinksSection {
  constructor(host, { getCase, onReveal, onSideBySide, onSameKey, onError }) {
    this.host = host;
    this.getCase = getCase;
    this.onReveal = onReveal || (() => {});
    this.onSideBySide = onSideBySide || (() => {});
    this.onSameKey = onSameKey || (() => {});
    this.onError = onError || (() => {});
    this.entry = null;
    this.chips = [];
    this.flow = createLinksFlow({ api, getCase, onState: (state) => this.render(state) });
    host.addEventListener("click", (event) => this.handleClick(event));
    this.clear();
  }

  clear() {
    this.entry = null;
    this.chips = [];
    this.flow.reset();
    this.host.hidden = true;
    this.host.textContent = "";
  }

  // The entry just fetched by the detail pane (or null when it was cleared).
  show(entry) {
    if (!entry || this.getCase() === null) {
      this.clear();
      return;
    }
    this.entry = entry;
    this.chips = buildKeyChips(entry);
    this.flow.load(entry.id);
  }

  handleClick(event) {
    if (!(event.target instanceof Element) || !this.entry) {
      return;
    }
    const button = event.target.closest("button[data-action]");
    if (!button || !this.host.contains(button)) {
      return;
    }
    const action = button.dataset.action;
    if (action === "key") {
      const chip = this.chips[Number(button.dataset.chip)];
      if (chip) {
        this.onSameKey(chip.type, chip.value);
      }
      return;
    }
    if (action === "side") {
      this.onSideBySide(this.entry.id);
      return;
    }
    const id = Number(button.dataset.id);
    const row = this.flow.state.rows.find((item) => item.entryId === id);
    if (!row) {
      return;
    }
    if (action === "reveal") {
      this.onReveal({ id: row.entryId, ts: row.ts });
    } else if (action === "preview") {
      if (this.flow.state.preview.entryId === id) {
        this.flow.closePreview();
      } else {
        this.flow.preview(id);
      }
    }
  }

  render(state) {
    const host = this.host;
    host.textContent = "";
    const hasChips = this.chips.length > 0;
    if (!this.entry || (!hasChips && (state.status === "idle" || (state.status === "ready" && state.rows.length === 0)))) {
      host.hidden = true;
      return;
    }
    host.hidden = false;
    if (hasChips) {
      host.appendChild(this.keyChips());
    }
    host.appendChild(element("h3", "lnk-title", S.lnkTitle));
    if (state.status === "loading") {
      host.appendChild(element("div", "lnk-muted", S.lnkLoading));
    } else if (state.status === "error") {
      host.appendChild(element("div", "lnk-error", S.lnkError + (state.error ? ": " + state.error : "")));
    } else if (state.rows.length === 0) {
      host.appendChild(element("div", "lnk-muted", S.lnkNone));
    } else {
      host.appendChild(this.linkList(state));
    }
  }

  keyChips() {
    const box = element("div", "lnk-keys");
    box.appendChild(element("h3", "lnk-title", S.lnkKeysTitle));
    const list = element("div", "lnk-chips");
    this.chips.forEach((chip, index) => {
      const button = element("button", "lnk-chip", chip.label);
      button.type = "button";
      button.dataset.action = "key";
      button.dataset.chip = String(index);
      button.title = S.lnkSameKey;
      button.setAttribute("aria-label", format(S.lnkSameKeyChip, { type: chip.type, value: chip.value }));
      list.appendChild(button);
    });
    box.appendChild(list);
    return box;
  }

  linkList(state) {
    const list = element("ul", "lnk-list");
    list.setAttribute("aria-label", S.lnkListLabel);
    const best = state.rows[0];
    for (const row of state.rows) {
      const item = element("li", "lnk-row");
      const head = element("div", "lnk-head");
      head.appendChild(element("span", "lnk-kind", row.kindLabel));
      const badge = element("span", "lnk-conf lnk-conf-" + row.tone, format(S.lnkConfidence, { name: row.confidenceLabel }));
      head.appendChild(badge);
      head.appendChild(element("span", "lnk-side lnk-side-" + (row.sensitive === true ? "sensitive" : "normal"), row.sideLabel));
      head.appendChild(element("span", "lnk-muted lnk-mono", [row.time, row.tid, row.code].filter(Boolean).join(" ")));
      item.appendChild(head);
      if (row.member) {
        item.appendChild(element("div", "lnk-muted lnk-mono", row.member));
      }
      item.appendChild(element("div", "lnk-text lnk-mono", row.text));
      const actions = element("div", "lnk-actions");
      actions.appendChild(this.button(revealLabel(row), "reveal", row.entryId));
      const previewing = state.preview.entryId === row.entryId;
      actions.appendChild(this.button(previewing ? S.lnkPreviewHide : S.lnkPreview, "preview", row.entryId));
      if (row === best) {
        const side = this.button(S.lnkSideBySide, "side", row.entryId);
        actions.appendChild(side);
      }
      item.appendChild(actions);
      if (previewing) {
        item.appendChild(this.previewBox(state.preview));
      }
      list.appendChild(item);
    }
    return list;
  }

  button(label, action, id) {
    const button = element("button", "lnk-button", label);
    button.type = "button";
    button.dataset.action = action;
    button.dataset.id = String(id);
    return button;
  }

  previewBox(preview) {
    const box = element("div", "lnk-preview");
    box.setAttribute("role", "status");
    if (preview.status === "loading") {
      box.textContent = S.lnkPreviewLoading;
    } else if (preview.status === "error") {
      box.classList.add("lnk-error");
      box.textContent = S.lnkPreviewError + (preview.error ? ": " + preview.error : "");
    } else {
      box.appendChild(element("pre", "lnk-pre", preview.text));
      if (preview.truncated) {
        box.appendChild(element("div", "lnk-muted", format(S.lnkPreviewMore, { count: PREVIEW_LINES })));
      }
    }
    return box;
  }
}
