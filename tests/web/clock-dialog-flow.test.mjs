// 9.15: the Align clock dialog and Reset clock on the fake DOM, with a fake API whose answers the test settles by hand.
// Covers the operation object: a case switch or a close while a request is in flight, overlapping requests, late answers,
// the focus and the live region. Synthetic data only; no timers.
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
const { mountClock } = await import("../../src/dmslog/web/js/clock-dialog.js");
const { S } = await import("../../src/dmslog/web/js/strings.js");

const T = Date.UTC(2026, 2, 9, 14, 5, 6, 789);
const has = (node, cls) => node.className.split(/\s+/).includes(cls);
const q = (root, cls) => root.all().filter((n) => has(n, cls));
const one = (root, cls) => q(root, cls)[0];
const dialogs = () => doc.body.all().filter((n) => n.tagName === "DIALOG");
const isOpen = (dlg) => dlg.getAttribute("open") === "";
const live = (dlg) => dlg.all().filter((n) => n.attrs["aria-live"] !== undefined || n.attrs.role === "status");
const deferred = () => {
  let resolve; let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
};
const collection = (group, number, extra = {}) => ({ group, number, tag: "C" + number, clock_offset_ms: 0, ...extra });
const entry = (id, source, ts = T, extra = {}) => ({ id, ts, source_id: source, ...extra });
const apiError = (status, code, message = "server text 12345") => Object.assign(new Error(message), { name: "ApiError", status, code });

function setup({ list = [collection(1, 1), collection(2, 2)], caseId = "A", ignoreAbort = false } = {}) {
  doc.body.children = [];
  doc.activeElement = doc.body;
  const state = { caseId, list, calls: [], messages: [], applied: [] };
  const api = {
    clock: {
      set(c, g, body, signal) {
        const d = deferred();
        const call = { kind: "set", c, g, body, signal, ...d };
        if (signal && !ignoreAbort) { // a transport that ignores the signal still must not be able to confuse the dialog
          signal.addEventListener("abort", () => d.reject(Object.assign(new Error("aborted"), { name: "AbortError" })));
        }
        state.calls.push(call);
        return d.promise;
      },
      reset(c, g, signal) {
        const d = deferred();
        const call = { kind: "reset", c, g, signal, ...d };
        state.calls.push(call);
        return d.promise;
      },
    },
  };
  const host = doc.createElement("div");
  doc.body.appendChild(host);
  const clock = mountClock(host, {
    api,
    getCase: () => state.caseId,
    getCollections: () => state.list,
    groupOfSource: (id) => ({ 1: 1, 2: 2, 3: 3 })[id] ?? null,
    onApplied: async (result) => { state.applied.push(result); },
    onMessage: (text, isError) => state.messages.push([text, isError]),
  });
  const opener = doc.createElement("button");
  host.appendChild(opener);
  return { state, clock, host, opener };
}

const dryAnswer = (body, extra = {}) => ({
  group: 2, previous_ms: 0, clock_offset_ms: 1500, clock_source: { method: "pair" }, applied: false, skew_ms: -1500,
  anchor: { entry_id: body.anchor, ts: T, ts_adjusted: T }, target: { entry_id: body.target, ts: T - 1500, ts_adjusted: T },
  ...extra,
});
const appliedAnswer = (extra = {}) => ({ group: 2, previous_ms: 0, clock_offset_ms: 1500, clock_source: { method: "pair" }, applied: true, ...extra });

// Anchor in C1 (record 10), target in C2 (record 20): opens the dialog and starts the dry run.
async function pairOpen(ctx) {
  assert.equal(ctx.clock.setAnchor(entry(10, 1)), true);
  ctx.clock.setTarget(entry(20, 2, T - 1500), ctx.opener);
  await tick();
  return dialogs()[0];
}

