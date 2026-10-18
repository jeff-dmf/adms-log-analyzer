// UI-20: the record detail in investigation order. Fake DOM, no wall-clock waits.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import { El, installGlobalDom, tick } from "./fixtures/fake-dom.mjs";

const doc = installGlobalDom();
globalThis.Element = El;
// The delegated listeners of the sections ask for the nearest `button[data-action]`.
El.prototype.closest = function closest(selector) {
  assert.equal(selector, "button[data-action]");
  for (let n = this; n; n = n.parent) {
    if (n.tagName === "BUTTON" && n.dataset.action !== undefined) return n;
  }
  return null;
};

const { Detail, adoptHeading, sectionHeading, setDetailPrefs } = await import("../../src/dmslog/web/js/detail.js");
const { PREFS_KEY, SECTION_DEFAULTS, createDetailPrefs, headerParts, parsePrefs, severityClass } = await import("../../src/dmslog/web/js/detail-model.js");
const { KnowledgeSection } = await import("../../src/dmslog/web/js/knowledge.js");
const { LinksSection } = await import("../../src/dmslog/web/js/links.js");
const { PayloadSection } = await import("../../src/dmslog/web/js/payload.js");
const { mountHighlights } = await import("../../src/dmslog/web/js/highlight-panel.js");
const { api } = await import("../../src/dmslog/web/js/api.js");
const { S } = await import("../../src/dmslog/web/js/strings.js");

const CSS = (name) => readFileSync(new URL("../../src/dmslog/web/css/" + name, import.meta.url), "utf8").replace(/\r\n/g, "\n");

function memoryStorage(initial = {}) {
  const data = { ...initial };
  return { data, getItem: (k) => (k in data ? data[k] : null), setItem: (k, v) => { data[k] = String(v); } };
}

const el = (tag = "div") => doc.createElement(tag);
const byClass = (root, cls) => root.all().filter((n) => n.className.split(/\s+/).includes(cls));

function freshPrefs(initial) {
  const storage = memoryStorage(initial);
  setDetailPrefs(createDetailPrefs(storage));
  return storage;
}

// ---- model -------------------------------------------------------------------------------------------------

test("prefs: defaults close only the payload and unknown or corrupt storage is ignored", () => {
  const prefs = createDetailPrefs(memoryStorage({ [PREFS_KEY]: "{not json" }));
  for (const key of Object.keys(SECTION_DEFAULTS)) {
    assert.equal(prefs.sectionOpen(key), key !== "payload");
  }
  assert.equal(prefs.wrap(), false);
  assert.deepEqual(parsePrefs(JSON.stringify({ sections: { knowledge: "no", evil: true, payload: true }, wrap: "yes" })),
    { sections: { payload: true }, wrap: false });
  assert.deepEqual(parsePrefs("[1]"), { sections: {}, wrap: false });
});

test("prefs: choices are remembered through the storage and a throwing storage does not break the detail", () => {
  const storage = memoryStorage();
  const prefs = createDetailPrefs(storage);
  prefs.setSectionOpen("links", false);
  prefs.setWrap(true);
  const again = createDetailPrefs(storage);
  assert.equal(again.sectionOpen("links"), false);
  assert.equal(again.wrap(), true);
  prefs.setSectionOpen("nope", false); // unknown keys are not stored
  assert.equal("nope" in JSON.parse(storage.data[PREFS_KEY]).sections, false);
  const broken = { getItem() { throw new Error("blocked"); }, setItem() { throw new Error("blocked"); } };
  const safe = createDetailPrefs(broken);
  safe.setWrap(true);
  assert.equal(safe.wrap(), true);
  assert.equal(createDetailPrefs(null).sectionOpen("knowledge"), true);
});

