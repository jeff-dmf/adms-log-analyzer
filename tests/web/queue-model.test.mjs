// Message queue panel (issue #74): the pure rules. All values are synthetic.
import test from "node:test";
import assert from "node:assert/strict";


const { S } = await import("../../src/dmslog/web/js/strings.js");
const M = await import("../../src/dmslog/web/js/queue-model.js");

const GUID = "{00000000-0000-0000-0000-000000000001}";

test("the state filter becomes the state parameter: Not sent is pending + other, All sends none", () => {
  assert.equal(M.stateParam("unsent"), "pending,other");
  assert.equal(M.stateParam("sent"), "sent");
  assert.equal(M.stateParam("all"), "");
  assert.deepEqual(M.buildItemsParams(M.defaultFilters(), null), { limit: 200, state: "pending,other" });
  assert.deepEqual(
    M.buildItemsParams({ state: "all", type: " T1 ", collection: "3", db: "7", q: " 42 " }, "10:5", 50),
    { limit: 50, type: "T1", collection: "3", db: "7", q: "42", after: "10:5" },
  );
});

test("the search box takes a GUID (braces or not, any case) or digits, nothing else", () => {
  for (const ok of ["", "  ", GUID, GUID.slice(1, -1).toUpperCase(), "1234567890123"]) {
    assert.equal(M.queryProblem(ok), "", ok);
  }
  for (const bad of ["abc", "12 34", "1; drop", "00000000-0000"]) {
    assert.equal(M.queryProblem(bad), S.mqBadQuery, bad);
  }
});

test("sizes, counts and identifiers are shown short", () => {
  assert.equal(M.formatSize(0), "0 B");
  assert.equal(M.formatSize(1536), "1.5 KB");
  assert.equal(M.formatSize(5 * 1024 * 1024), "5 MB");
  assert.equal(M.formatSize(null), "");
  assert.equal(M.countText(0), "0");
  assert.equal(M.countText(12), "12");
  assert.equal(M.countText(1000), "1,000+");
  assert.equal(M.abbreviateId(GUID), "00000000…0001");
  assert.equal(M.abbreviateId("short"), "short");
  assert.equal(M.abbreviateId(null), "");
});

test("a database row: counts, the warning of the snapshot, then the reader's warnings; an error says why", () => {
  const ok = M.buildDbRow({ id: 1, tag: "C1", name: "a.db3", adapter: "TestAdapter", cache: "TestCache", status: "ready", snapshot: "no_wal", warnings: ["items_truncated", "no_wal"], items: 5, pending: 2, sent: 2, other: 1 });
  assert.equal(ok.unsent, 3);
  assert.deepEqual(ok.warnings, [S.mqWarningNames.no_wal, S.mqWarningNames.items_truncated]);
  assert.match(ok.summary, /C1 · TestAdapter · TestCache — 5 items, 3 not sent/);
  const normal = M.buildDbRow({ id: 2, snapshot: "wal_applied", status: "ready", items: 1 });
  assert.deepEqual(normal.warnings, []);
  assert.match(normal.summary, /1 item,/);
  const failed = M.buildDbRow({ id: 3, status: "ready", snapshot: "wal_failed" });
  assert.deepEqual(failed.warnings, [S.mqWarningNames.wal_failed]);
  const broken = M.buildDbRow({ id: 4, status: "error", error: "too_large", warnings: ["items_truncated"] });
  assert.equal(broken.error, S.mqErrorNames.too_large);
  assert.deepEqual(broken.warnings, []);
  assert.equal(M.buildDbRow({ id: 5, status: "error", error: "future_code" }).error, "future_code");
  const other = M.buildDbRow({ id: 6, status: "not_a_queue", name: "b.db3" });
  assert.equal(other.isQueue, false);
  assert.equal(other.summary, "b.db3");
});

