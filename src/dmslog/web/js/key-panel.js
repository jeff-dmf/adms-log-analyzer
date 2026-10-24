// "Same key" panel: every record that carries one correlation key in all sources of the case, with
// keyset "Load more"; clicking a record selects it in the grid. Texts come from the ERS
// (untrusted): textContent only; the table body has ONE delegated listener.
import { api } from "./api.js";
import { buildChain, chainTruncatedText, createChainFlow } from "./key-chain-model.js";
import { buildKeyRow, createKeyFlow, keyCountText, keyFilter, keyHeading } from "./key-panel-model.js";
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

// host: empty element to fill. Options:
//   getCase()          current case id or null
//   onSelect({id, ts}) select the record in the grid
//   onShowInGrid({type, value, filter})  "Show in grid": the host opens a chained view filtered by the key
//   getSourceName(sourceId)    name of the source as the case lists it (the panel shows only the file name)
//   getTypeLabel(tplId)        readable title of a message type ("" when unknown); the template code is never shown
//   onShowEventInGrid(target)  "Show in grid" of an operation of the chain: target = gridTarget(event) ({from, to, entryId})
//   onOpenOperation(eventId)   opens the operation in the Operations panel
//   onRequestClose()   the dock closes the tab (the default hides the host)
// Returns { open(type, value), close(), reset() }. The panel never shows or hides its host: opening is the dock's.
let chainUid = 0;

