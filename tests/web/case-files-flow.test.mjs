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
  panel.watchProgress("X"); // an older stream, of another case
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

test("UI-14: when the folder browser cannot open, Browse says so and is usable again", async () => {
  // Native dialog unavailable -> the built-in browser; this fake document has no body, so opening it rejects.
  const fs = { capabilities: async () => ({ native: { available: false } }), pick: async () => ({}), cancelPick: async () => null };
  const { host, files } = setup(fs);
  const browse = byClass(host, "cf-browse")[0];
  await files.browse();
  assert.match(byClass(host, "cf-notices")[0].textContent, /folder browser could not be loaded/);
  assert.equal(files.waiting, false);
  assert.equal(browse.disabled, false);
});

test("the indexing progress stream stops after 3 failures in a row and Retry opens it again once", async () => {
  globalThis.EventSource = FakeEventSource;
  FakeEventSource.instances = [];
  api.progressUrl = () => "/p";
  const stopped = [];
  const refs = {
    caseSelect: new FakeNode("select"), newCaseForm: new FakeNode("form"), newCaseName: new FakeNode("input"),
    openForm: new FakeNode("form"), pathInput: new FakeNode("input"), scopeSelect: Object.assign(new FakeNode("select"), { value: "default" }),
    progressBar: new FakeNode("progress"), progressText: new FakeNode("div"), sourceList: new FakeNode("ul"),
  };
  const panel = new SourcesPanel(refs, {
    onCase() {}, onSources() {}, onSelection() {}, onDone() {}, onMessage() {}, onStreamStopped: () => stopped.push(1),
  });
  panel.caseId = "C";
  assert.equal(panel.resumeProgress(), false); // nothing stopped yet
  panel.watchProgress("C");
  const stream = FakeEventSource.instances[0];
  stream.readyState = 0; // CONNECTING: the browser keeps retrying
  const fail = () => stream.listeners.error({});
  fail();
  fail();
  assert.equal(stream.closed, undefined);
  stream.listeners.open({}); // reconnected: the count starts over
  fail();
  fail();
  assert.equal(stream.closed, undefined);
  fail();
  assert.equal(stream.closed, true);
  assert.deepEqual(stopped, [1]);
  assert.equal(panel.events, null);
  fail(); // a late error of the closed stream
  assert.deepEqual(stopped, [1]);
  assert.equal(panel.resumeProgress(), true);
  assert.equal(FakeEventSource.instances.length, 2);
  assert.equal(panel.resumeProgress(), false); // running again: nothing to resume
  assert.equal(FakeEventSource.instances.length, 2);
});

// ---- file A is indexing; the progress stream can be lost, Retried, file B opened and refused (409), A's done arrives ----

function permutations(items) {
  if (items.length <= 1) return [items];
  return items.flatMap((item, i) => permutations([...items.slice(0, i), ...items.slice(i + 1)]).map((rest) => [item, ...rest]));
}

async function scenario(order, secondFile) {
  globalThis.EventSource = FakeEventSource;
  FakeEventSource.instances = [];
  api.progressUrl = () => "/p";
  api.sources = async () => ({ sources: [] });
  const counts = { done: 0, stopped: 0 };
  const refs = {
    caseSelect: new FakeNode("select"), newCaseForm: new FakeNode("form"), newCaseName: new FakeNode("input"),
    openForm: new FakeNode("form"), pathInput: Object.assign(new FakeNode("input"), { value: "/a" }),
    scopeSelect: Object.assign(new FakeNode("select"), { value: "default" }),
    progressBar: new FakeNode("progress"), progressText: new FakeNode("div"), sourceList: new FakeNode("ul"),
  };
  const panel = new SourcesPanel(refs, {
    onCase() {}, onSources() {}, onSelection() {}, onDone: () => { counts.done += 1; }, onMessage() {},
    onStreamStopped: () => { counts.stopped += 1; },
  });
  panel.caseId = "C";
  api.addCollection = async () => ({ job_id: "A" });
  await panel.openCollection(); // A started: followed
  assert.equal(panel.events !== null, true);
  let refuse = null;
  let second = null;
  let deferredDone = false;
  const live = () => (panel.events !== null && !panel.events.closed ? panel.events : null);
  const deliverDone = () => {
    const stream = live();
    if (stream) {
      stream.emit("done", { job_id: "A", status: "ok", records: 1 });
      return true;
    }
    return false;
  };
  for (const step of order) {
    if (step === "L") {
      const stream = live();
      if (stream) {
        stream.readyState = 2;
        stream.listeners.error({});
      }
    } else if (step === "R") {
      panel.resumeProgress();
    } else if (step === "B") {
      api.addCollection = () => new Promise((_, reject) => { refuse = reject; });
      refs.pathInput.value = "/b";
      second = secondFile === "run" ? panel.runCollection(ENT, "default", "C") : panel.openCollection();
    } else if (step === "X") {
      await new Promise((r) => setTimeout(r, 0));
      refuse(Object.assign(new Error("busy"), { status: 409, code: "job_running" }));
      await second;
    } else if (step === "D") {
      if (!deliverDone()) deferredDone = true;
    }
    await new Promise((r) => setTimeout(r, 0));
  }
  if (deferredDone) deliverDone(); // the server replays it to whoever listens next
  await new Promise((r) => setTimeout(r, 0));
  return { panel, counts, live };
}

