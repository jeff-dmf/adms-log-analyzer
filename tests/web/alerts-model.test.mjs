// Local alerts: event parsing, drafts, the records filter, the notifier and the flow with fake api,
// Notification and EventSource (no DOM, no real requests).
import test from "node:test";
import assert from "node:assert/strict";

import {
  HISTORY_SHOWN, NOTIFY_KEY, connectAlertStream, createAlertsFlow, createNotifier, parseAlertEvent, recordsFilter,
  ruleBody, validateDraft, MAX_COUNT, SEEN_LIMIT,
} from "../../src/dmslog/web/js/alerts-model.js";

const EVENT = {
  id: 7, rule: "Errors", kind: "match", severity: "critical", count: 12, sample_ids: [5, 6], ts_from: 100, ts_to: 900,
  event_ids: [], case: "demo", at: "2026-10-13T10:00:00+00:00",
};

test("parseAlertEvent accepts the server body and rejects anything else", () => {
  const alert = parseAlertEvent(JSON.stringify(EVENT));
  assert.equal(alert.rule, "Errors");
  assert.deepEqual(alert.sampleIds, [5, 6]);
  assert.equal(alert.tsFrom, 100);
  assert.equal(parseAlertEvent("{not json"), null);
  assert.equal(parseAlertEvent(JSON.stringify({ rule: 3, count: 1 })), null);
  assert.equal(parseAlertEvent(null), null);
  const odd = parseAlertEvent({ rule: "x", count: 1, severity: "weird", kind: "weird", sample_ids: ["a", 2, 1.5] });
  assert.equal(odd.severity, "warning");
  assert.equal(odd.kind, "match");
  assert.deepEqual(odd.sampleIds, [2]);
  assert.equal(parseAlertEvent({ rule: "<b>x</b>", count: 1 }).rule, "<b>x</b>"); // plain text, never markup
});

test("recordsFilter: operations by event id, otherwise rule filter inside the time span", () => {
  const filter = { sev: { le: 2 } };
  assert.deepEqual(recordsFilter(parseAlertEvent({ ...EVENT, event_ids: [3, 4] }), null), { event: [3, 4] });
  assert.deepEqual(recordsFilter(parseAlertEvent(EVENT), { filter }), { and: [filter, { ts: { ge: 100, le: 900 } }] });
  assert.deepEqual(recordsFilter(parseAlertEvent(EVENT), null), { ts: { ge: 100, le: 900 } }); // rule deleted
  assert.deepEqual(recordsFilter(parseAlertEvent({ ...EVENT, ts_from: null, ts_to: null }), { filter }), filter);
  assert.equal(recordsFilter(parseAlertEvent({ ...EVENT, ts_from: null, ts_to: null }), null), null);
});

test("validateDraft builds the request body or names the failing field", () => {
  const filter = { tpl: "WFM-081" };
  const ok = validateDraft({ name: "  WFM 081 ", kind: "match", severity: "info", filter });
  assert.deepEqual(ok.body, {
    name: "WFM 081", kind: "match", severity: "info", enabled: true, min_interval_s: 30, filter, min_count: 1,
  });
  assert.equal(validateDraft({ name: "", kind: "match", filter }).error, "name");
  assert.equal(validateDraft({ name: "a", kind: "match", filter: null }).error, "filter");
  assert.equal(validateDraft({ name: "a", kind: "match", filter: {} }).error, "filter");
  assert.equal(validateDraft({ name: "A", kind: "match", filter }, [{ name: "a" }]).error, "duplicate");
  assert.equal(validateDraft({ name: "a", kind: "match", filter, minCount: "0" }).error, "count");
  assert.equal(validateDraft({ name: "a", kind: "match", filter, minInterval: "x" }).error, "interval");
  assert.equal(validateDraft({ name: "a", kind: "nope", filter }).error, "kind");
  const burst = validateDraft({ name: "b", kind: "threshold", filter, threshold: "10", windowS: "60" });
  assert.equal(burst.body.threshold, 10);
  assert.equal(burst.body.window_s, 60);
  assert.equal(validateDraft({ name: "b", kind: "threshold", filter, threshold: "10", windowS: "0" }).error, "window");
  assert.equal(validateDraft({ name: "b", kind: "threshold", filter, windowS: "5" }).error, "threshold");
  const param = validateDraft({ name: "o", kind: "open_operation", timeoutMode: "param", timeoutParam: "SendTimeout", operation: " Send " });
  assert.deepEqual([param.body.timeout_param, param.body.operation, param.body.filter], ["SendTimeout", "Send", undefined]);
  assert.equal(validateDraft({ name: "o", kind: "open_operation", timeoutMode: "param", timeoutParam: "bad name" }).error, "param");
  assert.equal(validateDraft({ name: "o", kind: "open_operation", timeoutMode: "seconds", timeoutS: "45" }).body.timeout_s, 45);
  assert.equal(validateDraft({ name: "o", kind: "open_operation", timeoutMode: "seconds", timeoutS: "" }).error, "timeout");
});

