// Copy conventions of js/strings.js (UI-09): terms, ellipsis, plural pairs and known keys. Values only, never comments.
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";

import { S, pluralKey, countNoun, format } from "../../src/dmslog/web/js/strings.js";

const JS_DIR = new URL("../../src/dmslog/web/js/", import.meta.url);

function leaves(obj, path = "") {
  const out = [];
  for (const [key, value] of Object.entries(obj)) {
    const here = path === "" ? key : path + "." + key;
    if (value !== null && typeof value === "object") {
      out.push(...leaves(value, here));
    } else {
      out.push([here, String(value)]);
    }
  }
  return out;
}

test("pluralKey picks One only for exactly 1", () => {
  assert.equal(pluralKey("x", 0), "xOther");
  assert.equal(pluralKey("x", 1), "xOne");
  assert.equal(pluralKey("x", 2), "xOther");
  assert.equal(pluralKey("x", "1"), "xOne");
  assert.equal(pluralKey("x", "1,234"), "xOther");
});

test("countNoun formats the count with the right noun", () => {
  assert.equal(countNoun("nRecord", 0), "0 records");
  assert.equal(countNoun("nRecord", 1), "1 record");
  assert.equal(countNoun("nRecord", 1234), "1,234 records");
  assert.equal(format(S[pluralKey("keyCount", 1)], { count: 1 }), "1 record");
});

test("every plural base has both forms", () => {
  const keys = new Set(Object.keys(S));
  const notPlural = new Set(["colPanelsOne", "timelineOther", "clkBannerOne", "sbsPaneOther", "cfKindOther"]); // not plural forms (a single collection, the "Other" bucket, a single banner and similar)
  for (const key of keys) {
    if (notPlural.has(key)) {
      continue;
    }
    if (key.endsWith("One")) {
      assert.ok(keys.has(key.slice(0, -3) + "Other"), key + " has no Other form");
    }
    if (key.endsWith("Other")) {
      assert.ok(keys.has(key.slice(0, -5) + "One"), key + " has no One form");
    }
  }
});

test("visible values follow the copy conventions", () => {
  for (const [key, value] of leaves(S)) {
    assert.ok(!/\(s\)/.test(value), key + ": use a plural pair instead of (s)");
    assert.ok(!/\.\.\./.test(value), key + ": use the ellipsis character");
    assert.ok(!/\bentr(y|ies)\b/i.test(value), key + ": say record (or item for a folder)");
    assert.ok(!/template/i.test(value), key + ": say message type");
    assert.ok(!/or worse|or milder/.test(value), key + ": say At least or At most");
  }
});

test("every S.key used by the js files exists in strings.js", () => {
  const missing = [];
  for (const name of fs.readdirSync(JS_DIR)) {
    if (!name.endsWith(".js") || name === "strings.js") {
      continue;
    }
    const text = fs.readFileSync(new URL(name, JS_DIR), "utf8");
    for (const m of text.matchAll(/\bS\.(\w+)/g)) {
      if (!(m[1] in S)) {
        missing.push(name + ": S." + m[1]);
      }
    }
    for (const m of text.matchAll(/pluralKey\(\s*"(\w+)"/g)) {
      if (!((m[1] + "One") in S && (m[1] + "Other") in S)) {
        missing.push(name + ": plural " + m[1]);
      }
    }
    for (const m of text.matchAll(/(?:pluralKey\([^"\n]*\?\s*"(\w+)"\s*:\s*"(\w+)")/g)) {
      for (const base of [m[1], m[2]]) {
        if (!((base + "One") in S && (base + "Other") in S)) {
          missing.push(name + ": plural " + base);
        }
      }
    }
  }
  assert.deepEqual(missing, []);
});

test("every data-i18n key of index.html exists", () => {
  const html = fs.readFileSync(new URL("../index.html", JS_DIR), "utf8");
  const missing = [];
  for (const m of html.matchAll(/data-i18n[\w-]*="(\w+)"/g)) {
    if (!(m[1] in S)) {
      missing.push(m[1]);
    }
  }
  assert.deepEqual(missing, []);
});

test("payload reason codes sent by the server all have a sentence", () => {
  for (const code of ["no_message_id", "no_operation_for_key", "no_request_for_correlation_id",
    "no_operation_ending_nearby", "no_failed_operation_nearby", "ambiguous", "not_in_operation",
    "too_many_payloads", "scan_limit"]) {
    assert.ok(S.plReasonNames[code], code);
  }
});

test("countNoun and pluralKey accept formatted numbers", () => {
  assert.equal(countNoun("nRecord", "1,234"), "1,234 records");
  assert.equal(countNoun("nRecord", "1"), "1 record");
  assert.equal(pluralKey("x", "1,234"), "xOther");
  assert.equal(pluralKey("x", " 1 "), "xOne");
});