test("the summary view: counters, queues apart from the other databases, the collections that have one", () => {
  const view = M.buildSummaryView({
    enabled: true,
    dbs: [
      { id: 1, collection: 1, tag: "C1", status: "ready", items: 3, pending: 2, sent: 1, other: 0 },
      { id: 2, collection: 2, tag: "C2", status: "ready", items: 2, pending: 0, sent: 0, other: 2 },
      { id: 3, collection: 2, tag: "C2", status: "not_a_queue", name: "x.db3" },
    ],
    totals: { items: 5, pending: 2, sent: 1, other: 2 },
  });
  assert.equal(view.unsent, 4);
  assert.equal(view.queues.length, 2);
  assert.equal(view.others.length, 1);
  assert.deepEqual(view.collections, [{ id: "1", tag: "C1" }, { id: "2", tag: "C2" }]);
  assert.equal(M.headerText(view), "4 not sent · 5 items");
  assert.equal(M.headerText(M.buildSummaryView({ totals: { items: 1 } })), "0 not sent · 1 item");
  assert.equal(M.buildSummaryView(null).enabled, false);
});

test("the empty states, in order: disabled, no databases, empty queues, no match", () => {
  const view = (extra) => M.buildSummaryView({ enabled: true, dbs: [], totals: {}, ...extra });
  assert.equal(M.emptyKind(view({ enabled: false }), 0, false), "disabled");
  assert.equal(M.emptyKind(view({}), 0, false), "no_dbs");
  assert.equal(M.emptyKind(view({ dbs: [{ id: 1, status: "not_a_queue" }] }), 0, false), "no_dbs");
  // data that stayed after the option was turned off is still shown
  assert.equal(M.emptyKind(view({ enabled: false, dbs: [{ id: 1, status: "ready", items: 1 }], totals: { items: 1 } }), 1, false), "");
  const queues = view({ dbs: [{ id: 1, status: "ready", items: 0 }] });
  assert.equal(M.emptyKind(queues, 0, false), "empty");
  const full = view({ dbs: [{ id: 1, status: "ready", items: 3 }], totals: { items: 3 } });
  assert.equal(M.emptyKind(full, 0, true), "filtered");
  assert.equal(M.emptyKind(full, 0, false), "");
  assert.equal(M.emptyKind(full, 2, true), "");
  assert.equal(M.emptyKind(null, 0, false), "");
  assert.equal(M.emptyText("filtered"), S.mqNoMatch);
  assert.equal(M.emptyText("disabled"), S.mqDisabled);
  assert.equal(M.emptyText("none"), "");
  assert.equal(M.isFiltered(M.defaultFilters()), false);
  assert.equal(M.isFiltered({ ...M.defaultFilters(), state: "all" }), true);
  assert.equal(M.isFiltered({ ...M.defaultFilters(), q: "1" }), true);
});

test("an item row: adjusted time first, abbreviated message ID, log counts decide the actions", () => {
  const row = M.buildItemRow({
    id: 9, item_id: 123, message_id: GUID, msg_type: "TestMessageType", state: "pending", tag: "C1", size: 2048,
    ts: Date.UTC(2026, 0, 2, 3, 4, 5, 6), ts_adjusted: Date.UTC(2026, 0, 2, 3, 4, 6, 0),
    log: { msgid: 2, im_id: 0, payload_id: 77 },
  });
  assert.equal(row.time, "2026-01-02 03:04:06.000");
  assert.equal(row.messageShort, "00000000…0001");
  assert.equal(row.messageId, GUID);
  assert.equal(row.canShowLog, true);
  assert.equal(row.canShowCache, false);
  assert.equal(row.payloadId, 77);
  assert.equal(row.sizeText, "2 KB");
  assert.equal(row.stateLabel, S.mqStateNames.pending);
  assert.equal(M.rowLabel(row), "Item 123, TestMessageType, Pending");
  const bare = M.buildItemRow({ id: 1, item_id: 2, message_id: null, state: "weird", log: {} });
  assert.equal(bare.canShowLog, false);
  assert.equal(bare.time, "");
  assert.equal(bare.payloadId, null);
  assert.equal(bare.preview, "available");
  assert.equal(bare.stateLabel, "weird");
  assert.equal(M.buildItemRow({ item_id: 1 }), null);
  assert.equal(M.buildItemRow(null), null);
  assert.equal(M.buildItemRow({ id: 1, ts: 5, log: { msgid: 5000 } }).inLog, "1,000+");
  assert.equal(M.buildItemRow({ id: 1, preview: "too_large" }).preview, "too_large");
});

