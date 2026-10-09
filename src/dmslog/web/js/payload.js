// "Payload" section of the entry detail: formatted / raw text, copy buttons, size and attribute
// chips, the request <-> response pair side by side, and links to the operation and to the
// announcement. Everything comes from the ERS (untrusted): textContent only, the text goes into
// <pre> elements; ONE delegated click listener on the host. Large payloads are rendered in chunks.
import { api } from "./api.js";
import { revealLabel } from "./links-model.js";
import {
  CHUNK_CHARS, announcementTarget, buildChips, carrierTarget, confidenceText, createPayloadFlow, displayView, splitChunks,
} from "./payload-model.js";
import { S, format } from "./strings.js";

const STYLESHEET = "/css/payload.css";

function ensureStylesheet() {
  if (!document.querySelector('link[rel="stylesheet"][href="' + STYLESHEET + '"]')) {
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = STYLESHEET;
    document.head.appendChild(link);
  }
}

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

// A <pre> that appends the text chunk by chunk (first chunk now, the rest on demand or when the
// reader scrolls near the end), so a huge payload never blocks the page.
class ChunkedPre {
  constructor(text, label) {
    this.chunks = splitChunks(text);
    this.next = 0;
    this.box = element("div", "pl-text-box");
    this.pre = element("pre", "pl-pre");
    this.pre.tabIndex = 0;
    if (label) {
      this.pre.setAttribute("aria-label", label);
    }
    this.more = element("button", "pl-more");
    this.more.type = "button";
    this.more.dataset.action = "more";
    this.box.append(this.pre, this.more);
    this.pre.addEventListener("scroll", () => this.maybeLoad());
    this.more.addEventListener("click", () => this.append());
    this.append();
  }

  append() {
    if (this.next >= this.chunks.length) {
      return;
    }
    this.pre.appendChild(document.createTextNode(this.chunks[this.next]));
    this.next += 1;
    this.updateMore();
  }

  remaining() {
    let chars = 0;
    for (let i = this.next; i < this.chunks.length; i += 1) {
      chars += this.chunks[i].length;
    }
    return chars;
  }

  updateMore() {
    const left = this.next < this.chunks.length;
    this.more.hidden = !left;
    if (left) {
      this.more.textContent = format(S.plShowMore, { remaining: this.remaining() });
    }
  }

  maybeLoad() {
    const pre = this.pre;
    if (this.next < this.chunks.length && pre.scrollTop + pre.clientHeight >= pre.scrollHeight - CHUNK_CHARS / 100) {
      this.append();
    }
  }
}

// host: element to fill (hidden while the entry has no payload). Options:
//   getCase()                   current case id or null
//   onReveal({ id, ts })        select a record in the grid
//   onGoOperation(eventId)      open the operation in the Operations panel
//   onError(err)
export class PayloadSection {
  constructor(host, { getCase, onReveal, onGoOperation, onError }) {
    this.host = host;
    this.getCase = getCase;
    this.onReveal = onReveal || (() => {});
    this.onGoOperation = onGoOperation || (() => {});
    this.onError = onError || (() => {});
    this.entry = null;
    this.copyNote = "";
    this.renderedKey = null;
    this.flow = createPayloadFlow({ api, getCase, onState: (state) => this.render(state) });
    host.addEventListener("click", (event) => this.handleClick(event));
    this.clear();
  }

  clear() {
    this.entry = null;
    this.copyNote = "";
    this.renderedKey = null;
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
    this.copyNote = "";
    this.renderedKey = null;
    this.flow.load(entry.id);
  }

  selectedMeta() {
    const state = this.flow.state;
    const item = state.items.find((candidate) => candidate.id === state.selectedId);
    return item ? item.meta : null;
  }