for (const secondFile of ["run", "open"]) {
  test(`file A is never lost, in any order of [stream lost, Retry, B opens, B refused 409, A done] (B via ${secondFile})`, async () => {
    const orders = permutations(["L", "R", "B", "X", "D"]).filter((o) => o.indexOf("B") < o.indexOf("X"));
    assert.equal(orders.length, 60);
    for (const order of orders) {
      const { panel, counts, live } = await scenario(order, secondFile);
      const label = order.join(" ");
      if (panel.finished.has("A")) {
        assert.equal(counts.done, 1, label + ": A's end handled exactly once");
        assert.equal(panel.jobs.size, 0, label + ": nothing left to follow");
        assert.equal(panel.events, null, label + ": the stream is closed when no job is left");
      } else {
        const followed = live() !== null && [...panel.jobs].some((job) => job.id === "A");
        assert.ok(followed || (panel.progressStopped && panel.resumeProgress() === true),
          label + ": A is neither followed, nor done, nor recoverable");
      }
    }
  });
}

test("B refused while A's stream is healthy neither closes it nor loses A's end; the done is handled once", async () => {
  const { panel, counts } = await scenario(["B", "D", "X"], "run");
  assert.equal(counts.done, 1);
  assert.equal(panel.finished.has("A"), true);
  assert.equal(panel.events, null);
});

test("A's done that arrived while B waited for its id is processed when B is refused (refresh and onDone run)", async () => {
  globalThis.EventSource = FakeEventSource;
  FakeEventSource.instances = [];
  api.progressUrl = () => "/p";
  let refreshes = 0;
  api.sources = async () => { refreshes += 1; return { sources: [] }; };
  let done = 0;
  const refs = {
    caseSelect: new FakeNode("select"), newCaseForm: new FakeNode("form"), newCaseName: new FakeNode("input"),
    openForm: new FakeNode("form"), pathInput: Object.assign(new FakeNode("input"), { value: "/a" }),
    scopeSelect: Object.assign(new FakeNode("select"), { value: "default" }),
    progressBar: new FakeNode("progress"), progressText: new FakeNode("div"), sourceList: new FakeNode("ul"),
  };
  const panel = new SourcesPanel(refs, { onCase() {}, onSources() {}, onSelection() {}, onDone: () => { done += 1; }, onMessage() {} });
  panel.caseId = "C";
  api.addCollection = async () => ({ job_id: "A" });
  await panel.openCollection();
  let refuse = null;
  api.addCollection = () => new Promise((_, reject) => { refuse = reject; });
  const b = panel.runCollection(ENT, "default", "C");
  await new Promise((r) => setTimeout(r, 0));
  panel.events.emit("done", { job_id: "A", status: "ok", records: 3 }); // B has no id yet: kept
  assert.equal(done, 0);
  refuse(Object.assign(new Error("busy"), { status: 409, code: "job_running" }));
  assert.equal((await b).status, "refused");
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(done, 1);
  assert.ok(refreshes >= 1);
});

