// 9.15: the detail shows the adjusted time next to the original one, and the two record actions ("Use as clock anchor" /
// "Use as clock target") of the real action registry drive the real clock dialog. Fake DOM; synthetic data.
import test from "node:test";
import assert from "node:assert/strict";

import { El, installGlobalDom, tick } from "./fixtures/fake-dom.mjs";

const doc = installGlobalDom();
globalThis.Element = El;
const baseCreate = doc.createElement;
doc.createElement = (tag) => {
  const node = baseCreate(tag);
  if (["input", "select", "textarea"].includes(String(tag))) {
    node.value = "";
  }
  return node;
};

const { Detail, adjustedFact, setDetailPrefs } = await import("../../src/dmslog/web/js/detail.js");
const { createDetailPrefs } = await import("../../src/dmslog/web/js/detail-model.js");
const { createActionRegistry } = await import("../../src/dmslog/web/js/actions.js");
const { registerAppActions } = await import("../../src/dmslog/web/js/app-actions.js");
const { contextMenuDefs } = await import("../../src/dmslog/web/js/commands-model.js");
const { S } = await import("../../src/dmslog/web/js/strings.js");

const T = Date.UTC(2026, 2, 9, 14, 5, 6, 789);
const byClass = (root, cls) => root.all().filter((n) => n.className.split(/\s+/).includes(cls));
const el = (tag = "div") => doc.createElement(tag);

function detailSetup() {
  setDetailPrefs(createDetailPrefs({ getItem: () => null, setItem() {} }));
  const refs = {};
  for (const name of ["empty", "meta", "text", "actions", "contextButton", "contextClose", "contextSame", "contextBox", "contextList",
    "copyButton", "compareButton", "bookmarkButton", "highlightButton", "copyStatus", "fields", "template"]) {
    refs[name] = el(name.endsWith("Button") || name === "template" ? "button" : "div");
  }
  const detail = new Detail(refs, {
    getCase: () => "c1", getSourceName: String, getSourceInfo: () => ({ name: "a.log", tag: "C2" }), onError: (e) => { throw e; },
  });
  return { detail, refs };
}

test("adjustedFact: only for a record of a collection with an offset", () => {
  assert.equal(adjustedFact({ ts: T, ts_adjusted: T + 1234, clock_offset_ms: 1234 }), "Adjusted time 2026-03-09 14:05:08.023 (clock +1.234s)");
  assert.equal(adjustedFact({ ts: T, ts_adjusted: T - 5, clock_offset_ms: -5 }), "Adjusted time 2026-03-09 14:05:06.784 (clock -0.005s)");
  for (const none of [{ ts: T }, { ts: T, ts_adjusted: T, clock_offset_ms: 0 }, { ts: T, ts_adjusted: "x", clock_offset_ms: 5 },
    { ts: T, ts_adjusted: T + 5, clock_offset_ms: 1.5 }, { ts: T, ts_adjusted: T + 5 }, null]) {
    assert.equal(adjustedFact(none), "", JSON.stringify(none));
  }
});

test("detail header: the original time stays in the title row and the adjusted one is a separate fact", () => {
  const { detail, refs } = detailSetup();
  const entry = { id: 7, ts: T, ts_adjusted: T + 2000, clock_offset_ms: 2000, pid: 4, source_id: 1, text: "x", fields: {}, keys: [] };
  detail.entry = entry;
  detail.renderEntry(entry);
  assert.equal(byClass(refs.meta, "dt-time")[0].textContent, "2026-03-09 14:05:06.789", "the original time");
  const facts = byClass(refs.meta, "dt-fact").map((n) => n.textContent);
  assert.equal(facts[0], "Adjusted time 2026-03-09 14:05:08.789 (clock +2.000s)");
  assert.ok(facts.includes("PID 4"));
  const plain = { id: 8, ts: T, pid: 4, source_id: 1, text: "x", fields: {}, keys: [] };
  detail.entry = plain;
  detail.renderEntry(plain);
  assert.equal(byClass(refs.meta, "dt-fact").some((n) => n.textContent.startsWith("Adjusted")), false);
});

test("the context list keeps the original time and puts the adjusted one in the tooltip", () => {
  const { detail, refs } = detailSetup();
  detail.entry = { id: 1, ts: T, source_id: 1, text: "x", fields: {}, keys: [] };
  detail.renderContext([
    { id: 1, ts: T, ts_adjusted: T + 1000, msg: "a" },
    { id: 2, ts: T + 1, ts_adjusted: T + 1, msg: "b" },
    { id: 3, ts: T + 2, msg: "c" },
  ]);
  const times = byClass(refs.contextList, "context-time");
  assert.deepEqual(times.map((t) => t.textContent), ["2026-03-09 14:05:06.789", "2026-03-09 14:05:06.790", "2026-03-09 14:05:06.791"]);
  assert.equal(times[0].title, "Adjusted time 2026-03-09 14:05:07.789");
  assert.equal(times[1].title, "", "no tooltip when nothing moved");
  assert.equal(times[2].title, "");
});

