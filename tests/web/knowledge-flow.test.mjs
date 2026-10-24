// Drives KnowledgeSection with a DOM-less fake and fake API answers.
import test from "node:test";
import assert from "node:assert/strict";

function node(tag) {
  return {
    tag, className: "", hidden: false, title: "", type: "", children: [], listeners: {}, attrs: {},
    _text: "",
    get textContent() { return this._text + this.children.map((c) => c.textContent).join(""); },
    set textContent(value) { this._text = value; this.children = []; },
    appendChild(child) { this.children.push(child); return child; },
    append(...items) { for (const item of items) { this.children.push(item); } },
    setAttribute(name, value) { this.attrs[name] = value; },
    addEventListener(name, fn) { this.listeners[name] = fn; },
  };
}

globalThis.document = {
  createElement: node,
  querySelector: () => ({}),
  head: { appendChild() {} },
};

const { KnowledgeSection } = await import("../../src/dmslog/web/js/knowledge.js");
const { api } = await import("../../src/dmslog/web/js/api.js");

function setup() {
  const host = node("section");
  const state = { caseId: "c1", errors: [] };
  const section = new KnowledgeSection(host, {
    getCase: () => state.caseId,
    getFamily: () => "AWFM",
    onError: (err) => state.errors.push(err),
  });
  return { host, state, section };
}

const TEMPLATE = {
  hint: { text: "Check the CRS.", params: ["WaitForCRSCallback"], note: "n.md" },
  params: [{ name: "WaitForCRSCallback", default: "35", description: "Seconds." }],
  adapter: { title: "Workforce Management Interface" },
  vault_note: "n.md",
};

test("shows hint, parameters and the vault note", async () => {
  const { host, section } = setup();
  api.knowledge = async () => TEMPLATE;
  api.knowledgeError = async () => { throw Object.assign(new Error("x"), { status: 404 }); };
  await section.show({ id: 1, tpl_id: 2, tpl: "WFM-080", fields: {}, source_id: 1 });
  assert.equal(host.hidden, false);
  const text = host.textContent;
  assert.match(text, /Check the CRS\./);
  assert.match(text, /WaitForCRSCallback/);
  assert.match(text, /n\.md/);
});

test("an out-of-vault error code shows the badge", async () => {
  const { host, section } = setup();
  api.knowledge = async () => TEMPLATE;
  api.knowledgeError = async (c, f, code) => ({
    code, severity: 1, reason: "Example reason", details: null, source: "ers", out_of_vault: true,
  });
  await section.show({ id: 2, tpl_id: 2, tpl: "GEN-ERR-01", fields: { codes: ["ExampleCodeOne"] }, source_id: 1 });
  assert.match(host.textContent, /Out of vault/);
  assert.match(host.textContent, /ExampleCodeOne: Critical \(ERS configuration\)/);
});

test("stays hidden when nothing is known (404s)", async () => {
  const { host, section } = setup();
  api.knowledge = async () => ({ hint: null, params: [], adapter: null, vault_note: null });
  api.knowledgeError = async () => { throw Object.assign(new Error("x"), { status: 404 }); };
  await section.show({ id: 3, tpl_id: 2, tpl: "WFM-011", fields: { code: "Nope" }, source_id: 1 });
  assert.equal(host.hidden, true);
});

test("answers are cached per case and template", async () => {
  const { section } = setup();
  let calls = 0;
  api.knowledge = async () => { calls += 1; return TEMPLATE; };
  const entry = { id: 4, tpl_id: 2, tpl: "WFM-080", fields: {}, source_id: 1 };
  await section.show(entry);
  await section.show({ ...entry, id: 5 });
  assert.equal(calls, 1);
  section.reset();
  await section.show(entry);
  assert.equal(calls, 2);
});

test("a stale answer is dropped when the case changed", async () => {
  const { host, state, section } = setup();
  let release;
  api.knowledge = () => new Promise((resolve) => { release = () => resolve(TEMPLATE); });
  const pending = section.show({ id: 6, tpl_id: 2, tpl: "WFM-080", fields: {}, source_id: 1 });
  state.caseId = "c2";
  release();
  await pending;
  assert.equal(host.hidden, true);
});

test("non-404 failures are reported and hide the section", async () => {
  const { host, state, section } = setup();
  api.knowledge = async () => { throw Object.assign(new Error("boom"), { status: 500 }); };
  await section.show({ id: 7, tpl_id: 2, tpl: "WFM-080", fields: {}, source_id: 1 });
  assert.equal(host.hidden, true);
  assert.equal(state.errors.length, 1);
});

const SERVICE_TEMPLATE = {
  hint: null, params: [], adapter: null, vault_note: null,
  service: {
    process: "DMS_ExampleService", family: "DMSEXAMPLE", group: "Example group", title: "Example Service",
    summary: "Example summary.", note: "Example/Service.md", section: null, install_dir: "C:\\Example",
    monitored: ["cpu"], counters: ["ExampleCounter"], usage_events: 0,
    references: [{ doc: "Example doc", note: "Example/Doc.md", section: "S1", mentions: 1 }],
    related: [{ process: "DMS_ExampleOther", via: "job_id" }], keys: ["job_id"], in_vault: false,
  },
};

function setupProcess(processes) {
  const host = node("section");
  const section = new KnowledgeSection(host, {
    getCase: () => "c1",
    getFamily: () => "DMSEXAMPLE",
    getProcess: (sourceId) => processes[sourceId] || "",
    onError: () => {},
  });
  return { host, section };
}

test("a service-only answer shows the Service block with text only", async () => {
  const { host, section } = setupProcess({ 1: "DMS_ExampleService" });
  let args;
  api.knowledge = async (...a) => { args = a; return SERVICE_TEMPLATE; };
  await section.show({ id: 10, tpl_id: 2, tpl: "RTL-001", fields: {}, source_id: 1 });
  assert.equal(host.hidden, false);
  assert.deepEqual({ ...args[2] }, { process: "DMS_ExampleService", streamFamily: "DMSEXAMPLE" });
  const text = host.textContent;
  assert.match(text, /Example Service/);
  assert.match(text, /DMS_ExampleOther \(job_id\)/);
  assert.match(text, /Example doc \u00b7 S1/);
  assert.match(text, /Not described in the vault/);
});

test("the cache is keyed by process as well as template", async () => {
  const { section } = setupProcess({ 1: "DMS_ExampleService", 2: "DMS_ExampleOther" });
  let calls = 0;
  api.knowledge = async () => { calls += 1; return SERVICE_TEMPLATE; };
  await section.show({ id: 11, tpl_id: 2, tpl: "RTL-001", fields: {}, source_id: 1 });
  await section.show({ id: 12, tpl_id: 2, tpl: "RTL-001", fields: {}, source_id: 1 });
  assert.equal(calls, 1);
  await section.show({ id: 13, tpl_id: 2, tpl: "RTL-001", fields: {}, source_id: 2 });
  assert.equal(calls, 2);
});
