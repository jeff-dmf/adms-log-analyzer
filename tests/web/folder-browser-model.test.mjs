// Folder browser model with a fake /api/fs/list: navigation, remembered folder, selection.
import test from "node:test";
import assert from "node:assert/strict";

import { FolderBrowser, STORAGE_KEY, breadcrumbs } from "../../src/dmslog/web/js/folder-browser-model.js";

function memoryStorage(initial = {}) {
  const data = { ...initial };
  return { getItem: (k) => (k in data ? data[k] : null), setItem: (k, v) => { data[k] = v; }, data };
}

const HOME = {
  path: "/home/u", parent: "/home", truncated: false, skipped: 0, other_files: 2,
  shortcuts: [{ kind: "home", path: "/home/u" }],
  entries: [
    { name: "logs", path: "/home/u/logs", type: "dir", hidden: false },
    { name: ".cache", path: "/home/u/.cache", type: "dir", hidden: true },
    { name: "a.zip", path: "/home/u/a.zip", type: "zip", size: 5, hidden: false },
    { name: "b.zip", path: "/home/u/b.zip", type: "zip", size: 6, hidden: false },
    { name: "c.zip", path: "/home/u/c.zip", type: "zip", size: 7, hidden: false },
    { name: "d.zip", path: "/home/u/d.zip", type: "zip", broken: true, hidden: false },
  ],
};

function fakeFs(table) {
  const calls = [];
  return {
    calls,
    list: async (path) => {
      calls.push(path);
      const hit = table[path === undefined ? "" : path];
      if (!hit) {
        throw Object.assign(new Error("not found"), { code: "path_not_found" });
      }
      return hit;
    },
  };
}

test("breadcrumbs for POSIX, drive and UNC paths", () => {
  assert.deepEqual(breadcrumbs("/home/u"), [
    { label: "/", path: "/" }, { label: "home", path: "/home" }, { label: "u", path: "/home/u" }]);
  assert.deepEqual(breadcrumbs("C:\\Users\\me"), [
    { label: "C:", path: "C:\\" }, { label: "Users", path: "C:\\Users" }, { label: "me", path: "C:\\Users\\me" }]);
  assert.deepEqual(breadcrumbs("\\\\srv\\share\\x").map((c) => c.path), ["\\\\srv", "\\\\srv\\share", "\\\\srv\\share\\x"]);
  assert.deepEqual(breadcrumbs(""), []);
});

test("open starts at the remembered folder, falls back to home when it is gone", async () => {
  const storage = memoryStorage({ [STORAGE_KEY]: "/gone" });
  const fs = fakeFs({ "": HOME });
  const model = new FolderBrowser({ fs, storage });
  assert.equal(await model.open(), true);
  assert.deepEqual(fs.calls, ["/gone", undefined]);
  assert.equal(model.path, "/home/u");
  assert.equal(storage.data[STORAGE_KEY], "/home/u");
  assert.equal(model.otherFiles, 2);
});

test("a broken storage does not stop the browser", async () => {
  const storage = { getItem() { throw new Error("blocked"); }, setItem() { throw new Error("blocked"); } };
  const model = new FolderBrowser({ fs: fakeFs({ "": HOME }), storage });
  assert.equal(await model.open(), true);
});

test("a failed navigation keeps the previous listing and reports the error", async () => {
  const model = new FolderBrowser({ fs: fakeFs({ "": HOME }), storage: null });
  await model.open();
  assert.equal(await model.load("/nope"), false);
  assert.equal(model.status, "error");
  assert.equal(model.error.code, "path_not_found");
  assert.equal(model.entries.length, 6);
});

test("hidden entries are shown only on request", async () => {
  const model = new FolderBrowser({ fs: fakeFs({ "": HOME }), storage: null });
  await model.open();
  assert.equal(model.visible.length, 5);
  model.setShowHidden(true);
  assert.equal(model.visible.length, 6);
});

test("selection: toggle, only, range, broken ZIPs and folders are not selectable", async () => {
  const model = new FolderBrowser({ fs: fakeFs({ "": HOME }), storage: null });
  await model.open();
  const [logs, , a, b, c, d] = model.entries;
  model.select(logs, "toggle");
  model.select(d, "toggle");
  assert.equal(model.selectedCount, 0);
  model.select(a, "toggle");
  model.select(c, "range");
  assert.deepEqual(model.selectedEntries().map((e) => e.path).sort(), ["/home/u/a.zip", "/home/u/b.zip", "/home/u/c.zip"]);
  model.select(b, "only");
  assert.deepEqual(model.selectedEntries().map((e) => e.path), ["/home/u/b.zip"]);
  model.select(b, "toggle");
  assert.equal(model.selectedCount, 0);
  model.selectAllVisible();
  assert.equal(model.selectedCount, 3);
});

test("the selection survives navigation to another folder", async () => {
  const other = { path: "/home/u/logs", parent: "/home/u", entries: [{ name: "z.zip", path: "/home/u/logs/z.zip", type: "zip", size: 1 }], shortcuts: [] };
  const model = new FolderBrowser({ fs: fakeFs({ "": HOME, "/home/u/logs": other }), storage: null });
  await model.open();
  model.select(model.entries[2], "toggle");
  await model.load("/home/u/logs");
  model.select(model.entries[0], "toggle");
  assert.deepEqual(model.selectedEntries().map((e) => e.path).sort(), ["/home/u/a.zip", "/home/u/logs/z.zip"]);
});

test("a late answer of an older navigation is dropped", async () => {
  let release;
  const slow = new Promise((resolve) => { release = resolve; });
  const fs = {
    list: async (path) => {
      if (path === "/slow") {
        await slow;
        return { path: "/slow", parent: "/", entries: [], shortcuts: [] };
      }
      return HOME;
    },
  };
  const model = new FolderBrowser({ fs, storage: null });
  const first = model.load("/slow");
  await model.load(undefined);
  release();
  await first;
  assert.equal(model.path, "/home/u");
});

test("focus: a removed focused element falls back, a kept key is restored", async () => {
  const { restoreFocusKey, trapTarget } = await import("../../src/dmslog/web/js/folder-browser-model.js");
  assert.equal(restoreFocusKey("crumb:/a", ["crumb:/", "crumb:/a"]), "crumb:/a");
  assert.equal(restoreFocusKey("crumb:/a/b", ["crumb:/", "crumb:/a"]), null); // element removed
  assert.equal(restoreFocusKey(null, ["crumb:/"]), null);
});

test("Tab trap: focus outside the dialog or on a removed element returns to the ends", async () => {
  const { trapTarget } = await import("../../src/dmslog/web/js/folder-browser-model.js");
  assert.equal(trapTarget(5, -1, false, false), "first");
  assert.equal(trapTarget(5, -1, false, true), "last");
  assert.equal(trapTarget(5, 4, true, false), "first");
  assert.equal(trapTarget(5, 0, true, true), "last");
  assert.equal(trapTarget(5, 2, true, false), null);
  assert.equal(trapTarget(0, -1, false, false), null);
});
