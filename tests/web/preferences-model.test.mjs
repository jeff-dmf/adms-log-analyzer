// Preferences (9.10): the pure parts and the two flows of preferences-model.js with a fake api. No DOM, no clock.
import test from "node:test";
import assert from "node:assert/strict";

import { ApiError } from "../../src/dmslog/web/js/api.js";
import {
  MAX_PACKAGE_BYTES, MAX_ROWS, buildReportView, createExportFlow, createImportFlow, defaultSelection, exportFileName,
  failureMessage, formatSize, readUtf8, renderMessage, renderMessages, selectedSections,
} from "../../src/dmslog/web/js/preferences-model.js";
import { S } from "../../src/dmslog/web/js/strings.js";

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

const section = (status, extra = {}) => ({
  status, counts: { added: 0, updated: 0, unchanged: 0, rejected: 0, removed: 0 }, items: [], removed: [], warnings: [], error: null, ...extra,
});
const report = (outcome, dryRun, sections, extra = {}) => ({
  outcome, ok: outcome === "applied" || outcome === "unchanged", applied: !dryRun && outcome === "applied", dry_run: dryRun,
  schema: { found: 1, current: 1, migrated: [] }, errors: [], sections, summary: {}, ...extra,
});
const changed = (dryRun = true, outcome = "applied") => report(outcome, dryRun, {
  presets: section("changed", { counts: { added: 1, updated: 0, unchanged: 0, rejected: 0, removed: 0 }, items: [{ index: 0, name: "P1", outcome: "added" }] }),
  alert_rules: section("absent"), redaction: section("absent"),
});

// ---- pure helpers
test("the default selection leaves the redaction section out", () => {
  assert.deepEqual(defaultSelection(), { presets: true, alert_rules: true, redaction: false });
  assert.deepEqual(selectedSections(defaultSelection()), ["presets", "alert_rules"]);
  assert.deepEqual(selectedSections({ redaction: true, presets: true, alert_rules: false }), ["presets", "redaction"]);
  assert.deepEqual(selectedSections({}), []);
});

test("the export file name has the local date and nothing else", () => {
  assert.equal(exportFileName(new Date(2026, 9, 8, 13, 5)), "dmslog-preferences-2026-10-08.json");
  assert.equal(exportFileName(new Date(NaN)), "dmslog-preferences.json");
  assert.equal(exportFileName("2026-10-08"), "dmslog-preferences.json");
});

test("sizes read in bytes, KB and MB", () => {
  assert.equal(formatSize(12), "12 bytes");
  assert.equal(formatSize(2048), "2.0 KB");
  assert.equal(formatSize(MAX_PACKAGE_BYTES), "8.0 MB");
  assert.equal(formatSize(-1), "");
  assert.equal(formatSize(NaN), "");
});

test("the limit mirrors the server (8 MiB)", () => {
  assert.equal(MAX_PACKAGE_BYTES, 8 * 1024 * 1024);
});

// ---- the report as a view
test("what is not a report is null", () => {
  for (const junk of [null, undefined, "x", 3, [], {}, { outcome: "applied" }, { outcome: "applied", sections: {} },
    { outcome: "weird", sections: {}, dry_run: true }, { outcome: "applied", sections: [], dry_run: true },
    { outcome: "applied", sections: {}, dry_run: "yes" }]) {
    assert.equal(buildReportView(junk), null, JSON.stringify(junk));
  }
});

test("the headline depends on the outcome and on whether it was a check", () => {
  for (const outcome of ["rejected", "partial", "applied", "unchanged", "failed"]) {
    for (const dry of [true, false]) {
      const view = buildReportView(report(outcome, dry, {}));
      const key = "pfOut" + outcome[0].toUpperCase() + outcome.slice(1) + (dry ? "Check" : "Done");
      assert.equal(view.headline.key, key);
      assert.ok(S[key], key);
      assert.equal(renderMessage(view.headline), S[key]);
    }
  }
});

test("Apply is offered only after a check that found something to write", () => {
  assert.equal(buildReportView(changed(true, "applied")).canApply, true);
  assert.equal(buildReportView(changed(true, "partial")).canApply, true);
  assert.equal(buildReportView(changed(false, "applied")).canApply, false, "a real run is not a check");
  assert.equal(buildReportView(changed(true, "rejected")).canApply, false);
  assert.equal(buildReportView(changed(true, "failed")).canApply, false);
  assert.equal(buildReportView(report("unchanged", true, { presets: section("unchanged") })).canApply, false);
  assert.equal(buildReportView(report("partial", true, { presets: section("rejected"), redaction: section("unchanged") })).canApply, false);
});

