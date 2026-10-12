// "Distinct values" panel and "Group by template" view (ROADMAP 9.2). Both read the current
// filter of the grid; the drill-down buttons add the leaf `field = value` (or its negation) to it.
// Values come from untrusted logs: only textContent, attribute values and class names are written.
import {
  DEFAULT_TOP, MAX_TOP, buildView, createDistinctFlow, createGroupFlow, fieldOptions, parseTop,
} from "./distinct-model.js";
import { S, format } from "./strings.js";
import { formatTs } from "./time.js";

const STYLESHEET = "/css/distinct.css";

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

function button(className, text) {
  const node = element("button", className, text);
  node.type = "button";
  return node;
}

// -- Distinct values ----------------------------------------------------------------------------

function initDistinct(host, { api, getCase, getFilter, getFieldNames, onDrill }) {
  host.textContent = "";
  const head = element("div", "dst-head");
  const title = element("h3", "dst-title", S.dstTitle);
  title.id = "dst-title";
  host.setAttribute("aria-labelledby", title.id);
  const fieldLabel = element("label", "dst-label", S.dstFieldLabel);
  const fieldSelect = element("select", "dst-field");
  fieldSelect.id = "dst-field";
  fieldLabel.htmlFor = fieldSelect.id;
  const topLabel = element("label", "dst-label", S.dstTopLabel);
  const topInput = element("input", "dst-top");
  topInput.id = "dst-top";
  topInput.type = "number";
  topInput.min = "1";
  topInput.max = String(MAX_TOP);
  topInput.value = String(DEFAULT_TOP);
  topLabel.htmlFor = topInput.id;
  const refreshButton = button("dst-refresh", S.dstRefresh);
  head.append(title, fieldLabel, fieldSelect, topLabel, topInput, refreshButton);
  const status = element("div", "dst-status");
  status.setAttribute("role", "status");
  status.setAttribute("aria-live", "polite");
  const tableHost = element("div", "dst-table-host");
  host.append(head, status, tableHost);

  let keyTypes = [];
  let keyTypesCase = null;

  function fillFields() {
    const current = fieldSelect.value || flow.state.field;
    fieldSelect.textContent = "";
    const groups = { column: S.dstGroupColumns, key: S.dstGroupKeys, field: S.dstGroupFields };
    const parents = {};
    for (const option of fieldOptions(keyTypes, getFieldNames())) {
      if (!parents[option.group]) {
        parents[option.group] = element("optgroup");
        parents[option.group].label = groups[option.group];
        fieldSelect.appendChild(parents[option.group]);
      }
      const node = element("option", "", option.label);
      node.value = option.value;
      parents[option.group].appendChild(node);
    }
    fieldSelect.value = current;
    if (fieldSelect.value !== current) {
      fieldSelect.value = "tpl";
    }
  }

  async function loadKeyTypes() {
    const caseId = getCase();
    if (!caseId || keyTypesCase === caseId) {
      return;
    }
    try {
      const data = await api.keyTypes(caseId);
      if (getCase() === caseId) {
        keyTypes = (data.types || []).map((t) => t.type);
        keyTypesCase = caseId;
        fillFields();
      }
    } catch (err) {
      // Without the key types the panel still offers columns and parsed fields.
    }
  }

  function setStatus(text, isError) {
    status.textContent = text;
    status.classList.toggle("is-error", Boolean(isError));
  }

  function render(state) {
    tableHost.textContent = "";
    if (state.status === "idle") {
      setStatus(getCase() ? "" : S.dstNoCase, false);
      return;
    }
    if (state.status === "loading") {
      setStatus(S.dstLoading, false);
      return;
    }
    if (state.status === "error") {
      setStatus(format(S.dstError, { error: state.error }), true);
      return;
    }
    const view = buildView(state.field, state.data);
    setStatus(view.summary, false);
    if (view.rows.length === 0) {
      tableHost.appendChild(element("div", "dst-empty", S.dstEmptyResult));
      return;
    }
    const table = element("table", "dst-table");
    table.appendChild(element("caption", "visually-hidden", S.dstTitle));
    const headRow = element("tr");
    for (const [text, cls] of [[S.dstColValue, ""], [S.dstColCount, "dst-num"], [S.dstColShare, "dst-num"], [S.dstColActions, ""]]) {
      const th = element("th", cls, text);
      th.scope = "col";
      headRow.appendChild(th);
    }
    table.appendChild(element("thead")).appendChild(headRow);
    const body = element("tbody");
    for (const row of view.rows) {
      const tr = element("tr");
      const valueCell = element("td", "dst-value", row.text);
      valueCell.title = row.text;
      tr.appendChild(valueCell);
      tr.appendChild(element("td", "dst-num", row.count));
      const share = element("td", "dst-num dst-share");
      const bar = element("span", "dst-bar");
      bar.style.width = (row.share * 100).toFixed(1) + "%";
      bar.setAttribute("aria-hidden", "true");
      share.append(bar, element("span", "dst-pct", row.pct));
      tr.appendChild(share);
      const actions = element("td", "dst-actions");
      const only = button("dst-include", S.dstInclude);
      const hide = button("dst-exclude", S.dstExclude);
      only.setAttribute("aria-label", format(S.dstIncludeLabel, { value: row.text }));
      hide.setAttribute("aria-label", format(S.dstExcludeLabel, { value: row.text }));
      only.disabled = hide.disabled = !row.leaf;
      if (!row.leaf) {
        only.title = hide.title = S.dstNoLeaf;
      }
      only.addEventListener("click", () => onDrill(row.leaf, false));
      hide.addEventListener("click", () => onDrill(row.leaf, true));
      actions.append(only, hide);
      tr.appendChild(actions);
      body.appendChild(tr);
    }
    table.appendChild(body);
    tableHost.appendChild(table);
    if (view.others) {
      tableHost.appendChild(element("div", "dst-others", view.others));
    }
  }

  const flow = createDistinctFlow({ api, getCase, getFilter, onState: render });

  function reload() {
    const top = parseTop(topInput.value);
    if (top === null) {
      setStatus(format(S.dstBadTop, { max: MAX_TOP }), true);
      return Promise.resolve();
    }
    return flow.load({ field: fieldSelect.value || "tpl", top });
  }

  fieldSelect.addEventListener("change", reload);
  topInput.addEventListener("change", reload);
  topInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      reload();
    }
  });
  refreshButton.addEventListener("click", reload);
  fillFields();

  return {
    refresh() {
      fillFields();
      loadKeyTypes();
      return reload();
    },
    reset() {
      keyTypes = [];
      keyTypesCase = null;
      fillFields();
      flow.cancel();
    },
  };
}

