// CaseFiles on a tiny fake DOM, and SourcesPanel.runCollection with a fake api and EventSource.
import test from "node:test";
import assert from "node:assert/strict";

class FakeNode {
  constructor(tag) {
    this.tag = tag;
    this.children = [];
    this.attrs = {};
    this.listeners = {};
    this.className = "";
    this.hidden = false;
    this.disabled = false;
    this.value = "";
    this._text = "";
    this.style = {};
  }

  get textContent() {
    return this._text + this.children.map((c) => c.textContent).join("");
  }

  set textContent(value) {
    this._text = String(value);
    this.children = [];
  }

  appendChild(child) {
    this.children.push(child);
    return child;
  }

  setAttribute(name, value) { this.attrs[name] = String(value); }
  getAttribute(name) { return name in this.attrs ? this.attrs[name] : null; }
  get ownerDocument() { return globalThis.document; }
  removeAttribute(name) { delete this.attrs[name]; }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  focus() {}
  remove() {}
  get classList() {
    return { toggle() {}, add() {} };
  }

  fire(type, event = {}) {
    for (const fn of this.listeners[type] || []) {
      fn({ preventDefault() {}, stopPropagation() {}, key: "", ...event });
    }
  }

  find(predicate, out = []) {
    if (predicate(this)) {
      out.push(this);
    }
    for (const child of this.children) {
      child.find(predicate, out);
    }
    return out;
  }
}

globalThis.document = {
  createElement: (tag) => new FakeNode(tag),
  createTextNode: (text) => Object.assign(new FakeNode("#text"), { _text: text }),
};

const { CaseFiles } = await import("../../src/dmslog/web/js/case-files.js");
const { SourcesPanel } = await import("../../src/dmslog/web/js/sources.js");
const { api, ApiError } = await import("../../src/dmslog/web/js/api.js");

const GUID = "0A1B2C3D-0000-1111-2222-333344445555";
const ENT = `/data/ERS_CL_${GUID}_20260101120000_Enterprise.zip`;
const SENS = `/data/ERS_CL_${GUID}_20260101120000_Sensitive_Enterprise.zip`;

function setup(fs) {
  const host = new FakeNode("div");
  const nameInput = new FakeNode("input");
  const messages = [];
  const files = new CaseFiles({
    host, nameInput, fs, storage: null, onMessage: (text, isError) => messages.push([text, isError]),
  });
  return { host, nameInput, messages, files };
}

const byClass = (host, cls) => host.find((n) => n.className.split(" ").includes(cls));

test("Create is disabled with an empty list and enabled after adding a pasted path with Enter", () => {
  const { host, files } = setup({});
  const create = byClass(host, "cf-create")[0];
  assert.equal(create.disabled, true);
  assert.equal(create.attrs.type, undefined);
  assert.equal(create.type, "submit");
  const input = byClass(host, "cf-path")[0];
  input.value = ENT;
  input.fire("keydown", { key: "Enter" });
  assert.equal(files.state.items.length, 1);
  assert.equal(input.value, "");
  assert.equal(create.disabled, false);
  assert.match(create.textContent, /1 file/);
});

test("file names are rendered as text, with kind and a Remove button per item", () => {
  const { host, files } = setup({});
  files.state.add([{ path: SENS, size: 2048 }, "/x/<img src=x onerror=1>.zip"]);
  const items = byClass(host, "cf-item");
  assert.equal(items.length, 2);
  assert.match(items[0].textContent, /Sensitive/);
  assert.match(items[0].textContent, /2\.0 KB/);
  assert.match(items[1].textContent, /<img src=x onerror=1>\.zip/);
  assert.equal(items[1].find((n) => n.tag === "img").length, 0);
  byClass(items[0], "cf-remove")[0].fire("click");
  assert.equal(files.state.items.length, 1);
});

