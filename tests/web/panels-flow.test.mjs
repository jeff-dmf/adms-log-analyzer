// The state machine of the Panels panel (panels-model.js createPanelsFlow) against a held fake server: the order of
// answers, the case and panel that an answer belongs to, overlapping runs, the progress-stream notices and their
// throttling, edits that conflict, and the timers and streams left alive.
import test from "node:test";
import assert from "node:assert/strict";

import { createPanelsFlow } from "../../src/dmslog/web/js/panels-model.js";
import { S } from "../../src/dmslog/web/js/strings.js";
import { ApiFail, flush, makeWorld, panel, runAnswer, widget } from "./fixtures/panels-world.mjs";

const A = panel("aaaaaaaaaaaa", "Alpha", [widget("w1"), widget("w2")]);
const B = panel("bbbbbbbbbbbb", "Beta", [widget("w1")]);

function setup(options = {}) {
  const w = makeWorld({ panels: [A, B], ...options });
  const flow = createPanelsFlow({ ...w.options, onState: () => { w.states += 1; } });
  w.flow = flow;
  return w;
}

// Shows the panel, answers the list and the first run.
async function opened(w, { run = true } = {}) {
  w.flow.setShown(true);
  w.answerList();
  await flush();
  if (run) {
    assert.equal(w.held.run.length, 1, "the first run starts after the list");
    w.held.run[0].resolve(runAnswer(A));
    await flush();
  }
}

const noticeAt = (w, text) => w.streams.at(-1).onNotice(JSON.stringify(text));
const lastRun = (w) => w.held.run.at(-1);

test("showing the panel reads the list, selects the first panel by name and runs all of its widgets", async () => {
  const w = setup();
  await opened(w);
  assert.equal(w.flow.state.selectedId, A.id);
  assert.deepEqual(w.held.run[0].args, ["A", A.id, undefined]);
  assert.equal(w.flow.state.run.status, "ready");
  assert.deepEqual(Object.keys(w.flow.state.run.results), ["w1", "w2"]);
  assert.equal(w.live().length, 1);
  assert.equal(w.live()[0].url, "/api/cases/A/progress");
});

for (const reject of [true, false]) test("a run that was replaced by a newer one never applies its answer (the newest wins)" + (reject ? "" : " (the old response still arrives)"), async () => {
  const w = setup();
  w.rejectOnAbort = reject;
  await opened(w);
  const first = w.flow.run();
  const second = w.flow.run();
  assert.equal(w.held.run.length, 3);
  assert.equal(w.held.run[1].aborted, true, "the older request is aborted");
  w.held.run[2].resolve(runAnswer(A, { elapsed_ms: 2000 }));
  await second;
  w.held.run[1].resolve(runAnswer(A, { elapsed_ms: 99999 })); // arrives late
  await first;
  assert.equal(w.flow.state.run.elapsedMs, 2000);
  assert.equal(w.flow.state.run.status, "ready");
});

for (const reject of [true, false]) test("an answer after a case change is dropped and the results of the old case are cleared" + (reject ? "" : " (the old response still arrives)"), async () => {
  const w = setup();
  w.rejectOnAbort = reject;
  await opened(w);
  const running = w.flow.run();
  w.caseId = "B";
  w.flow.caseChanged();
  assert.equal(w.flow.state.run.status, "idle");
  assert.deepEqual(w.flow.state.run.results, {});
  assert.equal(w.live().length, 0, "the stream of the old case is closed");
  w.held.run[1].resolve(runAnswer(A, { elapsed_ms: 7 }));
  await running;
  assert.equal(w.flow.state.run.status, "idle");
  assert.deepEqual(w.flow.state.run.results, {});
  w.flow.setShown(true); // the dock refreshes the open tab
  await flush();
  assert.deepEqual(lastRun(w).args, ["B", A.id, undefined]);
  assert.equal(w.live()[0].url, "/api/cases/B/progress");
});

