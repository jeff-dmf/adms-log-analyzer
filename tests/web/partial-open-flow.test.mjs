// M9 9.9: "Open only part first" in the Add files dialog, the two-stage progress, first_ready, done notes, and the
// Resume indexing offer. Real modules on the fake DOM; the api, the module loaders and the progress stream are stubbed.
// Data is synthetic. No wall-clock waits.
import test from "node:test";
import assert from "node:assert/strict";

import { installGlobalDom, tick } from "./fixtures/fake-dom.mjs";

const doc = installGlobalDom();
const baseCreate = doc.createElement;
doc.createElement = (tag) => {
  const node = baseCreate(tag);
  if (["input", "select", "textarea"].includes(String(tag))) {
    node.value = "";
  }
  return node;
};
const { SourcesPanel, stagedIndexingText, unmatchedText } = await import("../../src/dmslog/web/js/sources.js");
const { CaseFiles } = await import("../../src/dmslog/web/js/case-files.js");
const { api, ApiError } = await import("../../src/dmslog/web/js/api.js");
const { S } = await import("../../src/dmslog/web/js/strings.js");
const dialogsModule = await import("../../src/dmslog/web/js/case-dialogs.js");
const partialModule = await import("../../src/dmslog/web/js/partial-open.js");
const resumeModule = await import("../../src/dmslog/web/js/resume-banner.js");

const GUID = "0A1B2C3D-0000-1111-2222-333344445555";
const ENT = `/data/ERS_CL_${GUID}_20261009143000_Enterprise.zip`;
const SENS = `/data/ERS_CL_${GUID}_20261009143000_Sensitive_Enterprise.zip`;
const T = (text) => Date.parse(text + "Z");

const has = (node, cls) => node.className.split(/\s+/).includes(cls);
const q = (root, cls) => root.all().filter((n) => has(n, cls));
const dialogOf = (cls) => doc.body.all().find((n) => n.tagName === "DIALOG" && has(n, cls)) || null;
const isOpen = (dlg) => dlg.getAttribute("open") === "" || dlg.open === true;
const deferred = () => {
  let resolve; let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
};
const settle = async () => { await tick(); await tick(); await tick(); };

class FakeEventSource {
  static instances = [];
  constructor(url) {
    this.url = url;
    this.listeners = {};
    this.readyState = 1;
    FakeEventSource.instances.push(this);
  }
  addEventListener(type, fn) { this.listeners[type] = fn; }
  close() { this.closed = true; }
  emit(type, data) { return this.listeners[type]({ data: JSON.stringify(data) }); }
}
const live = () => FakeEventSource.instances.filter((s) => !s.closed).at(-1);

function page() {
  doc.body.children = [];
  doc.body.className = "";
  doc.activeElement = doc.body;
  FakeEventSource.instances = [];
  globalThis.EventSource = FakeEventSource;
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
  newCaseForm.append(make("label"), newCaseName, filesHost);
  const pathInput = make("input", { id: "path-input" });
  const scopeSelect = make("select", { id: "scope-select", value: "default" });
  const openForm = make("form", { id: "open-form", hidden: true });
  const inline = make("div", { className: "inline-form" });
  inline.append(make("label"), scopeSelect, make("button"));
  const addHost = make("div", { id: "add-files-host" });
  openForm.append(make("label"), pathInput, inline, addHost);
  const sourceList = make("ul", { id: "source-list" });
  header.append(make("span"), addFilesButton, menuButton);
  aside.append(header, caseRow, select, newCaseForm, openForm, sourceList);
  const grid = make("section", { id: "grid-panel" });
  grid.appendChild(make("div", { id: "grid-viewport" }));
  doc.body.append(aside, grid);
  const refs = {
    caseSelect: select, newCaseForm, newCaseName, openForm, pathInput, scopeSelect, sourceList, caseHeader: header,
    caseHeaderName: make("span"), caseRow, caseAddFiles: addFilesButton, caseMenu: menuButton, addFilesHost: addHost,
  };
  const calls = { messages: [], first: [], done: 0, progress: [] };
  const panel = new SourcesPanel(refs, {
    onCase() {}, onSources() {}, onSelection() {}, onDone() { calls.done += 1; },
    onFirstReady: (info) => { calls.first.push(info); },
    onMessage: (t, e) => calls.messages.push([t, e]),
    onProgress: (value) => calls.progress.push(value),
  });
  panel.caseFiles = new CaseFiles({ host: filesHost, nameInput: newCaseName, fs: {}, storage: null, onMessage: () => {} });
  panel.addFiles = new CaseFiles({
    host: addHost, nameInput: null, fs: {}, storage: null, mode: "add", onMessage: (t, e) => calls.messages.push([t, e]),
    onAdd: () => panel.addFilesToCase(),
  });
  const loads = { partial: 0, resume: 0 };
  panel.loadDialogs = async () => dialogsModule;
  panel.loadPartial = async () => { loads.partial += 1; return partialModule; };
  panel.loadResume = async () => { loads.resume += 1; return resumeModule; };
  api.sources = async () => ({ sources: [] });
  api.cases = async () => ({ cases: [] });
  api.progressUrl = () => "/progress";
  api.partial.pending = async () => ({ case: "A", running: false, pending: [] });
  return { refs, panel, calls, aside, loads };
}

// Opens the Add files dialog of case A with the option turned on and its body mounted.
async function openAdd(ctx, { on = true } = {}) {
  await ctx.panel.selectCase("A");
  await ctx.panel.revealAddFiles(false, ctx.refs.caseAddFiles);
  const dlg = dialogOf("case-dialog-add");
  const toggle = q(dlg, "po-toggle-box")[0];
  if (on) {
    toggle.checked = true;
    toggle.dispatch("change");
    await settle();
  }
  return { dlg, toggle };
}

const field = (dlg, key) => q(dlg, "po-time")[key === "from" ? 0 : 1];
const errorOf = (input) => doc.getElementById(input.id + "-error");
const fill = (dlg, from, to) => { field(dlg, "from").value = from; field(dlg, "to").value = to; };
const previewButton = (dlg) => q(dlg, "po-actions")[0].children[0];

const planOf = (list) => ({ sources: list.map((s, i) => ({
  member: "m" + i, name: "n" + i, family: "Wfm", process: "p", pid: null, sensitive: false, rotation: null, size: 2048,
  selected: true, indexable: true, first_ts: T("2026-10-09T10:00:00"), last_ts: T("2026-10-09T12:00:00"), ...s,
})) });

// ---- the option ------------------------------------------------------------------------------------------------

test("the option is off and its module is not loaded until it is turned on", async () => {
  const ctx = page();
  const { dlg, toggle } = await openAdd(ctx, { on: false });
  assert.equal(toggle.checked, false);
  assert.equal(ctx.loads.partial, 0, "nothing is loaded for a user who never opens the section");
  const body = q(dlg, "po-body")[0];
  assert.equal(body.hidden, true);
  assert.equal(toggle.getAttribute("aria-expanded"), "false");
  assert.equal(toggle.getAttribute("aria-controls"), body.id);
  toggle.checked = true;
  toggle.dispatch("change");
  await settle();
  assert.equal(ctx.loads.partial, 1);
  assert.equal(body.hidden, false);
  assert.equal(toggle.getAttribute("aria-expanded"), "true");
  assert.equal(q(dlg, "po-time").length, 2);
  toggle.checked = false;
  toggle.dispatch("change");
  toggle.checked = true;
  toggle.dispatch("change");
  await settle();
  assert.equal(ctx.loads.partial, 1, "turning it off and on again does not load the module again");
});

test("a failed load of the options shows Try again; the next try imports again and the dialog keeps working", async () => {
  const ctx = page();
  let n = 0;
  ctx.panel.loadPartial = async () => { n += 1; if (n <= 2) { throw new Error("chunk failed"); } return partialModule; };
  const { dlg } = await openAdd(ctx);
  const note = q(dlg, "po-load-note")[0];
  const retry = q(dlg, "po-retry")[0];
  assert.equal(note.textContent, S.poLoadFailed);
  assert.equal(retry.hidden, false);
  // Add with a failed load does not silently open everything.
  api.addCollection = async () => { throw new Error("must not be called"); };
  ctx.panel.refs.pathInput.value = "/data/x";
  ctx.refs.openForm.dispatch("submit");
  await settle();
  assert.deepEqual(ctx.calls.messages.at(-1), [S.poNotReady, true]);
  assert.equal(n, 2, "pressing Add tried to load the options again");
  assert.ok(isOpen(dlg));
  retry.focus();
  retry.dispatch("click");
  await settle();
  assert.equal(n, 3);
  assert.equal(retry.hidden, true);
  assert.equal(note.textContent, "");
  assert.equal(q(dlg, "po-time").length, 2);
  assert.equal(doc.activeElement, field(dlg, "from"), "the focus leaves the hidden Try again button for the first field");
});

// ---- the time window -------------------------------------------------------------------------------------------

test("a bad start or an end before the start is refused next to the field, with focus, before anything is sent", async () => {
  const ctx = page();
  const { dlg } = await openAdd(ctx);
  let sent = 0;
  api.addCollection = async () => { sent += 1; return { job_id: 1 }; };
  ctx.refs.pathInput.value = "/data/x";
  fill(dlg, "soon", "");
  ctx.refs.openForm.dispatch("submit");
  await settle();
  assert.equal(sent, 0);
  assert.equal(field(dlg, "from").getAttribute("aria-invalid"), "true");
  assert.equal(errorOf(field(dlg, "from")).textContent, S.poBadTime);
  assert.equal(doc.activeElement, field(dlg, "from"));
  assert.deepEqual(ctx.calls.messages.at(-1), [S.poBadTime, true]);
  fill(dlg, "2026-10-09 12:00:00", "2026-10-09 11:00:00");
  ctx.refs.openForm.dispatch("submit");
  await settle();
  assert.equal(sent, 0);
  assert.equal(field(dlg, "from").getAttribute("aria-invalid"), null, "the start is fine now");
  assert.equal(errorOf(field(dlg, "to")).textContent, S.poOrder);
  assert.equal(doc.activeElement, field(dlg, "to"));
  field(dlg, "to").dispatch("input");
  assert.equal(errorOf(field(dlg, "to")), null, "typing clears the error");
  assert.ok(isOpen(dlg), "the dialog stays open");
});