test("ruleBody drops what only the listing adds", () => {
  const body = ruleBody({ name: "a", enabled: true, available: false, unavailable_message: "x" }, { enabled: false });
  assert.deepEqual(body, { name: "a", enabled: false });
});

// ---- fakes -------------------------------------------------------------------------------------------------

function fakeNotification({ permission = "default", grant = "granted", throwOnShow = false } = {}) {
  const shown = [];
  let asked = 0;
  class Fake {
    constructor(title, options) {
      if (throwOnShow) {
        throw new Error("blocked");
      }
      shown.push({ title, options });
    }
    static get permission() {
      return permission;
    }
    static requestPermission() {
      asked += 1;
      permission = grant;
      return Promise.resolve(grant);
    }
  }
  return { Fake, shown, asked: () => asked };
}

function memoryStorage() {
  const map = new Map();
  return { getItem: (k) => (map.has(k) ? map.get(k) : null), setItem: (k, v) => map.set(k, v), map };
}

test("the permission is never asked on its own, only by request()", async () => {
  const fake = fakeNotification();
  const storage = memoryStorage();
  const notifier = createNotifier({ NotificationImpl: fake.Fake, storage });
  assert.equal(notifier.supported(), true);
  assert.equal(notifier.permission(), "default");
  assert.equal(notifier.notify("t", "b"), false);
  assert.equal(fake.asked(), 0);
  assert.equal(await notifier.request(), "granted");
  assert.equal(fake.asked(), 1);
  assert.equal(storage.map.get(NOTIFY_KEY), "1");
  assert.equal(notifier.notify("t", "b", "tag"), true);
  assert.deepEqual(fake.shown[0], { title: "t", options: { body: "b", tag: "tag" } });
  notifier.setWanted(false);
  assert.equal(notifier.notify("t", "b"), false); // the user switched it off
});

test("denied, unsupported and failing browsers fall back silently", async () => {
  const denied = fakeNotification({ permission: "denied", grant: "denied" });
  const n1 = createNotifier({ NotificationImpl: denied.Fake, storage: memoryStorage() });
  assert.equal(await n1.request(), "denied");
  assert.equal(n1.enabled(), false);
  assert.equal(n1.notify("t", "b"), false);
  const none = createNotifier({ NotificationImpl: null, storage: null });
  assert.equal(none.supported(), false);
  assert.equal(none.permission(), "unsupported");
  assert.equal(await none.request(), "unsupported");
  const broken = fakeNotification({ permission: "granted", throwOnShow: true });
  const n2 = createNotifier({ NotificationImpl: broken.Fake, storage: memoryStorage() });
  n2.setWanted(true);
  assert.equal(n2.notify("t", "b"), false); // throws inside: caught, banner remains the signal
  const throwing = { get permission() { throw new Error("x"); } };
  assert.equal(createNotifier({ NotificationImpl: throwing }).permission(), "unsupported");
  const rejecting = { permission: "default", requestPermission: () => Promise.reject(new Error("no")) };
  assert.equal(await createNotifier({ NotificationImpl: rejecting }).request(), "default");
});

class FakeEventSource {
  static instances = [];
  constructor(url) {
    this.url = url;
    this.handlers = {};
    this.closed = false;
    FakeEventSource.instances.push(this);
  }
  addEventListener(name, fn) {
    this.handlers[name] = fn;
  }
  close() {
    this.closed = true;
  }
}

test("connectAlertStream forwards alert events and closes", () => {
  const got = [];
  const stream = connectAlertStream({ EventSourceImpl: FakeEventSource, url: "/p", onAlert: (d) => got.push(d) });
  const source = FakeEventSource.instances.at(-1);
  assert.equal(source.url, "/p");
  source.handlers.alert({ data: "{}" });
  assert.deepEqual(got, ["{}"]);
  stream.close();
  assert.equal(source.closed, true);
  stream.close(); // twice is fine
  const none = connectAlertStream({ EventSourceImpl: null, url: "/p", onAlert: () => {} });
  none.close();
  const throwing = connectAlertStream({ EventSourceImpl: class { constructor() { throw new Error("x"); } }, url: "/p", onAlert: () => {} });
  throwing.close();
});