test("sections come in the fixed order and unknown ones are ignored", () => {
  const view = buildReportView(report("applied", true, {
    redaction: section("absent"), other: section("changed"), presets: section("changed"), alert_rules: section("unchanged"),
  }));
  assert.deepEqual(view.sections.map((s) => s.id), ["presets", "alert_rules", "redaction"]);
});

test("item names are clean labels, the rows are capped, outcomes and counts are checked", () => {
  const items = Array.from({ length: MAX_ROWS + 5 }, (_, index) => ({ index, name: "n\u0000a\nme" + "x".repeat(200), outcome: index === 0 ? "bogus" : "added" }));
  const view = buildReportView(report("applied", true, {
    presets: section("changed", { items, counts: { added: -1, updated: 1.5, unchanged: "2", rejected: 3, removed: 4 } }),
  }));
  const [presets] = view.sections;
  assert.equal(presets.items.length, MAX_ROWS);
  assert.equal(presets.hiddenItems, 5);
  assert.ok(presets.items[0].name.length <= 80);
  assert.ok(!/\p{Cc}/u.test(presets.items[0].name));
  assert.equal(presets.items[0].outcome, "unknown");
  assert.deepEqual(presets.counts, { added: 0, updated: 0, unchanged: 0, rejected: 3, removed: 4 });
  assert.equal(presets.items[1].number, 2);
});

test("a reason is a static text by code: the server's own message is never used", () => {
  const view = buildReportView(report("partial", true, {
    presets: section("rejected", {
      error: { code: "section_rejected", message: "SERVER TEXT with C:\\secret and a value" },
      warnings: [{ code: "existing_items_dropped", message: "SERVER WARNING" }],
      items: [
        { index: 0, name: "a", outcome: "rejected", code: "bad_filter", message: "FILTER TEXT" },
        { index: 1, name: "b", outcome: "rejected", code: "a_code_from_the_future", message: "NEW TEXT" },
      ],
    }),
  }, { errors: [{ code: "bad_json", message: "SERVER STRUCTURAL" }] }));
  const [presets] = view.sections;
  const texts = [renderMessage(presets.error), renderMessage(presets.warnings[0]), renderMessage(presets.items[0].reason),
    renderMessage(presets.items[1].reason), renderMessages(view.errors)];
  assert.deepEqual(texts, [S.pfReason.section_rejected, S.pfReason.existing_items_dropped, S.pfReason.bad_filter,
    S.pfReason.unknown, S.pfStructural.bad_json]);
  assert.ok(!JSON.stringify(texts).includes("SERVER"));
});

test("the redaction section says the whole profile is replaced when it changes", () => {
  const view = buildReportView(report("applied", true, { redaction: section("changed") }));
  assert.equal(view.sections[0].noteKey, "pfRedactionNote");
  assert.ok(S.pfRedactionNote);
  assert.equal(buildReportView(report("applied", true, { redaction: section("unchanged") })).sections[0].noteKey, null);
});

test("a section whose state is unknown says so", () => {
  const view = buildReportView(report("failed", false, { presets: section("rejected", { state: "unknown" }) }));
  assert.equal(view.sections[0].statusKey, "pfStatusUnknown");
  assert.equal(view.sections[0].unknown, true);
});

test("an older format that was upgraded is reported", () => {
  const view = buildReportView(report("applied", true, {}, { schema: { found: 0, current: 1, migrated: [0] } }));
  assert.equal(view.migratedFrom, 0);
  assert.equal(buildReportView(report("applied", true, {})).migratedFrom, null);
});

// ---- failures
test("failures map to static texts; an abort says nothing", () => {
  const abort = Object.assign(new Error("x"), { name: "AbortError" });
  assert.equal(failureMessage(abort), null);
  assert.equal(failureMessage(new ApiError(0, "network", "SERVER")).key, "pfErrNetwork");
  assert.equal(failureMessage(new ApiError(403, "forbidden", "SERVER")).key, "pfErrSession");
  assert.equal(failureMessage(new ApiError(500, "http_500", "SERVER")).key, "pfErrServer");
  assert.equal(failureMessage(new ApiError(413, "package_too_large", "SERVER")).key, "pfErrTooLarge");
  assert.equal(failureMessage(new ApiError(400, "weird", "SERVER")).key, "pfErrRequest");
  assert.deepEqual(failureMessage(new ApiError(400, "bad_json", "SERVER")), { map: "pfStructural", code: "bad_json" });
  assert.equal(failureMessage(new ApiError(400, "constructor", "SERVER")).key, "pfErrRequest", "no prototype keys");
  assert.equal(renderMessage(failureMessage(new ApiError(413, "x", "SERVER"))), S.pfErrTooLarge.replace("{limit}", "8.0 MB"));
});