test("a window goes to POST collections as log-clock ms; the typed path opens, the dialog closes and the option resets", async () => {
  const ctx = page();
  const { dlg, toggle } = await openAdd(ctx);
  let args = null;
  api.addCollection = async (...a) => { args = a; return { job_id: 7 }; };
  ctx.refs.pathInput.value = "/data/x.zip";
  fill(dlg, "2026-10-09 10:00:00", "2026-10-09 10:30:00.500");
  ctx.refs.openForm.dispatch("submit");
  await settle();
  assert.deepEqual(args, ["A", "/data/x.zip", "default", { from: T("2026-10-09T10:00:00"), to: T("2026-10-09T10:30:00.500") }]);
  assert.equal(isOpen(dlg), false);
  assert.equal(toggle.checked, false, "the option is off again for the next opening");
  assert.equal(field(dlg, "from").value, "");
  assert.equal(FakeEventSource.instances.length, 1, "the progress is watched");
});

test("only an end, or only a start, is a window; with the option off nothing narrows", async () => {
  const ctx = page();
  const { dlg, toggle } = await openAdd(ctx);
  const seen = [];
  api.addCollection = async (...a) => { seen.push(a[3]); return { job_id: 1 }; };
  ctx.refs.pathInput.value = "/data/x";
  fill(dlg, "", "2026-10-09 10:00:00");
  ctx.refs.openForm.dispatch("submit");
  await settle();
  assert.deepEqual(seen[0], { to: T("2026-10-09T10:00:00") });
  live().emit("done", { job_id: 1, status: "ok", records: 0 });
  await settle();
  await ctx.panel.revealAddFiles(false, ctx.refs.caseAddFiles);
  toggle.checked = true;
  toggle.dispatch("change");
  await settle();
  fill(dlg, "2026-10-09 10:00:00", "");
  ctx.refs.pathInput.value = "/data/y";
  ctx.refs.openForm.dispatch("submit");
  await settle();
  assert.deepEqual(seen[1], { from: T("2026-10-09T10:00:00") });
  live().emit("done", { job_id: 1, status: "ok", records: 0 });
  await settle();
  await ctx.panel.revealAddFiles(false, ctx.refs.caseAddFiles);
  ctx.refs.pathInput.value = "/data/z";
  ctx.refs.openForm.dispatch("submit");
  await settle();
  assert.equal(seen[2], null, "option off: a normal opening");
});

test("the option on with nothing narrowed is refused instead of opening everything", async () => {
  const ctx = page();
  const { dlg } = await openAdd(ctx);
  let sent = 0;
  api.addCollection = async () => { sent += 1; return { job_id: 1 }; };
  ctx.refs.pathInput.value = "/data/x";
  ctx.refs.openForm.dispatch("submit");
  await settle();
  assert.equal(sent, 0);
  assert.equal(q(dlg, "po-error").filter((n) => !n.hidden).length, 1);
  assert.equal(q(dlg, "po-error").find((n) => !n.hidden).textContent, S.poErrNothing);
  assert.deepEqual(ctx.calls.messages.at(-1), [S.poErrNothing, true]);
  assert.equal(doc.activeElement, field(dlg, "from"));
});

test("the server's bad_first refusal is shown in the section and the dialog stays", async () => {
  const ctx = page();
  const { dlg } = await openAdd(ctx);
  api.addCollection = async () => { throw new ApiError(400, "bad_first", "first.from must be before first.to"); };
  ctx.refs.pathInput.value = "/data/x";
  fill(dlg, "2026-10-09 10:00:00", "");
  ctx.refs.openForm.dispatch("submit");
  await settle();
  assert.ok(isOpen(dlg));
  const shown = q(dlg, "po-error").find((n) => !n.hidden);
  assert.equal(shown.textContent, "The server refused the selection: first.from must be before first.to");
  assert.equal(ctx.panel.starting, false);
  assert.equal(FakeEventSource.instances.every((s) => s.closed), true, "the progress stream is closed");
});

test("two quick submits start once", async () => {
  const ctx = page();
  const { dlg } = await openAdd(ctx);
  let sent = 0;
  const gate = deferred();
  api.addCollection = async () => { sent += 1; await gate.promise; return { job_id: 1 }; };
  ctx.refs.pathInput.value = "/data/x";
  fill(dlg, "2026-10-09 10:00:00", "");
  ctx.refs.openForm.dispatch("submit");
  ctx.refs.openForm.dispatch("submit");
  await settle();
  gate.resolve();
  await settle();
  assert.equal(sent, 1);
});

// ---- the preview ------------------------------------------------------------------------------------------------

test("the preview lists the sources as text, checks the selected ones and disables the rest", async () => {
  const ctx = page();
  const { dlg } = await openAdd(ctx);
  const evil = "<img src=x onerror=alert(1)>.log";
  let call = null;
  api.partial.plan = async (...a) => {
    call = a;
    return planOf([
      { name: evil, family: "Wfm<b>", size: 1536 },
      { name: "off.log", selected: false },
      { name: "bad.log", indexable: false },
      { name: "sens.log", sensitive: true, pid: 0, first_ts: null, last_ts: null },
    ]);
  };
  ctx.panel.addFiles.state.add([ENT]);
  previewButton(dlg).dispatch("click");
  await settle();
  assert.deepEqual(call.slice(0, 3), ["A", ENT, "default"]);
  const boxes = q(dlg, "po-check");
  assert.deepEqual(boxes.map((b) => b.checked), [true, false, false, true]);
  assert.deepEqual(boxes.map((b) => b.disabled), [false, true, true, false]);
  const names = q(dlg, "po-name");
  assert.equal(names[0].textContent, evil);
  assert.equal(dlg.all().some((n) => n.tagName === "IMG"), false, "no element is built from a name");
  assert.match(q(dlg, "po-meta")[0].textContent, /Wfm<b> .* 1\.5 KB .* 2026-10-09 10:00:00 - 2026-10-09 12:00:00/);
  assert.match(q(dlg, "po-meta")[3].textContent, /sensitive .* pid 0/, "pid 0 is shown");
  assert.equal(q(dlg, "po-source")[1].title, S.poOutOfScope);
  assert.equal(q(dlg, "po-source")[2].title, S.poNotIndexable);
  assert.equal(q(dlg, "po-count")[0] ?? null, null);
  assert.match(q(dlg, "po-period")[0].textContent, /2026-10-09 10:00:00 to 2026-10-09 12:00:00/);
  assert.equal(q(dlg, "po-status")[0].textContent, "Previewed 1 file.");
});

test("choosing sources sends their member names; all checked sends none; none checked is refused", async () => {
  const ctx = page();
  const { dlg } = await openAdd(ctx);
  api.partial.plan = async () => planOf([{}, {}, {}]);
  const sent = [];
  api.addCollection = async (...a) => { sent.push(a[3]); return { job_id: 1 }; };
  ctx.refs.pathInput.value = ENT;
  previewButton(dlg).dispatch("click");
  await settle();
  const boxes = q(dlg, "po-check");
  boxes[1].checked = false;
  boxes[1].dispatch("change");
  assert.match(q(dlg, "po-summary")[0].textContent, /3 sources first|2 of 3 sources first/);
  ctx.refs.openForm.dispatch("submit");
  await settle();
  assert.deepEqual(sent, [{ sources: ["m0", "m2"] }]);
  live().emit("done", { job_id: 1, status: "ok", records: 0 });
  await settle();

  await ctx.panel.revealAddFiles(false, ctx.refs.caseAddFiles);
  q(dlg, "po-toggle-box")[0].checked = true;
  q(dlg, "po-toggle-box")[0].dispatch("change");
  await settle();
  ctx.refs.pathInput.value = ENT;
  previewButton(dlg).dispatch("click");
  await settle();
  ctx.refs.openForm.dispatch("submit");
  await settle();
  assert.equal(sent.length, 1, "all sources checked and no window: nothing narrows, refused");
  q(dlg, "po-actions")[0].children[2].dispatch("click"); // Uncheck all
  ctx.refs.openForm.dispatch("submit");
  await settle();
  assert.equal(sent.length, 1);
  const message = q(dlg, "po-error").find((n) => !n.hidden).textContent;
  assert.match(message, /check at least 1 source/);
  assert.equal(q(dlg, "po-sources")[0].getAttribute("aria-invalid"), "true");
  assert.equal(doc.activeElement, q(dlg, "po-check")[0]);
  q(dlg, "po-actions")[0].children[1].dispatch("click"); // Check all
  assert.equal(q(dlg, "po-check").every((b) => b.checked), true);
});

test("a window and sources go together; the list of 500 is the limit", async () => {
  const ctx = page();
  const { dlg } = await openAdd(ctx);
  api.partial.plan = async () => planOf(Array.from({ length: 502 }, () => ({})));
  const sent = [];
  api.addCollection = async (...a) => { sent.push(a[3]); return { job_id: 1 }; };
  ctx.refs.pathInput.value = ENT;
  previewButton(dlg).dispatch("click");
  await settle();
  const boxes = q(dlg, "po-check");
  boxes[501].checked = false;
  boxes[501].dispatch("change"); // 501 of 502 checked
  ctx.refs.openForm.dispatch("submit");
  await settle();
  assert.equal(sent.length, 0);
  assert.match(q(dlg, "po-error").find((n) => !n.hidden).textContent, /at most 500 sources/);
  boxes[500].checked = false;
  boxes[500].dispatch("change"); // 500 of 502
  fill(dlg, "2026-10-09 10:00:00", "2026-10-09 11:00:00");
  ctx.refs.openForm.dispatch("submit");
  await settle();
  assert.equal(sent.length, 1);
  assert.equal(sent[0].sources.length, 500);
  assert.equal(sent[0].from, T("2026-10-09T10:00:00"));
  assert.equal(sent[0].to, T("2026-10-09T11:00:00"));
});

