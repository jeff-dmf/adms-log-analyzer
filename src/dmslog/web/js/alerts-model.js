// Pure model of the local alerts (no DOM): parsing of the `alert` SSE event, rule drafts, the filter that
// shows an alert's records, the browser-notification wrapper and the flow behind the panel and the banner.
// The server (store/alerts.py) is the authority; this only avoids round trips. Alert events carry ids,
// counts and the rule name only; the rule name is user text and only ever reaches the page as text.
import { createStreamGuard } from "./status-bar-model.js";


export const KINDS = ["match", "threshold", "open_operation"];
export const SEVERITIES = ["info", "warning", "critical"];
export const HISTORY_SHOWN = 50;
export const SEEN_LIMIT = 1000; // ids remembered to drop replayed events
export const MAX_NAME = 80; // store/views.py MAX_NAME_CHARS
export const NOTIFY_KEY = "dmslog.alerts.notify";
export const DEFAULT_MIN_INTERVAL = 30;
const MAX_IDS = 20;
export const MAX_COUNT = 100000; // store/alerts.py ROW_LIMIT: the most records a rule can count

const isInt = (v) => Number.isInteger(v) && Number.isSafeInteger(v);
const ints = (list) => (Array.isArray(list) ? list.filter(isInt).slice(0, MAX_IDS) : []);

// The `alert` event data (a JSON string) -> a normalized alert, or null when it is not one.
export function parseAlertEvent(raw) {
  let data = raw;
  if (typeof raw === "string") {
    try {
      data = JSON.parse(raw);
    } catch {
      return null;
    }
  }
  if (!data || typeof data !== "object" || typeof data.rule !== "string" || !isInt(data.count)) {
    return null;
  }
  return {
    id: isInt(data.id) ? data.id : null,
    rule: data.rule,
    kind: KINDS.includes(data.kind) ? data.kind : "match",
    severity: SEVERITIES.includes(data.severity) ? data.severity : "warning",
    count: data.count,
    sampleIds: ints(data.sample_ids),
    eventIds: ints(data.event_ids),
    tsFrom: isInt(data.ts_from) ? data.ts_from : null,
    tsTo: isInt(data.ts_to) ? data.ts_to : null,
    at: typeof data.at === "string" ? data.at : "",
  };
}

// The filter that shows the records of an alert: the operations for an open-operation alert, otherwise the
// rule's own filter inside the time span of the alert (the records of the cycle). `rule` may be missing
// (deleted since): then only the time span is applied. Returns null when nothing can be selected.
export function recordsFilter(alert, rule) {
  if (alert.eventIds.length > 0) {
    return { event: alert.eventIds };
  }
  const span = alert.tsFrom !== null && alert.tsTo !== null ? { ts: { ge: alert.tsFrom, le: alert.tsTo } } : null;
  const own = rule && rule.filter ? rule.filter : null;
  if (own && span) {
    return { and: [own, span] };
  }
  return own || span;
}

const INT = /^\d{1,9}$/;
const toInt = (text) => (INT.test(String(text).trim()) ? Number(String(text).trim()) : null);

