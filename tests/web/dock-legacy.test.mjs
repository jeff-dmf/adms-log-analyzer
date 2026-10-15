// UI-12: the provisional dock (js/dock-legacy.js) keeps the final dock API: two-step creation, hosts in
// registration order, open/close with focus, closeOnCase, caseChanged for every mounted panel, notify.
import test from "node:test";
import assert from "node:assert/strict";

import { createDock } from "../../src/dmslog/web/js/dock-legacy.js";
import { S } from "../../src/dmslog/web/js/strings.js";
import { makeDoc, tick } from "./fixtures/fake-dom.mjs";

function setup(caseId = 1) {
  const doc = makeDoc();
  const root = doc.createElement("section");
  doc.body.appendChild(root);
  const form = root.appendChild(doc.createElement("form"));
  const grid = doc.createElement("div");
  doc.body.appendChild(grid);
  const focusedOut = [];
  const messages = [];
  const dock = createDock({ root, before: form, storage: null, onFocusOut: () => focusedOut.push(true), doc });
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
  return { doc, root, form, dock, ctx, panel, log, focusedOut, messages };
}

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

test("hosts appear on the first open, in registration order, before the filter form", async () => {
  const { dock, ctx, panel, root, form } = setup();
  panel("one");
  panel("two");
  panel("three");
  dock.bind(ctx);
  assert.equal(root.children.length, 1); // nothing is created before the first open
  await dock.open("three");
  await dock.open("one");
  await dock.open("two");
  assert.deepEqual(root.children.map((c) => c.id || c.tagName), ["one-host", "two-host", "three-host", "FORM"]);
  assert.equal(root.children[3], form);
  assert.equal(root.children[0].className, "panel host-panel");
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
  assert.deepEqual(log.filter((l) => l[0] === "mount").length, 1); // the instance is cached
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

test("without a case a panel that needs one only says so; needsCase false opens anyway", async () => {
  const { dock, ctx, panel, messages, log } = setup(null);
  panel("a");
  panel("b", { needsCase: false });
  dock.bind(ctx);
  assert.equal(await dock.open("a"), false);
  assert.deepEqual(messages, [[S.needCase, "error"]]);
  assert.equal(log.length, 0);
  assert.equal(await dock.open("b"), true);
});

test("a failing mount leaves an error in the host, tells the status and can be retried", async () => {
  const { dock, ctx, doc, messages, root } = setup();
  let attempts = 0;
  dock.register({
    id: "bad",
    titleKey: "actDistinct",
    mount: async () => {
      attempts += 1;
      if (attempts === 1) throw new Error("module failed");
      return { refresh() {} };
    },
  });
  dock.bind(ctx);
  assert.equal(await dock.open("bad"), false);
  assert.deepEqual(messages, [["module failed", "error"]]);
  const host = root.children[0];
  assert.equal(host.children[0].className, "ui-error");
  assert.equal(host.children[0].textContent, S.actionUnavailable);
  assert.equal(await dock.open("bad"), true); // the retry mounts for real
  assert.equal(attempts, 2);
  void doc;
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

test("notify updates only the open panels that listen to the reason; a closed one refreshes when reopened", async () => {
  const { dock, ctx, panel, log } = setup();
  panel("view", { listens: ["view"] });
  panel("derived", { listens: ["derived"] }, { update: undefined });
  panel("deaf");
  dock.bind(ctx);
  await dock.open("view");
  await dock.open("derived");
  await dock.open("deaf");
  log.length = 0;
  dock.notify("view");
  dock.notify("derived");
  assert.deepEqual(log, [["update", "view", "view"], ["refresh", "derived"]]); // no update() -> refresh()
  dock.close("view");
  log.length = 0;
  dock.notify("view");
  assert.deepEqual(log, []);
  await dock.open("view");
  assert.deepEqual(log, [["refresh", "view"]]);
});

test("a module that hides its own host is noticed through isOpen", async () => {
  const { dock, ctx, panel, root } = setup();
  panel("self");
  dock.bind(ctx);
  await dock.open("self");
  root.children[0].hidden = true; // the module closed itself (Escape inside the panel)
  assert.equal(dock.isOpen("self"), false);
  await dock.open("self");
  assert.equal(dock.isOpen("self"), true);
  void tick;
});
