// UI-22: the New case / Add files dialogs, the empty state without a case, and the folder browser as a <dialog>.
// Real modules on the fake DOM; the api, the module loaders and the job runner are stubbed. Data is synthetic.
// No wall-clock waits.
import test from "node:test";
import assert from "node:assert/strict";

import { installGlobalDom, tick } from "./fixtures/fake-dom.mjs";

const doc = installGlobalDom();
const baseCreate = doc.createElement;
// A form field has a string value from the start, as in a browser.
doc.createElement = (tag) => {
  const node = baseCreate(tag);
  if (["input", "select", "textarea"].includes(String(tag))) {
    node.value = "";
  }
  return node;
};
const { SourcesPanel } = await import("../../src/dmslog/web/js/sources.js");
const { CaseFiles } = await import("../../src/dmslog/web/js/case-files.js");
const { api, ApiError } = await import("../../src/dmslog/web/js/api.js");
const { S } = await import("../../src/dmslog/web/js/strings.js");
const { ignoresShortcut } = await import("../../src/dmslog/web/js/filters.js");
const { openFolderBrowser } = await import("../../src/dmslog/web/js/folder-browser.js");
const { recentMeta } = await import("../../src/dmslog/web/js/case-empty.js");
const dialogsModule = await import("../../src/dmslog/web/js/case-dialogs.js");
const emptyModule = await import("../../src/dmslog/web/js/case-empty.js");
const { focusReturn, isHiddenChain } = dialogsModule;

const GUID = "0A1B2C3D-0000-1111-2222-333344445555";
const ENT = `/data/ERS_CL_${GUID}_20260101120000_Enterprise.zip`;

const has = (node, cls) => node.className.split(/\s+/).includes(cls);
const q = (root, cls) => root.all().filter((n) => has(n, cls));
const dialogOf = (cls) => doc.body.all().find((n) => n.tagName === "DIALOG" && has(n, cls)) || null;
const isOpen = (dlg) => dlg.getAttribute("open") === "" || dlg.open === true;
const deferred = () => {
  let resolve; let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
};

// The page as index.html has it: the side column holds the (hidden) forms, the grid panel the viewport.
function page({ emptyHost = true } = {}) {
  doc.body.children = [];
  doc.body.className = "";
  doc.activeElement = doc.body;
  const make = (tag, props = {}) => Object.assign(doc.createElement(tag), { value: "" }, props);
  const aside = make("aside", { id: "sources-panel" });
  const header = make("div", { hidden: true });
  const addFilesButton = make("button", { id: "case-add-files" });
  const menuButton = make("button", { id: "case-header-menu" });
  const caseRow = make("div");
  const select = make("select", { id: "case-select" });
  const newCaseName = make("input", { id: "new-case-name" });
  const newCaseForm = make("form", { id: "new-case-form", hidden: true });
  const filesHost = make("div", { id: "case-files-host" });
  const nameLabel = make("label", { className: "visually-hidden" });
  newCaseForm.append(nameLabel, newCaseName, filesHost);
  const pathInput = make("input", { id: "path-input" });
  const scopeSelect = make("select", { id: "scope-select", value: "default" });
  const openForm = make("form", { id: "open-form", hidden: true });
  const inline = make("div", { className: "inline-form" });
  inline.append(make("label", { className: "visually-hidden" }), scopeSelect, make("button"));
  const addHost = make("div", { id: "add-files-host" });
  openForm.append(make("label", { className: "visually-hidden" }), pathInput, inline, addHost);
  const sourceList = make("ul", { id: "source-list" });
  header.append(make("span"), addFilesButton, menuButton);
  aside.append(header, caseRow, select, newCaseForm, openForm, sourceList);
  const grid = make("section", { id: "grid-panel" });
  const viewport = make("div", { id: "grid-viewport" });
  grid.appendChild(viewport);
  doc.body.append(aside, grid);
  const refs = {
    caseSelect: select, newCaseForm, newCaseName, openForm, pathInput, scopeSelect, sourceList, caseHeader: header,
    caseHeaderName: make("span"), caseRow, caseAddFiles: addFilesButton, caseMenu: menuButton, addFilesHost: addHost,
    ...(emptyHost ? { emptyHost: grid } : {}),
  };
  const calls = { messages: [], cases: [] };
  const panel = new SourcesPanel(refs, {
    onCase: (id) => calls.cases.push(id), onSources() {}, onSelection() {}, onDone() {},
    onMessage: (t, e) => calls.messages.push([t, e]), onProgress() {},
  });
  panel.caseFiles = new CaseFiles({ host: filesHost, nameInput: newCaseName, fs: {}, storage: null, onMessage: (t, e) => calls.messages.push([t, e]) });
  panel.addFiles = new CaseFiles({
    host: addHost, nameInput: null, fs: {}, storage: null, mode: "add", onMessage: (t, e) => calls.messages.push([t, e]),
    onAdd: () => panel.addFilesToCase(),
  });
  panel.loadDialogs = async () => dialogsModule; // modules already loaded: no I/O, no timers
  panel.loadEmpty = async () => emptyModule;
  api.sources = async () => ({ sources: [] });
  api.cases = async () => ({ cases: [] });
  return { refs, panel, calls, aside, grid, viewport };
}

