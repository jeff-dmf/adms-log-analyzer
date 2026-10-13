// "Aggregate" panel mounted on a small fake DOM: toolbar button, form, examples, run, table as text,
// drill-down, stale marker, "Compute exactly" and copy. Log text is written only as text, never markup.
import test from "node:test";
import assert from "node:assert/strict";

class FakeElement {
  constructor(tag) {
    this.tagName = tag.toUpperCase();
    this.children = [];
    this.parent = null;
    this.attrs = {};
    this.dataset = {};
    this.style = {};
    this.listeners = {};
    this.hidden = false;
    this.disabled = false;
    this.checked = false;
    this.className = "";
    this.id = "";
    this.title = "";
    this._value = "";
    this._text = "";
    const classes = new Set();
    this.classList = { toggle: (c, on) => (on ? classes.add(c) : classes.delete(c)), contains: (c) => classes.has(c) };
  }
  // a select keeps only a value one of its options has (as the browser does)
  set value(v) {
    if (this.tagName === "SELECT") {
      const options = this.all().filter((n) => n.tagName === "OPTION");
      const found = options.find((o) => o.value === String(v));
      this._value = found ? found.value : (options[0] ? options[0].value : "");
      return;
    }
    this._value = String(v);
  }
  get value() { return this._value; }
  set innerHTML(v) { throw new Error("innerHTML must not be used"); }
  set textContent(v) { this.children = []; this._text = String(v); }
  get textContent() { return this._text + this.children.map((c) => c.textContent).join(""); }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return this.attrs[k]; }
  appendChild(c) { c.parent = this; this.children.push(c); return c; }
  append(...cs) {
    for (const c of cs) {
      if (typeof c === "string") {
        const text = new FakeElement("#text");
        text.textContent = c;
        this.appendChild(text);
      } else {
        this.appendChild(c);
      }
    }
  }
  insertBefore(c, ref) {
    c.parent = this;
    const i = ref ? this.children.indexOf(ref) : -1;
    if (i < 0) { this.children.push(c); } else { this.children.splice(i, 0, c); }
    return c;
  }
  remove() {}
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  fire(type) { for (const fn of this.listeners[type] || []) { fn({ preventDefault() {}, key: "" }); } }
  click() { this.fire("click"); }
  focus() { globalThis.document.activeElement = this; }
  all() { return [this, ...this.children.flatMap((c) => c.all())]; }
  find(pred) { return this.all().filter(pred); }
  byClass(c) { return this.find((n) => n.className.split(" ").includes(c)); }
  byId(id) { return this.find((n) => n.id === id)[0]; }
}

globalThis.document = {
  head: new FakeElement("head"),
  body: new FakeElement("body"),
  activeElement: null,
  createElement: (tag) => new FakeElement(tag),
  createTextNode: (text) => { const t = new FakeElement("#text"); t.textContent = text; return t; },
  querySelector: () => null,
};
const copied = [];
Object.defineProperty(globalThis, "navigator", {
  value: { clipboard: { writeText: async (text) => { copied.push(text); } } }, configurable: true,
});

const { mountAggregate } = await import("../../src/dmslog/web/js/aggregate-panel.js");
const { S } = await import("../../src/dmslog/web/js/strings.js");

const tick = async () => { for (let i = 0; i < 10; i += 1) { await Promise.resolve(); } };
const HOSTILE = "<img src=x onerror=alert(1)>";

function answer(body) {
  return {
    rows_kind: body.rows,
    columns: [...body.group_by.map((id) => ({ id, role: "group" })), ...body.measures.map((id) => ({ id, role: "measure" }))],
    rows: [
      { values: [HOSTILE, ...body.group_by.slice(1).map(() => null), 42, ...body.measures.slice(1).map(() => 7)],
        labels: body.group_by.map(() => null), drill: { field: { name: "op", eq: HOSTILE } } },
      { values: [null, ...body.group_by.slice(1).map(() => null), 1, ...body.measures.slice(1).map(() => null)],
        labels: body.group_by.map(() => null), drill: null },
    ],
    groups: 2, truncated: false, total: 43, exact: !body.estimate, method: body.estimate ? "sample" : "exact", notes: [],
  };
}

function setup({ estimate = false } = {}) {
  const toolbar = new FakeElement("div");
  const theme = toolbar.appendChild(new FakeElement("button"));
  const panelParent = new FakeElement("div");
  const calls = { aggregate: [], drill: [] };
  const api = {
    aggregate: async (caseId, body) => { calls.aggregate.push([caseId, body]); return answer({ ...body, estimate: estimate && !body.exact }); },
    aggregateCsv: async () => ({ data: "a,b\r\n" }),
    keyTypes: async () => ({ types: [{ type: "msgid" }, { type: "errcode" }] }),
  };
  const ui = mountAggregate({
    toolbar, beforeButton: theme, panelParent, beforePanel: null, api,
    getCase: () => "C1", getFilter: () => ({ sev: { le: 3 } }), getFieldNames: () => ["op", "n"],
    onDrill: (drill) => calls.drill.push(drill),
  });
  const host = panelParent.byId("aggregate-host");
  return { toolbar, host, calls, ui };
}