test("header parts: tid in hex, missing pid and tid stay null, severity class only for 1 to 5", () => {
  const opts = { formatTime: (ts) => "T" + ts, levelNames: S.levelNames };
  const parts = headerParts({ ts: 5, pid: 0, tid: 255, level: 3, sev: 2 }, { name: "a.log", sensitive: true, tag: "C2" }, opts);
  assert.deepEqual(parts, { time: "T5", level: "ERROR", sevClass: "dt-sev-2", source: "a.log", sensitive: true, pid: "0", tid: "0xff", tag: "C2" });
  const bare = headerParts({ ts: 5, pid: null, tid: undefined, level: 99, sev: 9 }, null, opts);
  assert.equal(bare.pid, null);
  assert.equal(bare.tid, null);
  assert.equal(bare.level, "");
  assert.equal(bare.sevClass, "");
  assert.equal(severityClass(0), "");
});

// ---- detail ------------------------------------------------------------------------------------------------

function detailSetup({ bookmarked = false, info = { name: "src.log", sensitive: true, tag: "C2" } } = {}) {
  freshPrefs();
  const refs = {};
  for (const name of ["empty", "meta", "text", "actions", "contextButton", "contextClose", "contextSame", "contextBox", "contextList",
    "copyButton", "compareButton", "bookmarkButton", "highlightButton", "copyStatus", "fields", "template"]) {
    refs[name] = el(name.endsWith("Button") || name === "template" ? "button" : "div");
  }
  const state = { bookmarked, toggled: [], compareFor: [] };
  const detail = new Detail(refs, {
    getCase: () => "c1",
    getSourceName: (id) => "name" + id,
    getSourceInfo: () => info,
    getCompareLabel: (entry) => { state.compareFor.push(entry ? entry.id : null); return entry ? "Compare with " + entry.id : S.cmpButton; },
    onError: (err) => { throw err; },
    isBookmarked: () => state.bookmarked,
    onBookmarkToggle: (id) => state.toggled.push(id),
  });
  return { detail, refs, state };
}

const ENTRY = { id: 7, ts: 1000, pid: 4321, tid: 4660, level: 3, sev: 2, source_id: 1, text: "line one\nline two", fields: {}, keys: [] };

test("empty state: one sentence, no text box; a record replaces it", () => {
  const { detail, refs } = detailSetup();
  assert.equal(refs.empty.textContent, "Select a record to see its full text, meaning and links.");
  assert.equal(refs.empty.hidden, false);
  assert.equal(refs.text.hidden, true);
  assert.equal(refs.actions.hidden, true);
  detail.entry = ENTRY;
  detail.renderEntry(ENTRY);
  assert.equal(refs.empty.hidden, true);
  assert.equal(refs.text.hidden, false);
  assert.equal(refs.actions.hidden, false);
  detail.clear();
  assert.equal(refs.empty.hidden, false);
  assert.equal(refs.meta.textContent, "");
});

test("header: time, level badge in the severity class, source with sensitive badge, PID, TID and collection as separate facts", () => {
  const { detail, refs } = detailSetup();
  detail.entry = ENTRY;
  detail.renderEntry(ENTRY);
  const meta = refs.meta;
  assert.equal(byClass(meta, "dt-time").length, 1);
  const level = byClass(meta, "dt-level")[0];
  assert.equal(level.textContent, "ERROR");
  assert.ok(level.className.split(" ").includes("dt-sev-2"));
  assert.ok(level.className.split(" ").includes("ui-badge"));
  assert.equal(byClass(meta, "dt-source")[0].textContent, "src.log");
  assert.equal(byClass(meta, "dt-sensitive")[0].textContent, "sensitive");
  assert.deepEqual(byClass(meta, "dt-fact").map((n) => n.textContent), ["PID 4321", "TID 0x1234", "Collection C2"]);
  assert.equal(meta.textContent.includes(" - "), false); // no run-on text with dashes
});

test("header: no sensitive badge, no collection and no pid/tid when the record does not carry them", () => {
  const { detail, refs } = detailSetup({ info: { name: "plain.log", sensitive: false, tag: "" } });
  const entry = { ...ENTRY, pid: null, tid: null };
  detail.entry = entry;
  detail.renderEntry(entry);
  assert.equal(byClass(refs.meta, "dt-sensitive").length, 0);
  assert.equal(byClass(refs.meta, "dt-fact").length, 0);
});

