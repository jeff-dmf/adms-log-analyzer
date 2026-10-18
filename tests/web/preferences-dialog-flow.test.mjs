// Preferences (9.10): the real dialog on the fake DOM with a fake api, a fake file and a fake saver. Data is synthetic.
// No wall-clock waits: the flows are driven through the promises the fake api hands out.
import test from "node:test";
import assert from "node:assert/strict";

import { installGlobalDom, tick } from "./fixtures/fake-dom.mjs";

const doc = installGlobalDom();
const { ApiError } = await import("../../src/dmslog/web/js/api.js");
const { createFileSaver, mountPreferencesDialog } = await import("../../src/dmslog/web/js/preferences-dialog.js");
const { MAX_ROWS } = await import("../../src/dmslog/web/js/preferences-model.js");
const { S } = await import("../../src/dmslog/web/js/strings.js");

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}
const has = (n, cls) => n.className.split(/\s+/).includes(cls);
const all = (root, cls) => root.all().filter((n) => has(n, cls));
const one = (root, cls) => {
  const found = all(root, cls);
  assert.equal(found.length, 1, cls);
  return found[0];
};
const byText = (root, tag, text) => root.all().find((n) => n.tagName === tag.toUpperCase() && n.textContent === text) || null;
const section = (status, extra = {}) => ({
  status, counts: { added: 0, updated: 0, unchanged: 0, rejected: 0, removed: 0 }, items: [], removed: [], warnings: [], error: null, ...extra,
});
const report = (outcome, dryRun, sections, extra = {}) => ({
  outcome, ok: true, applied: !dryRun && outcome === "applied", dry_run: dryRun, schema: { found: 1, current: 1, migrated: [] },
  errors: [], sections, summary: {}, ...extra,
});
const changed = (dryRun, name = "P1") => report("applied", dryRun, {
  presets: section("changed", { counts: { added: 1, updated: 0, unchanged: 0, rejected: 0, removed: 0 }, items: [{ index: 0, name, outcome: "added" }] }),
  alert_rules: section("absent"),
  redaction: section("absent"),
});
// A FileList has no array methods: a plain object with a length and indexes.
const fileList = (file) => ({ length: 1, 0: file });

function setup(extra = {}) {
  doc.body.children = [];
  doc.activeElement = doc.body;
  const opener = doc.createElement("button");
  doc.body.appendChild(opener);
  const exports = [];
  const imports = [];
  const saved = [];
  const applied = [];
  const closed = [];
  const reads = [];
  const api = {
    preferences: {
      export(sections, signal) { const d = deferred(); exports.push({ sections, signal, d }); return d.promise; },
      import(body, signal) { const d = deferred(); imports.push({ body, signal, d }); return d.promise; },
    },
  };
  const dialog = mountPreferencesDialog(doc.body, {
    api,
    saveFile: (name, text) => saved.push([name, text]),
    readFile: (file) => { const d = deferred(); reads.push({ file, d }); return d.promise; },
    now: () => new Date(2026, 9, 18),
    onApplied: (info) => applied.push(info),
    onClose: () => closed.push(true),
    ...extra,
  });
  const root = doc.body.all().find((n) => n.tagName === "DIALOG");
  const pick = (name = "p.json", size = 50) => {
    const input = root.all().find((n) => n.type === "file");
    input.files = fileList({ name, size });
    input.dispatch("change");
    return input;
  };
  const status = one(root, "pf-status");
  return { dialog, root, opener, exports, imports, saved, applied, closed, reads, pick, status, api };
}
const button = (root, cls) => one(root, cls);
const text = (n) => n.textContent;

async function toChecked(t, view = changed(true)) {
  t.dialog.open(t.opener);
  t.pick();
  t.reads[t.reads.length - 1].d.resolve('{"schema":1}');
  await tick();
  button(t.root, "pf-check").click();
  t.imports[t.imports.length - 1].d.resolve(view);
  await tick();
}

