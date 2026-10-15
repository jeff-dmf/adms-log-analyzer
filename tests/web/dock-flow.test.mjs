// UI-15: the bottom dock (js/dock.js) on a fake DOM: two-step creation, tabs (ARIA, roving tabindex, close),
// open/close with focus, closeOnCase, caseChanged for every mounted panel, notify and stale tabs, the divider
// (keys, drag, double click, remembered height), maximize and the minimums.
import test from "node:test";
import assert from "node:assert/strict";

import { createDock, domMeasure } from "../../src/dmslog/web/js/dock.js";
import { S, format } from "../../src/dmslog/web/js/strings.js";
import { makeDoc, tick } from "./fixtures/fake-dom.mjs";

const HEAD = 42;

function setup({ caseId = 1, storage = null, available = 700, content = 250, windowHeight = 900 } = {}) {
  const doc = makeDoc();
  const grid = doc.createElement("div");
  doc.body.appendChild(grid);
  const root = doc.createElement("section");
  grid.appendChild(root);
  const focusedOut = [];
  const messages = [];
  const space = { available, content };
  const measure = {
    available: () => space.available,
    gridHead: () => HEAD,
    content: () => space.content,
    container: () => space.available,
  };
  const dock = createDock({ root, storage, onFocusOut: () => focusedOut.push(true), doc, measure, windowHeight: () => windowHeight });
  const ctx = { caseId: () => caseId, status: { message: (text, o) => messages.push([text, o && o.kind]) } };
  const log = [];
  const panel = (id, extra = {}, instance = {}) => dock.register({
    id,
    titleKey: "actDistinct",
    mount: async (host) => {
      log.push(["mount", id]);
      const h = doc.createElement("h3");
      h.textContent = id;
      host.appendChild(h);
      return {
        refresh: () => log.push(["refresh", id]),
        reset: () => log.push(["reset", id]),
        closed: () => log.push(["closed", id]),
        update: (reason) => log.push(["update", id, reason]),
        show: (args) => log.push(["show", id, args]),
        ...instance,
      };
    },
    ...extra,
  });
  const part = (name) => root.all().find((n) => n.className.split(" ").includes(name));
  const parts = (name) => root.all().filter((n) => n.className.split(" ").includes(name));
  const tabButtons = () => root.all().filter((n) => n.attrs.role === "tab");
  const tabIds = () => tabButtons().map((t) => t.id);
  const hostOf = (id) => root.all().find((n) => n.id === id + "-host");
  return { doc, root, dock, ctx, panel, log, focusedOut, messages, space, part, parts, tabButtons, tabIds, hostOf };
}

const memoryStorage = (initial = {}) => {
  const data = { ...initial };
  return { data, getItem: (k) => (k in data ? data[k] : null), setItem: (k, v) => { data[k] = String(v); } };
};

test("the dock exists before its context: open, notify and caseChanged need bind(ctx), and bind runs once", async () => {
  const { dock, ctx, panel } = setup();
  panel("a");
  await assert.rejects(() => dock.open("a"), /before bind/);
  assert.throws(() => dock.notify("view"), /before bind/);
  assert.throws(() => dock.caseChanged(), /before bind/);
  dock.bind(ctx);
  assert.throws(() => dock.bind(ctx), /twice/);
  assert.throws(() => panel("a"), /duplicate/);
});

test("register validates the title key and the reasons it listens to", () => {
  const { dock } = setup();
  assert.throws(() => dock.register({ id: "x", titleKey: "nope", mount: () => ({}) }), /titleKey/);
  assert.throws(() => dock.register({ id: "x", titleKey: "actDistinct", listens: ["later"], mount: () => ({}) }), /listens/);
  assert.throws(() => dock.register({ id: "", titleKey: "actDistinct", mount: () => ({}) }), TypeError);
});

test("the dock is hidden without tabs and shows with the first one", async () => {
  const { dock, ctx, panel, root } = setup();
  panel("a");
  dock.bind(ctx);
  assert.equal(root.hidden, true);
  await dock.open("a");
  assert.equal(root.hidden, false);
  dock.close("a");
  assert.equal(root.hidden, true);
});

