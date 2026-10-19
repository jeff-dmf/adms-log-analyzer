// Message queue panel on the fake DOM (issue #74): states, rows written as text, filters, keyset "Load more", the preview
// (ok, error, too large, no body, decode failure), the fixed reconstruction label, the actions, removal, network errors
// that keep the screen. All values are synthetic.
import test from "node:test";
import assert from "node:assert/strict";

import { El, installGlobalDom, tick } from "./fixtures/fake-dom.mjs";

const doc = installGlobalDom();
globalThis.Element = El;
const { S } = await import("../../src/dmslog/web/js/strings.js");
const { initQueuePanel } = await import("../../src/dmslog/web/js/queue-panel.js");

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}
const abortError = () => Object.assign(new Error("aborted"), { name: "AbortError" });
const cls = (node, name) => node.className.split(/\s+/).includes(name);
const byClass = (root, name) => root.all().filter((n) => cls(n, name));
const find = (root, pred) => root.all().find(pred);

const item = (id, extra = {}) => ({
  id, item_id: 1000 + id, message_id: "00000000-0000-0000-0000-" + String(id).padStart(12, "0"), msg_type: "TypeA", state: "pending",
  ts: Date.UTC(2026, 0, 1, 0, 0, id), tag: "C1", size: 100, preview: "available", log: { msgid: 0, im_id: 0, payload_id: null }, ...extra,
});
const summary = (extra = {}) => ({
  enabled: true,
  dbs: [{ id: 1, collection: 1, tag: "C1", name: "a.db3", adapter: "TestAdapter", cache: "TestCache", status: "ready", snapshot: "wal_applied", warnings: [], items: 3, pending: 2, sent: 1, other: 0 }],
  totals: { items: 3, pending: 2, sent: 1, other: 0 },
  ...extra,
});

function world(options = {}) {
  const calls = [];
  const current = { caseId: "A" };
  const events = { log: [], cache: [], payload: [], read: [], closed: 0 };
  const hold = (name) => (...args) => {
    const d = deferred();
    const signal = args.at(-1) && typeof args.at(-1) === "object" && "aborted" in args.at(-1) ? args.at(-1) : null;
    calls.push({ name, args, d });
    signal?.addEventListener?.("abort", () => d.reject(abortError()));
    return d.promise;
  };
  const host = doc.createElement("section");
  doc.body.appendChild(host);
  const panel = initQueuePanel(host, {
    api: { queueSummary: hold("summary"), queueItems: hold("items"), queuePreview: hold("preview"), queueRemove: hold("remove") },
    getCase: () => current.caseId,
    getCollections: () => options.collections || [],
    onShowInLog: (v) => events.log.push(v),
    onShowCacheRecords: (v) => events.cache.push(v),
    onOpenPayload: (v) => events.payload.push(v),
    onReadQueue: (v) => events.read.push(v),
    onRequestClose: () => { events.closed += 1; },
  });
  const of = (name) => calls.filter((c) => c.name === name);
  const ui = {
    status: () => byClass(host, "mq-status")[0].textContent,
    rows: () => byClass(host, "mq-row"),
    more: () => byClass(host, "mq-more")[0],
    one: (name) => byClass(host, name)[0],
    selects: () => host.all().filter((n) => n.tagName === "SELECT"),
  };
  return { host, panel, calls, of, current, events, ui };
}

async function loaded(w, items = [item(1), item(2), item(3)], next = null, sum = summary()) {
  const loading = w.panel.refresh();
  w.of("summary").at(-1).d.resolve(sum);
  w.of("items").at(-1).d.resolve({ items, next });
  await loading;
  await tick();
}

const okPreview = (extra = {}) => ({
  available: true, decode: { ok: true }, reconstruction: { basis: "exemplar", exemplars: 2, notes: ["names_guessed"] },
  formatted: { text: "<Root>\n  <A>1</A>\n</Root>", ok: true, truncated_display: false }, ...extra,
});