for (const reject of [true, false]) test("choosing another panel during a run drops that run and runs the new panel" + (reject ? "" : " (the old response still arrives)"), async () => {
  const w = setup();
  w.rejectOnAbort = reject;
  await opened(w);
  const running = w.flow.run();
  assert.equal(w.flow.select(B.id), true);
  w.held.run[1].resolve(runAnswer(A));
  await running;
  assert.equal(w.flow.state.run.status, "running", "only the run of Beta is current");
  assert.deepEqual(lastRun(w).args, ["A", B.id, undefined]);
  lastRun(w).resolve(runAnswer(B));
  await flush();
  assert.equal(w.flow.state.run.panelId, B.id);
  assert.deepEqual(Object.keys(w.flow.state.run.results), ["w1"]);
});

test("results of widgets the panel does not have are ignored", async () => {
  const w = setup();
  await opened(w, { run: false });
  const answer = runAnswer(A);
  answer.results[0] = { id: "w1", status: "weird", result: null, error: null };
  answer.results.push({ id: "ghost", status: "ok", result: null, error: null });
  w.held.run[0].resolve(answer);
  await flush();
  assert.deepEqual(Object.keys(w.flow.state.run.results), ["w2"]);
});

test("a progress notice reruns every widget of the open panel, once the minimum interval has passed", async () => {
  const w = setup();
  await opened(w);
  w.advance(6000);
  noticeAt(w, { seq: 1, throttled: false, retry_after_ms: null, min_interval_ms: 5000 });
  assert.equal(w.held.run.length, 2, "ran at once: the last run started more than 5 s ago");
  assert.equal(w.held.run[1].args[2], undefined, "no widget list: all of them");
  w.held.run[1].resolve(runAnswer(A));
  await flush();
  noticeAt(w, { seq: 2, throttled: false, min_interval_ms: 5000 });
  assert.equal(w.held.run.length, 2, "too soon: waits");
  assert.equal(w.timers.size, 1);
  w.advance(4999);
  assert.equal(w.held.run.length, 2);
  w.advance(1);
  assert.equal(w.held.run.length, 3);
  assert.equal(w.timers.size, 0);
});

test("a throttled notice waits the time the server asked for; notices meanwhile share the one timer", async () => {
  const w = setup();
  await opened(w);
  w.advance(10000);
  noticeAt(w, { seq: 1, throttled: true, retry_after_ms: 3000, min_interval_ms: 5000 });
  assert.equal(w.held.run.length, 1);
  assert.equal([...w.timers.values()][0].ms, 3000);
  noticeAt(w, { seq: 2, throttled: true, retry_after_ms: 4000, min_interval_ms: 5000 });
  assert.equal(w.timers.size, 1, "a later deadline adds nothing");
  assert.equal([...w.timers.values()][0].ms, 3000);
  noticeAt(w, { seq: 3, throttled: true, retry_after_ms: 1000, min_interval_ms: 5000 });
  assert.equal(w.timers.size, 1, "an earlier deadline replaces it");
  assert.equal([...w.timers.values()][0].ms, 1000);
  w.advance(999);
  assert.equal(w.held.run.length, 1);
  w.advance(1);
  assert.equal(w.held.run.length, 2);
  assert.equal(w.timers.size, 0);
});

test("a notice that is not throttled runs at once when the interval has passed, and the scheduled run is not repeated", async () => {
  const w = setup();
  await opened(w);
  w.advance(10000);
  noticeAt(w, { seq: 1, throttled: true, retry_after_ms: 3000, min_interval_ms: 5000 });
  assert.equal(w.timers.size, 1);
  noticeAt(w, { seq: 2, throttled: false, min_interval_ms: 5000 });
  assert.equal(w.held.run.length, 2);
  assert.equal(w.timers.size, 0);
});

