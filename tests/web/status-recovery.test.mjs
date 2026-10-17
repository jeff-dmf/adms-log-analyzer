// UI-13 review fixes: new alerts and clock warnings reach the message region, a connection error never comes back
// as a stale message, Retry recovers a failed opening, reset clears field errors, the case-name error is only for the name.
import test from "node:test";
import assert from "node:assert/strict";

import { setFieldError, resetFormWithErrors } from "../../src/dmslog/web/js/field-error.js";
import { S } from "../../src/dmslog/web/js/strings.js";
import { createStatusBar } from "../../src/dmslog/web/js/status-bar.js";
import { createRecovery } from "../../src/dmslog/web/js/status-bar-model.js";
import { installGlobalDom } from "./fixtures/fake-dom.mjs";

const doc = installGlobalDom();
const { startAlerts } = await import("../../src/dmslog/web/js/alerts-panel.js");
const { initClockBanner } = await import("../../src/dmslog/web/js/clock-banner.js");
const { CaseFilesState } = await import("../../src/dmslog/web/js/case-files-model.js");

function bar() {
  const root = doc.createElement("footer");
  doc.body.appendChild(root);
  const status = createStatusBar(root, { S });
  const text = () => root.all().find((n) => n.className.includes("sb-message-text")).textContent;
  const shown = () => !root.all().find((n) => n.className.includes("sb-message")).hidden;
  return { root, status, text, shown };
}

class FakeEventSource {
  static last = null;
  constructor() { this.handlers = {}; FakeEventSource.last = this; }
  addEventListener(name, fn) { this.handlers[name] = fn; }
  close() {}
}

test("a new alert is spoken by the message region, a replayed one is not", () => {
  globalThis.EventSource = FakeEventSource;
  const { status, text, shown } = bar();
  const slot = status.slot("alerts");
  const alerts = startAlerts({
    slot, getCase: () => "C", onShowRecords() {}, onAnnounce: (t) => status.message(t, { kind: "warning" }),
    notifier: { notify() {}, permission: () => "denied", request: async () => "denied" },
    api: { alerts: async () => ({ alerts: [] }), rules: async () => ({ rules: [] }), history: async () => ({ alerts: [] }) },
  });
  alerts.onCase();
  assert.equal(shown(), false);
  const raw = JSON.stringify({ id: 5, rule: "R1", kind: "match", severity: "warning", count: 3 });
  FakeEventSource.last.handlers.alert({ data: raw });
  assert.equal(shown(), true);
  assert.match(text(), /R1/);
  status.message("");
  FakeEventSource.last.handlers.alert({ data: raw }); // replay after a reconnect
  assert.equal(shown(), false);
});

test("a clock warning is spoken once when it appears, not on every refresh", async () => {
  const { status, text, shown } = bar();
  const spoken = [];
  const api = {
    events: async () => ({
      events: [{ id: 1, kind: "clock_jump", ts_start: 5, ts_end: 6, entry_id: 1, detail: {} }],
      next: null,
    }),
  };
  const banner = initClockBanner(status.slot("clock"), {
    api, getCase: () => "C", onShowInGrid() {}, onAnnounce: (t) => { spoken.push(t); status.message(t, { kind: "warning" }); },
  });
  await banner.refresh();
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(spoken.length, 1);
  assert.equal(shown(), true);
  assert.match(text(), /[Cc]lock/);
  await banner.refresh();
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(spoken.length, 1);
});

test("a connection error text is never shown as a message (no stale copy after recovery)", () => {
  const { status, shown, root } = bar();
  status.connection("offline", { onRetry() {} });
  status.message(S.errorNetwork, { kind: "error" }); // e.g. SourcesPanel.openCollection: onMessage(err.message, true)
  status.connection("online");
  assert.equal(shown(), false);
  assert.equal(root.classList.contains("is-error"), false);
  status.message(S.errorForbidden, { kind: "error" });
  assert.equal(shown(), false);
  status.message("Something else", { kind: "error" });
  assert.equal(shown(), true);
});

test("Retry reloads the cases when the opening failed, applies the view after a restored connection, and stays offline while down", async () => {
  const calls = [];
  let healthy = false;
  let pending = true;
  const recovery = createRecovery({
    health: async () => { if (!healthy) throw new Error("down"); },
    setOnline: () => calls.push("online"),
    openingPending: () => pending,
    reloadCases: async () => { calls.push("cases"); pending = false; },
    fallback: async () => { calls.push("view"); },
    onError: (e) => calls.push("error:" + e.message),
  });
  await recovery.retry();
  assert.deepEqual(calls, []);
  healthy = true;
  await recovery.retry();
  assert.deepEqual(calls, ["online", "cases"]);
  await recovery.retry(); // nothing failed and the connection was not lost: nothing is reloaded
  assert.deepEqual(calls, ["online", "cases", "online"]);
  recovery.markOffline();
  await recovery.retry(); // the connection was lost and nothing is registered: the view is applied again
  assert.deepEqual(calls, ["online", "cases", "online", "online", "view"]);
  const failing = createRecovery({
    health: async () => {}, setOnline() {}, openingPending: () => true,
    reloadCases: async () => { throw new Error("boom"); }, fallback: async () => {}, onError: (e) => calls.push("error:" + e.message),
  });
  await failing.retry();
  assert.equal(calls.at(-1), "error:boom");
});

test("resetting the filter form clears the field errors too", () => {
  const form = doc.createElement("form");
  let resets = 0;
  form.reset = () => { resets += 1; };
  const from = doc.createElement("input");
  from.id = "from-input";
  const to = doc.createElement("input");
  to.id = "to-input";
  form.append(from, to);
  doc.body.appendChild(form);
  setFieldError(from, "bad");
  setFieldError(to, "bad");
  assert.equal(form.children.length, 4);
  resetFormWithErrors(form, [from, to]);
  assert.equal(resets, 1);
  assert.equal(form.children.length, 2);
  for (const input of [from, to]) {
    assert.equal(input.attrs["aria-invalid"], undefined);
    assert.equal(input.attrs["aria-describedby"], undefined);
  }
});

test("a failure to open the new case after it was created is not a name error", async () => {
  const state = new CaseFilesState();
  state.add(["/data/ERS_CL_0A1B2C3D-0000-1111-2222-333344445555_20260101120000_Enterprise.zip"]);
  const deps = (over) => ({ createCase: async (n) => ({ id: n }), selectCase: async () => {}, openPath: async () => ({ status: "ok" }), ...over });
  const select = await state.create("C", deps({ selectCase: async () => { throw new Error("could not open"); } }));
  assert.deepEqual(select, { state: "blocked", reason: "select" });
  assert.match(state.notices.at(-1).message, /could not open/);
  const fresh = new CaseFilesState();
  fresh.add(["/data/ERS_CL_0A1B2C3D-0000-1111-2222-333344445555_20260101120000_Enterprise.zip"]);
  const created = await fresh.create("C", deps({ createCase: async () => { throw new Error("exists"); } }));
  assert.deepEqual(created, { state: "blocked", reason: "case" });
});
