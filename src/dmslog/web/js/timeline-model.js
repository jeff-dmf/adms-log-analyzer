// Pure logic of the timeline (no DOM, no uPlot): bucket width, filter handling, series to chart
// data, selection window and the stale-response guard. Times are epoch ms of the log clock (UTC).
import { RequestGuard } from "./detail-model.js";
import { formatTs } from "./time.js";

// Same ladder as the server (store/query.py BUCKET_LADDER_MS).
export const BUCKET_LADDER_MS = [
  1000, 2000, 5000, 10000, 15000, 30000, 60000, 120000, 300000, 600000, 900000, 1800000,
  3600000, 7200000, 10800000, 21600000, 43200000, 86400000, 172800000, 604800000,
];
export const MAX_BUCKETS = 2000;
export const PIXELS_PER_BUCKET = 4;
export const MIN_DRAG_PX = 4;

// Smallest ladder width (>= minMs) that gives at most one bucket per PIXELS_PER_BUCKET pixels.
export function chooseBucketMs(spanMs, widthPx, minMs = 1000) {
  const wanted = Math.max(1, Math.min(MAX_BUCKETS, Math.floor(widthPx / PIXELS_PER_BUCKET)));
  const span = Math.max(1, spanMs);
  for (const width of BUCKET_LADDER_MS) {
    if (width >= minMs && span / width <= wanted) {
      return width;
    }
  }
  return BUCKET_LADDER_MS[BUCKET_LADDER_MS.length - 1];
}

// The timeline shows the data outside the time selection, so a top-level `ts` condition is
// removed from the filter (the selection is drawn as a band instead). Returns { filter, range }
// where range is { from, to } (epoch ms, inclusive) read from the removed conditions, or null.
export function splitTimeFilter(filter) {
  if (!filter || typeof filter !== "object") {
    return { filter: filter || null, range: null };
  }
  let range = null;
  const strip = (node) => {
    const keys = Object.keys(node);
    if (keys.length === 1 && keys[0] === "ts") {
      range = rangeOf(node.ts, range);
      return null;
    }
    if (keys.length === 1 && keys[0] === "and" && Array.isArray(node.and)) {
      const kept = node.and.map(strip).filter((child) => child !== null);
      if (kept.length === 0) {
        return null;
      }
      return kept.length === 1 ? kept[0] : { and: kept };
    }
    return node;
  };
  const result = strip(filter);
  return { filter: result, range };
}

function rangeOf(cond, previous) {
  if (!cond || typeof cond !== "object" || Array.isArray(cond)) {
    return previous;
  }
  const from = cond.ge !== undefined ? cond.ge : cond.gt !== undefined ? cond.gt + 1 : undefined;
  const to = cond.le !== undefined ? cond.le : cond.lt !== undefined ? cond.lt - 1 : undefined;
  const out = previous ? { ...previous } : { from: null, to: null };
  if (typeof from === "number") {
    out.from = out.from === null ? from : Math.max(out.from, from);
  }
  if (typeof to === "number") {
    out.to = out.to === null ? to : Math.min(out.to, to);
  }
  return out.from === null && out.to === null ? previous : out;
}

// Key that identifies one request: when it changes the previous response is obsolete.
export function requestKey(caseId, filter, split) {
  return JSON.stringify([caseId, filter || null, split]);
}

// Server response -> { xs, series, window, total } where xs are bucket starts in SECONDS (uPlot
// time scale), series[i] = { key, label, values, visible } holds plain per-bucket counts and
// window = { from, to } in ms (to exclusive). `labelOf(key)` names a series.
export function toChartData(response, labelOf) {
  const n = response && response.n_buckets ? response.n_buckets : 0;
  const bucketMs = response ? response.bucket_ms : 0;
  const xs = new Array(n);
  for (let i = 0; i < n; i += 1) {
    xs[i] = (response.start + i * bucketMs) / 1000;
  }
  const series = ((response && response.series) || []).map((s) => ({
    key: s.key,
    label: labelOf(s.key),
    values: s.counts,
  }));
  const window = n > 0 ? { from: response.start, to: response.start + n * bucketMs } : null;
  return { xs, series, window, bucketMs, total: response && response.total ? response.total : 0 };
}

// Cumulative stacking. `series` keeps its order (first = bottom). Hidden keys contribute 0.
// Returns arrays of cumulative tops in DRAW order (top of the stack first, so lower layers are
// painted over it), the matching series indexes and the maximum height.
export function stackSeries(series, hiddenKeys, n) {
  const running = new Array(n).fill(0);
  const tops = [];
  for (const s of series) {
    const hidden = hiddenKeys.has(s.key);
    for (let i = 0; i < n; i += 1) {
      running[i] += hidden ? 0 : s.values[i] || 0;
    }
    tops.push(running.slice());
  }
  const order = series.map((_s, index) => index).reverse();
  let max = 0;
  for (const v of running) {
    max = Math.max(max, v);
  }
  return { draw: order.map((index) => ({ index, tops: tops[index] })), max };
}