test("after an Apply, a lost answer is an unknown outcome, not a plain failure", () => {
  for (const err of [new ApiError(0, "network", ""), new ApiError(500, "http_500", ""), new ApiError(408, "timeout", ""), new TypeError("x")]) {
    assert.equal(failureMessage(err, { unknownOutcome: true }).key, "pfErrApplyUnknown");
  }
  assert.equal(failureMessage(new ApiError(400, "weird", ""), { unknownOutcome: true }).key, "pfErrRequest");
});

// ---- export flow
function exportSetup(extra = {}) {
  const calls = [];
  const saved = [];
  const states = [];
  const api = { preferences: { export(sections, signal) { const d = deferred(); calls.push({ sections, signal, d }); return d.promise; } } };
  const flow = createExportFlow({
    api, save: (name, text) => saved.push([name, text]), now: () => new Date(2026, 9, 18), onState: (s) => states.push(s), ...extra,
  });
  return { flow, calls, saved, states };
}
const answer = (sections, warnings = []) => ({ package: { schema: 1, kind: "dmslog-preferences", ...Object.fromEntries(sections.map((id) => [id, []])) }, text: "{}\n", warnings });

test("export asks for the default sections (no redaction) and saves the text under a dated name", async () => {
  const { flow, calls, saved } = exportSetup();
  const running = flow.run();
  assert.deepEqual(calls[0].sections, ["presets", "alert_rules"]);
  assert.equal(flow.state.phase, "working");
  calls[0].d.resolve(answer(["presets", "alert_rules"]));
  assert.equal(await running, true);
  assert.deepEqual(saved, [["dmslog-preferences-2026-10-18.json", "{}\n"]]);
  assert.equal(flow.state.phase, "idle");
  assert.match(renderMessages(flow.state.messages), /dmslog-preferences-2026-10-18\.json/);
  assert.match(renderMessages(flow.state.messages), /View presets, Alert rules/);
});

test("the redaction section is sent only when it was chosen", async () => {
  const { flow, calls } = exportSetup();
  flow.setSection("redaction", true);
  flow.run();
  assert.deepEqual(calls[0].sections, ["presets", "alert_rules", "redaction"]);
});

test("with nothing chosen nothing is requested", async () => {
  const { flow, calls } = exportSetup();
  flow.setSection("presets", false);
  flow.setSection("alert_rules", false);
  assert.equal(await flow.run(), false);
  assert.equal(calls.length, 0);
  assert.equal(flow.state.isError, true);
  assert.equal(renderMessages(flow.state.messages), S.pfErrNoSection);
});

test("a second run and a change of the selection are ignored while one is in flight", async () => {
  const { flow, calls } = exportSetup();
  flow.run();
  assert.equal(await flow.run(), false);
  assert.equal(flow.setSection("redaction", true), false);
  assert.equal(flow.state.selection.redaction, false);
  assert.equal(calls.length, 1);
});

test("a reset or a dispose while waiting drops the answer: nothing is saved", async () => {
  for (const stop of ["reset", "dispose"]) {
    const { flow, calls, saved } = exportSetup();
    const running = flow.run();
    flow[stop]();
    assert.equal(calls[0].signal.aborted, true, stop);
    calls[0].d.resolve(answer(["presets"]));
    assert.equal(await running, false);
    assert.deepEqual(saved, [], stop);
  }
});

test("a failure shows a static text and leaves nothing saved; the selection survives", async () => {
  const { flow, calls, saved } = exportSetup();
  flow.setSection("redaction", true);
  const running = flow.run();
  calls[0].d.reject(new ApiError(500, "boom", "SERVER SAYS C:\\secret"));
  await running;
  assert.equal(flow.state.phase, "idle");
  assert.equal(flow.state.isError, true);
  assert.equal(renderMessages(flow.state.messages), S.pfErrServer);
  assert.deepEqual(saved, []);
  assert.equal(flow.state.selection.redaction, true);
  assert.equal(await (async () => { const again = flow.run(); calls[1].d.resolve(answer(["presets"])); return again; })(), true, "can be repeated");
});