  async copy(which) {
    const state = this.flow.state;
    const view = displayView(state.detail.data, state.mode);
    const value = view ? (which === "raw" ? view.raw : view.formatted) : null;
    if (value === null || !navigator.clipboard) {
      return;
    }
    try {
      await navigator.clipboard.writeText(value);
      this.copyNote = S.plCopied;
    } catch (error) {
      this.copyNote = S.plCopyFailed;
    }
    const note = this.host.querySelector(".pl-copy-note");
    if (note) {
      note.textContent = this.copyNote;
    }
  }

  handleClick(event) {
    if (!(event.target instanceof Element) || !this.entry) {
      return;
    }
    const button = event.target.closest("button[data-action]");
    if (!button || !this.host.contains(button)) {
      return;
    }
    const state = this.flow.state;
    const action = button.dataset.action;
    const meta = this.selectedMeta();
    if (action === "select") {
      this.copyNote = "";
      this.flow.select(Number(button.dataset.id));
    } else if (action === "mode") {
      this.flow.setMode(button.dataset.mode);
    } else if (action === "copy-raw" || action === "copy-formatted") {
      this.copy(action === "copy-raw" ? "raw" : "formatted");
    } else if (action === "pair") {
      this.flow.togglePair();
    } else if (action === "operation" && meta && meta.event_id !== null && meta.event_id !== undefined &&
               state.caseId === this.getCase()) {
      this.onGoOperation(meta.event_id);
    } else if (action === "announcement") {
      const target = announcementTarget(meta, this.entry.id);
      if (target) {
        this.onReveal(target);
      }
    } else if (action === "carrier") {
      const target = carrierTarget(meta, this.entry.id);
      if (target) {
        this.onReveal(target);
      }
    }
  }

  button(label, action, className) {
    const node = element("button", className || "pl-button", label);
    node.type = "button";
    node.dataset.action = action;
    return node;
  }

  render(state) {
    const host = this.host;
    if (!this.entry || state.status === "idle" || (state.status === "ready" && state.items.length === 0)) {
      host.hidden = true;
      host.textContent = "";
      this.renderedKey = null;
      return;
    }
    // Same payload, same mode, same data: nothing to rebuild (the state is emitted for every step).
    const key = [
      state.status, state.selectedId, state.mode, state.detail.status, state.pair.status, state.pair.open,
      state.items.length,
    ].join("|");
    if (key === this.renderedKey) {
      return;
    }
    this.renderedKey = key;
    ensureStylesheet();
    host.hidden = false;
    host.textContent = "";
    host.appendChild(element("h3", "pl-title", S.plTitle));
    if (state.status === "loading") {
      host.appendChild(element("div", "pl-muted", S.plLoading));
      return;
    }
    if (state.status === "error") {
      host.appendChild(element("div", "pl-error", S.plError + (state.error ? ": " + state.error : "")));
      return;
    }
    if (state.items.length > 1 || state.items.some((item) => item.announced)) {
      host.appendChild(this.itemList(state));
    }
    const meta = this.selectedMeta();
    if (meta === null) {
      return;
    }
    host.appendChild(this.header(state, meta));
    if (state.detail.status === "loading") {
      host.appendChild(element("div", "pl-muted", S.plLoadingText));
    } else if (state.detail.status === "error") {
      host.appendChild(element("div", "pl-error", S.plTextError + (state.detail.error ? ": " + state.detail.error : "")));
    } else if (state.detail.status === "ready") {
      host.appendChild(this.body(state, meta));
    }
  }

  itemList(state) {
    const list = element("div", "pl-items");
    list.setAttribute("role", "group");
    list.setAttribute("aria-label", S.plListLabel);
    for (const item of state.items) {
      const label = item.announced ? item.label + " (" + S.plAnnounced + ")" : item.label;
      const node = this.button(label, "select", "pl-item");
      node.dataset.id = String(item.id);
      node.setAttribute("aria-pressed", item.id === state.selectedId ? "true" : "false");
      list.appendChild(node);
    }
    return list;
  }