function fakeApi({ rules = [], alerts = [] } = {}) {
  const calls = [];
  const store = { rules: [...rules], alerts: [...alerts] };
  return {
    calls, store,
    rules: async (c) => { calls.push(["rules", c]); return { rules: store.rules, invalid: false, problems: [] }; },
    history: async (c) => { calls.push(["history", c]); return { alerts: store.alerts }; },
    createRule: async (b) => { calls.push(["create", b]); store.rules.push(b); return b; },
    updateRule: async (n, b) => { calls.push(["update", n, b]); store.rules = store.rules.map((r) => (r.name === n ? b : r)); return b; },
    deleteRule: async (n) => { calls.push(["delete", n]); store.rules = store.rules.filter((r) => r.name !== n); },
    clearHistory: async (c) => { calls.push(["clear", c]); store.alerts = []; },
  };
}

test("flow: refresh loads rules and history (newest first)", async () => {
  const api = fakeApi({ rules: [{ name: "Errors", filter: { sev: 1 } }], alerts: [{ ...EVENT, id: 1 }, { ...EVENT, id: 2 }] });
  const flow = createAlertsFlow({ api, getCase: () => "demo" });
  await flow.refresh();
  assert.equal(flow.state.rules.length, 1);
  assert.deepEqual(flow.state.history.map((h) => h.id), [2, 1]);
});

test("flow: a stale refresh (case changed meanwhile) is dropped", async () => {
  let current = "A";
  const api = fakeApi({ rules: [{ name: "r" }] });
  const flow = createAlertsFlow({ api, getCase: () => current });
  const pending = flow.refresh();
  current = "B";
  await pending;
  assert.deepEqual(flow.state.rules, []);
});

test("flow: a live alert updates history and banner and notifies once when allowed", async () => {
  const fake = fakeNotification({ permission: "granted" });
  const notifier = createNotifier({ NotificationImpl: fake.Fake, storage: memoryStorage() });
  notifier.setWanted(true);
  const changes = [];
  const flow = createAlertsFlow({
    api: fakeApi(), getCase: () => "demo", notifier,
    describe: (a) => ({ title: "Alert " + a.rule, body: String(a.count) }),
    onChange: (s) => changes.push(s.banner && s.banner.more),
  });
  assert.equal(flow.receive("garbage"), null);
  flow.receive(JSON.stringify(EVENT));
  assert.equal(flow.state.banner.alert.rule, "Errors");
  assert.equal(flow.state.banner.more, 0);
  flow.receive(JSON.stringify({ ...EVENT, id: 8 }));
  assert.equal(flow.state.banner.more, 1);
  assert.equal(flow.state.history.length, 2);
  assert.equal(fake.shown.length, 2);
  assert.equal(fake.shown[0].title, "Alert Errors");
  flow.receive(JSON.stringify({ ...EVENT, id: 8 })); // same id: no duplicate history row
  assert.equal(flow.state.history.length, 2);
  flow.dismiss();
  assert.equal(flow.state.banner, null);
  assert.deepEqual(changes.slice(-1), [null]);
});

test("flow: history is bounded", () => {
  const flow = createAlertsFlow({ api: fakeApi(), getCase: () => "demo" });
  for (let i = 1; i <= HISTORY_SHOWN + 10; i += 1) {
    flow.receive({ ...EVENT, id: i });
  }
  assert.equal(flow.state.history.length, HISTORY_SHOWN);
  assert.equal(flow.state.history[0].id, HISTORY_SHOWN + 10);
});

test("flow: create, switch off, delete and clear go through the api and refresh", async () => {
  const api = fakeApi();
  const flow = createAlertsFlow({ api, getCase: () => "demo" });
  assert.equal(await flow.create({ name: "a", kind: "match", enabled: true }), true);
  assert.equal(flow.state.rules.length, 1);
  await flow.setEnabled({ name: "a", kind: "match", enabled: true, available: true }, false);
  assert.deepEqual(api.calls.find((c) => c[0] === "update").slice(1), ["a", { name: "a", kind: "match", enabled: false }]);
  assert.equal(flow.state.rules[0].enabled, false);
  flow.receive(EVENT);
  await flow.clearHistory();
  assert.deepEqual([flow.state.history.length, flow.state.banner], [0, null]);
  await flow.remove({ name: "a" });
  assert.equal(flow.state.rules.length, 0);
});

test("flow: a failing request is reported, not thrown", async () => {
  const api = fakeApi();
  api.createRule = async () => { throw new Error("duplicate"); };
  const flow = createAlertsFlow({ api, getCase: () => "demo" });
  assert.equal(await flow.create({ name: "a" }), false);
  assert.equal(flow.state.error, "duplicate");
});

