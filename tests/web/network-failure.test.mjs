// UI-25: the pieces around the grid. Which view the screen shows (rollback and Retry), the status bar notice with
// Retry and its focus, the watch stream that stops after 3 failures, the timeline that says it is out of date.
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";

import { S } from "../../src/dmslog/web/js/strings.js";
import { createStatusBar } from "../../src/dmslog/web/js/status-bar.js";
import { createViewTracker } from "../../src/dmslog/web/js/grid-model.js";
import { createViewsStack } from "../../src/dmslog/web/js/views-stack-model.js";
import { createStreamGuard } from "../../src/dmslog/web/js/follow-tail-model.js";
import { installGlobalDom, makeDoc, until } from "./fixtures/fake-dom.mjs";

// ---- view tracker -------------------------------------------------------------------------------------------

test("the tracker confirms what loaded and rolls a failure back to the last confirmed view of the same case", () => {
  const t = createViewTracker();
  const a = { caseId: "C", live: "A" };
  const b = { caseId: "C", live: "B" };
  assert.deepEqual(t.settle(t.begin(a), "ok"), { action: "confirm" });
  const tokenB = t.begin(b);
  assert.deepEqual(t.settle(tokenB, "failed", true), { action: "rollback", to: a });
  assert.equal(t.retryTarget(), b);
  assert.equal(t.confirmed, a);
});

test("a request the server rejects (4xx) is rolled back but not kept for Retry", () => {
  const t = createViewTracker();
  t.settle(t.begin({ caseId: "C", live: "A" }), "ok");
  const bad = { caseId: "C", live: "B" };
  assert.equal(t.settle(t.begin(bad), "failed", false).action, "rollback");
  assert.equal(t.retryTarget(), null);
});

test("the first view failing has nothing to go back to; another case never rolls back into the old one", () => {
  const t = createViewTracker();
  assert.deepEqual(t.settle(t.begin({ caseId: "C" }), "failed"), { action: "rollback", to: null });
  const u = createViewTracker();
  u.settle(u.begin({ caseId: "C", live: "A" }), "ok");
  assert.equal(u.settle(u.begin({ caseId: "D", live: "X" }), "failed").to, null);
});

test("late answers are stale: a newer change, a superseded outcome and a case switch change nothing", () => {
  const t = createViewTracker();
  const old = t.begin({ caseId: "C", live: "A" });
  const current = t.begin({ caseId: "C", live: "B" });
  assert.equal(t.settle(old, "ok").action, "stale"); // late success of the replaced change cannot confirm it
  assert.equal(t.settle(old, "failed").action, "stale"); // nor can its failure roll anything back
  assert.equal(t.confirmed, null);
  assert.equal(t.settle(current, "superseded").action, "stale");
  t.caseChanged();
  assert.equal(t.settle(current, "ok").action, "stale");
  assert.equal(t.confirmed, null);
  assert.equal(t.retryTarget(), null);
});

test("a newer view change drops the failed one: Retry never brings back what the user replaced", () => {
  const t = createViewTracker();
  t.settle(t.begin({ caseId: "C", live: "A" }), "ok");
  t.settle(t.begin({ caseId: "C", live: "B" }), "failed", true);
  assert.notEqual(t.retryTarget(), null);
  t.begin({ caseId: "C", live: "C" });
  assert.equal(t.retryTarget(), null);
});

// ---- trail: no view added by a change that did not load --------------------------------------------------------

test("the stack goes back with its checkpoint: no new view in the trail after a failed narrow, the trail is intact", () => {
  let live = { f: "x" };
  const stack = createViewsStack({ ownFilter: (s) => ({ and: [{ eq: s.f }] }), getLive: () => live });
  const point = stack.checkpoint();
  assert.equal(stack.narrow().ok, true);
  assert.equal(stack.size, 2);
  assert.equal(stack.rollback(point), true);
  assert.equal(stack.size, 1);
  assert.equal(stack.index, 0);
  // go back and forth with children, then roll a goTo back
  stack.narrow();
  stack.narrow();
  const at2 = stack.checkpoint();
  stack.goTo(0);
  assert.equal(stack.index, 0);
  stack.rollback(at2);
  assert.equal(stack.index, 2);
  assert.equal(stack.size, 3);
  assert.equal(stack.rollback(null), false);
  assert.equal(stack.rollback({ index: 9, nodes: [] }), false);
});