test("count lines say whether more is available", () => {
  assert.equal(M.countLine(1, false), "1 item shown");
  assert.equal(M.countLine(2, true), "2 items shown, more available");
  assert.equal(M.countLine(1200, false), "1,200 items shown");
});

test("the preview view: reconstruction base, notes by code, no body, too large, decode failure", () => {
  const row = M.buildItemRow({ id: 1, item_id: 5, size: 9 * 1024 * 1024 });
  const ok = M.buildPreviewView({
    form: "wire", available: true, decode: { ok: true },
    reconstruction: { basis: "exemplar", exemplars: 3, notes: ["names_guessed", "future_note"] },
    formatted: { text: "<A/>", ok: true, truncated_display: false },
  }, row);
  assert.equal(ok.kind, "ok");
  assert.equal(ok.label, S.mqReconstructionLabel);
  assert.equal(ok.basis, "Based on 3 payloads logged in this case");
  assert.deepEqual(ok.notes, [S.mqNoteNames.names_guessed, "future_note"]);
  assert.equal(ok.text, "<A/>");
  assert.equal(M.basisText({ basis: "exemplar", exemplars: 1 }), "Based on 1 payload logged in this case");
  assert.equal(M.basisText({ basis: "catalog", exemplars: 0 }), S.mqBasisCatalog);
  assert.equal(M.basisText({ basis: "none" }), S.mqBasisNone);
  assert.equal(M.basisText(null), S.mqBasisNone);
  const stored = M.buildPreviewView({ form: "stored", available: true, decode: { ok: true }, reconstruction: null, formatted: { text: "<B/>", truncated_display: true } }, row);
  assert.equal(stored.label, S.mqStoredNote);
  assert.equal(stored.basis, "");
  assert.deepEqual(stored.notes, []);
  assert.equal(stored.truncated, true);
  const big = M.buildPreviewView({ available: false, reason: "too_large" }, row);
  assert.equal(big.kind, "unavailable");
  assert.equal(big.message, "The body is larger than the preview limit (9 MB).");
  assert.equal(M.buildPreviewView({ available: false, reason: "no_body" }, row).message, S.mqNoBody);
  const bad = M.buildPreviewView({ available: true, decode: { ok: false, error: "truncated", offset: 4 } }, row);
  assert.equal(bad.kind, "decode_error");
  assert.equal(bad.message, "This message body could not be decoded (truncated).");
  assert.equal(bad.text, "");
});

// ---- the flow ---------------------------------------------------------------------------------------------------

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}
const abortError = () => Object.assign(new Error("aborted"), { name: "AbortError" });
const tick = async () => { for (let i = 0; i < 8; i += 1) await Promise.resolve(); };
const item = (id, extra = {}) => ({ id, item_id: 1000 + id, message_id: "00000000-0000-0000-0000-" + String(id).padStart(12, "0"), msg_type: "TypeA", state: "pending", ts: id * 1000, log: { msgid: 0, im_id: 0, payload_id: null }, ...extra });
const summary = (extra = {}) => ({ enabled: true, dbs: [{ id: 1, collection: 1, tag: "C1", status: "ready", items: 3, pending: 3, sent: 0, other: 0 }], totals: { items: 3, pending: 3, sent: 0, other: 0 }, ...extra });

function world() {
  const calls = [];
  const states = [];
  const hold = (name) => (...args) => {
    const d = deferred();
    const signal = args.at(-1) && typeof args.at(-1) === "object" && "aborted" in args.at(-1) ? args.at(-1) : null;
    calls.push({ name, args, d });
    signal?.addEventListener?.("abort", () => d.reject(abortError()));
    return d.promise;
  };
  const current = { caseId: "A" };
  const api = { queueSummary: hold("summary"), queueItems: hold("items"), queuePreview: hold("preview"), queueRemove: hold("remove") };
  const flow = M.createQueueFlow({ api, getCase: () => current.caseId, onState: (s) => states.push(s.status), limit: 2 });
  const of = (name) => calls.filter((c) => c.name === name);
  return { flow, calls, of, current, states };
}

