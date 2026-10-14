// "Aggregate" panel (ROADMAP 9.6): a declarative question over the current view, answered by
// POST /api/entries/aggregate as a table, a chart (uPlot, vendored, loaded on first use) and CSV.
// "Filter by this row" adds the row's filter to the user filter. Values come from untrusted logs:
// only textContent, attribute values and class names are written, never markup.
import {
  EXAMPLES, MAX_GROUP_BY, MAX_LIMIT, MAX_MEASURES, applicableLookups, buildRequest, buildView, chartData,
  createAggregateFlow, csvFileName, dimLabel, dimOptions, emptyForm, exampleForm, measureLabel, measureOptions,
  tableText,
} from "./aggregate-model.js";
import { S, format } from "./strings.js";

const CHART_HEIGHT = 220;
const COLORS = 8; // --agg-c0 .. --agg-c7

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

function labelled(id, text, control) {
  const wrap = element("span", "agg-field");
  const label = element("label", "agg-label", text);
  control.id = id;
  label.htmlFor = id;
  wrap.append(label, control);
  return wrap;
}

function fillSelect(select, options, groups, current, withNone) {
  select.textContent = "";
  if (withNone) {
    const none = element("option", "", S.aggNone);
    none.value = "";
    select.appendChild(none);
  }
  const parents = {};
  for (const option of options) {
    let parent = select;
    if (groups && groups[option.group]) {
      if (!parents[option.group]) {
        parents[option.group] = element("optgroup");
        parents[option.group].label = groups[option.group];
        select.appendChild(parents[option.group]);
      }
      parent = parents[option.group];
    }
    const node = element("option", "", option.label);
    node.value = option.value;
    parent.appendChild(node);
  }
  select.value = current;
  if (select.value !== current) {
    select.value = withNone ? "" : (options[0] ? options[0].value : "");
  }
}