async function openRow(w, index = 0) {
  w.ui.rows()[index].click();
  return w;
}

test("the frame: title, Refresh and Close (which asks the dock); the status is a polite status region", async () => {
  const w = world();
  assert.equal(find(w.host, (n) => n.tagName === "H2").textContent, S.mqTitle);
  find(w.host, (n) => cls(n, "panel-close")).click();
  assert.equal(w.events.closed, 1);
  assert.equal(w.ui.one("mq-status").getAttribute("role"), "status");
  w.current.caseId = null;
  await w.panel.refresh();
  assert.equal(w.ui.status(), S.mqNoCase);
  assert.equal(w.calls.length, 0);
});

test("loading, then the counters in the head, the databases with their warnings and a list written as text", async () => {
  const w = world();
  const loading = w.panel.refresh();
  assert.equal(w.ui.status(), S.mqLoading);
  w.of("summary")[0].d.resolve(summary({ dbs: [
    { id: 1, collection: 1, tag: "C1", name: "a.db3", adapter: "TestAdapter", cache: "TestCache", status: "ready", snapshot: "no_wal", warnings: ["items_truncated"], items: 3, pending: 2, sent: 1, other: 0 },
    { id: 2, collection: 1, tag: "C1", name: "bad.db3", status: "error", error: "corrupt" },
    { id: 3, collection: 1, tag: "C1", name: "other.db3", status: "not_a_queue" },
  ] }));
  w.of("items")[0].d.resolve({ items: [item(1, { msg_type: "<img src=x onerror=alert(1)>" }), item(2, { state: "sent" })], next: null });
  await loading;
  await tick();
  assert.equal(find(w.host, (n) => cls(n, "ui-panel-summary")).textContent, "2 not sent · 3 items");
  const banks = byClass(w.host, "mq-bank").map((n) => n.textContent).join("|");
  assert.match(banks, /C1 · TestAdapter · TestCache — 3 items, 2 not sent/);
  assert.match(banks, new RegExp("Warning: " + S.mqWarningNames.no_wal.replace(/[.:]/g, "\\$&")));
  assert.match(banks, new RegExp("Warning: " + S.mqWarningNames.items_truncated.replace(/[.():]/g, "\\$&")));
  assert.match(banks, new RegExp("Error: " + S.mqErrorNames.corrupt));
  assert.equal(byClass(w.host, "mq-others")[0].children[0].textContent, "Other databases (not queues): 1");
  const [first, second] = w.ui.rows();
  assert.ok(first.textContent.includes("<img src=x onerror=alert(1)>"));
  assert.equal(first.all().some((n) => n.tagName === "IMG"), false);
  assert.equal(first.children[0].textContent, "2026-01-01 00:00:01.000");
  assert.equal(second.children[2].textContent, S.mqStateNames.sent);
  assert.equal(first.children[3].title, "00000000-0000-0000-0000-000000000001");
  assert.equal(w.ui.status(), "2 items shown");
  // the fixed warning about the time zone is on screen with the list
  assert.equal(w.ui.one("mq-notice").hidden, false);
  assert.equal(w.ui.one("mq-notice").textContent, S.mqTimeNotice);
});

