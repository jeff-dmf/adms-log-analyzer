import test from "node:test";
import assert from "node:assert/strict";
import {
  MAX_CODES, buildKnowledgeView, errorCodesOf, planKnowledge,
} from "../../src/dmslog/web/js/knowledge-model.js";

test("errorCodesOf reads codes, code and error_code without repeats", () => {
  assert.deepEqual(
    errorCodesOf({ codes: ["ExampleCodeOne", "ExampleCodeTwo", "ExampleCodeOne"], code: "E100", error_code: " E100 " }),
    ["ExampleCodeOne", "ExampleCodeTwo", "E100"],
  );
});

test("errorCodesOf drops values the API would reject", () => {
  assert.deepEqual(errorCodesOf({ codes: ["", "has space", 7, null, "<b>x</b>", "OK-1"], code: 5 }), ["OK-1"]);
  assert.deepEqual(errorCodesOf(null), []);
  assert.deepEqual(errorCodesOf({ codes: "ExampleCode" }), []);
});

test("errorCodesOf caps the number of codes", () => {
  const codes = Array.from({ length: 40 }, (_, i) => "C" + i);
  assert.equal(errorCodesOf({ codes }).length, MAX_CODES);
});

test("planKnowledge asks for the template only when it is known", () => {
  assert.deepEqual(planKnowledge({ tpl_id: 3, tpl: "WFM-080", fields: {} }, "AWFM"), { template: "WFM-080", errors: [] });
  assert.equal(planKnowledge({ tpl_id: 0, tpl: null, fields: {} }, "AWFM").template, null);
  assert.equal(planKnowledge(null, "AWFM").template, null);
});

test("planKnowledge pairs error codes with the source family", () => {
  const entry = { tpl_id: 3, tpl: "GEN-ERR-01", fields: { codes: ["ExampleCodeOne"] } };
  assert.deepEqual(planKnowledge(entry, "AWFM").errors, [{ family: "AWFM", code: "ExampleCodeOne" }]);
  assert.deepEqual(planKnowledge(entry, "").errors, []);
  assert.deepEqual(planKnowledge(entry, "bad family!").errors, []);
});

test("buildKnowledgeView is null without hint and without resolved errors", () => {
  assert.equal(buildKnowledgeView(null, []), null);
  assert.equal(buildKnowledgeView({ hint: null, params: [], adapter: null }, [null]), null);
  assert.equal(buildKnowledgeView({ hint: { text: "  " } }, []), null);
});

test("buildKnowledgeView joins hint, parameters, adapter and vault note", () => {
  const view = buildKnowledgeView({
    hint: { text: "Check the CRS.", params: ["WaitForCRSCallback"], note: "n" },
    params: [
      { name: "WaitForCRSCallback", default: "35", description: "Seconds to wait." },
      { name: "Other", default: "", description: null },
      { nope: true },
    ],
    adapter: { title: "Workforce Management Interface" },
    vault_note: "12 Interfaces online/Example/Index.md",
  }, []);
  assert.equal(view.hint, "Check the CRS.");
  assert.deepEqual(view.params, [
    { name: "WaitForCRSCallback", defaultText: "35", description: "Seconds to wait." },
    { name: "Other", defaultText: null, description: null },
  ]);
  assert.equal(view.adapterTitle, "Workforce Management Interface");
  assert.equal(view.note, "12 Interfaces online/Example/Index.md");
  assert.equal(view.outOfVault, false);
});

test("buildKnowledgeView flags out-of-vault errors and keeps the source", () => {
  const view = buildKnowledgeView(null, [
    { code: "ExampleCodeOne", severity: 1, reason: "Example reason", details: null, source: "ers", out_of_vault: true },
    null,
    { code: "E2", severity: null, reason: "", details: "d", source: "vault", out_of_vault: false },
  ]);
  assert.equal(view.hint, null);
  assert.equal(view.outOfVault, true);
  assert.deepEqual(view.errors.map((e) => [e.code, e.severity, e.source, e.outOfVault, e.reason]), [
    ["ExampleCodeOne", 1, "ers", true, "Example reason"],
    ["E2", null, "vault", false, null],
  ]);
});

test("parameters are ignored when there is no hint", () => {
  const view = buildKnowledgeView({ hint: null, params: [{ name: "X", default: "1" }] }, [
    { code: "C", severity: 2, source: "vault", out_of_vault: false },
  ]);
  assert.deepEqual(view.params, []);
});

test("buildKnowledgeView shows the server notice when the knowledge pack is not installed", () => {
  const missing = { state: "not_installed", installed: false, message: "Knowledge pack not installed." };
  const onlyNotice = buildKnowledgeView({ hint: null, params: [], adapter: null, knowledge_pack: missing }, []);
  assert.equal(onlyNotice.packNotice, "Knowledge pack not installed.");
  assert.equal(onlyNotice.hint, null);
  assert.deepEqual(onlyNotice.errors, []);
  const fromError = buildKnowledgeView(null, [
    { code: "C", severity: 2, source: "ers", out_of_vault: false, knowledge_pack: missing },
  ]);
  assert.equal(fromError.packNotice, "Knowledge pack not installed.");
  const installed = { state: "installed", installed: true, message: null };
  const view = buildKnowledgeView({ hint: { text: "h" }, knowledge_pack: installed }, []);
  assert.equal(view.packNotice, null);
});
