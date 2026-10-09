// Pure helpers and the loading flow of the adapter dashboard (no DOM, no uPlot).
// Names, keys and signatures come from the ERS (untrusted): only shaped here, shown via textContent.
import { formatDuration, buildEventRow } from "./events-model.js";
import { S, format } from "./strings.js";
import { formatTs } from "./time.js";

export const HIST_KEYS = ["ok", "business_error", "fail", "open", "truncated", "other"];
export const EXCEPTION_LIMIT = 1000;
export const LEVEL_LIMIT = 200;
export const LIFECYCLE_LIMIT = 100;
export const DEFAULT_BUCKET_MS = 60000;
export const METRICS = {
  calls: { cumulative: "calls", delta: "d_calls", label: () => S.dashMetricCalls },
  req: { cumulative: "req_bytes", delta: "d_req_bytes", label: () => S.dashMetricReq },
  resp: { cumulative: "resp_bytes", delta: "d_resp_bytes", label: () => S.dashMetricResp },
};

function count(value) {
  return Number.isFinite(value) ? value : 0;
}

// Rows of the per (family, name) table; durations are formatted, counts are plain numbers.
export function buildStatsRows(stats) {
  const groups = stats && Array.isArray(stats.groups) ? stats.groups : [];
  const rows = groups.map((g) => ({
    family: g.family || "",
    name: g.name || S.evUnnamed,
    rawName: g.name || "",
    n: count(g.n),
    ok: count(g.ok),
    business_error: count(g.business_error),
    fail: count(g.fail),
    timeout: count(g.timeout),
    open: count(g.open) + count(g.truncated),
    p50: formatDuration(g.p50_ms),
    p90: formatDuration(g.p90_ms),
    p99: formatDuration(g.p99_ms),
    max: formatDuration(g.max_ms),
  }));
  rows.sort((a, b) => b.n - a.n || a.family.localeCompare(b.family) || a.name.localeCompare(b.name));
  return rows;
}

export function buildStatsTotals(stats) {
  const t = stats && stats.totals;
  if (!t) {
    return null;
  }
  return {
    family: "",
    name: S.dashTotals,
    rawName: "",
    n: count(t.n),
    ok: count(t.ok),
    business_error: count(t.business_error),
    fail: count(t.fail),
    timeout: count(t.timeout),
    open: count(t.open) + count(t.truncated),
    p50: formatDuration(t.p50_ms),
    p90: formatDuration(t.p90_ms),
    p99: formatDuration(t.p99_ms),
    max: formatDuration(t.max_ms),
  };
}

// Smallest gap between consecutive bucket starts (ms): the bucket width the server used.
export function inferBucketMs(buckets) {
  let best = null;
  for (let i = 1; i < buckets.length; i += 1) {
    const gap = buckets[i].t - buckets[i - 1].t;
    if (gap > 0 && (best === null || gap < best)) {
      best = gap;
    }
  }
  return best === null ? DEFAULT_BUCKET_MS : best;
}

// Count of one series in a bucket {t, total, counts: {status: n}}. "other" gathers every status
// without a series of its own (timeout, unknown, ...).
function bucketCount(bucket, key) {
  const counts = bucket && bucket.counts && typeof bucket.counts === "object" ? bucket.counts : {};
  if (key !== "other") {
    return count(counts[key]);
  }
  let sum = 0;
  for (const [status, n] of Object.entries(counts)) {
    if (!HIST_KEYS.includes(status)) {
      sum += count(n);
    }
  }
  return sum;
}

// Status histogram -> chart data. xs are bucket starts in SECONDS (uPlot time scale); series that
// are zero everywhere are left out. windowMs = { from, to } (to exclusive).
export function histogramToChart(response) {
  const buckets = response && Array.isArray(response.buckets)
    ? response.buckets.filter((b) => Number.isFinite(b.t)).slice().sort((a, b) => a.t - b.t)
    : [];
  if (buckets.length === 0) {
    return { xs: [], series: [], windowMs: null, bucketMs: DEFAULT_BUCKET_MS, total: 0 };
  }
  const bucketMs = inferBucketMs(buckets);
  const series = [];
  let total = 0;
  for (const key of HIST_KEYS) {
    const values = buckets.map((b) => bucketCount(b, key));
    const sum = values.reduce((a, b) => a + b, 0);
    if (sum > 0) {
      series.push({ key, label: S.dashHistSeries[key] || key, values });
      total += sum;
    }
  }
  return {
    xs: buckets.map((b) => b.t / 1000),
    series,
    windowMs: { from: buckets[0].t, to: buckets[buckets.length - 1].t + bucketMs },
    bucketMs,
    total,
  };
}

