// R3c and R4c in the two views of the sources: the Reduced badge, the closed "Not indexed" group, the View action and the
// "Large files" choice of the opening. Synthetic data; the viewer and the model load through replaced loaders.
import assert from "node:assert/strict";
import { test } from "node:test";
import { installGlobalDom, tick, until } from "./fixtures/fake-dom.mjs";

const doc = installGlobalDom();
const { SourceTree } = await import("../../src/dmslog/web/js/source-tree.js");
const treeModel = await import("../../src/dmslog/web/js/source-tree-model.js");
const { SourcesPanel } = await import("../../src/dmslog/web/js/sources.js");
const { api } = await import("../../src/dmslog/web/js/api.js");

const MB = 1024 * 1024;
const REDUCED = { cap: 256 * MB, head_end: MB, tail_start: 700 * MB, skipped_bytes: 600 * MB, skipped_lines: 2400000, skipped_from_ts: Date.UTC(2026, 1, 3, 4, 5, 6), skipped_to_ts: Date.UTC(2026, 1, 9, 1, 2, 3) };
const ok = (id, member, extra = {}) => ({ id, name: member.split("/").pop(), member, records: 10, status: "done", sensitive: false, collection: 1, family: "svc", pid: 1, size: 100, reduced: null, error: null, ...extra });
const skipped = (id, member, error) => ok(id, member, { status: "skipped", error, records: 0, family: null, pid: null });
const SOURCES = [
  ok(1, "Root/Log/big.log", { reduced: REDUCED, size: 800 * MB }),
  ok(2, "Root/Log/small.log"),
  skipped(3, "Root/Cfg/app.config", "not_indexable"),
  skipped(4, "Root/Cfg/sys.config", "not_indexable"),
  skipped(5, "Root/Perf/perf.blg", "not_indexable"),
  skipped(6, "Root/Log/install.log", "out_of_scope"),
];
const find = (root, cls) => root.all().find((n) => n.className.split(/\s+/).includes(cls));

// ---- the tree -----------------------------------------------------------------------------------------------------

function treeSetup(sources = SOURCES) {
  const host = doc.createElement("div");
  doc.body.appendChild(host);
  const viewed = [];
  const panel = {
    sources, selected: new Set(), query: "", caseId: "c1", refs: { filterInput: doc.createElement("input") }, treeHook: null,
    treeActive: true, counts: [],
    notifyRendered(visible, total) { this.counts.push([visible, total]); },
    applySelection(ids) { this.selected = new Set(ids); this.treeHook(); },
    viewSource(source, opener) { viewed.push([source.id, opener]); },
  };
  const tree = new SourceTree({ host, panel, storage: null });
  const rows = () => host.children.filter((c) => c.className.includes("st-row"));
  const nameOf = (r) => r.children.find((c) => c.className === "st-name").textContent;
  const byName = (text) => rows().find((r) => nameOf(r) === text);
  return { host, panel, tree, rows, byName, viewed, names: () => rows().map(nameOf) };
}

test("tree model: the skipped sources are a closed group at the end that no folder checks", () => {
  const tree = treeModel.buildTree(SOURCES);
  const last = tree.roots[tree.roots.length - 1];
  assert.equal(last.notIndexed, true);
  assert.equal(last.name, "Not indexed (4)");
  assert.deepEqual(tree.ids.sort(), [1, 2], "only indexed sources are selectable");
  assert.equal(treeModel.defaultOpen(tree).has(last.key), false, "closed by default");
  assert.deepEqual(treeModel.idsOf(last, null), []);
  const open = new Set([last.key]);
  const rows = treeModel.visibleRows(tree, open, null);
  assert.equal(rows.some((r) => r.node.notIndexed && r.node.folder && r.level === 2), true);
  assert.equal(treeModel.buildTree([skipped(1, "A/x.config", "not_indexable")]).roots.length, 1, "a case of only skipped sources still shows the group");
});

test("tree: the group is collapsed, its rows are not checkable, and a folder click opens it", () => {
  const t = treeSetup();
  const group = t.byName("Not indexed (4)");
  assert.ok(group, "the group row is listed");
  assert.equal(group.attrs["aria-expanded"], "false");
  assert.equal(group.attrs["aria-checked"], undefined, "no check state on a group that cannot be selected");
  assert.equal(t.names().includes("Text files (2)"), false);
  group.dispatch("click", { target: group.children[2] });
  assert.equal(t.panel.selected.size, 0, "clicking never selects a skipped source");
  assert.equal(t.byName("Not indexed (4)").attrs["aria-expanded"], "true");
  assert.deepEqual(t.names().slice(-4), ["Not indexed (4)", "Other logs (not selected) (1)", "Text files (2)", "Binary files (1)"]);
  const names = t.names();
  assert.equal(names.includes("Text files (2)"), true);
  assert.equal(names.includes("Binary files (1)"), true);
  assert.equal(names.includes("Other logs (not selected) (1)"), true);
});