test("the table has a hidden caption, column headers and a roving tab stop on the rows", async () => {
  const w = world();
  await loaded(w);
  const table = w.ui.one("mq-table");
  assert.equal(table.getAttribute("role"), "grid");
  assert.equal(find(table, (n) => n.tagName === "CAPTION").textContent, S.mqListCaption);
  const heads = table.all().filter((n) => n.tagName === "TH");
  assert.deepEqual(heads.map((h) => h.textContent), [S.mqColTime, S.mqColType, S.mqColState, S.mqColMessageId, S.mqColItemId, S.mqColSize, S.mqColInLog]);
  assert.ok(heads.every((h) => h.scope === "col"));
  const rows = w.ui.rows();
  assert.deepEqual(rows.map((r) => r.tabIndex), [0, -1, -1]);
  assert.equal(rows[0].getAttribute("aria-label"), "Item 1001, TypeA, Pending");
  rows[0].focus();
  const down = rows[0].dispatch("keydown", { key: "ArrowDown" });
  assert.ok(down.prevented);
  assert.equal(doc.activeElement, rows[1]);
  assert.deepEqual(rows.map((r) => r.tabIndex), [-1, 0, -1]);
  rows[1].dispatch("keydown", { key: "End" });
  assert.equal(doc.activeElement, rows[2]);
  rows[2].dispatch("keydown", { key: "Home" });
  assert.equal(doc.activeElement, rows[0]);
  rows[0].dispatch("keydown", { key: "ArrowUp" });
  assert.equal(doc.activeElement, rows[0]);
});

test("the collection column and filter appear only with more than one collection", async () => {
  const w = world();
  await loaded(w);
  const field = (name) => byClass(w.host, "mq-field").find((f) => f.children[0].textContent === name);
  assert.equal(field(S.mqFilterCollection).hidden, true);
  assert.equal(field(S.mqFilterDb).hidden, true);
  const two = summary({ dbs: [
    { id: 1, collection: 1, tag: "C1", name: "a.db3", status: "ready", items: 1 },
    { id: 2, collection: 2, tag: "C2", name: "b.db3", status: "ready", items: 2 },
  ], totals: { items: 3, pending: 3 } });
  await loaded(w, [item(1), item(2, { tag: "C2" })], null, two);
  assert.equal(field(S.mqFilterCollection).hidden, false);
  assert.equal(field(S.mqFilterDb).hidden, false);
  assert.deepEqual(byClass(w.host, "mq-table")[0].all().filter((n) => n.tagName === "TH").map((h) => h.textContent).slice(0, 2), [S.mqColTime, S.mqColCollection]);
  assert.equal(w.ui.rows()[1].children[1].textContent, "C2");
});

test("filters go to the query; the type list is the types seen; the search takes Enter", async () => {
  const w = world();
  await loaded(w, [item(1), item(2, { msg_type: "TypeB" })]);
  const [stateSelect, typeSelect] = w.ui.selects();
  assert.deepEqual(typeSelect.options.map((o) => o.value), ["", "TypeA", "TypeB"]);
  stateSelect.value = "sent";
  stateSelect.dispatch("change");
  assert.equal(w.of("items").at(-1).args[1].state, "sent");
  w.of("summary").at(-1).d.resolve(summary());
  w.of("items").at(-1).d.resolve({ items: [item(3, { state: "sent" })], next: null });
  await tick();
  typeSelect.value = "TypeB";
  typeSelect.dispatch("change");
  assert.equal(w.of("items").at(-1).args[1].type, "TypeB");
  w.of("summary").at(-1).d.resolve(summary());
  w.of("items").at(-1).d.resolve({ items: [], next: null });
  await tick();
  assert.equal(w.ui.status(), S.mqNoMatch);
  const input = w.ui.one("mq-search-input");
  input.value = "not a guid";
  input.dispatch("keydown", { key: "Enter" });
  assert.match(w.ui.status(), new RegExp(S.mqBadQuery.replace(/[()]/g, "\\$&")));
  const before = w.of("items").length;
  input.value = "00000000-0000-0000-0000-000000000009";
  input.dispatch("keydown", { key: "Enter" });
  assert.equal(w.of("items").length, before + 1);
  assert.equal(w.of("items").at(-1).args[1].q, "00000000-0000-0000-0000-000000000009");
});