// CSS token index of a histogram series (--ev-<key> in events.css).
export function histColorClass(key) {
  return "ev-h-" + key;
}

export function methodOptions(methods) {
  return (Array.isArray(methods) ? methods : []).map((m, index) => ({
    value: String(index),
    side: m.side,
    method: m.method,
    label: format(S.dashMethodOption, { side: m.side, method: m.method, calls: count(m.calls) }),
  }));
}

// Samples of one metric as a line: cumulative counter or per-sample delta. `null` keeps a gap
// where the server had no delta (first sample). xs are SECONDS, ascending, one point per instant.
export function seriesToChart(samples, metric, mode) {
  const spec = METRICS[metric] || METRICS.calls;
  const field = mode === "delta" ? spec.delta : spec.cumulative;
  const sorted = (Array.isArray(samples) ? samples : [])
    .filter((s) => Number.isFinite(s.ts))
    .slice()
    .sort((a, b) => a.ts - b.ts);
  const xs = [];
  const ys = [];
  for (const sample of sorted) {
    const x = sample.ts / 1000;
    const y = Number.isFinite(sample[field]) ? sample[field] : null;
    if (xs.length > 0 && xs[xs.length - 1] === x) {
      ys[ys.length - 1] = y;
    } else {
      xs.push(x);
      ys.push(y);
    }
  }
  return { xs, ys, label: spec.label(), hasData: ys.some((y) => y !== null) };
}

// Exceptions grouped client-side by event.key (the signature), most frequent first.
export function groupExceptions(events) {
  const groups = new Map();
  for (const event of Array.isArray(events) ? events : []) {
    const signature = event.key || event.name || "";
    let group = groups.get(signature);
    if (!group) {
      group = {
        signature: signature || S.dashNoSignature,
        count: 0,
        first: null,
        last: null,
        names: new Set(),
      };
      groups.set(signature, group);
    }
    group.count += 1;
    if (event.name) {
      group.names.add(event.name);
    }
    const ts = event.ts_start;
    if (ts !== null && ts !== undefined) {
      if (group.first === null || ts < group.first.ts_start) {
        group.first = event;
      }
      if (group.last === null || ts >= group.last.ts_start) {
        group.last = event;
      }
    }
  }
  return Array.from(groups.values())
    .map((g) => ({
      signature: g.signature,
      count: g.count,
      firstTime: g.first ? formatTs(g.first.ts_start) : "",
      lastTime: g.last ? formatTs(g.last.ts_start) : "",
      first: g.first,
      last: g.last,
      names: Array.from(g.names).sort().join(", "),
    }))
    .sort((a, b) => b.count - a.count || a.signature.localeCompare(b.signature));
}

// Log level windows (kind=level_window), in time order.
export function levelRows(events) {
  return (Array.isArray(events) ? events : [])
    .slice()
    .sort((a, b) => (a.ts_start || 0) - (b.ts_start || 0))
    .map((event) => ({
      event,
      level: event.name || event.key || "",
      from: formatTs(event.ts_start),
      to: event.ts_end === null || event.ts_end === undefined ? "" : formatTs(event.ts_end),
      duration: formatDuration(event.duration_ms),
      process: buildEventRow(event).process,
    }));
}

// Startups, restarts and gaps merged in time order.
export function lifecycleRows(groups) {
  const all = [];
  for (const list of groups) {
    for (const event of Array.isArray(list) ? list : []) {
      all.push(event);
    }
  }
  all.sort((a, b) => (a.ts_start || 0) - (b.ts_start || 0) || a.id - b.id);
  return all.map((event) => {
    const row = buildEventRow(event);
    return {
      event,
      kindLabel: row.kindLabel,
      time: row.time,
      process: row.process,
      what: [event.name, row.durationText].filter(Boolean).join(", "),
      suspect: row.clockSuspect,
    };
  });
}

function guard() {
  let generation = 0;
  let controller = null;
  return {
    begin() {
      generation += 1;
      if (controller) {
        controller.abort();
      }
      controller = new AbortController();
      return { token: generation, signal: controller.signal };
    },
    current(run) {
      return run.token === generation;
    },
  };
}

function section() {
  return { status: "loading", data: null, more: false, error: null };
}