test("tabs follow the ARIA tab pattern: tablist, tab, tabpanel, one tab stop, a close button per tab", async () => {
  const { dock, ctx, panel, part, tabButtons, hostOf } = setup();
  panel("one");
  panel("two");
  dock.bind(ctx);
  await dock.open("one");
  await dock.open("two");
  assert.equal(part("dock-tabs").attrs.role, "tablist");
  const [one, two] = tabButtons();
  assert.equal(one.getAttribute("aria-selected"), "false");
  assert.equal(two.getAttribute("aria-selected"), "true");
  assert.equal(one.getAttribute("tabindex"), "-1"); // roving tabindex: only the active tab is a tab stop
  assert.equal(two.getAttribute("tabindex"), "0");
  assert.equal(two.getAttribute("aria-controls"), "two-host");
  const host = hostOf("two");
  assert.equal(host.attrs.role, "tabpanel");
  assert.equal(host.getAttribute("aria-labelledby"), two.id);
  assert.ok(host.className.split(" ").includes("is-active"));
  assert.ok(!hostOf("one").className.split(" ").includes("is-active"));
  const closes = part("dock-tab").children.filter((c) => c.className === "dock-tab-close");
  assert.equal(closes.length, 1);
  assert.equal(closes[0].getAttribute("tabindex"), "-1");
  assert.equal(closes[0].getAttribute("aria-label"), format(S.dockCloseTab, { title: S.actDistinct }));
  assert.equal(part("dock-divider").attrs.role, "separator");
  assert.equal(part("dock-divider").getAttribute("aria-orientation"), "horizontal");
  assert.equal(part("dock-divider").getAttribute("tabindex"), "0");
  assert.equal(part("dock-divider").getAttribute("aria-label"), S.dockResize);
});

test("tabs appear in the order the panels were opened, and a closed tab leaves the list", async () => {
  const { dock, ctx, panel, tabIds } = setup();
  panel("one");
  panel("two");
  panel("three");
  dock.bind(ctx);
  await dock.open("three");
  await dock.open("one");
  await dock.open("two");
  assert.deepEqual(tabIds(), ["dock-tab-three", "dock-tab-one", "dock-tab-two"]);
  dock.close("one");
  assert.deepEqual(tabIds(), ["dock-tab-three", "dock-tab-two"]);
  assert.equal(dock.active(), "two");
});

test("closing the active tab activates the neighbour on the right, else on the left", async () => {
  const { dock, ctx, panel } = setup();
  panel("a");
  panel("b");
  panel("c");
  dock.bind(ctx);
  await dock.open("a");
  await dock.open("b");
  await dock.open("c");
  const activated = [];
  dock.on("activate", (e) => activated.push(e.id));
  dock.close("c");
  assert.equal(dock.active(), "b");
  dock.close("a"); // not the active tab: nothing changes
  assert.equal(dock.active(), "b");
  assert.deepEqual(activated, ["b"]);
});

test("clicking a tab activates it; the arrow keys, Home and End move between tabs and the focus follows", async () => {
  const { dock, ctx, panel, tabButtons, doc } = setup();
  for (const id of ["a", "b", "c"]) panel(id);
  dock.bind(ctx);
  for (const id of ["a", "b", "c"]) await dock.open(id);
  const [a, b, c] = tabButtons();
  a.click();
  assert.equal(dock.active(), "a");
  assert.equal(a.getAttribute("tabindex"), "0");
  assert.equal(c.getAttribute("tabindex"), "-1");
  a.dispatch("keydown", { key: "ArrowRight" });
  assert.equal(dock.active(), "b");
  assert.equal(doc.activeElement, b);
  b.dispatch("keydown", { key: "End" });
  assert.equal(dock.active(), "c");
  assert.equal(doc.activeElement, c);
  c.dispatch("keydown", { key: "ArrowRight" }); // wraps
  assert.equal(dock.active(), "a");
  a.dispatch("keydown", { key: "ArrowLeft" });
  assert.equal(dock.active(), "c");
  c.dispatch("keydown", { key: "Home" });
  assert.equal(dock.active(), "a");
});

test("Delete on a focused tab closes it; the close button of a tab closes it too", async () => {
  const { dock, ctx, panel, tabButtons, parts } = setup();
  panel("a");
  panel("b");
  dock.bind(ctx);
  await dock.open("a");
  await dock.open("b");
  tabButtons()[0].dispatch("keydown", { key: "Delete" });
  assert.equal(dock.isOpen("a"), false);
  parts("dock-tab-close")[0].click();
  assert.equal(dock.isOpen("b"), false);
});