// ---- record actions --------------------------------------------------------------------------------------------

function actionsSetup({ multi = true, caseId = "A" } = {}) {
  doc.body.children = [];
  doc.activeElement = doc.body;
  const registry = createActionRegistry({ onError: (err) => { throw err; } });
  const state = { caseId, gen: 1, multi, messages: [], sets: [], applied: 0, entry: null, errors: [] };
  const list = [{ group: 1, number: 1, tag: "C1", clock_offset_ms: 0 }, { group: 2, number: 2, tag: "C2", clock_offset_ms: 0 }];
  const app = registerAppActions(registry, {
    ready: {},
    hasCollections: () => state.multi,
    clockContext: () => ({ caseId: state.caseId, generation: state.gen }),
    onError: (err) => state.errors.push(err),
    clockOptions: () => ({
      api: { clock: { set: (c, g, body, signal) => { state.sets.push([c, g, body, signal]); return new Promise(() => {}); }, reset: async () => ({}) } },
      getContext: () => ({ caseId: state.caseId, generation: state.gen }),
      getCollections: () => list,
      groupOfSource: (id) => ({ 1: 1, 2: 2 })[id] ?? null,
      view: { keep: () => null, refreshList: async () => {}, applyView: async () => { state.applied += 1; }, refreshDerived() {}, reveal: async () => {}, selectedId: () => null, clearDetail() {} },
      onMessage: (text, isError) => state.messages.push([text, isError]),
    }),
  });
  const ctx = { caseId: () => state.caseId, entry: () => state.entry, selectedRow: () => null };
  return { registry, app, state, ctx };
}

test("both clock actions are record actions: in the record menu and the palette, off without a record with a time or without two collections", () => {
  const { registry, state, ctx } = actionsSetup();
  for (const id of ["record.clockAnchor", "record.clockTarget"]) {
    state.entry = null;
    const def = registry.get(id);
    assert.equal(def.group, "record");
    assert.equal(def.disabledKey, "ckNeedTwo");
    assert.equal(registry.state(id, ctx).enabled, false, "no record shown");
    state.entry = { id: 1, ts: T, source_id: 1 };
    assert.equal(registry.state(id, ctx).enabled, true);
    state.entry = { id: 1, ts: null, source_id: 1 };
    assert.equal(registry.state(id, ctx).enabled, false, "no time");
    state.entry = { id: 1, ts: T, source_id: 1 };
    state.multi = false;
    assert.equal(registry.state(id, ctx).enabled, false, "one collection");
    assert.equal(registry.state(id, ctx).reasonKey, "ckNeedTwo");
    state.multi = true;
  }
  const defs = contextMenuDefs(registry).map((d) => d.id);
  assert.ok(defs.includes("record.clockAnchor") && defs.includes("record.clockTarget"));
  assert.equal(S[registry.get("record.clockAnchor").labelKey], "Use as clock anchor");
  assert.equal(S[registry.get("record.clockTarget").labelKey], "Use as clock target");
});

test("anchor then target through the registry opens the dialog for the target's collection and returns the focus to the opener", async () => {
  const { registry, state, ctx } = actionsSetup();
  const opener = doc.createElement("button");
  doc.body.appendChild(opener);
  state.entry = { id: 10, ts: T, source_id: 1 };
  assert.deepEqual(await registry.invoke("record.clockAnchor", ctx), { ok: true });
  assert.ok(state.messages.at(-1)[0].startsWith("Clock anchor: record 10 of C1"));
  state.entry = { id: 20, ts: T - 1500, source_id: 2 };
  opener.focus();
  assert.deepEqual(await registry.invoke("record.clockTarget", ctx), { ok: true });
  await tick();
  const dialogs = doc.body.all().filter((n) => n.tagName === "DIALOG");
  assert.equal(dialogs.length, 1);
  assert.deepEqual(state.sets.map((s) => [s[0], s[1], s[2]]), [["A", 2, { anchor: 10, target: 20, dry_run: true }]]);
  byClass(dialogs[0], "ck-cancel")[0].click();
  assert.equal(doc.activeElement, opener);
  assert.equal(state.sets[0][3].aborted, true);
});

