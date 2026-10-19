// "Message queue" panel of the dock (issue #74, docs/notas/fila-mensagens-desenho.md section 5): the pending messages of the
// adapters' LocalPersistenceStore databases, read only when the user asked for it. A header with the counters, the
// databases with their warnings, filters, a keyset list ("Load more") and, for the selected item, a preview of the
// message XML with the fixed reconstruction label. Everything comes from a database of the ERS (untrusted): textContent
// and class names only; one delegated listener per table. The rules and the loading are in queue-model.js.
import {
  FORMS, STATE_FILTERS, countLine, createQueueFlow, emptyKind, emptyText, headerText, isFiltered, rowLabel,
} from "./queue-model.js";
import { createPanelShell, setStatusLine, statusLine } from "./panel-shell.js";
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

function within(root, node) {
  for (let n = node; n; n = n.parentNode || null) {
    if (n === root) {
      return true;
    }
  }
  return false;
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
//   api                  needs queueSummary(caseId, signal), queueItems(caseId, params, signal),
//                        queuePreview(caseId, id, form, signal) and queueRemove(caseId, group)
//   getCase()            current case id or null
//   getCollections()     the collections of the case as [{group, tag}] (the "Read message queue…" button asks for one when
//                        there are several); default none
//   onShowInLog(messageId)      records that carry this message ID ("Show in log")
//   onShowCacheRecords(itemId)  records of the cache item (the im_id key)
//   onOpenPayload(payloadId)    the payload viewer on the logged payload of the same message
//   onReadQueue(group)   the host starts the reading job (group = a collection, or null: the host chooses) and then tells
//                        the panel through setReading(true) / setReading(false) + refresh()
//   onRequestClose()     the dock closes the tab (the default hides the container)
// Returns { refresh(), reset(), setReading(on) }: refresh() when the case's data changed, reset() when the case changed.
export function initQueuePanel(container, {
  api, getCase, getCollections = () => [], onShowInLog = () => {}, onShowCacheRecords = () => {}, onOpenPayload = () => {},
  onReadQueue = () => {}, onRequestClose = null,
}) {
  uid += 1;
  const prefix = "mq" + uid + "-";
  const shell = createPanelShell(container, {
    title: S.mqTitle,
    onRequestClose,
    onRefresh: () => flow.refresh(),
    refreshLabel: S.mqRefresh,
  });

  // ---- head: the counters and "Remove queue data…"
  const removeButton = element("button", "ui-button is-ghost mq-remove", S.mqRemoveButton);
  removeButton.type = "button";
  shell.addActions(removeButton);

  // ---- tools: filters
  const tools = element("div", "mq-toolbar");
  function selectWith(id, labelText) {
    const wrap = element("span", "mq-field");
    const label = element("label", "", labelText);
    const select = element("select");
    select.id = prefix + id;
    label.htmlFor = select.id;
    wrap.append(label, select);
    tools.appendChild(wrap);
    return { wrap, select };
  }
  const stateField = selectWith("state", S.mqFilterState);
  for (const value of STATE_FILTERS) {
    const option = element("option", "", S.mqFilterNames[value]);
    option.value = value;
    stateField.select.appendChild(option);
  }
  const typeField = selectWith("type", S.mqFilterType);
  const collectionField = selectWith("collection", S.mqFilterCollection);
  const dbField = selectWith("db", S.mqFilterDb);
  const searchForm = element("span", "mq-field mq-search");
  const searchLabel = element("label", "", S.mqSearchLabel);
  const searchInput = element("input", "mq-search-input");
  searchInput.type = "text";
  searchInput.id = prefix + "q";
  searchInput.setAttribute("autocomplete", "off");
  searchInput.placeholder = S.mqSearchPlaceholder;
  searchLabel.htmlFor = searchInput.id;
  const searchButton = element("button", "ui-button mq-search-go", S.mqSearchButton);
  searchButton.type = "button";
  searchForm.append(searchLabel, searchInput, searchButton);
  tools.appendChild(searchForm);
  shell.addTools(tools);

  // ---- body
  const status = statusLine(document, "mq-status");
  const notice = element("p", "mq-notice", S.mqTimeNotice);
  const confirmBox = element("div", "mq-confirm");
  confirmBox.setAttribute("role", "alertdialog");
  confirmBox.setAttribute("aria-label", S.mqRemoveLabel);
  confirmBox.hidden = true;
  const confirmText = element("p", "mq-confirm-text", S.mqRemoveConfirm);
  const confirmYes = element("button", "ui-button is-danger mq-confirm-yes", S.mqRemoveYes);
  confirmYes.type = "button";
  const confirmNo = element("button", "ui-button mq-confirm-no", S.mqRemoveCancel);
  confirmNo.type = "button";
  confirmBox.append(confirmText, confirmYes, confirmNo);

  const emptyBox = element("section", "mq-empty");
  emptyBox.hidden = true;
  const emptyMessage = element("p", "mq-empty-text");
  const readLabel = element("label", "mq-read-label", S.mqReadWhich);
  const readSelect = element("select", "mq-read-select");
  readSelect.id = prefix + "read";
  readLabel.htmlFor = readSelect.id;
  const readButton = element("button", "ui-button is-primary mq-read", S.mqReadButton);
  readButton.type = "button";
  emptyBox.append(emptyMessage, readLabel, readSelect, readButton);

  const banksBox = element("section", "mq-banks");
  banksBox.setAttribute("aria-label", S.mqDbsTitle);
  banksBox.tabIndex = -1;
  const banksList = element("ul", "mq-bank-list");
  const othersBox = element("details", "mq-others");
  const othersSummary = element("summary", "");
  const othersList = element("ul", "mq-bank-list");
  othersBox.append(othersSummary, othersList);
  banksBox.append(element("h3", "", S.mqDbsTitle), banksList, othersBox);

  const main = element("div", "mq-main");
  const listBox = element("section", "mq-list");
  listBox.setAttribute("aria-label", S.mqListTitle);
  listBox.tabIndex = -1;
  const table = element("table", "ui-table mq-table");
  table.setAttribute("role", "grid");
  table.appendChild(element("caption", "visually-hidden", S.mqListCaption));
  const thead = element("thead");
  table.appendChild(thead);
  const tbody = element("tbody");
  table.appendChild(tbody);
  const moreNote = element("div", "ui-error mq-more-error");
  moreNote.hidden = true;
  listBox.append(table, moreNote);

  const previewBox = element("section", "mq-preview");
  previewBox.setAttribute("aria-label", S.mqPreviewRegion);
  previewBox.hidden = true;
  const previewHead = element("div", "mq-preview-head");
  const previewTitle = element("h3", "mq-preview-title");
  previewTitle.tabIndex = -1;
  const sensitiveBadge = element("span", "mq-badge", S.mqSensitive);
  const closePreviewButton = element("button", "ui-button is-ghost mq-preview-close", S.mqPreviewClose);
  closePreviewButton.type = "button";
  previewHead.append(previewTitle, sensitiveBadge, closePreviewButton);
  const actions = element("div", "mq-actions");
  const showLogButton = element("button", "ui-button mq-show-log", S.mqShowLog);
  const showCacheButton = element("button", "ui-button mq-show-cache", S.mqShowCache);
  const payloadButton = element("button", "ui-button mq-open-payload", S.mqOpenPayload);
  const copyButton = element("button", "ui-button mq-copy", S.mqCopy);
  for (const b of [showLogButton, showCacheButton, payloadButton, copyButton]) {
    b.type = "button";
    actions.appendChild(b);
  }
  const formGroup = element("div", "mq-forms");
  formGroup.setAttribute("role", "radiogroup");
  formGroup.setAttribute("aria-label", S.mqFormLabel);
  const formButtons = {};
  for (const form of FORMS) {
    const b = element("button", "ui-button mq-form", S.mqFormNames[form]);
    b.type = "button";
    b.setAttribute("role", "radio");
    b.dataset.form = form;
    formButtons[form] = b;
    formGroup.appendChild(b);
  }
  const banner = element("div", "mq-banner");
  const bannerLabel = element("p", "mq-banner-label");
  const bannerBasis = element("p", "mq-banner-basis");
  const bannerNotes = element("ul", "mq-banner-notes");
  banner.append(bannerLabel, bannerBasis, bannerNotes);
  const previewMessage = element("p", "mq-preview-message");
  const previewPre = element("pre", "mq-xml");
  previewPre.tabIndex = 0;
  previewPre.setAttribute("role", "region");
  previewPre.setAttribute("aria-label", S.mqPreviewText);
  previewBox.append(previewHead, actions, formGroup, banner, previewMessage, previewPre);
  main.append(listBox, previewBox);
  shell.body.append(status, notice, confirmBox, emptyBox, banksBox, main);
  const moreButton = element("button", "ui-button mq-more", S.mqLoadMore);
  moreButton.type = "button";
  shell.addFoot(moreButton);
  shell.foot.hidden = true;

  // ---- state -> DOM
  const rowsById = new Map();
  const nodesById = new Map();
  let renderedVersion = -1;
  let rendered = 0;
  let showCollection = null;
  let tabId = null; // the row that holds the tab stop
  let wantFocusId = null;
  let copyNote = "";
  let lastStatus = "";

  function setStatus(text, isError) {
    const key = (isError ? "!" : "") + text;
    if (key !== lastStatus) {
      lastStatus = key;
      setStatusLine(status, text, isError);
    }
  }

  function fillSelect(select, options, value) {
    const key = JSON.stringify(options);
    if (select.dataset.key !== key) {
      select.dataset.key = key;
      select.textContent = "";
      for (const [optValue, label] of options) {
        const option = element("option", "", label);
        option.value = optValue;
        select.appendChild(option);
      }
    }
    select.value = value;
  }

  function renderHead(state) {
    const view = state.summary;
    shell.summary.textContent = view ? headerText(view) : "";
    removeButton.disabled = !view || (view.dbs.length === 0 && view.items === 0) || state.removing;
    removeButton.hidden = !view;
  }

  function renderTools(state) {
    const view = state.summary;
    const filters = state.filters;
    const hasList = view !== null && view.queues.length > 0;
    tools.hidden = !hasList;
    shell.tools.hidden = !hasList;
    stateField.select.value = filters.state;
    fillSelect(typeField.select, [["", S.mqAny], ...state.types.map((t) => [t, t])].concat(
      filters.type !== "" && !state.types.includes(filters.type) ? [[filters.type, filters.type]] : []), filters.type);
    const collections = view ? view.collections : [];
    collectionField.wrap.hidden = collections.length < 2;
    fillSelect(collectionField.select, [["", S.mqAny], ...collections.map((c) => [c.id, c.tag || c.id])], filters.collection);
    const queues = view ? view.queues : [];
    dbField.wrap.hidden = queues.length < 2;
    fillSelect(dbField.select, [["", S.mqAny], ...queues.map((d) => [String(d.id), [d.tag, d.name].filter((x) => x !== "").join(" ")])], String(filters.db));
    if (document.activeElement !== searchInput) {
      searchInput.value = filters.q;
    }
  }

  function renderBanks(state) {
    const view = state.summary;
    banksBox.hidden = !view || view.dbs.length === 0;
    banksList.textContent = "";
    othersList.textContent = "";
    if (!view) {
      return;
    }
    const addBank = (list, db) => {
      const li = element("li", "mq-bank" + (db.status === "error" ? " is-error" : ""));
      li.appendChild(element("span", "mq-bank-line", db.summary));
      if (db.status === "error") {
        li.appendChild(element("span", "mq-bank-flag is-error", S.mqErrorPrefix + " " + db.error));
      }
      for (const w of db.warnings) {
        li.appendChild(element("span", "mq-bank-flag", S.mqWarningPrefix + " " + w));
      }
      list.appendChild(li);
    };
    for (const db of view.queues) {
      addBank(banksList, db);
    }
    for (const db of view.others) {
      addBank(othersList, db);
    }
    othersBox.hidden = view.others.length === 0;
    othersSummary.textContent = format(S.mqOthers, { count: view.others.length });
  }

  function renderEmpty(state, kind) {
    emptyBox.hidden = kind === "" || kind === "filtered" || state.status === "error";
    emptyMessage.textContent = emptyText(kind);
    const canRead = kind === "disabled";
    readButton.hidden = !canRead;
    const collections = canRead ? getCollections() : [];
    readLabel.hidden = !canRead || collections.length < 2;
    readSelect.hidden = readLabel.hidden;
    if (!readSelect.hidden) {
      fillSelect(readSelect, collections.map((c) => [String(c.group), c.tag || String(c.group)]), readSelect.value || String(collections[0].group));
    }
    readButton.disabled = state.reading;
  }

  function buildHeader(withCollection) {
    const labels = [S.mqColTime];
    if (withCollection) {
      labels.push(S.mqColCollection);
    }
    labels.push(S.mqColType, S.mqColState, S.mqColMessageId, S.mqColItemId, S.mqColSize, S.mqColInLog);
    thead.textContent = "";
    thead.appendChild(headRow(labels).children[0]);
  }

  function appendRow(row, withCollection) {
    const tr = element("tr", "mq-row");
    tr.dataset.id = String(row.id);
    tr.setAttribute("aria-label", rowLabel(row));
    tr.setAttribute("aria-selected", "false");
    tr.tabIndex = -1;
    tr.appendChild(element("td", "mq-mono mq-time", row.time));
    if (withCollection) {
      tr.appendChild(element("td", "is-text", row.tag));
    }
    tr.appendChild(element("td", "is-text", row.type));
    tr.appendChild(element("td", "is-text mq-state mq-state-" + row.state, row.stateLabel));
    const idCell = element("td", "mq-mono is-text", row.messageShort);
    idCell.title = row.messageId;
    tr.appendChild(idCell);
    tr.appendChild(element("td", "mq-mono is-num", row.itemId));
    tr.appendChild(element("td", "is-num", row.sizeText));
    tr.appendChild(element("td", "is-num", row.inLog));
    tbody.appendChild(tr);
    rowsById.set(String(row.id), row);
    nodesById.set(String(row.id), tr);
  }

  function renderList(state, hasList) {
    listBox.hidden = !hasList;
    const withCollection = Boolean(state.summary) && state.summary.collections.length > 1;
    if (wantFocusId !== null && !within(tbody, document.activeElement) && document.activeElement !== listBox) {
      wantFocusId = null; // the user moved the focus elsewhere meanwhile: a late answer must not take it back
    }
    if (state.version !== renderedVersion || state.items.length < rendered || withCollection !== showCollection) {
      const active = document.activeElement;
      if (within(tbody, active) && active.dataset && active.dataset.id !== undefined) {
        wantFocusId = active.dataset.id;
      }
      tbody.textContent = "";
      rowsById.clear();
      nodesById.clear();
      rendered = 0;
      renderedVersion = state.version;
      if (withCollection !== showCollection) {
        showCollection = withCollection;
        buildHeader(withCollection);
      }
    }
    for (let i = rendered; i < state.items.length; i += 1) {
      appendRow(state.items[i], withCollection);
    }
    rendered = state.items.length;
    table.hidden = state.items.length === 0;
    // The tab stop: the selected row, else the one that had it, else the first.
    const ids = state.items.map((row) => String(row.id));
    if (state.selectedId !== null && ids.includes(String(state.selectedId))) {
      tabId = String(state.selectedId);
    } else if (tabId === null || !ids.includes(tabId)) {
      tabId = ids.length ? ids[0] : null;
    }
    for (const [id, tr] of nodesById) {
      tr.tabIndex = id === tabId ? 0 : -1;
      tr.setAttribute("aria-selected", state.selectedId !== null && id === String(state.selectedId) ? "true" : "false");
      tr.classList.toggle("is-selected", state.selectedId !== null && id === String(state.selectedId));
    }
    if (wantFocusId !== null) {
      const same = nodesById.get(wantFocusId);
      if (same) {
        same.focus();
        wantFocusId = null;
      } else if (state.status === "ready") {
        listBox.focus();
        wantFocusId = null;
      }
    }
  }

  function setAction(button, enabled, whyNot, label) {
    button.setAttribute("aria-disabled", enabled ? "false" : "true");
    button.title = enabled ? "" : whyNot;
    button.setAttribute("aria-label", label);
  }

  function renderPreview(state) {
    const row = state.selectedId === null ? null : rowsById.get(String(state.selectedId)) || null;
    previewBox.hidden = row === null;
    if (row === null) {
      return;
    }
    const preview = state.preview;
    previewTitle.textContent = format(S.mqPreviewTitle, { id: row.itemId });
    setAction(showLogButton, row.canShowLog, S.mqNotInLog, format(S.mqShowLogLabel, { id: row.itemId }));
    setAction(showCacheButton, row.canShowCache, S.mqNotInCache, format(S.mqShowCacheLabel, { id: row.itemId }));
    payloadButton.hidden = row.payloadId === null;
    payloadButton.setAttribute("aria-label", format(S.mqOpenPayloadLabel, { id: row.itemId }));
    copyButton.setAttribute("aria-disabled", preview.status === "ready" && preview.view.kind === "ok" ? "false" : "true");
    for (const form of FORMS) {
      const on = form === preview.form;
      formButtons[form].setAttribute("aria-checked", on ? "true" : "false");
      formButtons[form].tabIndex = on ? 0 : -1;
    }
    const view = preview.status === "ready" ? preview.view : null;
    const isWire = preview.form === "wire";
    // The label is on screen for every wire preview, whatever the answer says (it is a constant of the form).
    banner.hidden = !isWire && !(view && view.kind === "ok");
    bannerLabel.textContent = isWire ? S.mqReconstructionLabel : S.mqStoredNote;
    bannerBasis.textContent = view && view.basis ? view.basis : "";
    bannerBasis.hidden = bannerBasis.textContent === "";
    bannerNotes.textContent = "";
    for (const note of view ? view.notes : []) {
      bannerNotes.appendChild(element("li", "", note));
    }
    bannerNotes.hidden = !view || view.notes.length === 0;
    let message = "";
    let isError = false;
    if (preview.status === "loading") {
      message = S.mqPreviewLoading;
    } else if (preview.status === "error") {
      message = S.mqPreviewError + (preview.error ? ": " + preview.error : "");
      isError = true;
    } else if (view && view.kind !== "ok") {
      message = view.message;
      isError = view.kind === "decode_error";
    } else if (view && view.truncated) {
      message = S.mqTruncated;
    }
    previewMessage.textContent = message;
    previewMessage.hidden = message === "";
    previewMessage.classList.toggle("ui-error", isError);
    previewPre.textContent = view && view.kind === "ok" ? view.text : "";
    previewPre.hidden = !(view && view.kind === "ok");
    sensitiveBadge.hidden = false;
  }

  function statusFor(state) {
    if (copyNote !== "") {
      return [copyNote, false];
    }
    if (state.removeError) {
      return [S.mqRemoveError + ": " + state.removeError, true];
    }
    if (state.removing) {
      return [S.mqRemoving, false];
    }
    if (state.status === "nocase") {
      return [S.mqNoCase, false];
    }
    if (state.status === "loading") {
      return [S.mqLoading, false];
    }
    if (state.status === "error") {
      return [S.mqLoadError + (state.error ? ": " + state.error : ""), true];
    }
    if (state.moreError) {
      return [S.mqMoreError + ": " + state.moreError, true];
    }
    if (state.refreshError) {
      return [S.mqRefreshError + ": " + state.refreshError, true];
    }
    if (state.reading) {
      return [S.mqReading, false];
    }
    if (state.removed) {
      return [format(S.mqRemoved, { dbs: state.removed.dbs.toLocaleString("en-US"), items: state.removed.items.toLocaleString("en-US") }), false];
    }
    const kind = emptyKind(state.summary, state.items.length, isFiltered(state.filters));
    if (kind !== "") {
      return [emptyText(kind), false];
    }
    return [countLine(state.items.length, state.next !== null), false];
  }

  function onState(state) {
    const hadMoreFocus = document.activeElement === moreButton;
    const kind = state.status === "ready" || state.summary ? emptyKind(state.summary, state.items.length, isFiltered(state.filters)) : "";
    const hasList = state.summary !== null && state.summary.queues.length > 0 && state.summary.items > 0;
    renderHead(state);
    renderTools(state);
    renderBanks(state);
    renderEmpty(state, kind);
    renderList(state, hasList);
    renderPreview(state);
    notice.hidden = !hasList;
    const more = state.next !== null && state.status === "ready";
    moreButton.hidden = !more;
    shell.foot.hidden = !more;
    moreButton.setAttribute("aria-disabled", state.loadingMore ? "true" : "false");
    moreButton.textContent = state.loadingMore ? S.mqLoadingMore : state.moreError ? S.mqRetry : S.mqLoadMore;
    moreNote.hidden = !state.moreError;
    moreNote.textContent = state.moreError ? S.mqMoreError + ": " + state.moreError : "";
    if (hadMoreFocus && !more) {
      listBox.focus(); // the button went away (the end of the list): the focus stays in the list
    }
    const [text, isError] = statusFor(state);
    setStatus(text, isError);
  }

  const flow = createQueueFlow({ api, getCase, onState });

  // ---- events
  function rowOf(target) {
    const tr = target instanceof Element ? target.closest(".mq-row") : null;
    return tr && tbody.contains(tr) ? tr : null;
  }
  function openRow(tr) {
    const id = Number(tr.dataset.id);
    copyNote = "";
    tabId = tr.dataset.id;
    flow.select(id);
  }
  tbody.addEventListener("click", (event) => {
    const tr = rowOf(event.target);
    if (tr) {
      openRow(tr);
    }
  });
  tbody.addEventListener("keydown", (event) => {
    const tr = rowOf(event.target);
    if (!tr) {
      return;
    }
    const trs = flow.state.items.map((row) => nodesById.get(String(row.id))).filter(Boolean);
    const at = trs.indexOf(tr);
    let to = null;
    if (event.key === "ArrowDown") {
      to = trs[Math.min(at + 1, trs.length - 1)];
    } else if (event.key === "ArrowUp") {
      to = trs[Math.max(at - 1, 0)];
    } else if (event.key === "Home") {
      to = trs[0];
    } else if (event.key === "End") {
      to = trs[trs.length - 1];
    } else if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      openRow(tr);
      return;
    }
    if (to) {
      event.preventDefault();
      tabId = to.dataset.id;
      for (const [id, node] of nodesById) {
        node.tabIndex = id === tabId ? 0 : -1;
      }
      to.focus();
    }
  });

  function closePreview() {
    const id = flow.state.selectedId;
    copyNote = "";
    flow.closePreview();
    const tr = id === null ? null : nodesById.get(String(id));
    (tr || listBox).focus(); // the focus goes back to the row that opened the preview
  }
  closePreviewButton.addEventListener("click", closePreview);
  previewBox.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      closePreview();
    }
  });

  const selectedRow = () => (flow.state.selectedId === null ? null : rowsById.get(String(flow.state.selectedId)) || null);
  const enabled = (button) => button.getAttribute("aria-disabled") !== "true";
  showLogButton.addEventListener("click", () => {
    const row = selectedRow();
    if (row && enabled(showLogButton) && flow.state.caseId === getCase()) {
      onShowInLog(row.messageId);
    }
  });
  showCacheButton.addEventListener("click", () => {
    const row = selectedRow();
    if (row && enabled(showCacheButton) && flow.state.caseId === getCase()) {
      onShowCacheRecords(row.itemId);
    }
  });
  payloadButton.addEventListener("click", () => {
    const row = selectedRow();
    if (row && row.payloadId !== null && flow.state.caseId === getCase()) {
      onOpenPayload(row.payloadId);
    }
  });
  copyButton.addEventListener("click", async () => {
    const preview = flow.state.preview;
    if (!enabled(copyButton) || preview.status !== "ready" || preview.view.kind !== "ok") {
      return;
    }
    try {
      if (!navigator.clipboard) {
        throw new Error("clipboard_unavailable");
      }
      await navigator.clipboard.writeText(preview.view.text); // only the XML: the label is not part of the copy
      copyNote = preview.form === "wire" ? S.mqCopiedReconstruction : S.mqCopied;
    } catch (error) {
      copyNote = S.mqCopyFailed;
    }
    const [text, isError] = statusFor(flow.state);
    setStatus(text, isError);
  });
  formGroup.addEventListener("click", (event) => {
    const button = event.target instanceof Element ? event.target.closest(".mq-form") : null;
    if (button && formGroup.contains(button)) {
      copyNote = "";
      flow.setForm(button.dataset.form);
    }
  });
  formGroup.addEventListener("keydown", (event) => {
    const keys = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 };
    if (!(event.key in keys)) {
      return;
    }
    event.preventDefault();
    const at = FORMS.indexOf(flow.state.preview.form);
    const form = FORMS[(at + keys[event.key] + FORMS.length) % FORMS.length];
    copyNote = "";
    flow.setForm(form).then(() => formButtons[form].focus());
    formButtons[form].focus();
  });

  stateField.select.addEventListener("change", () => flow.setFilters({ state: stateField.select.value }));
  typeField.select.addEventListener("change", () => flow.setFilters({ type: typeField.select.value }));
  collectionField.select.addEventListener("change", () => flow.setFilters({ collection: collectionField.select.value }));
  dbField.select.addEventListener("change", () => flow.setFilters({ db: dbField.select.value }));
  const search = () => flow.setFilters({ q: searchInput.value });
  searchButton.addEventListener("click", search);
  searchInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      search();
    }
  });
  moreButton.addEventListener("click", () => {
    if (moreButton.getAttribute("aria-disabled") !== "true") {
      flow.loadMore();
    }
  });
  readButton.addEventListener("click", () => {
    if (readButton.disabled) {
      return;
    }
    const collections = getCollections();
    onReadQueue(collections.length > 1 && !readSelect.hidden ? Number(readSelect.value) : null);
  });

  // "Remove queue data…": two steps, like the removal of a collection.
  removeButton.addEventListener("click", () => {
    confirmBox.hidden = false;
    confirmNo.focus();
  });
  confirmNo.addEventListener("click", () => {
    confirmBox.hidden = true;
    removeButton.focus();
  });
  confirmBox.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      confirmBox.hidden = true;
      removeButton.focus();
    }
  });
  confirmYes.addEventListener("click", async () => {
    confirmBox.hidden = true;
    copyNote = "";
    removeButton.focus();
    await flow.removeData();
  });

  return {
    refresh: () => flow.refresh(),
    // Another case: the filters go back to their defaults and everything is read for the new case.
    reset: () => {
      copyNote = "";
      confirmBox.hidden = true;
      return flow.reset();
    },
    setReading: (on) => flow.setReading(on),
  };
}
