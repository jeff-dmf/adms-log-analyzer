// Bookmarks: row shaping, the shared marker store and the panel's list flow, with a fake api.
import test from "node:test";
import assert from "node:assert/strict";

import {
  BOOKMARK_COLORS, MAX_NOTE, buildBookmarkRow, checkNote, createBookmarkStore, createBookmarksFlow, markerLabel,
} from "../../src/dmslog/web/js/bookmarks-model.js";

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

const item = (id, extra = {}) => ({
  id, entry_id: id * 10, orphan: false, color: "red", note: null, created_at: "2026-01-02T03:04:05Z",
  member: "Log/a.log", line_no: id, ts: 1000 * id, msg: "m" + id, ...extra,
});

test("palette has names for every color", () => {
  assert.equal(BOOKMARK_COLORS.length, 7);
  assert.equal(new Set(BOOKMARK_COLORS).size, 7);
  assert.equal(markerLabel(null), "");
  assert.match(markerLabel({ color: "red" }), /Red/);
});

test("buildBookmarkRow keeps untrusted text as plain strings", () => {
  const row = buildBookmarkRow(item(3, { note: "<b>x</b>", msg: "<img src=x>", color: "weird" }));
  assert.equal(row.note, "<b>x</b>");
  assert.equal(row.text, "<img src=x>");
  assert.equal(row.color, "yellow"); // unknown colors fall back to the default
  assert.equal(row.created, "2026-01-02");
  assert.match(row.location, /Log\/a\.log.*3/);
  const orphan = buildBookmarkRow(item(4, { orphan: true, entry_id: null, ts: null, msg: null }));
  assert.equal(orphan.orphan, true);
  assert.equal(orphan.entryId, null);
  assert.equal(orphan.time, "");
  assert.equal(orphan.text, "");
});

test("checkNote trims, normalizes newlines and limits the length", () => {
  assert.deepEqual(checkNote("  a\r\nb  "), { note: "a\nb", error: null });
  assert.deepEqual(checkNote(null), { note: "", error: null });
  assert.equal(checkNote("x".repeat(MAX_NOTE + 1)).error, "tooLong");
  assert.equal(checkNote("x".repeat(MAX_NOTE)).error, null);
});

function storeSetup() {
  const calls = [];
  const changes = [];
  const current = { caseId: "A" };
  const api = {
    bookmarks: {
      lookup(caseId, ids, signal) {
        const d = deferred();
        calls.push({ op: "lookup", caseId, ids, signal, d });
        return d.promise;
      },
      create(caseId, entryId, color, note) {
        const d = deferred();
        calls.push({ op: "create", caseId, entryId, color, note, d });
        return d.promise;
      },
      update(caseId, id, changesArg) {
        const d = deferred();
        calls.push({ op: "update", caseId, id, changes: changesArg, d });
        return d.promise;
      },
      remove(caseId, id) {
        const d = deferred();
        calls.push({ op: "remove", caseId, id, d });
        return d.promise;
      },
      get(caseId, id) {
        const d = deferred();
        calls.push({ op: "get", caseId, id, d });
        return d.promise;
      },
    },
  };
  const timers = [];
  const store = createBookmarkStore({
    api, getCase: () => current.caseId, onChange: (kind) => changes.push(kind), schedule: (fn) => { timers.push(fn); return timers.length; },
  });
  const tick = async () => {
    const fn = timers.shift();
    await fn();
  };
  return { store, calls, changes, current, tick, timers };
}

test("ensure batches ids once and remembers unmarked ones", async () => {
  const { store, calls, changes, tick, timers } = storeSetup();
  store.ensure([1, 2, 3]);
  store.ensure([2, 3, 4]);
  assert.equal(timers.length, 1); // one scheduled flush
  const flushing = tick();
  assert.deepEqual(calls[0].ids, [1, 2, 3, 4]);
  calls[0].d.resolve({ bookmarks: { 2: { id: 7, color: "blue", has_note: true } } });
  await flushing;
  assert.deepEqual(store.get(2), { id: 7, color: "blue", has_note: true });
  assert.equal(store.get(1), null);
  assert.deepEqual(changes, ["lookup"]);
  store.ensure([1, 2, 4]); // all known: nothing new to ask
  assert.equal(timers.length, 0);
});

test("a case change drops the marks and ignores a late answer", async () => {
  const { store, calls, current, tick } = storeSetup();
  store.ensure([1]);
  const flushing = tick();
  current.caseId = "B";
  store.ensure([5]); // sync() notices the new case
  calls[0].d.resolve({ bookmarks: { 1: { id: 1, color: "red", has_note: false } } });
  await flushing;
  assert.equal(store.get(1), null);
  assert.equal(calls[0].signal.aborted, true);
});