test("opening: the dialog is modal-like, the title has the focus and the redaction section is off with a warning", () => {
  const t = setup();
  t.dialog.open(t.opener);
  assert.equal(t.root.getAttribute("open"), "");
  assert.equal(t.dialog.isOpen(), true);
  assert.equal(text(t.root.all().find((n) => n.tagName === "H2")), S.pfTitle);
  assert.equal(doc.activeElement.tagName, "H2");
  const boxes = Object.fromEntries(t.root.all().filter((n) => n.type === "checkbox").map((n) => [n.name, n]));
  assert.equal(boxes["pf-section-presets"].checked, true);
  assert.equal(boxes["pf-section-alert_rules"].checked, true);
  assert.equal(boxes["pf-section-redaction"].checked, false);
  const warning = one(t.root, "pf-warning");
  assert.equal(text(warning), S.pfRedactionWarning);
  assert.ok(boxes["pf-section-redaction"].getAttribute("aria-describedby").split(" ").includes(warning.id));
  assert.equal(button(t.root, "pf-apply").disabled, true);
  assert.equal(button(t.root, "pf-check").disabled, true);
  assert.equal(t.root.getAttribute("aria-labelledby") !== null, true);
});

test("there is one live region, and it is the foot", () => {
  const t = setup();
  const live = t.root.all().filter((n) => n.attrs.role === "status" || n.attrs["aria-live"] !== undefined);
  assert.equal(live.length, 1);
  assert.equal(live[0], t.status);
  assert.ok(t.root.children[2].contains(t.status), "inside the foot");
});

test("Download file asks for the chosen sections and saves the text it gets", async () => {
  const t = setup();
  t.dialog.open(t.opener);
  button(t.root, "pf-export").click();
  assert.deepEqual(t.exports[0].sections, ["presets", "alert_rules"]);
  t.exports[0].d.resolve({ package: { schema: 1, kind: "dmslog-preferences", presets: [], alert_rules: [] }, text: "TEXT\n", warnings: [] });
  await tick();
  assert.deepEqual(t.saved, [["dmslog-preferences-2026-10-18.json", "TEXT\n"]]);
  assert.match(text(t.status), /Saved dmslog-preferences-2026-10-18\.json/);
});

test("choosing redaction sends it; unchecking the others leaves only it", () => {
  const t = setup();
  t.dialog.open(t.opener);
  const boxes = Object.fromEntries(t.root.all().filter((n) => n.type === "checkbox").map((n) => [n.name, n]));
  for (const [name, on] of [["pf-section-redaction", true], ["pf-section-presets", false], ["pf-section-alert_rules", false]]) {
    boxes[name].checked = on;
    boxes[name].dispatch("change");
  }
  button(t.root, "pf-export").click();
  assert.deepEqual(t.exports[0].sections, ["redaction"]);
});

test("nothing chosen: a message, no request", () => {
  const t = setup();
  t.dialog.open(t.opener);
  for (const box of t.root.all().filter((n) => n.type === "checkbox" && n.name !== "pf-section-redaction" && n.name.startsWith("pf-section"))) {
    box.checked = false;
    box.dispatch("change");
  }
  button(t.root, "pf-export").click();
  assert.equal(t.exports.length, 0);
  assert.equal(text(t.status), S.pfErrNoSection);
  assert.ok(has(t.status, "is-error"));
});

test("an export that fails shows a static text, never the server's", async () => {
  const t = setup();
  t.dialog.open(t.opener);
  button(t.root, "pf-export").click();
  t.exports[0].d.reject(new ApiError(500, "boom", "TOP SECRET server message C:\\x"));
  await tick();
  assert.equal(text(t.status), S.pfErrServer);
  assert.ok(!text(t.root).includes("TOP SECRET"));
  assert.deepEqual(t.saved, []);
});

test("picking a file reads it, shows its name and size, and clears the input so it can be picked again", async () => {
  const t = setup();
  t.dialog.open(t.opener);
  const input = t.pick("my prefs.json", 2048);
  assert.equal(input.value, "");
  assert.equal(text(one(t.root, "pf-file-info")), "my prefs.json (2.0 KB)");
  assert.equal(text(t.status), S.pfFileReading);
  assert.equal(button(t.root, "pf-check").disabled, true);
  t.reads[0].d.resolve("{}");
  await tick();
  assert.equal(button(t.root, "pf-check").disabled, false);
  assert.equal(text(t.status), S.pfFileReady);
});

test("a cancelled picker (no file) changes nothing", async () => {
  const t = setup();
  t.dialog.open(t.opener);
  t.pick();
  t.reads[0].d.resolve("{}");
  await tick();
  const input = t.root.all().find((n) => n.type === "file");
  input.files = { length: 0 };
  input.dispatch("change");
  assert.equal(text(one(t.root, "pf-file-info")), "p.json (50 bytes)");
  assert.equal(button(t.root, "pf-check").disabled, false);
});

