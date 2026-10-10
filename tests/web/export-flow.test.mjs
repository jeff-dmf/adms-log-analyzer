// The export and redaction flows with a fake api: stale answers, case changes, errors.
import test from "node:test";
import assert from "node:assert/strict";

import { createExportFlow, createRedactionFlow } from "../../src/dmslog/web/js/export-model.js";

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

const ctx = (extra) => ({ caseId: "A", filter: null, recordId: 3, eventId: null, ...extra });
const report = (name) => ({
  parts: [{ name, text: "dmslog report\r\n" }, { name: name + "-2", text: "part 2\r\n" }],
  legend: [{ category: "guid", label: "GUID", distinct: 1, replaced: 2 }],
  warnings: ["one note"],
  counts: { records: 1, events: 0, payloads: 0 },
  anonymized: true,
  map_saved: true,
});

function setup() {
  const calls = [];
  const current = { caseId: "A" };
  const states = [];
  const api = {
    exportReport(caseId, selection, options, signal) {
      const d = deferred();
      calls.push({ caseId, selection, options, signal, d });
      return d.promise;
    },
  };
  const flow = createExportFlow({ api, getCase: () => current.caseId, onState: (s) => states.push(s.status) });
  return { flow, calls, current, states };
}

test("a build sends the case, the selection and the options and keeps the parts", async () => {
  const { flow, calls, states } = setup();
  const building = flow.build("record", ctx(), { context: 2, anonymize: true, includePayloads: true });
  assert.equal(flow.state.status, "loading");
  assert.equal(calls[0].caseId, "A");
  assert.deepEqual(calls[0].selection, { ids: [3] });
  assert.deepEqual(calls[0].options, { anonymize: true, include_payloads: true, context: 2, max_bytes: 30000 });
  calls[0].d.resolve(report("one"));
  await building;
  assert.equal(flow.state.status, "ready");
  assert.equal(flow.state.parts.length, 2);
  assert.deepEqual(flow.state.legend, [{ label: "GUID", distinct: 1, replaced: 2 }]);
  assert.deepEqual(flow.state.warnings, ["one note"]);
  assert.equal(flow.state.mapSaved, true);
  assert.equal(flow.state.anonymized, true);
  assert.deepEqual(states.slice(-2), ["loading", "ready"]);
});

test("selecting a part", async () => {
  const { flow, calls } = setup();
  const building = flow.build("record", ctx(), {});
  calls[0].d.resolve(report("one"));
  await building;
  flow.selectPart(1);
  assert.equal(flow.state.active, 1);
  flow.selectPart(2);
  flow.selectPart(-1);
  flow.selectPart(0.5);
  assert.equal(flow.state.active, 1);
});

test("a new build aborts the old one and drops its answer", async () => {
  const { flow, calls } = setup();
  const first = flow.build("record", ctx(), {});
  const second = flow.build("record", ctx({ recordId: 4 }), {});
  assert.equal(calls[0].signal.aborted, true);
  calls[0].d.resolve(report("old"));
  await first;
  assert.deepEqual(flow.state.parts, []);
  assert.equal(flow.state.status, "loading");
  calls[1].d.resolve(report("new"));
  await second;
  assert.equal(flow.state.parts[0].name, "new");
});

test("reset and case change drop the answer", async () => {
  const { flow, calls, current } = setup();
  const first = flow.build("record", ctx(), {});
  flow.reset();
  calls[0].d.resolve(report("x"));
  await first;
  assert.equal(flow.state.status, "idle");
  assert.deepEqual(flow.state.parts, []);

  const second = flow.build("record", ctx(), {});
  current.caseId = "B"; // the user switched case while the report was being built
  calls[1].d.resolve(report("y"));
  await second;
  assert.deepEqual(flow.state.parts, []);
  assert.notEqual(flow.state.status, "ready");
});