test("anchor then target: the dialog opens, shows both original times, dry-runs, then applies and gives the focus back", async () => {
  const ctx = setup();
  const dlg = await pairOpen(ctx);
  assert.ok(dlg && isOpen(dlg));
  assert.equal(ctx.state.calls.length, 1);
  const dry = ctx.state.calls[0];
  assert.deepEqual([dry.c, dry.g, dry.body], ["A", 2, { anchor: 10, target: 20, dry_run: true }]);
  assert.ok(dry.signal && dry.signal.aborted === false);
  // before the answer: the originals are side by side, the adjusted time of the target is not known yet
  assert.equal(one(dlg, "ck-row-orig").children[1].textContent, "2026-03-09 14:05:06.789");
  assert.equal(one(dlg, "ck-row-orig").children[2].textContent, "2026-03-09 14:05:05.289");
  assert.equal(one(dlg, "ck-row-adj").children[2].textContent, "");
  assert.equal(one(dlg, "ck-row-coll").children[1].textContent, "C1");
  assert.equal(one(dlg, "ck-row-coll").children[2].textContent, "C2");
  assert.equal(one(dlg, "ck-status").textContent, S.ckChecking);
  assert.equal(one(dlg, "ck-apply").disabled, true, "nothing to apply before the dry run answers");
  dry.resolve(dryAnswer(dry.body));
  await tick();
  assert.equal(one(dlg, "ck-row-adj").children[2].textContent, "2026-03-09 14:05:06.789");
  assert.equal(one(dlg, "ck-row-orig").children[2].textContent, "2026-03-09 14:05:05.289", "the original time is still shown");
  assert.equal(one(dlg, "ck-preview").textContent, "C2 will be shifted by +1.500s. Its clock runs 1.500 s behind the anchor's.");
  assert.equal(one(dlg, "ck-status").textContent, one(dlg, "ck-preview").textContent);
  assert.equal(live(dlg).length, 1, "one live region in the dialog");
  assert.equal(one(dlg, "ck-apply").disabled, false);
  one(dlg, "ck-apply").click();
  await tick();
  assert.equal(ctx.state.calls.length, 2);
  const put = ctx.state.calls[1];
  assert.deepEqual(put.body, { anchor: 10, target: 20 }, "the real PUT is not a dry run");
  assert.equal(put.signal, undefined, "an applying PUT is never aborted");
  assert.equal(one(dlg, "ck-apply").getAttribute("aria-disabled"), "true");
  assert.equal(doc.activeElement, one(dlg, "ck-apply"), "the focus stays on Apply while it works");
  put.resolve(appliedAnswer());
  await tick();
  assert.equal(isOpen(dlg), false);
  assert.equal(dialogs().length, 0, "the dialog is removed from the page");
  assert.equal(doc.activeElement, ctx.opener, "the focus goes back to the opener");
  assert.deepEqual(ctx.state.messages.at(-1), [S.ckApplied.replace("{tag}", "C2").replace("{offset}", "+1.500s"), false]);
  assert.equal(ctx.state.applied.length, 1);
  assert.deepEqual(ctx.clock.picked(), { anchor: null, target: null }, "the pair was consumed");
  assert.equal(ctx.clock.isOpen(), false);
});

test("the target without an anchor, or in the anchor's collection, opens nothing and says why", async () => {
  const ctx = setup();
  ctx.clock.setTarget(entry(20, 2), ctx.opener);
  await tick();
  assert.equal(dialogs().length, 0);
  assert.equal(ctx.state.calls.length, 0);
  assert.ok(ctx.state.messages.at(-1)[0].startsWith("Clock target: record 20 of C2"));
  const same = setup();
  same.clock.setAnchor(entry(10, 2));
  same.clock.setTarget(entry(20, 2), same.opener);
  await tick();
  assert.equal(dialogs().length, 0);
  assert.deepEqual(same.state.messages.at(-1), [S.ckSameCollection, true]);
});

test("a record without time or collection is refused with a sentence", () => {
  const ctx = setup();
  assert.equal(ctx.clock.setAnchor(entry(10, 1, null)), false);
  assert.deepEqual(ctx.state.messages.at(-1), [S.ckRefNoTime, true]);
  assert.equal(ctx.clock.setAnchor(entry(10, 99)), false);
  assert.deepEqual(ctx.state.messages.at(-1), [S.ckRefNoCollection, true]);
  assert.equal(ctx.clock.setAnchor(null), false);
  assert.equal(ctx.clock.picked().anchor, null);
});