test("header text is placed as text, never as markup", () => {
  const { detail, refs } = detailSetup({ info: { name: "<img src=x onerror=alert(1)>", sensitive: false, tag: "" } });
  detail.entry = ENTRY;
  detail.renderEntry(ENTRY);
  assert.equal(byClass(refs.meta, "dt-source")[0].textContent, "<img src=x onerror=alert(1)>");
  assert.equal(refs.meta.all().some((n) => n.tagName === "IMG"), false);
});

test("actions: every ghost button is icon plus a short label, the compare label follows the picked record", () => {
  const { detail, refs } = detailSetup();
  const label = (button) => byClass(button, "dt-label")[0].textContent;
  const icon = (button) => byClass(button, "dt-icon")[0];
  assert.equal(label(refs.contextButton), "Context ▾");
  assert.equal(label(refs.copyButton), "Copy");
  assert.equal(label(refs.bookmarkButton), "Bookmark");
  assert.equal(label(refs.highlightButton), "Highlight ▾");
  assert.equal(label(refs.compareButton), S.cmpButton);
  assert.equal(icon(refs.copyButton).attrs["aria-hidden"], "true");
  detail.entry = ENTRY;
  detail.renderEntry(ENTRY);
  assert.equal(label(refs.compareButton), "Compare with 7");
});

test("bookmark star: pressed state follows the store and a click asks main to toggle", () => {
  const { detail, refs, state } = detailSetup({ bookmarked: false });
  detail.entry = ENTRY;
  detail.renderEntry(ENTRY);
  assert.equal(refs.bookmarkButton.attrs["aria-pressed"], "false");
  assert.equal(byClass(refs.bookmarkButton, "dt-icon")[0].textContent, S.detailIconBookmarkOff);
  refs.bookmarkButton.click();
  assert.deepEqual(state.toggled, [7]);
  state.bookmarked = true;
  detail.refreshBookmark();
  assert.equal(refs.bookmarkButton.attrs["aria-pressed"], "true");
  assert.equal(byClass(refs.bookmarkButton, "dt-icon")[0].textContent, S.detailIconBookmarkOn);
  detail.clear();
  state.toggled.length = 0;
  refs.bookmarkButton.click(); // no record: nothing to toggle
  assert.deepEqual(state.toggled, []);
});

test("message type: the readable title is the button text and the code stays in the title attribute", () => {
  const { detail, refs } = detailSetup();
  const entry = { ...ENTRY, tpl_id: 4, tpl: "WFM-080", tpl_title: "Callback timed out" };
  detail.entry = entry;
  detail.renderEntry(entry);
  assert.equal(refs.template.textContent, "Callback timed out");
  assert.equal(refs.template.title, "WFM-080");
  assert.equal(refs.template.hidden, false);
});

// ---- collapsible sections ----------------------------------------------------------------------------------

test("section heading: an h3 with a real button, aria-expanded and the collapsed flag follow a click and are remembered", () => {
  const storage = freshPrefs();
  const host = el("section");
  const heading = sectionHeading(host, "links", "Links", "lnk-title");
  host.appendChild(heading);
  assert.equal(heading.tagName, "H3");
  const button = heading.children[0];
  assert.equal(button.tagName, "BUTTON");
  assert.equal(button.attrs["aria-expanded"], "true");
  assert.equal(host.attrs["data-collapsed"], "false");
  button.click();
  assert.equal(button.attrs["aria-expanded"], "false");
  assert.equal(host.attrs["data-collapsed"], "true");
  assert.equal(JSON.parse(storage.data[PREFS_KEY]).sections.links, false);
  // a re-render builds a new heading and starts from the remembered state
  const next = el("section");
  const again = sectionHeading(next, "links", "Links", "lnk-title");
  assert.equal(again.children[0].attrs["aria-expanded"], "false");
  assert.equal(next.attrs["data-collapsed"], "true");
});

