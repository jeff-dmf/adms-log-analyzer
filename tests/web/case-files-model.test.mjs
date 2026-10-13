// Model of the chosen ZIPs list, the create flow and the native/built-in choice, with fake deps.
import test from "node:test";
import assert from "node:assert/strict";

import {
  CaseFilesState, PickFlow, ersKind, formatSize, pathKey, refusalResult, splitPath,
} from "../../src/dmslog/web/js/case-files-model.js";

const GUID = "0A1B2C3D-0000-1111-2222-333344445555";
const ENT = `/data/ERS_CL_${GUID}_20260101120000_Enterprise.zip`;
const SENS = `/data/ERS_CL_${GUID}_20260101120000_Sensitive_Enterprise.zip`;

test("kind comes from the file name only", () => {
  assert.equal(ersKind(`ERS_CL_${GUID}_20260101120000_Enterprise.zip`), "enterprise");
  assert.equal(ersKind(`ers_cl_${GUID.toLowerCase()}_20260101120000_Sensitive_Enterprise_x.ZIP`), "sensitive");
  assert.equal(ersKind("backup.zip"), null);
  assert.equal(ersKind(`ERS_CL_${GUID}_2026_Enterprise.zip`), null);
});

test("paths split on both separators and sizes are readable", () => {
  assert.deepEqual(splitPath("C:\\Logs\\a.zip"), { dir: "C:\\Logs", name: "a.zip" });
  assert.deepEqual(splitPath("/a.zip"), { dir: "/", name: "a.zip" });
  assert.deepEqual(splitPath("a.zip"), { dir: "", name: "a.zip" });
  assert.equal(formatSize(512), "512 B");
  assert.equal(formatSize(1536), "1.5 KB");
  assert.equal(formatSize(3 * 1024 ** 3), "3.0 GB");
  assert.equal(formatSize(null), "");
});

test("Windows paths compare without case, POSIX paths exactly", () => {
  assert.equal(pathKey("C:\\A\\x.zip"), pathKey("c:/a/X.ZIP"));
  assert.notEqual(pathKey("/a/x.zip"), pathKey("/a/X.zip"));
});

test("add ignores duplicates with a per-file notice and keeps sizes", () => {
  const state = new CaseFilesState();
  assert.equal(state.add([{ path: ENT, size: 10 }, SENS]), 2);
  assert.equal(state.add([ENT, "/other/b.zip"]), 1);
  assert.equal(state.items.length, 3);
  assert.deepEqual(state.notices.map((n) => [n.type, n.name]), [["duplicate", `ERS_CL_${GUID}_20260101120000_Enterprise.zip`]]);
  assert.equal(state.items[0].size, 10);
  assert.equal(state.items[0].kind, "enterprise");
  assert.equal(state.items[1].kind, "sensitive");
  assert.equal(state.items[2].kind, null);
});

test("remove and clear update the list; errors become notices", () => {
  const state = new CaseFilesState();
  state.add([ENT, SENS]);
  state.remove(state.items[0].id);
  assert.equal(state.items.length, 1);
  state.addErrors([{ path: "/x/bad.zip", error: "not_a_zip", message: "Not a ZIP" }]);
  assert.equal(state.notices[0].message, "Not a ZIP");
  state.clear();
  assert.equal(state.items.length, 0);
  assert.equal(state.canCreate, false);
});

function deps(overrides = {}) {
  const calls = [];
  return {
    calls,
    createCase: async (name) => { calls.push(["create", name]); return { id: name }; },
    selectCase: async (id) => { calls.push(["select", id]); },
    openPath: async (path) => { calls.push(["open", path]); return { status: "ok" }; },
    ...overrides,
  };
}

test("create needs a name and at least one file", async () => {
  const state = new CaseFilesState();
  const d = deps();
  assert.deepEqual(await state.create("A", d), { state: "blocked", reason: "files" });
  state.add([ENT]);
  assert.deepEqual(await state.create("  ", d), { state: "blocked", reason: "name" });
  assert.equal(d.calls.length, 0);
});

test("create opens the files in sequence and clears the list on success", async () => {
  const state = new CaseFilesState();
  state.add([ENT, SENS]);
  const d = deps();
  const result = await state.create("Case 1", d);
  assert.equal(result.state, "ok");
  assert.deepEqual(d.calls, [["create", "Case 1"], ["select", "Case 1"], ["open", ENT], ["open", SENS]]);
  assert.equal(state.items.length, 0);
  assert.equal(state.busy, false);
});

test("a refused file keeps the list and the others still open; retry reuses the case", async () => {
  const state = new CaseFilesState();
  state.add([ENT, "/y/other.zip", SENS]);
  const d = deps({
    openPath: async (path) => {
      d.calls.push(["open", path]);
      return path.includes("other") ? { status: "refused", message: "different server" } : { status: "ok" };
    },
  });
  const result = await state.create("C", d);
  assert.deepEqual([result.state, result.ok, result.total], ["partial", 2, 3]);
  assert.deepEqual(state.items.map((i) => i.status), ["ok", "refused", "ok"]);
  assert.equal(state.items[1].message, "different server");
  d.calls.length = 0;
  state.remove(state.items[1].id);
  const again = await state.create("C", d);
  assert.equal(again.state, "ok"); // the case is not created twice and ok items are not reopened
  assert.deepEqual(d.calls, [["select", "C"]]);
});