test("Load more is in the foot, follows the cursor, and gives the focus back to the list when it goes away", async () => {
  const w = world();
  await loaded(w, [item(1), item(2)], "1000:2");
  const more = w.ui.more();
  assert.equal(more.hidden, false);
  assert.equal(w.ui.status(), "2 items shown, more available");
  more.focus();
  more.click();
  assert.equal(more.textContent, S.mqLoadingMore);
  assert.equal(w.of("items")[1].args[1].after, "1000:2");
  w.of("items")[1].d.resolve({ items: [item(3)], next: null });
  await tick();
  assert.equal(w.ui.rows().length, 3);
  assert.equal(more.hidden, true);
  assert.equal(doc.activeElement, w.ui.one("mq-list"));
});

test("a failed Load more keeps the list, says so and offers Try again", async () => {
  const w = world();
  await loaded(w, [item(1), item(2)], "1000:2");
  w.ui.more().click();
  w.of("items")[1].d.reject(new Error("net down"));
  await tick();
  assert.equal(w.ui.rows().length, 2);
  assert.equal(w.ui.more().textContent, S.mqRetry);
  assert.match(w.ui.status(), /Could not load more items: net down/);
  assert.ok(cls(w.ui.one("mq-status"), "ui-error"));
  assert.equal(w.ui.one("mq-more-error").hidden, false);
});

test("a network error keeps what is on screen; the first load failing shows the error", async () => {
  const w = world();
  const failing = w.panel.refresh();
  w.of("summary")[0].d.reject(new Error("boom"));
  w.of("items")[0].d.reject(new Error("boom"));
  await failing;
  await tick();
  assert.match(w.ui.status(), /Could not load the message queue: boom/);
  assert.ok(cls(w.ui.one("mq-status"), "is-error"));
  assert.equal(w.ui.one("mq-empty").hidden, true);
  await loaded(w);
  assert.equal(w.ui.rows().length, 3);
  const again = w.panel.refresh();
  w.of("summary").at(-1).d.reject(new Error("later"));
  w.of("items").at(-1).d.reject(new Error("later"));
  await again;
  await tick();
  assert.equal(w.ui.rows().length, 3);
  assert.match(w.ui.status(), /Could not refresh the message queue: later/);
});

test("empty states: option off (with the Read button), no databases, empty queues", async () => {
  const w = world();
  await loaded(w, [], null, summary({ enabled: false, dbs: [], totals: {} }));
  assert.equal(w.ui.one("mq-empty").hidden, false);
  assert.equal(w.ui.one("mq-empty-text").textContent, S.mqDisabled);
  assert.equal(w.ui.status(), S.mqDisabled);
  assert.equal(w.ui.one("mq-table").hidden, true);
  assert.equal(w.ui.one("mq-notice").hidden, true);
  assert.equal(w.ui.one("mq-remove").hidden, false);
  w.ui.one("mq-read").click();
  assert.deepEqual(w.events.read, [null]);
  await loaded(w, [], null, summary({ dbs: [], totals: {} }));
  assert.equal(w.ui.one("mq-empty-text").textContent, S.mqNoDbs);
  assert.equal(w.ui.one("mq-read").hidden, true);
  await loaded(w, [], null, summary({ dbs: [{ id: 1, collection: 1, tag: "C1", status: "ready", items: 0 }], totals: { items: 0 } }));
  assert.equal(w.ui.one("mq-empty-text").textContent, S.mqEmptyQueues);
});

test("with several collections the Read button asks which one", async () => {
  const w = world({ collections: [{ group: 4, tag: "C1" }, { group: 9, tag: "C2" }] });
  await loaded(w, [], null, summary({ enabled: false, dbs: [], totals: {} }));
  const select = w.ui.one("mq-read-select");
  assert.equal(select.hidden, false);
  assert.deepEqual(select.options.map((o) => o.value), ["4", "9"]);
  select.value = "9";
  w.ui.one("mq-read").click();
  assert.deepEqual(w.events.read, [9]);
});