  header(state, meta) {
    const box = element("div", "pl-header");
    const chips = element("div", "pl-chips");
    for (const chip of buildChips(meta)) {
      chips.appendChild(element("span", "pl-chip pl-chip-" + chip.field, chip.label));
    }
    if (meta.sensitive === true) {
      chips.appendChild(element("span", "pl-badge", S.plSensitiveBadge));
    }
    box.appendChild(chips);
    const links = element("div", "pl-links");
    const carrier = carrierTarget(meta, this.entry.id);
    if (carrier) {
      links.appendChild(this.button(revealLabel({ sensitive: meta.sensitive }), "carrier"));
    }
    if (meta.event_id !== null && meta.event_id !== undefined) {
      links.appendChild(element("span", "pl-muted", format(S.plLinkOperation, { confidence: confidenceText(meta.event_conf) })));
      links.appendChild(this.button(S.plGoOperation, "operation"));
    } else {
      links.appendChild(element("span", "pl-muted", S.plNoOperation));
    }
    if (announcementTarget(meta, this.entry.id)) {
      links.appendChild(this.button(S.plGoAnnouncement, "announcement"));
    }
    box.appendChild(links);
    return box;
  }

  body(state, meta) {
    const data = state.detail.data;
    const view = displayView(data, state.mode);
    const box = element("div", "pl-body");
    if (view === null) {
      return box;
    }
    const bar = element("div", "pl-bar");
    const modes = element("span", "pl-modes");
    modes.setAttribute("role", "group");
    modes.setAttribute("aria-label", S.plViewLabel);
    for (const [mode, label] of [["formatted", S.plFormatted], ["raw", S.plRaw]]) {
      const node = this.button(label, "mode", "pl-mode");
      node.dataset.mode = mode;
      node.setAttribute("aria-pressed", view.shown === mode ? "true" : "false");
      if (mode === "formatted" && !view.formattedUsable) {
        node.disabled = true;
      }
      modes.appendChild(node);
    }
    bar.appendChild(modes);
    bar.appendChild(this.button(S.plCopyRaw, "copy-raw"));
    const copyFormatted = this.button(S.plCopyFormatted, "copy-formatted");
    copyFormatted.disabled = !view.formattedUsable;
    bar.appendChild(copyFormatted);
    if (data.pair) {
      bar.appendChild(this.button(state.pair.open ? S.plHidePair : S.plShowPair, "pair"));
    }
    bar.appendChild(element("span", "pl-muted pl-copy-note", this.copyNote));
    box.appendChild(bar);
    box.appendChild(element("div", "pl-muted pl-status", view.statusParts.join(" - ")));
    if (view.text.length > 200000) {
      box.appendChild(element("div", "pl-muted", S.plLargeNote));
    }
    const panes = element("div", state.pair.open ? "pl-panes pl-panes-two" : "pl-panes");
    panes.appendChild(this.pane(format(S.plPaneThis, { id: meta.id, role: meta.role }), view.text, state.pair.open));
    if (state.pair.open) {
      panes.appendChild(this.pairPane(state));
    }
    box.appendChild(panes);
    return box;
  }

  pane(title, text, withTitle) {
    const pane = element("div", "pl-pane");
    if (withTitle) {
      pane.appendChild(element("div", "pl-pane-title", title));
    }
    pane.appendChild(new ChunkedPre(text === "" ? S.plNoText : text, title).box);
    return pane;
  }

  pairPane(state) {
    const pair = state.pair;
    if (pair.status === "loading") {
      return element("div", "pl-pane pl-muted", S.plPairLoading);
    }
    if (pair.status === "error") {
      return element("div", "pl-pane pl-error", S.plPairError + (pair.error ? ": " + pair.error : ""));
    }
    const view = displayView(pair.data, state.mode);
    if (view === null) {
      return element("div", "pl-pane pl-muted", S.plPairLoading);
    }
    const meta = pair.data.payload;
    const pane = this.pane(format(S.plPanePair, { id: meta.id, role: meta.role }), view.text, true);
    pane.appendChild(element("div", "pl-muted pl-status", view.statusParts.join(" - ")));
    return pane;
  }
}
