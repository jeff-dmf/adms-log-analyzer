// Timeline: compact alarm strip of the entries matching the current filter (uPlot, vendored).
// Split by severity: Critical/Error/Warning as stacked bars on their own scale plus Info + Noise as a
// thin line on a second scale (see alarmLayers); other splits keep the plain stacked histogram.
// Drag selects a time range (reported through onRangeSelected), double-click clears it.
// Only text nodes and class names are written to the DOM; log-derived values never reach HTML.
import uPlot from "../vendor/uplot/uPlot.esm.min.js";
import { S, format, pluralKey } from "./strings.js";
import {
  NORMAL_LINE_PATH,
  NORMAL_SERIES_KEY,
  TimelineLoader,
  alarmDrawOrder,
  alarmLayers,
  alarmReadoutAt,
  alarmTotals,
  axisLabel,
  chooseBucketMs,
  colorIndex,
  compactCount,
  ERRORS_ONLY_HIDDEN,
  formatBucketStart,
  isErrorsOnly,
  parsePx,
  rangeToBox,
  readoutAt,
  requestKey,
  selectionToRange,
  splitTimeFilter,
  stackSeries,
  stripHeight,
  toChartData,
  widthParts,
} from "./timeline-model.js";

const EXPANDED_KEY = "dmslog.timelineExpanded";
const AXIS_SIZE = 14;
const LINE_WIDTH = 1.5;
const REFRESH_DELAY_MS = 120;
const SPLITS = [
  ["sev", "timelineSplitSev"],
  ["source", "timelineSplitSource"],
  ["tpl", "timelineSplitTpl"],
];

function readExpanded() {
  try {
    return window.localStorage.getItem(EXPANDED_KEY) === "1";
  } catch (err) {
    return false;
  }
}

