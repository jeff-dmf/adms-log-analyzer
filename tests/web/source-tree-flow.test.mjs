// The sources tree over the fake DOM: rows for the open folders, the check states, a folder click, the keys, the filter.
import assert from "node:assert/strict";
import { test } from "node:test";
import { installGlobalDom } from "./fixtures/fake-dom.mjs";

const doc = installGlobalDom();
const { SourceTree } = await import("../../src/dmslog/web/js/source-tree.js");

const src = (id, member, extra = {}) => ({ id, name: member.split("/").pop(), member, records: 10, status: "done", sensitive: false, collection: 1, family: "wfm", pid: 1, ...extra });
const SOURCES = [
  src(1, "SchneiderElectricData/ADMS/Log/wfm.log"),
  src(2, "SchneiderElectricData/ADMS/Log/ont.log", { sensitive: true }),
  src(3, "SchneiderElectricData/OASyS/Log/srv.log", { status: "error", error: "bad" }),
  src(5, "Deployment/Logs/install.log"),
];

function setup(query = "", sources = SOURCES) {
  const host = doc.createElement("div");
  doc.body.appendChild(host);
  const panel = {
    sources, selected: new Set(), query, caseId: "c1", refs: { filterInput: doc.createElement("input") }, treeHook: null,
    treeActive: true, counts: [],
    notifyRendered(visible, total) { this.counts.push([visible, total]); },
    applySelection(ids) { this.selected = new Set(ids); this.treeHook(); },
  };
  const store = new Map();
  const tree = new SourceTree({ host, panel, storage: { getItem: (k) => store.get(k) ?? null, setItem: (k, v) => store.set(k, v) } });
  const rows = () => host.children.filter((c) => c.className.includes("st-row"));
  const byName = (text) => rows().find((r) => r.children.find((c) => c.className === "st-name").textContent === text);
  return { host, panel, tree, rows, byName, store };
}

const names = (t) => t.rows().map((r) => r.children.find((c) => c.className === "st-name").textContent);

test("the first level is open and every row says its level, size and check state", () => {
  const t = setup();
  assert.deepEqual(names(t), ["Deployment/Logs", "install.log", "SchneiderElectricData", "ADMS/Log", "OASyS/Log"]);
  assert.equal(t.host.attrs.role, "tree");
  const folder = t.byName("SchneiderElectricData");
  assert.equal(folder.attrs.role, "treeitem");
  assert.equal(folder.attrs["aria-level"], "1");
  assert.equal(folder.attrs["aria-expanded"], "true");
  assert.equal(folder.attrs["aria-checked"], "false");
  assert.equal(t.byName("ADMS/Log").attrs["aria-expanded"], "false");
  assert.equal(t.byName("install.log").attrs["aria-expanded"], undefined, "a file has no expanded state");
});

test("clicking a folder checks everything under it, and a file makes it mixed", () => {
  const t = setup();
  t.byName("ADMS/Log").dispatch("click", { target: t.byName("ADMS/Log").children[1] });
  assert.deepEqual([...t.panel.selected].sort(), [1, 2]);
  assert.equal(t.byName("ADMS/Log").attrs["aria-checked"], "true");
  assert.equal(t.byName("SchneiderElectricData").attrs["aria-checked"], "mixed");
  t.byName("ADMS/Log").dispatch("click", { target: t.byName("ADMS/Log").children[1] });
  assert.equal(t.panel.selected.size, 0);
});

test("the twisty opens and closes a folder and the open folders are remembered per case", () => {
  const t = setup();
  const folder = t.byName("ADMS/Log");
  const twisty = folder.children[0];
  twisty.className = "st-twisty";
  folder.dispatch("click", { target: twisty });
  assert.deepEqual(names(t).slice(3, 6), ["ADMS/Log", "ont.log", "wfm.log"]);
  assert.match(t.store.get("dmslog.sourceTree.c1"), /ADMS/);
  t.byName("ADMS/Log").dispatch("click", { target: t.byName("ADMS/Log").children[0] });
  assert.equal(names(t).includes("ont.log"), false);
});

test("arrow keys move, open and close; Space checks; one row is the tab stop", () => {
  const t = setup();
  const first = t.rows()[0];
  assert.equal(first.attrs.tabindex, "0");
  assert.equal(t.rows()[1].attrs.tabindex, "-1");
  first.focus();
  const down = first.dispatch("keydown", { key: "ArrowDown" });
  assert.equal(down.prevented, true);
  assert.equal(doc.activeElement, t.rows()[1]);
  const adms = t.byName("ADMS/Log");
  adms.focus();
  adms.dispatch("keydown", { key: "ArrowRight" });
  assert.equal(names(t).includes("wfm.log"), true, "ArrowRight opens a closed folder");
  t.byName("wfm.log").focus();
  t.byName("wfm.log").dispatch("keydown", { key: " " });
  assert.deepEqual([...t.panel.selected], [1]);
  t.byName("wfm.log").dispatch("keydown", { key: "ArrowLeft" });
  assert.equal(doc.activeElement, t.byName("ADMS/Log"), "ArrowLeft on a file goes to its folder");
});