test("open mounts once, refreshes on (re)open, shows with args, focuses the title, and emits events", async () => {
  const { dock, ctx, panel, log, doc } = setup();
  panel("a");
  dock.bind(ctx);
  const events = [];
  dock.on("open", (e) => events.push(["open", e.id]));
  dock.on("close", (e) => events.push(["close", e.id]));
  const trigger = doc.createElement("button");
  doc.body.appendChild(trigger);
  trigger.focus();
  assert.equal(await dock.open("a"), true);
  assert.deepEqual(log, [["mount", "a"], ["refresh", "a"]]);
  assert.equal(dock.isOpen("a"), true);
  assert.equal(dock.active(), "a");
  assert.equal(doc.activeElement.tagName, "H3");
  await dock.open("a"); // already open: only activates
  assert.deepEqual(log, [["mount", "a"], ["refresh", "a"]]);
  await dock.open("a", { id: 5 });
  assert.deepEqual(log.at(-1), ["show", "a", { id: 5 }]);
  dock.close("a");
  assert.deepEqual(log.at(-1), ["closed", "a"]);
  assert.equal(dock.isOpen("a"), false);
  assert.equal(doc.activeElement, trigger); // focus was inside: back to who opened it
  await dock.open("a");
  assert.equal(log.filter((l) => l[0] === "mount").length, 1); // the instance is cached
  assert.deepEqual(log.at(-1), ["refresh", "a"]);
  assert.deepEqual(events, [["open", "a"], ["close", "a"], ["open", "a"]]);
});

test("closing returns focus to the grid when the opener is gone", async () => {
  const { dock, ctx, panel, doc, focusedOut } = setup();
  panel("a");
  dock.bind(ctx);
  const opener = doc.createElement("button");
  doc.body.appendChild(opener);
  opener.focus();
  await dock.open("a");
  opener.remove();
  dock.close("a");
  assert.deepEqual(focusedOut, [true]);
});

test("Escape inside a panel closes its tab and gives the focus back; a key already handled is left alone", async () => {
  const { dock, ctx, panel, doc, hostOf, focusedOut } = setup();
  panel("a", {}, {});
  dock.bind(ctx);
  const opener = doc.createElement("button");
  doc.body.appendChild(opener);
  opener.focus();
  await dock.open("a");
  const inside = hostOf("a").children[0];
  inside.dispatch("keydown", { key: "Escape", defaultPrevented: true }); // a menu or field handled it
  assert.equal(dock.isOpen("a"), true);
  inside.dispatch("keydown", { key: "Escape" });
  assert.equal(dock.isOpen("a"), false);
  assert.equal(doc.activeElement, opener);
  assert.deepEqual(focusedOut, []);
});

test("Escape on the focused tab closes the active tab", async () => {
  const { dock, ctx, panel, tabButtons } = setup();
  panel("a");
  dock.bind(ctx);
  await dock.open("a");
  tabButtons()[0].dispatch("keydown", { key: "Escape" });
  assert.equal(dock.isOpen("a"), false);
});

test("closing from the focused tab (Delete) gives the focus back to the opener", async () => {
  const { dock, ctx, panel, doc, tabButtons } = setup();
  panel("a");
  dock.bind(ctx);
  const opener = doc.createElement("button");
  doc.body.appendChild(opener);
  opener.focus();
  await dock.open("a");
  tabButtons()[0].focus();
  tabButtons()[0].dispatch("keydown", { key: "Delete" });
  assert.equal(doc.activeElement, opener);
});

test("without a case a panel that needs one only says so; needsCase false opens anyway", async () => {
  const { dock, ctx, panel, messages, log } = setup({ caseId: null });
  panel("a");
  panel("b", { needsCase: false });
  dock.bind(ctx);
  assert.equal(await dock.open("a"), false);
  assert.deepEqual(messages, [[S.needCase, "error"]]);
  assert.equal(log.length, 0);
  assert.equal(await dock.open("b"), true);
});