async function loaded(w, items = [item(1), item(2)], next = null, sum = summary()) {
  const loading = w.flow.refresh();
  w.of("summary").at(-1).d.resolve(sum);
  w.of("items").at(-1).d.resolve({ items, next });
  await loading;
}

test("flow: loads the summary and the first page; without a case it asks for nothing", async () => {
  const w = world();
  w.current.caseId = null;
  await w.flow.refresh();
  assert.equal(w.flow.state.status, "nocase");
  assert.equal(w.calls.length, 0);
  w.current.caseId = "A";
  const loading = w.flow.refresh();
  assert.equal(w.flow.state.status, "loading");
  assert.deepEqual(w.of("items")[0].args[1], { limit: 2, state: "pending,other" });
  w.of("summary")[0].d.resolve(summary());
  w.of("items")[0].d.resolve({ items: [item(1), item(2), { nonsense: true }], next: "2000:2" });
  await loading;
  assert.equal(w.flow.state.status, "ready");
  assert.equal(w.flow.state.items.length, 2);
  assert.equal(w.flow.state.next, "2000:2");
  assert.deepEqual(w.flow.state.types, ["TypeA"]);
});

test("flow: Load more follows the cursor, skips repeated ids; a failure keeps the list and Try again works", async () => {
  const w = world();
  await loaded(w, [item(1), item(2)], "2000:2");
  const more = w.flow.loadMore();
  assert.equal(w.flow.state.loadingMore, true);
  assert.equal(w.of("items")[1].args[1].after, "2000:2");
  w.of("items")[1].d.reject(new Error("net down"));
  await more;
  assert.equal(w.flow.state.items.length, 2);
  assert.equal(w.flow.state.moreError, "net down");
  assert.equal(w.flow.state.loadingMore, false);
  const again = w.flow.loadMore();
  w.of("items")[2].d.resolve({ items: [item(2), item(3)], next: null });
  await again;
  assert.deepEqual(w.flow.state.items.map((r) => r.id), [1, 2, 3]);
  assert.equal(w.flow.state.next, null);
  assert.equal(w.flow.state.moreError, null);
  await w.flow.loadMore(); // nothing more: no request
  assert.equal(w.of("items").length, 3);
});

test("flow: a filter reads again from the start; a failure puts the filters back and keeps the list", async () => {
  const w = world();
  await loaded(w, [item(1), item(2)], "2000:2");
  const change = w.flow.setFilters({ state: "sent", type: "TypeA" });
  assert.equal(w.flow.state.filters.state, "sent");
  assert.deepEqual(w.of("items")[1].args[1], { limit: 2, state: "sent", type: "TypeA" });
  w.of("summary")[1].d.reject(new Error("offline"));
  w.of("items")[1].d.reject(new Error("offline"));
  await change;
  assert.equal(w.flow.state.status, "ready");
  assert.equal(w.flow.state.filters.state, "unsent");
  assert.equal(w.flow.state.items.length, 2);
  assert.equal(w.flow.state.refreshError, "offline");
  const ok = w.flow.setFilters({ state: "all" });
  w.of("summary")[2].d.resolve(summary());
  w.of("items")[2].d.resolve({ items: [item(7)], next: null });
  await ok;
  assert.deepEqual(w.flow.state.items.map((r) => r.id), [7]);
  assert.equal(w.flow.state.refreshError, null);
  assert.deepEqual(w.of("items")[2].args[1], { limit: 2 });
});

test("flow: a bad search is refused before any request; the same filters do not read again", async () => {
  const w = world();
  await loaded(w);
  await w.flow.setFilters({ q: "not valid" });
  assert.equal(w.flow.state.refreshError, S.mqBadQuery);
  assert.equal(w.flow.state.filters.q, "");
  assert.equal(w.of("items").length, 1);
  await w.flow.setFilters({ state: "unsent" });
  assert.equal(w.of("items").length, 1);
});