test("a file over the limit is refused with no read", () => {
  const t = setup();
  t.dialog.open(t.opener);
  t.pick("huge.json", 9 * 1024 * 1024);
  assert.equal(t.reads.length, 0);
  assert.match(text(t.status), /too large/);
  assert.ok(has(t.status, "is-error"));
  assert.equal(text(one(t.root, "pf-file-info")), S.pfFileNone);
});

test("Check shows the report as tables with headers, nothing is applied yet", async () => {
  const t = setup();
  await toChecked(t);
  assert.deepEqual(t.imports[0].body, { package: '{"schema":1}', dry_run: true, mode: "merge", on_item_error: "reject_section" });
  const box = one(t.root, "pf-report");
  assert.equal(box.hidden, false);
  assert.equal(text(box.all().find((n) => n.tagName === "H3")), S.pfReportCheck);
  assert.equal(text(t.status), S.pfOutAppliedCheck);
  const tables = box.all().filter((n) => n.tagName === "TABLE");
  assert.equal(tables.length, 2, "summary and the items of presets");
  for (const table of tables) {
    assert.ok(table.all().some((n) => n.tagName === "CAPTION"));
    const heads = table.all().filter((n) => n.tagName === "TH");
    assert.ok(heads.length >= 4);
    assert.ok(heads.every((n) => n.attrs.scope === "col" || n.attrs.scope === "row"));
  }
  const summary = tables[0];
  const row = summary.all().find((n) => n.tagName === "TR" && n.textContent.startsWith(S.pfSecPresets));
  assert.equal(row.children[0].tagName, "TH");
  assert.equal(row.children[0].attrs.scope, "row");
  assert.equal(row.children[1].textContent, S.pfStatusWillChange);
  assert.equal(button(t.root, "pf-apply").disabled, false);
  assert.equal(t.imports.length, 1);
  assert.deepEqual(t.applied, []);
});

test("Apply (merge) sends what Check sent, shows the result, moves the focus to the report and reloads once", async () => {
  const t = setup();
  await toChecked(t);
  button(t.root, "pf-apply").click();
  assert.deepEqual(t.imports[1].body, { ...t.imports[0].body, dry_run: false });
  assert.equal(text(t.status), S.pfApplying);
  assert.equal(doc.activeElement, one(t.root, "pf-report"));
  assert.equal(button(t.root, "pf-apply").disabled, true);
  t.imports[1].d.resolve(changed(false));
  await tick();
  assert.equal(text(t.status), S.pfOutAppliedDone);
  assert.equal(text(one(t.root, "pf-report").all().find((n) => n.tagName === "H3")), S.pfReportDone);
  assert.deepEqual(t.applied, [{ unknown: false }]);
  assert.equal(button(t.root, "pf-apply").disabled, true);
  button(t.root, "pf-apply").click();
  assert.equal(t.imports.length, 2);
});

test("Replace asks first: the focus goes to Cancel, Cancel gives it back to Apply, nothing is sent", async () => {
  const t = setup();
  t.dialog.open(t.opener);
  const replace = t.root.all().find((n) => n.type === "radio" && n.value === "replace");
  replace.checked = true;
  replace.dispatch("change");
  t.pick();
  t.reads[0].d.resolve("{}");
  await tick();
  button(t.root, "pf-check").click();
  assert.equal(t.imports[0].body.mode, "replace");
  t.imports[0].d.resolve(changed(true));
  await tick();
  const confirm = one(t.root, "pf-confirm");
  assert.equal(confirm.hidden, true);
  button(t.root, "pf-apply").click();
  assert.equal(confirm.hidden, false);
  assert.equal(confirm.attrs.role, "group");
  assert.equal(doc.activeElement, button(t.root, "pf-confirm-no"));
  assert.equal(t.imports.length, 1);
  button(t.root, "pf-confirm-no").click();
  assert.equal(confirm.hidden, true);
  assert.equal(doc.activeElement, button(t.root, "pf-apply"));
  assert.equal(t.imports.length, 1);
  button(t.root, "pf-apply").click();
  button(t.root, "pf-confirm-yes").click();
  assert.equal(t.imports.length, 2);
  assert.equal(t.imports[1].body.mode, "replace");
  assert.equal(t.imports[1].body.dry_run, false);
  assert.equal(confirm.hidden, true);
  t.imports[1].d.resolve(changed(false));
  await tick();
  assert.deepEqual(t.applied, [{ unknown: false }]);
});