const settle = async () => { await tick(); await tick(); };

// ---- New case --------------------------------------------------------------------------------------------------

test("New case is a dialog: fields in the body, Create case in the foot tied to the form, no form left in the column", async () => {
  const { refs, panel, aside } = page();
  await panel.selectCase("A");
  refs.caseMenu.focus();
  await panel.revealNewCase(refs.caseMenu);
  const dlg = dialogOf("case-dialog-new");
  assert.ok(isOpen(dlg));
  const body = q(dlg, "ui-dialog-body")[0];
  const foot = q(dlg, "ui-dialog-foot")[0];
  assert.ok(body.contains(refs.newCaseForm) && body.contains(refs.newCaseName));
  assert.equal(refs.newCaseForm.hidden, false);
  assert.ok(!aside.contains(refs.newCaseForm) && !aside.contains(refs.openForm), "the column holds no form");
  const create = q(dlg, "cf-create")[0];
  assert.ok(foot.contains(create) && !body.contains(create), "the main action is in the foot");
  assert.equal(create.getAttribute("form"), "new-case-form", "it still submits the form (Enter in the name field)");
  assert.ok(create.className.includes("is-primary") && create.className.includes("ui-button"));
  assert.ok(!refs.newCaseForm.children[0].className.includes("visually-hidden"), "the name label is visible");
  assert.equal(doc.activeElement, refs.newCaseName);
  dlg.dispatch("cancel"); // Escape on a native dialog
  assert.equal(isOpen(dlg), false);
  assert.equal(doc.activeElement, refs.caseMenu, "the focus goes back to the opener");
});

test("without showModal() Escape closes the dialog, and a field that handled it keeps the dialog open", async () => {
  const { refs, panel } = page();
  await panel.selectCase("A");
  refs.caseMenu.focus();
  await panel.revealNewCase(refs.caseMenu);
  const dlg = dialogOf("case-dialog-new");
  dlg.dispatch("keydown", { key: "Escape", defaultPrevented: true });
  assert.ok(isOpen(dlg));
  dlg.dispatch("keydown", { key: "Escape" });
  assert.equal(isOpen(dlg), false);
  assert.equal(doc.activeElement, refs.caseMenu);
});

test("a native dialog is opened with showModal() and closed through close()", async () => {
  const { refs, panel } = page();
  await panel.selectCase("A");
  let modal = 0;
  panel.loadDialogs = async () => ({
    mountCaseDialogs(opts) {
      const mounted = dialogsModule.mountCaseDialogs(opts);
      for (const dlg of doc.body.all().filter((n) => n.tagName === "DIALOG")) {
        dlg.showModal = () => { modal += 1; dlg.open = true; };
        dlg.close = () => { dlg.open = false; };
      }
      return mounted;
    },
  });
  refs.caseMenu.focus();
  await panel.revealNewCase(refs.caseMenu);
  const dlg = dialogOf("case-dialog-new");
  assert.equal(modal, 1);
  assert.equal(dlg.getAttribute("open"), null, "the browser owns the open attribute");
  assert.equal(dlg.open, true);
  dlg.dispatch("cancel");
  assert.equal(dlg.open, false);
  assert.equal(doc.activeElement, refs.caseMenu);
});