test("Browse with a native dialog adds the paths and shows errors per file", async () => {
  const fs = {
    capabilities: async () => ({ native: { available: true } }),
    pick: async () => ({ paths: [ENT, SENS], errors: [{ path: "/q/bad.zip", message: "Not a ZIP file" }], cancelled: false }),
    cancelPick: async () => null,
  };
  const { host, files } = setup(fs);
  await files.browse();
  assert.equal(files.state.items.length, 2);
  assert.match(byClass(host, "cf-notices")[0].textContent, /bad\.zip: Not a ZIP file/);
  await files.browse(); // the same files again: duplicates are ignored with a warning
  assert.equal(files.state.items.length, 2);
  assert.match(byClass(host, "cf-notices")[0].textContent, /already in the list/);
});

test("submit needs a name, then creates through the deps", async () => {
  const { files, nameInput, messages } = setup({});
  files.state.add([ENT]);
  const calls = [];
  const deps = {
    createCase: async (n) => { calls.push(n); return { id: n }; },
    selectCase: async () => {},
    openPath: async () => ({ status: "ok" }),
  };
  assert.equal(await files.submit(deps), null);
  assert.equal(nameInput.attrs["aria-invalid"], "true"); // the error sits with the field, not in the status bar
  assert.equal(messages.length, 0);
  nameInput.value = "Case A";
  const result = await files.submit(deps);
  assert.equal(result.state, "ok");
  assert.deepEqual(calls, ["Case A"]);
  assert.equal(nameInput.value, "");
  assert.equal(nameInput.attrs["aria-invalid"], undefined);
});

// -- SourcesPanel.runCollection ------------------------------------------------------------

class FakeEventSource {
  static instances = [];
  constructor() {
    this.listeners = {};
    this.readyState = 1;
    FakeEventSource.instances.push(this);
  }

  addEventListener(type, fn) { this.listeners[type] = fn; }
  close() { this.closed = true; }
  emit(type, data) { this.listeners[type]({ data: JSON.stringify(data) }); }
}

function panelSetup() {
  const refs = {
    caseSelect: new FakeNode("select"), newCaseForm: new FakeNode("form"), newCaseName: new FakeNode("input"),
    openForm: new FakeNode("form"), pathInput: new FakeNode("input"), scopeSelect: Object.assign(new FakeNode("select"), { value: "default" }),
    progressBar: new FakeNode("progress"), progressText: new FakeNode("div"), sourceList: new FakeNode("ul"),
  };
  const messages = [];
  const panel = new SourcesPanel(refs, {
    onCase() {}, onSources() {}, onSelection() {}, onDone() {}, onMessage: (t, e) => messages.push([t, e]),
  });
  panel.caseId = "C";
  return { panel, messages };
}

test("runCollection waits for the done event of its own job, ignoring a replayed one", async () => {
  globalThis.EventSource = FakeEventSource;
  FakeEventSource.instances = [];
  api.sources = async () => ({ sources: [] });
  api.progressUrl = () => "/p";
  api.addCollection = async () => ({ job_id: "new" });
  const { panel } = panelSetup();
  const pending = panel.runCollection(ENT, "default");
  await new Promise((r) => setTimeout(r, 0));
  const stream = FakeEventSource.instances[0];
  stream.emit("done", { job_id: "old", status: "ok" }); // replay of the previous job
  let settled = false;
  pending.then(() => { settled = true; });
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(settled, false);
  stream.emit("done", { job_id: "new", status: "ok" });
  assert.deepEqual(await pending, { status: "ok" });
});