test("the anchor is kept by id: changing the record object afterwards changes nothing", async () => {
  const ctx = setup();
  const source = entry(10, 1);
  ctx.clock.setAnchor(source);
  source.id = 999;
  source.ts = 0;
  ctx.clock.setTarget(entry(20, 2), ctx.opener);
  await tick();
  assert.equal(ctx.state.calls[0].body.anchor, 10);
});

test("closing aborts the dry run; its late answer changes nothing and the dialog is gone", async () => {
  const ctx = setup();
  const dlg = await pairOpen(ctx);
  const dry = ctx.state.calls[0];
  one(dlg, "ck-cancel").click();
  assert.equal(dry.signal.aborted, true);
  assert.equal(isOpen(dlg), false);
  assert.equal(doc.activeElement, ctx.opener);
  dry.resolve(dryAnswer(dry.body));
  await tick();
  assert.equal(dialogs().length, 0);
  assert.equal(ctx.state.applied.length, 0);
  assert.equal(ctx.state.messages.filter(([, isError]) => isError).length, 0, "an aborted request reports nothing");
});

test("a case switch closes the dialog, forgets the picks and drops a late dry run", async () => {
  const ctx = setup();
  const dlg = await pairOpen(ctx);
  const dry = ctx.state.calls[0];
  ctx.state.caseId = "B";
  ctx.clock.forgetCase();
  assert.equal(isOpen(dlg), false);
  assert.equal(dry.signal.aborted, true);
  dry.resolve(dryAnswer(dry.body));
  await tick();
  assert.equal(ctx.state.applied.length, 0);
  ctx.state.caseId = "A";
  assert.deepEqual(ctx.clock.picked(), { anchor: null, target: null });
});

test("a dry run that answers for another case is dropped even if nothing told the dialog", async () => {
  const ctx = setup();
  const dlg = await pairOpen(ctx);
  const dry = ctx.state.calls[0];
  ctx.state.caseId = "B"; // forgetCase() was not called
  dry.resolve(dryAnswer(dry.body));
  await tick();
  assert.equal(one(dlg, "ck-row-adj").children[2].textContent, "");
  assert.equal(one(dlg, "ck-apply").disabled, true);
  one(dlg, "ck-apply").click();
  await tick();
  assert.equal(ctx.state.calls.length, 1, "no PUT for another case");
});

test("a case switch while the PUT is in flight: no message, no reload, and the lock is released", async () => {
  const ctx = setup();
  const dlg = await pairOpen(ctx);
  ctx.state.calls[0].resolve(dryAnswer(ctx.state.calls[0].body));
  await tick();
  one(dlg, "ck-apply").click();
  await tick();
  const put = ctx.state.calls[1];
  const before = ctx.state.messages.length;
  ctx.state.caseId = "B";
  ctx.clock.forgetCase();
  assert.equal(isOpen(dlg), false);
  assert.equal(ctx.clock.busy(), true, "the request is still running");
  put.resolve(appliedAnswer());
  await tick();
  assert.equal(ctx.state.messages.length, before);
  assert.equal(ctx.state.applied.length, 0);
  assert.equal(ctx.clock.busy(), false);
});

test("closing the dialog while the PUT is in flight: the answer still reloads the view of the same case", async () => {
  const ctx = setup();
  const dlg = await pairOpen(ctx);
  ctx.state.calls[0].resolve(dryAnswer(ctx.state.calls[0].body));
  await tick();
  one(dlg, "ck-apply").click();
  await tick();
  const put = ctx.state.calls[1];
  dlg.dispatch("cancel"); // Escape
  assert.equal(isOpen(dlg), false);
  put.resolve(appliedAnswer());
  await tick();
  assert.equal(ctx.state.applied.length, 1);
  assert.ok(ctx.state.messages.at(-1)[0].startsWith("Clock of C2 aligned"));
});