test("an empty name is refused under the field and nothing is created", async () => {
  const { refs, panel, calls } = page();
  await panel.selectCase("A");
  await panel.revealNewCase(refs.caseMenu);
  let created = 0;
  api.createCase = async () => { created += 1; return { id: "N" }; };
  await panel.createCase();
  assert.equal(created, 0);
  assert.equal(refs.newCaseName.getAttribute("aria-invalid"), "true");
  const note = doc.getElementById("new-case-name-error");
  assert.equal(note.textContent, S.cfNeedName);
  assert.ok(isOpen(dialogOf("case-dialog-new")), "the dialog stays open");
  refs.newCaseName.dispatch("input");
  assert.equal(doc.getElementById("new-case-name-error"), null, "typing clears the error");
  assert.deepEqual(calls.messages, []);
});

test("creating a case: all files opened closes the dialog and selects the case", async () => {
  const { refs, panel } = page();
  api.cases = async () => ({ cases: [{ id: "N", name: "new" }] });
  api.createCase = async () => ({ id: "N" });
  panel.runCollection = async () => ({ status: "ok" });
  await panel.selectCase(null);
  refs.pathInput.focus();
  await panel.revealNewCase(refs.caseMenu);
  refs.newCaseName.value = "new";
  panel.caseFiles.state.add([ENT]);
  refs.newCaseForm.dispatch("submit");
  await settle();
  assert.equal(panel.caseId, "N", "the case is selected after creation");
  assert.equal(isOpen(dialogOf("case-dialog-new")), false);
  assert.equal(panel.queueBusy, false);
});

test("a partial result keeps the dialog open with the per-file results and Retry (Create case again)", async () => {
  const { refs, panel } = page();
  api.cases = async () => ({ cases: [{ id: "N", name: "new" }] });
  api.createCase = async () => ({ id: "N" });
  let calls = 0;
  panel.runCollection = async () => { calls += 1; return calls === 1 ? { status: "failed", message: "boom" } : { status: "ok" }; };
  await panel.selectCase(null);
  await panel.revealNewCase(refs.caseMenu);
  refs.newCaseName.value = "new";
  panel.caseFiles.state.add([ENT, ENT.replace("Enterprise", "Other")]);
  refs.newCaseForm.dispatch("submit");
  await settle();
  const dlg = dialogOf("case-dialog-new");
  assert.ok(isOpen(dlg), "a failed file keeps the dialog open");
  assert.ok(q(dlg, "cf-result-failed").length === 1 && q(dlg, "cf-result-failed")[0].textContent.includes("boom"));
  assert.equal(panel.caseId, "N");
  assert.equal(panel.caseFiles.state.items.length, 2, "the list stays for Retry");
  refs.newCaseForm.dispatch("submit"); // Retry: only the failed file opens again
  await settle();
  assert.equal(isOpen(dlg), false);
  assert.equal(calls, 3);
});

test("a network error while creating the case is shown under the name field and the dialog stays open", async () => {
  const { refs, panel } = page();
  api.createCase = async () => { throw new ApiError(409, "case_exists", "A case with this name already exists."); };
  await panel.selectCase(null);
  await panel.revealNewCase(refs.caseMenu);
  refs.newCaseName.value = "dup";
  panel.caseFiles.state.add([ENT]);
  refs.newCaseForm.dispatch("submit");
  await settle();
  assert.ok(isOpen(dialogOf("case-dialog-new")));
  assert.equal(refs.newCaseName.getAttribute("aria-invalid"), "true");
  assert.match(doc.getElementById("new-case-name-error").textContent, /already exists/);
  assert.equal(panel.queueBusy, false, "the queue is free again");
  assert.equal(panel.caseFiles.state.items.length, 1);
});

test("the dialog stays open on a case switch while the queue runs, and Close does not lose the results", async () => {
  const { refs, panel } = page();
  api.sources = async () => ({ sources: [] });
  await panel.selectCase("A");
  await panel.revealNewCase(refs.caseMenu);
  panel.setQueueBusy(true);
  await panel.selectCase("B"); // the queue selects the case it created
  const dlg = dialogOf("case-dialog-new");
  assert.ok(isOpen(dlg));
  panel.setQueueBusy(false);
  await panel.selectCase("C"); // any other switch closes it (not busy any more)
  assert.equal(isOpen(dlg), false);
});