// ---- Alarm strip (split by severity) -------------------------------------------------------------
// Critical, Error and Warning are stacked bars on their own scale (the tallest bar fills the plot);
// Info and Noise are summed into one line on a second scale that tops out at NORMAL_LINE_FRACTION of
// the height, so a burst of errors is never flattened by 160 thousand ordinary records.
export const ABNORMAL_KEYS = [1, 2, 3];
export const NORMAL_KEYS = [4, 5];
export const NORMAL_SERIES_KEY = "normal"; // toggle id of the combined Info + Noise line
export const NORMAL_LINE_FRACTION = 0.4;
// Options of the uPlot stepped path of the Info + Noise line: `extend` draws the last bucket to the
// right edge of the plot (otherwise the path ends where the last bucket starts and a single bucket
// leaves no line at all).
export const NORMAL_LINE_PATH = { align: 1, extend: true };
export const ERRORS_ONLY_HIDDEN = [3, NORMAL_SERIES_KEY];

// hiddenKeys holds severity keys (1..3) and NORMAL_SERIES_KEY. Returns
// { bars: [{ key, index, tops }] (bottom layer first, tops are fractions 0..1 of the plot height),
//   line: [fraction 0..NORMAL_LINE_FRACTION per bucket] | null, peak: tallest stack in records,
//   normalPeak: highest Info + Noise bucket in records }.
export function alarmLayers(series, hiddenKeys, n) {
  const bars = series
    .map((s, index) => ({ s, index }))
    .filter(({ s }) => ABNORMAL_KEYS.includes(s.key))
    .sort((a, b) => a.s.key - b.s.key);
  const running = new Array(n).fill(0);
  const raw = [];
  for (const { s, index } of bars) {
    const hidden = hiddenKeys.has(s.key);
    for (let i = 0; i < n; i += 1) {
      running[i] += hidden ? 0 : s.values[i] || 0;
    }
    raw.push({ key: s.key, index, tops: running.slice() });
  }
  const peak = running.reduce((a, b) => Math.max(a, b), 0);
  const scaled = raw.map((layer) => ({ ...layer, tops: layer.tops.map((v) => (peak > 0 ? v / peak : 0)) }));
  const normal = new Array(n).fill(0);
  for (const s of series) {
    if (NORMAL_KEYS.includes(s.key)) {
      for (let i = 0; i < n; i += 1) {
        normal[i] += s.values[i] || 0;
      }
    }
  }
  const normalPeak = normal.reduce((a, b) => Math.max(a, b), 0);
  const showLine = !hiddenKeys.has(NORMAL_SERIES_KEY) && normalPeak > 0;
  const line = showLine ? normal.map((v) => (v / normalPeak) * NORMAL_LINE_FRACTION) : null;
  return { bars: scaled, line, peak, normalPeak };
}

// Draw order for uPlot: the top of the stack first so lower layers paint over it.
export function alarmDrawOrder(bars) {
  return bars.slice().reverse();
}

// Per-severity totals for the legend: [{ key, label, total }] for the three alarm series and, last,
// the combined Info + Noise line (key NORMAL_SERIES_KEY). Absent severities are listed with 0.
export function alarmTotals(series, labelOf, normalLabel) {
  const total = (keys) =>
    series.filter((s) => keys.includes(s.key)).reduce((sum, s) => sum + s.values.reduce((a, b) => a + b, 0), 0);
  const out = ABNORMAL_KEYS.map((key) => ({ key, label: labelOf(key), total: total([key]) }));
  out.push({ key: NORMAL_SERIES_KEY, label: normalLabel, total: total(NORMAL_KEYS) });
  return out;
}

// Whether the hidden set is exactly the "Errors only" view (Warning and the normal line hidden).
export function isErrorsOnly(hiddenKeys) {
  return hiddenKeys.size === ERRORS_ONLY_HIDDEN.length && ERRORS_ONLY_HIDDEN.every((k) => hiddenKeys.has(k));
}

// Per-bucket readout of the alarm strip: severity counts in order, hidden series left out.
export function alarmReadoutAt(series, hiddenKeys, idx, normalLabel) {
  const parts = [];
  for (const key of ABNORMAL_KEYS) {
    const s = series.find((x) => x.key === key);
    const count = s ? s.values[idx] || 0 : 0;
    if (count > 0 && !hiddenKeys.has(key)) {
      parts.push({ key, label: s.label, count });
    }
  }
  if (!hiddenKeys.has(NORMAL_SERIES_KEY)) {
    const count = series
      .filter((s) => NORMAL_KEYS.includes(s.key))
      .reduce((sum, s) => sum + (s.values[idx] || 0), 0);
    if (count > 0) {
      parts.push({ key: NORMAL_SERIES_KEY, label: normalLabel, count });
    }
  }
  return parts;
}

// Height (px) of the strip: collapsed or expanded, from the CSS tokens' values.
export function stripHeight(expanded, collapsedPx = 40, expandedPx = 120) {
  return expanded ? expandedPx : collapsedPx;
}

// "40px" / "120" -> number, or the fallback.
export function parsePx(text, fallback) {
  const value = Number.parseFloat(String(text || ""));
  return Number.isFinite(value) && value > 0 ? value : fallback;
}

