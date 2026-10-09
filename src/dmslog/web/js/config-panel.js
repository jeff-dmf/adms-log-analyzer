// "Effective configuration" panel: Registry keys with layers, the value the process logged, the
// divergence flag, and per-operation payload expectation (GET /api/config).
// Everything comes from the ERS (untrusted text): textContent and class names only.
import { S, format } from "./strings.js";
import {
  buildKeyRows, buildOperationRows, countDiverging, createConfigFlow, filterKeyRows, logLevelText, productOptions,
} from "./config-panel-model.js";

const STYLESHEET = "/css/config-panel.css";

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

// A text badge: the meaning is in the text, the tone class only adds colour.
function badge(text, tone) {
  return element("span", "cfg-badge cfg-" + tone, text);
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
//   api                 needs configProducts(caseId, signal) and config(caseId, product, group, signal)
//   getCase()           current case id or null
//   onOpenEntry(id)     open the entry where the value was logged
// Returns { refresh() }; call it when the case changed or indexing finished.
export function initConfigPanel(container, { api, getCase, onOpenEntry }) {
  ensureStylesheet();
  uid += 1;
  const prefix = "cfg" + uid + "-";
  let products = [];
  let keyRows = [];
  let config = null;
  let filterText = "";
  let onlyDiverging = false;
  let renderedCase = null;
  const expanded = new Set();

  container.textContent = "";
  const toolbar = element("div", "cfg-toolbar");
  toolbar.appendChild(element("span", "cfg-title", S.cfgTitle));
  const productLabel = element("label", "", S.cfgProduct);
  productLabel.htmlFor = prefix + "product";
  const productSelect = element("select", "cfg-product");
  productSelect.id = prefix + "product";
  const filterInput = element("input", "cfg-filter");
  filterInput.type = "search";
  filterInput.placeholder = S.cfgFilterPlaceholder;
  filterInput.setAttribute("aria-label", S.cfgFilterPlaceholder);
  const divLabel = element("label", "cfg-toggle");
  const divBox = element("input");
  divBox.type = "checkbox";
  divLabel.append(divBox, document.createTextNode(" " + S.cfgOnlyDiverging));
  const refreshButton = element("button", "cfg-refresh", S.cfgRefresh);
  refreshButton.type = "button";
  toolbar.append(productLabel, productSelect, filterInput, divLabel, refreshButton);
  const status = element("div", "cfg-status");
  status.setAttribute("role", "status");
  const summary = element("div", "cfg-summary");
  const body = element("div", "cfg-body");
  container.append(toolbar, status, summary, body);

  function setStatus(text, isError) {
    status.textContent = text;
    status.classList.toggle("is-error", Boolean(isError));
  }

  function layersTable(row) {
    const table = element("table", "cfg-layers");
    table.appendChild(element("caption", "", format(S.cfgLayers, { key: row.label })));
    table.appendChild(headRow([S.cfgLayerColLayer, S.cfgLayerColFile, S.cfgLayerColValue, S.cfgLayerColStatus]));
    const tbody = element("tbody");
    for (const layer of row.layers) {
      const tr = element("tr");
      tr.appendChild(element("td", "", layer.layerText));
      tr.appendChild(element("td", "cfg-mono", layer.file));
      tr.appendChild(element("td", "cfg-mono", layer.value));
      const cell = element("td");
      cell.appendChild(badge(layer.statusLabel, layer.tone));
      tr.appendChild(cell);
      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    return table;
  }

  function keysTable(rows) {
    const table = element("table", "cfg-table");
    table.appendChild(element("caption", "visually-hidden", S.cfgKeysCaption));
    table.appendChild(headRow([
      S.cfgColKey, S.cfgColValue, S.cfgColSource, S.cfgColStatus, S.cfgColObserved, S.cfgColDivergence,
    ]));
    const tbody = element("tbody");
    for (const row of rows) {
      const detailId = prefix + "layers-" + tbody.children.length;
      const tr = element("tr", row.divergence === "yes" ? "cfg-row-diverges" : "");
      const keyCell = element("td", "cfg-key");
      const toggle = element("button", "cfg-expand");
      toggle.type = "button";
      toggle.setAttribute("aria-controls", detailId);
      toggle.setAttribute("aria-label", format(S.cfgExpandLabel, { key: row.label }));
      const open = expanded.has(row.id);
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
      toggle.textContent = open ? "▾" : "▸";
      toggle.title = open ? S.cfgCollapse : S.cfgExpand;
      keyCell.append(toggle, element("span", "cfg-mono", row.label));
      tr.appendChild(keyCell);
      tr.appendChild(element("td", "cfg-mono", row.value));
      const source = element("td");
      source.appendChild(element("div", "cfg-mono", row.sourceFile));
      source.appendChild(element("div", "cfg-muted", row.sourceLayer));
      tr.appendChild(source);
      const statusCell = element("td");
      statusCell.appendChild(badge(row.statusLabel, row.tone));
      tr.appendChild(statusCell);
      const observed = element("td");
      observed.appendChild(element("span", "cfg-mono", row.observedText));
      if (row.observedEntryId !== null) {
        const link = element("button", "cfg-link", S.cfgOpenObserved);
        link.type = "button";
        link.addEventListener("click", () => {
          // The entry id belongs to the case the table was built for.
          if (renderedCase !== null && renderedCase === getCase()) {
            onOpenEntry(row.observedEntryId);
          }
        });
        observed.append(" ", link);
      }
      tr.appendChild(observed);
      const divergence = element("td");
      divergence.appendChild(badge(row.divergenceLabel, row.divergence === "yes" ? "bad" : row.divergence === "no" ? "ok" : "muted"));
      tr.appendChild(divergence);
      tbody.appendChild(tr);

      const detailRow = element("tr", "cfg-layers-row");
      detailRow.id = detailId;
      detailRow.hidden = !open;
      const detailCell = element("td");
      detailCell.colSpan = 6;
      detailCell.appendChild(layersTable(row));
      detailRow.appendChild(detailCell);
      tbody.appendChild(detailRow);
      toggle.addEventListener("click", () => {
        const now = detailRow.hidden;
        detailRow.hidden = !now;
        toggle.setAttribute("aria-expanded", now ? "true" : "false");
        toggle.textContent = now ? "▾" : "▸";
        toggle.title = now ? S.cfgCollapse : S.cfgExpand;
        if (now) {
          expanded.add(row.id);
        } else {
          expanded.delete(row.id);
        }
      });
    }
    table.appendChild(tbody);
    return table;
  }

  function operationsTable(rows) {
    const table = element("table", "cfg-table cfg-ops");
    table.appendChild(element("caption", "cfg-caption", S.cfgOpsCaption));
    table.appendChild(headRow([S.cfgOpColOperation, S.cfgOpColLevel, S.cfgOpColPayload, S.cfgOpColReason, S.cfgOpColSource]));
    const tbody = element("tbody");
    for (const row of rows) {
      const tr = element("tr");
      tr.appendChild(element("td", "cfg-mono", row.operation));
      tr.appendChild(element("td", "cfg-mono", row.level));
      const payload = element("td");
      payload.appendChild(badge(row.expectedLabel, row.expected === "yes" ? "ok" : row.expected === "no" ? "muted" : "warn"));
      tr.appendChild(payload);
      tr.appendChild(element("td", "", row.reason));
      tr.appendChild(element("td", "cfg-muted", row.source));
      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    return table;
  }

  function render() {
    body.textContent = "";
    summary.textContent = "";
    if (config === null) {
      return;
    }
    const shown = filterKeyRows(keyRows, filterText, onlyDiverging);
    summary.appendChild(element("div", "", format(S.cfgSummary, {
      shown: shown.length, total: keyRows.length, diverging: countDiverging(keyRows),
    })));
    summary.appendChild(element("div", "", logLevelText(config.log_level)));
    body.appendChild(keysTable(shown));
    const ops = buildOperationRows(config);
    if (ops.length > 0) {
      body.appendChild(operationsTable(ops));
    } else {
      body.appendChild(element("div", "cfg-muted", S.cfgOpsEmpty));
    }
    body.appendChild(element("p", "cfg-note", S.cfgWindowNote));
  }

  let shownProducts = null;
  let shownConfig = null;

  function onState(state) {
    const busy = state.status === "loading";
    productSelect.disabled = busy || state.products.length === 0;
    refreshButton.disabled = false;
    if (state.products !== shownProducts) {
      shownProducts = state.products;
      products = state.products;
      productSelect.textContent = "";
      for (const option of productOptions(products)) {
        const node = element("option", "", option.label);
        node.value = option.value;
        productSelect.appendChild(node);
      }
    }
    if (products.length > 0) {
      productSelect.value = String(state.selected);
    }
    if (state.config !== shownConfig) {
      shownConfig = state.config;
      config = state.config;
      keyRows = config === null ? [] : buildKeyRows(config);
      expanded.clear();
      renderedCase = state.caseId;
    }
    render();
    if (state.status === "nocase") {
      setStatus(S.cfgNoCase, false);
    } else if (state.status === "loading") {
      setStatus(S.cfgLoading, false);
    } else if (state.status === "empty") {
      setStatus(S.cfgNoProducts, false);
    } else if (state.status === "error") {
      setStatus(S.cfgError + (state.error ? ": " + state.error : ""), true);
    } else {
      setStatus("", false);
    }
  }

  const flow = createConfigFlow({ api, getCase, onState });

  productSelect.addEventListener("change", () => {
    flow.select(Number(productSelect.value));
  });
  filterInput.addEventListener("input", () => {
    filterText = filterInput.value;
    render();
  });
  divBox.addEventListener("change", () => {
    onlyDiverging = divBox.checked;
    render();
  });
  refreshButton.addEventListener("click", () => flow.refresh());

  return { refresh: () => flow.refresh() };
}