test("a checkpoint is a copy: later changes to the stack do not alter it", () => {
  const stack = createViewsStack({ ownFilter: () => ({ and: [{ eq: 1 }] }), getLive: () => ({}) });
  const point = stack.checkpoint();
  stack.narrow();
  stack.rename(0, "Renamed");
  assert.equal(point.nodes.length, 1);
  assert.equal(point.nodes[0].customName, "");
});

// ---- status bar: notice with Retry ---------------------------------------------------------------------------

function bar(extra = {}) {
  const doc = makeDoc();
  const root = doc.createElement("footer");
  doc.body.appendChild(root);
  const status = createStatusBar(root, { S, ...extra });
  const pick = (cls) => root.all().find((n) => n.className.split(/\s+/).includes(cls));
  return { doc, root, status, pick };
}

test("a message can carry Retry: it runs the action, stays until its owner dismisses it, and is a button", () => {
  const { status, pick } = bar();
  let runs = 0;
  const handle = status.message("Query timed out", { kind: "error", action: { label: S.sbRetry, run: () => { runs += 1; } } });
  const action = pick("sb-action");
  assert.equal(action.hidden, false);
  assert.equal(action.textContent, S.sbRetry);
  assert.equal(action.tagName, "BUTTON");
  action.click();
  action.click();
  assert.equal(runs, 2); // the model does not swallow clicks; the owner (grid.retry) is idempotent
  assert.equal(pick("sb-message").hidden, false); // it stays until the work succeeds
  handle.dismiss();
  assert.equal(pick("sb-message").hidden, true);
  assert.equal(action.hidden, true);
});

test("a message without an action (or with a broken one) shows no Retry; a newer message replaces the Retry", () => {
  const { status, pick } = bar();
  status.message("x", { kind: "error", action: { label: "", run() {} } });
  assert.equal(pick("sb-action").hidden, true);
  status.message("y", { kind: "error", action: { label: "Retry", run: "nope" } });
  assert.equal(pick("sb-action").hidden, true);
  status.message("z", { kind: "error", action: { label: "Retry", run() {} } });
  assert.equal(pick("sb-action").hidden, false);
  status.message("w", { kind: "info" });
  assert.equal(pick("sb-action").hidden, true);
});

test("the focus leaves the bar for a shown target on EVERY transition that hides what had it", () => {
  const moved = [];
  const ctx = bar({ focusFallback: () => { moved.push("grid"); outside.focus(); } });
  const { doc, status, pick } = ctx;
  const outside = doc.createElement("button");
  doc.body.appendChild(outside);
  const act = (label) => ({ label, run() {} });
  const focusIn = (cls) => pick(cls).focus();
  // error message: Dismiss hidden when it becomes info
  status.message("boom", { kind: "error" });
  focusIn("sb-dismiss");
  status.message("fine", { kind: "info" });
  assert.equal(moved.length, 1);
  // Retry of the message: dismissed by its owner
  const handle = status.message("boom", { kind: "error", action: act("Retry") });
  focusIn("sb-action");
  handle.dismiss();
  assert.equal(moved.length, 2);
  // action -> message without action
  status.message("boom", { kind: "error", action: act("Retry") });
  focusIn("sb-action");
  status.message("other", { kind: "info" });
  assert.equal(moved.length, 3);
  // offline -> forbidden hides the bar's Retry
  status.connection("offline", { onRetry() {} });
  focusIn("sb-retry");
  status.connection("forbidden");
  assert.equal(moved.length, 4);
  // offline -> online
  status.connection("offline", { onRetry() {} });
  focusIn("sb-retry");
  status.connection("online");
  assert.equal(moved.length, 5);
  // entering offline swallows every other segment of the bar (help button, message buttons)
  status.message("boom", { kind: "error" });
  focusIn("sb-dismiss");
  status.connection("offline", { onRetry() {} });
  assert.equal(moved.length, 6);
  status.connection("online");
  // focus outside the bar is never taken
  outside.focus();
  status.message("x", { kind: "error" });
  status.message("");
  status.connection("offline", { onRetry() {} });
  status.connection("online");
  assert.equal(moved.length, 6);
});