test("a filter lists only the paths to a match and a folder checks only what it shows", () => {
  const t = setup("oasys");
  assert.deepEqual(names(t), ["SchneiderElectricData", "OASyS/Log", "srv.log"]);
  t.byName("SchneiderElectricData").dispatch("click", { target: t.byName("SchneiderElectricData").children[1] });
  assert.deepEqual([...t.panel.selected], [3], "only the shown source");
  assert.equal(t.byName("srv.log").children.find((c) => c.className === "source-unreadable").hidden, false);
});

test("no match and no sources say so", () => {
  const t = setup("zzz");
  assert.equal(t.rows().length, 0);
  assert.match(t.host.textContent, /No/);
});

test("the filter box talks to the tree: its count, ArrowDown and Enter", () => {
  const t = setup("oasys");
  assert.deepEqual(t.panel.counts.at(-1), [1, 4], "the sources whose path matches, of all");
  t.tree.focusFirst();
  assert.equal(doc.activeElement, t.rows()[0]);
  t.tree.toggleFirst();
  assert.deepEqual([...t.panel.selected], [3], "Enter toggles the first row of the tree, filtered");
  const none = setup("zzz");
  assert.deepEqual(none.panel.counts.at(-1), [0, 4]);
  none.tree.toggleFirst();
  assert.equal(none.panel.selected.size, 0);
});

test("a folder chain that splits keeps its folders open and the focus on a surviving row", () => {
  const t = setup("", [src(1, "A/B/x.log")]);
  assert.deepEqual(names(t), ["A/B", "x.log"]);
  t.byName("A/B").focus();
  t.panel.sources = [src(1, "A/B/x.log"), src(2, "A/y.log")];
  t.panel.treeHook();
  assert.deepEqual(names(t), ["A", "B", "x.log", "y.log"], "both folders are still open");
  assert.equal(doc.activeElement, t.byName("B"), "the folder keeps its row and its focus");
  assert.equal(t.byName("B").attrs.tabindex, "0");
  // A row that leaves hands the focus to the folder that held it.
  t.byName("x.log").focus();
  t.panel.sources = [src(2, "A/y.log")];
  t.panel.treeHook();
  assert.equal(doc.activeElement, t.byName("A"));
});

test("a pool folder checks all its instances, opens by keyboard and names each instance for assistive tech", () => {
  const fh = (id, n) => src(id, "Svc/Log/DMS_FunctionHost_" + n + ".log", { family: "DMSFH", pool: "DMS_FunctionHost", instance: n });
  const t = setup("", [fh(1, 10), fh(2, 2), src(3, "Svc/Log/other.log")]);
  assert.deepEqual(names(t), ["Svc/Log", "DMS_FunctionHost (2 instances)", "other.log"]);
  const pool = t.byName("DMS_FunctionHost (2 instances)");
  assert.equal(pool.attrs["aria-expanded"], "false");
  assert.equal(pool.attrs["aria-level"], "2");
  pool.focus();
  pool.dispatch("keydown", { key: "ArrowRight" });
  assert.deepEqual(names(t).slice(1), ["DMS_FunctionHost (2 instances)", "DMS_FunctionHost_2.log", "DMS_FunctionHost_10.log", "other.log"]);
  const leaf = t.byName("DMS_FunctionHost_2.log");
  assert.equal(leaf.attrs["aria-level"], "3");
  assert.equal(leaf.attrs["aria-label"], "DMS_FunctionHost #2, DMS_FunctionHost_2.log");
  assert.equal(t.byName("other.log").getAttribute("aria-label"), null);
  leaf.focus();
  leaf.dispatch("keydown", { key: "ArrowLeft" });
  assert.equal(doc.activeElement, t.byName("DMS_FunctionHost (2 instances)"), "ArrowLeft goes to the pool folder");
  t.byName("DMS_FunctionHost (2 instances)").dispatch("keydown", { key: " " });
  assert.deepEqual([...t.panel.selected].sort(), [1, 2]);
  assert.equal(t.byName("DMS_FunctionHost (2 instances)").attrs["aria-checked"], "true");
  assert.equal(t.byName("Svc/Log").attrs["aria-checked"], "mixed");
});