test("two clicks on Apply send one PUT; a second change waits for the first", async () => {
  const ctx = setup();
  const dlg = await pairOpen(ctx);
  ctx.state.calls[0].resolve(dryAnswer(ctx.state.calls[0].body));
  await tick();
  const apply = one(dlg, "ck-apply");
  apply.click();
  apply.click();
  one(dlg, "ck-apply").dispatch("click");
  await tick();
  assert.equal(ctx.state.calls.filter((c) => c.kind === "set" && !c.body.dry_run).length, 1);
  assert.equal(one(dlg, "ck-status").textContent, S.ckApplying, "the second click did not report anything");
  assert.equal(apply.disabled, false, "Apply stays focusable while it works");
  // a Reset from elsewhere while it runs is refused
  await ctx.clock.reset(2);
  assert.deepEqual(ctx.state.messages.at(-1), [S.ckBusy, true]);
  assert.equal(ctx.state.calls.filter((c) => c.kind === "reset").length, 0);
});

test("a PUT refused with 4xx keeps the dialog open with a static sentence and does not reload; 5xx and odd answers reload", async () => {
  const ctx = setup();
  let dlg = await pairOpen(ctx);
  ctx.state.calls[0].resolve(dryAnswer(ctx.state.calls[0].body));
  await tick();
  one(dlg, "ck-apply").click();
  await tick();
  ctx.state.calls[1].reject(apiError(400, "offset_too_large", "the two records are 99999999 ms apart"));
  await tick();
  assert.ok(isOpen(dlg));
  assert.equal(one(dlg, "ck-status").textContent, S.ckErrors.offset_too_large);
  assert.ok(!dlg.all().some((n) => n.textContent.includes("99999999")));
  assert.equal(ctx.state.applied.length, 0, "a refusal changed nothing: no reload");
  assert.equal(one(dlg, "ck-apply").getAttribute("aria-disabled"), "false");
  assert.equal(one(dlg, "ck-apply").disabled, false, "it can be tried again");
  assert.equal(doc.activeElement, one(dlg, "ck-apply"));
  // a server error: the change may have happened, the view is read again
  one(dlg, "ck-apply").click();
  await tick();
  ctx.state.calls[2].reject(apiError(500, "database_error"));
  await tick();
  assert.equal(one(dlg, "ck-status").textContent, S.ckErrors.database_error);
  assert.equal(ctx.state.applied.length, 1);
  // an answer that is not the one we asked for
  one(dlg, "ck-apply").click();
  await tick();
  ctx.state.calls[3].resolve({ group: 7, applied: true });
  await tick();
  assert.equal(one(dlg, "ck-status").textContent, S.ckErrFailed);
  assert.equal(ctx.state.applied.length, 2);
  assert.ok(isOpen(dlg));
  dlg = null;
});

test("dry-run errors are shown as static sentences; an answer for other records is refused", async () => {
  const ctx = setup();
  const dlg = await pairOpen(ctx);
  ctx.state.calls[0].reject(apiError(400, "no_time", "entry 10 has no time"));
  await tick();
  assert.equal(one(dlg, "ck-status").textContent, S.ckErrors.no_time);
  assert.equal(one(dlg, "ck-apply").disabled, true);
  const other = setup();
  const dlg2 = await pairOpen(other);
  other.state.calls[0].resolve(dryAnswer({ anchor: 11, target: 20 }));
  await tick();
  assert.equal(one(dlg2, "ck-status").textContent, S.ckErrFailed);
  assert.equal(one(dlg2, "ck-apply").disabled, true);
});