test("a repeated Preview click while one runs asks once; a failed file shows its error and the others are listed", async () => {
  const ctx = page();
  const { dlg } = await openAdd(ctx);
  const gate = deferred();
  let calls = 0;
  api.partial.plan = async (caseId, path) => {
    calls += 1;
    if (path === SENS) { throw new ApiError(400, "path_unreadable", "Cannot read it"); }
    await gate.promise;
    return planOf([{}]);
  };
  ctx.panel.addFiles.state.add([ENT, SENS]);
  const button = previewButton(dlg);
  button.dispatch("click");
  button.dispatch("click");
  await settle();
  assert.equal(calls, 1);
  assert.equal(button.disabled, true);
  assert.match(q(dlg, "po-status")[0].textContent, /Reading .*Enterprise\.zip \(1 of 2\)/);
  gate.resolve();
  await settle();
  assert.equal(calls, 2);
  assert.equal(button.disabled, false);
  assert.equal(q(dlg, "po-group").length, 2);
  assert.match(q(dlg, "po-group")[1].textContent, /Sensitive_Enterprise\.zip: Cannot read it/);
  assert.equal(q(dlg, "po-check").length, 1);
  assert.equal(q(dlg, "po-status")[0].textContent, "Previewed 2 files.");
});

test("a preview answer that comes after a scope change, a case switch or a reset is dropped", async () => {
  const ctx = page();
  const { dlg } = await openAdd(ctx);
  const gate = deferred();
  api.partial.plan = async () => { await gate.promise; return planOf([{}]); };
  ctx.panel.addFiles.state.add([ENT]);
  previewButton(dlg).dispatch("click");
  await settle();
  ctx.refs.scopeSelect.value = "all";
  ctx.refs.scopeSelect.dispatch("change");
  assert.equal(previewButton(dlg).disabled, false, "the button is free again");
  gate.resolve();
  await settle();
  assert.equal(q(dlg, "po-check").length, 0, "the old answer does not fill the list");
  assert.equal(q(dlg, "po-status")[0].textContent, S.poScopeChanged);

  // a case switch while a preview runs
  const gate2 = deferred();
  api.partial.plan = async () => { await gate2.promise; return planOf([{}]); };
  ctx.refs.scopeSelect.value = "default";
  previewButton(dlg).dispatch("click");
  await settle();
  await ctx.panel.selectCase("B");
  gate2.resolve();
  await settle();
  assert.equal(q(dlg, "po-check").length, 0);
  assert.equal(q(dlg, "po-toggle-box")[0].checked, false, "the case switch resets the section");
  assert.equal(previewButton(dlg).disabled, false);
});

test("a preview made under scope default is not used once the scope is All", async () => {
  const ctx = page();
  const { dlg } = await openAdd(ctx);
  api.partial.plan = async () => planOf([{}, {}]);
  const sent = [];
  api.addCollection = async (...a) => { sent.push([a[2], a[3]]); return { job_id: 1 }; };
  ctx.refs.pathInput.value = ENT;
  previewButton(dlg).dispatch("click");
  await settle();
  const boxes = q(dlg, "po-check");
  boxes[1].checked = false;
  boxes[1].dispatch("change");
  ctx.refs.scopeSelect.value = "all";
  ctx.refs.scopeSelect.dispatch("change");
  fill(dlg, "2026-10-09 10:00:00", "");
  ctx.refs.openForm.dispatch("submit");
  await settle();
  assert.deepEqual(sent, [["all", { from: T("2026-10-09T10:00:00") }]], "the old choice of sources does not apply to another scope");
});

test("the window outside the previewed period is flagged in the summary", async () => {
  const ctx = page();
  const { dlg } = await openAdd(ctx);
  api.partial.plan = async () => planOf([{}]);
  ctx.panel.addFiles.state.add([ENT]);
  previewButton(dlg).dispatch("click");
  await settle();
  fill(dlg, "2026-10-09 15:00:00", "2026-10-09 16:00:00");
  field(dlg, "to").dispatch("input");
  assert.match(q(dlg, "po-summary")[0].textContent, /outside the period/);
  fill(dlg, "2026-10-09 11:00:00", "2026-10-09 16:00:00");
  field(dlg, "to").dispatch("input");
  assert.doesNotMatch(q(dlg, "po-summary")[0].textContent, /outside the period/);
});

// ---- the queue of files ------------------------------------------------------------------------------------------

test("each file gets its own sources; a file that was not previewed gets the window only; edits after the start do not leak", async () => {
  const ctx = page();
  const { dlg } = await openAdd(ctx);
  api.partial.plan = async (caseId, path) => (path === ENT ? planOf([{}, {}]) : (() => { throw new Error("must not be asked"); })());
  ctx.panel.addFiles.state.add([ENT]);
  previewButton(dlg).dispatch("click");
  await settle();
  q(dlg, "po-check")[1].checked = false;
  q(dlg, "po-check")[1].dispatch("change");
  fill(dlg, "2026-10-09 10:00:00", "2026-10-09 11:00:00");
  ctx.panel.addFiles.state.add([SENS]);
  const sent = [];
  api.addCollection = async (...a) => { sent.push(a[3]); return { job_id: "j" + sent.length }; };
  const run = ctx.panel.addFilesToCase();
  await settle();
  assert.equal(sent.length, 1);
  // edit the window while the first file runs
  fill(dlg, "2026-10-09 01:00:00", "2026-10-09 02:00:00");
  live().emit("done", { job_id: "j1", status: "ok", records: 5 });
  await settle();
  live().emit("done", { job_id: "j2", status: "ok", records: 5 });
  await run;
  assert.deepEqual(sent[0], { from: T("2026-10-09T10:00:00"), to: T("2026-10-09T11:00:00"), sources: ["m0"] });
  assert.deepEqual(sent[1], { from: T("2026-10-09T10:00:00"), to: T("2026-10-09T11:00:00") }, "the window as it was at the start");
});

test("validation failure keeps the queue from starting and frees it", async () => {
  const ctx = page();
  const { dlg } = await openAdd(ctx);
  let sent = 0;
  api.addCollection = async () => { sent += 1; return { job_id: 1 }; };
  ctx.panel.addFiles.state.add([ENT]);
  fill(dlg, "bad", "");
  await ctx.panel.addFilesToCase();
  assert.equal(sent, 0);
  assert.equal(ctx.panel.queueBusy, false);
  assert.equal(ctx.panel.addFiles.state.busy, false);
  assert.equal(ctx.panel.addFiles.state.items.length, 1, "the list stays");
});

// ---- progress, first_ready, done ---------------------------------------------------------------------------------

test("the status text follows the stage", () => {
  assert.equal(stagedIndexingText({ stage: "first", source: "core.log", records: 1200 }), "Indexing first part… core.log: 1,200 records");
  assert.equal(stagedIndexingText({ stage: "first", source: "", records: 1 }), "Indexing first part… 1 record");
  assert.equal(stagedIndexingText({ stage: "rest", source: "b.log", records: 5 }), "First part ready — indexing the rest in the background: b.log, 5 records");
  assert.equal(stagedIndexingText({ stage: "rest", source: "", records: 5 }), S.poStatusRestBare);
  assert.equal(stagedIndexingText({ stage: null, source: "a.log", records: 2 }), "Indexing a.log: 2 records", "without a stage: the plain text");
  assert.equal(stagedIndexingText({ stage: "weird", source: "a.log", records: 2 }), "Indexing a.log: 2 records");
  assert.equal(stagedIndexingText({ stage: "first", source: "a.log", records: NaN }), "Indexing first part… a.log: 0 records");
  assert.equal(stagedIndexingText(null), "Indexing: 0 records");
  assert.match(unmatchedText(["a", "b", "c", "d", "e"]), /^5 sources .*: a, b, c and 2 more\.$/);
  assert.match(unmatchedText(["a"]), /^1 source .*: a\.$/);
});

async function startedJob(ctx, dlg, window = ["2026-10-09 10:00:00", "2026-10-09 10:30:00"]) {
  api.addCollection = async () => ({ job_id: "J" });
  ctx.panel.addFiles.state.add([ENT]);
  fill(dlg, window[0], window[1]);
  const run = ctx.panel.addFilesToCase();
  await settle();
  return { run }; // wrapped: returning the promise itself would wait for the job
}

test("progress events show the stage in the status bar", async () => {
  const ctx = page();
  const { dlg } = await openAdd(ctx);
  const { run } = await startedJob(ctx, dlg);
  live().emit("progress", { job_id: "J", status: "progress", stage: "first", source: "core.log", records: 10, bytes_done: 5, bytes_total: 10 });
  assert.deepEqual(ctx.calls.progress.at(-1), { value: 5, max: 10, text: "Indexing first part… core.log: 10 records" });
  live().emit("progress", { job_id: "J", status: "progress", stage: "rest", source: "b.log", records: 3, bytes_done: 0, bytes_total: 0 });
  assert.deepEqual(ctx.calls.progress.at(-1), { value: null, max: null, text: "First part ready — indexing the rest in the background: b.log, 3 records" });
  live().emit("done", { job_id: "J", status: "ok", records: 13 });
  await run;
});

test("first_ready reloads, hands the window to the viewer, says the rest continues and closes the dialog of the last file", async () => {
  const ctx = page();
  const { dlg } = await openAdd(ctx);
  let loaded = 0;
  api.sources = async () => { loaded += 1; return { sources: [] }; };
  const { run } = await startedJob(ctx, dlg);
  assert.ok(isOpen(dlg));
  loaded = 0;
  live().emit("progress", { job_id: "J", status: "first_ready", stage: "first", records: 1234 });
  await settle();
  assert.equal(loaded, 1);
  assert.deepEqual(ctx.calls.first, [{ from: T("2026-10-09T10:00:00"), to: T("2026-10-09T10:30:00"), sources: null, records: 1234 }]);
  assert.equal(ctx.calls.done, 0, "the viewer's onFirstReady replaces onDone for this event");
  assert.deepEqual(ctx.calls.messages.at(-1), ["First part ready: 1,234 records. The rest is indexing in the background.", false]);
  assert.deepEqual(ctx.calls.progress.at(-1), { value: null, max: null, text: S.poStatusRestBare });
  assert.equal(isOpen(dlg), false, "the viewer is usable while the rest indexes");
  assert.equal(ctx.panel.queueBusy, true, "the queue still waits for the job");
  live().emit("progress", { job_id: "J", status: "first_ready", stage: "first", records: 1234 });
  await settle();
  assert.equal(ctx.calls.first.length, 1, "a second first_ready of the same job is ignored");
  live().emit("done", { job_id: "J", status: "ok", records: 5000, partial_left: 0, first_unmatched: [] });
  const result = await run;
  assert.equal(ctx.calls.done, 1);
  assert.equal(ctx.panel.queueBusy, false);
  assert.equal(result, undefined);
});

