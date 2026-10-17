// UI-13: the status bar on a fake DOM: segments, the one live message region, errors that swap the bar contents,
// slots, the help popover, and the field errors (aria-invalid + aria-describedby).
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";

import { setFieldError } from "../../src/dmslog/web/js/field-error.js";
import { S } from "../../src/dmslog/web/js/strings.js";
import { createStatusBar } from "../../src/dmslog/web/js/status-bar.js";
import { makeDoc } from "./fixtures/fake-dom.mjs";

function setup(options = {}) {
  const doc = makeDoc();
  const root = doc.createElement("footer");
  doc.body.appendChild(root);
  const timers = [];
  let clock = 0;
  const bar = createStatusBar(root, {
    S,
    helpText: () => "help text",
    ...options,
    now: () => clock,
    setTimer: (fn, ms) => { timers.push({ fn, at: clock + ms }); return timers.length; },
    clearTimer: (id) => { if (timers[id - 1]) timers[id - 1].fn = null; },
  });
  const pick = (cls) => root.all().find((n) => n.className.split(/\s+/).includes(cls));
  const advance = (ms) => {
    clock += ms;
    for (const t of timers) if (t.fn && t.at <= clock) { const fn = t.fn; t.fn = null; fn(); }
  };
  return { doc, root, bar, pick, advance };
}

test("count: exact, estimate, loading keeps the last number, null hides; announce only on request", () => {
  const { bar, pick } = setup();
  const count = pick("sb-count");
  const announcer = pick("sb-announcer");
  assert.equal(count.hidden, true);
  bar.count({ n: 285093, exact: true });
  assert.equal(count.textContent, "285,093 records");
  assert.equal(count.classList.contains("is-estimate"), false);
  assert.equal(announcer.textContent, "");
  bar.count("loading");
  assert.equal(count.textContent, "285,093 records");
  assert.equal(count.classList.contains("is-loading"), true);
  bar.count({ n: 1_200_000, exact: false }, { announce: true });
  assert.equal(count.textContent, "about 1.2M records");
  assert.equal(count.classList.contains("is-estimate"), true);
  assert.equal(count.classList.contains("is-loading"), false);
  assert.equal(announcer.textContent, "about 1.2M records");
  bar.count(null);
  assert.equal(count.hidden, true);
});

test("the first load shows the loading text; the position is plain text and never announced", () => {
  const { bar, pick } = setup();
  bar.count("loading");
  assert.equal(pick("sb-count").textContent, S.loading);
  bar.position(Date.UTC(2030, 0, 15, 10, 0, 0, 14));
  assert.match(pick("sb-position").textContent, /^Showing from 2030-01-15 10:00:00\.014$/);
  assert.equal(pick("sb-position").attrs["aria-live"], undefined);
  bar.position(null);
  assert.equal(pick("sb-position").hidden, true);
});

test("exactly two live regions exist: the message and the hidden count announcer", () => {
  const { root } = setup();
  const live = root.all().filter((n) => n.attrs["aria-live"] || n.attrs.role === "status");
  assert.deepEqual(live.map((n) => n.className.split(/\s+/)[1] || n.className).sort(), ["sb-announcer", "sb-message"]);
  assert.equal(live.find((n) => n.className.includes("sb-message")).attrs.role, "status");
});

test("a success message goes away after 8 s; an error stays and has a dismiss button", () => {
  const { bar, pick, advance } = setup();
  const message = pick("sb-message");
  const dismiss = pick("sb-dismiss");
  bar.message("Saved", { kind: "success" });
  assert.equal(message.hidden, false);
  assert.equal(message.textContent.startsWith("Saved"), true);
  assert.equal(dismiss.hidden, true);
  advance(7999);
  assert.equal(message.hidden, false);
  advance(1);
  assert.equal(message.hidden, true);

  bar.message("Failed", { kind: "error" });
  assert.equal(dismiss.hidden, false);
  assert.equal(message.classList.contains("is-error"), true);
  advance(60_000);
  assert.equal(message.hidden, false);
  dismiss.click();
  assert.equal(message.hidden, true);
});

test("a message handle dismisses only its own message; empty text clears", () => {
  const { bar, pick } = setup();
  const first = bar.message("one", { kind: "info" });
  bar.message("two", { kind: "info" });
  first.dismiss();
  assert.equal(pick("sb-message-text").textContent, "two");
  bar.message("");
  assert.equal(pick("sb-message").hidden, true);
});

test("a global error swaps the contents of the bar (role alert) and Retry now runs the handler", () => {
  const { bar, root, pick } = setup();
  let retried = 0;
  bar.connection("offline", { onRetry: () => { retried += 1; } });
  assert.equal(root.classList.contains("is-error"), true);
  const error = pick("sb-error");
  assert.equal(error.attrs.role, "alert");
  assert.equal(error.hidden, false);
  assert.equal(pick("sb-error-text").textContent, S.errorNetwork);
  assert.equal(pick("sb-retry").hidden, false);
  assert.equal(pick("sb-retry").textContent, "Retry now");
  pick("sb-retry").click();
  assert.equal(retried, 1);
  bar.connection("online");
  assert.equal(root.classList.contains("is-error"), false);
  assert.equal(error.hidden, true);

  bar.connection("forbidden", { onRetry: () => {} });
  assert.equal(pick("sb-error-text").textContent, S.errorForbidden);
  assert.equal(pick("sb-retry").hidden, true); // nothing to retry: only the text
});

