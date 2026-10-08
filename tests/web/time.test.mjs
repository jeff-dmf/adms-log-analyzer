import test from "node:test";
import assert from "node:assert/strict";
import { formatTs, parseTs } from "../../src/dmslog/web/js/time.js";

// 2026-10-08 12:34:56.789 read as UTC (the log's wall-clock text).
const MS = Date.UTC(2026, 9, 8, 12, 34, 56, 789);

test("formatTs shows the log wall-clock text", () => {
  assert.equal(formatTs(MS), "2026-10-08 12:34:56.789");
});

test("formatTs pads small fields", () => {
  assert.equal(formatTs(Date.UTC(2026, 0, 2, 3, 4, 5, 6)), "2026-01-02 03:04:05.006");
});

test("formatTs output does not depend on the PC time zone", () => {
  const original = process.env.TZ;
  const seen = new Set();
  try {
    for (const zone of ["UTC", "America/Sao_Paulo", "Asia/Kolkata", "Pacific/Auckland", "Europe/Lisbon"]) {
      process.env.TZ = zone;
      seen.add(formatTs(MS));
      seen.add(formatTs(Date.UTC(2026, 2, 29, 1, 30, 0, 0))); // inside a DST gap for some zones
    }
  } finally {
    if (original === undefined) {
      delete process.env.TZ;
    } else {
      process.env.TZ = original;
    }
  }
  assert.deepEqual([...seen].sort(), ["2026-03-29 01:30:00.000", "2026-10-08 12:34:56.789"]);
});

test("formatTs returns empty text for missing values", () => {
  assert.equal(formatTs(null), "");
  assert.equal(formatTs(undefined), "");
  assert.equal(formatTs(Number.NaN), "");
});

test("parseTs round-trips formatTs", () => {
  assert.equal(parseTs(formatTs(MS)), MS);
});

test("parseTs accepts partial forms and rejects bad input", () => {
  assert.equal(parseTs("2026-10-08"), Date.UTC(2026, 9, 8));
  assert.equal(parseTs("2026-10-08 12:34"), Date.UTC(2026, 9, 8, 12, 34));
  assert.equal(parseTs("2026-10-08T12:34:56.5"), Date.UTC(2026, 9, 8, 12, 34, 56, 500));
  assert.equal(parseTs("2026-02-30 00:00:00"), null);
  assert.equal(parseTs("2026-13-01"), null);
  assert.equal(parseTs("12:34"), null);
  assert.equal(parseTs(""), null);
});