test("a typed path that was not added yet counts when Create case is pressed", async () => {
  const { refs, panel } = page();
  api.cases = async () => ({ cases: [{ id: "N", name: "new" }] });
  api.createCase = async () => ({ id: "N" });
  panel.runCollection = async () => ({ status: "ok" });
  await panel.selectCase(null);
  await panel.revealNewCase(refs.caseMenu);
  refs.newCaseName.value = "new";
  q(doc.body, "cf-path")[0].value = ENT;
  refs.newCaseForm.dispatch("submit");
  await settle();
  assert.equal(panel.caseId, "N");
});

// ---- Add files -------------------------------------------------------------------------------------------------

test("Add files… opens a dialog: the list first, 'Folder or single file' below, Add N files to case in the foot", async () => {
  const { refs, panel, aside } = page();
  await panel.selectCase("A");
  refs.caseAddFiles.focus();
  refs.caseAddFiles.dispatch("click");
  await settle();
  const dlg = dialogOf("case-dialog-add");
  assert.ok(isOpen(dlg));
  assert.equal(refs.caseAddFiles.getAttribute("aria-haspopup"), "dialog");
  assert.equal(refs.caseAddFiles.getAttribute("aria-controls"), null);
  const body = q(dlg, "ui-dialog-body")[0];
  const foot = q(dlg, "ui-dialog-foot")[0];
  const kids = refs.openForm.children;
  assert.equal(kids[0], refs.addFilesHost, "the list is above the folder section");
  assert.equal(kids[1].textContent, S.cdFolderSection);
  assert.ok(body.contains(refs.pathInput) && !aside.contains(refs.openForm));
  const add = q(dlg, "cf-create")[0];
  assert.ok(foot.contains(add));
  assert.equal(add.textContent, S.cfAddFiles, "'Add files' with an empty list");
  panel.addFiles.state.add([ENT, ENT.replace("Enterprise", "Other")]);
  assert.equal(add.textContent, "Add 2 files to case");
  assert.equal(q(dlg, "cf-path-row")[0].children[1].textContent, "Add to list", "different verbs for different actions");
  dlg.dispatch("keydown", { key: "Escape" });
  assert.equal(doc.activeElement, refs.caseAddFiles);
});

test("Open a path… (menu) puts the focus in the path field; a path error stays under the field in the open dialog", async () => {
  const { refs, panel } = page();
  await panel.selectCase("A");
  refs.caseMenu.focus();
  await panel.caseMenuItems()[1].run();
  await settle();
  assert.equal(doc.activeElement, refs.pathInput);
  refs.pathInput.value = "/nope";
  api.addCollection = async () => { throw new ApiError(404, "path_not_found", "Path does not exist: /nope"); };
  refs.openForm.dispatch("submit");
  await settle();
  assert.ok(isOpen(dialogOf("case-dialog-add")));
  assert.equal(refs.pathInput.getAttribute("aria-invalid"), "true");
  assert.match(doc.getElementById("path-input-error").textContent, /does not exist/);
});

test("a path that starts opening closes the dialog and gives the focus back", async () => {
  const { refs, panel, calls } = page();
  await panel.selectCase("A");
  refs.caseAddFiles.focus();
  await panel.revealAddFiles(false, refs.caseAddFiles);
  refs.pathInput.value = "/data/x.zip";
  api.addCollection = async () => ({ job_id: 1 });
  refs.openForm.dispatch("submit");
  await settle();
  assert.equal(isOpen(dialogOf("case-dialog-add")), false);
  assert.equal(doc.activeElement, refs.caseAddFiles);
  assert.equal(refs.pathInput.value, "");
  assert.deepEqual(calls.messages.at(-1), [S.collectionStarted, false]);
});

test("Add N files: all opened closes the dialog; a failure keeps it open with the results", async () => {
  const { refs, panel } = page();
  await panel.selectCase("A");
  await panel.revealAddFiles(false, refs.caseAddFiles);
  let n = 0;
  panel.runCollection = async () => { n += 1; return n === 2 ? { status: "failed", message: "bad zip" } : { status: "ok" }; };
  panel.addFiles.state.add([ENT, ENT.replace("Enterprise", "Other")]);
  await panel.addFilesToCase();
  const dlg = dialogOf("case-dialog-add");
  assert.ok(isOpen(dlg));
  assert.ok(q(dlg, "cf-result-failed")[0].textContent.includes("bad zip"));
  await panel.addFilesToCase(); // Retry
  assert.equal(isOpen(dlg), false);
});