// -- Group by template --------------------------------------------------------------------------

function initGroup(host, { api, getCase, getFilter, getSourceName, onReveal }) {
  host.textContent = "";
  const head = element("div", "dst-head");
  const title = element("h3", "dst-title", S.grpTitle);
  title.id = "grp-title";
  host.setAttribute("aria-labelledby", title.id);
  const refreshButton = button("dst-refresh", S.dstRefresh);
  head.append(title, refreshButton);
  const status = element("div", "dst-status");
  status.setAttribute("role", "status");
  status.setAttribute("aria-live", "polite");
  const list = element("ul", "grp-list");
  const others = element("div", "dst-others");
  host.append(head, status, list, others);

  function setStatus(text, isError) {
    status.textContent = text;
    status.classList.toggle("is-error", Boolean(isError));
  }

  function rowsBlock(tplId, group, text) {
    const block = element("div", "grp-rows");
    block.id = "grp-rows-" + tplId;
    if (group.error) {
      block.appendChild(element("div", "dst-status is-error", format(S.dstError, { error: group.error })));
    }
    const rows = element("ul", "grp-row-list");
    rows.setAttribute("aria-label", format(S.grpRowsLabel, { value: text }));
    for (const row of group.rows) {
      const item = element("li");
      const open = button("grp-row");
      open.setAttribute("aria-label", format(S.grpOpenRecord, { id: row.id }));
      open.append(
        element("span", "grp-ts", formatTs(row.ts)),
        element("span", "grp-src", getSourceName(row.source_id)),
        element("span", "grp-msg", row.msg === null || row.msg === undefined ? "" : String(row.msg)),
      );
      open.addEventListener("click", () => onReveal({ id: row.id, ts: row.ts }));
      item.appendChild(open);
      rows.appendChild(item);
    }
    block.appendChild(rows);
    if (group.loading) {
      block.appendChild(element("div", "dst-status", S.dstLoading));
    } else if (group.next) {
      const more = button("grp-more", S.grpMore);
      more.addEventListener("click", () => flow.loadMore(tplId));
      block.appendChild(more);
    } else if (group.rows.length === 0 && !group.error) {
      block.appendChild(element("div", "dst-empty", S.dstEmptyResult));
    }
    return block;
  }

  function render(state) {
    const focusedId = document.activeElement && document.activeElement.dataset ? document.activeElement.dataset.tpl : undefined;
    list.textContent = "";
    others.textContent = "";
    if (state.status === "idle") {
      setStatus(getCase() ? "" : S.dstNoCase, false);
      return;
    }
    if (state.status === "loading") {
      setStatus(S.dstLoading, false);
      return;
    }
    if (state.status === "error") {
      setStatus(format(S.dstError, { error: state.error }), true);
      return;
    }
    const view = buildView("tpl", state.data);
    setStatus(view.summary, false);
    others.textContent = view.others;
    for (const row of view.rows) {
      const item = element("li", "grp-item");
      const group = state.groups.get(row.value);
      const toggle = button("grp-toggle");
      toggle.dataset.tpl = String(row.value);
      toggle.setAttribute("aria-expanded", group ? "true" : "false");
      toggle.setAttribute("aria-controls", "grp-rows-" + row.value);
      toggle.append(
        element("span", "grp-caret", group ? "▾" : "▸"),
        element("span", "grp-name", row.text),
        element("span", "grp-count", format(S.grpCount, { count: row.count, pct: row.pct })),
        element("span", "grp-span", format(S.grpSpan, { first: formatTs(row.firstTs), last: formatTs(row.lastTs) })),
      );
      toggle.addEventListener("click", () => flow.toggle(row.value));
      item.appendChild(toggle);
      if (group) {
        item.appendChild(rowsBlock(row.value, group, row.text));
      }
      list.appendChild(item);
      if (focusedId === String(row.value)) {
        toggle.focus();
      }
    }
    if (view.rows.length === 0) {
      list.appendChild(element("li", "dst-empty", S.dstEmptyResult));
    }
  }

  const flow = createGroupFlow({ api, getCase, getFilter, onState: render });
  refreshButton.addEventListener("click", () => flow.load());

  return {
    refresh: () => flow.load(),
    reset: () => flow.cancel(),
  };
}