export function initKeyPanel(host, { getCase, onSelect, onShowInGrid = null, onShowEventInGrid = null, onOpenOperation = null, getSourceName = null, getTypeLabel = null, onRequestClose = null }) {
  const shell = createPanelShell(host, {
    title: S.keyTitle,
    onRequestClose: onRequestClose || (() => {
      flow.close();
      chainFlow.close();
      host.hidden = true;
    }),
  });
  const heading = element("span", "key-heading");
  shell.summary.appendChild(heading);
  const showButton = element("button", "ui-button key-show", S.keyShowInGrid);
  showButton.type = "button";
  showButton.title = S.keyShowInGridTitle;
  showButton.disabled = true;
  shell.addActions(showButton);
  const status = statusLine(document, "key-status");

  // "Operations and handoffs": hidden unless the key has an operation or a handoff (or is still being read / failed).
  chainUid += 1;
  const chainSection = element("section", "key-chain");
  chainSection.hidden = true;
  const chainTitle = element("h3", "key-chain-title", S.chainTitle || "Operations and handoffs");
  chainTitle.id = "key-chain-title-" + chainUid;
  chainSection.setAttribute("aria-labelledby", chainTitle.id);
  const chainSummaryLine = element("div", "key-chain-summary");
  const chainStatus = statusLine(document, "key-chain-status");
  const chainNote = element("div", "key-chain-note");
  chainNote.hidden = true;
  const chainWrap = element("div", "key-chain-list");
  const chainTable = element("table", "ui-table key-chain-table");
  chainTable.appendChild(element("caption", "visually-hidden", S.chainCaption || "Operations and handoffs of this key, in time order"));
  const chainHead = element("tr");
  for (const label of [
    S.chainColTime || "Time", S.chainColProcess || "Process", S.chainColStep || "Step", S.chainColStatus || "Status",
    S.chainColDuration || "Duration", S.chainColRecords || "Records", S.chainColActions || "Actions",
  ]) {
    const th = element("th", "", label);
    th.scope = "col";
    chainHead.appendChild(th);
  }
  chainTable.appendChild(element("thead")).appendChild(chainHead);
  const chainBody = element("tbody");
  chainTable.appendChild(chainBody);
  chainWrap.appendChild(chainTable);
  chainSection.append(chainTitle, chainSummaryLine, chainStatus, chainNote, chainWrap);
  const wrap = element("div", "key-list");
  const table = element("table", "ui-table key-table");
  table.appendChild(element("caption", "visually-hidden", S.keyTableCaption));
  const tr = element("tr");
  for (const label of [S.keyColTime, S.keyColSource, S.keyColLevel, S.keyColTemplate, S.keyColText]) {
    const th = element("th", "", label);
    th.scope = "col";
    tr.appendChild(th);
  }
  table.appendChild(element("thead")).appendChild(tr);
  const tbody = element("tbody");
  table.appendChild(tbody);
  const moreButton = element("button", "ui-button key-more", S.keyLoadMore);
  moreButton.type = "button";
  wrap.append(table);
  shell.body.append(status, chainSection, wrap);
  shell.addFoot(moreButton); // "Load more" is the main action of a long list: always in sight
  shell.foot.hidden = true;

  let rendered = 0;
  let firstId = null;
  const rows = new Map(); // record id -> {ts}

  const lookups = { getSourceName, getTypeLabel };

  let chainRows = [];

  function chainButton(label, ariaLabel, action) {
    const button = element("button", "ui-button key-chain-action", label);
    button.type = "button";
    button.dataset.action = action;
    button.setAttribute("aria-label", ariaLabel);
    return button;
  }

  function appendChainRow(row, i) {
    const line = element("tr", "key-chain-row is-" + row.kind);
    line.dataset.i = String(i);
    line.appendChild(element("td", "key-mono key-chain-time", row.time));
    const processCell = element("td", "key-chain-process", row.process);
    processCell.title = row.process;
    line.appendChild(processCell);
    const stepCell = element("td", "key-mono key-chain-step", row.step);
    stepCell.title = row.step;
    line.appendChild(stepCell);
    const statusCell = element("td", "key-chain-state");
    if (row.kind === "operation") {
      statusCell.appendChild(element("span", "ev-badge ev-" + row.tone, row.statusText)); // the text carries the meaning
    } else {
      statusCell.textContent = row.statusText;
    }
    line.appendChild(statusCell);
    line.appendChild(element("td", "key-chain-num is-num", row.duration));
    line.appendChild(element("td", "key-chain-num is-num", row.records));
    const actions = element("td", "key-chain-actions");
    if (row.kind === "operation") {
      if (onShowEventInGrid && row.target !== null) {
        actions.appendChild(chainButton(S.chainShowInGrid || "Show in grid",
          format(S.chainShowInGridLabel || "Show operation {name} in grid", { name: row.step }), "grid"));
      }
      if (onOpenOperation && row.eventId !== null && row.eventId !== undefined) {
        actions.appendChild(chainButton(S.chainOpenOperation || "Open operation",
          format(S.chainOpenOperationLabel || "Open operation {name}", { name: row.step }), "operation"));
      }
    } else if (row.entryId !== null) {
      actions.appendChild(chainButton(S.chainGoToRecord || "Go to record",
        format(S.chainGoToRecordLabel || "Go to record {id} of the handoff {name}", { id: row.entryId, name: row.processBare }), "record"));
    }
    line.appendChild(actions);
    chainBody.appendChild(line);
  }

  function onChainState(state) {
    chainBody.textContent = "";
    chainRows = [];
    chainSummaryLine.textContent = "";
    chainNote.hidden = true;
    chainNote.textContent = "";
    chainWrap.hidden = true;
    if (state.status === "idle") {
      chainSection.hidden = true;
      setStatusLine(chainStatus, "", false);
      return;
    }
    if (state.status === "loading") {
      chainSection.hidden = false;
      setStatusLine(chainStatus, S.chainLoading || "Reading operations and handoffs…", false);
      return;
    }
    if (state.status === "error") {
      chainSection.hidden = false;
      setStatusLine(chainStatus, (S.chainError || "Could not read operations and handoffs") + (state.error ? ": " + state.error : ""), true);
      return;
    }
    const chain = buildChain(state.data);
    setStatusLine(chainStatus, "", false);
    if (chain.rows.length === 0) {
      chainSection.hidden = true; // nothing to show: no text either
      return;
    }
    chainSection.hidden = false;
    chainRows = chain.rows;
    chainSummaryLine.textContent = chain.failure ? chain.summary + ". " + chain.failure : chain.summary;
    if (state.data.truncated === true) {
      chainNote.textContent = chainTruncatedText(state.data.scanned);
      chainNote.hidden = false;
    }
    chain.rows.forEach(appendChainRow);
    chainWrap.hidden = false;
  }

  function appendRow(record) {
    const row = buildKeyRow(record, lookups);
    const line = element("tr", "key-row");
    line.dataset.id = String(row.id);
    const timeCell = element("td", "key-mono key-time");
    const open = element("button", "key-open", row.time || String(row.id));
    open.type = "button";
    open.dataset.id = String(row.id);
    open.setAttribute("aria-label", format(S.keyOpenRecord, { id: row.id }));
    timeCell.appendChild(open);
    line.appendChild(timeCell);
    const sourceCell = element("td", "key-source");
    sourceCell.title = row.sourceTitle;
    if (row.sensitive) { // the badge first: a long name is cut at its end, never the badge
      const badge = element("span", "ui-badge is-sensitive key-badge", S.gridSensitiveBadge);
      badge.setAttribute("aria-hidden", "true"); // the cell carries the name: a generic span cannot be named
      sourceCell.setAttribute("aria-label", row.sourceTitle); // like the grid: the full name and "sensitive"
      sourceCell.appendChild(badge);
    }
    sourceCell.appendChild(element("span", "key-source-name", row.source));
    line.appendChild(sourceCell);
    line.appendChild(element("td", "key-level", row.level));
    const typeCell = element("td", "key-type", row.type);
    typeCell.title = row.type;
    line.appendChild(typeCell);
    const textCell = element("td", "key-mono key-text", row.text);
    textCell.title = row.text;
    line.appendChild(textCell);
    tbody.appendChild(line);
    rows.set(String(row.id), row.ts);
  }

  function renderRows(records) {
    const first = records.length > 0 ? records[0].id : null;
    if (records.length < rendered || first !== firstId) {
      tbody.textContent = "";
      rows.clear();
      rendered = 0;
      firstId = first;
    }
    for (let i = rendered; i < records.length; i += 1) {
      appendRow(records[i]);
    }
    rendered = records.length;
  }

  function onState(state) {
    if (state.status === "idle") {
      renderRows([]);
      heading.textContent = "";
      showButton.disabled = true;
      moreButton.hidden = true;
      shell.foot.hidden = true;
      setStatusLine(status, "", false);
      return;
    }
    heading.textContent = state.type === null ? "" : keyHeading(state.type, state.value);
    showButton.disabled = state.type === null || state.caseId === null;
    renderRows(state.records);
    moreButton.hidden = state.next === null || state.status !== "ready";
    shell.foot.hidden = moreButton.hidden;
    moreButton.disabled = state.loadingMore;
    moreButton.textContent = state.loadingMore ? S.keyLoadingMore : S.keyLoadMore;
    const failed = state.status === "error";
    if (state.status === "nocase") {
      setStatusLine(status, S.keyNoCase, false);
    } else if (state.status === "loading") {
      setStatusLine(status, S.keyLoading, false);
    } else if (failed) {
      setStatusLine(status, S.keyError + (state.error ? ": " + state.error : ""), true);
    } else if (state.records.length === 0) {
      setStatusLine(status, S.keyEmpty, false);
    } else {
      setStatusLine(status, keyCountText(state.records.length, state.next !== null), false);
    }
  }

  const flow = createKeyFlow({ api, getCase, onState });
  const chainFlow = createChainFlow({ api, getCase, onState: onChainState });
  chainBody.addEventListener("click", (event) => {
    const button = event.target instanceof Element ? event.target.closest("button") : null;
    const line = button ? button.closest("tr") : null;
    if (!button || !button.dataset.action || !line || !chainBody.contains(line) || chainFlow.state.caseId === null || chainFlow.state.caseId !== getCase()) {
      return; // the ids belong to the case the chain was read for
    }
    const row = chainRows[Number(line.dataset.i)];
    if (!row) {
      return;
    }
    if (button.dataset.action === "grid" && onShowEventInGrid && row.target !== null) {
      onShowEventInGrid(row.target);
    } else if (button.dataset.action === "operation" && onOpenOperation) {
      onOpenOperation(row.eventId);
    } else if (button.dataset.action === "record" && row.entryId !== null) {
      onSelect({ id: row.entryId, ts: row.entryTs });
    }
  });
  tbody.addEventListener("click", (event) => {
    const target = event.target instanceof Element ? event.target.closest("tr") : null;
    if (target && target.dataset.id !== undefined && tbody.contains(target) && flow.state.caseId === getCase()) {
      const ts = rows.get(target.dataset.id);
      onSelect({ id: Number(target.dataset.id), ts: ts === undefined ? null : ts });
    }
  });
  moreButton.addEventListener("click", () => flow.loadMore());
  showButton.addEventListener("click", () => {
    // The key shown now, and only while the case is still the one it was read for.
    if (!onShowInGrid || flow.state.type === null || flow.state.caseId === null || flow.state.caseId !== getCase()) {
      return;
    }
    const { type, value } = flow.state;
    onShowInGrid({ type, value, filter: keyFilter(type, value) });
  });

  return {
    // The chain is read next to the records; one failing does not stop the other.
    open: (type, value) => Promise.all([flow.open(type, value), chainFlow.open(type, value)]).then(() => undefined),
    close: () => {
      flow.close();
      chainFlow.close();
    },
    reset: () => {
      flow.close();
      chainFlow.close();
    },
  };
}
