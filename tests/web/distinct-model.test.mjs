// Distinct values panel and group-by-template view: pure model and request flows (fake api).
import test from "node:test";
import assert from "node:assert/strict";

import {
  DEFAULT_TOP, GROUP_TOP, MAX_TOP, buildView, createDistinctFlow, createGroupFlow, drillFilter, fieldOptions,
  formatPct, parseTop, valueText,
} from "../../src/dmslog/web/js/distinct-model.js";
import { S } from "../../src/dmslog/web/js/strings.js";

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

test("field options: columns, valid key types and field names, sorted and deduplicated", () => {
  const options = fieldOptions(["msgid", "gid", "bad type", "msgid"], ["op", "1bad", "code", "op", "a-b"]);
  assert.deepEqual(options.map((o) => o.value), [
    "tpl", "source", "sev", "level", "tid", "pid", "key.gid", "key.msgid", "fields.code", "fields.op",
  ]);
  assert.equal(options[0].label, S.dstColumnNames.tpl);
  assert.deepEqual(fieldOptions(null, undefined).length, 6);
});

test("top N parsing", () => {
  assert.equal(parseTop("20"), 20);
  assert.equal(parseTop(" 1 "), 1);
  assert.equal(parseTop(String(MAX_TOP)), MAX_TOP);
  for (const bad of ["0", "-1", "2.5", "x", "", String(MAX_TOP + 1)]) {
    assert.equal(parseTop(bad), null, bad);
  }
});

test("value text per field, never markup", () => {
  assert.equal(valueText("tpl", 0, null), S.dstUnclassified);
  assert.equal(valueText("tpl", 7, "WFM-010"), "WFM-010");
  assert.equal(valueText("tpl", 7, null), "7");
  assert.equal(valueText("source", 3, "a.log"), "a.log (#3)");
  assert.equal(valueText("sev", 2, null), S.severityNames[2]);
  assert.equal(valueText("level", 8, null), S.levelNames[8]);
  assert.equal(valueText("tid", 255, null), "0xff");
  assert.equal(valueText("pid", 4001, null), "4001");
  assert.equal(valueText("fields.op", null, null), S.dstEmpty);
  assert.equal(valueText("fields.op", "<b>x</b>", null), "<b>x</b>"); // shown as text by the panel
  assert.equal(valueText("fields.list", [1, 2], null), "[1,2]");
  assert.equal(valueText("key.msgid", "", null), "");
});

test("view: rows, shares, others and summary (exact, estimate, too slow)", () => {
  const data = {
    field: "sev", total: 200, groups: 4, exact: true, method: "bucket",
    values: [
      { value: 2, count: 150, pct: 75, leaf: { sev: 2 } },
      { value: 4, count: 45, pct: 22.5, leaf: { sev: 4 } },
    ],
    others: { count: 5, groups: 2 },
  };
  const view = buildView("sev", data);
  assert.deepEqual(view.rows.map((r) => [r.text, r.count, r.pct, r.share]), [
    [S.severityNames[2], "150", "75.0%", 0.75], [S.severityNames[4], "45", "22.5%", 0.225],
  ]);
  assert.deepEqual(view.rows[0].leaf, { sev: 2 });
  assert.equal(view.summary, "200 in the filter, 4 distinct values");
  assert.equal(view.others, "Others: 5 in 2 more values");
  assert.equal(view.approximate, false);
  const approx = buildView("sev", { ...data, exact: false, method: "sample", total: 12345 });
  assert.match(approx.summary, /^About 12,345 /);
  assert.equal(approx.approximate, true);
  const none = buildView("sev", { values: [], total: null, groups: 0, exact: false, method: "none", others: { count: 0, groups: 0 } });
  assert.equal(none.summary, S.dstTooSlow);
  assert.equal(none.others, "");
  assert.equal(formatPct(3.14159), "3.14%");
});

test("drill-down ANDs the leaf or its negation into the user filter", () => {
  assert.deepEqual(drillFilter(null, { tpl: 3 }, false), { and: [{ tpl: 3 }] });
  assert.deepEqual(drillFilter(null, { tpl: 3 }, true), { and: [{ not: { tpl: 3 } }] });
  assert.deepEqual(drillFilter({ sev: { le: 2 } }, { key: { type: "msgid", eq: "m" } }, false), {
    and: [{ sev: { le: 2 } }, { key: { type: "msgid", eq: "m" } }],
  });
  assert.deepEqual(drillFilter({ and: [{ sev: 1 }, { tid: 5 }] }, { field: { name: "op", eq: null } }, true), {
    and: [{ sev: 1 }, { tid: 5 }, { not: { field: { name: "op", eq: null } } }],
  });
  const same = { sev: 1 };
  assert.equal(drillFilter(same, null, false), same);
});

function distinctSetup() {
  const calls = [];
  const states = [];
  const current = { caseId: "A", filter: { sev: { le: 3 } } };
  const api = {
    distinct(caseId, filter, field, limit, span, signal) {
      const d = deferred();
      calls.push({ caseId, filter, field, limit, span, signal, d });
      return d.promise;
    },
  };
  const flow = createDistinctFlow({
    api, getCase: () => current.caseId, getFilter: () => current.filter, onState: (s) => states.push(s.status),
  });
  return { flow, calls, current, states };
}