test("manual offset: the field is checked, committed with Enter, and the same value is not sent", async () => {
  const ctx = setup({ list: [collection(1, 1), collection(2, 2, { clock_offset_ms: 500 })] });
  ctx.clock.align(2, ctx.opener);
  await tick();
  const dlg = dialogs()[0];
  assert.equal(ctx.state.calls.length, 0, "no records picked: nothing to dry-run");
  assert.equal(one(dlg, "ck-current").textContent, "Current offset: +0.500s.");
  assert.ok(!one(dlg, "ck-how").hidden, "it says how to pick the records");
  const manual = q(dlg, "ck-mode-input")[1];
  manual.checked = true;
  manual.dispatch("change");
  assert.equal(one(dlg, "ck-manual").hidden, false);
  assert.equal(one(dlg, "ck-pair").hidden, true);
  const input = one(dlg, "ck-input");
  input.value = "12x";
  input.dispatch("input");
  input.dispatch("change");
  assert.equal(input.getAttribute("aria-invalid"), "true");
  assert.equal(one(dlg, "ck-status").textContent, S.ckManualBad);
  assert.equal(one(dlg, "ck-apply").disabled, true);
  input.value = "500";
  input.dispatch("input");
  assert.equal(input.getAttribute("aria-invalid"), null);
  input.dispatch("change");
  assert.equal(one(dlg, "ck-status").textContent, S.ckNoChange);
  input.dispatch("keydown", { key: "Enter" });
  await tick();
  assert.equal(ctx.state.calls.length, 0, "the current value is not sent again");
  input.value = "-1234";
  input.dispatch("input");
  input.dispatch("change");
  assert.equal(one(dlg, "ck-status").textContent, "C2 will be shifted by -1.234s.");
  assert.equal(one(dlg, "ck-apply").disabled, false);
  input.dispatch("keydown", { key: "Enter" });
  await tick();
  assert.deepEqual(ctx.state.calls[0].body, { offset_ms: -1234 });
  ctx.state.calls[0].resolve(appliedAnswer({ previous_ms: 500, clock_offset_ms: -1234 }));
  await tick();
  assert.equal(isOpen(dlg), false);
  assert.ok(ctx.state.messages.at(-1)[0].includes("-1.234s"));
  assert.equal(ctx.state.applied.length, 1);
});

test("changing the mode cancels the dry run in flight and a stale answer of the first one is ignored", async () => {
  const ctx = setup();
  const dlg = await pairOpen(ctx);
  const first = ctx.state.calls[0];
  const [pair, manual] = q(dlg, "ck-mode-input");
  manual.checked = true;
  manual.dispatch("change");
  assert.equal(first.signal.aborted, true);
  pair.checked = true;
  pair.dispatch("change");
  await tick();
  assert.equal(ctx.state.calls.length, 2, "coming back to the pair asks again");
  first.resolve(dryAnswer(first.body, { clock_offset_ms: 7777 })); // late, from the aborted request
  await tick();
  assert.equal(one(dlg, "ck-apply").disabled, true, "the stale answer did not enable Apply");
  ctx.state.calls[1].resolve(dryAnswer(ctx.state.calls[1].body));
  await tick();
  assert.equal(one(dlg, "ck-apply").disabled, false);
  assert.ok(one(dlg, "ck-preview").textContent.includes("+1.500s"));
});

test("the anchor in the aligned collection, or the target elsewhere, is explained and not sent", async () => {
  const ctx = setup({ list: [collection(1, 1), collection(2, 2), collection(3, 3)] });
  ctx.clock.setAnchor(entry(10, 3));
  ctx.clock.setTarget(entry(20, 2), ctx.opener);
  await tick();
  const dlg = dialogs()[0];
  assert.equal(ctx.state.calls.length, 1);
  ctx.state.calls[0].resolve(dryAnswer(ctx.state.calls[0].body));
  await tick();
  one(dlg, "ck-cancel").click();
  // Align clock of C3 (the anchor's own collection) with the picks of the other pair
  ctx.clock.align(3, ctx.opener);
  await tick();
  const dlg3 = dialogs()[0];
  assert.equal(ctx.state.calls.length, 1, "no request: the anchor is in C3 itself");
  assert.ok(one(dlg3, "ck-hint").textContent.includes("C3"));
  assert.equal(one(dlg3, "ck-hint").hidden, false);
  one(dlg3, "ck-cancel").click();
  ctx.clock.align(1, ctx.opener);
  await tick();
  const dlg1 = dialogs()[0];
  assert.equal(ctx.state.calls.length, 1, "the target is in C2, not C1");
  assert.ok(one(dlg1, "ck-hint").textContent.includes("C2"));
});