function openAll(t) {
  for (const label of ["Not indexed (4)", "Text files (2)", ".config (2)"]) {
    const row = t.byName(label);
    row.dispatch("click", { target: row.children[2] });
  }
}

test("tree: a text file has View and opens the viewer with the row as the opener; a binary one has none", () => {
  const t = treeSetup();
  openAll(t);
  const config = t.rows().find((r) => r.children.find((c) => c.className === "st-name").textContent === "Root/Cfg/app.config");
  assert.ok(config);
  const view = find(config, "st-view");
  assert.equal(view.hidden, false);
  assert.equal(view.attrs["aria-label"], "View Root/Cfg/app.config");
  assert.equal(config.attrs["aria-checked"], undefined);
  config.dispatch("click", { target: view });
  assert.deepEqual(t.viewed.map((v) => v[0]), [3]);
  assert.equal(t.viewed[0][1], view, "Return goes to the button that was clicked");
  config.dispatch("keydown", { key: "Enter" });
  assert.deepEqual(t.viewed.map((v) => v[0]), [3, 3]);
  assert.equal(t.viewed[1][1], config, "the keyboard opens it from the row and the focus comes back to the row");
  assert.equal(t.panel.selected.size, 0);
  // Binary file: no View.
  const binary = ["Binary files (1)", "Performance counter log (.blg) (1)"];
  for (const label of binary) {
    const row = t.byName(label);
    row.dispatch("click", { target: row.children[2] });
  }
  const blg = t.rows().find((r) => r.children.find((c) => c.className === "st-name").textContent === "Root/Perf/perf.blg");
  assert.equal(find(blg, "st-view").hidden, true);
  blg.dispatch("keydown", { key: "Enter" });
  assert.equal(t.viewed.length, 2, "nothing to open for a binary file");
});

test("tree: the Reduced badge shows on a reduced source only, with the skipped range in its title", () => {
  const t = treeSetup();
  const big = t.byName("big.log");
  assert.equal(find(big, "badge-reduced").hidden, false);
  assert.equal(find(big, "badge-reduced").textContent, "Reduced");
  assert.match(find(big, "badge-reduced").title, /Skipped 600 MB in between \(2,400,000 lines, 2026-02-03 04:05:06\.000 to 2026-02-09 01:02:03\.000\)/);
  assert.equal(find(t.byName("small.log"), "badge-reduced").hidden, true);
});

test("tree: a filter finds a skipped file and opens the path to it", () => {
  const t = treeSetup();
  t.panel.query = "perf.blg";
  t.tree.update();
  assert.equal(t.names().includes("Root/Perf/perf.blg"), true);
  assert.equal(t.names().includes("big.log"), false);
});

// ---- the flat list and the viewer ---------------------------------------------------------------------------------

const make = (tag, init = {}) => Object.assign(doc.createElement(tag), init);

function listSetup() {
  const refs = {
    caseSelect: make("select"), newCaseForm: make("form"), newCaseName: make("input"), openForm: make("form"),
    pathInput: make("input"), scopeSelect: make("select", { value: "default" }), sourceList: make("ul"), filterInput: make("input"),
    capSelect: make("select", { value: "256" }),
  };
  doc.body.appendChild(refs.sourceList);
  doc.body.appendChild(refs.filterInput);
  const messages = [];
  const panel = new SourcesPanel(refs, { onCase() {}, onSources() {}, onSelection() {}, onDone() {}, onMessage: (t, e) => messages.push([t, e]), onProgress() {} });
  panel.caseId = "c1";
  const opened = [];
  panel.loadViewer = async () => ({ mountFileViewer: (deps) => ({ open: (caseId, source, from) => opened.push([caseId, source.id, from]), close() { opened.push("close"); } }) });
  return { refs, panel, opened, messages };
}