test("a notice during a run asks for exactly one more run, after the interval", async () => {
  const w = setup();
  await opened(w);
  w.advance(6000);
  noticeAt(w, { seq: 1, min_interval_ms: 5000 });
  noticeAt(w, { seq: 2, min_interval_ms: 5000 });
  noticeAt(w, { seq: 3, min_interval_ms: 5000 });
  assert.equal(w.held.run.length, 2);
  w.held.run[1].resolve(runAnswer(A));
  await flush();
  assert.equal(w.held.run.length, 2, "the interval has not passed since this run started");
  assert.equal(w.timers.size, 1);
  w.advance(5000);
  assert.equal(w.held.run.length, 3);
  w.held.run[2].resolve(runAnswer(A));
  await flush();
  assert.equal(w.timers.size, 0, "nothing else pending");
});

test("notices that are not notices, or come from a closed stream or another case, change nothing", async () => {
  const w = setup();
  await opened(w);
  w.advance(10000);
  const old = w.streams.at(-1);
  old.onNotice("not json");
  old.onNotice("[1,2]");
  assert.equal(w.held.run.length, 1);
  w.caseChanged = (id) => {
    w.caseId = id;
    w.flow.caseChanged();
  };
  w.caseChanged("B");
  old.onNotice(JSON.stringify({ seq: 9 }));
  assert.equal(w.held.run.length, 1, "the stream of the old case was closed; its late event is ignored");
  w.flow.setShown(true);
  await flush();
  w.held.run[1].resolve(runAnswer(A));
  await flush();
  w.advance(10000);
  const fresh = w.streams.at(-1);
  w.caseId = "C"; // the page moved on before the stream was told
  fresh.onNotice(JSON.stringify({ seq: 10 }));
  assert.equal(w.held.run.length, 2, "only the run of the refresh; the event of a stale case is ignored");
  assert.equal(w.timers.size, 0, "and nothing is scheduled for it");
});

test("a notice from a stream that was closed (same case, panel shown again) is ignored", async () => {
  const w = setup();
  await opened(w);
  const old = w.streams.at(-1);
  w.flow.setShown(false);
  w.flow.setShown(true);
  await flush();
  w.held.run.at(-1).resolve(runAnswer(A));
  await flush();
  const runs = w.held.run.length;
  w.advance(10000); // a live notice would run the panel at once
  old.onNotice(JSON.stringify({ seq: 1, min_interval_ms: 5000 }));
  old.onOpen();
  old.onOpen();
  assert.equal(w.held.run.length, runs);
  assert.equal(w.timers.size, 0);
});

test("an answer for a case the page has already left is dropped even if nobody told the panel", async () => {
  const w = setup();
  await opened(w);
  const running = w.flow.run();
  w.rejectOnAbort = false;
  w.caseId = "B";
  w.held.run[1].resolve(runAnswer(A, { elapsed_ms: 31337 }));
  await running;
  assert.notEqual(w.flow.state.run.elapsedMs, 31337);
});

test("reconnecting reruns the panel; the first connection does not", async () => {
  const w = setup();
  await opened(w);
  const stream = w.streams.at(-1);
  w.advance(10000);
  stream.onOpen();
  assert.equal(w.held.run.length, 1, "the first open: the run that just finished covers it");
  stream.onOpen();
  assert.equal(w.held.run.length, 2, "reconnected");
});

test("after repeated failures the stream stops, the panel says so, and Run gives live updates another chance", async () => {
  const w = setup();
  await opened(w);
  const stream = w.streams.at(-1);
  stream.close(); // the real stream closes itself before it says it stopped
  stream.onStopped();
  assert.equal(w.flow.state.streamStopped, true);
  assert.equal(w.flow.state.notice.text, S.pnLiveStopped);
  assert.equal(w.live().length, 0);
  const running = w.flow.run();
  assert.equal(w.flow.state.streamStopped, false);
  assert.equal(w.live().length, 1, "a new stream");
  lastRun(w).resolve(runAnswer(A));
  await running;
});

test("hiding the panel aborts the run, clears the timers and closes the stream; showing it runs again", async () => {
  const w = setup();
  await opened(w);
  w.advance(10000);
  noticeAt(w, { seq: 1, min_interval_ms: 5000 });
  assert.equal(w.flow.state.run.status, "running");
  noticeAt(w, { seq: 2, min_interval_ms: 5000 });
  w.flow.setShown(false);
  assert.equal(lastRun(w).aborted, true);
  assert.equal(w.timers.size, 0);
  assert.equal(w.live().length, 0);
  assert.equal(w.flow.state.run.status, "ready", "the previous results stay");
  w.flow.setShown(true);
  assert.equal(w.held.run.length, 3);
  assert.equal(w.live().length, 1);
});

