// Timeline: stacked histogram of the entries matching the current filter (uPlot, vendored).
// Drag selects a time range (reported through onRangeSelected), double-click clears it.
// Only text nodes and class names are written to the DOM; log-derived values never reach HTML.
import uPlot from "../vendor/uplot/uPlot.esm.js";
import { S, format } from "./strings.js";
import {
  TimelineLoader,
  chooseBucketMs,
  colorIndex,
  compactCount,
  formatBucketStart,
  rangeToBox,
  readoutAt,
  requestKey,
  selectionToRange,
  splitTimeFilter,
  stackSeries,
  toChartData,
  widthParts,
} from "./timeline-model.js";

const PLOT_HEIGHT = 120;
const REFRESH_DELAY_MS = 120;
const SPLITS = [
  ["sev", "timelineSplitSev"],
  ["source", "timelineSplitSource"],
  ["tpl", "timelineSplitTpl"],
];
const STYLESHEETS = ["/vendor/uplot/uPlot.min.css", "/css/timeline.css"];

function ensureStylesheets() {
  for (const href of STYLESHEETS) {
    if (!document.querySelector('link[rel="stylesheet"][href="' + href + '"]')) {
      const link = document.createElement("link");
      link.rel = "stylesheet";
      link.href = href;
      document.head.appendChild(link);
    }
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
export function initTimeline(container, { api, getFilter, onRangeSelected, getSourceName }) {
  ensureStylesheets();
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
  const legend = element("ul", "tl-legend");
  head.append(splitLabel, splitSelect, status, legend);
  const plot = element("div", "tl-plot");
  plot.title = S.timelineHint;
  const readout = element("div", "tl-readout");
  root.append(head, plot, readout);
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

  function defaultReadout() {
    readout.textContent = data && data.series.length > 0 ? S.timelineHint : "";
  }

  function setStatus(text, isError) {
    status.textContent = text;
    status.classList.toggle("tl-error", Boolean(isError));
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
    data.series.forEach((s, position) => {
      const item = document.createElement("li");
      const button = element("button", "tl-legend-item");
      button.type = "button";
      button.setAttribute("aria-pressed", hidden.has(s.key) ? "false" : "true");
      const total = s.values.reduce((a, b) => a + b, 0);
      button.append(
        element("span", "tl-swatch tl-c" + colorIndex(response.split, s.key, position)),
        document.createTextNode(s.label + " " + compactCount(total)),
      );
      button.addEventListener("click", () => {
        if (hidden.has(s.key)) {
          hidden.delete(s.key);
        } else {
          hidden.add(s.key);
        }
        renderChart();
        renderLegend();
      });
      item.appendChild(button);
      legend.appendChild(item);
    });
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
    const { draw, max } = stackSeries(data.series, hidden, n);
    const muted = cssColor("--muted", "#888");
    const border = cssColor("--border", "#ccc");
    const split = response.split;
    const series = [{}];
    for (const entry of draw) {
      const s = data.series[entry.index];
      const color = cssColor("--tl-c" + colorIndex(split, s.key, entry.index), "#888");
      series.push({
        label: s.label,
        fill: color,
        stroke: color,
        width: 0,
        points: { show: false },
        paths: uPlot.paths.bars({ size: [1, Infinity, 1], align: 1, gap: 0 }),
      });
    }
    const axisBase = { stroke: muted, grid: { stroke: border, width: 1 }, ticks: { stroke: border, width: 1 } };
    const options = {
      width: Math.max(100, plot.clientWidth),
      height: PLOT_HEIGHT,
      // The log clock is stored as UTC: draw the axis with UTC getters, never the browser zone.
      tzDate: (ts) => uPlot.tzDate(new Date(ts * 1000), "Etc/UTC"),
      legend: { show: false },
      cursor: { drag: { x: true, y: false, setScale: false }, points: { show: false } },
      select: { show: true, left: 0, top: 0, width: 0, height: 0 },
      scales: {
        x: { time: true, range: () => [data.window.from / 1000, data.window.to / 1000] },
        y: { range: () => [0, Math.max(1, max) * 1.08] },
      },
      axes: [axisBase, { ...axisBase, size: 46, values: (_u, ticks) => ticks.map(compactCount) }],
      series,
      hooks: {
        setSelect: [onSelect],
        setCursor: [onCursor],
        ready: [(u) => u.over.addEventListener("dblclick", onClear)],
      },
    };
    const columns = [data.xs, ...draw.map((entry) => entry.tops)];
    chart = new uPlot(options, columns, plot);
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
    const parts = readoutAt(data.series, hidden, idx).map((p) => p.label + ": " + p.count);
    readout.textContent =
      parts.length > 0
        ? format(S.timelineReadout, { time, parts: parts.join(", ") })
        : format(S.timelineReadoutNone, { time });
  }

  function describeTotals() {
    const w = widthParts(response.bucket_ms);
    const width = format(S[w.key], { n: w.n });
    const template = response.exact ? S.timelineTotal : S.timelineTotalApprox;
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
      response = null;
      data = null;
      renderChart();
      renderLegend();
      setStatus("", false);
      defaultReadout();
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
      response = result.data;
      data = toChartData(response, (k) => labelOf(response.split, k));
      renderChart();
      renderLegend();
      if (data.series.length === 0) {
        setStatus(S.timelineEmpty, false);
        readout.textContent = "";
      } else {
        setStatus(describeTotals(), false);
        defaultReadout();
      }
    } catch (err) {
      setStatus(format(S.timelineError, { error: err && err.message ? err.message : "" }), true);
    }
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

  const resizeObserver =
    typeof ResizeObserver === "undefined"
      ? null
      : new ResizeObserver(() => {
          if (chart) {
            chart.setSize({ width: Math.max(100, plot.clientWidth), height: PLOT_HEIGHT });
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
  return { refresh, destroy };
}