function writeExpanded(value) {
  try {
    window.localStorage.setItem(EXPANDED_KEY, value ? "1" : "0");
  } catch (err) {
    // Storage may be blocked: the choice then lasts for this page only.
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

// container: empty element to fill. Options:
//   api               the api module object (needs histogram(caseId, filter, bucketMs, split, signal))
//   getFilter()       -> { caseId, filter } (filter = current filter tree incl. the time window) or null
//   onRangeSelected   ({ from, to }) after a drag (epoch ms of log time, inclusive), or null on clear
//   getSourceName(id) optional name for the "source" split
// Returns { refresh(), destroy() }; call refresh() whenever the case or the filter changed.
// onFailure(error) / onRecovered(): the histogram request failed (also the first one, with no chart yet) / worked again.
export function initTimeline(container, {
  api, getFilter, onRangeSelected, getSourceName, uplot = uPlot, onFailure, onRecovered,
}) {
  const root = element("div", "tl");
  const head = element("div", "tl-head");
  const splitLabel = element("label", "visually-hidden", S.timelineSplitLabel);
  const splitSelect = document.createElement("select");
  splitSelect.id = "timeline-split";
  splitLabel.htmlFor = splitSelect.id;
  for (const [value, labelKey] of SPLITS) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = S[labelKey];
    splitSelect.appendChild(option);
  }
  const status = element("span", "tl-status");
  status.setAttribute("role", "status");
  const undatedNote = element("span", "tl-undated");
  undatedNote.hidden = true;
  const legend = element("ul", "tl-legend");
  const errorsOnly = element("button", "tl-toggle", S.timelineErrorsOnly);
  errorsOnly.type = "button";
  errorsOnly.hidden = true;
  const expandButton = element("button", "tl-toggle");
  expandButton.type = "button";
  head.append(splitLabel, splitSelect, status, undatedNote, legend, errorsOnly, expandButton);
  const plot = element("div", "tl-plot");
  plot.title = S.timelineHint;
  root.append(head, plot);
  container.textContent = "";
  container.appendChild(root);

  const loader = new TimelineLoader();
  const hidden = new Set();
  let chart = null;
  let data = null; // result of toChartData for the current response
  let response = null;
  let range = null; // time condition of the current filter, drawn as the selection band
  let timer = null;
  let destroyed = false;
  let expanded = readExpanded();
  let statusText = ""; // the base status; a hovered bucket replaces it until the pointer leaves
  let forcedBucketMs = null; // set after a resize that calls for a clearly different bar width

  function labelOf(split, key) {
    if (key === null) {
      return S.timelineOther;
    }
    if (split === "sev") {
      return S.severityNames[key] || String(key);
    }
    if (split === "source") {
      const name = getSourceName ? getSourceName(key) : null;
      return name || format(S.timelineSourceLabel, { id: key });
    }
    return key === 0 ? S.timelineTplNone : format(S.timelineTplLabel, { id: key });
  }

  function cssColor(name, fallback) {
    const value = getComputedStyle(root).getPropertyValue(name).trim();
    return value || fallback;
  }

  function alarmMode() {
    return Boolean(response) && response.split === "sev";
  }

  function normalLabel() {
    return S.severityNames[4] + " + " + S.severityNames[5];
  }

  function plotHeight() {
    return stripHeight(
      expanded,
      parsePx(cssColor("--timeline-h", ""), 40),
      parsePx(cssColor("--timeline-h-expanded", ""), 120),
    );
  }

  function defaultReadout() {
    status.textContent = statusText;
  }

  // The last histogram could not be refreshed: the chart still shows the previous data, marked as out of date.
  let stale = false;
  let failed = false; // the last request failed (there may be no chart at all); `stale` is only "old data is shown"
  function setFailed(on, err) {
    const was = failed;
    failed = Boolean(on);
    if (failed && typeof onFailure === "function") {
      onFailure(err);
    } else if (!failed && was && typeof onRecovered === "function") {
      onRecovered();
    }
  }
  function setStale(on) {
    stale = Boolean(on);
    root.classList.toggle("tl-stale", stale);
  }

  function setStatus(text, isError) {
    statusText = text;
    status.textContent = text;
    status.classList.toggle("tl-error", Boolean(isError));
  }

  function renderExpand() {
    expandButton.textContent = expanded ? S.timelineCollapse : S.timelineExpand;
    expandButton.setAttribute("aria-expanded", expanded ? "true" : "false");
    root.classList.toggle("tl-expanded", expanded);
  }

  function renderUndated() {
    const count = response && Number.isFinite(response.undated) ? response.undated : 0;
    undatedNote.hidden = count <= 0;
    undatedNote.textContent = count > 0 ? format(S[pluralKey("timelineUndated", count)], { count: count.toLocaleString("en-US") }) : "";
  }

  function renderErrorsOnly() {
    errorsOnly.hidden = !alarmMode();
    errorsOnly.setAttribute("aria-pressed", isErrorsOnly(hidden) ? "true" : "false");
  }

  function destroyChart() {
    if (chart) {
      chart.destroy();
      chart = null;
    }
  }

  function renderLegend() {
    legend.textContent = "";
    if (!data) {
      return;
    }
    const entries = alarmMode()
      ? alarmTotals(data.series, (key) => labelOf("sev", key), normalLabel()).map((t) => ({
          key: t.key,
          label: t.label,
          total: t.total,
          swatch: t.key === NORMAL_SERIES_KEY ? "tl-swatch tl-swatch-line tl-c3" : "tl-swatch tl-c" + colorIndex("sev", t.key, 0),
        }))
      : data.series.map((s, position) => ({
          key: s.key,
          label: s.label,
          total: s.values.reduce((a, b) => a + b, 0),
          swatch: "tl-swatch tl-c" + colorIndex(response.split, s.key, position),
        }));
    for (const entry of entries) {
      const item = document.createElement("li");
      const button = element("button", "tl-legend-item");
      button.type = "button";
      button.setAttribute("aria-pressed", hidden.has(entry.key) ? "false" : "true");
      button.append(
        element("span", entry.swatch),
        document.createTextNode(entry.label + " " + compactCount(entry.total)),
      );
      button.addEventListener("click", () => {
        if (hidden.has(entry.key)) {
          hidden.delete(entry.key);
        } else {
          hidden.add(entry.key);
        }
        renderChart();
        renderLegend();
      });
      item.appendChild(button);
      legend.appendChild(item);
    }
    renderErrorsOnly();
  }

  function applyRange() {
    if (!chart || !data) {
      return;
    }
    const box = rangeToBox(range, data.window, (sec) => chart.valToPos(sec, "x"), chart.over.clientWidth);
    if (box) {
      chart.setSelect({ left: box.left, width: box.width, top: 0, height: chart.over.clientHeight }, false);
    } else {
      chart.setSelect({ left: 0, width: 0, top: 0, height: 0 }, false);
    }
  }

  function renderChart() {
    destroyChart();
    if (!data || data.series.length === 0) {
      plot.hidden = true;
      return;
    }
    plot.hidden = false;
    const n = data.xs.length;
    const muted = cssColor("--muted", "#888");
    const border = cssColor("--border", "#ccc");
    const alarm = alarmMode();
    const series = [{}];
    const columns = [data.xs];
    let yRange;
    if (alarm) {
      const layers = alarmLayers(data.series, hidden, n);
      for (const layer of alarmDrawOrder(layers.bars)) {
        const color = cssColor("--tl-c" + colorIndex("sev", layer.key, 0), "#888");
        series.push({
          label: labelOf("sev", layer.key),
          fill: color,
          stroke: color,
          width: 0,
          points: { show: false },
          paths: uplot.paths.bars({ size: [1, Infinity, 1], align: 1, gap: 0 }),
        });
        columns.push(layer.tops);
      }
      if (layers.line) {
        series.push({
          label: normalLabel(),
          stroke: cssColor("--tl-c3", "#6f7a8c"),
          width: LINE_WIDTH,
          points: { show: false },
          paths: uplot.paths.stepped(NORMAL_LINE_PATH),
        });
        columns.push(layers.line);
      }
      yRange = () => [0, 1];
    } else {
      const { draw, max } = stackSeries(data.series, hidden, n);
      for (const entry of draw) {
        const s = data.series[entry.index];
        const color = cssColor("--tl-c" + colorIndex(response.split, s.key, entry.index), "#888");
        series.push({
          label: s.label,
          fill: color,
          stroke: color,
          width: 0,
          points: { show: false },
          paths: uplot.paths.bars({ size: [1, Infinity, 1], align: 1, gap: 0 }),
        });
        columns.push(entry.tops);
      }
      yRange = () => [0, Math.max(1, max) * 1.08];
    }
    const spanSec = (data.window.to - data.window.from) / 1000;
    const axisBase = { stroke: muted, grid: { stroke: border, width: 1 }, ticks: { stroke: border, width: 1 } };
    const options = {
      width: Math.max(100, plot.clientWidth),
      height: plotHeight(),
      padding: [2, 0, 0, 0],
      // The log clock is stored as UTC: draw the axis with UTC getters, never the browser zone.
      tzDate: (ts) => uplot.tzDate(new Date(ts * 1000), "Etc/UTC"),
      legend: { show: false },
      cursor: { drag: { x: true, y: false, setScale: false }, points: { show: false } },
      select: { show: true, left: 0, top: 0, width: 0, height: 0 },
      scales: {
        x: { time: true, range: () => [data.window.from / 1000, data.window.to / 1000] },
        y: { range: yRange },
      },
      axes: [
        {
          ...axisBase,
          size: AXIS_SIZE,
          gap: 1,
          font: "10px system-ui, sans-serif",
          space: spanSec >= 2 * 86400 ? 80 : 110,
          values: (_u, ticks) => ticks.map((t) => axisLabel(t, spanSec)),
        },
        { show: alarm ? false : true, ...axisBase, size: 46, values: (_u, ticks) => ticks.map(compactCount) },
      ],
      series,
      hooks: {
        setSelect: [onSelect],
        setCursor: [onCursor],
        ready: [
          (u) => {
            u.over.addEventListener("dblclick", onClear);
            u.over.addEventListener("pointerleave", defaultReadout);
          },
        ],
      },
    };
    chart = new uplot(options, columns, plot);
    applyRange();
  }

  function onSelect(u) {
    if (!data || u.select.width <= 0) {
      return;
    }
    const left = u.posToVal(u.select.left, "x");
    const right = u.posToVal(u.select.left + u.select.width, "x");
    const picked = selectionToRange(left, right, data.window, u.over.clientWidth);
    if (picked === null) {
      applyRange();
      return;
    }
    range = picked;
    if (onRangeSelected) {
      onRangeSelected(picked);
    }
  }

  function onClear() {
    range = null;
    applyRange();
    if (onRangeSelected) {
      onRangeSelected(null);
    }
  }

  function onCursor(u) {
    const idx = u.cursor.idx;
    if (idx === null || idx === undefined || !data || idx >= data.xs.length) {
      defaultReadout();
      return;
    }
    const time = formatBucketStart(data.xs[idx] * 1000);
    const found = alarmMode() ? alarmReadoutAt(data.series, hidden, idx, normalLabel()) : readoutAt(data.series, hidden, idx);
    const parts = found.map((p) => p.label + ": " + p.count);
    status.textContent =
      parts.length > 0
        ? format(S.timelineReadout, { time, parts: parts.join(", ") })
        : format(S.timelineReadoutNone, { time });
  }

  function describeTotals() {
    const w = widthParts(response.bucket_ms);
    const width = format(S[w.key], { n: w.n });
    const template = S[pluralKey(response.exact ? "timelineTotal" : "timelineTotalApprox", response.total)];
    return format(template, { count: response.total, width });
  }

  async function load() {
    timer = null;
    if (destroyed) {
      return;
    }
    const request = getFilter();
    if (!request || request.caseId === null || request.caseId === undefined) {
      loader.invalidate();
      setStale(false);
      setFailed(false);
      response = null;
      data = null;
      renderChart();
      renderLegend();
      renderUndated();
      renderErrorsOnly();
      setStatus("", false);
      return;
    }
    const split = splitSelect.value;
    const parts = splitTimeFilter(request.filter);
    range = parts.range;
    const key = requestKey(request.caseId, parts.filter, split);
    const bucketMs = forcedBucketMs;
    setStatus(S.timelineLoading, false);
    try {
      const result = await loader.load(key, (signal) =>
        api.histogram(request.caseId, parts.filter, bucketMs, split, signal),
      );
      if (result.stale || destroyed) {
        return;
      }
      setStale(false);
      setFailed(false);
      response = result.data;
      data = toChartData(response, (k) => labelOf(response.split, k));
      renderChart();
      renderLegend();
      renderUndated();
      setStatus(data.series.length === 0 ? S.timelineEmpty : describeTotals(), false);
    } catch (err) {
      if (err && err.name === "AbortError") {
        return;
      }
      // Keep the previous chart (never mixed with the new answer) and say that it is out of date.
      setStale(data !== null);
      setFailed(true, err);
      setStatus(format(data !== null ? S.timelineStale : S.timelineError, { error: err && err.message ? err.message : "" }), true);
    }
  }

  // Refreshes now and resolves when the request is over (a recovery waits for it; refresh() only schedules).
  function refreshNow() {
    forcedBucketMs = null;
    if (timer !== null) {
      clearTimeout(timer);
      timer = null;
    }
    return load();
  }

  // Another case: the request in flight and the scheduled one are dropped, and what failed is forgotten.
  function invalidate() {
    if (timer !== null) {
      clearTimeout(timer);
      timer = null;
    }
    loader.invalidate();
    failed = false;
    setStale(false);
  }

  function refresh() {
    forcedBucketMs = null; // the server picks the width for a new filter
    schedule();
  }

  function schedule() {
    if (destroyed) {
      return;
    }
    if (timer !== null) {
      clearTimeout(timer);
    }
    timer = setTimeout(load, REFRESH_DELAY_MS);
  }

  // A much wider or narrower plot wants a different bar width: ask for it (whole minutes at least,
  // so the server keeps using the per-minute table).
  function rebucketIfNeeded() {
    if (!data || !response) {
      return;
    }
    const span = data.window.to - data.window.from;
    const ideal = chooseBucketMs(span, plot.clientWidth, 60000);
    const ratio = ideal / response.bucket_ms;
    if ((ratio >= 2 || ratio <= 0.5) && ideal !== forcedBucketMs) {
      forcedBucketMs = ideal;
      schedule();
    }
  }

  splitSelect.addEventListener("change", () => {
    hidden.clear();
    refresh();
  });
  errorsOnly.addEventListener("click", () => {
    const wasOn = isErrorsOnly(hidden);
    hidden.clear();
    if (!wasOn) {
      for (const key of ERRORS_ONLY_HIDDEN) {
        hidden.add(key);
      }
    }
    renderChart();
    renderLegend();
  });
  expandButton.addEventListener("click", () => {
    expanded = !expanded;
    writeExpanded(expanded);
    renderExpand();
    if (chart) {
      chart.setSize({ width: Math.max(100, plot.clientWidth), height: plotHeight() });
      applyRange();
    }
  });
  renderExpand();

  const resizeObserver =
    typeof ResizeObserver === "undefined"
      ? null
      : new ResizeObserver(() => {
          if (chart) {
            chart.setSize({ width: Math.max(100, plot.clientWidth), height: plotHeight() });
            applyRange();
            rebucketIfNeeded();
          }
        });
  if (resizeObserver) {
    resizeObserver.observe(plot);
  }
  // Canvas colours are read from CSS tokens: redraw when the theme changes.
  const themeObserver = new MutationObserver(() => {
    renderChart();
  });
  themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  const media = typeof window.matchMedia === "function" ? window.matchMedia("(prefers-color-scheme: dark)") : null;
  const onMedia = () => renderChart();
  if (media && media.addEventListener) {
    media.addEventListener("change", onMedia);
  }

  function destroy() {
    destroyed = true;
    if (timer !== null) {
      clearTimeout(timer);
    }
    loader.invalidate();
    destroyChart();
    if (resizeObserver) {
      resizeObserver.disconnect();
    }
    themeObserver.disconnect();
    if (media && media.removeEventListener) {
      media.removeEventListener("change", onMedia);
    }
    container.textContent = "";
  }

  refresh();
  return { refresh, refreshNow, invalidate, destroy, isStale: () => stale, hasFailure: () => failed };
}
