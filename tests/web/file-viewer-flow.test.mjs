// R4c: the text viewer over the fake DOM. The text is untrusted and goes in by textContent only; hidden values are masked
// until the click and gone when the viewer closes; Esc closes; the focus goes back. Synthetic data, no real wait.
import assert from "node:assert/strict";
import { test } from "node:test";
import { installGlobalDom, tick } from "./fixtures/fake-dom.mjs";

const doc = installGlobalDom();
const { mountFileViewer } = await import("../../src/dmslog/web/js/file-viewer.js");

const MASK = "\u2022\u2022\u2022\u2022";
const HOSTILE = '<script>alert("x")</script><img src=x onerror=alert(1)>';
const find = (root, cls) => root.all().find((n) => n.className.split(/\s+/).includes(cls));

function setup(fetchText) {
  const host = doc.createElement("div");
  doc.body.appendChild(host);
  const opener = doc.createElement("button");
  doc.body.appendChild(opener);
  const messages = [];
  const calls = [];
  const viewer = mountFileViewer({
    host,
    fetchText: async (caseId, id, options, signal) => { calls.push([caseId, id, options.reveal]); return fetchText(caseId, id, options, signal); },
    onMessage: (text, isError) => messages.push([text, isError]),
  });
  const overlay = host.children[0];
  return { host, opener, viewer, overlay, calls, messages, panel: find(overlay, "fv-panel"), pre: find(overlay, "fv-text"), status: find(overlay, "fv-status"), reveal: find(overlay, "fv-reveal"), warning: find(overlay, "fv-warning"), close: find(overlay, "fv-close"), copy: find(overlay, "fv-copy"), facts: find(overlay, "fv-facts"), title: find(overlay, "fv-title") };
}

const answer = (options, extra = {}) => ({
  source_id: 3, name: "app.config", member: "Dir/app.config", kind: "text", size: 2048, encoding: "utf-8", lines: 3, hidden: 2,
  revealed: options.reveal === true, text: options.reveal ? "pw=hunter2\n" + HOSTILE : "pw=" + MASK + "\n" + HOSTILE, ...extra,
});
const SOURCE = { id: 3, name: "app.config", member: "Dir/app.config" };

test("it is closed until opened, and a dialog with a name once open", async () => {
  const t = setup(async (c, i, o) => answer(o));
  assert.equal(t.overlay.hidden, true);
  assert.equal(t.viewer.isOpen(), false);
  t.opener.focus();
  t.viewer.open("c1", SOURCE, t.opener);
  assert.equal(t.overlay.hidden, false);
  assert.equal(t.panel.attrs.role, "dialog");
  assert.equal(t.panel.attrs["aria-modal"], "true");
  assert.equal(t.panel.attrs["aria-labelledby"], t.title.id);
  assert.equal(t.status.textContent, "Loading…", "loading state while the answer is pending");
  assert.equal(t.pre.hidden, true);
  assert.equal(doc.activeElement, t.close, "the focus moves into the panel");
  await tick();
  assert.equal(t.status.textContent, "");
  assert.equal(t.title.textContent, "Dir/app.config");
});

test("the text goes in as text: a script in the file is only characters", async () => {
  const t = setup(async (c, i, o) => answer(o));
  t.viewer.open("c1", SOURCE, t.opener);
  await tick();
  assert.equal(t.pre.hidden, false);
  assert.equal(t.pre.children.length, 0, "no child element, only the text");
  assert.ok(t.pre.textContent.includes(HOSTILE));
  assert.equal(t.pre.textContent, "pw=" + MASK + "\n" + HOSTILE);
  assert.equal(t.facts.textContent, "2.0 KB - 3 lines - utf-82 values hidden");
});

test("hidden values stay hidden until the click; the warning and the pressed state show while revealed", async () => {
  const t = setup(async (c, i, o) => answer(o));
  t.viewer.open("c1", SOURCE, t.opener);
  await tick();
  assert.equal(t.reveal.hidden, false);
  assert.equal(t.reveal.textContent, "Show hidden values");
  assert.equal(t.reveal.attrs["aria-pressed"], "false");
  assert.equal(t.warning.hidden, true);
  assert.ok(!t.pre.textContent.includes("hunter2"));
  assert.deepEqual(t.calls, [["c1", 3, false]], "opening asks for the masked text only");
  t.reveal.click();
  await tick();
  assert.ok(t.pre.textContent.includes("pw=hunter2"));
  assert.equal(t.reveal.attrs["aria-pressed"], "true");
  assert.equal(t.reveal.textContent, "Hide values");
  assert.equal(t.warning.hidden, false);
  assert.match(t.warning.textContent, /may be passwords\. They are shown only here and never stored or exported\./);
  assert.equal(t.warning.attrs.role, "note");
  t.reveal.click();
  await tick();
  assert.ok(!t.pre.textContent.includes("hunter2"));
  assert.equal(t.warning.hidden, true);
  assert.deepEqual(t.calls, [["c1", 3, false], ["c1", 3, true]]);
});