test("the explanation of Replace is tied to its radio", () => {
  const t = setup();
  const replace = t.root.all().find((n) => n.type === "radio" && n.value === "replace");
  const hint = t.root.all().find((n) => n.id === replace.getAttribute("aria-describedby"));
  assert.equal(text(hint), S.pfModeReplaceHint);
});

test("choosing another file after a Check hides the report and turns Apply off", async () => {
  const t = setup();
  await toChecked(t);
  t.pick("other.json");
  assert.equal(one(t.root, "pf-report").hidden, true);
  assert.equal(button(t.root, "pf-apply").disabled, true);
  button(t.root, "pf-apply").click();
  assert.equal(t.imports.length, 1);
});

test("changing the policy after a Check hides the report", async () => {
  const t = setup();
  await toChecked(t);
  const skip = t.root.all().find((n) => n.type === "checkbox" && n.name.endsWith("-skip"));
  skip.checked = true;
  skip.dispatch("change");
  assert.equal(one(t.root, "pf-report").hidden, true);
  assert.equal(button(t.root, "pf-apply").disabled, true);
});

test("an Apply that is lost explains that the outcome is unknown, reloads, and puts the focus on Check", async () => {
  const t = setup();
  await toChecked(t);
  button(t.root, "pf-apply").click();
  t.imports[1].d.reject(new ApiError(0, "network", "SERVER"));
  await tick();
  assert.equal(text(t.status), S.pfErrApplyUnknown);
  assert.ok(has(t.status, "is-error"));
  assert.deepEqual(t.applied, [{ unknown: true }]);
  assert.equal(doc.activeElement, button(t.root, "pf-check"));
  assert.equal(one(t.root, "pf-report").hidden, true);
});

test("a rejected report lists each reason as a static text; hostile names stay text", async () => {
  const t = setup();
  const evil = "<img src=x onerror=alert(1)>";
  const view = report("partial", true, {
    presets: section("rejected", {
      counts: { added: 0, updated: 0, unchanged: 0, rejected: 1, removed: 0 },
      error: { code: "section_rejected", message: "SERVER ERROR TEXT" },
      items: [{ index: 0, name: evil, outcome: "rejected", code: "bad_filter", message: "SERVER ITEM TEXT" }],
    }),
    alert_rules: section("changed", { counts: { added: 1, updated: 0, unchanged: 0, rejected: 0, removed: 0 }, items: [{ index: 0, name: "R1", outcome: "added" }] }),
    redaction: section("absent"),
  });
  await toChecked(t, view);
  const box = one(t.root, "pf-report");
  assert.ok(text(box).includes(evil), "shown as text");
  assert.ok(!box.all().some((n) => n.tagName === "IMG"));
  assert.ok(text(box).includes(S.pfReason.bad_filter));
  assert.ok(text(box).includes(S.pfReason.section_rejected));
  assert.ok(!text(t.root).includes("SERVER"));
  assert.equal(text(t.status), S.pfOutPartialCheck);
  assert.equal(button(t.root, "pf-apply").disabled, false, "the other section can still be applied");
});

test("a report with a very long list draws a capped number of rows", async () => {
  const t = setup();
  const items = Array.from({ length: MAX_ROWS + 30 }, (_, index) => ({ index, name: "item " + index, outcome: "added" }));
  await toChecked(t, report("applied", true, {
    presets: section("changed", { items, counts: { added: items.length, updated: 0, unchanged: 0, rejected: 0, removed: 0 } }),
  }));
  const rows = one(t.root, "pf-report").all().filter((n) => n.tagName === "TR");
  assert.ok(rows.length <= MAX_ROWS + 4, String(rows.length));
  assert.ok(text(one(t.root, "pf-report")).includes("30 more are not listed."));
});

test("closing: pending work stops, the file and the report are forgotten, the focus goes back to the opener", async () => {
  const t = setup();
  t.dialog.open(t.opener);
  t.pick();
  t.reads[0].d.resolve('{"schema":1}');
  await tick();
  button(t.root, "pf-check").click();
  button(t.root, "pf-export").click();
  t.dialog.close();
  assert.equal(t.root.getAttribute("open"), null);
  assert.equal(doc.activeElement, t.opener);
  assert.deepEqual(t.closed, [true]);
  assert.equal(t.imports[0].signal.aborted, true);
  assert.equal(t.exports[0].signal.aborted, true);
  t.imports[0].d.resolve(changed(true));
  t.exports[0].d.resolve({ package: { presets: [] }, text: "x", warnings: [] });
  await tick();
  assert.deepEqual(t.saved, [], "a download that finished after the close is not saved");
  assert.equal(one(t.root, "pf-report").hidden, true);
  assert.equal(text(one(t.root, "pf-file-info")), S.pfFileNone);
  assert.equal(text(t.status), "");
  assert.equal(button(t.root, "pf-check").disabled, true);
  t.dialog.open(t.opener);
  assert.equal(button(t.root, "pf-apply").disabled, true);
  const redaction = t.root.all().find((n) => n.name === "pf-section-redaction");
  assert.equal(redaction.checked, false);
});