test("the fallback target is a SHOWN one: the grid, else the search box; with no case the empty state's button", async () => {
  const { fallbackTarget } = await import("../../src/dmslog/web/js/status-bar-model.js");
  const node = (name, hidden = false, parent = null) => ({ name, hidden, parent, isConnected: true });
  const grid = node("grid");
  const search = node("search");
  const empty = node("new-case");
  const hiddenBox = node("box", true);
  const inHidden = node("inner", false, hiddenBox);
  const q = (map) => (selector) => map[selector] ?? null;
  assert.equal(fallbackTarget({ query: q({ "#grid-viewport": grid, "#search-input": search }), noCase: false }), grid);
  // the grid is hidden (a collapsed panel): the search box
  assert.equal(fallbackTarget({ query: q({ "#grid-viewport": inHidden, "#search-input": search }), noCase: false }), search);
  // no case: the grid is not offered even if it is "shown"; the empty state is
  assert.equal(fallbackTarget({ query: q({ ".case-empty-new": empty, "#grid-viewport": grid }), noCase: true }), empty);
  assert.equal(fallbackTarget({ query: q({}), noCase: true }), null);
});

test("the disconnected state says what to do and Retry is offered; role=alert", () => {
  const { status, root, pick } = bar();
  status.connection("offline", { onRetry() {} });
  assert.match(S.errorNetwork, /^Disconnected: dmslog is not running\. Start it again, then Retry\./);
  assert.equal(pick("sb-error-text").textContent, S.errorNetwork);
  assert.equal(pick("sb-error").attrs.role, "alert");
  assert.equal(pick("sb-retry").hidden, false);
  assert.equal(root.classList.contains("is-error"), true);
});

// ---- the watch stream ---------------------------------------------------------------------------------------

test("the stream guard stops at the 3rd consecutive failure; an open resets it; reset forgets the stop", () => {
  const guard = createStreamGuard();
  assert.equal(guard.failed(), false);
  assert.equal(guard.failed(), false);
  guard.opened();
  assert.equal(guard.failed(), false);
  assert.equal(guard.failed(), false);
  assert.equal(guard.failed(), true);
  assert.equal(guard.stopped, true);
  assert.equal(guard.failed(), false); // already stopped: reported once
  guard.reset();
  assert.equal(guard.stopped, false);
  assert.equal(guard.failed(), false);
  assert.equal(createStreamGuard(0).failed(), false); // a nonsense limit falls back to 3
  assert.equal(createStreamGuard(NaN).failed(), false);
});

