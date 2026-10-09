import test from "node:test";
import assert from "node:assert/strict";
import {
  baseName, buildKeyRows, buildOperationRows, countDiverging, filterKeyRows, formatValue, levelRank,
  logLevelText, payloadReason, productOptions,
} from "../../src/dmslog/web/js/config-panel-model.js";

const CONFIG = {
  product: "AdapterWfm",
  group: 0,
  log_level: { value: "Info", source: "observed" },
  keys: [
    {
      scope: "registry", key_path: "", name: "LogLevel", effective: "DebugLog", status: "wins", diverges: true,
      observed: { name: "LogLevel", value: "Info", is_default: false, entry_id: 12 },
      layers: [
        { layer: 1, layer_label: "Project", source_file: "C:\\Reg\\AdapterWfm.xml", value: "Info", status: "overridden", effective: false },
        { layer: 2, layer_label: "Site", source_file: "C:\\Reg\\AdapterWfm_2.xml", value: "DebugLog", status: "wins", effective: true },
      ],
    },
    {
      scope: "registry", key_path: "", name: "SendTimeout", effective: "00:01:00", status: "only", diverges: false,
      observed: { name: "SendTimeout", value: "00:01:00", is_default: true, entry_id: 13 },
      layers: [{ layer: 1, layer_label: "Project", source_file: "/reg/AdapterWfm.xml", value: "00:01:00", status: "only", effective: true }],
    },
    {
      scope: "registry", key_path: "Tags", name: "", effective: ["a", "b"], status: "ambiguous", diverges: false,
      observed: null,
      layers: [],
    },
  ],
  operations: [
    { operation: "CreatedThing", message_log_level: "Info", payload_expected: true, source_file: "x/Example_WebServiceConfiguration.xml", layer_label: "Site" },
    { operation: "ChangedThing", message_log_level: "Verbose", payload_expected: false },
    { operation: "QuietThing", message_log_level: "None", payload_expected: false },
    { operation: "OddThing", message_log_level: "Sideways", payload_expected: null },
    { operation: "BareThing", message_log_level: null, payload_expected: null },
  ],
};

test("levelRank follows the server order and synonyms", () => {
  assert.equal(levelRank("DebugLog") < levelRank("Warning"), true);
  assert.equal(levelRank("Warning") < levelRank("Info"), true);
  assert.equal(levelRank("Info") < levelRank("Verbose"), true);
  assert.equal(levelRank("debug"), levelRank("DebugLog"));
  assert.equal(levelRank("Information"), levelRank("Info"));
  assert.equal(levelRank("Sideways"), null);
  assert.equal(levelRank(null), null);
});

test("baseName handles both separators", () => {
  assert.equal(baseName("C:\\a\\b\\f.xml"), "f.xml");
  assert.equal(baseName("a/b/f.xml"), "f.xml");
  assert.equal(baseName(null), "");
});

test("formatValue shows lists, nulls and empty strings", () => {
  assert.equal(formatValue(["a", "b"]), "a, b");
  assert.equal(formatValue(null), "(none)");
  assert.equal(formatValue(""), "(none)");
  assert.equal(formatValue("x"), "x");
});

test("key rows carry text for status and divergence, not only tone", () => {
  const rows = buildKeyRows(CONFIG);
  assert.equal(rows.length, 3);
  const [level, send, list] = rows;
  assert.equal(level.label, "LogLevel");
  assert.equal(level.value, "DebugLog");
  assert.equal(level.statusLabel, "Wins");
  assert.equal(level.divergence, "yes");
  assert.equal(level.divergenceLabel, "Diverges");
  assert.equal(level.sourceFile, "AdapterWfm_2.xml");
  assert.equal(level.sourceLayer, "Site, layer 2");
  assert.equal(level.observedText, "Info");
  assert.equal(level.observedEntryId, 12);
  assert.deepEqual(level.layers.map((l) => [l.layer, l.statusLabel, l.effective]), [[1, "Overridden", false], [2, "Wins", true]]);
  assert.equal(level.layers[0].tone, "muted");
  assert.equal(send.divergence, "no");
  assert.equal(send.observedText, "00:01:00 (default)");
  assert.equal(list.label, "Tags");
  assert.equal(list.value, "a, b");
  assert.equal(list.statusLabel, "Ambiguous");
  assert.equal(list.tone, "warn");
  assert.equal(list.divergence, "na");
  assert.equal(list.divergenceLabel, "Not compared");
  assert.equal(list.observedText, "not logged");
  assert.equal(list.observedEntryId, null);
});

test("filterKeyRows applies the text filter and the divergences switch", () => {
  const rows = buildKeyRows(CONFIG);
  assert.equal(filterKeyRows(rows, "", false).length, 3);
  assert.deepEqual(filterKeyRows(rows, "", true).map((r) => r.label), ["LogLevel"]);
  assert.deepEqual(filterKeyRows(rows, "sendtime", false).map((r) => r.label), ["SendTimeout"]);
  assert.deepEqual(filterKeyRows(rows, "ambiguous", false).map((r) => r.label), ["Tags"]);
  assert.deepEqual(filterKeyRows(rows, "nothing", false), []);
  assert.equal(countDiverging(rows), 1);
});

test("operation rows explain the payload expectation", () => {
  const rows = buildOperationRows(CONFIG);
  const by = Object.fromEntries(rows.map((r) => [r.operation, r]));
  assert.equal(by.CreatedThing.expected, "yes");
  assert.equal(by.CreatedThing.expectedLabel, "Yes");
  assert.match(by.CreatedThing.reason, /Info is at least MessageLogLevel Info/);
  assert.equal(by.CreatedThing.source, "Example_WebServiceConfiguration.xml, Site");
  assert.equal(by.ChangedThing.expected, "no");
  assert.match(by.ChangedThing.reason, /below MessageLogLevel Verbose/);
  assert.match(by.QuietThing.reason, /never logged/);
  assert.equal(by.OddThing.expected, "unknown");
  assert.equal(by.OddThing.expectedLabel, "Unknown");
  assert.match(by.OddThing.reason, /Sideways is not a known level/);
  assert.equal(by.BareThing.level, "(not set)");
  assert.match(by.BareThing.reason, /not set/);
});

test("payloadReason covers an unknown or odd log level", () => {
  assert.match(payloadReason("Info", { value: null }, null), /unknown/);
  assert.match(payloadReason("Info", { value: "Loud" }, null), /Loud is not a known level/);
  assert.match(payloadReason("Info", null, null), /unknown/);
});

test("logLevelText names the source", () => {
  assert.equal(logLevelText({ value: "Info", source: "observed" }), "Log level in force: Info (logged by the process)");
  assert.equal(logLevelText({ value: "Info", source: "registry" }), "Log level in force: Info (Registry)");
  assert.equal(logLevelText({ value: null, source: null }), "Log level in force: unknown");
});

test("productOptions indexes the products of the answer", () => {
  assert.deepEqual(productOptions([{ group: 0, product: "AdapterWfm", keys: 4 }]), [
    { value: "0", label: "AdapterWfm (group 0, 4 keys)" },
  ]);
  assert.deepEqual(productOptions(undefined), []);
});
