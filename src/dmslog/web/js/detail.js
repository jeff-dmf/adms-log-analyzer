// Detail panel: full multi-line text of the selected entry (text nodes only) plus a context list.
import { api } from "./api.js";
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
    this.terms = [];
    this.token = 0;
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
    this.entry = null;
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
    const token = (this.token += 1);
    try {
      const entry = await api.entry(caseId, id);
      if (token === this.token) {
        this.entry = entry;
        if (!keepContext) {
          this.closeContext();
        }
        this.renderEntry(entry);
      }
    } catch (err) {
      if (token === this.token) {
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
    if (!this.entry || caseId === null) {
      return;
    }
    const same = this.refs.contextSame.value;
    try {
      const data = await api.context(caseId, this.entry.id, same, 50, 50);
      this.renderContext(data.rows);
    } catch (err) {
      this.onError(err);
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
    this.refs.contextBox.hidden = true;
    this.refs.contextList.textContent = "";
  }
}