test("an answer that is not a package is refused; a save that throws is reported", async () => {
  const bad = exportSetup();
  const first = bad.flow.run();
  bad.calls[0].d.resolve({ text: 5 });
  await first;
  assert.equal(renderMessages(bad.flow.state.messages), S.pfErrBadReport);
  const broken = exportSetup({ save: () => { throw new Error("no"); } });
  const second = broken.flow.run();
  broken.calls[0].d.resolve(answer(["presets"]));
  await second;
  assert.equal(renderMessages(broken.flow.state.messages), S.pfErrSave);
  assert.equal(broken.flow.state.phase, "idle");
});

test("a section left out is said; so is a redaction section that does not exist yet", async () => {
  const { flow, calls } = exportSetup();
  flow.setSection("redaction", true);
  const running = flow.run();
  calls[0].d.resolve(answer(["presets"], [{ section: "alert_rules", code: "file_invalid", message: "SERVER" }]));
  await running;
  const text = renderMessages(flow.state.messages);
  assert.match(text, /Alert rules was left out/);
  assert.match(text, new RegExp(S.pfExportNoRedaction.slice(0, 20)));
  assert.ok(!text.includes("SERVER"));
});

test("choosing a section clears the last message (one live region, nothing stale)", () => {
  const { flow } = exportSetup();
  flow.setSection("presets", false);
  flow.setSection("alert_rules", false);
  flow.run();
  const seq = flow.state.seq;
  flow.setSection("presets", true);
  assert.notEqual(flow.state.seq, seq);
  assert.deepEqual(flow.state.messages, []);
});

// ---- import flow
function importSetup(extra = {}) {
  const calls = [];
  const states = [];
  const applied = [];
  const reads = [];
  const api = { preferences: { import(body, signal) { const d = deferred(); calls.push({ body, signal, d }); return d.promise; } } };
  const flow = createImportFlow({
    api,
    readFile: (file) => { const d = deferred(); reads.push({ file, d }); return d.promise; },
    onState: (s) => states.push(s),
    onApplied: (info) => applied.push(info),
    ...extra,
  });
  return { flow, calls, states, applied, reads };
}
const fileOf = (name, size = 100) => ({ name, size });

// Gets the flow to "ready" with `text`.
async function ready(setup, text = '{"schema":1}', name = "p.json") {
  const choosing = setup.flow.chooseFile(fileOf(name));
  setup.reads[setup.reads.length - 1].d.resolve(text);
  await choosing;
}
async function checked(setup, view = changed(true)) {
  await ready(setup);
  const checking = setup.flow.check();
  setup.calls[setup.calls.length - 1].d.resolve(view);
  await checking;
}

test("a file over the limit or empty is refused before it is read", async () => {
  const s = importSetup();
  assert.equal(await s.flow.chooseFile(fileOf("big.json", MAX_PACKAGE_BYTES + 1)), false);
  assert.equal(s.reads.length, 0);
  assert.equal(s.flow.state.phase, "empty");
  assert.equal(s.flow.state.file, null);
  assert.equal(renderMessages(s.flow.state.messages), S.pfErrTooLarge.replace("{limit}", "8.0 MB"));
  assert.equal(await s.flow.chooseFile(fileOf("none.json", 0)), false);
  assert.equal(renderMessages(s.flow.state.messages), S.pfErrEmptyFile);
  assert.equal(await s.flow.chooseFile(fileOf("odd.json", NaN)), false);
  assert.equal(renderMessages(s.flow.state.messages), S.pfErrRead);
  assert.equal(s.reads.length, 0);
});

test("the limit itself is accepted", async () => {
  const s = importSetup();
  const choosing = s.flow.chooseFile(fileOf("edge.json", MAX_PACKAGE_BYTES));
  assert.equal(s.flow.state.phase, "reading");
  s.reads[0].d.resolve("{}");
  assert.equal(await choosing, true);
});

test("text that is too long once read, blank or not text is refused", async () => {
  for (const [content, key] of [["x".repeat(MAX_PACKAGE_BYTES + 1), "pfErrTooLarge"], ["  \n", "pfErrEmptyFile"], [null, "pfErrRead"]]) {
    const s = importSetup();
    const choosing = s.flow.chooseFile(fileOf("a.json"));
    s.reads[0].d.resolve(content);
    assert.equal(await choosing, false);
    assert.equal(s.flow.state.phase, "empty");
    assert.equal(s.flow.state.messages[0].key, key);
  }
});