test("a panel whose module fails to load says which one, shows it in its tab and can be retried", async () => {
  const { dock, ctx, messages, hostOf } = setup();
  let attempts = 0;
  dock.register({
    id: "bad",
    titleKey: "actDistinct",
    mount: async () => {
      attempts += 1;
      if (attempts === 1) throw new TypeError("Failed to fetch dynamically imported module: /js/x.js");
      return { refresh() {} };
    },
  });
  dock.bind(ctx);
  assert.equal(await dock.open("bad"), false);
  const text = format(S.panelLoadFailed, { panel: S.actDistinct });
  assert.deepEqual(messages, [[text, "error"]]); // the raw import error is not shown
  const failed = hostOf("bad").children[0];
  assert.equal(failed.className, "ui-error");
  assert.equal(failed.textContent, text);
  assert.equal(await dock.open("bad"), true); // the retry mounts for real
  assert.equal(attempts, 2);
});

test("caseChanged resets EVERY mounted panel, open or closed, and closes only the closeOnCase ones; notify is separate", async () => {
  const { dock, ctx, panel, log } = setup();
  panel("tied", { closeOnCase: true });
  panel("free");
  panel("closed");
  panel("never"); // registered, never mounted: nothing to reset
  dock.bind(ctx);
  await dock.open("tied");
  await dock.open("free");
  await dock.open("closed");
  dock.close("closed");
  log.length = 0;
  dock.caseChanged();
  assert.deepEqual(log, [["closed", "tied"], ["reset", "tied"], ["reset", "free"], ["reset", "closed"]]);
  assert.equal(dock.isOpen("tied"), false);
  assert.equal(dock.isOpen("free"), true);
  log.length = 0;
  dock.notify("case"); // only the visual update, and only for listeners
  assert.deepEqual(log, []);
});

test("notify updates the active tab that listens; a tab behind is marked stale and refreshes once when shown", async () => {
  const { dock, ctx, panel, log, tabButtons } = setup();
  panel("view", { listens: ["view"] });
  panel("derived", { listens: ["derived"] }, { update: undefined });
  panel("deaf");
  dock.bind(ctx);
  await dock.open("view");
  await dock.open("derived");
  await dock.open("deaf"); // the active tab; `view` and `derived` are behind it
  log.length = 0;
  dock.notify("view");
  dock.notify("derived");
  assert.deepEqual(log, []); // nothing visible to update
  tabButtons()[0].click();
  assert.deepEqual(log, [["refresh", "view"]]); // `view` is shown: refreshed once (as the default of update)
  log.length = 0;
  tabButtons()[2].click();
  tabButtons()[0].click();
  assert.deepEqual(log, [], "not stale any more");
  tabButtons()[1].click(); // `derived` was stale too
  assert.deepEqual(log, [["refresh", "derived"]]);
  log.length = 0;
  dock.notify("derived"); // now the active one
  assert.deepEqual(log, [["refresh", "derived"]]); // no update() -> refresh()
  dock.notify("view"); // behind
  dock.close("derived"); // closing activates a neighbour; `view` is neighbour or not, stale ones refresh when shown
  assert.ok(log.length >= 1);
});

test("notify on the active tab calls update(reason); a closed panel is left to refresh when reopened", async () => {
  const { dock, ctx, panel, log } = setup();
  panel("view", { listens: ["view"] });
  dock.bind(ctx);
  await dock.open("view");
  log.length = 0;
  dock.notify("view");
  assert.deepEqual(log, [["update", "view", "view"]]);
  dock.close("view");
  log.length = 0;
  dock.notify("view");
  assert.deepEqual(log, []);
  await dock.open("view");
  assert.deepEqual(log, [["refresh", "view"]]);
});

test("a module that hides its own host closes its tab", async () => {
  const { dock, ctx, panel, hostOf, tabIds } = setup();
  panel("self");
  panel("other");
  dock.bind(ctx);
  await dock.open("self");
  await dock.open("other");
  hostOf("self").hidden = true; // the module closed itself (its Close button)
  assert.equal(dock.isOpen("self"), false);
  assert.deepEqual(tabIds(), ["dock-tab-other"]);
  await dock.open("self");
  assert.equal(dock.isOpen("self"), true);
});