test("toggle creates then removes, and reports edits", async () => {
  const { store, calls, changes } = storeSetup();
  const adding = store.toggle(5);
  assert.equal(calls[0].op, "create");
  assert.equal(calls[0].color, "yellow");
  calls[0].d.resolve({ id: 9, color: "yellow", note: null });
  await adding;
  assert.deepEqual(store.get(5), { id: 9, color: "yellow", has_note: false });
  const removing = store.toggle(5);
  assert.equal(calls[1].op, "remove");
  assert.equal(calls[1].id, 9);
  calls[1].d.resolve(null);
  await removing;
  assert.equal(store.get(5), null);
  assert.deepEqual(changes, ["edit", "edit"]);
});

test("toggle on a record bookmarked elsewhere refreshes instead of failing", async () => {
  const { store, calls, changes, timers } = storeSetup();
  const adding = store.toggle(5);
  calls[0].d.reject(Object.assign(new Error("dup"), { code: "already_bookmarked" }));
  assert.equal(await adding, null);
  assert.deepEqual(changes, ["reset"]);
  assert.equal(timers.length, 1); // the record is looked up again
});

test("update changes color and note; load fetches the full bookmark", async () => {
  const { store, calls } = storeSetup();
  const adding = store.toggle(5);
  calls[0].d.resolve({ id: 9, color: "yellow", note: null });
  await adding;
  assert.equal(await store.update(6, { color: "red" }), null); // not bookmarked: nothing to do
  const updating = store.update(5, { color: "green", note: "n" });
  assert.deepEqual(calls[1].changes, { color: "green", note: "n" });
  calls[1].d.resolve({ id: 9, color: "green", note: "n" });
  await updating;
  assert.deepEqual(store.get(5), { id: 9, color: "green", has_note: true });
  const loading = store.load(5);
  assert.equal(calls[2].id, 9);
  calls[2].d.resolve({ id: 9, note: "n" });
  assert.equal((await loading).note, "n");
  assert.equal(await store.load(77), null);
});

test("no case: ensure and toggle do nothing", async () => {
  const { store, calls, current, timers } = storeSetup();
  current.caseId = null;
  store.ensure([1]);
  assert.equal(timers.length, 0);
  assert.equal(await store.toggle(1), null);
  assert.equal(calls.length, 0);
});

function flowSetup() {
  const calls = [];
  const states = [];
  const current = { caseId: "A" };
  const api = {
    bookmarks: {
      list(caseId, params, signal) {
        const d = deferred();
        calls.push({ caseId, params, signal, d });
        return d.promise;
      },
    },
  };
  const flow = createBookmarksFlow({ api, getCase: () => current.caseId, onState: (s) => states.push(s.status), limit: 2 });
  return { flow, calls, states, current };
}

test("list flow: first page, keyset load more, duplicate ids", async () => {
  const { flow, calls } = flowSetup();
  const opening = flow.open();
  assert.equal(flow.state.status, "loading");
  assert.deepEqual(calls[0].params, { limit: 2 });
  calls[0].d.resolve({ bookmarks: [item(1), item(2)], next: 2 });
  await opening;
  assert.equal(flow.state.status, "ready");
  const more = flow.loadMore();
  assert.deepEqual(calls[1].params, { limit: 2, after: 2 });
  flow.loadMore(); // a click while loading does nothing
  assert.equal(calls.length, 2);
  calls[1].d.resolve({ bookmarks: [item(2), item(3)], next: null });
  await more;
  assert.deepEqual(flow.state.items.map((i) => i.id), [1, 2, 3]);
  await flow.loadMore();
  assert.equal(calls.length, 2);
});

test("list flow: filters restart the list and stale answers are dropped", async () => {
  const { flow, calls } = flowSetup();
  const first = flow.open();
  const second = flow.setFilters({ color: "red", text: "  boom " });
  assert.equal(calls[0].signal.aborted, true);
  assert.deepEqual(calls[1].params, { limit: 2, color: "red", q: "boom" });
  calls[0].d.resolve({ bookmarks: [item(1)], next: null });
  await first;
  assert.deepEqual(flow.state.items, []);
  calls[1].d.resolve({ bookmarks: [item(2)], next: null });
  await second;
  assert.deepEqual(flow.state.items.map((i) => i.id), [2]);
  const third = flow.setFilters({ color: "magenta", text: "" }); // unknown color is ignored
  assert.equal(flow.state.color, "");
  assert.deepEqual(calls[2].params, { limit: 2 });
  calls[2].d.resolve({ bookmarks: [], next: null });
  await third;
});

test("list flow: errors, no case, close, local edits", async () => {
  const { flow, calls, current, states } = flowSetup();
  const opening = flow.open();
  calls[0].d.reject(new Error("boom"));
  await opening;
  assert.equal(flow.state.status, "error");
  assert.equal(flow.state.error, "boom");
  const again = flow.refresh();
  calls[1].d.resolve({ bookmarks: [item(1), item(2)], next: null });
  await again;
  flow.replaceItem(item(2, { note: "edited" }));
  assert.equal(flow.state.items[1].note, "edited");
  flow.removeItem(1);
  assert.deepEqual(flow.state.items.map((i) => i.id), [2]);
  flow.close();
  assert.equal(flow.state.status, "idle");
  assert.deepEqual(flow.state.items, []);
  current.caseId = null;
  await flow.open();
  assert.equal(flow.state.status, "nocase");
  assert.ok(states.includes("nocase"));
});