test("a file with nothing hidden has no reveal button", async () => {
  const t = setup(async (c, i, o) => answer(o, { hidden: 0, text: "plain" }));
  t.viewer.open("c1", SOURCE, t.opener);
  await tick();
  assert.equal(t.reveal.hidden, true);
  assert.equal(t.facts.textContent.includes("No values hidden"), true);
});

test("Esc closes, clears the original values from the page and gives the focus back; reopening is masked again", async () => {
  const t = setup(async (c, i, o) => answer(o));
  t.opener.focus();
  t.viewer.open("c1", SOURCE, t.opener);
  await tick();
  t.reveal.click();
  await tick();
  const event = t.panel.dispatch("keydown", { key: "Escape" });
  assert.equal(event.prevented, true);
  assert.equal(t.overlay.hidden, true);
  assert.equal(t.viewer.isOpen(), false);
  assert.equal(t.pre.textContent, "", "no text stays in the page");
  assert.equal(doc.activeElement, t.opener);
  t.viewer.open("c1", SOURCE, t.opener);
  await tick();
  assert.equal(t.reveal.attrs["aria-pressed"], "false");
  assert.ok(!t.pre.textContent.includes("hunter2"), "revealing does not persist across openings");
  assert.equal(t.warning.hidden, true);
});

test("the Close button closes; a click on the backdrop closes, a click inside does not", async () => {
  const t = setup(async (c, i, o) => answer(o));
  t.viewer.open("c1", SOURCE, t.opener);
  await tick();
  t.panel.dispatch("click", { target: t.panel });
  assert.equal(t.viewer.isOpen(), true);
  t.overlay.dispatch("click", { target: t.overlay });
  assert.equal(t.viewer.isOpen(), false);
  t.viewer.open("c1", SOURCE, t.opener);
  t.close.click();
  assert.equal(t.viewer.isOpen(), false);
});

test("Tab cycles inside the panel", async () => {
  const t = setup(async (c, i, o) => answer(o));
  t.viewer.open("c1", SOURCE, t.opener);
  await tick();
  const stops = [t.close, t.reveal, t.copy, t.pre];
  for (let i = 0; i < stops.length; i += 1) {
    stops[i].focus();
    t.panel.dispatch("keydown", { key: "Tab" });
    assert.equal(doc.activeElement, stops[(i + 1) % stops.length]);
  }
  t.close.focus();
  t.panel.dispatch("keydown", { key: "Tab", shiftKey: true });
  assert.equal(doc.activeElement, t.pre, "Shift+Tab from the first goes to the last");
});

test("each error shows its own text, with no file text", async () => {
  const cases = [
    [{ status: 404, code: "source_not_found" }, "This file is not in the case."],
    [{ status: 409, code: "not_viewable" }, "This file cannot be shown as text."],
    [{ status: 413, code: "file_too_large", limit: 4 * 1024 * 1024 }, "This file is larger than 4.0 MB and is not shown."],
    [{ status: 422, code: "binary_content" }, "This is not a text file."],
    [{ status: 410, code: "container_missing" }, "The collection file is no longer available."],
  ];
  for (const [error, text] of cases) {
    const t = setup(async () => { throw Object.assign(new Error("server words"), error); });
    t.viewer.open("c1", SOURCE, t.opener);
    await tick();
    assert.equal(t.status.textContent, text);
    assert.equal(t.pre.hidden, true);
    assert.equal(t.pre.textContent, "");
    assert.equal(t.reveal.hidden, true);
    assert.equal(t.copy.disabled, true);
  }
});

test("Copy writes the text as shown: masked unless the user revealed it", async () => {
  const written = [];
  Object.defineProperty(globalThis, "navigator", { value: { clipboard: { writeText: async (text) => written.push(text) }, platform: "Linux" }, configurable: true });
  const t = setup(async (c, i, o) => answer(o));
  t.viewer.open("c1", SOURCE, t.opener);
  await tick();
  t.copy.click();
  await tick();
  t.reveal.click();
  await tick();
  t.copy.click();
  await tick();
  assert.equal(written.length, 2);
  assert.ok(!written[0].includes("hunter2"));
  assert.ok(written[1].includes("pw=hunter2"));
  Object.defineProperty(globalThis, "navigator", { value: { clipboard: null, platform: "Linux" }, configurable: true });
});

test("opening a second file while one is open keeps the first opener for the focus", async () => {
  const t = setup(async (c, i, o) => answer(o, { name: "n" + i, source_id: i }));
  t.opener.focus();
  t.viewer.open("c1", { id: 3, member: "A/a.xml" }, t.opener);
  const other = doc.createElement("button");
  doc.body.appendChild(other);
  t.viewer.open("c1", { id: 4, member: "A/b.xml" }, other);
  await tick();
  assert.equal(t.title.textContent, "A/b.xml");
  t.viewer.close();
  assert.equal(doc.activeElement, t.opener);
});