test("a read that fails says so and leaves no file", async () => {
  const s = importSetup();
  const choosing = s.flow.chooseFile(fileOf("a.json"));
  s.reads[0].d.reject(new Error("NotReadableError C:\\secret"));
  await choosing;
  assert.equal(s.flow.state.phase, "empty");
  assert.equal(s.flow.state.file, null);
  assert.equal(renderMessages(s.flow.state.messages), S.pfErrRead);
});

test("a late read of an old file never replaces the new one", async () => {
  const s = importSetup();
  const first = s.flow.chooseFile(fileOf("old.json"));
  const second = s.flow.chooseFile(fileOf("new.json"));
  s.reads[1].d.resolve('{"new":1}');
  await second;
  s.reads[0].d.resolve('{"old":1}');
  await first;
  assert.equal(s.flow.state.file.name, "new.json");
  s.flow.check();
  assert.equal(s.calls[0].body.package, '{"new":1}');
});

test("Check sends the text, a dry run, the mode and the policy; a double click sends one request", async () => {
  const s = importSetup();
  await ready(s, '{"a":1}');
  s.flow.setMode("replace");
  s.flow.setSkipInvalid(true);
  const checking = s.flow.check();
  assert.equal(await s.flow.check(), false);
  assert.equal(s.calls.length, 1);
  assert.deepEqual(s.calls[0].body, { package: '{"a":1}', dry_run: true, mode: "replace", on_item_error: "skip_item" });
  assert.equal(s.flow.state.phase, "checking");
  s.calls[0].d.resolve(changed(true));
  assert.equal(await checking, true);
  assert.equal(s.flow.state.phase, "checked");
  assert.equal(s.flow.state.canApply, true);
  assert.equal(renderMessages(s.flow.state.messages), S.pfOutAppliedCheck);
});

test("Check needs a file; Apply needs a Check that found something", async () => {
  const s = importSetup();
  assert.equal(await s.flow.check(), false);
  assert.equal(await s.flow.apply(), false);
  await ready(s);
  assert.equal(await s.flow.apply(), false, "no check yet");
  assert.equal(s.calls.length, 0);
  const checking = s.flow.check();
  s.calls[0].d.resolve(report("unchanged", true, { presets: section("unchanged") }));
  await checking;
  assert.equal(s.flow.state.canApply, false);
  assert.equal(await s.flow.apply(), false);
  assert.equal(s.calls.length, 1);
});

test("choosing another file after a Check drops the report: Apply cannot send the old one", async () => {
  const s = importSetup();
  await checked(s);
  assert.equal(s.flow.state.canApply, true);
  const choosing = s.flow.chooseFile(fileOf("other.json"));
  assert.equal(s.flow.state.report, null);
  assert.equal(s.flow.state.canApply, false);
  assert.equal(await s.flow.apply(), false);
  s.reads[1].d.resolve('{"other":1}');
  await choosing;
  assert.equal(s.flow.state.phase, "ready");
  assert.equal(await s.flow.apply(), false, "the new file was not checked");
  assert.equal(s.calls.length, 1);
});

test("changing the mode or the policy after a Check drops the report", async () => {
  for (const change of [(f) => f.setMode("replace"), (f) => f.setSkipInvalid(true)]) {
    const s = importSetup();
    await checked(s);
    assert.equal(change(s.flow), true);
    assert.equal(s.flow.state.report, null);
    assert.equal(s.flow.state.phase, "ready");
    assert.equal(await s.flow.apply(), false);
    assert.deepEqual(s.flow.state.messages, []);
  }
});

test("an option set to its current value, or a mode that does not exist, changes nothing", async () => {
  const s = importSetup();
  await checked(s);
  assert.equal(s.flow.setMode("merge"), false);
  assert.equal(s.flow.setSkipInvalid(false), false);
  assert.equal(s.flow.setMode("destroy"), false);
  assert.equal(s.flow.state.phase, "checked");
});

test("a Check answered after an option changed is dropped", async () => {
  const s = importSetup();
  await ready(s);
  const checking = s.flow.check();
  s.flow.setMode("replace");
  assert.equal(s.calls[0].signal.aborted, true);
  s.calls[0].d.resolve(changed(true));
  assert.equal(await checking, false);
  assert.equal(s.flow.state.report, null);
  assert.equal(s.flow.state.phase, "ready");
  assert.equal(s.flow.state.canApply, false);
});

