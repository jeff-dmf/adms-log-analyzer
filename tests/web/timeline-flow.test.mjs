// Behaviour of the timeline strip (js/timeline.js) over the fake DOM and a fake uPlot that records what it is given.
import test from "node:test";
import assert from "node:assert/strict";
import { installGlobalDom, tick, until } from "./fixtures/fake-dom.mjs";

const doc = installGlobalDom();
globalThis.getComputedStyle = () => ({
  getPropertyValue: (name) => ({ "--timeline-h": "40px", "--timeline-h-expanded": "120px" })[name] || "",
});
const { initTimeline } = await import("../../src/dmslog/web/js/timeline.js");
const { NORMAL_LINE_PATH } = await import("../../src/dmslog/web/js/timeline-model.js");

class FakeUPlot {
  static instances = [];
  static paths = {
    bars: (opts) => ({ kind: "bars", opts }),
    stepped: (opts) => ({ kind: "stepped", opts }),
  };
  static tzDate(d) { return d; }
  constructor(options, columns, host) {
    this.options = options;
    this.columns = columns;
    this.over = new (doc.body.constructor)(doc, "div");
    this.select = { left: 0, width: 0, top: 0, height: 0 };
    this.sizes = [];
    this.destroyed = false;
    FakeUPlot.instances.push(this);
    host.appendChild(this.over);
  }
  setSize(size) { this.sizes.push(size); }
  setSelect() {}
  valToPos(v) { return v; }
  posToVal(v) { return v; }
  destroy() { this.destroyed = true; }
}
const live = () => FakeUPlot.instances.filter((u) => !u.destroyed).at(-1);

function response(counts, extra = {}) {
  const n = counts[2] ? counts[2].length : counts[0].length;
  return {
    bucket_ms: 60000, start: 1760000000000, n_buckets: n, split: "sev", total: 1, exact: true,
    series: [{ key: 2, counts: counts[0] }, { key: 3, counts: counts[1] }, { key: 4, counts: counts[2] || counts[0] }],
    ...extra,
  };
}

function setup({ resp, storage = null } = {}) {
  FakeUPlot.instances.length = 0;
  window.localStorage = storage;
  const calls = [];
  const ranges = [];
  const api = { histogram: async (...args) => { calls.push(args); return resp; } };
  const host = doc.createElement("div");
  doc.body.appendChild(host);
  let request = { caseId: "c1", filter: null };
  const timeline = initTimeline(host, {
    api, uplot: FakeUPlot, getFilter: () => request, onRangeSelected: (r) => ranges.push(r),
  });
  const find = (cls) => host.all().find((n) => n.className.split(/\s+/).includes(cls));
  const buttons = (cls) => host.all().filter((n) => n.tagName === "BUTTON" && n.className.split(/\s+/).includes(cls));
  return { host, timeline, calls, ranges, find, buttons, setRequest: (r) => { request = r; } };
}

const SEV = response([[0, 5, 0], [0, 1, 0], [10, 10, 10]]);

test("Info + Noise is drawn as a 1.5 px line whose path reaches the right edge (one bucket and several)", async () => {
  for (const counts of [[[7], [0], [30]], [[0, 5, 0], [0, 1, 0], [10, 10, 10]]]) {
    const t = setup({ resp: response(counts) });
    await until(() => live(), "the chart");
    const chart = live();
    const line = chart.options.series.at(-1);
    assert.equal(line.width, 1.5);
    assert.equal(line.paths.kind, "stepped");
    assert.equal(line.paths.opts.extend, true);
    assert.deepEqual(line.paths.opts, NORMAL_LINE_PATH);
    assert.equal(chart.columns.length, chart.options.series.length); // x + bars + line
    assert.equal(chart.columns.at(-1).length, counts[0].length);
    t.timeline.destroy();
  }
});

test("hiding the Info + Noise legend entry removes the line", async () => {
  const t = setup({ resp: SEV });
  await until(() => live(), "the chart");
  const before = live().options.series.length;
  const item = t.buttons("tl-legend-item").find((b) => b.textContent.startsWith("Info + Noise"));
  assert.ok(item);
  item.click();
  const after = t.buttons("tl-legend-item").find((b) => b.textContent.startsWith("Info + Noise"));
  assert.equal(after.attrs["aria-pressed"], "false");
  assert.equal(live().options.series.length, before - 1);
  t.timeline.destroy();
});