test("slots hold the clock and alerts lines and start hidden", () => {
  const { bar } = setup();
  const clock = bar.slot("clock");
  const alerts = bar.slot("alerts");
  assert.notEqual(clock, alerts);
  assert.equal(clock.hidden, true);
  assert.equal(alerts.hidden, true);
  assert.equal(bar.slot("clock"), clock);
});

test("progress shows a determinate or indeterminate bar with its text and hides on null", () => {
  const { bar, pick } = setup();
  const box = pick("sb-progress");
  const progress = pick("sb-progress-bar");
  assert.equal(box.hidden, true);
  bar.progress({ value: null, max: null, text: "Indexing A: 1 record" });
  assert.equal(box.hidden, false);
  assert.equal(progress.attrs.value, undefined);
  assert.equal(pick("sb-progress-text").textContent, "Indexing A: 1 record");
  bar.progress({ value: 5, max: 10, text: "Indexing A: 2 records" });
  assert.equal(progress.value, 5);
  assert.equal(progress.max, 10);
  bar.progress(null);
  assert.equal(box.hidden, true);
});

test("the ? button shows the current help text in a popover and Escape closes it", () => {
  const { bar, doc, pick } = setup();
  const button = pick("sb-help");
  const pop = pick("sb-help-pop");
  assert.equal(button.textContent, "?");
  assert.equal(pop.hidden, true);
  button.click();
  assert.equal(pop.hidden, false);
  assert.equal(pop.textContent, "help text");
  assert.equal(button.attrs["aria-expanded"], "true");
  for (const fn of doc.listeners.keydown) fn({ key: "Escape", target: button });
  assert.equal(pop.hidden, true);
  bar.help();
  assert.equal(pop.hidden, false);
});

test("with onHelp (UI-28) the ? button opens the shortcuts dialog instead of the popover", () => {
  let opened = 0;
  const { pick } = setup({ onHelp: () => { opened += 1; } });
  const button = pick("sb-help");
  button.click();
  button.click();
  assert.equal(opened, 2, "every click asks for the dialog; the dialog itself ignores a second open");
  assert.equal(pick("sb-help-pop").hidden, true);
  assert.equal(button.attrs["aria-haspopup"], "dialog");
  assert.equal(button.getAttribute("aria-expanded"), null);
  assert.equal(button.getAttribute("aria-controls"), null);
  assert.equal(button.attrs["aria-label"], S.actShortcuts);
});

test("a field error sits with the field: aria-invalid, aria-describedby and a .ui-field-error note", () => {
  const doc = makeDoc();
  const form = doc.createElement("form");
  const input = doc.createElement("input");
  input.id = "path-input";
  const other = doc.createElement("button");
  form.append(input, other);
  doc.body.appendChild(form);
  setFieldError(input, "Path does not exist: /x");
  const note = form.children[1];
  assert.equal(note.className, "ui-field-error");
  assert.equal(note.textContent, "Path does not exist: /x");
  assert.equal(input.attrs["aria-invalid"], "true");
  assert.equal(input.attrs["aria-describedby"], note.id);
  assert.equal(form.children[2], other); // the note is right after the field
  setFieldError(input, "Another text");
  assert.equal(form.children.length, 3); // reused, not duplicated
  assert.equal(note.textContent, "Another text");
  setFieldError(input, null);
  assert.equal(form.children.length, 2);
  assert.equal(input.attrs["aria-invalid"], undefined);
  assert.equal(input.attrs["aria-describedby"], undefined);
});

test("the status bar css keeps a fixed height and never overlays or reflows (no message can move the grid)", () => {
  const css = fs.readFileSync(new URL("../../src/dmslog/web/css/status-bar.css", import.meta.url), "utf8").replace(/\r\n?/g, "\n");
  const bar = css.match(/\.ui-statusbar \{[^}]*\}/)[0];
  assert.match(bar, /height: var\(--status-height\)/);
  assert.match(bar, /max-height: var\(--status-height\)/);
  assert.doesNotMatch(bar, /overflow: hidden/); // the help popover opens upward from the bar
  assert.match(bar, /flex: none/);
  assert.match(bar, /white-space: nowrap/);
  assert.doesNotMatch(css, /#[0-9a-fA-F]{3,8}\b/); // tokens only
  const html = fs.readFileSync(new URL("../../src/dmslog/web/index.html", import.meta.url), "utf8");
  for (const gone of ['id="message"', 'id="grid-status"', 'id="alert-banner"', 'id="clock-banner"', "shortcuts-help", 'id="progress-bar"']) {
    assert.ok(!html.includes(gone), gone + " is replaced by the status bar");
  }
  assert.match(html, /<footer id="status-bar" class="ui-statusbar"><\/footer>\s*<\/div>\s*<\/body>/);
});