test("list flow: a closed panel ignores a late answer", async () => {
  const { flow, calls } = flowSetup();
  const opening = flow.open();
  flow.close();
  calls[0].d.resolve({ bookmarks: [item(1)], next: null });
  await opening;
  assert.equal(flow.state.status, "idle");
  assert.deepEqual(flow.state.items, []);
});

test("list flow: an update answered after a case switch does not touch the new case's items", async () => {
  const calls = [];
  const current = { caseId: "A" };
  const api = {
    bookmarks: {
      list(caseId) {
        const d = deferred();
        calls.push({ op: "list", caseId, d });
        return d.promise;
      },
      update(caseId, id, changes) {
        const d = deferred();
        calls.push({ op: "update", caseId, id, changes, d });
        return d.promise;
      },
      remove(caseId, id) {
        const d = deferred();
        calls.push({ op: "remove", caseId, id, d });
        return d.promise;
      },
    },
  };
  const flow = createBookmarksFlow({ api, getCase: () => current.caseId, onState: () => {} });
  const opening = flow.open();
  calls[0].d.resolve({ bookmarks: [item(1, { note: "A-note" })], next: null });
  await opening;
  const updating = flow.updateItem(1, { note: "changed in A" });
  const deleting = flow.deleteItem(1);
  assert.deepEqual([calls[1].op, calls[1].caseId, calls[2].op, calls[2].caseId], ["update", "A", "remove", "A"]);
  current.caseId = "B";
  const reopening = flow.open(); // case B: same ids
  calls[3].d.resolve({ bookmarks: [item(1, { note: "B-note" })], next: null });
  await reopening;
  calls[1].d.resolve(item(1, { note: "changed in A" }));
  calls[2].d.resolve(null);
  assert.equal(await updating, null);
  assert.equal(await deleting, false);
  assert.equal(flow.state.items.length, 1);
  assert.equal(flow.state.items[0].note, "B-note");
});

test("list flow: a fresh update is applied; a list reload in between makes it stale", async () => {
  const calls = [];
  const api = {
    bookmarks: {
      list() {
        const d = deferred();
        calls.push({ op: "list", d });
        return d.promise;
      },
      update(caseId, id, changes) {
        const d = deferred();
        calls.push({ op: "update", changes, d });
        return d.promise;
      },
      remove() {
        return Promise.resolve(null);
      },
    },
  };
  const flow = createBookmarksFlow({ api, getCase: () => "A", onState: () => {} });
  const opening = flow.open();
  calls[0].d.resolve({ bookmarks: [item(1), item(2)], next: null });
  await opening;
  const ok = flow.updateItem(1, { color: "blue" });
  calls[1].d.resolve(item(1, { color: "blue" }));
  assert.equal((await ok).color, "blue");
  assert.equal(flow.state.items[0].color, "blue");
  const stale = flow.updateItem(2, { color: "green" });
  const reload = flow.refresh();
  calls[3].d.resolve({ bookmarks: [item(2, { color: "gray" })], next: null });
  await reload;
  calls[2].d.resolve(item(2, { color: "green" }));
  assert.equal(await stale, null);
  assert.equal(flow.state.items[0].color, "gray");
});

test("list flow: a late REJECTION from the old case is dropped, not reported", async () => {
  const calls = [];
  const current = { caseId: "A" };
  const api = {
    bookmarks: {
      list() {
        const d = deferred();
        calls.push({ op: "list", d });
        return d.promise;
      },
      update() {
        const d = deferred();
        calls.push({ op: "update", d });
        return d.promise;
      },
      remove() {
        const d = deferred();
        calls.push({ op: "remove", d });
        return d.promise;
      },
    },
  };
  const flow = createBookmarksFlow({ api, getCase: () => current.caseId, onState: () => {} });
  const opening = flow.open();
  calls[0].d.resolve({ bookmarks: [item(1)], next: null });
  await opening;
  const updating = flow.updateItem(1, { note: "x" });
  const deleting = flow.deleteItem(1);
  current.caseId = "B";
  const reopening = flow.open();
  calls[3].d.resolve({ bookmarks: [item(1, { note: "B" })], next: null });
  await reopening;
  calls[1].d.reject(new Error("old case failed"));
  calls[2].d.reject(new Error("old case failed"));
  assert.equal(await updating, null); // no throw: nothing reaches the panel's error handling
  assert.equal(await deleting, false);
  assert.equal(flow.state.status, "ready");
  assert.equal(flow.state.items[0].note, "B");
  // a closed list drops late failures too, and a current failure is still reported
  const again = flow.updateItem(1, { note: "y" });
  flow.close();
  calls[4].d.reject(new Error("late"));
  assert.equal(await again, null);
  const last = flow.open();
  calls[calls.length - 1].d.resolve({ bookmarks: [item(1)], next: null });
  await last;
  const failing = flow.updateItem(1, { note: "z" });
  calls[calls.length - 1].d.reject(new Error("real failure"));
  await assert.rejects(failing, /real failure/);
});
