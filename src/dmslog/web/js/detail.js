// Detail panel: full multi-line text of the selected entry (text nodes only) plus a context list. The header, the
// ghost action buttons and the collapsible "Parsed fields" section follow the investigation order of UI-20.
import { api } from "./api.js";
import { RequestGuard, canLoadContext, createDetailPrefs, headerParts } from "./detail-model.js";
import { renderHighlighted } from "./search.js";
import { S, format } from "./strings.js";
import { formatTs } from "./time.js";

// ---- collapsible sections (UI-20): an h3 with a real button (aria-expanded), the state remembered ----------------
// The sections re-render from scratch, so every render calls sectionHeading() again and the stored state is applied.
let shared = null;

function safeStorage() {
  try {
    return window.localStorage || null;
  } catch (err) {
    return null;
  }
}

// The one preferences object of the page (created on first use; tests replace it with setDetailPrefs).
export function detailPrefs() {
  if (shared === null) {
    shared = createDetailPrefs(safeStorage());
  }
  return shared;
}

export function setDetailPrefs(prefs) {
  shared = prefs;
}

function applyState(host, open) {
  if (typeof host.setAttribute === "function") {
    host.setAttribute("data-collapsed", open ? "false" : "true");
  }
}

// Builds the heading button inside `heading` (an h3) and wires the toggle. Text only.
function wire(host, heading, button, key, prefs) {
  let open = prefs.sectionOpen(key);
  const paint = () => {
    button.setAttribute("aria-expanded", open ? "true" : "false");
    applyState(host, open);
  };
  button.addEventListener("click", () => {
    open = !open;
    prefs.setSectionOpen(key, open);
    paint();
  });
  paint();
  heading.appendChild(button);
  return heading;
}

// A new heading for a section the module renders itself. className keeps the module's own heading class.
export function sectionHeading(host, key, title, className, prefs = detailPrefs()) {
  const heading = document.createElement("h3");
  heading.className = "ds-heading" + (className ? " " + className : "");
  const button = document.createElement("button");
  button.type = "button";
  button.className = "ds-toggle";
  button.textContent = title;
  return wire(host, heading, button, key, prefs);
}

// Turns the existing h3 of a section built elsewhere (the bookmark note) into the same collapsible heading.
// Call once after the section is built; false when there is no h3.
export function adoptHeading(host, key, prefs = detailPrefs()) {
  const heading = host.querySelector("h3");
  if (!heading || heading.querySelector("button")) {
    return false;
  }
  const title = heading.textContent;
  heading.className = (heading.className ? heading.className + " " : "") + "ds-heading";
  heading.textContent = "";
  const button = document.createElement("button");
  button.type = "button";
  button.className = "ds-toggle";
  button.textContent = title;
  wire(host, heading, button, key, prefs);
  return true;
}

export class Detail {
  constructor(refs, {
    getCase, getSourceName, getSourceInfo, getCompareLabel, onError, getTemplateCode, onTemplate, onFields, onEntry, isBookmarked, onBookmarkToggle,
  }) {
    this.refs = refs;
    this.getCase = getCase;
    this.getSourceName = getSourceName;
    // { name, sensitive, tag } of a source; without it only the name is known.
    this.getSourceInfo = getSourceInfo || ((id) => ({ name: getSourceName(id) }));
    this.getCompareLabel = getCompareLabel || (() => S.cmpButton);
    this.isBookmarked = isBookmarked || (() => false);
    this.onBookmarkToggle = onBookmarkToggle || (() => {});
    this.onError = onError;
    this.getTemplateCode = getTemplateCode || (() => "");
    this.onTemplate = onTemplate || (() => {});
    this.onFields = onFields || (() => {});
    this.onEntry = onEntry || (() => {}); // entry fetched (or null when cleared): Knowledge section
    this.entry = null;
    this.entryCase = null;
    this.terms = [];
    this.entryGuard = new RequestGuard();
    this.contextGuard = new RequestGuard();
    refs.contextButton.addEventListener("click", () => this.loadContext());
    refs.contextClose.addEventListener("click", () => this.closeContext());
    refs.copyButton.addEventListener("click", () => this.copyText());
    this.paintActions();
    if (refs.bookmarkButton) {
      refs.bookmarkButton.addEventListener("click", () => {
        if (this.entry) {
          this.onBookmarkToggle(this.entry.id);
        }
      });
    }
    if (refs.template) {
      refs.template.addEventListener("click", () => {
        if (this.entry && this.entry.tpl_id > 0) {
          this.onTemplate(this.entry.tpl_id);
        }
      });
    }
    this.clear();
  }

  setTerms(terms) {
    this.terms = terms;
    if (this.entry) {
      this.renderEntry(this.entry);
    }
  }

  // Ghost buttons: icon (decorative) plus a short label that never wraps.
  setButton(button, icon, label) {
    if (!button) {
      return;
    }
    button.textContent = "";
    const mark = document.createElement("span");
    mark.className = "dt-icon";
    mark.setAttribute("aria-hidden", "true");
    mark.textContent = icon;
    const text = document.createElement("span");
    text.className = "dt-label";
    text.textContent = label;
    button.appendChild(mark);
    button.appendChild(text);
  }

  paintActions() {
    this.setButton(this.refs.contextButton, S.detailIconContext, S.detailBtnContext);
    this.setButton(this.refs.copyButton, S.detailIconCopy, S.detailBtnCopy);
    this.setButton(this.refs.bookmarkButton, S.detailIconBookmarkOff, S.detailBtnBookmark);
    this.setButton(this.refs.highlightButton, S.detailIconHighlight, S.detailBtnHighlight);
  }