test("with more files to open the dialog stays at first_ready", async () => {
  const ctx = page();
  const { dlg } = await openAdd(ctx);
  api.addCollection = async () => ({ job_id: "J" + (ctx.panel.addFiles.state.items.filter((i) => i.status === "ok").length + 1) });
  ctx.panel.addFiles.state.add([ENT, SENS]);
  fill(dlg, "2026-10-09 10:00:00", "");
  const run = ctx.panel.addFilesToCase();
  await settle();
  live().emit("progress", { job_id: "J1", status: "first_ready", stage: "first", records: 3 });
  await settle();
  assert.ok(isOpen(dlg), "SENS is still waiting its turn");
  live().emit("done", { job_id: "J1", status: "ok", records: 3 });
  await settle();
  live().emit("progress", { job_id: "J2", status: "first_ready", stage: "first", records: 3 });
  await settle();
  assert.equal(isOpen(dlg), false, "the last file's first part is ready");
  live().emit("done", { job_id: "J2", status: "ok", records: 3 });
  await run;
});

test("a first_ready of another job, or after the case changed, does nothing; one before the start answered waits for it", async () => {
  const ctx = page();
  const { dlg } = await openAdd(ctx);
  const gate = deferred();
  api.addCollection = async () => { await gate.promise; return { job_id: "J" }; };
  ctx.panel.addFiles.state.add([ENT]);
  fill(dlg, "2026-10-09 10:00:00", "");
  const run = ctx.panel.addFilesToCase();
  await settle();
  live().emit("progress", { job_id: "OLD", status: "first_ready", stage: "first", records: 1 });
  live().emit("progress", { job_id: "J", status: "first_ready", stage: "first", records: 2 });
  await settle();
  assert.equal(ctx.calls.first.length, 0, "not decided before the job id is known");
  gate.resolve();
  await settle();
  assert.equal(ctx.calls.first.length, 1);
  assert.equal(ctx.calls.first[0].records, 2, "the first_ready of the old job was ignored");
  live().emit("done", { job_id: "J", status: "ok", records: 2 });
  await run;
});

test("first_ready whose reload ends after the job did still applies the window; after a case switch it does not", async () => {
  const ctx = page();
  const { dlg } = await openAdd(ctx);
  const { run } = await startedJob(ctx, dlg);
  const slow = deferred();
  api.sources = async () => { await slow.promise; return { sources: [] }; };
  const stream = live();
  stream.emit("progress", { job_id: "J", status: "first_ready", stage: "first", records: 1 });
  await settle();
  stream.emit("done", { job_id: "J", status: "ok", records: 9 }); // the job ends while the first reload is still running
  slow.resolve();
  await run;
  await settle();
  await settle();
  assert.equal(ctx.calls.first.length, 1, "the window of this operation is applied even though the job ended first");
  assert.deepEqual([ctx.calls.first[0].from, ctx.calls.first[0].to], [T("2026-10-09T10:00:00"), T("2026-10-09T10:30:00")]);
  assert.equal(ctx.calls.done, 1);
  assert.notEqual(ctx.calls.messages.at(-1)[0], S.poFirstShown);
  assert.equal(ctx.calls.progress.at(-1), null, "the progress bar is not brought back after the job ended");
});

test("a viewer handler that throws is reported, not left unhandled", async () => {
  const ctx = page();
  const { dlg } = await openAdd(ctx);
  ctx.panel.handlers.onFirstReady = () => { throw new Error("view broke"); };
  const { run } = await startedJob(ctx, dlg);
  live().emit("progress", { job_id: "J", status: "first_ready", stage: "first", records: 1 });
  await settle();
  assert.deepEqual(ctx.calls.messages.at(-1), ["view broke", true]);
  live().emit("done", { job_id: "J", status: "ok", records: 1 });
  await run;
});

test("done with first_unmatched and partial_left says so, flags the work left and asks for the pending list", async () => {
  const ctx = page();
  const { dlg } = await openAdd(ctx);
  let pendingCalls = 0;
  api.partial.pending = async () => { pendingCalls += 1; return { running: false, pending: [{ collection: 1, group: 1, scope: "default", partial: 1, pending: 1, indexing: 0 }] }; };
  const { run } = await startedJob(ctx, dlg);
  live().emit("done", { job_id: "J", status: "ok", records: 1500, first_unmatched: ["x.log", "<b>y</b>"], partial_left: 2 });
  await run;
  await settle();
  const [text, isError] = ctx.calls.messages.find(([t]) => t.startsWith("Indexed"));
  assert.equal(text, "Indexed 1,500 records. 2 sources chosen to open first were not found among the indexed sources: x.log, <b>y</b>. 2 sources are not fully indexed yet.");
  assert.equal(isError, true, "work left is not a quiet success");
  assert.equal(pendingCalls, 1, "a partial opening is followed by a pending check");
  const banner = q(doc.body, "resume-banner")[0];
  assert.equal(banner.hidden, false);
  assert.equal(ctx.loads.resume, 1);
});

test("a done without notes reads as before and a plain opening does not ask for pending work", async () => {
  const ctx = page();
  await ctx.panel.selectCase("A");
  let pendingCalls = 0;
  api.partial.pending = async () => { pendingCalls += 1; return { pending: [] }; };
  pendingCalls = 0;
  api.addCollection = async () => ({ job_id: "J" });
  const pending = ctx.panel.runCollection(ENT, "default", "A");
  await settle();
  live().emit("done", { job_id: "J", status: "ok", records: 1, partial_left: 0, first_unmatched: [] });
  assert.deepEqual(await pending, { status: "ok" });
  assert.deepEqual(ctx.calls.messages.at(-1), ["Indexed 1 record.", false]);
  await settle();
  assert.equal(pendingCalls, 0);
});

// ---- pending work and Resume now ---------------------------------------------------------------------------------

const PENDING = { case: "A", running: false, pending: [{ collection: 1, group: 1, scope: "default", partial: 2, pending: 1, indexing: 0 }] };
const banner = () => q(doc.body, "resume-banner")[0];

test("a case with nothing pending loads no banner", async () => {
  const ctx = page();
  await ctx.panel.selectCase("A");
  await settle();
  assert.equal(ctx.loads.resume, 0);
  assert.equal(banner(), undefined);
});

test("a case with pending work shows the offer once, announced in the status bar", async () => {
  const ctx = page();
  api.partial.pending = async () => PENDING;
  await ctx.panel.selectCase("A");
  await settle();
  assert.equal(ctx.loads.resume, 1);
  const node = banner();
  assert.equal(node.hidden, false);
  assert.equal(node.getAttribute("role"), null, "not a live region of its own: the status bar announces it");
  assert.equal(q(node, "resume-banner-text")[0].textContent, "Indexing is incomplete: 3 sources are not fully indexed.");
  const button = q(node, "resume-banner-button")[0];
  assert.equal(button.textContent, S.poResumeNow);
  assert.equal(button.disabled, false);
  assert.equal(ctx.refs.caseHeader.nextSibling, node, "it sits right under the case header");
  assert.equal(ctx.calls.messages.filter(([t]) => t === S.poPendingMessage).length, 1);
  await ctx.panel.selectCase("A");
  await settle();
  assert.equal(ctx.calls.messages.filter(([t]) => t === S.poPendingMessage).length, 1, "reselecting the same case does not announce it again");
  assert.equal(q(doc.body, "resume-banner").length, 1, "one banner");
});

test("pending work without sources left (the run stopped before it finished) has its own text", async () => {
  const ctx = page();
  api.partial.pending = async () => ({ running: false, pending: [{ group: 1, partial: 0, pending: 0, indexing: 0 }] });
  await ctx.panel.selectCase("A");
  await settle();
  assert.equal(q(banner(), "resume-banner-text")[0].textContent, S.poResumeInterrupted);
});

test("Resume now posts once, shows Resuming, follows the job and the offer goes away when nothing is left", async () => {
  const ctx = page();
  let left = true;
  api.partial.pending = async () => (left ? PENDING : { running: false, pending: [] });
  await ctx.panel.selectCase("A");
  await settle();
  const button = q(banner(), "resume-banner-button")[0];
  const gate = deferred();
  let posts = 0;
  api.partial.resume = async () => { posts += 1; await gate.promise; return { job_id: "R" }; };
  button.focus();
  button.dispatch("click");
  button.dispatch("click");
  await settle();
  assert.equal(posts, 1);
  assert.equal(button.disabled, true);
  assert.equal(button.textContent, S.poResuming);
  gate.resolve();
  await settle();
  assert.deepEqual(ctx.calls.messages.at(-1), [S.poResumeStarted, false]);
  live().emit("progress", { job_id: "R", status: "progress", stage: "rest", source: "z.log", records: 4, bytes_done: 1, bytes_total: 2 });
  assert.match(ctx.calls.progress.at(-1).text, /First part ready/);
  left = false;
  live().emit("done", { job_id: "R", status: "ok", records: 4, partial_left: 0 });
  await settle();
  assert.equal(banner().hidden, true, "nothing is pending any more");
  assert.equal(ctx.panel.resuming, false);
  assert.notEqual(doc.activeElement, button, "the focus left the hidden button");
  assert.equal(doc.activeElement, ctx.refs.caseAddFiles);
  assert.equal(ctx.calls.done, 1);
});