test("while the host reads the databases the panel says so, and keeps the button off", async () => {
  const w = world();
  await loaded(w, [], null, summary({ enabled: false, dbs: [], totals: {} }));
  w.panel.setReading(true);
  assert.equal(w.ui.status(), S.mqReading);
  assert.equal(w.ui.one("mq-read").disabled, true);
  w.panel.setReading(false);
  assert.equal(w.ui.status(), S.mqDisabled);
});

test("opening an item shows the fixed label first, the base, the notes, the sensitive badge and the XML as text", async () => {
  const w = world();
  await loaded(w);
  assert.equal(w.ui.one("mq-preview").hidden, true);
  await openRow(w, 1);
  const box = w.ui.one("mq-preview");
  assert.equal(box.hidden, false);
  assert.equal(w.ui.one("mq-banner-label").textContent, "Reconstruction, may differ from the bytes sent.");
  assert.equal(w.ui.one("mq-preview-message").textContent, S.mqPreviewLoading);
  assert.equal(w.ui.rows()[1].getAttribute("aria-selected"), "true");
  assert.equal(w.ui.rows()[0].getAttribute("aria-selected"), "false");
  assert.deepEqual(w.of("preview")[0].args.slice(0, 3), ["A", 2, "wire"]);
  w.of("preview")[0].d.resolve(okPreview({ formatted: { text: "<a><script>x</script></a>", ok: true } }));
  await tick();
  assert.equal(w.ui.one("mq-banner").hidden, false);
  assert.equal(w.ui.one("mq-banner-label").textContent, S.mqReconstructionLabel);
  assert.equal(w.ui.one("mq-banner-basis").textContent, "Based on 2 payloads logged in this case");
  assert.equal(byClass(w.host, "mq-banner-notes")[0].children[0].textContent, S.mqNoteNames.names_guessed);
  const pre = w.ui.one("mq-xml");
  assert.equal(pre.textContent, "<a><script>x</script></a>");
  assert.equal(pre.all().length, 1);
  assert.equal(pre.tabIndex, 0);
  assert.equal(pre.getAttribute("aria-label"), S.mqPreviewText);
  assert.equal(w.ui.one("mq-badge").textContent, S.mqSensitive);
  assert.equal(w.ui.one("mq-preview-title").textContent, "Item 1002");
});

test("the label stays while the preview loads, fails or has no body (wire form)", async () => {
  const w = world();
  await loaded(w);
  await openRow(w, 0);
  w.of("preview")[0].d.reject(new Error("server down"));
  await tick();
  assert.match(w.ui.one("mq-preview-message").textContent, /Could not load the preview: server down/);
  assert.equal(w.ui.one("mq-banner-label").textContent, S.mqReconstructionLabel);
  assert.equal(w.ui.one("mq-banner").hidden, false);
  assert.equal(w.ui.rows().length, 3); // the list is untouched
  await openRow(w, 1);
  w.of("preview")[1].d.resolve({ available: false, reason: "no_body", decode: null });
  await tick();
  assert.equal(w.ui.one("mq-preview-message").textContent, S.mqNoBody);
  assert.equal(w.ui.one("mq-xml").hidden, true);
  assert.equal(w.ui.one("mq-banner-label").textContent, S.mqReconstructionLabel);
  await openRow(w, 2);
  w.of("preview")[2].d.resolve({ available: false, reason: "too_large" });
  await tick();
  assert.equal(w.ui.one("mq-preview-message").textContent, "The body is larger than the preview limit (100 B).");
});

test("a body that cannot be decoded says the code and nothing else", async () => {
  const w = world();
  await loaded(w);
  await openRow(w, 0);
  w.of("preview")[0].d.resolve({ available: true, decode: { ok: false, error: "bad_header", offset: 0 }, formatted: { text: "SECRET-BYTES" } });
  await tick();
  assert.equal(w.ui.one("mq-preview-message").textContent, "This message body could not be decoded (bad_header).");
  assert.ok(cls(w.ui.one("mq-preview-message"), "ui-error"));
  assert.equal(w.ui.one("mq-xml").hidden, true);
  assert.equal(w.ui.one("mq-xml").textContent, "");
});

