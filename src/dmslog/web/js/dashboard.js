// "Adapter dashboard": operation statistics per (family, name), a status histogram over time,
// calls by method, exceptions grouped by signature, log level windows and startups/restarts/gaps.
// Everything comes from the ERS (untrusted text): textContent and class names only; one delegated
// listener per list. Charts are uPlot (vendored), coloured from the CSS tokens of the theme.
import uPlot from "../vendor/uplot/uPlot.esm.min.js";
import {
  HIST_KEYS, METRICS, buildStatsRows, buildStatsTotals, createDashboardFlow, groupExceptions, histColorClass,
  histogramToChart, levelRows, lifecycleRows, methodOptions, seriesToChart,
} from "./dashboard-model.js";
import { gridTarget } from "./events-model.js";
import { statusBadge } from "./events-panel.js";
import { createPanelShell, statusLine } from "./panel-shell.js";
import { S, format } from "./strings.js";
import { compactCount, formatBucketStart, readoutAt, stackSeries } from "./timeline-model.js";

const PLOT_HEIGHT = 160;

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

function cssColor(host, name, fallback) {
  const value = getComputedStyle(host).getPropertyValue(name).trim();
  return value || fallback;
}

let uid = 0;

// container: empty element to fill. Options:
//   api               needs eventStats, eventHistogram, statsMethods, statsSeries, events
//   getCase()         current case id or null
//   onShowInGrid(t)   t = { from, to, entryId }: set the grid time filter and select the entry
//   onRequestClose()  the dock closes the tab (the default hides the container)
// Returns { refresh() }; call it when the case changed or indexing finished.
export function initDashboard(container, { api, getCase, onShowInGrid, onRequestClose = null }) {
  uid += 1;
  const prefix = "dash" + uid + "-";

  const shell = createPanelShell(container, {
    title: S.dashTitle,
    onRequestClose,
    onRefresh: () => flow.refresh(),
    refreshLabel: S.dashRefresh,
  });
  const status = statusLine(document, "ev-status");
  const body = element("div", "dash-body");

  function block(title) {
    const wrap = element("section", "dash-block");
    wrap.appendChild(element("h3", "", title));
    const content = element("div", "dash-content");
    wrap.appendChild(content);
    body.appendChild(wrap);
    return content;
  }
  const statsHost = block(S.dashStatsTitle);
  const histHost = block(S.dashHistTitle);
  const methodsHost = block(S.dashMethodsTitle);
  const exceptionsHost = block(S.dashExceptionsTitle);
  const levelsHost = block(S.dashLevelsTitle);
  const lifecycleHost = block(S.dashLifecycleTitle);
  shell.body.append(status, body);

  // Grid targets of the buttons of each list, kept by index so the delegated click finds them again.
  const targets = { exceptions: [], levels: [], lifecycle: [] };
  function targetFor(list, event) {
    const target = gridTarget(event);
    if (target === null) {
      return null;
    }
    targets[list].push(target);
    return list + ":" + (targets[list].length - 1);
  }

  function showSection(host, section, render) {
    host.textContent = "";
    if (section.status === "loading") {
      host.appendChild(element("div", "ev-muted", S.dashLoading));
    } else if (section.status === "error") {
      host.appendChild(element("div", "ev-error", S.dashSectionError + (section.error ? ": " + section.error : "")));
    } else {
      render(section);
    }
  }

  function showButton(list, label, event, aria) {
    const index = targetFor(list, event);
    if (index === null) {
      return element("span");
    }
    const button = element("button", "dash-show", label);
    button.type = "button";
    button.dataset.target = index;
    if (aria) {
      button.setAttribute("aria-label", aria);
    }
    return button;
  }

  // -- Statistics table -----------------------------------------------------------------------

  function statsRow(row, totals) {
    const tr = element("tr", totals ? "dash-total" : "");
    tr.appendChild(element("td", "", row.family));
    const nameCell = element("td", "ev-mono is-text ev-name-cell");
    if (totals || row.rawName === "") {
      nameCell.textContent = row.name;
    } else {
      const pick = element("button", "dash-pick ev-mono", row.name);
      pick.type = "button";
      pick.dataset.name = row.rawName;
      pick.setAttribute("aria-label", format(S.dashPickName, { name: row.name }));
      nameCell.appendChild(pick);
    }
    tr.appendChild(nameCell);
    for (const value of [row.n, row.ok, row.business_error, row.fail, row.timeout, row.open]) {
      tr.appendChild(element("td", "ev-num", String(value)));
    }
    for (const value of [row.p50, row.p90, row.p99, row.max]) {
      tr.appendChild(element("td", "ev-num", value));
    }
    return tr;
  }

  function renderStats(section) {
    const rows = buildStatsRows(section.data);
    if (rows.length === 0) {
      statsHost.appendChild(element("div", "ev-muted", S.dashEmpty));
      return;
    }
    const table = element("table", "ui-table ev-table dash-stats");
    table.appendChild(element("caption", "visually-hidden", S.dashStatsCaption));
    table.appendChild(headRow([
      S.dashColFamily, S.dashColName, S.dashColCount, S.dashColOk, S.dashColBusiness, S.dashColFail,
      S.dashColTimeout, S.dashColOpen, S.dashColP50, S.dashColP90, S.dashColP99, S.dashColMax,
    ]));
    const tbody = element("tbody");
    for (const row of rows) {
      tbody.appendChild(statsRow(row, false));
    }
    const totals = buildStatsTotals(section.data);
    if (totals) {
      tbody.appendChild(statsRow(totals, true));
    }
    table.appendChild(tbody);
    const wrap = element("div", "dash-scroll");
    wrap.appendChild(table);
    statsHost.appendChild(wrap);
  }

  // -- Charts ---------------------------------------------------------------------------------

  function destroyCharts(list) {
    for (const chart of list.splice(0)) {
      chart.destroy();
    }
  }
  const histCharts = [];
  const methodCharts = [];
  const hiddenHist = new Set();
  let histData = null;
  let histPlot = null;
  let histReadout = null;
  let seriesPlot = null;
  let lastSeries = null;
  let refocusMethod = false;
  let metric = "calls";
  let mode = "cumulative";

  function axisBase(host) {
    const muted = cssColor(host, "--muted", "#888");
    const border = cssColor(host, "--border", "#ccc");
    return { stroke: muted, grid: { stroke: border, width: 1 }, ticks: { stroke: border, width: 1 } };
  }

  function drawHistogram() {
    destroyCharts(histCharts);
    if (!histPlot || !histData || histData.series.length === 0) {
      return;
    }
    const n = histData.xs.length;
    const { draw, max } = stackSeries(histData.series, hiddenHist, n);
    const series = [{}];
    for (const entry of draw) {
      const s = histData.series[entry.index];
      const color = cssColor(histPlot, "--ev-h-" + s.key, "#888");
      series.push({
        label: s.label,
        fill: color,
        stroke: color,
        width: 0,
        points: { show: false },
        paths: uPlot.paths.bars({ size: [1, Infinity, 1], align: 1, gap: 0 }),
      });
    }
    const axis = axisBase(histPlot);
    const options = {
      width: Math.max(200, histPlot.clientWidth),
      height: PLOT_HEIGHT,
      tzDate: (ts) => uPlot.tzDate(new Date(ts * 1000), "Etc/UTC"),
      legend: { show: false },
      cursor: { drag: { x: false, y: false }, points: { show: false } },
      scales: {
        x: { time: true, range: () => [histData.windowMs.from / 1000, histData.windowMs.to / 1000] },
        y: { range: () => [0, Math.max(1, max) * 1.08] },
      },
      axes: [axis, { ...axis, size: 46, values: (_u, ticks) => ticks.map(compactCount) }],
      series,
      hooks: {
        setCursor: [(u) => {
          const idx = u.cursor.idx;
          if (idx === null || idx === undefined || idx >= histData.xs.length) {
            histReadout.textContent = S.dashHistHint;
            return;
          }
          const time = formatBucketStart(histData.xs[idx] * 1000);
          const parts = readoutAt(histData.series, hiddenHist, idx).map((p) => p.label + ": " + p.count);
          histReadout.textContent = parts.length > 0
            ? format(S.dashHistReadout, { time, parts: parts.join(", ") })
            : format(S.dashHistReadoutNone, { time });
        }],
      },
    };
    const columns = [histData.xs, ...draw.map((entry) => entry.tops)];
    histCharts.push(new uPlot(options, columns, histPlot));
  }

  function renderHistogram(state) {
    histHost.textContent = "";
    histData = histogramToChart(state.histogram.data);
    const head = element("div", "dash-chart-head");
    head.appendChild(element("span", "ev-muted", state.histName
      ? format(S.dashHistFor, { name: state.histName })
      : S.dashHistAll));
    if (state.histName) {
      const clear = element("button", "", S.dashHistClear);
      clear.type = "button";
      clear.dataset.action = "clear-name";
      head.appendChild(clear);
    }
    histHost.appendChild(head);
    if (histData.series.length === 0) {
      histPlot = null;
      histHost.appendChild(element("div", "ev-muted", S.dashEmpty));
      return;
    }
    const legend = element("ul", "dash-legend");
    for (const s of histData.series) {
      const item = element("li");
      const button = element("button", "dash-legend-item");
      button.type = "button";
      button.dataset.key = s.key;
      button.setAttribute("aria-pressed", hiddenHist.has(s.key) ? "false" : "true");
      const total = s.values.reduce((a, b) => a + b, 0);
      button.append(element("span", "dash-swatch " + histColorClass(s.key)), document.createTextNode(s.label + " " + compactCount(total)));
      item.appendChild(button);
      legend.appendChild(item);
    }
    histPlot = element("div", "dash-plot");
    histReadout = element("div", "ev-muted dash-readout", S.dashHistHint);
    histHost.append(legend, histPlot, histReadout);
    drawHistogram();
  }

  function drawSeries() {
    destroyCharts(methodCharts);
    if (!seriesPlot || !lastSeries) {
      return;
    }
    const chart = seriesToChart(lastSeries, metric, mode);
    if (!chart.hasData) {
      return;
    }
    const color = cssColor(seriesPlot, "--tl-c5", "#0072b2");
    const axis = axisBase(seriesPlot);
    const options = {
      width: Math.max(200, seriesPlot.clientWidth),
      height: PLOT_HEIGHT,
      tzDate: (ts) => uPlot.tzDate(new Date(ts * 1000), "Etc/UTC"),
      legend: { show: false },
      cursor: { drag: { x: false, y: false } },
      scales: { x: { time: true } },
      axes: [axis, { ...axis, size: 56, values: (_u, ticks) => ticks.map(compactCount) }],
      series: [{}, { label: chart.label, stroke: color, width: 2, spanGaps: false, points: { show: chart.xs.length < 60 } }],
    };
    methodCharts.push(new uPlot(options, [chart.xs, chart.ys], seriesPlot));
  }

  function selectControl(labelText, id, options, value) {
    const wrap = element("span", "dash-control");
    const label = element("label", "", labelText);
    const select = element("select");
    select.id = prefix + id;
    label.htmlFor = select.id;
    for (const option of options) {
      const node = element("option", "", option.label);
      node.value = option.value;
      select.appendChild(node);
    }
    select.value = value;
    wrap.append(label, select);
    return { wrap, select };
  }

  function renderMethods(state) {
    methodsHost.textContent = "";
    destroyCharts(methodCharts);
    const options = methodOptions(state.methods.data);
    if (options.length === 0) {
      seriesPlot = null;
      methodsHost.appendChild(element("div", "ev-muted", S.dashMethodNone));
      return;
    }
    const controls = element("div", "dash-controls");
    const method = selectControl(S.dashSide, "method", options, String(state.methodIndex));
    const metricControl = selectControl(
      S.dashMetric, "metric", Object.keys(METRICS).map((key) => ({ value: key, label: METRICS[key].label() })), metric,
    );
    const modeControl = selectControl(S.dashMode, "mode", [
      { value: "cumulative", label: S.dashModeCumulative }, { value: "delta", label: S.dashModeDelta },
    ], mode);
    method.select.dataset.control = "method";
    metricControl.select.dataset.control = "metric";
    modeControl.select.dataset.control = "mode";
    controls.append(method.wrap, metricControl.wrap, modeControl.wrap);
    if (refocusMethod) {
      refocusMethod = state.series.status === "loading";
      queueMicrotask(() => method.select.focus());
    }
    seriesPlot = element("div", "dash-plot");
    const note = element("div", "ev-muted");
    methodsHost.append(controls, seriesPlot, note);
    const series = state.series;
    lastSeries = series.status === "ready" ? series.data : null;
    if (series.status === "loading") {
      note.textContent = S.dashLoading;
    } else if (series.status === "error") {
      note.className = "ev-error";
      note.textContent = S.dashSectionError + (series.error ? ": " + series.error : "");
    } else if (series.status === "ready") {
      if (seriesToChart(series.data, metric, mode).hasData) {
        drawSeries();
      } else {
        note.textContent = S.dashSeriesEmpty;
      }
    }
  }

  // -- Lists ----------------------------------------------------------------------------------

  function renderExceptions(section) {
    targets.exceptions = [];
    const groups = groupExceptions(section.data);
    if (groups.length === 0) {
      exceptionsHost.appendChild(element("div", "ev-muted", S.dashEmpty));
      return;
    }
    const table = element("table", "ui-table ev-table");
    table.appendChild(element("caption", "visually-hidden", S.dashExceptionsCaption));
    table.appendChild(headRow([S.dashColSignature, S.dashColOccurrences, S.dashColFirst, S.dashColLast]));
    const tbody = element("tbody");
    for (const group of groups) {
      const tr = element("tr");
      const signature = element("td", "ev-mono ev-text", group.signature);
      if (group.names && group.names !== group.signature) {
        signature.appendChild(element("div", "ev-muted", group.names));
      }
      tr.appendChild(signature);
      tr.appendChild(element("td", "ev-num", String(group.count)));
      const first = element("td");
      if (group.first) {
        first.append(element("span", "ev-mono", group.firstTime + " "), showButton("exceptions", S.dashShowFirst, group.first));
      }
      const last = element("td");
      if (group.last) {
        last.append(element("span", "ev-mono", group.lastTime + " "), showButton("exceptions", S.dashShowLast, group.last));
      }
      tr.append(first, last);
      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    exceptionsHost.appendChild(table);
    if (section.more) {
      exceptionsHost.appendChild(element("div", "ev-muted", S.dashMore));
    }
  }

  function renderLevels(section) {
    targets.levels = [];
    const rows = levelRows(section.data);
    if (rows.length === 0) {
      levelsHost.appendChild(element("div", "ev-muted", S.dashEmpty));
      return;
    }
    const table = element("table", "ui-table ev-table");
    table.appendChild(element("caption", "visually-hidden", S.dashLevelsCaption));
    table.appendChild(headRow([S.dashColLevel, S.dashColFrom, S.dashColTo, S.dashColDuration, S.evColProcess, ""]));
    const tbody = element("tbody");
    for (const row of rows) {
      const tr = element("tr");
      tr.appendChild(element("td", "ev-mono", row.level));
      tr.appendChild(element("td", "ev-mono", row.from));
      tr.appendChild(element("td", "ev-mono", row.to));
      tr.appendChild(element("td", "ev-num", row.duration));
      tr.appendChild(element("td", "", row.process));
      const action = element("td");
      action.appendChild(showButton("levels", S.evShowInGrid, row.event, format(S.dashOpenInGrid, { time: row.from })));
      tr.appendChild(action);
      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    levelsHost.appendChild(table);
    if (section.more) {
      levelsHost.appendChild(element("div", "ev-muted", S.dashMore));
    }
  }

  function renderLifecycle(section) {
    targets.lifecycle = [];
    const rows = lifecycleRows(section.data);
    if (rows.length === 0) {
      lifecycleHost.appendChild(element("div", "ev-muted", S.dashEmpty));
      return;
    }
    const table = element("table", "ui-table ev-table");
    table.appendChild(element("caption", "visually-hidden", S.dashLifecycleCaption));
    table.appendChild(headRow([S.dashColKind, S.dashColWhen, S.evColProcess, S.dashColWhat, ""]));
    const tbody = element("tbody");
    for (const row of rows) {
      const tr = element("tr");
      tr.appendChild(element("td", "", row.kindLabel));
      tr.appendChild(element("td", "ev-mono", row.time));
      tr.appendChild(element("td", "", row.process));
      const what = element("td", "ev-mono", row.what);
      if (row.suspect) {
        what.append(" ", statusBadge(S.dashClockSuspect, "warn"));
      }
      tr.appendChild(what);
      const action = element("td");
      action.appendChild(showButton("lifecycle", S.evShowInGrid, row.event, format(S.dashOpenInGrid, { time: row.time })));
      tr.appendChild(action);
      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    lifecycleHost.appendChild(table);
    if (section.more) {
      lifecycleHost.appendChild(element("div", "ev-muted", S.dashMore));
    }
  }

  // -- State -> DOM ---------------------------------------------------------------------------

  // A section is redrawn only when its object or status changed (answers arrive one by one).
  const shown = new Map();
  function changed(key, value, extra) {
    const previous = shown.get(key);
    const token = value.status + ":" + String(extra);
    if (previous && previous.mark === value && previous.token === token) {
      return false;
    }
    shown.set(key, { mark: value, token });
    return true;
  }

  function onState(state) {
    if (state.status === "nocase") {
      status.textContent = S.dashNoCase;
    } else if (state.status === "loading") {
      status.textContent = S.dashLoading;
    } else {
      status.textContent = "";
    }
    if (state.status === "nocase") {
      for (const host of [statsHost, histHost, methodsHost, exceptionsHost, levelsHost, lifecycleHost]) {
        host.textContent = "";
      }
      destroyCharts(histCharts);
      destroyCharts(methodCharts);
      shown.clear();
      return;
    }
    if (changed("stats", state.stats, "")) {
      showSection(statsHost, state.stats, renderStats);
    }
    if (changed("hist", state.histogram, state.histName)) {
      destroyCharts(histCharts);
      showSection(histHost, state.histogram, () => renderHistogram(state));
    }
    if (changed("methods", state.methods, state.methodIndex + ":" + state.series.status + ":" + (state.series.data ? state.series.data.length : 0))) {
      showSection(methodsHost, state.methods, () => renderMethods(state));
    }
    if (changed("exceptions", state.exceptions, "")) {
      showSection(exceptionsHost, state.exceptions, renderExceptions);
    }
    if (changed("levels", state.levels, "")) {
      showSection(levelsHost, state.levels, renderLevels);
    }
    if (changed("lifecycle", state.lifecycle, "")) {
      showSection(lifecycleHost, state.lifecycle, renderLifecycle);
    }
  }

  const flow = createDashboardFlow({ api, getCase, onState });

  body.addEventListener("click", (event) => {
    if (!(event.target instanceof Element)) {
      return;
    }
    const pick = event.target.closest("button[data-name]");
    if (pick) {
      flow.setHistogramName(pick.dataset.name);
      return;
    }
    if (event.target.closest("button[data-action=clear-name]")) {
      flow.setHistogramName("");
      return;
    }
    const legend = event.target.closest("button[data-key]");
    if (legend && HIST_KEYS.includes(legend.dataset.key)) {
      const key = legend.dataset.key;
      if (hiddenHist.has(key)) {
        hiddenHist.delete(key);
      } else {
        hiddenHist.add(key);
      }
      legend.setAttribute("aria-pressed", hiddenHist.has(key) ? "false" : "true");
      drawHistogram();
      return;
    }
    const show = event.target.closest("button[data-target]");
    if (show) {
      const [list, index] = show.dataset.target.split(":");
      const target = targets[list] ? targets[list][Number(index)] : undefined;
      // The ids belong to the case the lists were loaded for.
      if (target && flow.state.caseId !== null && flow.state.caseId === getCase()) {
        onShowInGrid(target);
      }
    }
  });
  body.addEventListener("change", (event) => {
    const select = event.target instanceof Element ? event.target.closest("select[data-control]") : null;
    if (!select) {
      return;
    }
    if (select.dataset.control === "method") {
      refocusMethod = true; // the selector is rebuilt when the samples arrive
      flow.selectMethod(Number(select.value));
    } else if (select.dataset.control === "metric" && METRICS[select.value]) {
      metric = select.value;
      drawSeries();
    } else if (select.dataset.control === "mode") {
      mode = select.value === "delta" ? "delta" : "cumulative";
      drawSeries();
    }
  });

  // Canvas colours come from CSS tokens: redraw when the theme changes; resize with the container.
  const redraw = () => {
    drawHistogram();
    drawSeries();
  };
  new MutationObserver(redraw).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  if (typeof ResizeObserver !== "undefined") {
    new ResizeObserver(() => {
      for (const chart of [...histCharts, ...methodCharts]) {
        const host = chart.root.parentElement;
        if (host && host.clientWidth > 0) {
          chart.setSize({ width: Math.max(200, host.clientWidth), height: PLOT_HEIGHT });
        }
      }
    }).observe(body);
  }

  return { refresh: () => flow.refresh() };
}