test("flow: a late answer of an older request is dropped", async () => {
  const w = world();
  const first = w.flow.refresh();
  const second = w.flow.refresh();
  w.of("summary")[1].d.resolve(summary());
  w.of("items")[1].d.resolve({ items: [item(5)], next: null });
  await second;
  w.of("items")[0].d.resolve({ items: [item(1)], next: null });
  await first;
  assert.deepEqual(w.flow.state.items.map((r) => r.id), [5]);
});

test("flow: the first load failing shows an error; a refresh failing later keeps what is on screen", async () => {
  const w = world();
  const failing = w.flow.refresh();
  w.of("summary")[0].d.reject(new Error("boom"));
  w.of("items")[0].d.reject(new Error("boom"));
  await failing;
  assert.equal(w.flow.state.status, "error");
  assert.equal(w.flow.state.error, "boom");
  await loaded(w);
  assert.equal(w.flow.state.status, "ready");
  const again = w.flow.refresh();
  w.of("summary").at(-1).d.reject(new Error("later"));
  w.of("items").at(-1).d.reject(new Error("later"));
  await again;
  assert.equal(w.flow.state.status, "ready");
  assert.equal(w.flow.state.items.length, 2);
  assert.equal(w.flow.state.refreshError, "later");
});

test("flow: the preview of an item, the form switch, a failed preview, and closing", async () => {
  const w = world();
  await loaded(w);
  const open = w.flow.select(2);
  assert.equal(w.flow.state.preview.status, "loading");
  assert.deepEqual(w.of("preview")[0].args.slice(0, 3), ["A", 2, "wire"]);
  w.of("preview")[0].d.resolve({ available: true, decode: { ok: true }, reconstruction: { basis: "none", exemplars: 0, notes: [] }, formatted: { text: "<X/>" } });
  await open;
  assert.equal(w.flow.state.preview.view.text, "<X/>");
  const sw = w.flow.setForm("stored");
  assert.equal(w.of("preview")[1].args[2], "stored");
  w.of("preview")[1].d.reject(new Error("gone"));
  await sw;
  assert.equal(w.flow.state.preview.status, "error");
  assert.equal(w.flow.state.preview.error, "gone");
  assert.equal(w.flow.state.items.length, 2); // the list stays
  await w.flow.setForm("bogus");
  assert.equal(w.of("preview").length, 2);
  w.flow.closePreview();
  assert.equal(w.flow.state.selectedId, null);
  await w.flow.select(999); // not in the list: nothing
  assert.equal(w.of("preview").length, 2);
});

test("flow: an older preview answer never replaces a newer one", async () => {
  const w = world();
  await loaded(w);
  const a = w.flow.select(1);
  const b = w.flow.select(2);
  w.of("preview")[1].d.resolve({ available: true, decode: { ok: true }, formatted: { text: "<B/>" } });
  await b;
  w.of("preview")[0].d.resolve({ available: true, decode: { ok: true }, formatted: { text: "<A/>" } });
  await a;
  assert.equal(w.flow.state.selectedId, 2);
  assert.equal(w.flow.state.preview.view.text, "<B/>");
});

test("flow: the preview stays open over a refresh while the item is listed, closes when it left", async () => {
  const w = world();
  await loaded(w);
  const open = w.flow.select(1);
  w.of("preview")[0].d.resolve({ available: true, decode: { ok: true }, formatted: { text: "<A/>" } });
  await open;
  await loaded(w, [item(1), item(2)]);
  assert.equal(w.flow.state.selectedId, 1);
  await loaded(w, [item(2)]);
  assert.equal(w.flow.state.selectedId, null);
});

test("flow: removing the queue data, then reading again; a failure says so and keeps the list", async () => {
  const w = world();
  await loaded(w);
  const failed = w.flow.removeData();
  assert.equal(w.flow.state.removing, true);
  assert.deepEqual(w.of("remove")[0].args, ["A", null]);
  w.of("remove")[0].d.reject(new Error("job_running"));
  assert.equal(await failed, false);
  assert.equal(w.flow.state.removeError, "job_running");
  assert.equal(w.flow.state.items.length, 2);
  const done = w.flow.removeData();
  w.of("remove")[1].d.resolve({ dbs: 1, items: 3 });
  await tick();
  w.of("summary").at(-1).d.resolve(summary({ dbs: [], totals: {} }));
  w.of("items").at(-1).d.resolve({ items: [], next: null });
  assert.equal(await done, true);
  assert.deepEqual(w.flow.state.removed, { dbs: 1, items: 3 });
  assert.equal(w.flow.state.items.length, 0);
});