test("Expand toggles aria-expanded, the chart height and the remembered choice", async () => {
  const store = {};
  const storage = { getItem: (k) => store[k] ?? null, setItem: (k, v) => { store[k] = v; } };
  const t = setup({ resp: SEV, storage });
  await until(() => live(), "the chart");
  const expand = t.buttons("tl-toggle").find((b) => b.attrs["aria-expanded"] !== undefined);
  assert.equal(expand.attrs["aria-expanded"], "false");
  assert.equal(live().options.height, 40);
  expand.click();
  assert.equal(expand.attrs["aria-expanded"], "true");
  assert.equal(live().sizes.at(-1).height, 120);
  assert.equal(store["dmslog.timelineExpanded"], "1");
  expand.click();
  assert.equal(expand.attrs["aria-expanded"], "false");
  assert.equal(live().sizes.at(-1).height, 40);
  t.timeline.destroy();
  // A remembered expanded strip opens expanded.
  const again = setup({ resp: SEV, storage });
  await until(() => live(), "the chart");
  assert.equal(again.buttons("tl-toggle").find((b) => b.attrs["aria-expanded"] !== undefined).attrs["aria-expanded"], "false");
  again.timeline.destroy();
  store["dmslog.timelineExpanded"] = "1";
  const open = setup({ resp: SEV, storage });
  await until(() => live(), "the chart");
  assert.equal(live().options.height, 120);
  assert.equal(open.buttons("tl-toggle").find((b) => b.attrs["aria-expanded"] !== undefined).attrs["aria-expanded"], "true");
  open.timeline.destroy();
});

test("a blocked localStorage is swallowed (read and write)", async () => {
  const blocked = {
    getItem() { throw new Error("blocked"); },
    setItem() { throw new Error("blocked"); },
  };
  const t = setup({ resp: SEV, storage: blocked });
  await until(() => live(), "the chart");
  const expand = t.buttons("tl-toggle").find((b) => b.attrs["aria-expanded"] !== undefined);
  assert.doesNotThrow(() => expand.click());
  assert.equal(expand.attrs["aria-expanded"], "true");
  t.timeline.destroy();
  // The accessor itself may throw (private window).
  Object.defineProperty(window, "localStorage", { configurable: true, get() { throw new Error("denied"); } });
  FakeUPlot.instances.length = 0;
  const host = doc.createElement("div");
  doc.body.appendChild(host);
  const timeline = initTimeline(host, { api: { histogram: async () => SEV }, uplot: FakeUPlot, getFilter: () => ({ caseId: "c", filter: null }) });
  await until(() => live(), "the chart");
  assert.doesNotThrow(() => host.all().find((n) => n.attrs["aria-expanded"] !== undefined).click());
  timeline.destroy();
  Object.defineProperty(window, "localStorage", { configurable: true, writable: true, value: null });
});

test("Errors only is a view of the strip: no new request and no filter change", async () => {
  const t = setup({ resp: SEV });
  await until(() => live(), "the chart");
  const requests = t.calls.length;
  const button = t.find("tl-toggle");
  assert.equal(button.textContent, "Errors only");
  assert.equal(button.attrs["aria-pressed"], "false");
  const seriesBefore = live().options.series.length;
  button.click();
  assert.equal(button.attrs["aria-pressed"], "true");
  assert.ok(live().options.series.length < seriesBefore); // Warning and the line are gone
  await new Promise((resolve) => setTimeout(resolve, 250));
  assert.equal(t.calls.length, requests);
  assert.deepEqual(t.ranges, []);
  button.click();
  assert.equal(button.attrs["aria-pressed"], "false");
  assert.equal(live().options.series.length, seriesBefore);
  assert.equal(t.calls.length, requests);
  t.timeline.destroy();
});

test("Errors only disappears with the case and for splits that are not severity", async () => {
  const t = setup({ resp: SEV });
  await until(() => live(), "the chart");
  const button = t.find("tl-toggle");
  assert.equal(button.hidden, false);
  t.setRequest(null);
  t.timeline.refresh();
  await until(() => button.hidden, "Errors only hidden after the case went away");
  t.timeline.destroy();
});

test("undated records are shown only for a numeric count", async () => {
  const cases = [[5, "5 undated records"], [1, "1 undated record"], ["5", null], ["abc", null], [0, null], [undefined, null], [NaN, null]];
  for (const [undated, expected] of cases) {
    const t = setup({ resp: { ...SEV, undated } });
    await until(() => live(), "the chart");
    const note = t.find("tl-undated");
    if (expected === null) {
      assert.equal(note.hidden, true, String(undated));
    } else {
      assert.equal(note.hidden, false);
      assert.equal(note.textContent, expected);
    }
    t.timeline.destroy();
  }
});

test("other splits keep the plain stacked histogram with a value axis", async () => {
  const t = setup({ resp: { ...SEV, split: "source", series: [{ key: 7, counts: [1, 2, 3] }, { key: 8, counts: [3, 2, 1] }] } });
  await until(() => live(), "the chart");
  assert.equal(live().options.axes[1].show, true);
  assert.ok(live().options.series.every((s) => !s.paths || s.paths.kind === "bars"));
  assert.equal(t.find("tl-toggle").hidden, true);
  t.timeline.destroy();
  await tick();
});