test("Resume now: nothing pending any more (200), already running (409) and a failure", async () => {
  const ctx = page();
  api.partial.pending = async () => PENDING;
  await ctx.panel.selectCase("A");
  await settle();
  const button = q(banner(), "resume-banner-button")[0];
  api.partial.resume = async () => ({ job_id: null });
  api.partial.pending = async () => ({ running: false, pending: [] });
  button.dispatch("click");
  await settle();
  assert.ok(ctx.calls.messages.some(([t]) => t === S.poResumeNothing));
  assert.equal(banner().hidden, true);
  assert.equal(ctx.panel.waiter, null);
  assert.equal(FakeEventSource.instances.every((s) => s.closed), true);

  api.partial.pending = async () => PENDING;
  await ctx.panel.checkPending("A");
  assert.equal(banner().hidden, false);
  api.partial.resume = async () => { throw new ApiError(409, "job_running", "busy"); };
  button.dispatch("click");
  await settle();
  assert.deepEqual(ctx.calls.messages.at(-1), [S.poResumeBusy, true]);
  assert.equal(button.hidden, true, "a job runs: text only, no button");
  assert.equal(q(banner(), "resume-banner-text")[0].textContent, S.poResumeRunning);
  assert.ok(live(), "its progress is watched");
  live().emit("done", { job_id: "x", status: "ok", records: 1 });
  await settle();

  api.partial.pending = async () => PENDING;
  await ctx.panel.checkPending("A");
  api.partial.resume = async () => { throw new ApiError(500, "internal", "disk on fire"); };
  button.dispatch("click");
  await settle();
  assert.deepEqual(ctx.calls.messages.find(([t]) => t.startsWith("Could not resume")), ["Could not resume indexing: disk on fire", true]);
  assert.equal(button.hidden, false, "the offer is back after a failure");
  assert.equal(button.disabled, false);
});

test("a job already running when the case opens is followed without a button", async () => {
  const ctx = page();
  api.partial.pending = async () => ({ ...PENDING, running: true });
  await ctx.panel.selectCase("A");
  await settle();
  assert.equal(q(banner(), "resume-banner-button")[0].hidden, true);
  assert.equal(q(banner(), "resume-banner-text")[0].textContent, S.poResumeRunning);
  assert.equal(FakeEventSource.instances.length, 1, "its progress is watched once");
  api.partial.pending = async () => ({ running: false, pending: [] });
  live().emit("done", { job_id: "x", status: "ok", records: 1 });
  await settle();
  assert.equal(banner().hidden, true);
});

test("a pending answer that comes after a case switch is dropped, and the banner of the old case goes away", async () => {
  const ctx = page();
  const gate = deferred();
  api.partial.pending = async (caseId) => { if (caseId === "A") { await gate.promise; return PENDING; } return { pending: [] }; };
  await ctx.panel.selectCase("A");
  await ctx.panel.selectCase("B");
  gate.resolve();
  await settle();
  assert.equal(ctx.loads.resume, 0, "the late answer of case A did nothing");

  api.partial.pending = async () => PENDING;
  await ctx.panel.selectCase("A");
  await settle();
  assert.equal(banner().hidden, false);
  api.partial.pending = async () => ({ pending: [] });
  await ctx.panel.selectCase("B");
  await settle();
  assert.equal(banner().hidden, true, "the offer belongs to the case it was found in");
});

test("a failed pending request shows nothing, and a failed banner module is reported and tried again at the next check", async () => {
  const ctx = page();
  api.partial.pending = async () => { throw new ApiError(0, "network", "down"); };
  await ctx.panel.selectCase("A");
  await settle();
  assert.equal(banner(), undefined);
  assert.equal(ctx.calls.messages.length, 0, "a best-effort check says nothing");

  api.partial.pending = async () => PENDING;
  let n = 0;
  ctx.panel.loadResume = async () => { n += 1; if (n === 1) { throw new Error("chunk"); } return resumeModule; };
  await ctx.panel.checkPending("A");
  assert.deepEqual(ctx.calls.messages.at(-1), [S.poResumeLoadFailed, true]);
  await ctx.panel.checkPending("A");
  assert.equal(n, 2);
  assert.equal(banner().hidden, false);
});

test("Resume now while a queue runs is refused, and the banner is not offered while a job waits", async () => {
  const ctx = page();
  api.partial.pending = async () => PENDING;
  await ctx.panel.selectCase("A");
  await settle();
  ctx.panel.setQueueBusy(true);
  let posts = 0;
  api.partial.resume = async () => { posts += 1; return { job_id: "R" }; };
  await ctx.panel.resumeIndexing();
  assert.equal(posts, 0);
  assert.deepEqual(ctx.calls.messages.at(-1), [S.poResumeBusy, true]);
  ctx.panel.setQueueBusy(false);
});

test("a file result carries the note of its done event", async () => {
  const ctx = page();
  await ctx.panel.selectCase("A");
  api.addCollection = async () => ({ job_id: "J" });
  const result = ctx.panel.runCollection(ENT, "default", "A", { sources: ["a.log"] });
  await settle();
  live().emit("done", { job_id: "J", status: "ok", records: 4, first_unmatched: ["a.log"], partial_left: 0 });
  const value = await result;
  assert.equal(value.status, "ok");
  assert.equal(value.message, "1 source chosen to open first was not found among the indexed sources: a.log.");
});

test("a case switch while the options are being checked starts nothing", async () => {
  const ctx = page();
  const { dlg } = await openAdd(ctx);
  const gate = deferred();
  // the options module is slow to answer the second time (a fresh load after a failure)
  fill(dlg, "2026-10-09 10:00:00", "");
  ctx.panel.addFiles.state.add([ENT]);
  let sent = 0;
  api.addCollection = async () => { sent += 1; return { job_id: "J" }; };
  const slot = ctx.panel.dialogs.partial;
  const realReady = slot.ready;
  slot.ready = async () => { await gate.promise; return realReady(); };
  const run = ctx.panel.addFilesToCase();
  await settle();
  await ctx.panel.selectCase("B");
  gate.resolve();
  await run;
  assert.equal(sent, 0);
  assert.equal(ctx.panel.queueBusy, false);
});

test("Esc on the dialog during a preview: the preview ends quietly and the next opening shows its result", async () => {
  const ctx = page();
  const { dlg } = await openAdd(ctx);
  const gate = deferred();
  api.partial.plan = async () => { await gate.promise; return planOf([{}, {}]); };
  ctx.panel.addFiles.state.add([ENT]);
  previewButton(dlg).dispatch("click");
  await settle();
  dlg.dispatch("keydown", { key: "Escape" });
  assert.equal(isOpen(dlg), false);
  gate.resolve();
  await settle();
  assert.equal(previewButton(dlg).disabled, false);
  assert.equal(q(dlg, "po-check").length, 2);
  assert.notEqual(doc.activeElement, previewButton(dlg), "a closed dialog does not steal the focus back");
});

test("the Preview button gets the focus back after the preview; a failed request never leaves it disabled", async () => {
  const ctx = page();
  const { dlg } = await openAdd(ctx);
  api.partial.plan = async () => { throw new ApiError(0, "network", "down"); };
  ctx.panel.addFiles.state.add([ENT]);
  const button = previewButton(dlg);
  button.focus();
  button.dispatch("click");
  await settle();
  assert.equal(button.disabled, false);
  assert.equal(doc.activeElement, button);
  assert.match(q(dlg, "po-group")[0].textContent, /down/);
});

test("Preview with no case, or no file, says so and asks nothing", async () => {
  const ctx = page();
  const { dlg } = await openAdd(ctx);
  let asked = 0;
  api.partial.plan = async () => { asked += 1; return planOf([{}]); };
  previewButton(dlg).dispatch("click");
  await settle();
  assert.equal(q(dlg, "po-status")[0].textContent, S.poNeedPath);
  assert.equal(asked, 0);
});

test("Enter in a window field does not submit the Open form", async () => {
  const ctx = page();
  const { dlg } = await openAdd(ctx);
  const event = field(dlg, "from").dispatch("keydown", { key: "Enter" });
  assert.equal(event.prevented, true);
});

test("a first_ready of an earlier job that arrives after the start answered is ignored too", async () => {
  const ctx = page();
  const { dlg } = await openAdd(ctx);
  const { run } = await startedJob(ctx, dlg);
  live().emit("progress", { job_id: "OLD", status: "first_ready", stage: "first", records: 9 });
  await settle();
  assert.equal(ctx.calls.first.length, 0);
  assert.equal(ctx.calls.messages.some(([t]) => t.startsWith("First part ready")), false);
  live().emit("done", { job_id: "J", status: "ok", records: 1 });
  await run;
});

test("Resume now called twice runs one job", async () => {
  const ctx = page();
  api.partial.pending = async () => PENDING;
  await ctx.panel.selectCase("A");
  await settle();
  const gate = deferred();
  let posts = 0;
  api.partial.resume = async () => { posts += 1; await gate.promise; return { job_id: "R" }; };
  const first = ctx.panel.resumeIndexing();
  const second = ctx.panel.resumeIndexing();
  await settle();
  assert.equal(posts, 1);
  gate.resolve();
  await settle();
  live().emit("done", { job_id: "R", status: "ok", records: 1 });
  await Promise.all([first, second]);
  assert.equal(posts, 1);
});

test("with the options unavailable nothing falls through to a full opening", async () => {
  const ctx = page();
  ctx.panel.loadPartial = async () => { throw new Error("chunk failed"); };
  const { dlg } = await openAdd(ctx);
  let sent = 0;
  api.addCollection = async () => { sent += 1; return { job_id: 1 }; };
  ctx.panel.addFiles.state.add([ENT]);
  await ctx.panel.addFilesToCase();
  ctx.refs.pathInput.value = "/data/x";
  ctx.refs.openForm.dispatch("submit");
  await settle();
  assert.equal(sent, 0);
  assert.ok(isOpen(dlg));
});

test("an offer left in 'running' while a plain job ran is settled when that job ends", async () => {
  const ctx = page();
  api.partial.pending = async () => PENDING;
  await ctx.panel.selectCase("A");
  await settle();
  api.addCollection = async () => ({ job_id: "P" });
  const result = ctx.panel.runCollection(ENT, "default", "A"); // a plain opening, no window
  await settle();
  await ctx.panel.checkPending("A");
  assert.equal(q(banner(), "resume-banner-text")[0].textContent, S.poResumeRunning, "a job waits: no button");
  api.partial.pending = async () => ({ running: false, pending: [] });
  live().emit("done", { job_id: "P", status: "ok", records: 1 });
  await result;
  await settle();
  assert.equal(banner().hidden, true);
});

test("the banner describes its button and keeps the visible name", async () => {
  const ctx = page();
  api.partial.pending = async () => PENDING;
  await ctx.panel.selectCase("A");
  await settle();
  const button = q(banner(), "resume-banner-button")[0];
  const text = q(banner(), "resume-banner-text")[0];
  assert.equal(button.getAttribute("aria-label"), null, "the accessible name is the visible one");
  assert.equal(button.getAttribute("aria-describedby"), text.id);
  assert.ok(text.id !== "");
});

