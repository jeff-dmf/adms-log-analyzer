// Pure helpers of the export panel and the redaction settings.
import test from "node:test";
import assert from "node:assert/strict";

import {
  EXPORT_LIMITS,
  buildExportRequest,
  buildSelection,
  copyText,
  defaultSelectionKind,
  formToProfile,
  legendRows,
  normalizeOptions,
  parseLines,
  profileToForm,
  selectionAvailability,
} from "../../src/dmslog/web/js/export-model.js";

const ctx = (extra) => ({ caseId: "demo", filter: { sev: { le: 2 } }, recordId: null, eventId: null, ...extra });

test("which selections are available", () => {
  assert.deepEqual(selectionAvailability(null), { filter: false, record: false, event: false });
  assert.deepEqual(selectionAvailability(ctx()), { filter: true, record: false, event: false });
  assert.deepEqual(selectionAvailability(ctx({ recordId: 5 })), { filter: true, record: true, event: false });
  assert.deepEqual(selectionAvailability(ctx({ recordId: 5, eventId: 9 })), { filter: true, record: true, event: true });
  assert.deepEqual(selectionAvailability(ctx({ recordId: "5", eventId: 0 })), { filter: true, record: false, event: false });
  assert.equal(selectionAvailability({ caseId: null, recordId: 5 }).record, false);
});

test("the default selection prefers the event, then the record, then the filter", () => {
  assert.equal(defaultSelectionKind(ctx({ recordId: 5, eventId: 9 })), "event");
  assert.equal(defaultSelectionKind(ctx({ recordId: 5 })), "record");
  assert.equal(defaultSelectionKind(ctx()), "filter");
});

test("selection objects", () => {
  assert.deepEqual(buildSelection("record", ctx({ recordId: 5 })), { ids: [5] });
  assert.deepEqual(buildSelection("event", ctx({ eventId: 9 })), { events: [9] });
  assert.deepEqual(buildSelection("filter", ctx()), { filter: { sev: { le: 2 } } });
  // no filter at all still exports (the first records), and the server never gets an empty object
  assert.deepEqual(buildSelection("filter", ctx({ filter: null })), { filter: { and: [] } });
  assert.deepEqual(buildSelection("filter", ctx({ filter: {} })), { filter: { and: [] } });
  assert.equal(buildSelection("record", ctx()), null);
  assert.equal(buildSelection("nope", ctx()), null);
});

test("options are clamped and anonymization is always sent", () => {
  assert.deepEqual(normalizeOptions(undefined), { anonymize: false, include_payloads: false, context: 0, max_bytes: 30000 });
  assert.deepEqual(
    normalizeOptions({ anonymize: false, includePayloads: true, context: "7", maxBytes: "50000" }),
    { anonymize: false, include_payloads: true, context: 7, max_bytes: 50000 },
  );
  const wild = normalizeOptions({ context: "9999", maxBytes: "5" });
  assert.equal(wild.context, EXPORT_LIMITS.contextMax);
  assert.equal(wild.max_bytes, EXPORT_LIMITS.maxBytesMin);
  assert.equal(normalizeOptions({ maxBytes: "999999999" }).max_bytes, EXPORT_LIMITS.maxBytesMax);
  assert.equal(normalizeOptions({ context: "-3" }).context, 0);
  assert.equal(normalizeOptions({ context: "abc", maxBytes: "" }).max_bytes, 30000);
  // U18: the original text is the default; only an explicit true turns anonymization on
  for (const value of [undefined, null, "on", 1, "true", false]) {
    assert.equal(normalizeOptions({ anonymize: value }).anonymize, false);
  }
  assert.equal(normalizeOptions({ anonymize: true }).anonymize, true);
  assert.equal(normalizeOptions({ includePayloads: "yes" }).include_payloads, false);
});

test("export request", () => {
  const ok = buildExportRequest("record", ctx({ recordId: 5 }), { context: 2, anonymize: true });
  assert.deepEqual(ok, {
    caseId: "demo",
    selection: { ids: [5] },
    options: { anonymize: true, include_payloads: false, context: 2, max_bytes: 30000 },
  });
  assert.deepEqual(buildExportRequest("filter", { caseId: null }, {}), { error: "exportNoCase" });
  assert.deepEqual(buildExportRequest("event", ctx(), {}), { error: "exportNoSelection" });
  assert.deepEqual(buildExportRequest(null, ctx(), {}), { error: "exportNoSelection" });
});

test("legend rows keep categories and counts only", () => {
  assert.deepEqual(legendRows([{ category: "guid", label: "GUID", distinct: 2, replaced: "5", extra: "x" }]), [
    { label: "GUID", distinct: 2, replaced: 5 },
  ]);
  assert.deepEqual(legendRows(null), []);
});

test("lines are trimmed, deduplicated and empty ones dropped", () => {
  assert.deepEqual(parseLines(" a \r\n\r\nb\na\n  \n c"), ["a", "b", "c"]);
  assert.deepEqual(parseLines(undefined), []);
  assert.deepEqual(parseLines(""), []);
});

test("profile <-> form round trip", () => {
  const profile = {
    name: "mine",
    categories: { guid: true, ip: false },
    terms: { word: ["Acme", "Beta"], literal: ["SE-1"] },
    exceptions: ["SendWorks"],
    payload_names: ["Name", "Phone"],
  };
  const form = profileToForm(profile);
  assert.equal(form.word, "Acme\nBeta");
  assert.equal(form.names, "Name\nPhone");
  assert.deepEqual(formToProfile(form), profile);
  const edited = formToProfile({ ...form, word: "  Acme \n\nGamma\nGamma", exceptions: "" });
  assert.deepEqual(edited.terms.word, ["Acme", "Gamma"]);
  assert.deepEqual(edited.exceptions, []);
  assert.deepEqual(profileToForm(null).categories, {});
  assert.equal(formToProfile(profileToForm(undefined)).name, "default");
});

test("copy: clipboard first, then the legacy way, then failure", async () => {
  const calls = [];
  const clipboard = { writeText: async (text) => calls.push(["clipboard", text]) };
  assert.equal(await copyText("x", { clipboard, fallback: () => assert.fail("not needed") }), "clipboard");
  assert.deepEqual(calls, [["clipboard", "x"]]);
  const blocked = { writeText: async () => { throw new Error("denied"); } };
  assert.equal(await copyText("y", { clipboard: blocked, fallback: (text) => text === "y" }), "fallback");
  assert.equal(await copyText("y", { clipboard: undefined, fallback: () => true }), "fallback");
  assert.equal(await copyText("y", { clipboard: blocked, fallback: () => false }), "failed");
  assert.equal(await copyText("y", { clipboard: blocked, fallback: () => { throw new Error("x"); } }), "failed");
  assert.equal(await copyText("y", { clipboard: null }), "failed");
});
