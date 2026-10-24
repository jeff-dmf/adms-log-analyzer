// The "Same key" button in the detail of the Operations panel: the type of the key comes from the starting record
// (first type, alphabetical, whose value is the event key); without one the button stays disabled with a reason.
// Fake DOM and a fake api; names and keys are synthetic.
import test from "node:test";
import assert from "node:assert/strict";

import { El, installGlobalDom, tick } from "./fixtures/fake-dom.mjs";

const doc = installGlobalDom();
globalThis.Element = El;
const { initEventsPanel } = await import("../../src/dmslog/web/js/events-panel.js");

const cls = (node, name) => node.className.split(/\s+/).includes(name);
const find = (root, name) => root.all().find((n) => cls(n, name));

function world({ entry, event = {}, onSameKey = true } = {}) {
  const calls = { same: [], entry: [] };
  const state = { caseId: "A" };
  const full = { id: 7, kind: "operation", name: "FunctionHostJob", key: "100001", status: "ok", ts_start: 1000, ts_end: 1100, start_entry_id: 70, ...event };
  const api = {
    events: async () => ({ events: [full], next: null }),
    event: async () => ({ event: full, entries: [], entries_total: 0, related: [] }),
    entry: async (caseId, id) => {
      calls.entry.push([caseId, id]);
      if (entry instanceof Error) {
        throw entry;
      }
      return entry;
    },
    eventPayloads: async () => ({ payloads: [] }),
    entryPayloads: async () => ({ payloads: [] }),
  };
  const host = doc.createElement("div");
  doc.body.appendChild(host);
  const panel = initEventsPanel(host, {
    api, getCase: () => state.caseId, onShowInGrid() {}, ...(onSameKey ? { onSameKey: (type, value) => calls.same.push([type, value]) } : {}),
  });
  return { host, panel, calls, state };
}

async function openEvent(w) {
  await w.panel.refresh();
  await w.panel.open(7);
  await tick();
  await tick();
  return find(w.host, "ev-samekey");
}

test("the button resolves the key type from the starting record and opens the panel", async () => {
  const w = world({ entry: { keys: [{ key_type: "tid", value: "9" }, { key_type: "job_id", value: "100001" }, { key_type: "gid", value: "100001" }] } });
  const button = await openEvent(w);
  assert.ok(button);
  assert.equal(button.disabled, false);
  assert.deepEqual(w.calls.entry, [["A", 70]]);
  button.click();
  assert.deepEqual(w.calls.same, [["gid", "100001"]]); // first type in alphabetical order whose value is the key
});

test("without a record carrying the key the button is disabled and says why", async () => {
  const w = world({ entry: { keys: [{ key_type: "tid", value: "9" }] } });
  const button = await openEvent(w);
  assert.equal(button.disabled, true);
  assert.notEqual(button.title, "");
  button.click();
  assert.deepEqual(w.calls.same, []);
});

test("a failing record read or an event without a starting record leaves the button disabled", async () => {
  const failing = world({ entry: new Error("boom") });
  assert.equal((await openEvent(failing)).disabled, true);
  const noStart = world({ entry: { keys: [] }, event: { start_entry_id: null } });
  assert.equal((await openEvent(noStart)).disabled, true);
  assert.deepEqual(noStart.calls.entry, []);
});

test("no button without a key on the event or without the host callback", async () => {
  const keyless = world({ entry: { keys: [] }, event: { key: null } });
  assert.equal(await openEvent(keyless), undefined);
  const bare = world({ entry: { keys: [{ key_type: "job_id", value: "100001" }] }, onSameKey: false });
  assert.equal(await openEvent(bare), undefined);
  assert.deepEqual(bare.calls.entry, []);
});

test("the click does nothing when the case changed after the detail was read", async () => {
  const w = world({ entry: { keys: [{ key_type: "job_id", value: "100001" }] } });
  const button = await openEvent(w);
  w.state.caseId = "B";
  button.click();
  assert.deepEqual(w.calls.same, []);
});