test("Follow tail: with the server stopped the stream is tried 3 times, then closed; Retry starts it once", async () => {
  const doc = installGlobalDom();
  const sources = [];
  class FakeEventSource {
    constructor(url) { this.url = url; this.handlers = {}; this.closed = false; sources.push(this); }
    addEventListener(name, fn) { (this.handlers[name] ??= []).push(fn); }
    close() { this.closed = true; }
    emit(name, data = {}) { for (const fn of this.handlers[name] || []) fn(data); }
  }
  globalThis.EventSource = FakeEventSource;
  const { initFollowTail } = await import("../../src/dmslog/web/js/follow-tail.js");
  const toggle = doc.createElement("input");
  toggle.type = "checkbox";
  const newButton = doc.createElement("button");
  const stopped = [];
  const grid = { tailInfo: () => ({ atEnd: true, top: 0, visible: 1, length: 1 }), followTail: async () => {}, total: null };
  const follow = initFollowTail({
    toggle, newButton, grid, getCase: () => "C", getFilter: () => null, onChanged() {}, onError() {},
    onStreamStopped: () => stopped.push("stopped"),
  });
  follow.onCase();
  assert.equal(sources.length, 0); // off: no stream
  toggle.checked = true;
  toggle.dispatch("change");
  assert.equal(sources.length, 1);
  const stream = sources[0];
  stream.emit("error");
  stream.emit("error");
  assert.equal(stream.closed, false); // the browser keeps trying for the first two
  assert.deepEqual(stopped, []);
  stream.emit("error"); // the 3rd
  assert.equal(stream.closed, true);
  assert.deepEqual(stopped, ["stopped"]);
  assert.equal(follow.stopped(), true);
  stream.emit("error"); // a late error of the closed stream changes nothing
  assert.deepEqual(stopped, ["stopped"]);
  assert.equal(follow.resume(), true);
  assert.equal(sources.length, 2);
  assert.equal(follow.resume(), false); // idempotent: it is running now
  assert.equal(sources.length, 2);
  const again = sources[1];
  again.emit("error");
  again.emit("open"); // it reconnected: the count starts over
  again.emit("error");
  again.emit("error");
  assert.equal(again.closed, false);
  again.emit("error");
  assert.equal(again.closed, true);
  assert.equal(stopped.length, 2);
  // a stopped stream of the old case does not count for the new one
  follow.onCase();
  assert.equal(sources.length, 3);
  assert.equal(follow.stopped(), false);
  delete globalThis.EventSource;
});

// ---- timeline -----------------------------------------------------------------------------------------------

test("the timeline keeps the previous chart, says it is out of date, and recovers on refresh", async () => {
  const doc = installGlobalDom();
  globalThis.getComputedStyle = () => ({ getPropertyValue: () => "" });
  const { initTimeline } = await import("../../src/dmslog/web/js/timeline.js");
  class U {
    static paths = { bars: () => ({}), stepped: () => ({}) };
    static tzDate(d) { return d; }
    constructor(o, c, host) { this.over = new (doc.body.constructor)(doc, "div"); this.select = {}; host.appendChild(this.over); }
    setSize() {} setSelect() {} valToPos(v) { return v; } posToVal(v) { return v; } destroy() {}
  }
  const good = { bucket_ms: 60000, start: 1760000000000, n_buckets: 1, split: "sev", total: 1, exact: true, series: [{ key: 2, counts: [1] }] };
  let mode = "ok";
  const api = { histogram: async () => { if (mode === "fail") throw Object.assign(new Error("Could not reach"), { status: 0 }); return good; } };
  const host = doc.createElement("div");
  doc.body.appendChild(host);
  const timeline = initTimeline(host, { api, uplot: U, getFilter: () => ({ caseId: "C", filter: null }), onRangeSelected() {} });
  const root = host.all().find((n) => n.className.split(/\s+/).includes("tl"));
  const statusEl = host.all().find((n) => n.className.split(/\s+/).includes("tl-status"));
  await until(() => !timeline.isStale() && statusEl.textContent !== "" && !statusEl.textContent.startsWith("Loading"), "the chart");
  assert.equal(timeline.isStale(), false);
  mode = "fail";
  timeline.refresh();
  await until(() => timeline.isStale(), "stale");
  assert.equal(root.classList.contains("tl-stale"), true);
  assert.match(statusEl.textContent, /Out of date/);
  mode = "ok";
  timeline.refresh();
  await until(() => !timeline.isStale(), "fresh");
  assert.equal(root.classList.contains("tl-stale"), false);
  timeline.destroy();
});

// ---- wiring (main.js is not run here: its pieces are tested above; this keeps the connections from rotting) ------