test("flow: reset forgets the filters, the types and the preview of the old case", async () => {
  const w = world();
  await loaded(w);
  w.flow.setFilters({ state: "sent" }); // still in flight when the case changes
  w.current.caseId = "B";
  const resetting = w.flow.reset();
  assert.equal(w.flow.state.filters.state, "unsent");
  assert.deepEqual(w.flow.state.types, []);
  assert.equal(w.flow.state.summary, null);
  assert.equal(w.of("items").at(-1).args[0], "B");
  w.of("summary").at(-1).d.resolve(summary());
  w.of("items").at(-1).d.resolve({ items: [], next: null });
  await resetting;
  assert.equal(w.flow.state.status, "ready");
});

test("flow: Load more is blocked while a new filter's first page is on its way (no old cursor with a new filter)", async () => {
  const w = world();
  await loaded(w, [item(1), item(2)], "2:2");
  assert.equal(w.flow.state.next, "2:2");
  const change = w.flow.setFilters({ state: "sent" });
  assert.equal(w.flow.state.next, null); // the old cursor belongs to the old filters
  await w.flow.loadMore();
  assert.equal(w.of("items").length, 2); // no second request: nothing was asked with the old cursor
  w.of("summary").at(-1).d.resolve(summary());
  w.of("items").at(-1).d.resolve({ items: [item(3)], next: "3:3" });
  await change;
  assert.equal(w.flow.state.next, "3:3");
});

test("flow: a failed filter change goes back to what is really on screen, not to the last request", async () => {
  const w = world();
  await loaded(w, [item(1), item(2)], "2:2");
  const shown = { ...w.flow.state.filters };
  const b = w.flow.setFilters({ state: "sent" });
  const c = w.flow.setFilters({ type: "T9" }); // requested before B finished
  w.of("summary").at(-1).d.reject(new Error("down"));
  w.of("items").at(-1).d.reject(new Error("down"));
  await Promise.allSettled([b, c]);
  assert.deepEqual(w.flow.state.filters, shown); // the list on screen is still the first one
  assert.equal(w.flow.state.next, "2:2");
  assert.equal(w.flow.state.items.length, 2);
});

test("flow: a removal that was running when the case changed does not leave the new case stuck", async () => {
  const w = world();
  await loaded(w);
  const stale = w.flow.removeData();
  assert.equal(w.flow.state.removing, true);
  w.current.caseId = "B";
  const resetting = w.flow.reset();
  assert.equal(w.flow.state.removing, false);
  w.of("remove")[0].d.resolve({ dbs: 1, items: 1 }); // the answer of case A arrives late
  assert.equal(await stale, false);
  w.of("summary").at(-1).d.resolve(summary());
  w.of("items").at(-1).d.resolve({ items: [], next: null });
  await resetting;
  const next = w.flow.removeData();
  assert.equal(w.of("remove").length, 2); // the new case can remove again
  w.of("remove")[1].d.reject(new Error("x"));
  await next;
});

test("flow: a refresh that cancels a pending filter change and then fails restores the list on screen", async () => {
  const w = world();
  await loaded(w, [item(1), item(2)], "2:2");
  const shown = { ...w.flow.state.filters };
  const change = w.flow.setFilters({ state: "sent" }); // B in flight
  const again = w.flow.refresh(); // Refresh cancels B
  w.of("summary").at(-1).d.reject(new Error("down"));
  w.of("items").at(-1).d.reject(new Error("down"));
  await Promise.allSettled([change, again]);
  assert.deepEqual(w.flow.state.filters, shown); // the selectors show A again
  assert.equal(w.flow.state.next, "2:2"); // and Load more still works on A
  assert.equal(w.flow.state.items.length, 2);
});