function saveText(text, name) {
  const blob = new Blob([text], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

async function copyText(text) {
  if (navigator.clipboard && navigator.clipboard.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }
  throw new Error(S.aggCopyFailed);
}

function initPanel(host, { api, getCase, getFilter, getFieldNames, onDrill }) {
  host.textContent = "";
  let form = emptyForm();
  let keyTypes = [];
  let keyTypesCase = null;
  let extra = null; // condition the form added to the view ("Only errors"), part of every drill-down
  let chart = null;
  let lookupBoxes = [];

  const title = element("h3", "agg-title", S.aggTitle);
  title.id = "agg-title";
  host.setAttribute("aria-labelledby", title.id);

  const examples = element("select", "agg-examples");
  const blank = element("option", "", S.aggExamplesNone);
  blank.value = "";
  examples.appendChild(blank);
  for (const example of EXAMPLES) {
    const node = element("option", "", example.title());
    node.value = example.id;
    examples.appendChild(node);
  }
  const head = element("div", "agg-head");
  head.append(title, labelled("agg-examples", S.aggExamples, examples));

  const rowsSelect = element("select", "agg-rows");
  for (const [value, text] of [["entries", S.aggRowsEntries], ["events", S.aggRowsEvents]]) {
    const node = element("option", "", text);
    node.value = value;
    rowsSelect.appendChild(node);
  }
  const onlyErrors = element("input", "agg-only-errors");
  onlyErrors.type = "checkbox";
  const line1 = element("div", "agg-line");
  line1.append(labelled("agg-rows", S.aggRows, rowsSelect), labelled("agg-only-errors", S.aggOnlyErrors, onlyErrors));

  const groupSelects = [];
  const line2 = element("div", "agg-line");
  for (let k = 0; k < MAX_GROUP_BY; k += 1) {
    const select = element("select", "agg-group");
    groupSelects.push(select);
    line2.appendChild(labelled("agg-group-" + k, format(S.aggGroupBy, { n: k + 1 }), select));
  }
  const measureSelects = [];
  const line3 = element("div", "agg-line");
  for (let k = 0; k < MAX_MEASURES; k += 1) {
    const select = element("select", "agg-measure");
    measureSelects.push(select);
    line3.appendChild(labelled("agg-measure-" + k, format(S.aggMeasure, { n: k + 1 }), select));
  }

  const eventLine = element("div", "agg-line agg-events");
  const eventInputs = {};
  for (const [key, text] of [["eventKinds", S.aggEventKinds], ["eventNames", S.aggEventNames], ["eventStatuses", S.aggEventStatuses]]) {
    const input = element("input", "agg-event-input");
    input.type = "text";
    input.placeholder = S.aggEventHint;
    input.maxLength = 2000;
    eventInputs[key] = input;
    eventLine.appendChild(labelled("agg-" + key, text, input));
  }

  const orderSelect = element("select", "agg-order");
  const dirSelect = element("select", "agg-dir");
  for (const [value, text] of [["desc", S.aggOrderDesc], ["asc", S.aggOrderAsc]]) {
    const node = element("option", "", text);
    node.value = value;
    dirSelect.appendChild(node);
  }
  const limitInput = element("input", "agg-limit");
  limitInput.type = "number";
  limitInput.min = "1";
  limitInput.max = String(MAX_LIMIT);
  const lookupHost = element("span", "agg-lookups");
  const line4 = element("div", "agg-line");
  line4.append(
    labelled("agg-order", S.aggOrderBy, orderSelect), labelled("agg-dir", S.aggOrderDir, dirSelect),
    labelled("agg-limit", S.aggLimit, limitInput), lookupHost,
  );

  const runButton = button("agg-run", S.aggRun);
  const csvButton = button("agg-csv", S.aggCsv);
  const copyButton = button("agg-copy", S.aggCopy);
  const actions = element("div", "agg-actions");
  actions.append(runButton, csvButton, copyButton);

  const status = element("div", "agg-status");
  status.setAttribute("role", "status");
  status.setAttribute("aria-live", "polite");
  const notices = element("div", "agg-notices");
  const chartHost = element("div", "agg-chart");
  chartHost.hidden = true;
  const tableHost = element("div", "agg-table-host");
  const formBox = element("form", "agg-form");
  formBox.append(line1, line2, line3, eventLine, line4, actions);
  host.append(head, formBox, status, notices, chartHost, tableHost);

  function setStatus(text, isError) {
    status.textContent = text;
    status.classList.toggle("is-error", Boolean(isError));
  }

  // -- form <-> controls

  function paintForm() {
    rowsSelect.value = form.rows;
    onlyErrors.checked = Boolean(form.onlyErrors);
    const fields = getFieldNames();
    const dims = dimOptions(form.rows, keyTypes, fields);
    const dimGroups = { column: S.aggGroupColumns, time: S.aggGroupTime, key: S.aggGroupKeys, field: S.aggGroupFields };
    groupSelects.forEach((select, k) => fillSelect(select, dims, dimGroups, form.groupBy[k] || "", true));
    const measures = measureOptions(form.rows, keyTypes, fields);
    const mGroups = { count: S.aggGroupCount, distinct: S.aggGroupDistinct, number: S.aggGroupNumbers };
    measureSelects.forEach((select, k) => fillSelect(select, measures, mGroups, form.measures[k] || "", k > 0));
    eventLine.hidden = form.rows !== "events";
    for (const key of Object.keys(eventInputs)) {
      eventInputs[key].value = form[key] || "";
    }
    paintOrder();
    dirSelect.value = form.orderDir === "asc" ? "asc" : "desc";
    limitInput.value = form.limit;
    paintLookups();
  }

  function paintOrder() {
    const dims = groupSelects.map((s) => s.value).filter(Boolean);
    const ids = [...dims, ...measureSelects.map((s) => s.value).filter(Boolean)];
    const options = ids.map((id) => ({ value: id, label: dims.includes(id) ? dimLabel(id) : measureLabel(id) }));
    orderSelect.textContent = "";
    const def = element("option", "", S.aggOrderDefault);
    def.value = "";
    orderSelect.appendChild(def);
    for (const option of options) {
      const node = element("option", "", option.label);
      node.value = option.value;
      orderSelect.appendChild(node);
    }
    orderSelect.value = ids.includes(form.orderBy) ? form.orderBy : "";
  }

  function paintLookups() {
    lookupHost.textContent = "";
    lookupBoxes = [];
    const names = applicableLookups(form);
    if (!names.length) {
      return;
    }
    lookupHost.appendChild(element("span", "agg-label", S.aggLookups));
    for (const name of names) {
      const box = element("input", "agg-lookup");
      box.type = "checkbox";
      box.value = name;
      box.checked = (form.lookups || []).includes(name);
      lookupBoxes.push(box);
      box.addEventListener("change", () => {
        readForm();
      });
      lookupHost.appendChild(labelled("agg-lookup-" + name.replace(/\W/g, "-"), S.aggLookupNames[name] || name, box));
    }
  }

  function readForm() {
    const lookups = lookupBoxes.filter((b) => b.checked).map((b) => b.value);
    form = {
      ...form,
      rows: rowsSelect.value === "events" ? "events" : "entries",
      onlyErrors: Boolean(onlyErrors.checked),
      groupBy: groupSelects.map((s) => s.value),
      measures: measureSelects.map((s) => s.value),
      orderBy: orderSelect.value,
      orderDir: dirSelect.value,
      limit: limitInput.value,
      lookups,
      eventKinds: eventInputs.eventKinds.value,
      eventNames: eventInputs.eventNames.value,
      eventStatuses: eventInputs.eventStatuses.value,
    };
    return form;
  }

  rowsSelect.addEventListener("change", () => {
    const rows = rowsSelect.value;
    form = { ...emptyForm(), rows, groupBy: [rows === "events" ? "name" : "tpl", "", ""] };
    paintForm();
  });
  for (const select of [...groupSelects, ...measureSelects]) {
    select.addEventListener("change", () => {
      readForm();
      paintOrder();
      paintLookups();
    });
  }
  examples.addEventListener("change", () => {
    const next = exampleForm(examples.value);
    if (next) {
      form = next;
      paintForm();
      run();
    }
  });
  formBox.addEventListener("submit", (event) => {
    event.preventDefault();
    run();
  });

  // -- results

  function destroyChart() {
    if (chart) {
      chart.destroy();
      chart = null;
    }
    chartHost.textContent = "";
    chartHost.hidden = true;
  }

  function cssColor(name, fallback) {
    const value = getComputedStyle(host).getPropertyValue(name).trim();
    return value || fallback;
  }

  async function drawChart(data) {
    destroyChart();
    const spec = chartData(data);
    if (!spec) {
      return;
    }
    let uPlot;
    try {
      uPlot = (await import("../vendor/uplot/uPlot.esm.js")).default;
    } catch (err) {
      return; // no chart: the table is still there
    }
    if (flow.state.data !== data) {
      return;
    }
    try {
      buildChart(uPlot, spec);
    } catch (err) {
      destroyChart(); // no chart: the table is still there
    }
  }

  function buildChart(uPlot, spec) {
    chartHost.hidden = false;
    const plot = element("div", "agg-plot");
    plot.setAttribute("role", "img");
    plot.setAttribute("aria-label", format(S.aggChartLabel, { measure: spec.measure }));
    chartHost.appendChild(plot);
    if (spec.truncated) {
      chartHost.appendChild(element("div", "agg-note", S.aggChartMore));
    }
    const legend = element("ul", "agg-legend");
    const muted = cssColor("--muted", "#888");
    const border = cssColor("--border", "#ccc");
    const axis = { stroke: muted, grid: { stroke: border, width: 1 }, ticks: { stroke: border, width: 1 } };
    const series = [{}];
    spec.series.forEach((s, k) => {
      const color = cssColor("--agg-c" + (k % COLORS), "#3b82c4");
      const item = element("li", "agg-legend-item");
      item.append(element("span", "agg-swatch agg-c" + (k % COLORS)), document.createTextNode(s.label));
      legend.appendChild(item);
      if (spec.kind === "bars") {
        series.push({ label: s.label, fill: color, stroke: color, width: 0, paths: uPlot.paths.bars({ size: [0.7, 60] }), points: { show: false } });
      } else {
        series.push({ label: s.label, stroke: color, width: 2, spanGaps: false, points: { show: spec.xs.length < 60 } });
      }
    });
    const xs = spec.kind === "bars" ? spec.labels.map((_l, k) => k) : spec.xs;
    const options = {
      width: Math.max(200, chartHost.clientWidth || 600),
      height: CHART_HEIGHT,
      legend: { show: false },
      cursor: { points: { show: false } },
      scales: {
        // bars: one slot per row, centred on its index
        x: spec.kind === "bars" ? { time: false, range: [-0.5, xs.length - 0.5] } : { time: true },
        y: { range: (_u, lo, hi) => [Math.min(0, lo), Math.max(1, hi) * 1.08] },
      },
      axes: [
        spec.kind === "bars"
          ? {
            ...axis,
            splits: () => xs,
            values: (_u, ticks) => ticks.map((t) => (spec.labels[t] ? spec.labels[t].slice(0, 18) : "")),
          }
          : axis,
        { ...axis, size: 56 },
      ],
      series,
    };
    if (spec.kind === "time") {
      // The log clock is stored as UTC: draw the axis with UTC getters, never the browser zone.
      options.tzDate = (ts) => uPlot.tzDate(new Date(ts * 1000), "Etc/UTC");
    }
    chart = new uPlot(options, [xs, ...spec.series.map((s) => s.values)], plot);
    chartHost.appendChild(legend);
  }

  // live: the result belongs to the current case and view; otherwise drilling down is disabled
  function renderTable(view, live) {
    tableHost.textContent = "";
    if (view.rows.length === 0) {
      tableHost.appendChild(element("div", "agg-empty", S.aggEmpty));
      return;
    }
    const table = element("table", "agg-table");
    table.appendChild(element("caption", "visually-hidden", S.aggTitle));
    const headRow = element("tr");
    view.headers.forEach((text, i) => {
      const th = element("th", view.numeric[i] ? "agg-num" : "", text);
      th.scope = "col";
      headRow.appendChild(th);
    });
    const actionsHead = element("th", "", "");
    actionsHead.scope = "col";
    headRow.appendChild(actionsHead);
    table.appendChild(element("thead")).appendChild(headRow);
    const body = element("tbody");
    for (const row of view.rows) {
      const tr = element("tr");
      row.cells.forEach((text, i) => {
        const td = element("td", view.numeric[i] ? "agg-num" : "agg-text", text);
        if (!view.numeric[i]) {
          td.title = text;
        }
        tr.appendChild(td);
      });
      const cell = element("td", "agg-actions-cell");
      const drill = button("agg-drill", S.aggFilterRow);
      drill.setAttribute("aria-label", format(S.aggFilterRowLabel, { row: row.title || S.aggTitle }));
      drill.disabled = !row.drill || !live;
      if (!row.drill) {
        drill.title = S.aggNoDrill;
      } else if (!live) {
        drill.title = S.aggStale;
      }
      drill.addEventListener("click", () => {
        if (row.drill && !flow.state.stale && flow.state.caseId === getCase()) {
          onDrill(row.drill);
        }
      });
      cell.appendChild(drill);
      tr.appendChild(cell);
      body.appendChild(tr);
    }
    table.appendChild(body);
    tableHost.appendChild(table);
  }

  function render(state) {
    notices.textContent = "";
    const busy = state.status === "loading";
    runButton.disabled = busy;
    csvButton.disabled = busy || state.status !== "ready";
    copyButton.disabled = busy || state.status !== "ready";
    if (state.status === "idle") {
      destroyChart();
      tableHost.textContent = "";
      setStatus(state.error || (getCase() ? "" : S.aggNoCase), false);
      return;
    }
    if (busy) {
      setStatus(S.aggLoading, false);
      return;
    }
    if (state.status === "error") {
      destroyChart();
      tableHost.textContent = "";
      setStatus(format(S.aggError, { error: state.error }), true);
      return;
    }
    const view = buildView(state.data, state.exact, extra);
    setStatus(state.stale ? view.summary + " " + S.aggStale : view.summary, false);
    for (const text of view.notices) {
      notices.appendChild(element("div", "agg-notice", text));
    }
    if (view.canComputeExactly && !state.stale) {
      const exact = button("agg-exact", S.aggComputeExactly);
      exact.addEventListener("click", () => flow.run(state.request, { exact: true }));
      notices.appendChild(exact);
    }
    const live = !state.stale && state.caseId === getCase();
    renderTable(view, live);
    if (!state.stale) {
      drawChart(state.data);
    }
  }

  const flow = createAggregateFlow({ api, getCase, onState: render });

  function run() {
    readForm();
    const built = buildRequest(form, getFilter());
    if (built.error) {
      setStatus(built.error, true);
      return Promise.resolve();
    }
    extra = form.onlyErrors ? { sev: { le: 2 } } : null;
    return flow.run(built.request);
  }

  runButton.addEventListener("click", () => run());
  csvButton.addEventListener("click", async () => {
    try {
      const text = await flow.csv();
      const name = csvFileName(getCase());
      saveText(text, name);
      setStatus(format(S.aggCsvSaved, { file: name }), false);
    } catch (err) {
      setStatus(format(S.aggError, { error: err && err.message ? err.message : S.errorGeneric }), true);
    }
  });
  copyButton.addEventListener("click", async () => {
    if (flow.state.status !== "ready") {
      return;
    }
    try {
      await copyText(tableText(buildView(flow.state.data, flow.state.exact, extra)));
      setStatus(S.aggCopied, false);
    } catch (err) {
      setStatus(S.aggCopyFailed, true);
    }
  });

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
        readForm();
        paintForm();
      }
    } catch (err) {
      // Without the key types the panel still offers columns, time and parsed fields.
    }
  }

  paintForm();
  render(flow.state);

  return {
    open() {
      readForm();
      paintForm();
      loadKeyTypes();
    },
    close() {
      destroyChart();
      flow.cancel();
    },
    viewChanged() {
      flow.markStale();
    },
    run,
    flow,
  };
}