test("distinct flow: request shape, stale answers dropped, errors and no case", async () => {
  const { flow, calls, current } = distinctSetup();
  assert.equal(flow.state.top, DEFAULT_TOP);
  const first = flow.load({ field: "tpl", top: 5 });
  assert.equal(flow.state.status, "loading");
  assert.deepEqual([calls[0].caseId, calls[0].filter, calls[0].field, calls[0].limit, calls[0].span],
    ["A", { sev: { le: 3 } }, "tpl", 5, false]);
  const second = flow.load({ field: "key.msgid" });
  assert.equal(calls[0].signal.aborted, true);
  calls[0].d.resolve({ values: [{ value: 1 }] });
  await first;
  assert.equal(flow.state.status, "loading");
  calls[1].d.resolve({ values: [], total: 0 });
  await second;
  assert.equal(flow.state.status, "ready");
  assert.equal(flow.state.field, "key.msgid");
  assert.equal(calls[1].limit, 5); // the top N stays
  const failing = flow.load();
  calls[2].d.reject(new Error("boom"));
  await failing;
  assert.equal(flow.state.status, "error");
  assert.equal(flow.state.error, "boom");
  const aborted = flow.load();
  const abort = new Error("aborted");
  abort.name = "AbortError";
  calls[3].d.reject(abort);
  await aborted;
  assert.equal(flow.state.status, "loading"); // an aborted request changes nothing
  current.caseId = null;
  await flow.load();
  assert.equal(flow.state.status, "idle");
  assert.equal(calls.length, 4);
  flow.cancel();
  assert.equal(flow.state.data, null);
});

function groupSetup() {
  const calls = { distinct: [], page: [] };
  const current = { caseId: "A", filter: { source: [1] } };
  const api = {
    distinct(caseId, filter, field, limit, span, signal) {
      const d = deferred();
      calls.distinct.push({ caseId, filter, field, limit, span, signal, d });
      return d.promise;
    },
    page(caseId, filter, cursor, dir, limit, signal) {
      const d = deferred();
      calls.page.push({ caseId, filter, cursor, dir, limit, signal, d });
      return d.promise;
    },
  };
  const flow = createGroupFlow({ api, getCase: () => current.caseId, getFilter: () => current.filter, onState: () => {}, pageLimit: 2 });
  return { flow, calls, current };
}

const row = (id) => ({ id, ts: id * 10, source_id: 1, msg: "m" + id });

test("group flow: templates with span, expand pages by keyset, collapse", async () => {
  const { flow, calls } = groupSetup();
  const loading = flow.load();
  assert.deepEqual([calls.distinct[0].field, calls.distinct[0].limit, calls.distinct[0].span], ["tpl", GROUP_TOP, true]);
  calls.distinct[0].d.resolve({ values: [{ value: 7, count: 3, pct: 100, first_ts: 1, last_ts: 2 }], total: 3, exact: true });
  await loading;
  assert.equal(flow.state.status, "ready");
  const opening = flow.toggle(7);
  assert.deepEqual(calls.page[0].filter, { and: [{ source: [1] }, { tpl: [7] }] });
  assert.deepEqual([calls.page[0].cursor, calls.page[0].dir, calls.page[0].limit], [null, "fwd", 2]);
  assert.equal(flow.state.groups.get(7).loading, true);
  calls.page[0].d.resolve({ rows: [row(1), row(2)], next: "20:2" });
  await opening;
  const more = flow.loadMore(7);
  flow.loadMore(7); // ignored while loading
  assert.equal(calls.page.length, 2);
  assert.equal(calls.page[1].cursor, "20:2");
  calls.page[1].d.resolve({ rows: [row(2), row(3)], next: null });
  await more;
  assert.deepEqual(flow.state.groups.get(7).rows.map((r) => r.id), [1, 2, 3]);
  assert.equal(flow.state.groups.get(7).next, null);
  await flow.loadMore(7);
  assert.equal(calls.page.length, 2);
  await flow.toggle(7);
  assert.equal(flow.state.groups.has(7), false);
});

test("group flow: a reload or collapse drops pending rows; errors are kept per group", async () => {
  const { flow, calls, current } = groupSetup();
  const loading = flow.load();
  calls.distinct[0].d.resolve({ values: [], total: 0, exact: true });
  await loading;
  const opening = flow.toggle(1);
  flow.load(); // new filter: the open groups go away
  assert.equal(calls.page[0].signal.aborted, true);
  calls.page[0].d.resolve({ rows: [row(9)], next: null });
  await opening;
  assert.equal(flow.state.groups.size, 0);
  calls.distinct[1].d.resolve({ values: [], total: 0, exact: true });
  const failing = flow.toggle(2);
  calls.page[1].d.reject(new Error("nope"));
  await failing;
  assert.equal(flow.state.groups.get(2).error, "nope");
  assert.equal(flow.state.groups.get(2).loading, false);
  current.caseId = null;
  await flow.load();
  assert.equal(flow.state.status, "idle");
  flow.cancel();
  assert.equal(flow.state.groups.size, 0);
});