test("a done of an older job (a replay) while a file waits is ignored; the file's own done resolves it", async () => {
  globalThis.EventSource = FakeEventSource;
  FakeEventSource.instances = [];
  api.progressUrl = () => "/p";
  api.sources = async () => ({ sources: [] });
  api.addCollection = async () => ({ job_id: "new" });
  const { panel } = panelSetup();
  const pending = panel.runCollection(ENT, "default");
  await new Promise((r) => setTimeout(r, 0));
  panel.events.emit("done", { job_id: "old", status: "ok" });
  assert.equal(panel.finished.has("old"), false);
  panel.events.emit("done", { job_id: "new", status: "ok" });
  assert.deepEqual(await pending, { status: "ok" });
});

function jobsRig() {
  globalThis.EventSource = FakeEventSource;
  FakeEventSource.instances = [];
  api.progressUrl = () => "/p";
  api.sources = async () => ({ sources: [] });
  const counts = { done: 0 };
  const refs = {
    caseSelect: new FakeNode("select"), newCaseForm: new FakeNode("form"), newCaseName: new FakeNode("input"),
    openForm: new FakeNode("form"), pathInput: Object.assign(new FakeNode("input"), { value: "/a" }),
    scopeSelect: Object.assign(new FakeNode("select"), { value: "default" }),
    progressBar: new FakeNode("progress"), progressText: new FakeNode("div"), sourceList: new FakeNode("ul"),
  };
  const panel = new SourcesPanel(refs, { onCase() {}, onSources() {}, onSelection() {}, onDone: () => { counts.done += 1; }, onMessage() {} });
  panel.caseId = "C";
  return { panel, counts };
}
const tick = () => new Promise((r) => setTimeout(r, 0));

test("a second file reuses the live progress stream (it carries every job of the case)", async () => {
  const { panel } = jobsRig();
  api.addCollection = async () => ({ job_id: "A" });
  await panel.openCollection();
  const stream = panel.events;
  api.addCollection = () => new Promise(() => {});
  panel.runCollection(ENT, "default", "C");
  await tick();
  assert.equal(FakeEventSource.instances.length, 1);
  assert.equal(panel.events, stream);
});

test("while B waits with its id known, A's done ends A only and B still waits for its own", async () => {
  const { panel, counts } = jobsRig();
  api.addCollection = async () => ({ job_id: "A" });
  await panel.openCollection();
  api.addCollection = async () => ({ job_id: "B" });
  const b = panel.runCollection(ENT, "default", "C");
  await tick();
  let settled = false;
  b.then(() => { settled = true; });
  panel.events.emit("done", { job_id: "A", status: "ok", records: 1 });
  await tick();
  assert.equal(counts.done, 1);
  assert.equal(settled, false); // A's end did not resolve B
  assert.notEqual(panel.events, null); // and B's job is still followed
  panel.events.emit("done", { job_id: "B", status: "ok", records: 2 });
  assert.deepEqual(await b, { status: "ok" });
  assert.equal(counts.done, 2);
});

test("the same done seen twice (two streams, a replay) is handled once", async () => {
  const { panel, counts } = jobsRig();
  api.addCollection = async () => ({ job_id: "A" });
  await panel.openCollection();
  const stream = panel.events;
  stream.emit("done", { job_id: "A", status: "ok", records: 1 });
  stream.emit("done", { job_id: "A", status: "ok", records: 1 });
  await tick();
  assert.equal(counts.done, 1);
});

test("A's done kept while B had no id is processed when B's answer arrives with its id (B accepted)", async () => {
  const { panel, counts } = jobsRig();
  api.addCollection = async () => ({ job_id: "A" });
  await panel.openCollection();
  let answer = null;
  api.addCollection = () => new Promise((resolve) => { answer = resolve; });
  const b = panel.runCollection(ENT, "default", "C");
  await tick();
  panel.events.emit("done", { job_id: "A", status: "ok", records: 1 }); // kept: whose is it?
  assert.equal(counts.done, 0);
  answer({ job_id: "B" });
  await tick();
  assert.equal(counts.done, 1); // it was A's
  panel.events.emit("done", { job_id: "B", status: "ok", records: 1 });
  assert.deepEqual(await b, { status: "ok" });
});