test("list: reduced rows get the badge; skipped sources are not rows but one closed group at the end", async () => {
  const t = listSetup();
  t.panel.sources = SOURCES;
  t.panel.renderSources();
  await until(() => t.refs.sourceList.children.some((c) => c.className === "ni-group"), "the group");
  const children = t.refs.sourceList.children;
  assert.equal(children.filter((c) => c.className !== "ni-group").length, 2, "two indexed rows");
  assert.equal(children[children.length - 1].className, "ni-group", "the group is last");
  const bigLabel = children[0].children[0];
  const badge = bigLabel.children.find((c) => c.className.includes("badge-reduced"));
  assert.equal(badge.textContent, "Reduced");
  assert.match(badge.title, /Indexed the first 1\.0 MB and the last 100 MB of 800 MB\./);
  const group = children[children.length - 1];
  assert.equal(group.children.length, 1, "closed: one row");
  assert.equal(group.children[0].children[0].attrs["aria-expanded"], "false");
  assert.equal(group.children[0].children[0].children[1].textContent, "Not indexed (4)");
});

test("list: opening the group and a folder shows the file with View; View opens the viewer for the case", async () => {
  const t = listSetup();
  t.panel.sources = SOURCES;
  t.panel.renderSources();
  await until(() => t.refs.sourceList.children.some((c) => c.className === "ni-group"), "the group");
  const toggleAt = (label) => {
    const group = t.refs.sourceList.children.find((c) => c.className === "ni-group");
    const button = group.all().find((n) => n.tagName === "BUTTON" && n.textContent === label);
    assert.ok(button, label);
    button.dispatch("click");
  };
  toggleAt("Not indexed (4)");
  toggleAt("Text files (2)");
  toggleAt(".config (2)");
  const group = t.refs.sourceList.children.find((c) => c.className === "ni-group");
  const views = group.all().filter((n) => n.className.includes("ni-view"));
  assert.equal(views.length, 2);
  assert.equal(views[0].attrs["aria-label"], "View Root/Cfg/app.config");
  views[0].focus();
  views[0].click();
  await tick();
  await until(() => t.opened.length === 1, "the viewer");
  assert.equal(t.opened[0][0], "c1");
  assert.equal(t.opened[0][1], 3);
  assert.equal(t.opened[0][2], views[0], "the focus goes back to the View button");
});

test("list: the group stays closed across a re-render and a switch of case drops it", async () => {
  const t = listSetup();
  t.panel.sources = SOURCES;
  t.panel.renderSources();
  await until(() => t.refs.sourceList.children.some((c) => c.className === "ni-group"), "the group");
  const first = t.refs.sourceList.children.find((c) => c.className === "ni-group");
  t.panel.renderSources();
  assert.equal(t.refs.sourceList.children.filter((c) => c.className === "ni-group").length, 1);
  assert.equal(t.refs.sourceList.children.find((c) => c.className === "ni-group"), first, "unchanged: not rebuilt");
  t.panel.sources = SOURCES.slice(0, 2);
  t.panel.renderSources();
  assert.equal(t.refs.sourceList.children.some((c) => c.className === "ni-group"), false, "no skipped source: no group");
});

test("a failed load of the viewer is reported and the next View tries again", async () => {
  const t = listSetup();
  let attempts = 0;
  t.panel.loadViewer = async () => {
    attempts += 1;
    if (attempts === 1) throw new Error("offline");
    return { mountFileViewer: () => ({ open: (c, s) => t.opened.push(s.id), close() {} }) };
  };
  await t.panel.viewSource(SOURCES[2], null);
  assert.equal(t.messages.length, 1);
  assert.equal(t.messages[0][1], true);
  assert.match(t.messages[0][0], /could not be loaded/);
  await t.panel.viewSource(SOURCES[2], null);
  assert.deepEqual(t.opened, [3]);
});

test("Large files: the select gives 256 MB, 1 GB or the whole file to the request; no select sends nothing", async () => {
  const t = listSetup();
  assert.equal(t.panel.currentCapMb(), 256);
  t.refs.capSelect.value = "1024";
  assert.equal(t.panel.currentCapMb(), 1024);
  t.refs.capSelect.value = "0";
  assert.equal(t.panel.currentCapMb(), 0);
  const seen = [];
  const original = api.addCollection;
  api.addCollection = (...args) => { seen.push(args); return Promise.resolve({ job_id: null }); };
  try {
    t.panel.runCollection("/some/path.zip", "default", "c1");
    await tick();
  } finally {
    api.addCollection = original;
  }
  assert.equal(seen.length, 1);
  assert.equal(seen[0][5], 0, "the sixth argument is the limit in MB");
  delete t.refs.capSelect;
  assert.equal(t.panel.currentCapMb(), undefined);
});