test("Align clock needs another collection; a second align while open opens nothing new", async () => {
  const one_ = setup({ list: [collection(1, 1)] });
  await one_.clock.align(1, one_.opener);
  assert.equal(dialogs().length, 0);
  assert.deepEqual(one_.state.messages.at(-1), [S.ckAlignNeedTwo, true]);
  const ctx = setup();
  const closed = ctx.clock.align(2, ctx.opener);
  const again = ctx.clock.align(1, ctx.opener);
  await tick();
  assert.equal(dialogs().length, 1);
  one(dialogs()[0], "ck-cancel").click();
  await closed;
  await again;
  assert.equal(dialogs().length, 0);
  assert.equal(ctx.clock.isOpen(), false);
});

test("Reset clock: DELETE for the group, the old offset in the message, the list and view reload", async () => {
  const ctx = setup({ list: [collection(1, 1), collection(2, 2, { clock_offset_ms: 1500 })] });
  const done = ctx.clock.reset(2);
  await tick();
  const call = ctx.state.calls[0];
  assert.deepEqual([call.kind, call.c, call.g], ["reset", "A", 2]);
  assert.equal(ctx.clock.busy(), true);
  call.resolve(appliedAnswer({ clock_offset_ms: 0, previous_ms: 1500, clock_source: null }));
  await done;
  assert.deepEqual(ctx.state.messages.at(-1), ["Clock of C2 reset (it was +1.500s).", false]);
  assert.equal(ctx.state.applied.length, 1);
  assert.equal(ctx.clock.busy(), false);
});

test("Reset clock on a collection without an offset sends nothing; a refusal reports a static sentence", async () => {
  const ctx = setup({ list: [collection(1, 1), collection(2, 2, { clock_offset_ms: 20 })] });
  await ctx.clock.reset(1);
  assert.equal(ctx.state.calls.length, 0);
  assert.deepEqual(ctx.state.messages.at(-1), ["C1 has no clock offset.", false]);
  const done = ctx.clock.reset(2);
  await tick();
  ctx.state.calls[0].reject(apiError(404, "collection_not_found", "collection 2 does not exist (C:\\secret)"));
  await done;
  assert.deepEqual(ctx.state.messages.at(-1), [S.ckErrors.collection_not_found, true]);
  assert.equal(ctx.state.applied.length, 0);
  const net = setup({ list: [collection(1, 1), collection(2, 2, { clock_offset_ms: 20 })] });
  const second = net.clock.reset(2);
  await tick();
  net.state.calls[0].reject(Object.assign(new Error("x"), { status: 0, code: "network" }));
  await second;
  assert.deepEqual(net.state.messages.at(-1), [S.ckErrors.network, true]);
  assert.equal(net.state.applied.length, 1, "the answer was lost: the view is read again");
});

test("a Reset whose answer arrives in another case is dropped", async () => {
  const ctx = setup({ list: [collection(1, 1), collection(2, 2, { clock_offset_ms: 20 })] });
  const done = ctx.clock.reset(2);
  await tick();
  ctx.state.caseId = "B";
  ctx.state.calls[0].resolve(appliedAnswer({ clock_offset_ms: 0, previous_ms: 20 }));
  await done;
  assert.equal(ctx.state.messages.length, 0);
  assert.equal(ctx.state.applied.length, 0);
  assert.equal(ctx.clock.busy(), false);
});

test("the dialog leaves nothing behind: elements removed and no document listener added", async () => {
  const before = Object.keys(doc.listeners).map((k) => [k, (doc.listeners[k] || []).length]);
  const ctx = setup();
  const dlg = await pairOpen(ctx);
  ctx.state.calls[0].resolve(dryAnswer(ctx.state.calls[0].body));
  await tick();
  one(dlg, "ck-cancel").click();
  await tick();
  assert.equal(ctx.host.children.some((n) => n.tagName === "DIALOG"), false);
  assert.equal(doc.body.all().some((n) => n.tagName === "DIALOG"), false);
  const after = Object.keys(doc.listeners).map((k) => [k, (doc.listeners[k] || []).length]);
  assert.deepEqual(after, before);
});