test("an opening that was waiting for the mount does not finish after a case change (no arguments of the old case)", async () => {
  const { dock, ctx, log } = setup();
  let release;
  dock.register({
    id: "slow",
    titleKey: "actCompare",
    mount: () => new Promise((resolve) => {
      release = () => resolve({ show: (args) => log.push(["show", args]), refresh: () => log.push(["refresh"]), reset: () => log.push(["reset"]) });
    }),
  });
  dock.bind(ctx);
  const pending = dock.open("slow", { a: 1, b: 2 }); // ids of case A
  while (!release) await tick();
  dock.caseChanged(); // switch to B while the panel is still mounting
  release();
  assert.equal(await pending, false);
  assert.equal(dock.isOpen("slow"), false);
  assert.deepEqual(log, []); // neither show(args of A) nor refresh
  assert.equal(await dock.open("slow", { a: 7, b: 8 }), true); // opening again in B works
  assert.deepEqual(log, [["show", { a: 7, b: 8 }]]);
});

test("focus goes to the panel's focusTarget, else its title, else its first control, else the host", async () => {
  const { dock, ctx, doc } = setup();
  const make = (id, build, extra = {}) => dock.register({
    id, titleKey: "actDistinct", mount: async (host) => { build(host); return {}; }, ...extra,
  });
  make("custom", (host) => { host.appendChild(doc.createElement("h3")); host.appendChild(doc.createElement("input")); },
    { focusTarget: (host) => host.children[1] });
  make("title", (host) => { host.appendChild(doc.createElement("button")); host.appendChild(doc.createElement("h3")); });
  make("control", (host) => { host.appendChild(doc.createElement("p")); host.appendChild(doc.createElement("select")); });
  make("bare", (host) => { host.appendChild(doc.createElement("p")); });
  dock.bind(ctx);
  await dock.open("custom");
  assert.equal(doc.activeElement.tagName, "INPUT");
  await dock.open("title");
  assert.equal(doc.activeElement.tagName, "H3");
  await dock.open("control");
  assert.equal(doc.activeElement.tagName, "SELECT");
  await dock.open("bare");
  assert.equal(doc.activeElement.id, "bare-host");
  assert.equal(doc.activeElement.getAttribute("tabindex"), "-1");
});

test("closeAll closes every tab, hides the dock and returns the focus", async () => {
  const { dock, ctx, panel, root, tabIds } = setup();
  panel("a");
  panel("b");
  dock.bind(ctx);
  await dock.open("a");
  await dock.open("b");
  dock.closeAll();
  assert.deepEqual(tabIds(), []);
  assert.equal(root.hidden, true);
  assert.equal(dock.active(), null);
});

// ---- height, divider, maximize ---------------------------------------------------------------------------------

test("the first height follows the content, between 160 px and 40% of the space", async () => {
  const s = setup({ available: 700, content: 250 });
  s.panel("a");
  s.dock.bind(s.ctx);
  await s.dock.open("a");
  assert.equal(s.root.style.height, "250px");
  s.dock.close("a");
  s.space.content = 80;
  await s.dock.open("a");
  assert.equal(s.root.style.height, "160px");
  s.dock.close("a");
  s.space.content = 900;
  await s.dock.open("a");
  assert.equal(s.root.style.height, "280px"); // 40% of 700
});

test("whatever the content, the grid keeps its 8 rows: the dock never passes the largest height", async () => {
  const s = setup({ available: 400, content: 900, storage: memoryStorage({ "dmslog.dockHeight": "0.95" }) });
  s.panel("a");
  s.dock.bind(s.ctx);
  await s.dock.open("a");
  const max = 400 - (HEAD + 8 * 20);
  assert.equal(s.root.style.height, max + "px");
  assert.equal(s.part("dock-divider").getAttribute("aria-valuenow"), String(Math.round((max / 400) * 100)));
});

test("a remembered height beats the content; a broken storage value is ignored", async () => {
  const s = setup({ available: 700, content: 250, storage: memoryStorage({ "dmslog.dockHeight": "0.3" }) });
  s.panel("a");
  s.dock.bind(s.ctx);
  await s.dock.open("a");
  assert.equal(s.root.style.height, "210px");
  const t = setup({ available: 700, content: 250, storage: memoryStorage({ "dmslog.dockHeight": "bogus" }) });
  t.panel("a");
  t.dock.bind(t.ctx);
  await t.dock.open("a");
  assert.equal(t.root.style.height, "250px");
});

test("a storage that throws never breaks the dock", async () => {
  const broken = { getItem() { throw new Error("blocked"); }, setItem() { throw new Error("blocked"); } };
  const s = setup({ storage: broken });
  s.panel("a");
  s.dock.bind(s.ctx);
  await s.dock.open("a");
  const divider = s.part("dock-divider");
  divider.dispatch("keydown", { key: "ArrowUp" });
  assert.equal(s.root.style.height, "285px"); // 250 + 5% of 700
});