test("a pending timer does nothing after the panel is hidden, the case changes, or the flow is destroyed", async () => {
  for (const stop of [(w) => w.flow.setShown(false), (w) => { w.caseId = "B"; w.flow.caseChanged(); }, (w) => w.flow.destroy()]) {
    const w = setup();
    await opened(w);
    noticeAt(w, { seq: 1, min_interval_ms: 5000 });
    assert.equal(w.timers.size, 1);
    const timer = [...w.timers.values()][0];
    stop(w);
    assert.equal(w.timers.size, 0);
    timer.fn(); // even a timer that fires anyway does nothing
    assert.equal(w.held.run.length, 1);
    assert.equal(w.live().length, 0);
  }
});

test("a revision that moved between the list and the run: the list is read again and the run is repeated once", async () => {
  const w = setup();
  w.flow.setShown(true);
  w.answerList();
  await flush();
  const moved = panel(A.id, "Alpha", A.widgets, 2);
  w.held.run[0].resolve(runAnswer(moved));
  await flush();
  assert.equal(w.held.list.length, 2);
  w.server.panels = [moved, B];
  w.answerList();
  await flush();
  assert.equal(w.held.run.length, 2);
  w.held.run[1].resolve(runAnswer(moved));
  await flush();
  assert.equal(w.flow.state.run.status, "ready");
  assert.equal(w.flow.state.run.error, "");
});

test("a revision that keeps disagreeing is reported, not retried forever", async () => {
  const w = setup();
  w.flow.setShown(true);
  w.answerList();
  await flush();
  w.held.run[0].resolve(runAnswer(panel(A.id, "Alpha", A.widgets, 5)));
  await flush();
  w.answerList(); // the list still says rev 1
  await flush();
  w.held.run[1].resolve(runAnswer(panel(A.id, "Alpha", A.widgets, 5)));
  await flush();
  assert.equal(w.held.run.length, 2);
  assert.equal(w.flow.state.run.status, "error");
  assert.equal(w.flow.state.run.error, S.pnErrors.stale_panel);
});

test("a failed run keeps the previous results and says why; the same automatic failure is said once", async () => {
  const w = setup();
  await opened(w);
  w.advance(10000);
  noticeAt(w, { seq: 1, min_interval_ms: 5000 });
  w.held.run[1].reject(new ApiFail(500, "database_error"));
  await flush();
  assert.equal(w.flow.state.run.status, "error");
  assert.equal(Object.keys(w.flow.state.run.results).length, 2);
  const first = w.flow.state.notice.seq;
  w.advance(10000);
  noticeAt(w, { seq: 2, min_interval_ms: 5000 });
  w.held.run[2].reject(new ApiFail(500, "database_error"));
  await flush();
  assert.equal(w.flow.state.notice.seq, first, "no new announcement for the same automatic failure");
  w.flow.run();
  w.held.run[3].reject(new ApiFail(500, "database_error"));
  await flush();
  assert.ok(w.flow.state.notice.seq > first, "a manual Run always answers");
});

test("a panel deleted somewhere else: the run reports it, the list is read again and the selection moves on", async () => {
  const w = setup();
  await opened(w);
  w.flow.run();
  w.held.run[1].reject(new ApiFail(404, "not_found"));
  await flush();
  w.server.panels = [B];
  w.answerList();
  await flush();
  assert.equal(w.flow.state.selectedId, B.id);
  assert.equal(w.flow.state.list.panels.length, 1);
});

test("without a case nothing is asked and the reason is said", async () => {
  const w = setup({ caseId: null });
  w.flow.setShown(true);
  w.answerList();
  await flush();
  assert.equal(w.held.run.length, 0);
  assert.equal(w.live().length, 0);
});