test("the CSS shows the bar after 400 ms of aria-busy, mutes the old rows, and respects reduced motion", () => {
  const css = fs.readFileSync(new URL("../../src/dmslog/web/css/app.css", import.meta.url), "utf8").replace(/\r\n/g, "\n");
  assert.match(css, /\.grid-viewport\.is-reloading \.grid-rows \{\s*opacity: 0\.6;/);
  assert.match(css, /\.grid-viewport\[aria-busy="true"\]::before/);
  assert.match(css, /animation: grid-busy-in 0s linear 400ms forwards, grid-busy-run 1\.2s ease-in-out 400ms infinite;/);
  assert.match(css, /prefers-reduced-motion: reduce\) \{\s*\.grid-viewport\[aria-busy="true"\]::before/);
});

test("Follow tail: a stream the browser closed stops at once", async () => {
  const doc = installGlobalDom();
  const sources = [];
  class FakeEventSource {
    constructor() { this.handlers = {}; this.closed = false; this.readyState = 2; sources.push(this); }
    addEventListener(name, fn) { (this.handlers[name] ??= []).push(fn); }
    close() { this.closed = true; }
    emit(name) { for (const fn of this.handlers[name] || []) fn({}); }
  }
  globalThis.EventSource = FakeEventSource;
  const { initFollowTail } = await import("../../src/dmslog/web/js/follow-tail.js");
  const toggle = doc.createElement("input");
  toggle.type = "checkbox";
  const stopped = [];
  const follow = initFollowTail({
    toggle, newButton: doc.createElement("button"),
    grid: { tailInfo: () => ({ atEnd: true, top: 0, visible: 1, length: 1 }), followTail: async () => {}, total: null },
    getCase: () => "C", getFilter: () => null, onChanged() {}, onError() {}, onStreamStopped: () => stopped.push(1),
  });
  toggle.checked = true;
  toggle.dispatch("change");
  sources[0].emit("error");
  assert.deepEqual(stopped, [1]);
  assert.equal(follow.stopped(), true);
  delete globalThis.EventSource;
});

// ---- the real bar with the failure center (a message that replaced the notice must give it back) -------------------

test("count 408 -> an alert takes the bar -> the alert expires: the failure and its Retry are back; a user's Dismiss is respected", async () => {
  const { createFailureCenter } = await import("../../src/dmslog/web/js/status-bar-model.js");
  const { ApiError } = await import("../../src/dmslog/web/js/api.js");
  const doc2 = makeDoc();
  const root = doc2.createElement("footer");
  doc2.body.appendChild(root);
  let clock = 0;
  const timers = [];
  const status = createStatusBar(root, {
    S, now: () => clock,
    setTimer: (fn, ms) => { timers.push({ fn, at: clock + ms }); return timers.length; },
    clearTimer: (id) => { if (timers[id - 1]) timers[id - 1].fn = null; },
  });
  const advance = (ms) => { clock += ms; for (const t of timers) if (t.fn && t.at <= clock) { const fn = t.fn; t.fn = null; fn(); } };
  const pick = (cls) => root.all().find((n) => n.className.split(/\s+/).includes(cls));
  const text = () => (pick("sb-message").hidden ? null : pick("sb-message-text").textContent);
  const retryShown = () => !pick("sb-action").hidden;
  const center = createFailureCenter({
    status, retryLabel: "Retry now", genericMessage: "x", pageHint: "",
    goOffline() {}, health: async () => {}, setOnline() {}, openingPending: () => false, reloadCases: async () => {},
    fallback: async () => {}, onError() {}, retryView: async () => {}, hasViewRetry: () => false, dropViewRetry() {},
    retryGrid: async () => {}, retryCount: async () => {}, refreshTimeline: async () => {}, resumeStream: () => true,
    cancelRequests() {},
  });
  center.gridFailure(new ApiError(408, "query_timeout", "Count timed out"), { kind: "timeout", scope: "count", op: "count" });
  assert.equal(text(), "Count timed out");
  assert.equal(retryShown(), true);
  status.message("Alert fired", { kind: "warning" }); // an alert takes the single slot
  assert.equal(text(), "Alert fired");
  advance(8000); // and expires
  assert.equal(text(), "Count timed out");
  assert.equal(retryShown(), true);
  // the user presses Dismiss: it stays dismissed, however the bar idles afterwards
  pick("sb-dismiss").click();
  assert.equal(text(), null);
  status.message("Another", { kind: "info" });
  advance(8000);
  assert.equal(text(), null);
  // a different failure shows again (the state changed)
  center.timelineFailure(new ApiError(408, "query_timeout", "Timeline timed out"));
  assert.equal(text(), "Timeline timed out");
  assert.equal(retryShown(), true);
  // recovery clears it for good
  center.timelineRecovered();
  center.gridRecovered("count");
  assert.equal(text(), null);
});

test("one change of the failure shows ONE message on the bar (no second pass from the bar's idle report)", async () => {
  const { createFailureCenter } = await import("../../src/dmslog/web/js/status-bar-model.js");
  const { ApiError } = await import("../../src/dmslog/web/js/api.js");
  const doc2 = makeDoc();
  const root = doc2.createElement("footer");
  doc2.body.appendChild(root);
  const real = createStatusBar(root, { S });
  let shownCount = 0;
  const status = { ...real, message: (...args) => { shownCount += 1; return real.message(...args); } };
  const center = createFailureCenter({
    status, retryLabel: "Retry now", genericMessage: "x", pageHint: "",
    goOffline() {}, health: async () => {}, setOnline() {}, openingPending: () => false, reloadCases: async () => {},
    fallback: async () => {}, onError() {}, retryView: async () => {}, hasViewRetry: () => false, dropViewRetry() {},
    retryGrid: async () => {}, retryCount: async () => {}, refreshTimeline: async () => {}, resumeStream: () => true,
    cancelRequests() {},
  });
  center.timelineFailure(new ApiError(408, "query_timeout", "first"));
  assert.equal(shownCount, 1);
  center.timelineFailure(new ApiError(408, "query_timeout", "second")); // replaces the text: dismiss + show, once
  assert.equal(shownCount, 2);
});

test("a dismissed failure does not hide a NEW failure that has the same text (another task, or the same one again)", async () => {
  const { createFailureCenter } = await import("../../src/dmslog/web/js/status-bar-model.js");
  const { ApiError } = await import("../../src/dmslog/web/js/api.js");
  const doc2 = makeDoc();
  const root = doc2.createElement("footer");
  doc2.body.appendChild(root);
  const status = createStatusBar(root, { S });
  const pick = (cls) => root.all().find((n) => n.className.split(/\s+/).includes(cls));
  const text = () => (pick("sb-message").hidden ? null : pick("sb-message-text").textContent);
  const center = createFailureCenter({
    status, retryLabel: "Retry now", genericMessage: "x", pageHint: "",
    goOffline() {}, health: async () => {}, setOnline() {}, openingPending: () => false, reloadCases: async () => {},
    fallback: async () => {}, onError() {}, retryView: async () => {}, hasViewRetry: () => false, dropViewRetry() {},
    retryGrid: async () => {}, retryCount: async () => {}, refreshTimeline: async () => {}, resumeStream: () => true,
    cancelRequests() {},
  });
  const timeout = () => new ApiError(408, "query_timeout", "The query took too long");
  center.gridFailure(timeout(), { kind: "timeout", scope: "count", op: "count" });
  pick("sb-dismiss").click();
  assert.equal(text(), null);
  center.timelineFailure(timeout()); // same standard text, another origin
  assert.equal(text(), "The query took too long");
  assert.equal(center.recovery.has("count"), true);
  assert.equal(center.recovery.has("timeline"), true);
  pick("sb-dismiss").click();
  assert.equal(text(), null);
  center.timelineFailure(timeout()); // the same task failing again is a new failure too
  assert.equal(text(), "The query took too long");
  // dismissed while two tasks share the text; one recovers: the OTHER failure comes into view (it was never dismissed)
  pick("sb-dismiss").click();
  assert.equal(text(), null);
  center.timelineRecovered();
  assert.equal(text(), "The query took too long");
  assert.equal(center.recovery.has("count"), true);
});