test("a start request that throws synchronously frees the queue with a failed result", async () => {
  const ctx = page();
  await ctx.panel.selectCase("A");
  api.addCollection = () => { throw new Error("no way"); };
  const result = await ctx.panel.runCollection(ENT, "default", "A");
  assert.equal(result.status, "failed");
  assert.equal(result.message, "no way");
  assert.equal(ctx.panel.waiter, null);
});

// ---- review round: one operation at a time, identity of the operation, what each action opens --------------------------

test("Resume then Add: the add is refused and the resume keeps its stream, waiter and busy offer", async () => {
  const ctx = page();
  api.partial.pending = async () => PENDING;
  await ctx.panel.selectCase("A");
  await settle();
  api.partial.resume = async () => ({ job_id: "R" });
  q(banner(), "resume-banner-button")[0].dispatch("click");
  await settle();
  const stream = live();
  const waiter = ctx.panel.waiter;
  let sent = 0;
  api.addCollection = async () => { sent += 1; return { job_id: "X" }; };
  ctx.panel.addFiles.state.add([ENT]);
  await ctx.panel.addFilesToCase();
  const direct = await ctx.panel.runCollection(ENT, "default", "A");
  assert.equal(sent, 0, "nothing was posted");
  assert.equal(direct.status, "refused");
  assert.equal(direct.code, "job_running");
  assert.equal(live(), stream, "the resume's stream was not replaced or closed");
  assert.equal(ctx.panel.waiter, waiter);
  assert.equal(ctx.panel.resuming, true);
  assert.equal(q(banner(), "resume-banner-button")[0].disabled, true);
  assert.deepEqual(ctx.calls.messages.at(-1), [S.cfBusy, true]);
  api.partial.pending = async () => ({ pending: [] });
  stream.emit("done", { job_id: "R", status: "ok", records: 1 });
  await settle();
  assert.equal(ctx.panel.resuming, false);
  assert.equal(banner().hidden, true);
});

test("Add then Resume: the resume is refused and the queue is untouched", async () => {
  const ctx = page();
  api.partial.pending = async () => PENDING;
  await ctx.panel.selectCase("A");
  await settle();
  const { dlg } = await openAdd(ctx);
  const { run } = await startedJob(ctx, dlg);
  const stream = live();
  let posts = 0;
  api.partial.resume = async () => { posts += 1; return { job_id: "R" }; };
  await ctx.panel.resumeIndexing();
  assert.equal(posts, 0);
  assert.equal(live(), stream);
  assert.deepEqual(ctx.calls.messages.at(-1), [S.poResumeBusy, true]);
  stream.emit("done", { job_id: "J", status: "ok", records: 1 });
  await run;
});

test("an answer from an earlier case or dialog session never touches the current dialog", async () => {
  const ctx = page();
  const { dlg } = await openAdd(ctx);
  const gate = deferred();
  api.addCollection = async () => { await gate.promise; return { job_id: "J" }; };
  ctx.refs.pathInput.value = "/data/a";
  fill(dlg, "2026-10-09 10:00:00", "");
  ctx.refs.openForm.dispatch("submit");
  await settle();
  await ctx.panel.selectCase("B");
  await ctx.panel.revealAddFiles(false, ctx.refs.caseAddFiles);
  const toggle = q(dlg, "po-toggle-box")[0];
  toggle.checked = true;
  toggle.dispatch("change");
  await settle();
  ctx.refs.pathInput.value = "/data/b";
  fill(dlg, "2026-10-09 11:00:00", "");
  gate.resolve();
  await settle();
  assert.ok(isOpen(dlg), "the dialog of case B stays");
  assert.equal(ctx.refs.pathInput.value, "/data/b");
  assert.equal(field(dlg, "from").value, "2026-10-09 11:00:00");
  assert.equal(toggle.checked, true);
  assert.equal(ctx.panel.waiter, null);
  assert.equal(ctx.panel.starting, false);
});

test("a late refusal from an earlier case leaves the current field, dialog and stream alone", async () => {
  const ctx = page();
  const { dlg } = await openAdd(ctx);
  const gate = deferred();
  api.addCollection = async () => { await gate.promise; };
  ctx.refs.pathInput.value = "/data/a";
  fill(dlg, "2026-10-09 10:00:00", "");
  ctx.refs.openForm.dispatch("submit");
  await settle();
  await ctx.panel.selectCase("B");
  await ctx.panel.revealAddFiles(false, ctx.refs.caseAddFiles);
  api.progressUrl = () => "/p-b";
  const mine = ctx.panel.watchProgress("B"); // a stream of the current case
  void mine;
  const stream = live();
  gate.reject(new ApiError(404, "path_not_found", "Path does not exist: /data/a"));
  await settle();
  assert.equal(ctx.refs.pathInput.getAttribute("aria-invalid"), null);
  assert.equal(live(), stream, "the current stream stays open");
  assert.ok(isOpen(dialogOf("case-dialog-add")));
});

test("closing and reopening the dialog in the same case also retires the earlier submit", async () => {
  const ctx = page();
  const { dlg } = await openAdd(ctx);
  const gate = deferred();
  api.addCollection = async () => { await gate.promise; return { job_id: "J" }; };
  ctx.refs.pathInput.value = "/data/a";
  fill(dlg, "2026-10-09 10:00:00", "");
  ctx.refs.openForm.dispatch("submit");
  await settle();
  dlg.dispatch("keydown", { key: "Escape" });
  await ctx.panel.revealAddFiles(false, ctx.refs.caseAddFiles);
  ctx.refs.pathInput.value = "/data/second";
  gate.resolve();
  await settle();
  assert.ok(isOpen(dlg), "the reopened dialog is not closed by the old answer");
  assert.equal(ctx.refs.pathInput.value, "/data/second");
  assert.ok(ctx.panel.waiter, "the job itself goes on");
});

test("the Add action validates only the files it opens; the folder field is the other action's", async () => {
  const ctx = page();
  const { dlg } = await openAdd(ctx);
  api.partial.plan = async () => planOf([{}, {}]);
  const sent = [];
  api.addCollection = async (...a) => { sent.push([a[1], a[3]]); return { job_id: "J" }; };
  ctx.panel.addFiles.state.add([ENT]);
  ctx.refs.pathInput.value = SENS;
  previewButton(dlg).dispatch("click");
  await settle();
  const groups = q(dlg, "po-group");
  assert.equal(groups.length, 2);
  const box = q(groups[1], "po-check")[1];
  box.checked = false;
  box.dispatch("change"); // restrict only the file of the folder field
  await ctx.panel.addFilesToCase();
  assert.equal(sent.length, 0, "ENT would have opened whole: refused");
  assert.equal(q(dlg, "po-error").find((n) => !n.hidden).textContent, S.poErrNothing);
  ctx.refs.openForm.dispatch("submit");
  await settle();
  assert.deepEqual(sent, [[SENS, { sources: ["m0"] }]], "the folder field action opens its own file with its choice");
});

test("the scope is frozen with the queue", async () => {
  const ctx = page();
  const { dlg } = await openAdd(ctx);
  const calls = [];
  api.addCollection = async (...a) => { calls.push([a[2], a[3]]); return { job_id: "J" + calls.length }; };
  ctx.panel.addFiles.state.add([ENT, SENS]);
  fill(dlg, "2026-10-09 10:00:00", "");
  const run = ctx.panel.addFilesToCase();
  await settle();
  ctx.refs.scopeSelect.value = "all";
  ctx.refs.scopeSelect.dispatch("change");
  live().emit("done", { job_id: "J1", status: "ok", records: 1 });
  await settle();
  live().emit("done", { job_id: "J2", status: "ok", records: 1 });
  await run;
  const win = { from: T("2026-10-09T10:00:00") };
  assert.deepEqual(calls, [["default", win], ["default", win]]);
});

test("the typed path is bound to its job like the queue: a replay of another job changes nothing", async () => {
  const ctx = page();
  const { dlg } = await openAdd(ctx);
  api.addCollection = async () => ({ job_id: "NEW" });
  ctx.refs.pathInput.value = "/data/x";
  fill(dlg, "2026-10-09 10:00:00", "2026-10-09 11:00:00");
  ctx.refs.openForm.dispatch("submit");
  await settle();
  const stream = live();
  stream.emit("progress", { job_id: "OLD", status: "first_ready", stage: "first", records: 1 });
  stream.emit("done", { job_id: "OLD", status: "ok", records: 99 });
  await settle();
  assert.equal(ctx.calls.first.length, 0, "the window of NEW is not applied for OLD");
  assert.ok(ctx.panel.waiter, "OLD's done did not finish NEW");
  assert.equal(ctx.calls.messages.some(([t]) => t.startsWith("Indexed 99")), false);
  stream.emit("progress", { job_id: "NEW", status: "first_ready", stage: "first", records: 2 });
  await settle();
  assert.equal(ctx.calls.first.length, 1);
  assert.equal(ctx.calls.first[0].from, T("2026-10-09T10:00:00"));
  stream.emit("done", { job_id: "NEW", status: "ok", records: 5 });
  await settle();
  assert.equal(ctx.panel.waiter, null);
});

test("a first_ready kept before the start answered and replayed after it runs once", async () => {
  const ctx = page();
  const { dlg } = await openAdd(ctx);
  const gate = deferred();
  api.addCollection = async () => { await gate.promise; return { job_id: "J" }; };
  ctx.panel.addFiles.state.add([ENT]);
  fill(dlg, "2026-10-09 10:00:00", "");
  const run = ctx.panel.addFilesToCase();
  await settle();
  const stream = live();
  stream.emit("progress", { job_id: "J", status: "first_ready", stage: "first", records: 2 });
  stream.emit("progress", { job_id: "J", status: "first_ready", stage: "first", records: 2 });
  gate.resolve();
  await settle();
  stream.emit("progress", { job_id: "J", status: "first_ready", stage: "first", records: 2 }); // replay after a reconnection
  await settle();
  assert.equal(ctx.calls.first.length, 1);
  stream.emit("done", { job_id: "J", status: "ok", records: 2 });
  await run;
});