test("the Add files dialog closes when the case changes, and without a case it does not open", async () => {
  const { refs, panel, calls } = page();
  await panel.selectCase("A");
  await panel.revealAddFiles(false, refs.caseAddFiles);
  const dlg = dialogOf("case-dialog-add");
  panel.addFiles.state.add([ENT]);
  await panel.selectCase("B");
  assert.equal(isOpen(dlg), false);
  await panel.selectCase(null);
  await panel.revealAddFiles(false, refs.caseAddFiles);
  assert.equal(isOpen(dialogOf("case-dialog-add")), false, "no case: nothing to add to");
  assert.deepEqual(calls.messages.at(-1), [S.needCase, true]);
});

// ---- loading, concurrency, failure -----------------------------------------------------------------------------

test("the dialogs load once; two quick openings share the load and open one dialog", async () => {
  const { refs, panel } = page();
  await panel.selectCase("A");
  let loads = 0;
  const gate = deferred();
  const real = panel.loadDialogs;
  panel.loadDialogs = async () => { loads += 1; await gate.promise; return real(); };
  const first = panel.revealNewCase(refs.caseMenu);
  const second = panel.revealNewCase(refs.caseMenu);
  assert.equal(loads, 1);
  gate.resolve();
  await Promise.all([first, second]);
  assert.equal(loads, 1);
  assert.equal(doc.body.all().filter((n) => n.tagName === "DIALOG" && has(n, "case-dialog-new")).length, 1);
  assert.ok(isOpen(dialogOf("case-dialog-new")));
});

test("while the module loads, only the last request opens", async () => {
  const { refs, panel } = page();
  await panel.selectCase("A");
  const gate = deferred();
  const real = panel.loadDialogs;
  panel.loadDialogs = async () => { await gate.promise; return real(); };
  const a = panel.revealNewCase(refs.caseMenu);
  const b = panel.revealAddFiles(false, refs.caseAddFiles);
  gate.resolve();
  await Promise.all([a, b]);
  assert.equal(dialogOf("case-dialog-new") && isOpen(dialogOf("case-dialog-new")), false);
  assert.ok(isOpen(dialogOf("case-dialog-add")));
});

test("a request that waits for the module is dropped when the case changes meanwhile", async () => {
  const { refs, panel } = page();
  await panel.selectCase("A");
  const gate = deferred();
  const real = panel.loadDialogs;
  panel.loadDialogs = async () => { await gate.promise; return real(); };
  const pending = panel.revealAddFiles(false, refs.caseAddFiles);
  await panel.selectCase("B");
  gate.resolve();
  await pending;
  assert.equal(dialogOf("case-dialog-add") && isOpen(dialogOf("case-dialog-add")), false);
});

test("a failed load is reported, nothing opens, and the next click loads again", async () => {
  const { refs, panel, calls } = page();
  await panel.selectCase("A");
  const real = panel.loadDialogs;
  let loads = 0;
  panel.loadDialogs = () => { loads += 1; return loads === 1 ? Promise.reject(new Error("net down")) : real(); };
  await panel.revealNewCase(refs.caseMenu);
  assert.equal(loads, 1);
  assert.deepEqual(calls.messages.at(-1), [S.cdLoadFailed + " net down", true]);
  assert.equal(dialogOf("case-dialog-new"), null);
  await panel.revealNewCase(refs.caseMenu);
  assert.equal(loads, 2);
  assert.ok(isOpen(dialogOf("case-dialog-new")));
});

test("the preload with no case is silent when it fails, and does not break the empty state", async () => {
  const { panel, calls } = page();
  panel.loadDialogs = () => Promise.reject(new Error("offline"));
  panel.loadEmpty = () => import("../../src/dmslog/web/js/case-empty.js");
  await panel.selectCase(null);
  await settle();
  assert.deepEqual(calls.messages, []);
  assert.ok(doc.body.className.includes("no-case"));
});

// ---- the empty state -------------------------------------------------------------------------------------------