test("an empty panel is not sent to the server", async () => {
  const w = setup({ panels: [panel("cccccccccccc", "Empty", [])] });
  w.flow.setShown(true);
  w.answerList();
  await flush();
  assert.equal(w.held.run.length, 0);
  assert.equal(w.flow.state.run.status, "ready");
});

// ---- creating, renaming, deleting

test("New: the name is checked first, one change at a time, the new panel is selected and gets the focus", async () => {
  const w = setup();
  await opened(w);
  assert.equal(w.flow.startNaming("create"), true);
  assert.equal(await w.flow.submitName("   "), false);
  assert.match(w.flow.state.notice.text, /1 to 80/);
  assert.equal(w.held.create.length, 0);
  const saving = w.flow.submitName("  Gamma  ");
  assert.equal(w.flow.state.busy, "create");
  assert.equal(await w.flow.submitName("Delta"), false, "a second change while one is being written");
  assert.deepEqual(w.held.create[0].args[0], { name: "Gamma", widgets: [] });
  const created = panel("cccccccccccc", "Gamma", [], 1);
  w.held.create[0].resolve(created);
  assert.equal(await saving, true);
  assert.equal(w.flow.state.selectedId, created.id);
  assert.equal(w.flow.state.naming, null);
  assert.equal(w.flow.state.focus.target, "heading");
  assert.equal(w.flow.state.busy, null);
  assert.equal(w.held.run.length, 1, "an empty panel needs no run");
});

test("a refused name stays open with the text of the code; duplicate_name keeps what was typed", async () => {
  const w = setup();
  await opened(w);
  w.flow.startNaming("create");
  const saving = w.flow.submitName("Alpha");
  w.held.create[0].reject(new ApiFail(409, "duplicate_name"));
  assert.equal(await saving, false);
  assert.equal(w.flow.state.notice.text, S.pnErrors.duplicate_name);
  assert.deepEqual(w.flow.state.naming, { kind: "create", id: null });
});

test("New is refused at the panel limit, before any request", async () => {
  const w = setup();
  w.flow.setShown(true);
  w.answerList(0, { panels: [A, B], problems: [], invalid: false, limits: { panels: 2 } });
  await flush();
  w.held.run[0].resolve(runAnswer(A));
  await flush();
  assert.equal(w.flow.startNaming("create"), false);
  assert.match(w.flow.state.notice.text, /at most 2 panels/);
});

test("Rename writes over the revision that was seen; a conflict reads the list again", async () => {
  const w = setup();
  await opened(w);
  w.flow.startNaming("rename");
  const saving = w.flow.submitName("Alpha 2");
  assert.deepEqual(w.held.update[0].args, [A.id, { name: "Alpha 2", widgets: A.widgets }, 1]);
  w.held.update[0].reject(new ApiFail(409, "rev_conflict"));
  await flush();
  assert.equal(w.held.list.length, 2, "the list is read again");
  w.answerList();
  await saving;
  assert.equal(w.flow.state.notice.text, S.pnErrors.rev_conflict);
  assert.equal(w.flow.state.naming, null);
});

test("Delete asks first; it deletes the revision the user saw, and refuses a panel that changed meanwhile", async () => {
  const w = setup();
  await opened(w);
  assert.equal(w.flow.askDelete(), true);
  assert.equal(w.flow.state.confirming, true);
  assert.equal(w.flow.state.focus.target, "confirm");
  w.flow.cancelDelete();
  assert.equal(w.flow.state.confirming, false);
  assert.equal(w.flow.state.focus.target, "delete");
  w.flow.askDelete();
  const deleting = w.flow.confirmDelete();
  assert.equal(w.held.list.length, 2);
  const changed = panel(A.id, "Alpha", A.widgets, 2);
  w.held.list[1].resolve({ panels: [changed, B], problems: [], invalid: false, limits: {} });
  assert.equal(await deleting, false);
  assert.equal(w.held.remove.length, 0, "nothing was deleted");
  assert.equal(w.flow.state.notice.text, S.pnErrors.changed_before_delete);
  assert.equal(w.flow.state.confirming, false);
  assert.equal(w.flow.state.list.panels.find((p) => p.id === A.id).rev, 2);
});