test("the dialog structure: a title, a table with headers, labelled field, no innerHTML", async () => {
  const ctx = setup();
  const dlg = await pairOpen(ctx);
  assert.equal(one(dlg, "ui-dialog-title").textContent, "Align clock of C2");
  const table = dlg.all().find((n) => n.tagName === "TABLE");
  assert.ok(table);
  const heads = table.all().filter((n) => n.tagName === "TH");
  assert.deepEqual(heads.filter((h) => h.getAttribute("scope") === "col").map((h) => h.textContent), [S.ckHeadAnchor, S.ckHeadTarget]);
  assert.equal(heads.filter((h) => h.getAttribute("scope") === "row").length, 4);
  const fieldset = dlg.all().find((n) => n.tagName === "FIELDSET");
  assert.equal(fieldset.all().find((n) => n.tagName === "LEGEND").textContent, S.ckModeLegend);
  const input = one(dlg, "ck-input");
  assert.equal(input.parent.tagName, "LABEL");
  assert.equal(input.getAttribute("aria-describedby"), one(dlg, "ck-manual-hint").id);
  assert.equal(doc.activeElement, one(dlg, "ui-dialog-title"), "the focus starts on the title");
});

test("log text in a collection tag is written as text, never as markup", async () => {
  const ctx = setup({ list: [collection(1, 1, { tag: "<b>C1</b>" }), collection(2, 2)] });
  const dlg = await pairOpen(ctx);
  assert.equal(one(dlg, "ck-row-coll").children[1].textContent, "<b>C1</b>");
  assert.equal(one(dlg, "ck-row-coll").children[1].children.length, 0);
});

test("a transport that ignores the abort cannot confuse the dialog: a superseded dry run's answer or error is dropped", async () => {
  const ctx = setup({ ignoreAbort: true });
  const dlg = await pairOpen(ctx);
  const first = ctx.state.calls[0];
  const [pair, manual] = q(dlg, "ck-mode-input");
  manual.checked = true;
  manual.dispatch("change");
  pair.checked = true;
  pair.dispatch("change");
  await tick();
  const second = ctx.state.calls[1];
  first.resolve(dryAnswer(first.body, { clock_offset_ms: 7777 }));
  await tick();
  assert.equal(one(dlg, "ck-apply").disabled, true, "the superseded answer did not enable Apply");
  assert.equal(one(dlg, "ck-status").textContent, S.ckChecking);
  second.resolve(dryAnswer(second.body));
  await tick();
  assert.ok(one(dlg, "ck-preview").textContent.includes("+1.500s"));
  // an error of a superseded request
  const other = setup({ ignoreAbort: true });
  const dlg2 = await pairOpen(other);
  const old = other.state.calls[0];
  const [pair2, manual2] = q(dlg2, "ck-mode-input");
  manual2.checked = true;
  manual2.dispatch("change");
  pair2.checked = true;
  pair2.dispatch("change");
  await tick();
  old.reject(apiError(400, "no_time"));
  await tick();
  assert.equal(one(dlg2, "ck-status").textContent, S.ckChecking, "the old request's error is not shown");
  // an answer that arrives while the dialog shows the manual mode
  const third = setup({ ignoreAbort: true });
  const dlg3 = await pairOpen(third);
  const pending = third.state.calls[0];
  const manual3 = q(dlg3, "ck-mode-input")[1];
  manual3.checked = true;
  manual3.dispatch("change");
  pending.resolve(dryAnswer(pending.body));
  await tick();
  assert.equal(one(dlg3, "ck-preview").textContent, "", "nothing from the pair is shown in manual mode");
  assert.equal(one(dlg3, "ck-apply").disabled, true);
});

test("Align clock is refused while a change runs", async () => {
  const ctx = setup({ list: [collection(1, 1), collection(2, 2, { clock_offset_ms: 20 })] });
  const reset = ctx.clock.reset(2);
  await tick();
  await ctx.clock.align(1, ctx.opener);
  assert.equal(dialogs().length, 0);
  assert.deepEqual(ctx.state.messages.at(-1), [S.ckBusy, true]);
  ctx.state.calls[0].resolve(appliedAnswer({ clock_offset_ms: 0, previous_ms: 20 }));
  await reset;
  ctx.clock.align(1, ctx.opener);
  await tick();
  assert.equal(dialogs().length, 1, "once the change finished it opens");
});