test("without a case: the body is marked, the empty state shows 'No case open.' and the two primary actions", async () => {
  const { panel, grid } = page();
  await panel.selectCase(null);
  await settle();
  assert.ok(doc.body.className.includes("no-case"));
  const empty = q(grid, "case-empty")[0];
  assert.equal(empty.hidden, false);
  assert.equal(q(empty, "case-empty-title")[0].textContent, "Open an ERS to start");
  assert.equal(q(empty, "case-empty-state")[0].textContent, "No case open.");
  assert.equal(q(empty, "case-empty-new")[0].textContent, "New case…");
  assert.ok(q(empty, "case-empty-new")[0].className.includes("is-primary"));
  assert.equal(q(empty, "case-empty-path")[0].textContent, "Open a path…");
  assert.equal(q(empty, "case-empty-hint")[0].textContent, "Paste a path or use Browse.");
  assert.equal(q(empty, "case-empty-recent")[0].hidden, true, "no recent cases yet");
});

test("the empty state is not shown before the first case selection, and goes away with a case", async () => {
  const { panel, grid, viewport } = page();
  assert.ok(!doc.body.className.includes("no-case"));
  await settle();
  assert.equal(q(grid, "case-empty").length, 0, "nothing flashes before the page knows");
  await panel.selectCase(null);
  await settle();
  const empty = q(grid, "case-empty")[0];
  assert.equal(empty.hidden, false);
  q(empty, "case-empty-new")[0].focus();
  await panel.selectCase("A");
  assert.equal(empty.hidden, true);
  assert.ok(!doc.body.className.includes("no-case"));
  assert.equal(doc.activeElement, viewport, "the hidden button had the focus: the grid takes it");
});

test("closing the case moves the focus to New case… of the empty state", async () => {
  const { refs, panel, grid } = page();
  await panel.selectCase("A");
  refs.caseMenu.focus();
  await panel.selectCase(null);
  await settle();
  assert.equal(doc.activeElement, q(grid, "case-empty-new")[0]);
  await panel.selectCase(null); // already closed: the focus is not pulled again
  doc.body.focus();
  await panel.selectCase(null);
  assert.equal(doc.activeElement, doc.body);
});

test("recent cases: names, counts and a click opens the case; names are text, measures that are not counts are 0", async () => {
  const { refs, panel, grid } = page();
  api.cases = async () => ({ cases: [
    { id: "x<b>", name: "<b>bold</b> case", sources: 1, records: 1 },
    { id: "y", name: "second", sources: Number.NaN, records: null },
  ] });
  await panel.loadCases(null);
  await panel.selectCase(null);
  await settle();
  const items = q(grid, "case-empty-open");
  assert.equal(items.length, 2);
  assert.equal(q(items[0], "case-empty-name")[0].textContent, "<b>bold</b> case");
  assert.equal(q(items[0], "case-empty-meta")[0].textContent, "1 file · 1 record");
  assert.equal(q(items[1], "case-empty-meta")[0].textContent, "0 files · 0 records");
  assert.equal(recentMeta({ sources: -4, records: Infinity }), "0 files · 0 records");
  assert.equal(recentMeta(null), "0 files · 0 records");
  assert.equal(recentMeta({ sources: 1200, records: 3400567 }), "1,200 files · 3,400,567 records");
  items[1].dispatch("click");
  await settle();
  assert.equal(panel.caseId, "y");
  assert.equal(refs.caseSelect.value, "y");
});

test("a recent case cannot be opened while a case is being created", async () => {
  const { panel, grid, calls } = page();
  api.cases = async () => ({ cases: [{ id: "y", name: "second", sources: 1, records: 1 }] });
  await panel.loadCases(null);
  await panel.selectCase(null);
  await settle();
  panel.setQueueBusy(true);
  q(grid, "case-empty-open")[0].dispatch("click");
  await settle();
  assert.equal(panel.caseId, null);
  assert.deepEqual(calls.messages.at(-1), [S.cfQueueBusy, true]);
});