test("the payload section starts closed and an existing h3 (bookmark note) can be adopted once", () => {
  freshPrefs();
  const host = el("section");
  const title = el("h3");
  title.textContent = "Bookmark";
  host.appendChild(title);
  assert.equal(adoptHeading(host, "bookmark"), true);
  assert.equal(title.children[0].textContent, "Bookmark");
  assert.equal(title.children[0].attrs["aria-expanded"], "true");
  assert.equal(adoptHeading(host, "bookmark"), false); // already a button
  const payloadHost = el("section");
  assert.equal(sectionHeading(payloadHost, "payload", "Payload").children[0].attrs["aria-expanded"], "false");
});

test("parsed fields: collapsible heading plus a table, placed as text", () => {
  const { detail, refs } = detailSetup();
  const entry = { ...ENTRY, fields: { code: "<b>x</b>", n: 3 } };
  detail.entry = entry;
  detail.renderEntry(entry);
  const heading = refs.fields.children[0];
  assert.equal(heading.tagName, "H3");
  assert.equal(heading.children[0].textContent, "Parsed fields");
  assert.equal(refs.fields.textContent.includes("<b>x</b>"), true);
  assert.equal(refs.fields.all().some((n) => n.tagName === "B"), false);
});

// ---- knowledge: What it means ------------------------------------------------------------------------------

function knowledgeSetup() {
  freshPrefs();
  const host = el("section");
  const section = new KnowledgeSection(host, { getCase: () => "c1", getFamily: () => "AWFM", onError: () => {} });
  return { host, section };
}

test("What it means: the section is titled so and shows the hint", async () => {
  const { host, section } = knowledgeSetup();
  api.knowledge = async () => ({ hint: { text: "Check the CRS." }, params: [], adapter: null, vault_note: "n.md" });
  api.knowledgeError = async () => { throw Object.assign(new Error("x"), { status: 404 }); };
  await section.show({ id: 1, tpl_id: 2, tpl: "WFM-080", fields: {}, source_id: 1 });
  assert.equal(host.hidden, false);
  assert.equal(byClass(host, "kn-title")[0].children[0].textContent, "What it means");
  assert.match(host.textContent, /Check the CRS\./);
  assert.match(host.textContent, /n\.md/);
});

test("What it means: without the knowledge pack the section disappears instead of showing a notice", async () => {
  const { host, section } = knowledgeSetup();
  api.knowledge = async () => ({ hint: null, params: [], adapter: null, vault_note: null, knowledge_pack: { installed: false, message: "Pack missing." } });
  api.knowledgeError = async () => { throw Object.assign(new Error("x"), { status: 404 }); };
  await section.show({ id: 2, tpl_id: 2, tpl: "WFM-080", fields: {}, source_id: 1 });
  assert.equal(host.hidden, true);
  assert.equal(host.textContent.includes("Pack missing."), false);
});

// ---- links: Keys host --------------------------------------------------------------------------------------

test("keys have their own host with a collapsible heading; the links host stays hidden without linked records", async () => {
  freshPrefs();
  const host = el("section");
  const keysHost = el("section");
  const picked = [];
  api.entryLinks = async () => ({ links: [] });
  const section = new LinksSection(host, { getCase: () => "c1", onSameKey: (t, v) => picked.push([t, v]), keysHost });
  section.show({ id: 3, keys: [{ key_type: "gid", value: "42" }, { key_type: "gid", value: "42" }] });
  await tick();
  assert.equal(keysHost.hidden, false);
  assert.equal(keysHost.children[0].children[0].textContent, "Keys");
  const chips = byClass(keysHost, "lnk-chip");
  assert.equal(chips.length, 1);
  assert.equal(chips[0].title, "Same key");
  chips[0].dispatch("click");
  assert.deepEqual(picked, [["gid", "42"]]);
  assert.equal(host.hidden, true);
  section.show(null);
  assert.equal(keysHost.hidden, true);
});

