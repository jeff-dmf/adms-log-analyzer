// "Distinct values" panel and "Group by message type" view mounted on a small fake DOM: buttons,
// drill-down, expansion by keyset, and log text written only as text (never as markup).
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
    this.className = "";
    this.id = "";
    this.title = "";
    this.value = "";
    this._text = "";
    const classes = new Set();
    this.classList = { toggle: (c, on) => (on ? classes.add(c) : classes.delete(c)), contains: (c) => classes.has(c) };
  }
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
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  click() { for (const fn of this.listeners.click || []) { fn({ preventDefault() {} }); } }
  focus() { globalThis.document.activeElement = this; }
  all() { return [this, ...this.children.flatMap((c) => c.all())]; }
  find(pred) { return this.all().filter(pred); }
  byClass(c) { return this.find((n) => n.className.split(" ").includes(c)); }
}

globalThis.document = {
  head: new FakeElement("head"),
  activeElement: null,
  createElement: (tag) => new FakeElement(tag),
  querySelector: () => null,
};

const { mountDistinct } = await import("../../src/dmslog/web/js/distinct-panel.js");
const { S } = await import("../../src/dmslog/web/js/strings.js");

const tick = async () => { for (let i = 0; i < 6; i += 1) { await Promise.resolve(); } };

function setup({ distinctAnswer, pageAnswer }) {
  const toolbar = new FakeElement("div");
  const theme = toolbar.appendChild(new FakeElement("button"));
  const panelParent = new FakeElement("div");
  const calls = { distinct: [], page: [], drill: [], reveal: [] };
  const api = {
    distinct: async (...args) => { calls.distinct.push(args); return distinctAnswer(args); },
    page: async (...args) => { calls.page.push(args); return pageAnswer(args); },
    keyTypes: async () => ({ types: [{ type: "msgid" }] }),
  };
  const ui = mountDistinct({
    toolbar, beforeButton: theme, panelParent, beforePanel: null, api,
    getCase: () => "C1", getFilter: () => ({ sev: { le: 3 } }), getFieldNames: () => ["op"],
    getSourceName: (id) => "file-" + id + ".log",
    onDrill: (leaf, exclude) => calls.drill.push([leaf, exclude]),
    onReveal: (row) => calls.reveal.push(row),
  });
  return { toolbar, panelParent, calls, ui };
}

const HOSTILE = "<img src=x onerror=alert(1)>";

test("distinct panel: opens, lists values as text and drills down", async () => {
  const { toolbar, panelParent, calls, ui } = setup({
    distinctAnswer: ([, , field]) => ({
      field, total: 10, groups: 3, exact: true, method: "exact", others: { count: 1, groups: 1 },
      values: [
        { value: HOSTILE, count: 6, pct: 60, leaf: { field: { name: "op", eq: HOSTILE } } },
        { value: "x".repeat(3), count: 3, pct: 30, leaf: null },
      ],
    }),
    pageAnswer: () => ({ rows: [], next: null }),
  });
  const [button] = toolbar.find((n) => n.id === "distinct-button");
  assert.equal(button.textContent, S.dstButton);
  assert.equal(button.getAttribute("aria-expanded"), "false");
  const [host] = panelParent.find((n) => n.id === "distinct-host");
  assert.equal(host.hidden, true);
  button.click();
  await tick();
  assert.equal(host.hidden, false);
  assert.equal(button.getAttribute("aria-expanded"), "true");
  assert.deepEqual(calls.distinct[0].slice(0, 5), ["C1", { sev: { le: 3 } }, "tpl", 20, false]);
  const [select] = host.find((n) => n.id === "dst-field");
  select.value = "fields.op";
  for (const fn of select.listeners.change) { fn(); }
  await tick();
  assert.equal(calls.distinct.at(-1)[2], "fields.op");
  const cells = host.byClass("dst-value");
  assert.equal(cells[0].textContent, HOSTILE); // literal text, no markup
  assert.match(host.byClass("dst-others")[0].textContent, /^Others: 1 in 1 more value$/);
  host.byClass("dst-include")[0].click();
  host.byClass("dst-exclude")[0].click();
  assert.deepEqual(calls.drill, [
    [{ field: { name: "op", eq: HOSTILE } }, false], [{ field: { name: "op", eq: HOSTILE } }, true],
  ]);
  assert.equal(host.byClass("dst-include")[1].disabled, true); // no leaf: nothing to drill
  const before = calls.distinct.length;
  ui.viewChanged(); // the filter changed: an open panel reloads
  await tick();
  assert.equal(calls.distinct.length, before + 1);
  button.click(); // closing cancels and does not reload on later changes
  ui.viewChanged();
  await tick();
  assert.equal(calls.distinct.length, before + 1);
});