test("onCase closes the dialog and forgets the picks; the rows' clock object is busy only while a change runs", async () => {
  const { registry, app, state, ctx } = actionsSetup();
  assert.equal(app.clock.busy(), false);
  state.entry = { id: 10, ts: T, source_id: 1 };
  await registry.invoke("record.clockAnchor", ctx);
  state.entry = { id: 20, ts: T, source_id: 2 };
  await registry.invoke("record.clockTarget", ctx);
  await tick();
  assert.equal(doc.body.all().filter((n) => n.tagName === "DIALOG").length, 1);
  state.caseId = "B";
  state.gen += 1;
  app.onCase();
  assert.equal(doc.body.all().filter((n) => n.tagName === "DIALOG").length, 0);
  state.caseId = "A";
  state.gen += 1;
  state.entry = { id: 21, ts: T, source_id: 2 };
  await registry.invoke("record.clockTarget", ctx);
  assert.ok(state.messages.at(-1)[0].startsWith("Clock target: record 21"), "the anchor was forgotten: no dialog");
});

test("a failed load of the dialog module is reported through onError and tried again", async () => {
  const registry = createActionRegistry({ onError: () => {} });
  const boom = new Error("no options");
  let tries = 0;
  const errors = [];
  const app = registerAppActions(registry, { ready: {}, onError: (e) => errors.push(e), clockContext: () => ({ caseId: "A", generation: 1 }), clockOptions: () => { tries += 1; throw boom; } });
  await app.clock.align(2, null);
  await app.clock.reset(2);
  assert.deepEqual(errors, [boom, boom]);
  assert.equal(tries, 2, "the second use tried again");
});

function lateLoadApp() {
  doc.body.children = [];
  const registry = createActionRegistry({ onError: () => {} });
  const state = { caseId: "A", gen: 1, resets: [], messages: [], errors: [] };
  const list = [{ group: 1, number: 1, tag: "C1", clock_offset_ms: 0 }, { group: 2, number: 2, tag: "C2", clock_offset_ms: 20 }];
  const app = registerAppActions(registry, {
    ready: {},
    onError: (e) => state.errors.push(e),
    clockContext: () => ({ caseId: state.caseId, generation: state.gen }),
    clockOptions: () => {
      state.caseId = "A"; // the import finishes: the page is on A again, but it is another opening of A
      state.gen += 2;
      return {
        api: { clock: { reset: async (...args) => { state.resets.push(args); return {}; }, set: async () => ({}) } },
        getContext: () => ({ caseId: state.caseId, generation: state.gen }),
        getCollections: () => list,
        groupOfSource: () => 1,
        view: { keep: () => null, refreshList: async () => {}, applyView: async () => {}, refreshDerived() {}, reveal: async () => {}, selectedId: () => null, clearDetail() {} },
        onMessage: (text, isError) => state.messages.push([text, isError]),
      };
    },
  });
  return { app, state };
}

test("Reset decided in A never reaches the server when the module loads after the case was opened again", async () => {
  const { app, state } = lateLoadApp();
  await app.clock.reset(2);
  assert.deepEqual(state.resets, [], "no DELETE");
  assert.deepEqual(state.messages, []);
  assert.deepEqual(state.errors, []);
  await app.clock.reset(2); // a new decision, taken in the live scope, goes through
  assert.equal(state.resets.length, 1);
});

test("Align clock decided in A opens no dialog in the case opened meanwhile", async () => {
  const { app, state } = lateLoadApp();
  await app.clock.align(2, null);
  assert.equal(doc.body.all().filter((n) => n.tagName === "DIALOG").length, 0);
  assert.deepEqual(state.errors, []);
});

// main.js is the page's wiring and has no unit harness: these checks read its text for the pieces the review asked for.
test("main.js wiring: a generation per opened case, one clock scope for the actions and the dialog, offsets on both time spans", async () => {
  const { readFileSync } = await import("node:fs");
  const main = readFileSync(new URL("../../src/dmslog/web/js/main.js", import.meta.url), "utf8");
  assert.match(main, /caseGen \+= 1;\s*caseId = id;/, "every opened case gets a new generation");
  assert.match(main, /const clockContext = \(\) => \(\{ caseId, generation: caseGen \}\);/);
  assert.match(main, /clockContext,\s*clockOptions: \(\) => \(\{[\s\S]*getContext: clockContext,/);
  assert.match(main, /timeSpan\(sources, effectiveSourceIds\(sources, ids, groups\), formState\.from, formState\.to, offsetOf\)/);
  assert.match(main, /timeSpan\(sources, \[\], null, null, offsetOf\)/);
  assert.doesNotMatch(main, /onApplied/, "the reload sequence lives in clock-dialog.js, where every wait is followed by a scope check");
});