test("closing forgets a redaction choice: it is off again when the dialog reopens", () => {
  const t = setup();
  t.dialog.open(t.opener);
  const redaction = t.root.all().find((n) => n.name === "pf-section-redaction");
  redaction.checked = true;
  redaction.dispatch("change");
  t.dialog.close();
  t.dialog.open(t.opener);
  assert.equal(redaction.checked, false);
  button(t.root, "pf-export").click();
  assert.deepEqual(t.exports[0].sections, ["presets", "alert_rules"]);
});

test("Escape closes it through the native cancel event and returns the focus", () => {
  const t = setup();
  t.opener.focus();
  t.dialog.open(t.opener);
  const event = t.root.dispatch("cancel");
  assert.equal(event.prevented, true);
  assert.equal(t.dialog.isOpen(), false);
  assert.equal(doc.activeElement, t.opener);
});

test("the Close button works from the keyboard path (a click)", () => {
  const t = setup();
  t.dialog.open(t.opener);
  one(t.root, "dialog-close").click();
  assert.equal(t.dialog.isOpen(), false);
});

test("dispose removes the dialog and stops pending work", async () => {
  const t = setup();
  t.dialog.open(t.opener);
  button(t.root, "pf-export").click();
  t.dialog.dispose();
  assert.equal(doc.body.all().includes(t.root), false);
  assert.equal(t.exports[0].signal.aborted, true);
  t.exports[0].d.resolve({ package: { presets: [] }, text: "x", warnings: [] });
  await tick();
  assert.deepEqual(t.saved, []);
});

test("no element carries an inline handler, a style attribute or an innerHTML-style content", () => {
  const t = setup();
  t.dialog.open(t.opener);
  for (const n of t.root.all()) {
    for (const key of Object.keys(n.attrs)) {
      assert.ok(!key.startsWith("on") && key !== "style", key);
    }
  }
});

// ---- the saver (Blob + link)
function fakeEnv() {
  const log = { urls: [], revoked: [], timers: new Map(), cleared: [], clicks: [] };
  let n = 0;
  const env = {
    Blob: class { constructor(parts, options) { this.parts = parts; this.options = options; } },
    URL: {
      createObjectURL: (blob) => { const url = "blob:" + (n += 1); log.urls.push([url, blob]); return url; },
      revokeObjectURL: (url) => log.revoked.push(url),
    },
    setTimeout: (fn, ms) => { const id = ++n; log.timers.set(id, { fn, ms }); return id; },
    clearTimeout: (id) => { log.cleared.push(id); log.timers.delete(id); },
  };
  return { env, log };
}

test("the saver downloads through a link that does not stay in the page and revokes the URL after a second", () => {
  const d = (globalThis.document = doc);
  doc.body.children = [];
  const { env, log } = fakeEnv();
  const saver = createFileSaver(d, env);
  const create = d.createElement;
  d.createElement = (tag) => {
    const el = create(tag);
    if (tag === "a") { el.click = () => log.clicks.push([el.download, el.href, el.parent === d.body]); }
    return el;
  };
  saver.save("p.json", "TEXT");
  d.createElement = create;
  assert.deepEqual(log.clicks, [["p.json", "blob:1", true]]);
  assert.equal(doc.body.children.length, 0, "the link is gone");
  assert.deepEqual(log.urls[0][1].parts, ["TEXT"]);
  assert.match(log.urls[0][1].options.type, /^application\/json/);
  assert.deepEqual(log.revoked, []);
  const [[, timer]] = [...log.timers];
  assert.equal(timer.ms, 1000);
  timer.fn();
  assert.deepEqual(log.revoked, ["blob:1"]);
});