test("a Check answered after another file was chosen is dropped", async () => {
  const s = importSetup();
  await ready(s);
  const checking = s.flow.check();
  const choosing = s.flow.chooseFile(fileOf("b.json"));
  s.calls[0].d.resolve(changed(true));
  await checking;
  assert.equal(s.flow.state.report, null);
  assert.equal(s.flow.state.phase, "reading");
  s.reads[1].d.resolve("{}");
  await choosing;
  assert.equal(s.flow.state.phase, "ready");
});

test("a newer Check wins over an older answer", async () => {
  const s = importSetup();
  await ready(s);
  const first = s.flow.check();
  s.flow.setSkipInvalid(true); // invalidates the first
  const second = s.flow.check();
  s.calls[0].d.resolve(report("rejected", true, {}));
  await first;
  assert.equal(s.flow.state.phase, "checking");
  s.calls[1].d.resolve(changed(true));
  await second;
  assert.equal(s.flow.state.report.outcome, "applied");
});

test("Apply sends exactly what Check sent, with dry_run false, and reloads once", async () => {
  const s = importSetup();
  await ready(s, '{"x":2}');
  s.flow.setMode("merge");
  s.flow.setSkipInvalid(true);
  const checking = s.flow.check();
  s.calls[0].d.resolve(changed(true));
  await checking;
  const applying = s.flow.apply();
  assert.equal(s.flow.state.phase, "applying");
  assert.equal(await s.flow.apply(), false, "a double click sends one request");
  assert.deepEqual(s.calls[1].body, { ...s.calls[0].body, dry_run: false });
  s.calls[1].d.resolve(changed(false));
  assert.equal(await applying, true);
  assert.equal(s.flow.state.phase, "applied");
  assert.equal(s.flow.state.canApply, false);
  assert.equal(s.flow.state.report.dryRun, false);
  assert.equal(renderMessages(s.flow.state.messages), S.pfOutAppliedDone);
  assert.deepEqual(s.applied, [{ unknown: false }]);
  assert.equal(await s.flow.apply(), false, "an applied report is not applied again");
  assert.equal(s.calls.length, 2);
});

test("Replace asks first, in a way that does not block, and only the answer goes on", async () => {
  const s = importSetup();
  await ready(s);
  s.flow.setMode("replace");
  const checking = s.flow.check();
  s.calls[0].d.resolve(changed(true));
  await checking;
  assert.equal(await s.flow.apply(), "confirm");
  assert.equal(s.flow.state.confirming, true);
  assert.equal(s.calls.length, 1);
  s.flow.cancelConfirm();
  assert.equal(s.flow.state.confirming, false);
  assert.equal(s.flow.state.canApply, true);
  assert.equal(renderMessages(s.flow.state.messages), S.pfConfirmCancelled);
  assert.equal(s.calls.length, 1);
  assert.equal(await s.flow.apply(), "confirm");
  const applying = s.flow.apply({ confirmed: true });
  assert.equal(s.calls[1].body.mode, "replace");
  assert.equal(s.calls[1].body.dry_run, false);
  assert.equal(s.flow.state.confirming, false);
  s.calls[1].d.resolve(changed(false));
  await applying;
  assert.equal(s.applied.length, 1);
});

test("a question that is open goes away when the option or the file changes", async () => {
  const s = importSetup();
  await ready(s);
  s.flow.setMode("replace");
  const checking = s.flow.check();
  s.calls[0].d.resolve(changed(true));
  await checking;
  await s.flow.apply();
  assert.equal(s.flow.state.confirming, true);
  s.flow.setMode("merge");
  assert.equal(s.flow.state.confirming, false);
  assert.equal(s.flow.state.report, null);
});

test("merge needs no question", async () => {
  const s = importSetup();
  await checked(s);
  const applying = s.flow.apply();
  assert.equal(s.calls.length, 2);
  s.calls[1].d.resolve(changed(false));
  await applying;
});

test("while an Apply is in flight the file and the options cannot change", async () => {
  const s = importSetup();
  await checked(s);
  const applying = s.flow.apply();
  assert.equal(await s.flow.chooseFile(fileOf("other.json")), false);
  assert.equal(s.flow.setMode("replace"), false);
  assert.equal(s.flow.setSkipInvalid(true), false);
  assert.equal(s.reads.length, 1);
  assert.equal(s.flow.state.mode, "merge");
  s.calls[1].d.resolve(changed(false));
  await applying;
  assert.equal(s.flow.state.file.name, "p.json");
});