// Adds the toolbar button and the host panel. Options:
//   toolbar, beforeButton    where the button goes; panelParent, beforePanel  where the panel goes
//   api, getCase(), getFilter()   the current case and the ONE composed filter of the grid
//   getFieldNames()          parsed-field names seen so far
//   onDrill(filter)          AND a row's filter into the user filter
// Returns { viewChanged() } to call whenever the case or the filter changed.
export function mountAggregate(options) {
  const { toolbar, beforeButton, panelParent, beforePanel } = options;
  const toggle = button("", S.aggButton);
  toggle.id = "aggregate-button";
  toggle.setAttribute("aria-expanded", "false");
  const host = element("section", "panel host-panel agg-host");
  host.id = "aggregate-host";
  host.hidden = true;
  toggle.setAttribute("aria-controls", host.id);
  toolbar.insertBefore(toggle, beforeButton || null);
  panelParent.insertBefore(host, beforePanel || null);
  const panel = initPanel(host, options);
  toggle.addEventListener("click", () => {
    host.hidden = !host.hidden;
    toggle.setAttribute("aria-expanded", host.hidden ? "false" : "true");
    if (host.hidden) {
      panel.close();
    } else {
      panel.open();
    }
  });
  return {
    viewChanged() {
      if (!host.hidden) {
        panel.viewChanged();
      }
    },
    panel,
  };
}