test("the divider keys resize by 5%, Home and End jump, and the height is remembered", async () => {
  const storage = memoryStorage();
  const s = setup({ available: 800, content: 250, storage });
  s.panel("a");
  s.dock.bind(s.ctx);
  await s.dock.open("a");
  const divider = s.part("dock-divider");
  const sizes = [];
  s.dock.on("resize", (e) => sizes.push(e));
  divider.dispatch("keydown", { key: "ArrowUp" });
  assert.equal(s.root.style.height, "290px"); // 250 + 40
  assert.equal(storage.data["dmslog.dockHeight"], "0.363");
  divider.dispatch("keydown", { key: "ArrowDown" });
  assert.equal(s.root.style.height, "250px");
  divider.dispatch("keydown", { key: "Home" });
  assert.equal(s.root.style.height, "160px");
  divider.dispatch("keydown", { key: "End" });
  assert.equal(s.root.style.height, String(800 - (HEAD + 160)) + "px");
  assert.equal(divider.getAttribute("aria-valuenow"), String(Math.round(((800 - HEAD - 160) / 800) * 100)));
  assert.equal(sizes.at(-1).maximized, false);
  assert.equal(sizes.at(-1).height, 800 - HEAD - 160);
  s.dock.closeAll();
  await s.dock.open("a"); // the remembered height beats the content on the next opening
  assert.equal(s.root.style.height, String(Math.round(Number(storage.data["dmslog.dockHeight"]) * 800)) + "px");
});

test("dragging the divider up grows the dock inside the limits and remembers the height when released", async () => {
  const storage = memoryStorage();
  const s = setup({ available: 800, content: 250, storage });
  s.panel("a");
  s.dock.bind(s.ctx);
  await s.dock.open("a");
  const divider = s.part("dock-divider");
  const down = divider.dispatch("pointerdown", { clientY: 500, button: 0 });
  assert.equal(down.prevented, true);
  assert.equal(storage.data["dmslog.dockHeight"], undefined, "nothing is remembered while dragging");
  s.doc.listeners.pointermove[0]({ clientY: 450 });
  assert.equal(s.root.style.height, "300px");
  s.doc.listeners.pointermove[0]({ clientY: -5000 });
  assert.equal(s.root.style.height, String(800 - HEAD - 160) + "px");
  s.doc.listeners.pointermove[0]({ clientY: 5000 });
  assert.equal(s.root.style.height, "160px");
  s.doc.listeners.pointermove[0]({ clientY: 440 });
  s.doc.listeners.pointerup[0]({});
  assert.equal(storage.data["dmslog.dockHeight"], "0.388");
  assert.equal(s.doc.listeners.pointermove.length, 0, "the document listeners are gone after the drop");
});

test("a double click on the divider maximizes; another brings back 40%", async () => {
  const s = setup({ available: 700, content: 250 });
  s.panel("a");
  s.dock.bind(s.ctx);
  await s.dock.open("a");
  const divider = s.part("dock-divider");
  divider.dispatch("dblclick");
  assert.ok(s.root.className.split(" ").includes("is-maximized"));
  assert.equal(s.root.style.height, "");
  divider.dispatch("dblclick");
  assert.ok(!s.root.className.split(" ").includes("is-maximized"));
  assert.equal(s.root.style.height, "280px");
});

test("Maximize and Restore: the button label and aria-pressed follow, Close all closes everything", async () => {
  const s = setup();
  s.panel("a");
  s.dock.bind(s.ctx);
  await s.dock.open("a");
  const button = s.part("dock-max");
  assert.equal(button.textContent, S.dockMaximize);
  assert.equal(button.getAttribute("aria-pressed"), "false");
  button.click();
  assert.equal(button.textContent, S.dockRestore);
  assert.equal(button.getAttribute("aria-pressed"), "true");
  assert.ok(s.root.className.split(" ").includes("is-maximized"));
  assert.equal(s.part("dock-divider").getAttribute("aria-valuenow"), s.part("dock-divider").getAttribute("aria-valuemax"));
  const sizes = [];
  s.dock.on("resize", (e) => sizes.push(e));
  button.click();
  assert.equal(button.textContent, S.dockMaximize);
  assert.equal(sizes.at(-1).maximized, false);
  s.part("dock-close-all").click();
  assert.equal(s.dock.isOpen("a"), false);
});

