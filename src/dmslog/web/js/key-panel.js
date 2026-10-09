// "Same key" panel: every record that carries one correlation key in all sources of the case, with
// keyset "Load more"; clicking a record selects it in the grid. Texts come from the ERS
// (untrusted): textContent only; the table body has ONE delegated listener.
import { api } from "./api.js";
import { buildKeyRow, createKeyFlow, keyHeading } from "./key-panel-model.js";
import { S, format } from "./strings.js";

const STYLESHEET = "/css/key-panel.css";

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

// host: empty element to fill (hidden until opened). Options:
//   getCase()          current case id or null
//   onSelect({id, ts}) select the record in the grid
// Returns { open(type, value), close(), reset() }.
export function initKeyPanel(host, { getCase, onSelect }) {
  ensureStylesheet();
  host.textContent = "";
  const head = element("div", "key-head");
  head.appendChild(element("h3", "key-title", S.keyTitle));
  const heading = element("span", "key-heading");
  const closeButton = element("button", "key-close", S.keyClose);
  closeButton.type = "button";
  head.append(heading, closeButton);
  const status = element("div", "key-status");
  status.setAttribute("role", "status");
  const wrap = element("div", "key-list");
  const table = element("table", "key-table");
  table.appendChild(element("caption", "visually-hidden", S.keyTableCaption));
  const tr = element("tr");
  for (const label of [S.keyColTime, S.keyColSource, S.keyColTid, S.keyColTemplate, S.keyColText]) {
    const th = element("th", "", label);
    th.scope = "col";
    tr.appendChild(th);
  }
  table.appendChild(element("thead")).appendChild(tr);
  const tbody = element("tbody");
  table.appendChild(tbody);
  const moreButton = element("button", "key-more", S.keyLoadMore);
  moreButton.type = "button";
  wrap.append(table, moreButton);
  host.append(head, status, wrap);

  let rendered = 0;
  let firstId = null;
  const rows = new Map(); // record id -> {ts}

  function appendRow(record) {
    const row = buildKeyRow(record);
    const line = element("tr", "key-row");
    line.dataset.id = String(row.id);
    const timeCell = element("td", "key-mono");
    const open = element("button", "key-open", row.time || String(row.id));
    open.type = "button";
    open.dataset.id = String(row.id);
    open.setAttribute("aria-label", format(S.keyOpenRecord, { id: row.id }));
    timeCell.appendChild(open);
    line.appendChild(timeCell);
    const sourceCell = element("td", "key-mono", row.source);
    if (row.sensitive) {
      sourceCell.appendChild(element("span", "key-tag", " " + S.keySensitiveTag));
    }
    line.appendChild(sourceCell);
    line.appendChild(element("td", "key-mono", row.tid));
    line.appendChild(element("td", "key-mono", row.code));
    line.appendChild(element("td", "key-mono key-text", row.text));
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
      host.hidden = true;
      renderRows([]);
      return;
    }
    host.hidden = false;
    heading.textContent = state.type === null ? "" : keyHeading(state.type, state.value);
    renderRows(state.records);
    moreButton.hidden = state.next === null || state.status !== "ready";
    moreButton.disabled = state.loadingMore;
    moreButton.textContent = state.loadingMore ? S.keyLoadingMore : S.keyLoadMore;
    status.classList.toggle("is-error", state.status === "error");
    if (state.status === "nocase") {
      status.textContent = S.keyNoCase;
    } else if (state.status === "loading") {
      status.textContent = S.keyLoading;
    } else if (state.status === "error") {
      status.textContent = S.keyError + (state.error ? ": " + state.error : "");
    } else if (state.records.length === 0) {
      status.textContent = S.keyEmpty;
    } else {
      status.textContent = format(state.next === null ? S.keyCount : S.keyCountMore, { count: state.records.length });
    }
  }

  const flow = createKeyFlow({ api, getCase, onState });
  tbody.addEventListener("click", (event) => {
    const target = event.target instanceof Element ? event.target.closest("tr[data-id]") : null;
    if (target && tbody.contains(target) && flow.state.caseId === getCase()) {
      const ts = rows.get(target.dataset.id);
      onSelect({ id: Number(target.dataset.id), ts: ts === undefined ? null : ts });
    }
  });
  moreButton.addEventListener("click", () => flow.loadMore());
  closeButton.addEventListener("click", () => flow.close());

  return {
    open: (type, value) => flow.open(type, value),
    close: () => flow.close(),
    reset: () => flow.close(),
  };
}
