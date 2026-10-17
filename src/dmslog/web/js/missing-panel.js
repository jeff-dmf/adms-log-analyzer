// "Missing steps" panel (ROADMAP 9.12): the events whose expected sequence did not close, with the step that is missing
// and why. A summary by kind, family and name on top (with the causes), the keyset list below, each item opening the
// event in the grid. Everything comes from the ERS (untrusted text): textContent and class names only; one delegated
// listener per table. The rules and the loading are in missing-model.js.
import { kindLabel, statusLabel } from "./events-model.js";
import {
  CAUSES, MISSING_KINDS, MISSING_STATUSES, causeLabel, countText, createMissingFlow, summaryRows, summaryTotal,
} from "./missing-model.js";
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

function within(root, node) {
  for (let n = node; n; n = n.parentNode || null) {
    if (n === root) {
      return true;
    }
  }
  return false;
}

let uid = 0;

// container: empty element to fill. Options:
//   api               needs missingEvents(caseId, params, signal) and missingSummary(caseId, params, signal)
//   getCase()         current case id or null
//   onShowInGrid(t)   t = { from, to, entryId }: set the grid time filter and select the entry
//   onRequestClose()  the dock closes the tab (the default hides the container)
// Returns { refresh(), reset() }: refresh() when the case's events changed, reset() when the case changed.
export function initMissingPanel(container, { api, getCase, onShowInGrid, onRequestClose = null }) {
  uid += 1;
  const prefix = "ms" + uid + "-";
  const shell = createPanelShell(container, {
    title: S.msTitle,
    onRequestClose,
    onRefresh: () => flow.refresh(),
    refreshLabel: S.msRefresh,
  });

  // ---- tools: the three selectors, and the name filter chosen from the summary
  const tools = element("div", "ms-toolbar");
  function selectWith(id, labelText, anyText, values, labelOf) {
    const label = element("label", "", labelText);
    const select = element("select");
    select.id = prefix + id;
    label.htmlFor = select.id;
    const any = element("option", "", anyText);
    any.value = "";
    select.appendChild(any);
    for (const value of values) {
      const option = element("option", "", labelOf(value));
      option.value = value;
      select.appendChild(option);
    }
    tools.append(label, select);
    return select;
  }
  const kindSelect = selectWith("kind", S.msKind, S.msKindAny, MISSING_KINDS, kindLabel);
  const statusSelect = selectWith("status", S.msStatus, S.evStatusAny, MISSING_STATUSES, statusLabel);
  const causeSelect = selectWith("cause", S.msCause, S.msCauseAny, CAUSES, causeLabel);
  const nameTag = element("span", "ms-name-tag");
  nameTag.hidden = true;
  const nameText = element("span", "ms-mono");
  const nameClear = element("button", "ui-button is-ghost ms-name-clear", S.msClearName);
  nameClear.type = "button";
  nameTag.append(element("span", "ev-muted", S.msNameFilter + " "), nameText, nameClear);
  tools.appendChild(nameTag);
  shell.addTools(tools);

  // ---- body: status line, summary, list
  const status = statusLine(document, "ms-status");
  const summaryBox = element("section", "ms-summary");
  summaryBox.setAttribute("aria-label", S.msSummaryTitle);
  summaryBox.tabIndex = -1;
  const listBox = element("section", "ms-list");
  listBox.setAttribute("aria-label", S.msListTitle);
  listBox.tabIndex = -1;
  const summaryHead = element("h3", "", S.msSummaryTitle);
  const summaryNote = element("div", "ev-muted ms-note");
  const summaryTable = element("table", "ui-table ms-table ms-summary-table");
  summaryTable.appendChild(element("caption", "visually-hidden", S.msSummaryCaption));
  summaryTable.appendChild(headRow([S.msColKind, S.msColFamily, S.msColName, S.msColMissing, S.msColCount, S.msColCauses, ""]));
  const summaryBody = element("tbody");
  summaryTable.appendChild(summaryBody);
  summaryBox.append(summaryHead, summaryNote, summaryTable);
  const listHead = element("h3", "", S.msListTitle);
  const moreNote = element("div", "ui-error ms-more-error");
  moreNote.hidden = true;
  const listTable = element("table", "ui-table ms-table ms-list-table");
  listTable.appendChild(element("caption", "visually-hidden", S.msListCaption));
  listTable.appendChild(headRow([S.msColTime, S.msColProcess, S.msColKind, S.msColName, S.msColMissing, S.msColCause, S.msColStatus]));
  const listBody = element("tbody");
  listTable.appendChild(listBody);
  listBox.append(listHead, listTable, moreNote);
  shell.body.append(status, summaryBox, listBox);
  const moreButton = element("button", "ui-button ms-more", S.msLoadMore);
  moreButton.type = "button";
  shell.addFoot(moreButton);
  shell.foot.hidden = true;

  // ---- list rows: appended while the list only grows, rebuilt when its version changes
  let renderedVersion = -1;
  let rendered = 0;
  function appendItem(row) {
    const tr = element("tr", "ms-row");
    tr.dataset.id = String(row.id);
    const timeCell = element("td", "ev-time");
    const open = element("button", "ms-open", row.time || row.kindLabel);
    open.type = "button";
    open.dataset.id = String(row.id);
    open.setAttribute("aria-label", format(S.msOpenLabel, { kind: row.kindLabel, name: row.name }));
    if (row.target === null) {
      open.setAttribute("aria-disabled", "true");
      open.title = S.msNoTime;
    }
    timeCell.appendChild(open);
    tr.appendChild(timeCell);
    tr.appendChild(element("td", "is-text", row.process));
    tr.appendChild(element("td", "is-text", row.kindLabel));
    const nameCell = element("td", "ev-mono is-text", row.name);
    nameCell.title = row.name;
    tr.appendChild(nameCell);
    tr.appendChild(element("td", "is-text", row.missing));
    const causeCell = element("td", "is-text");
    causeCell.appendChild(element("span", "", row.causeLabel));
    causeCell.appendChild(element("span", "ms-proof" + (row.proven ? " is-proven" : ""), row.proven ? S.msProven : S.msNotProven));
    tr.appendChild(causeCell);
    const statusCell = element("td");
    statusCell.appendChild(element("span", "ev-badge ev-" + row.tone, row.statusLabel));
    tr.appendChild(statusCell);
    listBody.appendChild(tr);
    rowsById.set(String(row.id), row);
  }
  const rowsById = new Map();

  function renderList(state) {
    if (state.version !== renderedVersion || state.items.length < rendered) {
      const active = document.activeElement;
      if (within(listBody, active) && active.dataset && active.dataset.id !== undefined) {
        wantFocus = active.dataset.id;
      } else if (active !== listBox) {
        wantFocus = null; // the focus is somewhere else: nothing to give back
      }
      listBody.textContent = "";
      rowsById.clear();
      rendered = 0;
      renderedVersion = state.version;
    }
    for (let i = rendered; i < state.items.length; i += 1) {
      appendItem(state.items[i]);
    }
    rendered = state.items.length;
    listTable.hidden = state.items.length === 0;
    if (wantFocus !== null) {
      // The rebuild removed the focused button: give the focus to the same event's button once it is listed again (a
      // refresh empties the list first), meanwhile to the list itself.
      const same = listBody.children.map((tr) => tr.children[0].children[0]).find((b) => b && b.dataset.id === wantFocus);
      if (same) {
        same.focus();
        wantFocus = null;
      } else {
        listBox.focus();
        if (state.status === "ready") {
          wantFocus = null;
        }
      }
    }
  }
  let wantFocus = null;

  // ---- summary rows
  let summaryKey = null;
  function renderSummary(state) {
    const summary = state.summary;
    const rows = summary.status === "ready" ? summaryRows(summary.groups, state.filters.cause) : [];
    const key = JSON.stringify([summary.status, state.filters.cause, rows.map((r) => [r.key, r.count, r.causes])]);
    if (key === summaryKey) {
      return;
    }
    summaryKey = key;
    const hadFocus = within(summaryBody, document.activeElement);
    summaryBody.textContent = "";
    summaryTable.hidden = rows.length === 0;
    if (summary.status === "loading") {
      summaryNote.textContent = S.msLoading;
      summaryNote.classList.remove("ui-error");
    } else if (summary.status === "error") {
      summaryNote.textContent = S.msSummaryError + (summary.error ? ": " + summary.error : "");
      summaryNote.classList.add("ui-error");
    } else if (summary.status === "ready" && rows.length === 0) {
      summaryNote.textContent = S.msSummaryEmpty;
      summaryNote.classList.remove("ui-error");
    } else {
      const total = summaryTotal(rows);
      summaryNote.textContent = summary.status === "ready"
        ? format(S[total === 1 ? "msSummaryTotalOne" : "msSummaryTotalOther"], { count: total.toLocaleString("en-US") })
        : "";
      summaryNote.classList.remove("ui-error");
    }
    for (const row of rows) {
      const tr = element("tr", "ms-sum-row");
      tr.appendChild(element("td", "is-text", row.kindLabel));
      tr.appendChild(element("td", "is-text", row.family));
      const nameCell = element("td", "ev-mono is-text", row.name);
      nameCell.title = row.name;
      tr.appendChild(nameCell);
      tr.appendChild(element("td", "is-text", row.missing));
      tr.appendChild(element("td", "ev-num is-num", row.count.toLocaleString("en-US")));
      tr.appendChild(element("td", "is-text ev-muted", row.causes));
      const actionCell = element("td");
      const pick = element("button", "ui-button is-ghost ms-pick", S.msPick);
      pick.type = "button";
      pick.dataset.kind = String(row.kind);
      pick.dataset.name = row.rawName;
      pick.setAttribute("aria-label", format(S.msPickLabel, { name: row.name }));
      if (!row.canFilter) {
        pick.setAttribute("aria-disabled", "true");
        pick.title = S.msPickNone;
      }
      actionCell.appendChild(pick);
      tr.appendChild(actionCell);
      summaryBody.appendChild(tr);
    }
    if (hadFocus) {
      summaryBox.focus(); // the button that had the focus is gone: the section keeps it
    }
  }

  function setStatus(text, isError) {
    setStatusLine(status, text, isError);
  }

  function onState(state) {
    const hadMoreFocus = document.activeElement === moreButton;
    renderList(state);
    renderSummary(state);
    kindSelect.value = state.filters.kind;
    statusSelect.value = state.filters.status;
    causeSelect.value = state.filters.cause;
    nameTag.hidden = state.filters.name === "";
    nameText.textContent = state.filters.name;
    const more = state.next !== null && state.status === "ready";
    moreButton.hidden = !more;
    shell.foot.hidden = !more;
    moreButton.setAttribute("aria-disabled", state.loadingMore ? "true" : "false");
    moreButton.textContent = state.loadingMore ? S.msLoadingMore : state.moreError ? S.msRetry : S.msLoadMore;
    moreNote.hidden = !state.moreError;
    moreNote.textContent = state.moreError ? S.msMoreError + ": " + state.moreError : "";
    if (hadMoreFocus && !more) {
      listBox.focus(); // the button went away (the end of the list): the focus stays in the list
    }
    // ONE announcement path: the main status line. The summary note and the pagination alert are visible text only;
    // their failures are said here too.
    let text;
    let isError = false;
    if (state.status === "nocase") {
      text = S.msNoCase;
    } else if (state.status === "loading") {
      text = S.msLoading;
    } else if (state.status === "error") {
      text = S.msError + (state.error ? ": " + state.error : "");
      isError = true;
    } else if (state.items.length === 0) {
      text = state.next !== null ? S.msNoneYet : state.filters.cause || state.filters.status || state.filters.kind || state.filters.name ? S.msEmptyFiltered : S.msEmpty;
    } else {
      text = countText(state.items.length, state.next !== null);
    }
    if (state.moreError) {
      text = S.msMoreError + ": " + state.moreError;
      isError = true;
    } else if (state.summary.status === "error" && !isError) {
      text += ". " + S.msSummaryError;
      isError = true;
    }
    setStatus(text, isError);
  }

  const flow = createMissingFlow({ api, getCase, onState });

  listBody.addEventListener("click", (event) => {
    const button = event.target instanceof Element ? event.target.closest(".ms-open") : null;
    if (!button || !listBody.contains(button)) {
      return;
    }
    const row = rowsById.get(button.dataset.id);
    // The ids belong to the case the list was loaded for.
    if (row && row.target !== null && flow.state.caseId !== null && flow.state.caseId === getCase()) {
      onShowInGrid(row.target);
    }
  });
  summaryBody.addEventListener("click", (event) => {
    const button = event.target instanceof Element ? event.target.closest(".ms-pick") : null;
    if (!button || !summaryBody.contains(button) || button.getAttribute("aria-disabled") === "true") {
      return;
    }
    flow.setFilters({ kind: button.dataset.kind, name: button.dataset.name });
  });
  nameClear.addEventListener("click", () => {
    flow.setFilters({ name: "" });
    kindSelect.focus(); // the tag with the button is gone
  });
  kindSelect.addEventListener("change", () => flow.setFilters({ kind: kindSelect.value, name: "" }));
  statusSelect.addEventListener("change", () => flow.setFilters({ status: statusSelect.value }));
  causeSelect.addEventListener("change", () => flow.setFilters({ cause: causeSelect.value }));
  moreButton.addEventListener("click", () => {
    if (moreButton.getAttribute("aria-disabled") !== "true") {
      flow.loadMore();
    }
  });

  return {
    refresh: () => flow.refresh(),
    // Another case: the selectors go back to "any" and the lists are read for the new case.
    reset: () => flow.reset(),
  };
}