test("aggregate panel: opens, runs the default question, shows text only and drills down", async () => {
  const { toolbar, host, calls, ui } = setup();
  const button = toolbar.byId("aggregate-button");
  assert.equal(button.textContent, S.aggButton);
  assert.equal(host.hidden, true);
  button.click();
  await tick();
  assert.equal(host.hidden, false);
  assert.equal(button.getAttribute("aria-expanded"), "true");
  assert.equal(calls.aggregate.length, 0); // opening does not run anything
  host.byClass("agg-run")[0].click();
  await tick();
  assert.deepEqual(calls.aggregate[0], ["C1", { rows: "entries", filter: { sev: { le: 3 } }, group_by: ["tpl"], measures: ["count"], limit: 100 }]);
  const cells = host.byClass("agg-text");
  assert.equal(cells[0].textContent, HOSTILE); // literal text, no markup
  assert.deepEqual(host.find((n) => n.tagName === "TH").map((n) => n.textContent).slice(0, 2), ["Message type", "Count"]);
  const drills = host.byClass("agg-drill");
  drills[0].click();
  assert.deepEqual(calls.drill, [{ field: { name: "op", eq: HOSTILE } }]);
  assert.equal(drills[1].disabled, true);
  assert.equal(drills[1].title, S.aggNoDrill);
  assert.match(host.byClass("agg-status")[0].textContent, /^2 groups\. Total 43\.$/);
  ui.viewChanged(); // the view changed: the result is marked stale, nothing re-runs by itself
  await tick();
  assert.equal(calls.aggregate.length, 1);
  assert.match(host.byClass("agg-status")[0].textContent, new RegExp(S.aggStale.replace(/[.:]/g, "\\$&")));
  host.byClass("agg-copy")[0].click();
  await tick();
  assert.match(copied.at(-1), /^Message type\tCount\n/);
  button.click(); // closing cancels; later view changes do nothing
  ui.viewChanged();
  assert.equal(host.hidden, true);
});

test("aggregate panel: examples fill the form and run; events show their conditions", async () => {
  const { toolbar, host, calls } = setup();
  toolbar.byId("aggregate-button").click();
  await tick();
  const examples = host.byId("agg-examples");
  examples.value = "op_duration";
  examples.fire("change");
  await tick();
  const [, body] = calls.aggregate.at(-1);
  assert.deepEqual(body, {
    rows: "events", filter: { sev: { le: 3 } }, group_by: ["name"],
    measures: ["count", "p50:duration", "p90:duration", "p99:duration"], limit: 100,
    events: { kind: ["operation"] }, order: { by: "p90:duration", dir: "desc" },
  });
  assert.equal(host.byClass("agg-events")[0].hidden, false);
  examples.value = "errors_per_hour";
  examples.fire("change");
  await tick();
  const [, errors] = calls.aggregate.at(-1);
  assert.deepEqual(errors.filter, { and: [{ sev: { le: 3 } }, { sev: { le: 2 } }] });
  assert.deepEqual(errors.group_by, ["tpl", "bucket:1h"]);
  host.byClass("agg-drill")[0].click(); // the drill-down keeps "Only errors"
  assert.equal(host.byClass("agg-events")[0].hidden, true);
  examples.value = "error_codes";
  examples.fire("change");
  await tick();
  assert.deepEqual(calls.aggregate.at(-1)[1].lookups, ["error.reason"]);
  // a bad limit is reported without a request
  const before = calls.aggregate.length;
  host.byId("agg-limit").value = "5000";
  host.byClass("agg-run")[0].click();
  await tick();
  assert.equal(calls.aggregate.length, before);
  assert.match(host.byClass("agg-status")[0].textContent, /Rows must be/);
});

test("aggregate panel: an estimate offers 'Compute exactly' once", async () => {
  const { toolbar, host, calls } = setup({ estimate: true });
  toolbar.byId("aggregate-button").click();
  await tick();
  host.byClass("agg-run")[0].click();
  await tick();
  assert.ok(host.byClass("agg-notice").some((n) => n.textContent === S.aggSampleWarning));
  const exact = host.byClass("agg-exact");
  assert.equal(exact.length, 1);
  exact[0].click();
  await tick();
  assert.equal(calls.aggregate.at(-1)[1].exact, true);
  assert.equal(host.byClass("agg-exact").length, 0);
});