test("an Apply that is lost (network, 5xx) has an unknown outcome: the report is dropped and the reload still runs", async () => {
  for (const err of [new ApiError(0, "network", "SERVER"), new ApiError(500, "http_500", "SERVER")]) {
    const s = importSetup();
    await checked(s);
    const applying = s.flow.apply();
    s.calls[1].d.reject(err);
    await applying;
    assert.equal(s.flow.state.report, null);
    assert.equal(s.flow.state.phase, "ready");
    assert.equal(s.flow.state.isError, true);
    assert.equal(renderMessages(s.flow.state.messages), S.pfErrApplyUnknown);
    assert.deepEqual(s.applied, [{ unknown: true }]);
    assert.equal(s.flow.state.canCheck, true);
  }
});

test("an Apply the server refused changed nothing: no reload", async () => {
  const s = importSetup();
  await checked(s);
  const applying = s.flow.apply();
  s.calls[1].d.reject(new ApiError(413, "package_too_large", "SERVER"));
  await applying;
  assert.deepEqual(s.applied, []);
  assert.equal(renderMessages(s.flow.state.messages), S.pfErrTooLarge.replace("{limit}", "8.0 MB"));
});

test("an Apply that wrote nothing does not reload", async () => {
  const s = importSetup();
  await checked(s);
  const applying = s.flow.apply();
  s.calls[1].d.resolve(report("rejected", false, { presets: section("rejected") }));
  assert.equal(await applying, false);
  assert.deepEqual(s.applied, []);
  assert.equal(s.flow.state.phase, "applied");
});

test("answers that are not the report asked for are refused", async () => {
  const a = importSetup();
  await ready(a);
  const checking = a.flow.check();
  a.calls[0].d.resolve(changed(false)); // a check must come back as a dry run
  await checking;
  assert.equal(a.flow.state.phase, "ready");
  assert.equal(renderMessages(a.flow.state.messages), S.pfErrBadReport);
  const b = importSetup();
  await checked(b);
  const applying = b.flow.apply();
  b.calls[1].d.resolve(changed(true)); // an apply must not come back as a dry run
  await applying;
  assert.deepEqual(b.applied, [{ unknown: true }]);
  assert.equal(renderMessages(b.flow.state.messages), S.pfErrApplyUnknown);
  const c = importSetup();
  await ready(c);
  const third = c.flow.check();
  c.calls[0].d.resolve({ nothing: true });
  await third;
  assert.equal(renderMessages(c.flow.state.messages), S.pfErrBadReport);
});

test("a failed Check keeps the file and can be repeated", async () => {
  const s = importSetup();
  await ready(s);
  const first = s.flow.check();
  s.calls[0].d.reject(new ApiError(400, "bad_json", "SERVER"));
  await first;
  assert.equal(s.flow.state.phase, "ready");
  assert.equal(renderMessages(s.flow.state.messages), S.pfStructural.bad_json);
  const second = s.flow.check();
  s.calls[1].d.resolve(changed(true));
  assert.equal(await second, true);
});

test("a Check can be run again after an Apply", async () => {
  const s = importSetup();
  await checked(s);
  const applying = s.flow.apply();
  s.calls[1].d.resolve(changed(false));
  await applying;
  const again = s.flow.check();
  assert.equal(s.calls[2].body.dry_run, true);
  s.calls[2].d.resolve(report("unchanged", true, { presets: section("unchanged") }));
  await again;
  assert.equal(s.flow.state.report.outcome, "unchanged");
});

test("a reset (the dialog closed) stops everything and forgets the file", async () => {
  const s = importSetup();
  await ready(s);
  const checking = s.flow.check();
  s.flow.reset();
  assert.equal(s.calls[0].signal.aborted, true);
  s.calls[0].d.resolve(changed(true));
  await checking;
  assert.equal(s.flow.state.phase, "empty");
  assert.equal(s.flow.state.file, null);
  assert.equal(s.flow.state.report, null);
  assert.equal(await s.flow.check(), false, "the text is gone");
  const reading = importSetup();
  const choosing = reading.flow.chooseFile(fileOf("a.json"));
  reading.flow.reset();
  reading.reads[0].d.resolve("{}");
  await choosing;
  assert.equal(reading.flow.state.phase, "empty", "a read finished after the close is dropped");
});

test("a reset forgets the mode and the policy too", async () => {
  const s = importSetup();
  s.flow.setMode("replace");
  s.flow.setSkipInvalid(true);
  s.flow.reset();
  assert.equal(s.flow.state.mode, "merge");
  assert.equal(s.flow.state.skipInvalid, false);
});