// Draft of the form -> {body} or {error: code, field}. `filter` is the filter to keep (current filter or
// preset), required for the kinds that match records.
export function validateDraft(draft, existing = []) {
  const name = String(draft.name || "").replace(/\s+/g, " ").trim();
  if (name === "") {
    return { error: "name", field: "name" };
  }
  if ([...name].length > MAX_NAME) {
    return { error: "nameLong", field: "name" };
  }
  if (existing.some((r) => r.name.toLowerCase() === name.toLowerCase())) {
    return { error: "duplicate", field: "name" };
  }
  if (!KINDS.includes(draft.kind)) {
    return { error: "kind", field: "kind" };
  }
  const interval = toInt(draft.minInterval === undefined || draft.minInterval === "" ? DEFAULT_MIN_INTERVAL : draft.minInterval);
  if (interval === null || interval > 86400) {
    return { error: "interval", field: "minInterval" };
  }
  const body = {
    name, kind: draft.kind, severity: SEVERITIES.includes(draft.severity) ? draft.severity : "warning",
    enabled: true, min_interval_s: interval,
  };
  if (draft.kind === "open_operation") {
    if (draft.timeoutMode === "param") {
      const param = String(draft.timeoutParam || "").trim();
      if (!/^[A-Za-z][A-Za-z0-9_]{0,63}$/.test(param)) {
        return { error: "param", field: "timeoutParam" };
      }
      body.timeout_param = param;
    } else {
      const seconds = toInt(draft.timeoutS);
      if (seconds === null || seconds < 1 || seconds > 7 * 86400) {
        return { error: "timeout", field: "timeoutS" };
      }
      body.timeout_s = seconds;
    }
    const operation = String(draft.operation || "").trim();
    if (operation !== "") {
      body.operation = operation;
    }
    return { body };
  }
  if (!draft.filter || typeof draft.filter !== "object" || Object.keys(draft.filter).length === 0) {
    return { error: "filter", field: "filter" };
  }
  body.filter = draft.filter;
  if (draft.kind === "match") {
    const count = toInt(draft.minCount === undefined || draft.minCount === "" ? 1 : draft.minCount);
    if (count === null || count < 1 || count > MAX_COUNT) {
      return { error: "count", field: "minCount" };
    }
    body.min_count = count;
    return { body };
  }
  const threshold = toInt(draft.threshold);
  if (threshold === null || threshold < 1 || threshold >= MAX_COUNT) {
    return { error: "threshold", field: "threshold" };
  }
  const windowS = toInt(draft.windowS);
  if (windowS === null || windowS < 1 || windowS > 86400) {
    return { error: "window", field: "windowS" };
  }
  body.threshold = threshold;
  body.window_s = windowS;
  return { body };
}

// A rule as the server lists it -> the body to send back (drops what only the listing adds).
export function ruleBody(rule, changes = {}) {
  const { available, unavailable_message: _message, ...rest } = rule;
  void available;
  return { ...rest, ...changes };
}

// ---- browser notifications --------------------------------------------------------------------------
// Permission is asked only by an explicit user action (request()), never on its own. Every browser call is
// guarded: a missing or failing Notification API leaves the banner as the only signal.

export function createNotifier({ NotificationImpl, storage } = {}) {
  const api = NotificationImpl === undefined && typeof Notification === "function" ? Notification : NotificationImpl;
  const read = () => {
    try {
      return storage ? storage.getItem(NOTIFY_KEY) === "1" : false;
    } catch {
      return false;
    }
  };
  const write = (on) => {
    try {
      if (storage) {
        storage.setItem(NOTIFY_KEY, on ? "1" : "0");
      }
    } catch {
      // storage unavailable: the choice lasts until the page closes
    }
  };
  let wanted = read();
  const permission = () => {
    if (!api) {
      return "unsupported";
    }
    try {
      return typeof api.permission === "string" ? api.permission : "default";
    } catch {
      return "unsupported";
    }
  };
  return {
    supported: () => permission() !== "unsupported",
    permission,
    // Notifications on: the user turned them on AND the browser allows them.
    enabled: () => wanted && permission() === "granted",
    wanted: () => wanted,
    setWanted(on) {
      wanted = Boolean(on);
      write(wanted);
    },
    // Ask the browser (call from a click handler only). Resolves to the resulting permission.
    async request() {
      if (!api || typeof api.requestPermission !== "function") {
        return "unsupported";
      }
      try {
        await Promise.resolve(api.requestPermission());
      } catch {
        // some browsers reject; the permission is read back below
      }
      if (permission() === "granted") {
        wanted = true;
        write(true);
      }
      return permission();
    },
    // Show one notification; false when it was not shown (disabled, unsupported or the browser refused).
    notify(title, body, tag) {
      if (!this.enabled()) {
        return false;
      }
      try {
        const shown = new api(title, { body, tag: tag || undefined });
        void shown;
        return true;
      } catch {
        return false;
      }
    },
  };
}

// ---- the flow ------------------------------------------------------------------------------------------