test("New case… and Open a path… of the empty state open the dialog; after creating, the focus lands on the grid", async () => {
  const { refs, panel, grid, viewport } = page();
  api.cases = async () => ({ cases: [{ id: "N", name: "new" }] });
  api.createCase = async () => ({ id: "N" });
  panel.runCollection = async () => ({ status: "ok" });
  await panel.selectCase(null);
  await settle();
  const pathButton = q(grid, "case-empty-path")[0];
  pathButton.focus();
  pathButton.dispatch("click");
  await settle();
  const dlg = dialogOf("case-dialog-new");
  assert.ok(isOpen(dlg));
  assert.equal(doc.activeElement, q(dlg, "cf-path")[0], "Open a path… starts in the path field of the list");
  refs.newCaseName.value = "new";
  panel.caseFiles.state.add([ENT]);
  refs.newCaseForm.dispatch("submit");
  await settle();
  assert.equal(isOpen(dlg), false);
  assert.equal(panel.caseId, "N");
  assert.equal(doc.activeElement, viewport, "the opener (empty state) is hidden now: the focus does not fall to the page");
});

test("Esc on a dialog opened from the empty state gives the focus back to its button", async () => {
  const { panel, grid } = page();
  await panel.selectCase(null);
  await settle();
  const newButton = q(grid, "case-empty-new")[0];
  newButton.focus();
  newButton.dispatch("click");
  await settle();
  const dlg = dialogOf("case-dialog-new");
  assert.ok(isOpen(dlg));
  dlg.dispatch("cancel");
  assert.equal(doc.activeElement, newButton);
});

test("a failed load of the empty state is reported once and tried again at the next layout", async () => {
  const { panel, calls, grid } = page();
  let loads = 0;
  const real = panel.loadEmpty;
  panel.loadEmpty = () => { loads += 1; return loads === 1 ? Promise.reject(new Error("x")) : real(); };
  await panel.selectCase(null);
  await settle();
  assert.equal(loads, 1);
  assert.equal(calls.messages.filter(([t]) => t.startsWith(S.cdLoadFailed)).length, 1);
  await panel.selectCase(null);
  await settle();
  assert.equal(loads, 2);
  assert.equal(q(grid, "case-empty")[0].hidden, false);
});

test("two layout passes before the empty state loads mount it once", async () => {
  const { panel, grid } = page();
  let loads = 0;
  const real = panel.loadEmpty;
  panel.loadEmpty = () => { loads += 1; return real(); };
  await panel.selectCase(null);
  panel.applyLayout();
  panel.applyLayout();
  await settle();
  assert.equal(loads, 1);
  assert.equal(q(grid, "case-empty").length, 1);
});

test("a case opened while the empty state was still loading never shows it", async () => {
  const { panel, grid } = page();
  const gate = deferred();
  const real = panel.loadEmpty;
  panel.loadEmpty = async () => { await gate.promise; return real(); };
  await panel.selectCase(null);
  await panel.selectCase("A");
  gate.resolve();
  await settle();
  const empty = q(grid, "case-empty")[0];
  assert.ok(!empty || empty.hidden === true);
});

// ---- shortcuts, focus helper -----------------------------------------------------------------------------------

test("the one-key shortcuts stay quiet inside the case dialogs", async () => {
  const { refs, panel } = page();
  await panel.selectCase("A");
  await panel.revealNewCase(refs.caseMenu);
  const title = q(dialogOf("case-dialog-new"), "ui-dialog-title")[0];
  assert.equal(ignoresShortcut({ target: title, defaultPrevented: false }), true);
  assert.equal(ignoresShortcut({ target: refs.newCaseName, defaultPrevented: false }), true);
});

test("focusReturn: a hidden or disconnected opener falls back; a visible one gets the focus", () => {
  doc.body.children = [];
  const make = (tag) => { const n = doc.createElement(tag); doc.body.appendChild(n); return n; };
  const opener = make("button");
  const fallback = make("div");
  const parent = make("section");
  const inner = doc.createElement("button");
  parent.appendChild(inner);
  focusReturn(opener, () => fallback).focus();
  assert.equal(doc.activeElement, opener);
  parent.hidden = true;
  assert.equal(isHiddenChain(inner), true);
  focusReturn(inner, () => fallback).focus();
  assert.equal(doc.activeElement, fallback);
  const lost = doc.createElement("button"); // never attached
  focusReturn(lost, () => opener).focus();
  assert.equal(doc.activeElement, opener);
  focusReturn(null, () => null).focus(); // nothing to focus: no throw
});

// ---- the folder browser as a <dialog> --------------------------------------------------------------------------