test("a fast job under a case switch does not apply its window to the other case", async () => {
  const ctx = page();
  const { dlg } = await openAdd(ctx);
  const { run } = await startedJob(ctx, dlg);
  const slow = deferred();
  api.sources = async () => { await slow.promise; return { sources: [] }; };
  live().emit("progress", { job_id: "J", status: "first_ready", stage: "first", records: 1 });
  await settle();
  const switching = ctx.panel.selectCase("B");
  slow.resolve();
  await switching;
  await run;
  await settle();
  assert.equal(ctx.calls.first.length, 0);
});

test("Resume failing with /pending also failing leaves an actionable offer", async () => {
  const ctx = page();
  api.partial.pending = async () => PENDING;
  await ctx.panel.selectCase("A");
  await settle();
  const button = q(banner(), "resume-banner-button")[0];
  api.partial.resume = async () => { throw new ApiError(0, "network", "down"); };
  api.partial.pending = async () => { throw new ApiError(0, "network", "down"); };
  button.dispatch("click");
  await settle();
  assert.equal(button.disabled, false);
  assert.equal(button.textContent, S.poResumeNow);
  assert.equal(ctx.panel.resuming, false);
  // and a resume that worked, followed by a /pending that fails, is not left busy either
  api.partial.resume = async () => ({ job_id: "R" });
  button.dispatch("click");
  await settle();
  live().emit("done", { job_id: "R", status: "ok", records: 1 });
  await settle();
  assert.equal(button.disabled, false);
  assert.equal(button.hidden, false);
});

test("an offer left running while a job ended and /pending fails acts again", async () => {
  const ctx = page();
  api.partial.pending = async () => ({ ...PENDING, running: true });
  await ctx.panel.selectCase("A");
  await settle();
  assert.equal(q(banner(), "resume-banner-button")[0].hidden, true);
  api.partial.pending = async () => { throw new ApiError(0, "network", "down"); };
  live().emit("done", { job_id: "x", status: "ok", records: 1 });
  await settle();
  assert.equal(q(banner(), "resume-banner-button")[0].hidden, false);
});

// ---- review round, P2 ------------------------------------------------------------------------------------------------

test("a rotation chain is chosen whole: its boxes move together and the count and the names say so", async () => {
  const ctx = page();
  const { dlg } = await openAdd(ctx);
  api.partial.plan = async () => planOf([
    { name: "core.log", pid: 7 }, { name: "core.log.1", pid: 7 }, { name: "other.log", pid: 7 },
  ]);
  const sent = [];
  api.addCollection = async (...a) => { sent.push(a[3]); return { job_id: "J" }; };
  ctx.refs.pathInput.value = ENT;
  previewButton(dlg).dispatch("click");
  await settle();
  const boxes = q(dlg, "po-check");
  boxes[1].checked = false; // core.log.1
  boxes[1].dispatch("change");
  assert.equal(boxes[0].checked, false, "core.log follows core.log.1");
  assert.equal(boxes[2].checked, true);
  assert.match(q(dlg, "po-summary")[0].textContent, /1 of 3 sources first/);
  boxes[1].checked = true;
  boxes[1].dispatch("change");
  boxes[2].checked = false;
  boxes[2].dispatch("change");
  assert.match(q(dlg, "po-summary")[0].textContent, /2 of 3 sources first/);
  ctx.refs.openForm.dispatch("submit");
  await settle();
  assert.deepEqual(sent, [{ sources: ["m0", "m1"] }], "the whole chain goes");
  assert.match(S.poSourcesHelp, /Rotated files .* together/);
});

test("a preview of a file that left the dialog is marked and never used", async () => {
  const ctx = page();
  const { dlg } = await openAdd(ctx);
  api.partial.plan = async () => planOf([{}, {}]);
  ctx.panel.addFiles.state.add([ENT]);
  previewButton(dlg).dispatch("click");
  await settle();
  const box = q(dlg, "po-check")[1];
  box.checked = false;
  box.dispatch("change");
  assert.match(q(dlg, "po-summary")[0].textContent, /1 of 2 sources first|first/);
  ctx.panel.addFiles.state.remove(ctx.panel.addFiles.state.items[0].id);
  const group = q(dlg, "po-group")[0];
  assert.ok(has(group, "po-stale"));
  assert.match(group.textContent, /No longer in the list/);
  assert.equal(q(group, "po-check").every((b) => b.disabled), true);
  assert.doesNotMatch(q(dlg, "po-summary")[0].textContent, /sources first/);
  ctx.panel.addFiles.state.add([ENT]);
  assert.equal(has(q(dlg, "po-group")[0], "po-stale"), false, "back in the list: current again");
});

const regionsOf = (dlg) => dlg.all().filter((n) => ["status", "alert"].includes(n.getAttribute("role")) || n.getAttribute("aria-live") !== null);

test("each dialog has exactly one live region, and the list, the notices and the section speak through it", async () => {
  const ctx = page();
  const { dlg } = await openAdd(ctx);
  api.partial.plan = async () => planOf([{}]);
  ctx.panel.addFiles.state.add([ENT, ENT]); // the second is a duplicate: a notice
  previewButton(dlg).dispatch("click");
  await settle();
  assert.deepEqual(regionsOf(dlg).map((n) => n.className), ["cd-live visually-hidden"], "the whole Add dialog");
  const live = q(dlg, "cd-live")[0];
  assert.equal(live.textContent, "Previewed 1 file.");
  ctx.panel.addFiles.state.add(["/data/other.zip"]);
  assert.equal(live.textContent, "2 chosen", "the list's count is spoken through the same region");
  ctx.panel.addFiles.state.add(["/data/other.zip"]);
  assert.match(live.textContent, /already in the list/, "a notice too");
  await ctx.panel.revealNewCase(ctx.refs.caseMenu);
  const fresh = dialogOf("case-dialog-new");
  assert.deepEqual(regionsOf(fresh).map((n) => n.className), ["cd-live visually-hidden"], "and the New case dialog");
});

test("the period says what its end is, and unpreviewed files say what applies to them", async () => {
  const ctx = page();
  const { dlg } = await openAdd(ctx);
  api.partial.plan = async () => planOf([{ last_ts: null }]);
  ctx.panel.addFiles.state.add([ENT, SENS]);
  ctx.refs.pathInput.value = "";
  previewButton(dlg).dispatch("click");
  await settle();
  assert.match(q(dlg, "po-period")[0].textContent, /to about 2026-10-09 14:30:00 \(capture time of the file name\)/);
  ctx.panel.addFiles.state.add(["/data/plain-a.zip"]);
  api.partial.plan = async (c, path) => planOf([{ last_ts: null, first_ts: T("2026-10-09T10:00:00") }]);
  ctx.panel.addFiles.state.clear();
  ctx.panel.addFiles.state.add(["/data/plain.zip"]);
  previewButton(dlg).dispatch("click");
  await settle();
  assert.match(q(dlg, "po-period")[0].textContent, /to at least 2026-10-09 10:00:00 \(the end is not known\)/);
  ctx.panel.addFiles.state.add([ENT]);
  assert.match(q(dlg, "po-summary")[0].textContent, /1 file was not previewed and opens normally/);
  fill(dlg, "2026-10-09 10:00:00", "");
  field(dlg, "from").dispatch("input");
  assert.match(q(dlg, "po-summary")[0].textContent, /1 file was not previewed: only the time window applies to it/);
});

test("the first part of a queue started in an earlier dialog session does not close the reopened dialog", async () => {
  const ctx = page();
  const { dlg } = await openAdd(ctx);
  const { run } = await startedJob(ctx, dlg);
  dlg.dispatch("keydown", { key: "Escape" });
  await ctx.panel.revealAddFiles(false, ctx.refs.caseAddFiles);
  assert.ok(isOpen(dlg));
  live().emit("progress", { job_id: "J", status: "first_ready", stage: "first", records: 1 });
  await settle();
  assert.ok(isOpen(dlg), "the new session of the dialog stays");
  live().emit("done", { job_id: "J", status: "ok", records: 1 });
  await run;
  assert.ok(isOpen(dlg), "and the old queue's end does not reset or close it either");
});

// ---- second review round ---------------------------------------------------------------------------------------------

test("a late first_ready reload of a finished job does not put its window over a later operation's", async () => {
  const ctx = page();
  const { dlg } = await openAdd(ctx);
  const first = await startedJob(ctx, dlg, ["2026-10-09 10:00:00", "2026-10-09 10:30:00"]);
  const slow = deferred();
  api.sources = async () => { await slow.promise; return { sources: [] }; };
  const stream1 = live();
  stream1.emit("progress", { job_id: "J", status: "first_ready", stage: "first", records: 1 }); // J1's reload is slow
  await settle();
  api.sources = async () => ({ sources: [] });
  stream1.emit("done", { job_id: "J", status: "ok", records: 1 });
  await first.run;
  await settle();
  const applied = ctx.calls.first.length; // J1's window may or may not have landed yet: it has not (reload pending)
  assert.equal(applied, 0);
  // J2 starts and applies its own window
  await ctx.panel.revealAddFiles(false, ctx.refs.caseAddFiles);
  q(dlg, "po-toggle-box")[0].checked = true;
  q(dlg, "po-toggle-box")[0].dispatch("change");
  await settle();
  api.addCollection = async () => ({ job_id: "J2" });
  ctx.panel.addFiles.state.add([ENT]);
  fill(dlg, "2026-10-09 12:00:00", "2026-10-09 13:00:00");
  const second = ctx.panel.addFilesToCase();
  await settle();
  live().emit("progress", { job_id: "J2", status: "first_ready", stage: "first", records: 2 });
  await settle();
  assert.equal(ctx.calls.first.length, 1);
  assert.equal(ctx.calls.first[0].from, T("2026-10-09T12:00:00"));
  slow.resolve(); // J1's late reload ends now
  await settle();
  assert.equal(ctx.calls.first.length, 1, "J1's 10:00 window did not come back over J2's");
  live().emit("done", { job_id: "J2", status: "ok", records: 2 });
  await second;
});

test("a late first_ready reload across A -> B -> A is dropped", async () => {
  const ctx = page();
  const { dlg } = await openAdd(ctx);
  const { run } = await startedJob(ctx, dlg);
  const slow = deferred();
  api.sources = async () => { await slow.promise; return { sources: [] }; };
  live().emit("progress", { job_id: "J", status: "first_ready", stage: "first", records: 1 });
  await settle();
  api.sources = async () => ({ sources: [] });
  await ctx.panel.selectCase("B");
  await ctx.panel.selectCase("A");
  slow.resolve();
  await run;
  await settle();
  assert.equal(ctx.calls.first.length, 0);
});