// Options: api (alerts.rules/createRule/updateRule/deleteRule/history/clearHistory), getCase(), notifier,
// describe(alert) -> {title, body}, onChange(state). The state is plain data for the view.
export function createAlertsFlow({ api, getCase, notifier, describe, onChange }) {
  const seen = new Set(); // ids received so far (bounded, insertion order), apart from the visible history
  const remember = (id) => {
    if (id === null) {
      return;
    }
    seen.add(id);
    while (seen.size > SEEN_LIMIT) {
      seen.delete(seen.values().next().value);
    }
  };
  const state = { rules: [], history: [], banner: null, problems: [], invalid: false, error: null };
  let seq = 0;
  const emit = () => onChange && onChange(state);
  const fail = (err) => {
    state.error = err && err.message ? String(err.message) : "error";
    emit();
    return false;
  };

  async function refresh() {
    const caseId = getCase();
    seq += 1;
    const mine = seq;
    if (caseId === null) {
      state.rules = [];
      state.history = [];
      emit();
      return;
    }
    try {
      const [rules, history] = await Promise.all([api.rules(caseId), api.history(caseId)]);
      if (mine !== seq || caseId !== getCase()) {
        return; // a newer refresh or another case
      }
      state.rules = rules.rules || [];
      state.invalid = Boolean(rules.invalid);
      state.problems = rules.problems || [];
      const loaded = (history.alerts || []).map(parseAlertEvent).filter(Boolean);
      loaded.forEach((a) => remember(a.id));
      state.history = loaded.reverse().slice(0, HISTORY_SHOWN);
      state.error = null;
      emit();
    } catch (err) {
      if (mine === seq) {
        fail(err);
      }
    }
  }

  // A live `alert` event: history, banner and (when allowed) a browser notification.
  function receive(raw) {
    const alert = parseAlertEvent(raw);
    if (alert === null) {
      return null;
    }
    if (alert.id !== null && seen.has(alert.id)) {
      return null; // replayed after an SSE reconnect (or already loaded): no second banner or notification
    }
    remember(alert.id);
    state.history = [alert, ...state.history].slice(0, HISTORY_SHOWN);
    state.banner = { alert, more: state.banner ? state.banner.more + 1 : 0 };
    if (notifier && describe) {
      const text = describe(alert);
      notifier.notify(text.title, text.body, "dmslog-" + alert.rule);
    }
    emit();
    return alert;
  }

  return {
    state,
    refresh,
    receive,
    dismiss() {
      state.banner = null;
      emit();
    },
    reset() {
      seq += 1;
      seen.clear();
      state.rules = [];
      state.history = [];
      state.banner = null;
      state.error = null;
      emit();
    },
    async create(body) {
      try {
        await api.createRule(body);
      } catch (err) {
        return fail(err);
      }
      await refresh();
      return true;
    },
    async setEnabled(rule, on) {
      try {
        await api.updateRule(rule.name, ruleBody(rule, { enabled: Boolean(on) }));
      } catch (err) {
        return fail(err);
      }
      await refresh();
      return true;
    },
    async remove(rule) {
      try {
        await api.deleteRule(rule.name);
      } catch (err) {
        return fail(err);
      }
      await refresh();
      return true;
    },
    async clearHistory() {
      const caseId = getCase();
      if (caseId === null) {
        return true;
      }
      try {
        await api.clearHistory(caseId);
      } catch (err) {
        return fail(err);
      }
      state.history = [];
      state.banner = null;
      emit();
      return true;
    },
    recordsFilterFor(alert) {
      const rule = state.rules.find((r) => r.name === alert.rule) || null;
      return recordsFilter(alert, rule);
    },
  };
}

// ---- the live stream ---------------------------------------------------------------------------------

// Listens to the case's progress stream for `alert` events. The browser reconnects by itself after a dropped
// stream. Returns {close()}; without EventSource support it returns a no-op handle.
export function connectAlertStream({ EventSourceImpl, url, onAlert, onStopped }) {
  const Impl = EventSourceImpl === undefined && typeof EventSource === "function" ? EventSource : EventSourceImpl;
  if (!Impl) {
    return { close() {}, stopped: () => false };
  }
  const guard = createStreamGuard();
  let source = null;
  try {
    source = new Impl(url);
    const mine = source;
    source.addEventListener("alert", (event) => onAlert(event.data));
    source.addEventListener("open", () => guard.opened());
    // After 3 failures in a row the stream stops (the server is down); the owner starts it again on Retry.
    source.addEventListener("error", () => {
      if (source === mine && (mine.readyState === 2 ? guard.stop() : guard.failed())) {
        mine.close();
        source = null;
        if (typeof onStopped === "function") {
          onStopped();
        }
      }
    });
  } catch {
    source = null;
  }
  return {
    stopped: () => guard.stopped,
    close() {
      if (source) {
        source.close();
        source = null;
      }
    },
  };
}