test("nothing to export, other case, server errors and aborted requests", async () => {
  const { flow, calls, current } = setup();
  await flow.build("event", ctx(), {});
  assert.equal(flow.state.status, "error");
  assert.equal(flow.state.errorKey, "exportNoSelection");
  assert.equal(calls.length, 0);
  current.caseId = "B";
  await flow.build("record", ctx(), {}); // the viewer says case A, the app is on case B
  assert.equal(flow.state.errorKey, "exportNoCase");
  current.caseId = "A";
  const failing = flow.build("record", ctx(), {});
  calls[0].d.reject(Object.assign(new Error("bad option"), { status: 400 }));
  await failing;
  assert.equal(flow.state.status, "error");
  assert.equal(flow.state.error, "bad option");
  assert.equal(flow.state.errorKey, null);
  const aborted = flow.build("record", ctx(), {});
  flow.reset();
  calls[1].d.reject(Object.assign(new Error("x"), { name: "AbortError" }));
  await aborted;
  assert.equal(flow.state.status, "idle");
});

test("the anonymization flag the server answers with wins", async () => {
  const { flow, calls } = setup();
  const building = flow.build("record", ctx(), { anonymize: false });
  assert.equal(calls[0].options.anonymize, false);
  calls[0].d.resolve({ ...report("p"), anonymized: false, map_saved: false });
  await building;
  assert.equal(flow.state.anonymized, false);
  assert.equal(flow.state.mapSaved, false);
});

// -- redaction settings ----------------------------------------------------------------------

function redactionSetup() {
  const calls = [];
  const api = {
    redaction(signal) {
      const d = deferred();
      calls.push({ kind: "load", signal, d });
      return d.promise;
    },
    saveRedaction(profile, signal) {
      const d = deferred();
      calls.push({ kind: "save", profile, signal, d });
      return d.promise;
    },
  };
  const flow = createRedactionFlow({ api, onState: () => {} });
  return { flow, calls };
}

const stored = (name) => ({ profile: { name }, defaults: { name: "default" }, categories: [{ id: "guid", label: "GUIDs" }], invalid: false });

test("load, save and the stale answers of an older request", async () => {
  const { flow, calls } = redactionSetup();
  const loading = flow.load();
  assert.equal(flow.state.status, "loading");
  calls[0].d.resolve(stored("one"));
  await loading;
  assert.equal(flow.state.status, "ready");
  assert.equal(flow.state.profile.name, "one");
  assert.equal(flow.state.categories.length, 1);

  const saving = flow.save({ name: "two" });
  assert.equal(flow.state.status, "saving");
  assert.deepEqual(calls[1].profile, { name: "two" });
  const reloading = flow.load(); // a newer request cancels the save's answer
  assert.equal(calls[1].signal.aborted, true);
  calls[1].d.resolve({ profile: { name: "two" }, invalid: false });
  await saving;
  assert.equal(flow.state.profile.name, "one");
  calls[2].d.resolve(stored("three"));
  await reloading;
  assert.equal(flow.state.profile.name, "three");
});

test("an invalid stored profile is flagged, a rejected save keeps the form state", async () => {
  const { flow, calls } = redactionSetup();
  const loading = flow.load();
  calls[0].d.resolve({ ...stored("bad"), invalid: true, message: "save a new one" });
  await loading;
  assert.equal(flow.state.invalid, true);
  assert.equal(flow.state.message, "save a new one");
  const saving = flow.save({ name: "x" });
  calls[1].d.reject(Object.assign(new Error("Invalid redaction profile"), { status: 400 }));
  await saving;
  assert.equal(flow.state.status, "error");
  assert.equal(flow.state.error, "Invalid redaction profile");
  assert.equal(flow.state.invalid, true);
  const retry = flow.save({ name: "y" });
  calls[2].d.resolve({ profile: { name: "y" }, invalid: false });
  await retry;
  assert.equal(flow.state.invalid, false);
  assert.equal(flow.state.saved, true);
});
