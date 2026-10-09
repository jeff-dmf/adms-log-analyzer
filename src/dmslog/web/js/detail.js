// Detail panel: full multi-line text of the selected entry (text nodes only) plus a context list.
import { api } from "./api.js";
import { RequestGuard, canLoadContext } from "./detail-model.js";
import { renderHighlighted } from "./search.js";
import { S, format } from "./strings.js";
import { formatTs } from "./time.js";

export class Detail {
  constructor(refs, { getCase, getSourceName, onError }) {
    this.refs = refs;
    this.getCase = getCase;
    this.getSourceName = getSourceName;
    this.onError = onError;
    this.entry = null;
    this.entryCase = null;
    this.terms = [];
    this.entryGuard = new RequestGuard();
    this.contextGuard = new RequestGuard();
    refs.contextButton.addEventListener("click", () => this.loadContext());
    refs.contextClose.addEventListener("click", () => this.closeContext());
    refs.copyButton.addEventListener("click", () => this.copyText());
    this.clear();
  }

  setTerms(terms) {
    this.terms = terms;
    if (this.entry) {
      this.renderEntry(this.entry);
    }
  }

  clear() {
    this.entryGuard.invalidate();
    this.contextGuard.invalidate();
    this.entry = null;
    this.entryCase = null;
    this.refs.meta.textContent = "";
    this.refs.text.textContent = S.detailEmpty;
    this.refs.actions.hidden = true;
    this.closeContext();
  }

  async show(id, keepContext = false) {
    const caseId = this.getCase();
    if (caseId === null) {
      return;
    }
    this.contextGuard.invalidate();
    const ticket = this.entryGuard.begin(caseId, id);
    try {
      const entry = await api.entry(caseId, id, ticket.signal);
      if (this.entryGuard.isCurrent(ticket, this.getCase(), id) && entry && String(entry.id) === String(id)) {
        this.entry = entry;
        this.entryCase = ticket.caseId;
        if (!keepContext) {
          this.closeContext();
        }
        this.renderEntry(entry);
      }
    } catch (err) {
      if (this.entryGuard.isCurrent(ticket, this.getCase(), id) && !(err && err.name === "AbortError")) {
        this.onError(err);
      }
    }
  }

  renderEntry(entry) {
    this.refs.meta.textContent = format(S.detailMeta, {
      time: formatTs(entry.ts),
      pid: entry.pid === null || entry.pid === undefined ? "-" : entry.pid,
      tid: entry.tid === null || entry.tid === undefined ? "-" : "0x" + Number(entry.tid).toString(16),
      level: S.levelNames[entry.level] || "-",
      source: this.getSourceName(entry.source_id),
    });
    renderHighlighted(this.refs.text, entry.text === undefined || entry.text === null ? "" : String(entry.text), this.terms);
    this.refs.actions.hidden = false;
  }

  async copyText() {
    if (!this.entry || !navigator.clipboard) {
      return;
    }
    try {
      await navigator.clipboard.writeText(String(this.entry.text));
      this.refs.copyStatus.textContent = S.copied;
    } catch (err) {
      this.refs.copyStatus.textContent = "";
    }
  }

  async loadContext() {
    const caseId = this.getCase();
    if (!canLoadContext(this.entry, this.entryCase, caseId)) {
      return;
    }
    const entryId = this.entry.id;
    const same = this.refs.contextSame.value;
    const ticket = this.contextGuard.begin(caseId, entryId);
    try {
      const data = await api.context(caseId, entryId, same, 50, 50, ticket.signal);
      if (this.contextGuard.isCurrent(ticket, this.getCase(), this.entry ? this.entry.id : null)) {
        this.renderContext(data.rows);
      }
    } catch (err) {
      if (this.contextGuard.isCurrent(ticket, this.getCase(), this.entry ? this.entry.id : null) &&
          !(err && err.name === "AbortError")) {
        this.onError(err);
      }
    }
  }

  renderContext(rows) {
    const list = this.refs.contextList;
    list.textContent = "";
    let current = null;
    for (const row of rows) {
      const item = document.createElement("li");
      item.className = "context-row";
      item.tabIndex = 0;
      if (this.entry && row.id === this.entry.id) {
        item.classList.add("current");
        current = item;
      }
      const time = document.createElement("span");
      time.className = "context-time";
      time.textContent = formatTs(row.ts);
      const message = document.createElement("span");
      message.className = "context-msg";
      message.textContent = row.msg === null || row.msg === undefined ? "" : String(row.msg);
      item.appendChild(time);
      item.appendChild(message);
      const open = () => this.show(row.id, true);
      item.addEventListener("click", open);
      item.addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
          open();
        }
      });
      list.appendChild(item);
    }
    this.refs.contextBox.hidden = false;
    if (current) {
      current.scrollIntoView({ block: "center" });
    }
  }

  closeContext() {
    this.contextGuard.invalidate(); // a pending context answer must not reopen the panel
    this.refs.contextBox.hidden = true;
    this.refs.contextList.textContent = "";
  }
}