test("maximized is not remembered: closing the last tab resets it", async () => {
  const s = setup();
  s.panel("a");
  s.dock.bind(s.ctx);
  await s.dock.open("a");
  s.dock.maximize(true);
  s.dock.close("a");
  await s.dock.open("a");
  assert.ok(!s.root.className.split(" ").includes("is-maximized"));
});

test("a window shorter than 640 px opens the dock maximized", async () => {
  const s = setup({ windowHeight: 600 });
  s.panel("a");
  s.dock.bind(s.ctx);
  await s.dock.open("a");
  assert.ok(s.root.className.split(" ").includes("is-maximized"));
  s.dock.maximize(false); // the user can still restore it: there is room
  assert.ok(!s.root.className.split(" ").includes("is-maximized"));
});

test("with no room for 8 grid rows plus 160 px the dock opens maximized and cannot be restored", async () => {
  const s = setup({ available: 300 });
  s.panel("a");
  s.dock.bind(s.ctx);
  await s.dock.open("a");
  assert.ok(s.root.className.split(" ").includes("is-maximized"));
  assert.equal(s.part("dock-max").disabled, true);
  s.dock.maximize(false);
  assert.ok(s.root.className.split(" ").includes("is-maximized"));
  s.part("dock-divider").dispatch("keydown", { key: "Home" }); // the divider cannot make a dock the grid has no room for
  assert.ok(s.root.className.split(" ").includes("is-maximized"));
});

test("the divider keys leave the maximized state only toward a smaller dock", async () => {
  const s = setup({ available: 800, content: 250 });
  s.panel("a");
  s.dock.bind(s.ctx);
  await s.dock.open("a");
  s.dock.maximize(true);
  const divider = s.part("dock-divider");
  divider.dispatch("keydown", { key: "ArrowUp" });
  assert.ok(s.root.className.split(" ").includes("is-maximized"));
  divider.dispatch("keydown", { key: "ArrowDown" });
  assert.ok(!s.root.className.split(" ").includes("is-maximized"));
  assert.equal(s.root.style.height, String(800 - HEAD - 160 - 40) + "px");
});

// ---- review fixes (PR #194) -----------------------------------------------------------------------------------

test("with a remembered height, dragging still resizes: the second drag too, and the drop remembers the new one", async () => {
  const storage = memoryStorage({ "dmslog.dockHeight": "0.3" });
  const s = setup({ available: 800, content: 250, storage });
  s.panel("a");
  s.dock.bind(s.ctx);
  await s.dock.open("a");
  assert.equal(s.root.style.height, "240px");
  const divider = s.part("dock-divider");
  divider.dispatch("pointerdown", { clientY: 500, button: 0 });
  s.doc.listeners.pointermove[0]({ clientY: 450 });
  assert.equal(s.root.style.height, "290px");
  s.doc.listeners.pointerup[0]({});
  assert.equal(storage.data["dmslog.dockHeight"], "0.363");
  divider.dispatch("pointerdown", { clientY: 500, button: 0 }); // second drag
  s.doc.listeners.pointermove[0]({ clientY: 520 });
  assert.equal(s.root.style.height, "270px");
  s.doc.listeners.pointerup[0]({});
  assert.equal(storage.data["dmslog.dockHeight"], "0.338");
  s.dock.closeAll();
  await s.dock.open("a");
  assert.equal(s.root.style.height, "270px"); // the next opening starts from what was dropped
});

// A page whose grid area can be resized: header, viewport, dock and one fixed sibling (the timeline).
function layoutPage({ container, others = 300, head = 22 }) {
  const doc = makeDoc();
  const area = doc.createElement("div");
  const sizes = { container, others, head, dock: 0, maximized: false };
  const define = (node, props) => Object.entries(props).forEach(([k, get]) => Object.defineProperty(node, k, { get }));
  define(area, { clientHeight: () => sizes.container });
  const header = doc.createElement("div");
  header.id = "grid-header";
  const viewport = doc.createElement("div");
  viewport.id = "grid-viewport";
  const root = doc.createElement("section");
  define(root, { parentElement: () => area, offsetHeight: () => (root.hidden ? 0 : sizes.dock) });
  define(header, { offsetHeight: () => (sizes.maximized ? 0 : sizes.head) });
  define(viewport, { offsetHeight: () => (sizes.maximized ? 0 : sizes.container - sizes.others - sizes.head - sizes.dock) });
  doc.getElementById = (id) => ({ "grid-header": header, "grid-viewport": viewport })[id] || null;
  area.append(header, viewport, root);
  doc.body.appendChild(area);
  return { doc, root, sizes, measure: domMeasure(root, doc) };
}