test("a dispose is final: nothing is reported after it", async () => {
  const s = importSetup();
  await ready(s);
  const checking = s.flow.check();
  const seen = s.states.length;
  s.flow.dispose();
  s.calls[0].d.resolve(changed(true));
  await checking;
  await s.flow.chooseFile(fileOf("x.json"));
  assert.equal(s.states.length, seen);
  assert.equal(await s.flow.check(), false);
});

test("a reload hook that throws does not undo a finished import", async () => {
  const s = importSetup({ onApplied: () => { throw new Error("hook"); } });
  await checked(s);
  const applying = s.flow.apply();
  s.calls[1].d.resolve(changed(false));
  assert.equal(await applying, true);
  assert.equal(s.flow.state.phase, "applied");
});

// ---- 9.10 review round: an Apply survives the close, the file is read as strict UTF-8
test("a reset during an Apply does not stop it: the reload still runs and nothing is kept afterwards", async () => {
  const s = importSetup();
  await checked(s);
  const applying = s.flow.apply();
  s.flow.reset();
  assert.equal(s.calls[1].signal.aborted, false, "the wait is not aborted");
  assert.equal(s.flow.state.phase, "applying");
  assert.equal(s.flow.state.busy, true);
  assert.equal(await s.flow.chooseFile(fileOf("again.json")), false, "no new import while the Apply runs");
  assert.equal(await s.flow.check(), false);
  s.calls[1].d.resolve(changed(false));
  assert.equal(await applying, true);
  assert.deepEqual(s.applied, [{ unknown: false }]);
  assert.equal(s.flow.state.phase, "empty", "closed dialog: the file, the report and the text are forgotten");
  assert.equal(s.flow.state.report, null);
  assert.equal(s.flow.state.file, null);
});

test("a reset during an Apply that is lost still reloads, as unknown", async () => {
  const s = importSetup();
  await checked(s);
  const applying = s.flow.apply();
  s.flow.reset();
  s.calls[1].d.reject(new ApiError(0, "network", "SERVER"));
  await applying;
  assert.deepEqual(s.applied, [{ unknown: true }]);
  assert.equal(s.flow.state.phase, "empty");
});

test("reopening during an Apply keeps it frozen and shows its result", async () => {
  const s = importSetup();
  await checked(s);
  const applying = s.flow.apply();
  s.flow.reset();
  s.flow.attach();
  assert.equal(s.flow.state.frozen, true);
  assert.equal(s.flow.setMode("replace"), false);
  s.calls[1].d.resolve(changed(false));
  await applying;
  assert.equal(s.flow.state.phase, "applied");
  assert.equal(s.flow.state.report.dryRun, false);
});

test("a dispose during an Apply still asks for the reload (the outcome is unknown)", async () => {
  const s = importSetup();
  await checked(s);
  const applying = s.flow.apply();
  s.flow.dispose();
  s.calls[1].d.resolve(changed(false));
  await applying;
  assert.deepEqual(s.applied, [{ unknown: true }]);
});

const fileOfBytes = (bytes) => ({ name: "p.json", size: bytes.length, arrayBuffer: async () => Uint8Array.from(bytes).buffer });

test("the default reader decodes UTF-8 strictly: invalid bytes are an error, never U+FFFD", async () => {
  const good = [...new TextEncoder().encode('{"n":"caf\u00e9 \u00e9"}')];
  assert.equal(await readUtf8(fileOfBytes(good)), '{"n":"caf\u00e9 \u00e9"}');
  const accented = [...new TextEncoder().encode('{"n":"café"}')];
  assert.equal(await readUtf8(fileOfBytes(accented)), '{"n":"café"}');
  const bad = [...new TextEncoder().encode('{"n":"caf')], tail = [0xe9, 0x22, 0x7d];
  await assert.rejects(readUtf8(fileOfBytes([...bad, ...tail])), (err) => err.code === "not_utf8");
});

test("the reader keeps a BOM as it is (the server decides: it refuses it)", async () => {
  const text = await readUtf8(fileOfBytes([0xef, 0xbb, 0xbf, 0x7b, 0x7d]));
  assert.equal(text, "\ufeff{}");
});

test("a file with invalid UTF-8 is a static error and leaves no file", async () => {
  const s = importSetup({ readFile: readUtf8 });
  const f = fileOfBytes([0x7b, 0xe9, 0x7d]);
  assert.equal(await s.flow.chooseFile(f), false);
  assert.equal(s.flow.state.phase, "empty");
  assert.equal(s.flow.state.file, null);
  assert.equal(renderMessages(s.flow.state.messages), S.pfErrNotUtf8);
  assert.equal(s.calls.length, 0, "nothing is sent");
});