// ---- payload: wrap lines -----------------------------------------------------------------------------------

test("payload: Wrap lines is remembered, applied to the panes, and Copy copies what is shown", async () => {
  const storage = freshPrefs();
  const host = el("section");
  const written = [];
  Object.defineProperty(globalThis, "navigator", { value: { clipboard: { writeText: async (t) => written.push(t) } }, configurable: true });
  api.entryPayloads = async () => ({ payloads: [{ id: 5, entry_id: 3, event_id: null, ref_entry_id: null, pair_id: null, role: "request", direction: "in", char_len: 9, sensitive: false }] });
  api.payload = async () => ({ payload: { id: 5, entry_id: 3, role: "request", sensitive: false }, text: "<a><b/></a>", formatted: { ok: true, text: "<a>\n  <b/>\n</a>", kind: "xml" }, pair: null });
  const section = new PayloadSection(host, { getCase: () => "c1" });
  section.show({ id: 3 });
  await tick();
  await tick();
  const panes = byClass(host, "pl-panes")[0];
  assert.ok(panes, "payload rendered");
  assert.equal(panes.className.includes("is-wrap"), false);
  const wrap = host.all().find((n) => n.dataset && n.dataset.action === "wrap");
  assert.equal(wrap.attrs["aria-pressed"], "false");
  assert.equal(wrap.textContent, "Wrap lines");
  wrap.dispatch("click");
  assert.equal(panes.className.includes("is-wrap"), true);
  assert.equal(wrap.attrs["aria-pressed"], "true");
  assert.equal(JSON.parse(storage.data[PREFS_KEY]).wrap, true);
  const copy = host.all().find((n) => n.dataset && n.dataset.action === "copy");
  copy.dispatch("click");
  await tick();
  assert.deepEqual(written, ["<a>\n  <b/>\n</a>"]); // Formatted is the shown view
  host.all().find((n) => n.dataset && n.dataset.mode === "raw").dispatch("click");
  await tick();
  host.all().find((n) => n.dataset && n.dataset.action === "copy").dispatch("click");
  await tick();
  assert.equal(written[1], "<a><b/></a>"); // Raw is the shown view
  // a new record starts with the remembered wrap
  section.show({ id: 4 });
  await tick();
  await tick();
  assert.equal(byClass(host, "pl-panes")[0].className.includes("is-wrap"), true);
});

test("payload: the heading is the collapsible button of the section and starts closed", async () => {
  freshPrefs();
  const host = el("section");
  api.entryPayloads = async () => ({ payloads: [{ id: 5, entry_id: 3, event_id: null, ref_entry_id: null, pair_id: null, role: "request", direction: "in", char_len: 9, sensitive: false }] });
  api.payload = async () => ({ payload: { id: 5, entry_id: 3, role: "request" }, text: "x", formatted: null, pair: null });
  const section = new PayloadSection(host, { getCase: () => "c1" });
  section.show({ id: 3 });
  await tick();
  assert.equal(host.attrs["data-collapsed"], "true");
  const button = byClass(host, "ds-toggle")[0];
  assert.equal(button.textContent, "Payload");
  button.click();
  assert.equal(host.attrs["data-collapsed"], "false");
});

// ---- highlight: the section hangs from the Highlight button ------------------------------------------------