test("a thrown error marks only that file as failed", async () => {
  const state = new CaseFilesState();
  state.add([ENT, SENS]);
  const d = deps({
    openPath: async (path) => {
      if (path === ENT) {
        throw new Error("boom");
      }
      return { status: "ok" };
    },
  });
  const result = await state.create("C", d);
  assert.equal(result.state, "partial");
  assert.deepEqual(state.items.map((i) => i.status), ["failed", "ok"]);
  assert.equal(state.items[0].message, "boom");
});

test("a failed case creation keeps the list and reports the server message", async () => {
  const state = new CaseFilesState();
  state.add([ENT]);
  const d = deps({ createCase: async () => { throw new Error("A case with this name already exists."); } });
  const result = await state.create("C", d);
  assert.deepEqual(result, { state: "blocked", reason: "case" });
  assert.equal(state.items.length, 1);
  assert.equal(state.busy, false);
  assert.match(state.notices[0].message, /already exists/);
});

test("removing and clearing are ignored while creating", async () => {
  const state = new CaseFilesState();
  state.add([ENT]);
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  const d = deps({ openPath: async () => { await gate; return { status: "ok" }; } });
  const running = state.create("C", d);
  await new Promise((r) => setTimeout(r, 0));
  state.remove(state.items[0].id);
  state.clear();
  assert.equal(state.items.length, 1);
  assert.equal((await Promise.resolve(state.canCreate)), false);
  release();
  await running;
});

test("refusalResult explains a different server in plain words", () => {
  const text = { conflictServer: "S", conflictCapture: "C", busy: "B" };
  assert.deepEqual(refusalResult({ status: 409, code: "different_guid", message: "x" }, text), { status: "refused", code: "different_guid", message: "S" });
  assert.equal(refusalResult({ status: 409, code: "different_capture", message: "x" }, text).message, "C");
  assert.equal(refusalResult({ status: 409, code: "job_running", message: "x" }, text).message, "B");
  const other = refusalResult({ status: 400, code: "path_not_found", message: "No such path" }, text);
  assert.deepEqual([other.status, other.message], ["failed", "No such path"]);
});

function fakeFs({ available = true, pick } = {}) {
  const calls = [];
  return {
    calls,
    capabilities: async () => { calls.push("caps"); return { native: { available } }; },
    pick: async (kind, multiple) => { calls.push(["pick", kind, multiple]); return pick(); },
    cancelPick: async () => { calls.push("cancel"); return null; },
  };
}

test("pick flow: native paths, cancel and timeout", async () => {
  let fs = fakeFs({ pick: () => ({ paths: [ENT], cancelled: false, timed_out: false, errors: [{ path: "/b", message: "m" }] }) });
  let flow = new PickFlow(fs);
  const picked = await flow.browse();
  assert.deepEqual([picked.outcome, picked.paths, picked.errors.length], ["paths", [ENT], 1]);
  assert.deepEqual(fs.calls, ["caps", ["pick", "zip", true]]);
  await flow.browse();
  assert.equal(fs.calls.filter((c) => c === "caps").length, 1); // capabilities asked once
  fs = fakeFs({ pick: () => ({ paths: [], cancelled: true, errors: [] }) });
  assert.equal((await new PickFlow(fs).browse()).outcome, "cancelled");
  fs = fakeFs({ pick: () => ({ paths: [], timed_out: true, errors: [] }) });
  assert.deepEqual(await new PickFlow(fs, 15).browse(), { outcome: "timed_out", minutes: 15 });
});

test("pick flow: no native dialog or a failing one goes to the built-in browser for good", async () => {
  let fs = fakeFs({ available: false });
  assert.equal((await new PickFlow(fs).browse()).outcome, "fallback");
  assert.deepEqual(fs.calls, ["caps"]);
  const err = Object.assign(new Error("x"), { code: "picker_failed" });
  fs = fakeFs({ pick: () => { throw err; } });
  const flow = new PickFlow(fs);
  assert.deepEqual(await flow.browse(), { outcome: "fallback", failed: true });
  await flow.browse();
  assert.equal(fs.calls.filter((c) => Array.isArray(c)).length, 1); // the native one is not tried again
});

test("pick flow: busy dialog and other errors", async () => {
  let fs = fakeFs({ pick: () => { throw Object.assign(new Error("x"), { code: "picker_busy" }); } });
  assert.equal((await new PickFlow(fs).browse()).outcome, "busy");
  fs = fakeFs({ pick: () => { throw new Error("network down"); } });
  assert.deepEqual(await new PickFlow(fs).browse(), { outcome: "error", message: "network down" });
  fs = { capabilities: async () => { throw new Error("x"); } };
  assert.equal((await new PickFlow(fs).browse()).outcome, "fallback");
});