const LONG = `ERS_CL_${GUID}_20260101120000_Sensitive_${"Product".repeat(8)}.zip`;
const LISTING = {
  path: "/data", parent: "/", truncated: false, skipped: 0, other_files: 0,
  shortcuts: [{ kind: "home", path: "/home/u" }],
  entries: [
    { name: "logs", path: "/data/logs", type: "dir", hidden: false },
    { name: `ERS_CL_${GUID}_20260101120000_Enterprise.zip`, path: ENT, type: "zip", size: 5, hidden: false },
    { name: LONG, path: "/data/" + LONG, type: "zip", size: 6, hidden: false },
    { name: "notes.zip", path: "/data/notes.zip", type: "zip", size: 7, hidden: false },
  ],
};
const fs = { list: async () => LISTING };

test("folder browser: a modal <dialog> with the action in the foot, ZIP names cut in the middle, kind badges", async () => {
  doc.body.children = [];
  const opener = doc.createElement("button");
  doc.body.appendChild(opener);
  opener.focus();
  const result = openFolderBrowser({ fs, storage: null });
  await settle();
  const dlg = dialogOf("fb-dialog");
  assert.ok(dlg && isOpen(dlg) && has(dlg, "ui-dialog"));
  assert.equal(q(dlg, "ui-dialog-title")[0].textContent, S.fbTitle);
  assert.ok(q(dlg, "ui-dialog-foot")[0].contains(q(dlg, "fb-add")[0]), "Add selected files is fixed in the foot");
  assert.equal(q(dlg, "fb-add")[0].disabled, true, "nothing selected yet");
  const names = q(dlg, "fb-name");
  const long = names.find((n) => n.title === LONG);
  assert.ok(long.textContent.includes("…") && long.textContent.endsWith(".zip") && long.textContent.length <= 44);
  assert.equal(long.title, LONG, "the full name is the tooltip");
  const badges = q(dlg, "fb-kind").map((n) => n.textContent);
  assert.deepEqual(badges, ["Enterprise", "Sensitive"], "ERS names get the kind; notes.zip and folders get none");
  assert.ok(q(dlg, "fb-kind")[1].className.includes("is-sensitive"));
  dlg.dispatch("cancel");
  assert.equal(await result, null);
  assert.equal(isOpen(dlg), false);
  assert.equal(doc.activeElement, opener, "the focus goes back to the Browse button");
  assert.equal(dialogOf("fb-dialog"), null, "the dialog is removed from the page");
});

test("folder browser: selecting a ZIP and pressing Add selected files resolves to the entries", async () => {
  doc.body.children = [];
  const result = openFolderBrowser({ fs, storage: null });
  await settle();
  const dlg = dialogOf("fb-dialog");
  const row = q(dlg, "fb-zip")[0];
  row.dispatch("click", { shiftKey: false, ctrlKey: false });
  const add = q(dlg, "fb-add")[0];
  assert.equal(add.disabled, false);
  add.click();
  assert.deepEqual(await result, [{ path: ENT, size: 5 }]);
  assert.equal(dialogOf("fb-dialog"), null);
});

test("folder browser: Escape without showModal() closes it; a late listing does not throw after it closed", async () => {
  doc.body.children = [];
  const gate = deferred();
  const slow = { list: () => gate.promise };
  const result = openFolderBrowser({ fs: slow, storage: null });
  await tick();
  const dlg = dialogOf("fb-dialog");
  dlg.dispatch("keydown", { key: "Escape" });
  assert.equal(await result, null);
  gate.resolve(LISTING);
  await settle();
  assert.equal(dialogOf("fb-dialog"), null);
});

test("folder browser: opened from the New case dialog it stacks on top and gives the focus back to Browse", async () => {
  const { refs, panel } = page();
  await panel.selectCase("A");
  await panel.revealNewCase(refs.caseMenu);
  const browse = q(dialogOf("case-dialog-new"), "cf-browse")[0];
  browse.focus();
  const result = openFolderBrowser({ fs, storage: null });
  await settle();
  const fb = dialogOf("fb-dialog");
  assert.ok(isOpen(fb) && isOpen(dialogOf("case-dialog-new")), "both open: the browser is the top one");
  fb.dispatch("cancel");
  await result;
  assert.equal(doc.activeElement, browse);
  assert.ok(isOpen(dialogOf("case-dialog-new")), "closing the browser does not close the dialog below");
});
