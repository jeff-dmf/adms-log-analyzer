// "Operations" panel: derived events (GET /api/events) with filters, keyset "Load more" and a
// detail pane (fields, entries, related events, "Show in grid").
// Everything comes from the ERS (untrusted text): textContent and class names only. The table has
// ONE delegated listener; rows are plain elements with a button in the first cell for the keyboard.
import {
  EVENT_KINDS, OPERATION_KIND, STATUS_VALUES, buildDetailView, buildEventRow, createEventsFlow, kindLabel, statusLabel,
} from "./events-model.js";
import { createPayloadNoticeFlow } from "./payload-model.js";
import { createPanelShell, setStatusLine, statusLine } from "./panel-shell.js";
import { S, format, pluralKey } from "./strings.js";

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

// A text badge: the meaning is in the text, the tone class only adds colour.
export function statusBadge(text, tone) {
  return element("span", "ev-badge ev-" + tone, text);
}

function headRow(labels) {
  const tr = element("tr");
  for (const label of labels) {
    const th = element("th", "", label);
    th.scope = "col";
    tr.appendChild(th);
  }
  const thead = element("thead");
  thead.appendChild(tr);
  return thead;
}

let uid = 0;

// container: empty element to fill. Options:
//   api               needs events(caseId, params, signal) and event(caseId, id, signal); entry(caseId, id, signal) for "Same key"
//   getCase()         current case id or null
//   onShowInGrid(t)   t = { from, to, entryId }: set the grid time filter and select the entry
//   onSameKey(type, value)  opens the "Same key" panel for the key of the open event (the button needs api.entry too)
//   onRequestClose()  the dock closes the tab (the default hides the container)
// Returns { refresh(), open(id) }; call refresh() when the case changed or indexing finished.
export function initEventsPanel(container, { api, getCase, onShowInGrid, onSameKey = null, onRequestClose = null }) {
  uid += 1;
  const prefix = "ev" + uid + "-";

  const shell = createPanelShell(container, {
    title: S.evTitle,
    onRequestClose,
    onRefresh: () => {
      notices.clearCache();
      return flow.refresh();
    },
    refreshLabel: S.evRefresh,
  });
  const toolbar = element("div", "ev-toolbar");

  function field(labelText, control, id) {
    const label = element("label", "", labelText);
    control.id = prefix + id;
    label.htmlFor = control.id;
    return [label, control];
  }

  const kindSelect = element("select");
  for (const kind of EVENT_KINDS) {
    const option = element("option", "", kindLabel(kind));
    option.value = kind;
    kindSelect.appendChild(option);
  }
  kindSelect.value = OPERATION_KIND;
  const statusSelect = element("select");
  const anyOption = element("option", "", S.evStatusAny);
  anyOption.value = "";
  statusSelect.appendChild(anyOption);
  for (const status of STATUS_VALUES) {
    const option = element("option", "", statusLabel(status));
    option.value = status;
    statusSelect.appendChild(option);
  }
  const nameInput = element("input", "ev-name");
  nameInput.type = "search";
  nameInput.autocomplete = "off";
  nameInput.placeholder = S.evNamePlaceholder;
  const queryInput = element("input", "ev-query");
  queryInput.type = "search";
  queryInput.autocomplete = "off";
  queryInput.placeholder = S.evSearchPlaceholder;
  toolbar.append(
    ...field(S.evKind, kindSelect, "kind"),
    ...field(S.evStatus, statusSelect, "status"),
    ...field(S.evName, nameInput, "name"),
    ...field(S.evSearch, queryInput, "q"),
  );
  shell.addTools(toolbar);

  const status = statusLine(document, "ev-status");
  const layout = element("div", "ev-layout");
  const listWrap = element("div", "ev-list");
  const table = element("table", "ui-table ev-table");
  table.appendChild(element("caption", "visually-hidden", S.evTableCaption));
  table.appendChild(headRow([
    S.evColTime, S.evColProcess, S.evColName, S.evColKey, S.evColDuration, S.evColStatus, S.evColEntries,
  ]));
  const tbody = element("tbody");
  table.appendChild(tbody);
  const moreButton = element("button", "ui-button ev-more", S.evLoadMore);
  moreButton.type = "button";
  listWrap.append(table);
  shell.addFoot(moreButton); // "Load more" is the main action of a long list: always in sight
  shell.foot.hidden = true;
  const detailPane = element("section", "ev-detail");
  detailPane.setAttribute("aria-label", S.evDetailTitle);
  detailPane.tabIndex = -1;
  layout.append(listWrap, detailPane);
  shell.body.append(status, layout);

  // Rows are appended incrementally; a changed first row or a shorter list rebuilds the table.
  let rendered = 0;
  let firstRenderedId = null;
  const rowById = new Map();
  let selectedRow = null;

  function appendRow(event) {
    const row = buildEventRow(event);
    const tr = element("tr", "ev-row");
    tr.dataset.id = String(row.id);
    const timeCell = element("td", "ev-time");
    const open = element("button", "ev-open", row.time || row.kindLabel);
    open.type = "button";
    open.dataset.id = String(row.id);
    open.setAttribute("aria-label", format(S.evOpenRelated, { kind: row.kindLabel, name: row.name }));
    timeCell.appendChild(open);
    tr.appendChild(timeCell);
    tr.appendChild(element("td", "is-text", row.process));
    const nameCell = element("td", "ev-mono is-text ev-name-cell", row.name);
    nameCell.title = row.name;
    tr.appendChild(nameCell);
    const keyCell = element("td", "ev-mono is-text ev-key", row.key);
    keyCell.title = row.key;
    tr.appendChild(keyCell);
    tr.appendChild(element("td", "ev-num is-num", row.durationText));
    const statusCell = element("td");
    statusCell.appendChild(statusBadge(row.statusLabel, row.tone));
    tr.appendChild(statusCell);
    tr.appendChild(element("td", "ev-num is-num", row.entries));
    tbody.appendChild(tr);
    rowById.set(String(row.id), tr);
  }

  function renderRows(events) {
    const first = events.length > 0 ? events[0].id : null;
    if (events.length < rendered || first !== firstRenderedId) {
      tbody.textContent = "";
      rowById.clear();
      selectedRow = null;
      rendered = 0;
      firstRenderedId = first;
    }
    for (let i = rendered; i < events.length; i += 1) {
      appendRow(events[i]);
    }
    rendered = events.length;
  }

  function markSelected(id) {
    if (selectedRow) {
      selectedRow.removeAttribute("aria-current");
      selectedRow.classList.remove("is-selected");
      selectedRow = null;
    }
    const tr = id === null ? null : rowById.get(String(id)) || null;
    if (tr) {
      tr.setAttribute("aria-current", "true");
      tr.classList.add("is-selected");
      selectedRow = tr;
    }
  }

  function relatedList(rows) {
    const wrap = element("div", "ev-related");
    wrap.appendChild(element("h3", "", S.evRelatedTitle));
    if (rows.length === 0) {
      wrap.appendChild(element("div", "ev-muted", S.evRelatedNone));
      return wrap;
    }
    const list = element("ul", "ev-related-list");
    for (const row of rows) {
      const item = element("li");
      const button = element("button", "ev-related-open");
      button.type = "button";
      button.dataset.related = String(row.id);
      button.append(
        element("span", "ev-muted", row.kindLabel + " "),
        element("span", "ev-mono", row.name + " "),
        element("span", "ev-muted", row.time + " "),
        statusBadge(row.statusLabel, row.tone),
      );
      item.appendChild(button);
      list.appendChild(item);
    }
    wrap.appendChild(list);
    return wrap;
  }

  function entriesTable(view) {
    const wrap = element("div", "ev-entries");
    wrap.appendChild(element("h3", "", S.evEntriesTitle));
    if (view.entries.length === 0) {
      wrap.appendChild(element("div", "ev-muted", S.evEntriesNone));
      return wrap;
    }
    if (view.entriesPartial) {
      wrap.appendChild(element("div", "ev-muted", format(S[pluralKey("evEntriesPartial", view.entriesTotal)], {
        shown: view.entries.length, total: view.entriesTotal,
      })));
    }
    const entries = element("table", "ui-table ev-table ev-entry-table");
    entries.appendChild(headRow([S.evEntryColTime, S.evEntryColTid, S.evEntryColLevel, S.evEntryColText]));
    const body = element("tbody");
    for (const entry of view.entries) {
      const tr = element("tr", entry.sev !== null && entry.sev !== undefined ? "ev-sev-" + entry.sev : "");
      tr.appendChild(element("td", "ev-mono", entry.time));
      tr.appendChild(element("td", "ev-num", entry.tid));
      tr.appendChild(element("td", "", entry.level));
      tr.appendChild(element("td", "ev-mono ev-text", entry.text));
      body.appendChild(tr);
    }
    entries.appendChild(body);
    wrap.appendChild(entries);
    return wrap;
  }

  function renderDetail(state) {
    const current = state.detail;
    notices.cancel();
    noticeBox = null;
    detailPane.textContent = "";
    if (current.status === "none") {
      detailPane.appendChild(element("div", "ev-muted", S.evDetailNone));
      return;
    }
    if (current.status === "loading") {
      detailPane.appendChild(element("div", "ev-muted", S.evDetailLoading));
      return;
    }
    if (current.status === "error") {
      detailPane.appendChild(element("div", "ev-error", S.evDetailError + (current.error ? ": " + current.error : "")));
      return;
    }
    const view = buildDetailView(current.data);
    if (view === null) {
      detailPane.appendChild(element("div", "ev-muted", S.evDetailNone));
      return;
    }
    const head = element("div", "ev-detail-head");
    head.appendChild(element("h3", "ev-mono", view.title));
    head.appendChild(statusBadge(view.statusLabel, view.tone));
    if (view.target !== null) {
      const show = element("button", "ev-show", S.evShowInGrid);
      show.type = "button";
      show.dataset.action = "show";
      head.appendChild(show);
    }
    const eventKey = current.data.event.key;
    if (onSameKey && eventKey !== null && eventKey !== undefined && eventKey !== "") {
      const same = element("button", "ev-show ev-samekey", S.evSameKey || "Same key");
      same.type = "button";
      same.dataset.action = "samekey";
      same.disabled = true; // enabled once the type of the key is known
      head.appendChild(same);
      resolveKeyType(same, current.data.event);
    }
    detailPane.appendChild(head);
    const dl = element("dl", "ev-fields");
    for (const item of view.fields) {
      if (item.value === "") {
        continue;
      }
      dl.appendChild(element("dt", "", item.label));
      dl.appendChild(element("dd", "ev-mono", item.value));
    }
    noticeBox = element("div", "ev-notice");
    noticeBox.setAttribute("role", "status");
    noticeBox.hidden = true;
    detailPane.append(noticeBox, dl, entriesTable(view), relatedList(view.related));
    notices.check(current.data.event);
  }

  // The event does not say the type of its key: the first one (alphabetical) of its starting record whose value is that key.
  let keyTypeToken = 0;
  async function resolveKeyType(button, event) {
    keyTypeToken += 1;
    const token = keyTypeToken;
    const caseId = getCase();
    let type = null;
    if (caseId && event.start_entry_id && typeof api.entry === "function") {
      try {
        const data = await api.entry(caseId, event.start_entry_id);
        const keys = data && Array.isArray(data.keys) ? data.keys : [];
        const types = keys
          .filter((item) => item && item.key_type !== null && item.key_type !== undefined && String(item.value) === String(event.key))
          .map((item) => String(item.key_type))
          .sort();
        type = types.length > 0 ? types[0] : null;
      } catch (error) {
        type = null;
      }
    }
    if (token !== keyTypeToken || !detailPane.contains(button)) {
      return;
    }
    if (type === null) {
      button.title = S.evSameKeyNone || "No record of this operation carries its key as a correlation key.";
      return;
    }
    button.dataset.keyType = type;
    button.disabled = false;
  }

  function setStatus(text, isError) {
    setStatusLine(status, text, isError);
  }

  function onState(state) {
    renderRows(state.events);
    markSelected(state.selectedId);
    moreButton.hidden = state.next === null || state.status !== "ready";
    shell.foot.hidden = moreButton.hidden;
    moreButton.disabled = state.loadingMore;
    moreButton.textContent = state.loadingMore ? S.evLoadingMore : S.evLoadMore;
    if (state.status === "nocase") {
      setStatus(S.evNoCase, false);
    } else if (state.status === "loading") {
      setStatus(S.evLoading, false);
    } else if (state.status === "error") {
      setStatus(S.evError + (state.error ? ": " + state.error : ""), true);
    } else if (state.events.length === 0) {
      setStatus(S.evEmpty, false);
    } else {
      setStatus(format(S[pluralKey(state.next === null ? "evCount" : "evCountMore", state.events.length)], { count: state.events.length }), false);
    }
    const detailKey = state.detail.status + ":" + String(state.selectedId);
    if (detailKey !== renderedDetailKey) {
      renderedDetailKey = detailKey;
      renderDetail(state);
    }
  }
  let renderedDetailKey = null;

  const flow = createEventsFlow({ api, getCase, onState });

  // Payload expected / not expected notice of an operation (filled after the detail is drawn).
  let noticeBox = null;
  const notices = createPayloadNoticeFlow({
    api,
    getCase,
    onResult: (notice) => {
      if (noticeBox) {
        noticeBox.textContent = notice ? notice.text : "";
        noticeBox.className = "ev-notice" + (notice && notice.kind === "absent" ? " ev-notice-absent" : "");
        noticeBox.hidden = notice === null;
      }
    },
  });

  tbody.addEventListener("click", (event) => {
    const target = event.target instanceof Element ? event.target.closest("tr[data-id]") : null;
    if (target && tbody.contains(target)) {
      flow.select(Number(target.dataset.id));
    }
  });
  detailPane.addEventListener("click", (event) => {
    if (!(event.target instanceof Element)) {
      return;
    }
    const related = event.target.closest("button[data-related]");
    if (related) {
      flow.select(Number(related.dataset.related));
      return;
    }
    const same = event.target.closest("button");
    if (same && same.dataset.action === "samekey") {
      const key = flow.state.detail.data && flow.state.detail.data.event ? flow.state.detail.data.event.key : null;
      if (onSameKey && !same.disabled && same.dataset.keyType && key && flow.state.caseId !== null && flow.state.caseId === getCase()) {
        onSameKey(same.dataset.keyType, String(key));
      }
      return;
    }
    if (event.target.closest("button[data-action=show]")) {
      const view = buildDetailView(flow.state.detail.data);
      // The ids belong to the case the detail was loaded for.
      if (view && view.target && flow.state.caseId !== null && flow.state.caseId === getCase()) {
        onShowInGrid(view.target);
      }
    }
  });
  kindSelect.addEventListener("change", () => flow.setFilters({ kind: kindSelect.value }));
  statusSelect.addEventListener("change", () => flow.setFilters({ status: statusSelect.value }));
  nameInput.addEventListener("change", () => flow.setFilters({ name: nameInput.value }));
  queryInput.addEventListener("change", () => flow.setFilters({ q: queryInput.value }));
  moreButton.addEventListener("click", () => flow.loadMore());

  return {
    refresh: () => {
      notices.clearCache();
      return flow.refresh();
    },
    // Opens one event (from another panel): a kind or name filter in force is kept as is.
    open: (id) => flow.select(id),
  };
}