test("a refusal (bad_first) of an earlier dialog session is not shown in the reopened dialog", async () => {
  const ctx = page();
  const { dlg } = await openAdd(ctx);
  const gate = deferred();
  api.addCollection = async () => { await gate.promise; };
  ctx.panel.addFiles.state.add([ENT]);
  fill(dlg, "2026-10-09 10:00:00", "");
  const run = ctx.panel.addFilesToCase();
  await settle();
  dlg.dispatch("keydown", { key: "Escape" });
  await ctx.panel.revealAddFiles(false, ctx.refs.caseAddFiles);
  gate.reject(new ApiError(400, "bad_first", "window of /data/old is bad"));
  await run;
  await settle();
  assert.equal(q(dlg, "po-error").some((n) => !n.hidden && /\/data\/old/.test(n.textContent)), false);
  // the same refusal in its own session is shown
  const gate2 = deferred();
  api.addCollection = async () => { await gate2.promise; };
  q(dlg, "po-toggle-box")[0].checked = true;
  q(dlg, "po-toggle-box")[0].dispatch("change");
  await settle();
  ctx.panel.addFiles.state.add([ENT]);
  fill(dlg, "2026-10-09 10:00:00", "");
  const run2 = ctx.panel.addFilesToCase();
  await settle();
  gate2.reject(new ApiError(400, "bad_first", "first.from is bad"));
  await run2;
  assert.ok(q(dlg, "po-error").some((n) => !n.hidden && /first\.from is bad/.test(n.textContent)));
});

test("removing the file while its /plan runs leaves a stale group with disabled checkboxes", async () => {
  const ctx = page();
  const { dlg } = await openAdd(ctx);
  const gate = deferred();
  api.partial.plan = async () => { await gate.promise; return planOf([{}, {}]); };
  ctx.panel.addFiles.state.add([ENT]);
  previewButton(dlg).dispatch("click");
  await settle();
  ctx.panel.addFiles.state.remove(ctx.panel.addFiles.state.items[0].id);
  gate.resolve();
  await settle();
  const group = q(dlg, "po-group")[0];
  assert.ok(has(group, "po-stale"));
  assert.equal(q(group, "po-check").every((b) => b.disabled), true);
});

test("an old /pending failure cannot enable the button of a resume that is running", async () => {
  const ctx = page();
  api.partial.pending = async () => PENDING;
  await ctx.panel.selectCase("A");
  await settle();
  const button = q(banner(), "resume-banner-button")[0];
  // J1 ends; its follow-up /pending is slow and will fail
  const gate = deferred();
  api.addCollection = async () => ({ job_id: "J1" });
  const done1 = ctx.panel.runCollection(ENT, "default", "A", { sources: ["x"] });
  await settle();
  api.partial.pending = async () => { await gate.promise; throw new ApiError(0, "network", "down"); };
  live().emit("done", { job_id: "J1", status: "ok", records: 1 });
  await done1;
  await settle();
  // the user presses Resume now: R is running
  api.partial.resume = async () => ({ job_id: "R" });
  button.dispatch("click");
  await settle();
  assert.equal(button.disabled, true);
  assert.equal(ctx.panel.resuming, true);
  gate.resolve(); // the old check fails now
  await settle();
  assert.equal(button.disabled, true, "still busy: the resume owns the offer");
  assert.equal(ctx.panel.resuming, true);
});

test("progress of another job does not move the bar of this operation, nor does progress before the start answered", async () => {
  const ctx = page();
  const { dlg } = await openAdd(ctx);
  const gate = deferred();
  api.addCollection = async () => { await gate.promise; return { job_id: "NEW" }; };
  ctx.panel.addFiles.state.add([ENT]);
  fill(dlg, "2026-10-09 10:00:00", "");
  const run = ctx.panel.addFilesToCase();
  await settle();
  const stream = live();
  const before = ctx.calls.progress.length;
  stream.emit("progress", { job_id: "OLD", status: "progress", stage: "rest", source: "o.log", records: 99, bytes_done: 99, bytes_total: 100 });
  assert.equal(ctx.calls.progress.length, before, "before the start answered nothing is shown");
  gate.resolve();
  await settle();
  stream.emit("progress", { job_id: "OLD", status: "progress", stage: "rest", source: "o.log", records: 99, bytes_done: 99, bytes_total: 100 });
  assert.equal(ctx.calls.progress.at(-1) === null || ctx.calls.progress.at(-1).max !== 100, true, "OLD does not draw 99/100 during NEW");
  stream.emit("progress", { job_id: "NEW", status: "progress", stage: "first", source: "n.log", records: 3, bytes_done: 1, bytes_total: 4 });
  assert.deepEqual(ctx.calls.progress.at(-1), { value: 1, max: 4, text: "Indexing first part… n.log: 3 records" });
  stream.emit("done", { job_id: "NEW", status: "ok", records: 3 });
  await run;
});

// ---- third review round ----------------------------------------------------------------------------------------------

test("a slow sources answer of case A that arrives after B was opened touches nothing of B", async () => {
  const ctx = page();
  const { dlg } = await openAdd(ctx);
  const seen = [];
  ctx.panel.handlers.onSources = (list) => seen.push(list.map((s) => s.name));
  const { run } = await startedJob(ctx, dlg);
  const slowA = deferred();
  api.sources = async (caseId) => (caseId === "A" ? (await slowA.promise, { sources: [{ id: 1, name: "from-A.log" }] }) : { sources: [{ id: 9, name: "from-B.log" }] });
  live().emit("progress", { job_id: "J", status: "first_ready", stage: "first", records: 1 });
  await settle();
  await ctx.panel.selectCase("B");
  assert.deepEqual(ctx.panel.sources.map((s) => s.name), ["from-B.log"]);
  seen.length = 0;
  const messagesBefore = ctx.calls.messages.length;
  slowA.resolve();
  await run;
  await settle();
  assert.deepEqual(ctx.panel.sources.map((s) => s.name), ["from-B.log"], "B keeps its own sources");
  assert.deepEqual(seen, [], "onSources was not called with A's list");
  assert.equal(ctx.panel.caseId, "B");
  assert.equal(ctx.calls.first.length, 0);
  void messagesBefore;
});

test("a failing sources answer of an earlier selection says nothing and clears nothing", async () => {
  const ctx = page();
  await ctx.panel.selectCase("A");
  api.sources = async (caseId) => ({ sources: [{ id: 9, name: caseId + ".log" }] });
  await ctx.panel.selectCase("B");
  const slow = deferred();
  api.sources = async () => { await slow.promise; throw new ApiError(500, "boom", "A broke"); };
  const refresh = ctx.panel.refreshSources(true); // B asks
  api.sources = async () => ({ sources: [{ id: 3, name: "B2.log" }] });
  const again = ctx.panel.selectCase("B"); // the same case selected again: a newer selection
  await again;
  slow.resolve();
  await refresh;
  assert.deepEqual(ctx.panel.sources.map((s) => s.name), ["B2.log"]);
  assert.equal(ctx.calls.messages.some(([t]) => t === "A broke"), false);
});

test("a file that starts, ends or fails is spoken through the dialog's region even when the count is unchanged", async () => {
  const ctx = page();
  const { dlg } = await openAdd(ctx);
  const spoken = [];
  const original = ctx.panel.addFiles.announce;
  ctx.panel.addFiles.announce = (text) => { spoken.push(text); original(text); };
  api.addCollection = async () => ({ job_id: "J" + (spoken.length + 1) });
  ctx.panel.addFiles.state.add([ENT, SENS]);
  fill(dlg, "2026-10-09 10:00:00", "");
  const run = ctx.panel.addFilesToCase();
  await settle();
  assert.ok(spoken.some((t) => t.includes("Enterprise.zip: Opening…")), "the first file starts");
  const before = spoken.length;
  live().emit("done", { job_id: live() ? ctx.panel.waiter.jobId : "", status: "error", message: "bad zip", code: "x" });
  await settle();
  assert.ok(spoken.slice(before).some((t) => /Enterprise\.zip: Failed: bad zip/.test(t)), "the first fails");
  assert.ok(spoken.slice(before).some((t) => /Sensitive_Enterprise\.zip: Opening…/.test(t)), "the second starts, same count");
  live().emit("done", { job_id: ctx.panel.waiter.jobId, status: "ok", records: 1 });
  await run;
});

test("two /sources queries of the same case: only the latest publishes, even if the older answers last", async () => {
  const ctx = page();
  await ctx.panel.selectCase("A");
  const published = [];
  ctx.panel.handlers.onSources = (list) => published.push(list.map((s) => s.name));
  const slowOld = deferred();
  api.sources = async () => { await slowOld.promise; return { sources: [{ id: 1, name: "first.log" }] }; };
  const older = ctx.panel.refreshSources(true); // started at first_ready
  api.sources = async () => ({ sources: [{ id: 1, name: "first.log" }, { id: 2, name: "rest.log" }] });
  ctx.panel.selected = new Set([1, 2]);
  const newer = ctx.panel.refreshSources(); // started at done: the complete list
  await newer;
  assert.deepEqual(published.at(-1), ["first.log", "rest.log"]);
  slowOld.resolve();
  await older;
  assert.deepEqual(ctx.panel.sources.map((s) => s.name), ["first.log", "rest.log"], "the old answer did not replace the list");
  assert.deepEqual([...ctx.panel.selected].sort(), [1, 2], "the selection of rest.log is kept");
  assert.equal(published.length, 1);
  // an old failure is silent too
  const slowFail = deferred();
  api.sources = async () => { await slowFail.promise; throw new ApiError(500, "boom", "old broke"); };
  const failing = ctx.panel.refreshSources(true);
  api.sources = async () => ({ sources: [{ id: 2, name: "rest.log" }] });
  await ctx.panel.refreshSources(true);
  slowFail.resolve();
  await failing;
  assert.deepEqual(ctx.panel.sources.map((s) => s.name), ["rest.log"]);
  assert.equal(ctx.calls.messages.some(([t]) => t === "old broke"), false);
});