test("the saver revokes at once the URLs still waiting when it is flushed, and clears their timers", () => {
  const { env, log } = fakeEnv();
  const saver = createFileSaver(doc, env);
  saver.save("a.json", "A");
  saver.save("b.json", "B");
  assert.equal(log.timers.size, 2);
  saver.flush();
  assert.equal(log.revoked.length, 2);
  assert.equal(log.timers.size, 0);
  assert.equal(log.cleared.length, 2);
  saver.flush();
  assert.equal(log.revoked.length, 2, "once");
});

test("a download that fails to start revokes its URL and says so to the caller", () => {
  const { env, log } = fakeEnv();
  const saver = createFileSaver(doc, env);
  const create = doc.createElement;
  doc.createElement = (tag) => {
    const el = create(tag);
    if (tag === "a") { el.click = () => { throw new Error("blocked"); }; }
    return el;
  };
  assert.throws(() => saver.save("a.json", "A"), /blocked/);
  doc.createElement = create;
  assert.deepEqual(log.revoked, ["blob:1"]);
  assert.equal(log.timers.size, 0);
  assert.equal(doc.body.children.length, 0);
});

test("a save that throws becomes the static message of the dialog", async () => {
  const t = setup({ saveFile: () => { throw new Error("blocked C:\\secret"); } });
  t.dialog.open(t.opener);
  button(t.root, "pf-export").click();
  t.exports[0].d.resolve({ package: { presets: [] }, text: "x", warnings: [] });
  await tick();
  assert.equal(text(t.status), S.pfErrSave);
  assert.ok(!text(t.root).includes("secret"));
});

// ---- 9.10 review round
test("closing during an Apply keeps following it: the reload runs, and reopening cannot start another import", async () => {
  const t = setup();
  await toChecked(t);
  button(t.root, "pf-apply").click();
  assert.equal(t.imports.length, 2);
  t.dialog.close();
  assert.equal(t.imports[1].signal.aborted, false);
  t.dialog.open(t.opener);
  const input = t.root.all().find((n) => n.type === "file");
  assert.equal(input.disabled, true);
  assert.equal(button(t.root, "pf-check").disabled, true);
  assert.equal(button(t.root, "pf-apply").disabled, true);
  t.imports[1].d.resolve(changed(false));
  await tick();
  assert.deepEqual(t.applied, [{ unknown: false }]);
  assert.equal(input.disabled, false);
});

test("an Apply answered after the dialog closed reloads and leaves nothing behind", async () => {
  const t = setup();
  await toChecked(t);
  button(t.root, "pf-apply").click();
  t.dialog.close();
  t.imports[1].d.resolve(changed(false));
  await tick();
  assert.deepEqual(t.applied, [{ unknown: false }]);
  assert.equal(one(t.root, "pf-report").hidden, true);
  assert.equal(text(one(t.root, "pf-file-info")), S.pfFileNone);
});

test("controls whose changes would be ignored are disabled: the file, mode and policy during Apply", async () => {
  const t = setup();
  await toChecked(t);
  button(t.root, "pf-apply").click();
  const inputs = t.root.all().filter((n) => n.tagName === "INPUT" && (n.type === "file" || n.type === "radio" || n.name.endsWith("-skip")));
  assert.ok(inputs.length >= 4);
  assert.ok(inputs.every((n) => n.disabled === true));
  t.imports[1].d.resolve(changed(false));
  await tick();
  assert.ok(inputs.every((n) => n.disabled === false));
});

test("controls are disabled during an export: the section boxes and the button", async () => {
  const t = setup();
  t.dialog.open(t.opener);
  button(t.root, "pf-export").click();
  const boxes = t.root.all().filter((n) => n.name && n.name.startsWith("pf-section-"));
  assert.equal(boxes.length, 3);
  assert.ok(boxes.every((n) => n.disabled === true));
  assert.equal(button(t.root, "pf-export").disabled, true);
  t.exports[0].d.resolve({ package: { presets: [] }, text: "x", warnings: [] });
  await tick();
  assert.ok(boxes.every((n) => n.disabled === false));
  assert.equal(button(t.root, "pf-export").disabled, false);
});

test("reopened during an Apply, the result stays visible when it arrives", async () => {
  const t = setup();
  await toChecked(t);
  button(t.root, "pf-apply").click();
  t.dialog.close();
  t.dialog.open(t.opener);
  t.imports[1].d.resolve(changed(false));
  await tick();
  assert.equal(one(t.root, "pf-report").hidden, false);
  assert.deepEqual(t.applied, [{ unknown: false }]);
});
