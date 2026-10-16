// Payload list panel: payloads of the case with filters (role, direction, peer, operation, format,
// id, orphans only), keyset "Load more"; clicking a row opens the record that holds the payload.
// Everything comes from the ERS (untrusted): textContent only; the table body has ONE delegated
// listener.
import {
  PAYLOAD_DIRECTIONS, PAYLOAD_FORMATS, PAYLOAD_ROLES, buildPayloadRow, createPayloadListFlow,
} from "./payload-list-model.js";
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

let uid = 0;

// host: empty element to fill. Options:
//   api               needs payloadPage(caseId, params, signal)
//   getCase()         current case id or null
//   onOpen({id, ts})  select the record that holds the payload (its detail shows the Payload section)
//   onRequestClose()  the dock closes the tab (the default hides the host)
// Returns { refresh(), close() }. The panel never shows or hides its host: opening is the dock's.
export function initPayloadList(host, { api, getCase, onOpen, onRequestClose = null }) {
  uid += 1;
  const prefix = "pg" + uid + "-";
  const shell = createPanelShell(host, {
    title: S.plgTitle,
    onRefresh: () => flow.refresh(),
    refreshLabel: S.plgRefresh,
    onRequestClose: onRequestClose || (() => {
      flow.close();
      host.hidden = true;
    }),
  });

  function field(labelText, control, id) {
    control.id = prefix + id;
    const label = element("label", "", labelText);
    label.htmlFor = control.id;
    return [label, control];
  }

  function select(values) {
    const node = element("select");
    const any = element("option", "", S.plgAny);
    any.value = "";
    node.appendChild(any);
    for (const value of values) {
      const option = element("option", "", value);
      option.value = value;
      node.appendChild(option);
    }
    return node;
  }

  const roleSelect = select(PAYLOAD_ROLES);
  const directionSelect = select(PAYLOAD_DIRECTIONS);
  const formatSelect = select(PAYLOAD_FORMATS);
  const text = (placeholder) => {
    const input = element("input", "pg-input");
    input.type = "search";
    input.autocomplete = "off";
    input.placeholder = placeholder;
    return input;
  };
  const peerInput = text(S.plgPeerPlaceholder);
  const operationInput = text(S.plgOperationPlaceholder);
  const queryInput = text(S.plgSearchPlaceholder);
  const orphanBox = element("input");
  orphanBox.type = "checkbox";
  const filters = element("div", "pg-filters");
  filters.append(
    ...field(S.plgRole, roleSelect, "role"),
    ...field(S.plgDirection, directionSelect, "direction"),
    ...field(S.plgPeer, peerInput, "peer"),
    ...field(S.plgOperation, operationInput, "operation"),
    ...field(S.plgFormat, formatSelect, "fmt"),
    ...field(S.plgSearch, queryInput, "q"),
    ...field(S.plgOrphan, orphanBox, "orphan"),
  );
  shell.addTools(filters);

  const status = statusLine(document, "pg-status");
  const wrap = element("div", "pg-list");
  const table = element("table", "ui-table is-wrap pg-table");
  table.appendChild(element("caption", "visually-hidden", S.plgTableCaption));
  const tr = element("tr");
  for (const label of [
    S.plgColTime, S.plgColRole, S.plgColDirection, S.plgColPeer, S.plgColOperation, S.plgColFormat, S.plgColSize,
    S.plgColSide, S.plgColNote,
  ]) {
    const th = element("th", "", label);
    th.scope = "col";
    tr.appendChild(th);
  }
  table.appendChild(element("thead")).appendChild(tr);
  const tbody = element("tbody");
  table.appendChild(tbody);
  const moreButton = element("button", "ui-button pg-more", S.plgLoadMore);
  moreButton.type = "button";
  wrap.append(table);
  shell.body.append(status, wrap);
  shell.addFoot(moreButton); // "Load more" is the main action of a long list: always in sight
  shell.foot.hidden = true;

  let rendered = 0;
  let firstId = null;
  const rows = new Map(); // payload id -> {id, ts} of the record that holds it

  function appendRow(meta) {
    const row = buildPayloadRow(meta);
    const line = element("tr", "pg-row" + (row.orphan ? " pg-orphan" : ""));
    line.dataset.id = String(row.id);
    const timeCell = element("td", "pg-mono");
    const open = element("button", "pg-open", row.time || String(row.id));
    open.type = "button";
    open.setAttribute("aria-label", format(S.plgOpen, { id: row.id }));
    timeCell.appendChild(open);
    line.appendChild(timeCell);
    for (const value of [row.role, row.direction, row.peer, row.operation, row.fmt]) {
      line.appendChild(element("td", "pg-mono", value));
    }
    line.appendChild(element("td", "pg-num", row.size));
    line.appendChild(element("td", "pg-mono", [row.side, row.member].filter(Boolean).join(" ")));
    line.appendChild(element("td", "pg-note", row.note));
    tbody.appendChild(line);
    rows.set(String(row.id), { id: row.entryId, ts: row.ts });
  }

  function renderRows(payloads) {
    const first = payloads.length > 0 ? payloads[0].id : null;
    if (payloads.length < rendered || first !== firstId) {
      tbody.textContent = "";
      rows.clear();
      rendered = 0;
      firstId = first;
    }
    for (let i = rendered; i < payloads.length; i += 1) {
      appendRow(payloads[i]);
    }
    rendered = payloads.length;
  }

  function onState(state) {
    if (state.status === "idle") {
      renderRows([]);
      setStatusLine(status, "", false);
      moreButton.hidden = true;
      shell.foot.hidden = true;
      return;
    }
    renderRows(state.payloads);
    moreButton.hidden = state.next === null || state.status !== "ready";
    shell.foot.hidden = moreButton.hidden;
    moreButton.disabled = state.loadingMore;
    moreButton.textContent = state.loadingMore ? S.plgLoadingMore : S.plgLoadMore;
    if (state.status === "nocase") {
      setStatusLine(status, S.plgNoCase, false);
    } else if (state.status === "loading") {
      setStatusLine(status, S.plgLoading, false);
    } else if (state.status === "error") {
      setStatusLine(status, S.plgError + (state.error ? ": " + state.error : ""), true);
    } else if (state.payloads.length === 0) {
      setStatusLine(status, S.plgEmpty, false);
    } else {
      setStatusLine(status, format(S[pluralKey(state.next === null ? "plgCount" : "plgCountMore", state.payloads.length)], { count: state.payloads.length }), false);
    }
  }

  const flow = createPayloadListFlow({ api, getCase, onState });
  const apply = () => flow.setFilters({
    role: roleSelect.value,
    direction: directionSelect.value,
    peer: peerInput.value,
    operation: operationInput.value,
    fmt: formatSelect.value,
    q: queryInput.value,
    orphan: orphanBox.checked,
  });
  tbody.addEventListener("click", (event) => {
    const target = event.target instanceof Element ? event.target.closest("tr[data-id]") : null;
    if (target && tbody.contains(target) && flow.state.caseId === getCase()) {
      const record = rows.get(target.dataset.id);
      if (record) {
        onOpen(record);
      }
    }
  });
  for (const control of [roleSelect, directionSelect, formatSelect, orphanBox, peerInput, operationInput, queryInput]) {
    control.addEventListener("change", apply);
  }
  moreButton.addEventListener("click", () => flow.loadMore());

  return { refresh: () => flow.refresh(), close: () => flow.close() };
}