test("runCollection reports an error job as failed and a 409 as refused", async () => {
  globalThis.EventSource = FakeEventSource;
  FakeEventSource.instances = [];
  api.sources = async () => ({ sources: [] });
  api.progressUrl = () => "/p";
  api.addCollection = async () => ({ job_id: "j1" });
  let { panel } = panelSetup();
  let pending = panel.runCollection(ENT, "default");
  await new Promise((r) => setTimeout(r, 0));
  FakeEventSource.instances[0].emit("done", { job_id: "j1", status: "error", message: "Corrupt archive", code: "unsafe_archive" });
  assert.deepEqual(await pending, { status: "failed", code: "unsafe_archive", message: "Corrupt archive" });

  api.addCollection = async () => { throw Object.assign(new Error("server text"), { status: 409, code: "duplicate_collection" }); };
  ({ panel } = panelSetup());
  const refused = await panel.runCollection(SENS, "default");
  assert.equal(refused.status, "refused");
  assert.match(refused.message, /already has this ERS file from another location/);
  assert.equal(panel.waiter, null);
});

// -- review fixes -------------------------------------------------------------------------

test("picker_busy keeps a Cancel the open dialog action until it is used", async () => {
  let cancelled = 0;
  const fs = {
    capabilities: async () => ({ native: { available: true } }),
    pick: async () => { throw Object.assign(new Error("busy"), { code: "picker_busy" }); },
    cancelPick: async () => { cancelled += 1; return null; },
  };
  const { host, files } = setup(fs);
  await files.browse();
  const box = byClass(host, "cf-wait")[0];
  assert.equal(box.hidden, false);
  assert.match(box.textContent, /already open/);
  const cancel = box.find((n) => n.tag === "button")[0];
  assert.equal(cancel.textContent, "Cancel the open dialog");
  await cancel.listeners.click[0]();
  assert.equal(cancelled, 1);
  assert.equal(box.hidden, true);
});

test("switching case or opening a collection is blocked while the files of a new case open", async () => {
  api.cases = async () => ({ cases: [] });
  api.sources = async () => ({ sources: [] });
  api.createCase = async () => ({ id: "NEW" });
  const { panel, messages } = panelSetup();
  panel.caseId = null;
  let release;
  const gate = new Promise((r) => { release = r; });
  const { files } = setup({});
  files.state.add([ENT]);
  files.nameInput.value = "N";
  panel.caseFiles = files;
  panel.runCollection = async () => { await gate; return { status: "ok" }; };
  const running = panel.createCase();
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(panel.queueBusy, true);
  assert.equal(panel.refs.caseSelect.attrs["aria-disabled"], "true");
  panel.refs.caseSelect.value = "OTHER";
  panel.refs.caseSelect.fire("change");
  assert.equal(panel.caseId, "NEW");
  assert.equal(panel.refs.caseSelect.value, "NEW");
  await panel.openCollection();
  assert.match(messages.at(-1)[0], /still opening/);
  release();
  await running;
  assert.equal(panel.queueBusy, false);
  assert.equal(panel.refs.caseSelect.attrs["aria-disabled"], undefined);
});

test("a case change cancels the waiting run with a clear error, without a pending promise", async () => {
  globalThis.EventSource = FakeEventSource;
  FakeEventSource.instances = [];
  api.sources = async () => ({ sources: [] });
  api.progressUrl = () => "/p";
  api.addCollection = async () => ({ job_id: "j" });
  const { panel } = panelSetup();
  const pending = panel.runCollection(ENT, "default", "C");
  await new Promise((r) => setTimeout(r, 0));
  await panel.selectCase("OTHER");
  const result = await pending;
  assert.equal(result.status, "failed");
  assert.match(result.message, /case changed/);
  assert.equal(panel.waiter, null);
  const late = await panel.runCollection(ENT, "default", "C"); // the bound case is no longer current
  assert.match(late.message, /case changed/);
});

// -- progress stream closing ---------------------------------------------------------------

test("a definitively closed stream releases the waiting run and the queue with a clear error", async () => {
  globalThis.EventSource = FakeEventSource;
  FakeEventSource.instances = [];
  api.sources = async () => ({ sources: [] });
  api.progressUrl = () => "/p";
  api.addCollection = async () => ({ job_id: "j" });
  const { panel } = panelSetup();
  const pending = panel.runCollection(ENT, "default", "C");
  await new Promise((r) => setTimeout(r, 0));
  const stream = FakeEventSource.instances[0];
  stream.readyState = 2;
  stream.emit("error", {});
  const result = await pending;
  assert.equal(result.status, "failed");
  assert.match(result.message, /connection .* closed/);
  assert.equal(panel.waiter, null);
});