test("flow: 'Show these records' filter comes from the rule listed now", async () => {
  const filter = { sev: { le: 2 } };
  const flow = createAlertsFlow({ api: fakeApi({ rules: [{ name: "Errors", filter }] }), getCase: () => "demo" });
  await flow.refresh();
  const alert = parseAlertEvent(EVENT);
  assert.deepEqual(flow.recordsFilterFor(alert), { and: [filter, { ts: { ge: 100, le: 900 } }] });
});

test("flow: an event replayed after an SSE reconnect gives no second banner or notification", () => {
  const fake = fakeNotification({ permission: "granted" });
  const notifier = createNotifier({ NotificationImpl: fake.Fake, storage: memoryStorage() });
  notifier.setWanted(true);
  const flow = createAlertsFlow({
    api: fakeApi(), getCase: () => "demo", notifier, describe: (a) => ({ title: a.rule, body: "" }),
  });
  assert.notEqual(flow.receive(EVENT), null);
  flow.dismiss();
  assert.equal(flow.receive(EVENT), null); // the server replays its event log to a new subscriber
  assert.equal(flow.state.banner, null);
  assert.equal(fake.shown.length, 1);
  assert.equal(flow.state.history.length, 1);
});

test("validateDraft keeps counts inside what the server can reach", () => {
  const filter = { tpl: "X" };
  assert.equal(validateDraft({ name: "a", kind: "match", filter, minCount: String(MAX_COUNT) }).error, undefined);
  assert.equal(validateDraft({ name: "a", kind: "match", filter, minCount: String(MAX_COUNT + 1) }).error, "count");
  assert.equal(validateDraft({ name: "a", kind: "threshold", filter, threshold: String(MAX_COUNT), windowS: "5" }).error, "threshold");
});

test("flow: a replay of more alerts than the visible history still gives no duplicates", () => {
  const fake = fakeNotification({ permission: "granted" });
  const notifier = createNotifier({ NotificationImpl: fake.Fake, storage: memoryStorage() });
  notifier.setWanted(true);
  const flow = createAlertsFlow({
    api: fakeApi(), getCase: () => "demo", notifier, describe: (a) => ({ title: a.rule, body: "" }),
  });
  const total = HISTORY_SHOWN + 1;
  for (let i = 1; i <= total; i += 1) {
    flow.receive({ ...EVENT, id: i });
  }
  assert.equal(fake.shown.length, total);
  flow.dismiss();
  for (let i = 1; i <= total; i += 1) {
    assert.equal(flow.receive({ ...EVENT, id: i }), null); // the whole log replayed after a reconnect
  }
  assert.equal(fake.shown.length, total);
  assert.equal(flow.state.banner, null);
});

test("flow: the remembered ids are bounded and a new case starts clean", () => {
  const flow = createAlertsFlow({ api: fakeApi(), getCase: () => "demo" });
  for (let i = 1; i <= SEEN_LIMIT + 5; i += 1) {
    flow.receive({ ...EVENT, id: i });
  }
  assert.notEqual(flow.receive({ ...EVENT, id: 1 }), null); // the oldest id fell out of the bounded set
  assert.equal(flow.receive({ ...EVENT, id: SEEN_LIMIT + 5 }), null);
  flow.reset();
  assert.notEqual(flow.receive({ ...EVENT, id: SEEN_LIMIT + 5 }), null);
});

test("the alert stream stops after 3 failures in a row, reports it once, and an open resets the count", async () => {
  const { connectAlertStream } = await import("../../src/dmslog/web/js/alerts-model.js");
  const sources = [];
  class Fake {
    constructor() { this.h = {}; this.closed = false; sources.push(this); }
    addEventListener(n, fn) { (this.h[n] ??= []).push(fn); }
    close() { this.closed = true; }
    emit(n) { for (const fn of this.h[n] || []) fn({ data: "" }); }
  }
  const stops = [];
  const handle = connectAlertStream({ EventSourceImpl: Fake, url: "/p", onAlert() {}, onStopped: () => stops.push(1) });
  const s = sources[0];
  s.emit("error"); s.emit("error"); s.emit("open"); s.emit("error"); s.emit("error");
  assert.equal(s.closed, false);
  assert.equal(handle.stopped(), false);
  s.emit("error");
  assert.equal(s.closed, true);
  assert.equal(handle.stopped(), true);
  assert.deepEqual(stops, [1]);
  s.emit("error");
  assert.deepEqual(stops, [1]);
  handle.close(); // closing a stopped stream is harmless
  assert.equal(connectAlertStream({ EventSourceImpl: null, url: "/p", onAlert() {} }).stopped(), false);
});