  // The compare button's label comes from the compare model (it says "Compare with record N" while one is picked).
  paintCompare() {
    this.setButton(this.refs.compareButton, S.detailIconCompare, this.getCompareLabel(this.entry));
  }

  // Bookmark star: pressed state and glyph follow the shared store (main.js calls this on every change).
  refreshBookmark() {
    const button = this.refs.bookmarkButton;
    if (!button) {
      return;
    }
    const on = Boolean(this.entry) && Boolean(this.isBookmarked(this.entry.id));
    button.setAttribute("aria-pressed", on ? "true" : "false");
    this.setButton(button, on ? S.detailIconBookmarkOn : S.detailIconBookmarkOff, S.detailBtnBookmark);
    button.title = on ? S.bmToggleOff : S.bmToggleOn;
  }

  clear() {
    this.entryGuard.invalidate();
    this.contextGuard.invalidate();
    this.entry = null;
    this.entryCase = null;
    this.refs.meta.textContent = "";
    this.refs.meta.hidden = Boolean(this.refs.empty);
    if (this.refs.empty) {
      // No record: one calm sentence instead of an empty text box and bare headings.
      this.refs.empty.textContent = S.detailEmpty;
      this.refs.empty.hidden = false;
      this.refs.text.textContent = "";
      this.refs.text.hidden = true;
    } else {
      this.refs.text.textContent = S.detailEmpty;
    }
    this.refs.actions.hidden = true;
    this.paintCompare();
    if (this.refs.fields) {
      this.refs.fields.textContent = "";
    }
    if (this.refs.template) {
      this.refs.template.hidden = true;
    }
    this.closeContext();
    this.onEntry(null);
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
        this.onEntry(entry);
      }
    } catch (err) {
      if (this.entryGuard.isCurrent(ticket, this.getCase(), id) && !(err && err.name === "AbortError")) {
        this.onError(err);
      }
    }
  }

  // Header: time (mono, 600), level badge in the severity colour (the level name is the text, colour only reinforces),
  // source name with the `sensitive` badge, then PID, TID and collection as muted facts.
  renderHeader(entry) {
    const box = this.refs.meta;
    box.textContent = "";
    box.hidden = false;
    const parts = headerParts(entry, this.getSourceInfo(entry.source_id) || {}, { formatTime: formatTs, levelNames: S.levelNames });
    const span = (className, text) => {
      const node = document.createElement("span");
      node.className = className;
      node.textContent = text;
      return node;
    };
    const first = document.createElement("div");
    first.className = "dt-row";
    first.appendChild(span("dt-time", parts.time));
    if (parts.level !== "") {
      first.appendChild(span("ui-badge dt-level" + (parts.sevClass ? " " + parts.sevClass : ""), parts.level));
    }
    if (parts.source !== "") {
      first.appendChild(span("dt-source", parts.source));
    }
    if (parts.sensitive) {
      first.appendChild(span("ui-badge is-sensitive dt-sensitive", S.sourceSensitive));
    }
    box.appendChild(first);
    const facts = [];
    if (parts.pid !== null) {
      facts.push(format(S.detailPid, { pid: parts.pid }));
    }
    if (parts.tid !== null) {
      facts.push(format(S.detailTid, { tid: parts.tid }));
    }
    if (parts.tag !== "") {
      facts.push(format(S.detailCollection, { tag: parts.tag }));
    }
    if (facts.length > 0) {
      const second = document.createElement("div");
      second.className = "dt-row dt-facts";
      for (const fact of facts) {
        second.appendChild(span("dt-fact", fact));
      }
      box.appendChild(second);
    }
  }

  renderEntry(entry) {
    this.renderHeader(entry);
    if (this.refs.empty) {
      this.refs.empty.hidden = true;
      this.refs.text.hidden = false;
    }
    this.refreshBookmark();
    this.paintCompare();
    renderHighlighted(this.refs.text, entry.text === undefined || entry.text === null ? "" : String(entry.text), this.terms);
    this.refs.actions.hidden = false;
    this.renderTemplate(entry);
    this.renderFields(entry);
  }

  renderTemplate(entry) {
    const button = this.refs.template;
    if (!button) {
      return;
    }
    const known = entry.tpl_id > 0;
    button.hidden = !known;
    button.textContent = known ? entry.tpl_title || this.getTemplateCode(entry.tpl_id) || String(entry.tpl || entry.tpl_id) : "";
    button.title = known && entry.tpl ? String(entry.tpl) : ""; // the code stays as technical detail
  }

  // Parsed fields as a name/value table; every value is placed as a text node.
  renderFields(entry) {
    const box = this.refs.fields;
    if (!box) {
      return;
    }
    box.textContent = "";
    const fields = entry.fields && typeof entry.fields === "object" ? entry.fields : {};
    const names = Object.keys(fields);
    this.onFields(names);
    if (names.length === 0) {
      return;
    }
    box.appendChild(sectionHeading(box, "fields", S.detailFieldsTitle, "ds-fields-title"));
    const table = document.createElement("table");
    table.className = "fields-table";
    table.setAttribute("aria-label", S.detailFieldsTitle);
    const head = document.createElement("tr");
    for (const label of [S.detailFieldName, S.detailFieldValue]) {
      const th = document.createElement("th");
      th.textContent = label;
      head.appendChild(th);
    }
    table.appendChild(head);
    for (const name of names) {
      const value = fields[name];
      const row = document.createElement("tr");
      const key = document.createElement("td");
      key.textContent = name;
      const cell = document.createElement("td");
      cell.textContent = value === null || value === undefined ? "" : typeof value === "object" ? JSON.stringify(value) : String(value);
      row.appendChild(key);
      row.appendChild(cell);
      table.appendChild(row);
    }
    box.appendChild(table);
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