test("highlight: with a trigger the section stays closed until the button is pressed and closes without a record", () => {
  const parent = el("aside");
  const anchor = parent.appendChild(el("div"));
  const trigger = el("button");
  const store = {
    get: () => null, known: () => true, ensure() {}, rules: () => [], active: () => [],
    highlightKey: async () => ({}), create: async () => ({}),
  };
  const ui = mountHighlights({
    detailAnchor: anchor, detailTrigger: trigger, store, getCase: () => "c1", getFilter: () => null,
    getSelectedRow: () => null, reveal: async () => {}, showMessage: () => {},
  });
  const section = parent.children[1];
  ui.showEntry({ id: 1, keys: [{ key_type: "gid", value: "7" }], tpl_id: 0 });
  assert.equal(section.hidden, true);
  assert.equal(trigger.attrs["aria-expanded"], "false");
  trigger.click();
  assert.equal(section.hidden, false);
  assert.equal(trigger.attrs["aria-expanded"], "true");
  assert.equal(byClass(section, "hl-chip").length, 1);
  trigger.click();
  assert.equal(section.hidden, true);
  trigger.click();
  ui.showEntry(null);
  assert.equal(section.hidden, true);
  assert.equal(trigger.attrs["aria-expanded"], "false");
});

// ---- stylesheet contract -----------------------------------------------------------------------------------