// Loads every dashboard section independently (one failure does not blank the others).
// The histogram (operation name) and the method series have their own guards.
export function createDashboardFlow({ api, getCase, onState }) {
  const master = guard();
  const histGuard = guard();
  const seriesGuard = guard();
  const state = {
    caseId: null,
    status: "nocase",
    stats: section(),
    histogram: section(),
    histName: "",
    methods: section(),
    methodIndex: 0,
    series: { status: "none", data: null, error: null },
    exceptions: section(),
    levels: section(),
    lifecycle: section(),
  };

  function emit() {
    onState(state);
  }

  function errorText(error) {
    return error && error.message ? error.message : "";
  }

  function fill(target, run, signal, fetcher, shape) {
    return fetcher()
      .then((data) => {
        if (!master.current(run)) {
          return;
        }
        target.data = shape(data);
        target.more = Boolean(data && data.next !== null && data.next !== undefined);
        target.status = "ready";
        emit();
      })
      .catch((error) => {
        if (master.current(run) && !signal.aborted) {
          target.status = "error";
          target.error = errorText(error);
          emit();
        }
      });
  }

  async function loadHistogram(caseId) {
    const run = histGuard.begin();
    state.histogram = section();
    emit();
    const params = { kind: "operation" };
    if (state.histName) {
      params.name = state.histName;
    }
    try {
      const data = await api.eventHistogram(caseId, params, run.signal);
      if (!histGuard.current(run) || caseId !== state.caseId) {
        return;
      }
      state.histogram = { status: "ready", data, more: false, error: null };
      emit();
    } catch (error) {
      if (histGuard.current(run) && !run.signal.aborted) {
        state.histogram = { status: "error", data: null, more: false, error: errorText(error) };
        emit();
      }
    }
  }

  async function loadSeries(caseId) {
    const run = seriesGuard.begin();
    const methods = state.methods.data || [];
    const chosen = methods[state.methodIndex];
    if (!chosen) {
      state.series = { status: "none", data: null, error: null };
      emit();
      return;
    }
    state.series = { status: "loading", data: null, error: null };
    emit();
    try {
      const data = await api.statsSeries(
        caseId,
        { stream: chosen.stream_id, side: chosen.side, method: chosen.method },
        run.signal,
      );
      if (!seriesGuard.current(run) || caseId !== state.caseId) {
        return;
      }
      state.series = { status: "ready", data: Array.isArray(data.samples) ? data.samples : [], error: null };
      emit();
    } catch (error) {
      if (seriesGuard.current(run) && !run.signal.aborted) {
        state.series = { status: "error", data: null, error: errorText(error) };
        emit();
      }
    }
  }

  async function refresh() {
    const run = master.begin();
    histGuard.begin();
    seriesGuard.begin();
    const caseId = getCase() || null;
    state.caseId = caseId;
    state.status = caseId ? "loading" : "nocase";
    state.stats = section();
    state.histogram = section();
    state.histName = "";
    state.methods = section();
    state.methodIndex = 0;
    state.series = { status: "none", data: null, error: null };
    state.exceptions = section();
    state.levels = section();
    state.lifecycle = section();
    emit();
    if (!caseId) {
      return;
    }
    const signal = run.signal;
    const events = (kind, limit) => api.events(caseId, { kind, limit }, signal);
    const plain = (data) => (data && Array.isArray(data.events) ? data.events : []);
    const jobs = [
      fill(state.stats, run, signal, () => api.eventStats(caseId, { kind: "operation" }, signal), (d) => d),
      fill(state.exceptions, run, signal, () => events("exception", EXCEPTION_LIMIT), plain),
      fill(state.levels, run, signal, () => events("level_window", LEVEL_LIMIT), plain),
      fill(
        state.lifecycle,
        run,
        signal,
        async () => {
          const [startups, restarts, gaps] = await Promise.all([
            events("startup", LIFECYCLE_LIMIT), events("restart", LIFECYCLE_LIMIT), events("gap", LIFECYCLE_LIMIT),
          ]);
          return {
            lists: [plain(startups), plain(restarts), plain(gaps)],
            next: [startups, restarts, gaps].some((d) => d.next !== null && d.next !== undefined) ? true : null,
          };
        },
        (d) => d.lists,
      ),
      loadHistogram(caseId),
      (async () => {
        await fill(
          state.methods, run, signal, () => api.statsMethods(caseId, {}, signal),
          (d) => (d && Array.isArray(d.methods) ? d.methods : []),
        );
        if (master.current(run) && state.methods.status === "ready") {
          await loadSeries(caseId);
        }
      })(),
    ];
    await Promise.all(jobs);
    if (master.current(run)) {
      state.status = "ready";
      emit();
    }
  }

  function setHistogramName(name) {
    if (!state.caseId || state.caseId !== getCase()) {
      return Promise.resolve();
    }
    state.histName = name || "";
    return loadHistogram(state.caseId);
  }

  function selectMethod(index) {
    if (!state.caseId || state.caseId !== getCase()) {
      return Promise.resolve();
    }
    state.methodIndex = index;
    return loadSeries(state.caseId);
  }

  return { state, refresh, setHistogramName, selectMethod };
}