// ISO calendar date of an epoch-seconds tick (UTC): "2030-01-15".
export function isoDate(sec) {
  return formatTs(sec * 1000).slice(0, 10);
}

// Axis label of a tick (UTC): the ISO date for a span of two days or more, date and minutes for two
// hours or more, otherwise date and seconds so ticks inside one minute stay distinct.
export function axisLabel(sec, spanSec) {
  if (spanSec >= 2 * 86400) {
    return isoDate(sec);
  }
  return formatTs(sec * 1000).slice(0, spanSec >= 7200 ? 16 : 19);
}

// Per-bucket readout for the bucket at `idx`: [{ key, label, count }] without hidden/zero series.
export function readoutAt(series, hiddenKeys, idx) {
  const out = [];
  for (const s of series) {
    const count = s.values[idx] || 0;
    if (count > 0 && !hiddenKeys.has(s.key)) {
      out.push({ key: s.key, label: s.label, count });
    }
  }
  return out;
}

// Drag selection in plot values (seconds) -> { from, to } in ms (inclusive), clamped to the
// data window; null when too narrow (a click) or outside the data.
export function selectionToRange(leftSec, rightSec, windowMs, widthPx) {
  if (!windowMs || !Number.isFinite(leftSec) || !Number.isFinite(rightSec)) {
    return null;
  }
  const lo = Math.max(windowMs.from, Math.floor(Math.min(leftSec, rightSec) * 1000));
  const hi = Math.min(windowMs.to - 1, Math.ceil(Math.max(leftSec, rightSec) * 1000));
  if (hi < lo) {
    return null;
  }
  const span = windowMs.to - windowMs.from;
  if (widthPx > 0 && ((hi - lo) / span) * widthPx < MIN_DRAG_PX) {
    return null;
  }
  return { from: lo, to: hi };
}

// Pixel box for a { from, to } range given a time -> x mapping (for drawing the active range).
export function rangeToBox(range, windowMs, posOf, plotWidth) {
  if (!range || !windowMs) {
    return null;
  }
  const from = range.from === null ? windowMs.from : range.from;
  const to = range.to === null ? windowMs.to : range.to;
  if (to < windowMs.from || from > windowMs.to) {
    return null;
  }
  const left = Math.max(0, posOf(Math.max(from, windowMs.from) / 1000));
  const right = Math.min(plotWidth, posOf(Math.min(to + 1, windowMs.to) / 1000));
  return right > left ? { left, width: right - left } : null;
}

// Index of the CSS colour token --tl-c<index>: 0..4 severities, 5..12 categorical, 13 "other".
export const COLOR_OTHER = 13;
export const CATEGORICAL_COLORS = 8;
export function colorIndex(split, key, position) {
  if (key === null || key === undefined) {
    return COLOR_OTHER;
  }
  if (split === "sev") {
    return Math.min(4, Math.max(0, Number(key) - 1));
  }
  return 5 + (position % CATEGORICAL_COLORS);
}

// Short count for the value axis: 950, 1.2k, 3M.
export function compactCount(value) {
  const abs = Math.abs(value);
  const trim = (x) => String(Math.round(x * 10) / 10);
  if (abs >= 1e9) {
    return trim(value / 1e9) + "G";
  }
  if (abs >= 1e6) {
    return trim(value / 1e6) + "M";
  }
  if (abs >= 1e3) {
    return trim(value / 1e3) + "k";
  }
  return String(Math.round(value));
}

// Start of a bucket as log text, "YYYY-MM-DD HH:MM:SS" (buckets are at least one second wide).
export function formatBucketStart(ms) {
  return formatTs(ms).slice(0, 19);
}

// Bucket width as { key, n } for the strings widthSeconds / widthMinutes / widthHours / widthDays.
export function widthParts(ms) {
  if (ms >= 86400000 && ms % 86400000 === 0) {
    return { key: "widthDays", n: ms / 86400000 };
  }
  if (ms >= 3600000 && ms % 3600000 === 0) {
    return { key: "widthHours", n: ms / 3600000 };
  }
  if (ms >= 60000 && ms % 60000 === 0) {
    return { key: "widthMinutes", n: ms / 60000 };
  }
  return { key: "widthSeconds", n: Math.round(ms / 100) / 10 };
}

// Runs one fetch at a time: a newer load (or invalidate) makes every older response "stale".
export class TimelineLoader {
  constructor() {
    this.guard = new RequestGuard();
  }

  invalidate() {
    this.guard.invalidate();
  }

  // fetch(signal) -> Promise<data>. Resolves { stale: true } when superseded (or aborted),
  // { stale: false, data } otherwise. Real errors of the current request are rethrown.
  async load(key, fetch) {
    const ticket = this.guard.begin(key, undefined);
    try {
      const data = await fetch(ticket.signal);
      if (!this.guard.isCurrent(ticket, key)) {
        return { stale: true };
      }
      return { stale: false, data };
    } catch (err) {
      if (!this.guard.isCurrent(ticket, key) || (err && err.name === "AbortError")) {
        return { stale: true };
      }
      throw err;
    }
  }
}
