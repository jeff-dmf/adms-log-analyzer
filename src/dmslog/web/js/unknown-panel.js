// Unknown-templates panel: signatures of records no template matched (GET /api/unknown).
// Signatures come from untrusted logs: only textContent and class names are written to the DOM.
import { S, format, pluralKey } from "./strings.js";
import { buildRows, buildTomlSketch, filterRows, formatShare, sortRows } from "./unknown-panel-model.js";

const LIMIT = 500;

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

async function copyText(text, fallbackHost) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch (error) {
    // Fall through to select-and-copy.
  }
  fallbackHost.textContent = "";
  const label = element("label", "unk-fallback-label", S.unkFallbackLabel);
  const area = element("textarea", "unk-fallback");
  area.readOnly = true;
  area.value = text;
  label.appendChild(area);
  fallbackHost.appendChild(label);
  area.focus();
  area.select();
  try {
    return document.execCommand("copy");
  } catch (error) {
    return false;
  }
}

// container: empty element to fill. Options:
//   api              needs unknown(caseId, family, limit, signal)
//   getCase()        current case id or null; getFamily() family code or "" for all
//   onOpenEntry(id)  open the example entry in the grid/detail
// Returns { refresh() }; call it when the case or the family changed.
export function initUnknownPanel(container, { api, getCase, getFamily, onOpenEntry }) {
  let rows = [];
  let totalRecords = 0;
  let sortKey = "count";
  let sortDesc = true;
  let filterText = "";
  let controller = null;

  container.textContent = "";
  const toolbar = element("div", "unk-toolbar");
  toolbar.appendChild(element("span", "unk-title", S.unkTitle));
  const filterInput = element("input", "unk-filter");
  filterInput.type = "search";
  filterInput.placeholder = S.unkFilterPlaceholder;
  filterInput.setAttribute("aria-label", S.unkFilterPlaceholder);
  const sortSelect = element("select", "unk-sort");
  sortSelect.setAttribute("aria-label", S.unkSortLabel);
  for (const [value, label] of [["count", S.unkSortCount], ["signature", S.unkSortSignature]]) {
    const option = element("option", "", S.unkSortLabel + ": " + label);
    option.value = value;
    sortSelect.appendChild(option);
  }
  const dirButton = element("button", "unk-dir", "↓");
  dirButton.type = "button";
  const refreshButton = element("button", "unk-refresh", S.unkRefresh);
  refreshButton.type = "button";
  const summary = element("span", "unk-summary");
  toolbar.append(filterInput, sortSelect, dirButton, refreshButton, summary);
  const status = element("div", "unk-status");
  const tableHost = element("div", "unk-table-host");
  const fallbackHost = element("div", "unk-fallback-host");
  container.append(toolbar, status, tableHost, fallbackHost);

  function setStatus(text, isError) {
    status.textContent = text;
    status.classList.toggle("is-error", Boolean(isError));
  }

  function render() {
    const shown = sortRows(filterRows(rows, filterText), sortKey, sortDesc);
    summary.textContent = format(S.unkSummary, {
      shown: shown.length,
      signatures: format(S[pluralKey("nSignature", rows.length)], { count: rows.length }),
      records: format(S[pluralKey("nUnclassifiedRecord", totalRecords)], { count: totalRecords }),
    });
    dirButton.textContent = sortDesc ? "↓" : "↑";
    tableHost.textContent = "";
    if (shown.length === 0) {
      setStatus(S.unkEmpty, false);
      return;
    }
    setStatus("", false);
    const table = element("table", "unk-table");
    const head = element("tr");
    for (const [label, num] of [[S.unkColSignature, false], [S.unkColCount, true], [S.unkColShare, true], ["", false]]) {
      head.appendChild(element("th", num ? "unk-num" : "", label));
    }
    table.appendChild(element("thead")).appendChild(head);
    const body = element("tbody");
    for (const row of shown) {
      const tr = element("tr");
      tr.appendChild(element("td", "unk-signature", row.signature));
      tr.appendChild(element("td", "unk-num", String(row.n)));
      tr.appendChild(element("td", "unk-num", formatShare(row.share)));
      const actions = element("div", "unk-actions");
      const open = element("button", "unk-open", S.unkOpenExample);
      open.type = "button";
      open.disabled = !row.firstEntryId;
      open.addEventListener("click", () => onOpenEntry(row.firstEntryId));
      const copy = element("button", "unk-copy", S.unkCopySketch);
      copy.type = "button";
      copy.addEventListener("click", async () => {
        fallbackHost.textContent = "";
        const ok = await copyText(buildTomlSketch(row, getFamily() || row.family), fallbackHost);
        setStatus(ok ? S.unkCopied : S.unkCopyFailed, !ok);
      });
      actions.append(open, copy);
      tr.appendChild(element("td")).appendChild(actions);
      body.appendChild(tr);
    }
    table.appendChild(body);
    tableHost.appendChild(table);
  }

  async function refresh() {
    if (controller) {
      controller.abort();
    }
    const caseId = getCase();
    if (!caseId) {
      rows = [];
      totalRecords = 0;
      render();
      setStatus(S.unkNoCase, false);
      return;
    }
    controller = new AbortController();
    const mine = controller;
    setStatus(S.unkLoading, false);
    try {
      const data = await api.unknown(caseId, getFamily() || "", LIMIT, mine.signal);
      if (mine !== controller) {
        return;
      }
      totalRecords = data.records || 0;
      rows = buildRows(data.unknown || [], totalRecords);
      render();
    } catch (error) {
      if (mine.signal.aborted) {
        return;
      }
      rows = [];
      render();
      setStatus(S.unkError + (error && error.message ? ": " + error.message : ""), true);
    }
  }

  filterInput.addEventListener("input", () => {
    filterText = filterInput.value;
    render();
  });
  sortSelect.addEventListener("change", () => {
    sortKey = sortSelect.value;
    sortDesc = sortKey === "count";
    render();
  });
  dirButton.addEventListener("click", () => {
    sortDesc = !sortDesc;
    render();
  });
  refreshButton.addEventListener("click", refresh);

  return { refresh };
}