test("group view: message types with span, expand to rows by keyset, reveal a row", async () => {
  const { toolbar, panelParent, calls } = setup({
    distinctAnswer: ([, , field]) => ({
      field, total: 5, groups: 1, exact: true, method: "bucket", others: { count: 0, groups: 0 },
      values: [{
        value: 7, count: 5, pct: 100, leaf: { tpl: 7 }, label: "Request sent", code: "WFM-010",
        first_ts: Date.UTC(2026, 0, 2, 3, 4, 5), last_ts: Date.UTC(2026, 0, 2, 3, 9, 0),
      }],
    }),
    pageAnswer: ([, , cursor]) => (cursor === null
      ? { rows: [{ id: 11, ts: 1, source_id: 2, msg: HOSTILE }], next: "1:11" }
      : { rows: [{ id: 12, ts: 2, source_id: 2, msg: "second" }], next: null }),
  });
  const [button] = toolbar.find((n) => n.id === "group-template-button");
  assert.equal(button.textContent, S.grpButton);
  button.click();
  await tick();
  assert.deepEqual(calls.distinct[0].slice(0, 5), ["C1", { sev: { le: 3 } }, "tpl", 200, true]);
  const [host] = panelParent.find((n) => n.id === "group-template-host");
  const [toggle] = host.byClass("grp-toggle");
  assert.equal(host.byClass("grp-name")[0].textContent, "Request sent");
  assert.equal(host.byClass("grp-name")[0].title, "Request sent (WFM-010)");
  assert.equal(host.byClass("grp-span")[0].textContent, "2026-01-02 03:04:05.000 to 2026-01-02 03:09:00.000");
  assert.equal(toggle.getAttribute("aria-expanded"), "false");
  toggle.click();
  await tick();
  assert.deepEqual(calls.page[0].slice(1, 5), [{ and: [{ sev: { le: 3 } }, { tpl: [7] }] }, null, "fwd", 50]);
  const [opened] = host.byClass("grp-toggle");
  assert.equal(opened.getAttribute("aria-expanded"), "true");
  assert.equal(host.byClass("grp-msg")[0].textContent, HOSTILE);
  assert.equal(host.byClass("grp-src")[0].textContent, "file-2.log");
  host.byClass("grp-more")[0].click();
  await tick();
  assert.equal(calls.page[1][2], "1:11");
  assert.deepEqual(host.byClass("grp-msg").map((n) => n.textContent), [HOSTILE, "second"]);
  assert.equal(host.byClass("grp-more").length, 0);
  host.byClass("grp-row")[1].click();
  assert.deepEqual(calls.reveal, [{ id: 12, ts: 2 }]);
  host.byClass("grp-toggle")[0].click(); // collapse
  assert.equal(host.byClass("grp-row").length, 0);
});

test("an estimate warns and 'Count exactly' repeats the request with exact: true", async () => {
  const answers = [
    { exact: false, method: "sample" }, // default request: estimate
    { exact: false, method: "sample" }, // "Count exactly" did not fit its time either
    { exact: true, method: "exact" }, // after a filter change and a second try
  ];
  const { toolbar, panelParent, calls, ui } = setup({
    distinctAnswer: ([, , field]) => ({
      field, total: 4, groups: 1, others: { count: 0, groups: 0 },
      values: [{ value: 2, count: 4, pct: 100, leaf: { level: 2 } }], ...answers[calls.distinct.length - 1],
    }),
    pageAnswer: () => ({ rows: [], next: null }),
  });
  toolbar.find((n) => n.id === "distinct-button")[0].click();
  await tick();
  const [host] = panelParent.find((n) => n.id === "distinct-host");
  assert.equal(calls.distinct[0][6], false);
  assert.match(host.byClass("dst-warning")[0].textContent, /^Estimate: rare values may be missing\. Count exactly$/);
  host.byClass("dst-exact")[0].click();
  await tick();
  assert.equal(calls.distinct[1][6], true);
  assert.equal(host.byClass("dst-warning")[0].textContent, S.dstExactTooSlow);
  assert.equal(host.byClass("dst-exact").length, 0);
  ui.viewChanged(); // a new filter: back to the default request
  await tick();
  assert.equal(calls.distinct[2][6], false);
  assert.equal(host.byClass("dst-warning").length, 0); // exact answer: no warning
});

test("group view: an estimate warns and 'Count exactly' repeats the request with exact: true", async () => {
  const answers = [{ exact: false, method: "bucket_estimate" }, { exact: true, method: "bucket" }];
  const { toolbar, panelParent, calls } = setup({
    distinctAnswer: ([, , field]) => ({
      field, total: 5, groups: 1, others: { count: 0, groups: 0 },
      values: [{ value: 7, count: 5, pct: 100, leaf: { tpl: 7 }, label: "Request sent", code: "WFM-010" }],
      ...answers[calls.distinct.length - 1],
    }),
    pageAnswer: () => ({ rows: [], next: null }),
  });
  toolbar.find((n) => n.id === "group-template-button")[0].click();
  await tick();
  const [host] = panelParent.find((n) => n.id === "group-template-host");
  assert.equal(calls.distinct[0][6], false);
  assert.equal(host.byClass("dst-warning")[0].textContent, S.dstEstimateWarning + " " + S.dstCountExactly);
  host.byClass("dst-exact")[0].click();
  await tick();
  assert.deepEqual(calls.distinct[1].slice(2, 5).concat(calls.distinct[1][6]), ["tpl", 200, true, true]);
  assert.equal(host.byClass("dst-warning").length, 0); // exact answer: no notice
  assert.equal(host.byClass("grp-name")[0].textContent, "Request sent");
});