test("the form switch is a radio group: wire and stored; the stored form has no reconstruction base", async () => {
  const w = world();
  await loaded(w);
  await openRow(w, 0);
  w.of("preview")[0].d.resolve(okPreview());
  await tick();
  const group = w.ui.one("mq-forms");
  assert.equal(group.getAttribute("role"), "radiogroup");
  const [wire, stored] = byClass(group, "mq-form");
  assert.equal(wire.getAttribute("aria-checked"), "true");
  assert.equal(stored.getAttribute("aria-checked"), "false");
  assert.deepEqual([wire.tabIndex, stored.tabIndex], [0, -1]);
  stored.click();
  assert.equal(w.of("preview")[1].args[2], "stored");
  w.of("preview")[1].d.resolve(okPreview({ form: "stored", reconstruction: null, formatted: { text: "<S/>" } }));
  await tick();
  assert.equal(stored.getAttribute("aria-checked"), "true");
  assert.equal(w.ui.one("mq-banner-label").textContent, S.mqStoredNote);
  assert.equal(w.ui.one("mq-banner-basis").hidden, true);
  assert.equal(w.ui.one("mq-xml").textContent, "<S/>");
  stored.dispatch("keydown", { key: "ArrowLeft" });
  assert.equal(w.of("preview")[2].args[2], "wire");
});

test("Show in log, Show cache records and Open logged payload: names with the item, disabled with the reason, callbacks", async () => {
  const w = world();
  await loaded(w, [
    item(1, { log: { msgid: 3, im_id: 0, payload_id: 55 } }),
    item(2, { log: { msgid: 0, im_id: 4, payload_id: null } }),
  ]);
  await openRow(w, 0);
  w.of("preview")[0].d.resolve(okPreview());
  await tick();
  const showLog = w.ui.one("mq-show-log");
  const showCache = w.ui.one("mq-show-cache");
  const payload = w.ui.one("mq-open-payload");
  assert.equal(showLog.getAttribute("aria-label"), "Show item 1001 in log");
  assert.equal(showLog.getAttribute("aria-disabled"), "false");
  assert.equal(showCache.getAttribute("aria-disabled"), "true");
  assert.equal(showCache.title, S.mqNotInCache);
  assert.equal(payload.hidden, false);
  showLog.click();
  showCache.click();
  payload.click();
  assert.deepEqual(w.events.log, ["00000000-0000-0000-0000-000000000001"]);
  assert.deepEqual(w.events.cache, []);
  assert.deepEqual(w.events.payload, [55]);
  await openRow(w, 1);
  w.of("preview")[1].d.resolve(okPreview());
  await tick();
  assert.equal(showLog.getAttribute("aria-disabled"), "true");
  assert.equal(showLog.title, S.mqNotInLog);
  assert.equal(payload.hidden, true);
  showLog.click();
  showCache.click();
  assert.deepEqual(w.events.log.length, 1);
  assert.deepEqual(w.events.cache, ["1002"]);
});

test("Copy copies only the XML and says it was a reconstruction; a missing clipboard says it failed", async () => {
  const w = world();
  await loaded(w);
  await openRow(w, 0);
  w.of("preview")[0].d.resolve(okPreview());
  await tick();
  const copy = w.ui.one("mq-copy");
  const written = [];
  Object.defineProperty(globalThis, "navigator", { value: { clipboard: { writeText: async (t) => { written.push(t); } } }, configurable: true });
  copy.click();
  await tick();
  assert.deepEqual(written, ["<Root>\n  <A>1</A>\n</Root>"]);
  assert.equal(w.ui.status(), S.mqCopiedReconstruction);
  Object.defineProperty(globalThis, "navigator", { value: { clipboard: null }, configurable: true });
  copy.click();
  await tick();
  assert.equal(w.ui.status(), S.mqCopyFailed);
});