test("Delete: removes the panel, selects the next one by name, gives the focus to the list and runs the new panel", async () => {
  const w = setup();
  await opened(w);
  w.flow.askDelete();
  const deleting = w.flow.confirmDelete();
  w.held.list[1].resolve({ panels: [A, B], problems: [], invalid: false, limits: {} });
  await flush();
  assert.deepEqual(w.held.remove[0].args, [A.id]);
  w.held.remove[0].resolve(null);
  assert.equal(await deleting, true);
  assert.equal(w.flow.state.selectedId, B.id);
  assert.equal(w.flow.state.focus.target, "select");
  assert.equal(w.flow.state.list.panels.length, 1);
  assert.deepEqual(lastRun(w).args, ["A", B.id, undefined]);
});

test("Delete of a panel that is already gone counts as deleted", async () => {
  const w = setup();
  await opened(w);
  w.flow.askDelete();
  const deleting = w.flow.confirmDelete();
  w.held.list[1].resolve({ panels: [B], problems: [], invalid: false, limits: {} });
  assert.equal(await deleting, true);
  assert.equal(w.held.remove.length, 0);
  assert.equal(w.flow.state.selectedId, B.id);
});

test("choosing a panel is refused while a change is being written or a draft is open", async () => {
  const w = setup();
  await opened(w);
  w.flow.edit();
  assert.equal(w.flow.select(B.id), false);
  w.flow.cancelEdit();
  assert.equal(w.flow.select(B.id), true);
  assert.equal(w.flow.select("zzzzzzzzzzzz"), false, "an id the list does not have");
});

// ---- editing

function editing(w) {
  assert.equal(w.flow.edit(), true);
  return w.flow.state;
}

test("editing pauses the panel: the run is aborted, the stream closed, and notices do nothing", async () => {
  const w = setup();
  await opened(w);
  w.advance(10000);
  const running = w.flow.run();
  editing(w);
  assert.equal(lastRun(w).aborted, true);
  assert.equal(w.live().length, 0);
  assert.equal(w.timers.size, 0);
  w.held.run[1].resolve(runAnswer(A));
  await running;
  assert.equal(w.flow.state.mode, "edit");
  assert.notEqual(w.flow.state.run.status, "running");
});

test("Cancel drops the draft and runs the saved panel again", async () => {
  const w = setup();
  await opened(w);
  editing(w);
  w.flow.updateWidget("w1", { title: "Changed" });
  w.flow.cancelEdit();
  assert.equal(w.flow.state.draft, null);
  assert.equal(w.flow.state.list.panels.find((p) => p.id === A.id).widgets[0].title, "Widget w1");
  assert.equal(w.held.run.length, 2);
  assert.equal(w.live().length, 1);
});

test("add, order, change and remove widgets in the draft; the options follow the chart and the query", async () => {
  const w = setup();
  await opened(w);
  editing(w);
  const id = w.flow.addWidget();
  assert.equal(id, "w3");
  assert.equal(w.flow.state.editingWidget, "w3");
  w.flow.updateWidget("w3", { title: "Per type", chart: "bar" });
  assert.deepEqual(w.flow.state.draft.widgets[2].options, { x: "tpl", y: "count" });
  w.flow.updateWidget("w3", { query: { rows: "entries", group_by: ["tpl", "sev"], measures: ["count"], limit: 50 } });
  assert.deepEqual(w.flow.state.draft.widgets[2].options, { series: "sev", x: "tpl", y: "count" });
  w.flow.updateWidget("w3", { chart: "line" });
  assert.deepEqual(w.flow.state.draft.widgets[2].options, {}, "a line chart cannot draw a query without a time bucket");
  w.flow.moveWidget("w3", "up");
  assert.deepEqual(w.flow.state.draft.widgets.map((x) => x.id), ["w1", "w3", "w2"]);
  assert.equal(w.flow.state.focus.target, "up:w3");
  assert.equal(w.flow.moveWidget("w1", "up"), false);
  w.flow.removeWidget("w2");
  assert.deepEqual(w.flow.state.draft.widgets.map((x) => x.id), ["w1", "w3"]);
});