// -- mounting -----------------------------------------------------------------------------------

// Adds the two toolbar buttons and their host panels. Options:
//   toolbar, beforeButton    where the buttons go; panelParent, beforePanel  where the panels go
//   api, getCase(), getFilter()   the current case and the ONE composed filter of the grid
//   getFieldNames()          parsed-field names seen so far; getSourceName(id)
//   onDrill(leaf, exclude)   add the leaf (or its negation) to the user filter
//   onReveal({id, ts})       select a record in the grid
// Returns { viewChanged() } to call whenever the case or the filter changed.
export function mountDistinct(options) {
  ensureStylesheet();
  const { toolbar, beforeButton, panelParent, beforePanel } = options;
  const make = (id, label) => {
    const toggle = button("", label);
    toggle.id = id + "-button";
    toggle.setAttribute("aria-expanded", "false");
    const host = element("section", "panel host-panel dst-host");
    host.id = id + "-host";
    host.hidden = true;
    toggle.setAttribute("aria-controls", host.id);
    toolbar.insertBefore(toggle, beforeButton || null);
    panelParent.insertBefore(host, beforePanel || null);
    return { toggle, host };
  };
  const distinctUi = make("distinct", S.dstButton);
  const groupUi = make("group-template", S.grpButton);
  const distinct = initDistinct(distinctUi.host, options);
  const group = initGroup(groupUi.host, options);
  const wire = ({ toggle, host }, panel) => {
    toggle.addEventListener("click", () => {
      host.hidden = !host.hidden;
      toggle.setAttribute("aria-expanded", host.hidden ? "false" : "true");
      if (host.hidden) {
        panel.reset();
      } else {
        panel.refresh();
      }
    });
  };
  wire(distinctUi, distinct);
  wire(groupUi, group);
  return {
    viewChanged() {
      if (!distinctUi.host.hidden) {
        distinct.refresh();
      }
      if (!groupUi.host.hidden) {
        group.refresh();
      }
    },
  };
}