// Tab and screen readers follow the DOM, so the DOM itself must be in reading order (contract 5.2); CSS `order` is not used.
test("html: the DOM order of the detail panel is the reading order and CSS does not reorder it", () => {
  const html = readFileSync(new URL("../../src/dmslog/web/index.html", import.meta.url), "utf8").replace(/\r\n/g, "\n");
  const start = html.indexOf('<aside id="detail-panel"');
  const aside = html.slice(start, html.indexOf("</aside>", start));
  const ids = [...aside.matchAll(/^ {8}<[a-z0-9]+\b[^>]*\bid="([^"]+)"/gm)].map((m) => m[1]);
  assert.deepEqual(ids, ["detail-title", "detail-empty", "detail-meta", "detail-actions", "detail-template", "detail-text",
    "detail-repeat", "detail-knowledge", "detail-fields", "detail-keys", "detail-links", "detail-payload", "detail-bookmark", "context-box"]);
  for (const name of ["knowledge.css", "links.css", "payload.css", "bookmarks.css", "app.css"]) {
    assert.equal(/(^|[\s;{])order:\s*\d/m.test(CSS(name)), false, name + " must not reorder with `order`");
  }
});

test("html: the Highlight section is inserted right after the actions, so DOM order stays the reading order", () => {
  const parent = el("aside");
  const actions = parent.appendChild(el("div"));
  parent.appendChild(el("button")); // the message type
  const store = { get: () => null, known: () => true, ensure() {}, rules: () => [], active: () => [] };
  mountHighlights({
    detailAnchor: actions, detailTrigger: el("button"), store, getCase: () => "c1", getFilter: () => null,
    getSelectedRow: () => null, reveal: async () => {}, showMessage: () => {},
  });
  assert.equal(parent.children[1].className.split(/\s+/).includes("detail-highlight"), true);
  assert.equal(parent.children.length, 3);
});

test("css: every collapsible section hides its body but keeps its heading; actions never wrap a label", () => {
  const css = CSS("knowledge.css");
  for (const id of ["knowledge", "fields", "keys", "links", "payload", "bookmark"]) {
    assert.ok(css.includes("#detail-" + id + '[data-collapsed="true"] > :not(.ds-heading)'), id);
  }
  assert.match(css, /\.detail-actions \.ui-button \{\s*white-space: nowrap;/);
  assert.match(CSS("payload.css"), /\.pl-panes\.is-wrap \.pl-pre \{\s*white-space: pre-wrap;/);
  assert.match(CSS("bookmarks.css"), /\.detail-bookmark \.bm-toggle \{\s*display: none;/);
  assert.equal(CSS("bookmarks.css").includes(":has("), false, "no :has() (no fallback in older browsers)");
  assert.match(CSS("bookmarks.css"), /\.detail-bookmark:not\(\[data-marked="true"\]\) \{\s*display: none;/);
});

test("css: the record text keeps six lines minimum, 45vh maximum and a vertical resize", () => {
  const css = readFileSync(new URL("../../src/dmslog/web/css/app.css", import.meta.url), "utf8").replace(/\r\n/g, "\n");
  const block = /\.detail-text \{([^}]*)\}/.exec(css)[1];
  assert.match(block, /min-height: calc\(6lh/);
  assert.match(block, /max-height: 45vh/);
  assert.match(block, /resize: vertical/);
});

test("no innerHTML in the detail modules", () => {
  for (const file of ["detail.js", "knowledge.js", "links.js", "payload.js", "highlight-panel.js"]) {
    const text = readFileSync(new URL("../../src/dmslog/web/js/" + file, import.meta.url), "utf8");
    assert.equal(/innerHTML/.test(text), false, file);
  }
});

// ---- bookmark note section: visibility is a flag set by the code, not :has() ----------------------------------

test("bookmark note: data-marked follows the store and is false without a record", async () => {
  const { initDetailBookmark } = await import("../../src/dmslog/web/js/bookmarks-panel.js");
  const marks = new Map();
  const store = { get: (id) => marks.get(id) || null, ensure() {}, load: async () => null, toggle: async () => {}, update: async () => {} };
  const host = el("section");
  const ui = initDetailBookmark(host, { store, onError: () => {} });
  ui.show(null);
  assert.equal(host.attrs["data-marked"], "false");
  assert.equal(host.hidden, true);
  ui.show({ id: 3 });
  assert.equal(host.attrs["data-marked"], "false");
  marks.set(3, { id: 3, color: "yellow" });
  ui.refresh();
  assert.equal(host.attrs["data-marked"], "true");
  marks.delete(3);
  ui.refresh();
  assert.equal(host.attrs["data-marked"], "false");
});

// ---- the Highlight button while its module loads or failed ------------------------------------------------------

test("highlight button: disabled while the module loads, opens on the first click once loaded, disabled again with a reason on failure", async () => {
  const { createButtonGate } = await import("../../src/dmslog/web/js/late-init.js");
  const button = el("button");
  const gate = createButtonGate(button, { loadingTitle: "loading", failedTitle: S.actionUnavailable });
  assert.equal(button.disabled, true);
  assert.equal(button.title, "loading");
  let finish;
  const loading = gate.track(new Promise((resolve) => { finish = resolve; }));
  button.click(); // a click while loading goes nowhere (disabled)
  finish("module");
  assert.equal(await loading, "module");
  assert.equal(button.disabled, false);
  assert.equal(button.attrs["aria-disabled"], "false");
  // the first click after loading opens the section
  const parent = el("aside");
  const anchor = parent.appendChild(el("div"));
  const store = { get: () => null, known: () => true, ensure() {}, rules: () => [], active: () => [] };
  const ui = mountHighlights({
    detailAnchor: anchor, detailTrigger: button, store, getCase: () => "c1", getFilter: () => null,
    getSelectedRow: () => null, reveal: async () => {}, showMessage: () => {},
  });
  ui.showEntry({ id: 1, keys: [{ key_type: "gid", value: "7" }], tpl_id: 0 });
  button.click();
  assert.equal(button.attrs["aria-expanded"], "true");
  assert.equal(parent.children[1].hidden, false);
  // once ready, a later attempt is a no-op and never disables the focused button again
  await gate.track(Promise.resolve());
  assert.equal(button.disabled, false);
});

test("highlight button: a failed load leaves it disabled with the unavailable reason and a retry can enable it", async () => {
  const { createButtonGate } = await import("../../src/dmslog/web/js/late-init.js");
  const button = el("button");
  const gate = createButtonGate(button, { loadingTitle: "loading", failedTitle: S.actionUnavailable });
  await assert.rejects(gate.track(Promise.reject(new Error("boom"))), /boom/);
  assert.equal(button.disabled, true);
  assert.equal(button.title, S.actionUnavailable);
  const retry = gate.track(Promise.resolve());
  assert.equal(button.title, "loading");
  await retry;
  assert.equal(button.disabled, false);
  assert.equal(button.title, "");
});

test("main.js sends the Highlight loader through the button gate and the module through retryable()", () => {
  const main = readFileSync(new URL("../../src/dmslog/web/js/main.js", import.meta.url), "utf8").replace(/\r\n/g, "\n");
  assert.match(main, /createButtonGate\(byId\("highlight-button"\)/);
  assert.match(main, /\(\) => highlightGate\.track\(loadHighlights\(\)\)/);
  assert.match(main, /const loadHighlights = retryable\(/);
});

// ---- UI-20 record actions in the registry ----------------------------------------------------------------------

async function recordRegistry(over = {}) {
  const { createActionRegistry } = await import("../../src/dmslog/web/js/actions.js");
  const { registerAppActions } = await import("../../src/dmslog/web/js/app-actions.js");
  const errors = [];
  const registry = createActionRegistry({ onError: (err) => errors.push(String(err.message)) });
  const calls = [];
  const state = { shown: true, type: true, hlReady: true };
  registerAppActions(registry, {
    ready: {},
    highlightReady: () => state.hlReady,
    copyRecord: () => calls.push(["copy"]),
    loadContext: (same) => calls.push(["context", same]),
    filterType: () => calls.push(["filterType"]),
    highlightType: () => calls.push(["highlightType"]),
    ...over,
  });
  return { registry, calls, state, errors };
}

test("record actions: copy, context, filterType and highlightType are registered with strings.js labels", async () => {
  const { registry } = await recordRegistry();
  const labels = { "record.copy": "Copy text", "record.context": "Context", "record.filterType": "Only this message type",
    "record.highlightType": "Highlight this message type" };
  for (const [id, label] of Object.entries(labels)) {
    const def = registry.get(id);
    assert.ok(def, id);
    assert.equal(def.group, "record");
    assert.equal(S[def.labelKey], label);
  }
  assert.deepEqual(registry.get("record.context").children({}).map((c) => c.args), ["source", "tid", "merge"]);
});

test("record actions: run through invoke with their arguments and follow the record and its message type", async () => {
  const { registry, calls, state } = await recordRegistry();
  // The record is read from ctx.entry() (the menu and the palette hand in the one they captured).
  const ctx = { caseId: () => "c1", entry: () => (state.shown ? { id: 1, tpl_id: state.type ? 5 : 0 } : null) };
  assert.deepEqual(await registry.invoke("record.copy", ctx), { ok: true });
  await registry.invoke("record.context", ctx, "tid");
  await registry.invoke("record.filterType", ctx);
  await registry.invoke("record.highlightType", ctx);
  assert.deepEqual(calls, [["copy"], ["context", "tid"], ["filterType"], ["highlightType"]]);
  state.type = false;
  assert.equal((await registry.invoke("record.filterType", ctx)).reason, "disabled");
  assert.equal((await registry.invoke("record.highlightType", ctx)).reason, "disabled");
  state.type = true;
  state.hlReady = false;
  assert.equal((await registry.invoke("record.highlightType", ctx)).reason, "disabled");
  state.shown = false;
  assert.equal((await registry.invoke("record.copy", ctx)).reason, "disabled");
  assert.equal(registry.state("record.copy", ctx).reasonKey, "cmpNeedRecord");
});

test("detail controls run through runAction with the record action ids", () => {
  freshPrefs();
  const refs = {};
  for (const name of ["empty", "meta", "text", "actions", "contextButton", "contextClose", "contextSame", "contextBox", "contextList",
    "copyButton", "compareButton", "bookmarkButton", "highlightButton", "copyStatus", "fields", "template"]) {
    refs[name] = el(name.endsWith("Button") || name === "template" || name === "contextClose" ? "button" : "div");
  }
  refs.contextSame.value = "tid";
  const ran = [];
  new Detail(refs, { getCase: () => "c1", getSourceName: String, onError: (e) => { throw e; }, runAction: (id, args) => ran.push([id, args]) });
  refs.contextButton.click();
  refs.copyButton.click();
  refs.template.click();
  assert.deepEqual(ran, [["record.context", "tid"], ["record.copy", undefined], ["record.filterType", undefined]]);
});