test("Escape and Close hide the preview and the focus goes back to the row", async () => {
  const w = world();
  await loaded(w);
  await openRow(w, 1);
  w.of("preview")[0].d.resolve(okPreview());
  await tick();
  const row = w.ui.rows()[1];
  const pre = w.ui.one("mq-xml");
  pre.focus();
  const key = pre.dispatch("keydown", { key: "Escape" });
  assert.ok(key.prevented);
  assert.equal(w.ui.one("mq-preview").hidden, true);
  assert.equal(doc.activeElement, row);
  await openRow(w, 1);
  w.of("preview")[1].d.resolve(okPreview());
  await tick();
  w.ui.one("mq-preview-close").click();
  assert.equal(w.ui.one("mq-preview").hidden, true);
  assert.equal(doc.activeElement, row);
});

test("a refresh keeps the open preview and the focus on the row while the item is listed", async () => {
  const w = world();
  await loaded(w);
  await openRow(w, 0);
  w.of("preview")[0].d.resolve(okPreview());
  await tick();
  w.ui.rows()[0].focus();
  await loaded(w, [item(1), item(2), item(3), item(4)]);
  assert.equal(w.ui.rows().length, 4);
  assert.equal(w.ui.one("mq-preview").hidden, false);
  assert.equal(doc.activeElement, w.ui.rows()[0]);
  assert.equal(w.ui.rows()[0].getAttribute("aria-selected"), "true");
});

test("Remove queue data is a two step action; cancelling asks for nothing; the answer is announced; a failure keeps the list", async () => {
  const w = world();
  await loaded(w);
  const remove = w.ui.one("mq-remove");
  const confirm = w.ui.one("mq-confirm");
  assert.equal(confirm.hidden, true);
  remove.click();
  assert.equal(confirm.hidden, false);
  assert.equal(confirm.getAttribute("role"), "alertdialog");
  assert.equal(doc.activeElement, w.ui.one("mq-confirm-no"));
  w.ui.one("mq-confirm-no").click();
  assert.equal(confirm.hidden, true);
  assert.equal(doc.activeElement, remove);
  assert.equal(w.of("remove").length, 0);
  remove.click();
  confirm.dispatch("keydown", { key: "Escape" });
  assert.equal(confirm.hidden, true);
  remove.click();
  w.ui.one("mq-confirm-yes").click();
  assert.equal(w.ui.status(), S.mqRemoving);
  assert.deepEqual(w.of("remove")[0].args, ["A", null]);
  w.of("remove")[0].d.reject(new Error("another job is running"));
  await tick();
  assert.match(w.ui.status(), /Could not remove the queue data: another job is running/);
  assert.equal(w.ui.rows().length, 3);
  remove.click();
  w.ui.one("mq-confirm-yes").click();
  w.of("remove")[1].d.resolve({ dbs: 1, items: 3 });
  await tick();
  w.of("summary").at(-1).d.resolve(summary({ dbs: [], totals: {} }));
  w.of("items").at(-1).d.resolve({ items: [], next: null });
  await tick();
  assert.equal(w.ui.status(), "Queue data removed. Databases: 1. Items: 3.");
  assert.equal(w.ui.rows().length, 0);
});

test("a new case starts from the defaults and asks again", async () => {
  const w = world();
  await loaded(w);
  await openRow(w, 0);
  w.current.caseId = "B";
  const resetting = w.panel.reset();
  assert.equal(w.of("items").at(-1).args[0], "B");
  w.of("summary").at(-1).d.resolve(summary({ enabled: false, dbs: [], totals: {} }));
  w.of("items").at(-1).d.resolve({ items: [], next: null });
  await resetting;
  await tick();
  assert.equal(w.ui.one("mq-preview").hidden, true);
  assert.equal(w.ui.rows().length, 0);
  assert.equal(w.ui.status(), S.mqDisabled);
});