test("the space is measured again while the dock is maximized and the grid is hidden", () => {
  const page = layoutPage({ container: 700 });
  assert.equal(page.measure.available(), 400); // 700 - 300 others
  assert.equal(page.measure.gridHead(), 22 + 20);
  page.sizes.maximized = true; // the grid is hidden
  page.sizes.container = 300;
  assert.equal(page.measure.available(), 0);
  page.sizes.container = 900;
  assert.equal(page.measure.available(), 600);
  assert.equal(page.measure.gridHead(), 22 + 20, "the hidden header keeps its last height");
});

test("maximize at a roomy size, shrink the window, restore: the dock does not push the grid under its 8 rows", async () => {
  const page = layoutPage({ container: 700 });
  const dock = createDock({ root: page.root, doc: page.doc, measure: page.measure, windowHeight: () => 900, storage: null });
  const doc = page.doc;
  const status = [];
  dock.bind({ caseId: () => 1, status: { message: (t) => status.push(t) } });
  dock.register({ id: "a", titleKey: "actDistinct", mount: async () => ({}) });
  page.sizes.dock = 160;
  await dock.open("a");
  dock.maximize(true);
  page.sizes.container = 480; // 480 - 300 others = 180 shared: no room for 8 rows plus 160 px
  dock.maximize(false);
  assert.ok(page.root.className.split(" ").includes("is-maximized"), "cannot restore without room");
  page.sizes.container = 900; // room again: 600 shared
  dock.maximize(false);
  assert.ok(!page.root.className.split(" ").includes("is-maximized"));
});

test("the divider keeps min <= now <= max in every state, maximized included", async () => {
  const s = setup({ available: 700, content: 250 });
  s.panel("a");
  s.dock.bind(s.ctx);
  await s.dock.open("a");
  const check = () => {
    const d = s.part("dock-divider");
    const [min, now, max] = ["aria-valuemin", "aria-valuenow", "aria-valuemax"].map((k) => Number(d.getAttribute(k)));
    assert.ok(min <= now && now <= max, `${min} <= ${now} <= ${max}`);
  };
  check();
  s.dock.maximize(true);
  check();
  s.part("dock-divider").dispatch("keydown", { key: "Home" });
  check();
});

test("Close all pressed from its own button gives the focus back to who opened the panel, or to the grid", async () => {
  const s = setup();
  s.panel("a");
  s.panel("b");
  s.dock.bind(s.ctx);
  const opener = s.doc.createElement("button");
  s.doc.body.appendChild(opener);
  opener.focus();
  await s.dock.open("a");
  await s.dock.open("b");
  const closeAll = s.part("dock-close-all");
  closeAll.focus(); // keyboard user on the button
  closeAll.click();
  assert.equal(s.dock.isOpen("a") || s.dock.isOpen("b"), false);
  assert.notEqual(s.doc.activeElement, closeAll);
  assert.ok(s.doc.activeElement === opener || s.focusedOut.length === 1, "focus returned somewhere real");
  // opener gone: the grid gets it
  const s2 = setup();
  s2.panel("a");
  s2.dock.bind(s2.ctx);
  await s2.dock.open("a");
  s2.part("dock-close-all").focus();
  s2.part("dock-close-all").click();
  assert.deepEqual(s2.focusedOut, [true]);
});

test("a closeOnCase panel whose module failed to load still closes when the case changes", async () => {
  const s = setup();
  s.dock.register({ id: "bad", titleKey: "actDistinct", closeOnCase: true, mount: async () => { throw new Error("x"); } });
  s.dock.bind(s.ctx);
  assert.equal(await s.dock.open("bad"), false);
  assert.equal(s.dock.isOpen("bad"), true); // the tab shows the error
  s.dock.caseChanged();
  assert.equal(s.dock.isOpen("bad"), false);
});