test("Save is refused, with the reason, while a chart does not fit its query or a form is invalid", async () => {
  const w = setup();
  await opened(w);
  editing(w);
  w.flow.addWidget();
  w.flow.updateWidget("w3", { chart: "line" });
  assert.equal(await w.flow.saveEdit(), false);
  assert.equal(w.held.update.length, 0);
  assert.equal(w.flow.state.notice.text, S.pnFit.line);
  assert.equal(w.flow.state.editingWidget, "w3");
  w.flow.updateWidget("w3", { chart: "table" });
  w.flow.setFormError("w3", "Give at least one measure.");
  assert.equal(await w.flow.saveEdit(), false);
  assert.equal(w.flow.state.notice.text, "Give at least one measure.");
  w.flow.setEditingWidget("w1"); // leaving the form: the draft keeps the last valid query
  assert.equal(w.flow.state.formErrors.w3, undefined);
});

test("Save sends the draft with the revision it started from, leaves the editor, and runs the saved panel", async () => {
  const w = setup();
  await opened(w);
  editing(w);
  w.flow.updateWidget("w1", { title: "Changed" });
  const saving = w.flow.saveEdit();
  assert.equal(w.flow.state.busy, "save");
  assert.equal(w.held.update[0].args[0], A.id);
  assert.equal(w.held.update[0].args[2], 1);
  assert.equal(w.held.update[0].args[1].widgets[0].title, "Changed");
  assert.equal(w.held.update[0].args[1].id, undefined);
  const saved = panel(A.id, "Alpha", w.held.update[0].args[1].widgets, 2);
  w.held.update[0].resolve(saved);
  assert.equal(await saving, true);
  assert.equal(w.flow.state.mode, "view");
  assert.equal(w.flow.state.draft, null);
  assert.equal(w.flow.state.focus.target, "heading");
  assert.equal(w.held.run.length, 2);
  assert.equal(w.live().length, 1);
});

test("a conflict on Save keeps every edit, reads the list again, and the next Save writes over the new revision", async () => {
  const w = setup();
  await opened(w);
  editing(w);
  w.flow.updateWidget("w1", { title: "Mine" });
  const saving = w.flow.saveEdit();
  w.held.update[0].reject(new ApiFail(409, "rev_conflict"));
  await flush();
  w.server.panels = [panel(A.id, "Alpha", [widget("w1", { title: "Theirs" })], 4), B];
  w.answerList();
  await saving;
  await flush();
  assert.equal(w.flow.state.mode, "edit");
  assert.equal(w.flow.state.draft.widgets[0].title, "Mine");
  assert.equal(w.flow.state.draft.baseRev, 4);
  assert.equal(w.flow.state.notice.text, S.pnErrors.rev_conflict);
  const again = w.flow.saveEdit();
  assert.equal(w.held.update[1].args[2], 4);
  w.held.update[1].resolve(panel(A.id, "Alpha", w.held.update[1].args[1].widgets, 5));
  assert.equal(await again, true);
});

test("a panel deleted while it is being edited: the draft stays and Save creates it again", async () => {
  const w = setup();
  await opened(w);
  editing(w);
  w.flow.updateWidget("w1", { title: "Mine" });
  const saving = w.flow.saveEdit();
  w.held.update[0].reject(new ApiFail(404, "not_found"));
  await flush();
  w.server.panels = [B];
  w.answerList();
  await saving;
  await flush();
  assert.equal(w.flow.state.mode, "edit");
  assert.equal(w.flow.state.draft.recreate, true);
  assert.equal(w.flow.state.draft.widgets[0].title, "Mine");
  const again = w.flow.saveEdit();
  assert.equal(w.held.create.length, 1);
  assert.equal(w.held.create[0].args[0].name, "Alpha");
  const created = panel("dddddddddddd", "Alpha", w.held.create[0].args[0].widgets, 1);
  w.held.create[0].resolve(created);
  assert.equal(await again, true);
  assert.equal(w.flow.state.selectedId, created.id);
});