test("CONNECTING keeps waiting and a later done resolves", async () => {
  globalThis.EventSource = FakeEventSource;
  FakeEventSource.instances = [];
  api.sources = async () => ({ sources: [] });
  api.progressUrl = () => "/p";
  api.addCollection = async () => ({ job_id: "j" });
  const { panel } = panelSetup();
  const pending = panel.runCollection(ENT, "default", "C");
  await new Promise((r) => setTimeout(r, 0));
  const stream = FakeEventSource.instances[0];
  stream.readyState = 0;
  stream.emit("error", {});
  let settled = false;
  pending.then(() => { settled = true; });
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(settled, false);
  stream.readyState = 1;
  stream.emit("done", { job_id: "j", status: "ok" });
  assert.deepEqual(await pending, { status: "ok" });
});

test("an old stream closing does not affect the waiter of the current job", async () => {
  globalThis.EventSource = FakeEventSource;
  FakeEventSource.instances = [];
  api.sources = async () => ({ sources: [] });
  api.progressUrl = () => "/p";
  api.addCollection = async () => ({ job_id: "j" });
  const { panel } = panelSetup();
  panel.watchProgress("C"); // an older stream
  const old = FakeEventSource.instances[0];
  const pending = panel.runCollection(ENT, "default", "C"); // closes the old one, opens a new one
  await new Promise((r) => setTimeout(r, 0));
  old.readyState = 2;
  old.emit("error", {});
  let settled = false;
  pending.then(() => { settled = true; });
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(settled, false);
  assert.notEqual(panel.waiter, null);
  FakeEventSource.instances.at(-1).emit("done", { job_id: "j", status: "ok" });
  assert.deepEqual(await pending, { status: "ok" });
});

test("typing in the case name clears its error; a refused name shows under the field", async () => {
  const { files, nameInput, messages } = setup({});
  files.state.add([ENT]);
  assert.equal(await files.submit({}), null); // empty name
  assert.equal(nameInput.attrs["aria-invalid"], "true");
  nameInput.fire("input");
  assert.equal(nameInput.attrs["aria-invalid"], undefined);
  nameInput.value = "Dup";
  const deps = { createCase: async () => { throw new Error("A case with this name already exists."); }, selectCase: async () => {}, openPath: async () => ({ status: "ok" }) };
  await files.submit(deps);
  assert.equal(nameInput.attrs["aria-invalid"], "true");
  assert.equal(messages.length, 0);
  nameInput.fire("input");
  assert.equal(nameInput.attrs["aria-invalid"], undefined);
});

test("a failure to open the created case is a message, not a name error", async () => {
  const { files, nameInput, messages } = setup({});
  files.state.add([ENT]);
  nameInput.value = "Fine";
  const deps = { createCase: async (n) => ({ id: n }), selectCase: async () => { throw new Error("could not open it"); }, openPath: async () => ({ status: "ok" }) };
  await files.submit(deps);
  assert.equal(nameInput.attrs["aria-invalid"], undefined);
  assert.deepEqual(messages.at(-1), ["could not open it", true]);
});

test("typing in the path field clears its error", async () => {
  api.addCollection = async () => { throw new ApiError(400, "path_not_found", "Path does not exist: /x"); };
  api.progressUrl = () => "/p";
  globalThis.EventSource = undefined;
  const { panel } = panelSetup();
  panel.refs.pathInput.value = "/x";
  await panel.openCollection();
  assert.equal(panel.refs.pathInput.attrs["aria-invalid"], "true");
  panel.refs.pathInput.fire("input");
  assert.equal(panel.refs.pathInput.attrs["aria-invalid"], undefined);
});