test("a list reload that no longer has the panel being edited turns the draft into a new panel", async () => {
  const w = setup();
  await opened(w);
  editing(w);
  w.flow.reloadAll();
  w.server.panels = [B];
  w.answerList();
  await flush();
  assert.equal(w.flow.state.mode, "edit");
  assert.equal(w.flow.state.draft.recreate, true);
  assert.equal(w.flow.state.notice.text, S.pnErrors.deleted_while_editing);
});

test("an unreadable answer to a write is a failure, not a panel", async () => {
  const w = setup();
  await opened(w);
  w.flow.startNaming("create");
  const saving = w.flow.submitName("Gamma");
  w.held.create[0].resolve({ nonsense: true });
  assert.equal(await saving, false);
  assert.equal(w.flow.state.list.panels.length, 2);
  assert.equal(w.flow.state.busy, null);
});

test("destroy aborts everything: no timer, no stream, no request left, no state update afterwards", async () => {
  const w = setup();
  await opened(w);
  w.advance(10000);
  noticeAt(w, { seq: 1, min_interval_ms: 5000 });
  const before = w.states;
  w.flow.destroy();
  assert.equal(w.timers.size, 0);
  assert.equal(w.live().length, 0);
  assert.equal(lastRun(w).aborted, true);
  w.flow.run();
  await flush();
  assert.equal(w.states, before);
});

test("choosing another panel keeps the case's stream (it belongs to the case, not to the panel)", async () => {
  const w = setup();
  await opened(w);
  const stream = w.streams.at(-1);
  w.flow.select(B.id);
  assert.equal(stream.closed, false);
  assert.equal(w.live().length, 1);
  assert.equal(w.streams.length, 1);
});

test("a list still on its way when a write is answered is dropped, not applied over the newer panel", async () => {
  const w = setup();
  await opened(w);
  w.flow.reloadAll();
  const old = w.held.list.at(-1);
  w.flow.startNaming("rename");
  const saving = w.flow.submitName("Alpha 2");
  const renamed = panel(A.id, "Alpha 2", A.widgets, 2);
  w.held.update[0].resolve(renamed);
  await saving;
  assert.equal(old.aborted, true);
  old.resolve({ panels: [A, B], problems: [], invalid: false, limits: {} }); // too late: the old revision
  await flush();
  assert.equal(w.flow.state.list.panels.find((p) => p.id === A.id).rev, 2);
  assert.equal(w.flow.state.list.panels.find((p) => p.id === A.id).name, "Alpha 2");
});

test("a write answered before the first list ever arrived reads the list afterwards", async () => {
  const w = setup();
  w.flow.setShown(true);
  w.held.list[0].reject(new ApiFail(0, "network"));
  await flush();
  assert.equal(w.flow.state.list.status, "error");
  w.flow.startNaming("create");
  const saving = w.flow.submitName("Gamma");
  w.held.create[0].resolve(panel("cccccccccccc", "Gamma", [], 1));
  await saving;
  await flush();
  assert.equal(w.held.list.length, 2, "the list is asked again");
  w.answerList(1, { panels: [A, B, panel("cccccccccccc", "Gamma", [], 1)], problems: [], invalid: false, limits: {} });
  await flush();
  assert.equal(w.flow.state.list.panels.length, 3);
  assert.equal(w.flow.state.selectedId, "cccccccccccc");
});

test("a panel holds at most the number of widgets the server said", async () => {
  const w = setup();
  w.flow.setShown(true);
  w.answerList(0, { panels: [A, B], problems: [], invalid: false, limits: { widgets: 2 } });
  await flush();
  w.held.run[0].resolve(runAnswer(A));
  await flush();
  w.flow.edit();
  assert.equal(w.flow.addWidget(), null, "Alpha already has two");
  assert.match(w.flow.state.notice.text, /at most 2 widgets/);
  assert.equal(w.flow.state.draft.widgets.length, 2);
});
