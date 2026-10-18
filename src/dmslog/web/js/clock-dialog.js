// "Align clock" (task 9.15, ARQUITETURA 10.13): the dialog that gives a collection a clock offset from two records the user
// picked (anchor in another collection, target in this one) or from a manual value, and "Reset clock". Loaded on demand.
//
// One OPERATION at a time (`current`): {token, caseId, group, anchor, target, mode, ...}. Every answer that arrives later is
// checked against it (same operation object, same case, same dry-run token) before it touches the page; records are kept by
// id and time, never as elements, so a row that leaves the grid window changes nothing. Closing the dialog aborts the dry run.
// The PUT/DELETE itself is not aborted (it may already have been applied): its answer reloads the view if the case is still
// the same, and is dropped if another case is open. One clock change at a time (`mutating`).
// Everything from the API or from a log reaches the page through textContent only.
import { createDialogShell, node, setStatusLine, statusLine } from "./panel-shell.js";
import {
  acceptApplied, acceptPreview, createPicks, currentText, errorText, formatOffset, isAbort, pairBody, pairOf, parseOffset,
  previewText, problemText, recordRef, refReasonText, sameScope, timeText,
} from "./clock-model.js";
import { S, format } from "./strings.js";

let uid = 0;

// host: where the dialog is added (document.body). Options:
//   api                   { clock: { set(caseId, group, body, signal), reset(caseId, group, signal) } }
//   getContext()          {caseId, generation}: the open case (null when none) and its generation, a counter that grows every time
//                         a case is opened, so an answer meant for case A cannot act on A opened again later
//                         (every method that takes `expected` does nothing when it no longer matches the live context)
//   getCollections()      the list of GET /collections of the open case
//   groupOfSource(id)     the collection group of a source, or null
//   view                  what the page offers to put the view right after an offset changed (the sequence and its checks live
//                         here, so every wait is followed by a check of the scope):
//                         keep() -> {id, ts} | null     the record the user is on (selected, else shown in the detail)
//                         refreshList() -> Promise<"failed" | other>  reload GET /collections (offsets, `clocks`, the Adjusted time column);
//                                                       "failed" = the list is the old one: the reload stops and offers a retry
//                         applyView() -> Promise        reload the grid, the timeline and the filter strip
//                         refreshDerived()              the panels fed by derived events
//                         reveal({id, ts}) -> Promise   select that record again, at its adjusted time
//                         selectedId() -> id | null     the selected record
//                         clearDetail()                 the record left the view: the detail is emptied
//   onMessage(text, isError, action?)   (action = {label, run}: a button on the message) the page's message region (the dialog has its own for what happens while it is open)
// Returns { setAnchor(entry, expected), setTarget(entry, opener, expected), align(group, opener, expected), reset(group, expected),
//           forgetCase(), busy(), isOpen(), picked() }; align() resolves when the dialog is closed.
export function mountClock(host, options) {
  const doc = host.ownerDocument || document;
  const { api, getContext, getCollections, groupOfSource, view, onMessage } = options;
  const scopeNow = () => {
    const context = getContext();
    return { caseId: context.caseId, generation: context.generation };
  };
  const here = (scope) => sameScope(scope, scopeNow());
  // `expected` (optional): the scope the caller saw when it decided; a caller that waited (a module load) passes it back.
  const stale = (expected) => expected !== undefined && expected !== null && !here(expected);
  const picks = createPicks();
  let seq = 0;
  let current = null; // the operation the dialog shows
  let ui = null; // the dialog's elements while it is open
  let mutating = null; // {scope, group}: the PUT/DELETE in flight
  let waiters = []; // align() promises waiting for the close

  function collectionOf(group) {
    const list = getCollections() || [];
    for (let i = 0; i < list.length; i += 1) {
      if (list[i].group === group) {
        return list[i];
      }
    }
    return null;
  }

  function tagOf(group) {
    const item = collectionOf(group);
    return item ? item.tag || format(S.colTag, { number: item.number }) : String(group);
  }

  const live = (op) => current === op && ui !== null && here(op.scope);

  // ---- picking the records ------------------------------------------------------------------------------------------

  function pick(entry) {
    const made = recordRef(entry, { scope: scopeNow(), groupOfSource });
    if (!made.ok) {
      onMessage(refReasonText(made.reason), true);
      return null;
    }
    return made.ref;
  }

  function setAnchor(entry, expected) {
    if (stale(expected)) {
      return false;
    }
    const ref = pick(entry);
    if (ref === null) {
      return false;
    }
    picks.setAnchor(ref);
    onMessage(format(S.ckAnchorSet, { id: ref.id, tag: tagOf(ref.group), time: timeText(ref.ts) }), false);
    return true;
  }

  // The target completes the pair: with an anchor in another collection the dialog opens for the target's collection.
  function setTarget(entry, opener, expected) {
    if (stale(expected)) {
      return Promise.resolve();
    }
    const ref = pick(entry);
    if (ref === null) {
      return Promise.resolve();
    }
    picks.setTarget(ref);
    const anchor = picks.anchor(ref);
    if (anchor === null) {
      onMessage(format(S.ckTargetNoAnchor, { id: ref.id, tag: tagOf(ref.group), time: timeText(ref.ts) }), false);
      return Promise.resolve();
    }
    if (anchor.group === ref.group) {
      onMessage(S.ckSameCollection, true);
      return Promise.resolve();
    }
    return align(ref.group, opener, ref);
  }

  // ---- the dialog ---------------------------------------------------------------------------------------------------

  function align(group, opener, expected) {
    const scope = scopeNow();
    if (stale(expected) || scope.caseId === null || scope.caseId === undefined) {
      return Promise.resolve();
    }
    if (collectionOf(group) === null || (getCollections() || []).length < 2) {
      onMessage(S.ckAlignNeedTwo, true);
      return Promise.resolve();
    }
    if (current !== null) {
      return new Promise((resolve) => waiters.push(resolve)); // already open: the modal dialog is what the user sees
    }
    if (mutating !== null) {
      onMessage(S.ckBusy, true);
      return Promise.resolve();
    }
    const anchor = picks.anchor(scope);
    const target = picks.target(scope);
    const op = {
      token: (seq += 1), scope, caseId: scope.caseId, group, anchor, target, pair: pairOf(group, anchor, target), mode: "pair",
      previous: collectionOf(group).clock_offset_ms || 0, manualText: "", preview: null, dry: null, dryToken: 0, applying: false,
    };
    current = op;
    build(op, opener);
    const closed = new Promise((resolve) => waiters.push(resolve));
    runDry(op);
    return closed;
  }

  function build(op, opener) {
    const tag = tagOf(op.group);
    const shell = createDialogShell(doc, {
      title: format(S.ckDialogTitle, { tag }),
      className: "ck-dialog",
      onClose: () => finishClose(op),
    });
    const body = shell.body;

    const modes = node(doc, "fieldset", "ck-modes");
    modes.appendChild(node(doc, "legend", "ck-legend", S.ckModeLegend));
    const radios = {};
    for (const [value, label] of [["pair", S.ckModePair], ["manual", S.ckModeManual]]) {
      const row = node(doc, "label", "ck-mode");
      const radio = node(doc, "input", "ck-mode-input");
      radio.type = "radio";
      radio.setAttribute("name", "ck-mode-" + op.token);
      radio.value = value;
      radio.checked = value === op.mode;
      radio.addEventListener("change", () => {
        if (radio.checked) {
          setMode(op, value);
        }
      });
      row.append(radio, node(doc, "span", "ck-mode-text", label));
      modes.appendChild(row);
      radios[value] = radio;
    }

    const pairBox = node(doc, "div", "ck-pair");
    pairBox.appendChild(node(doc, "p", "ck-intro", format(S.ckPairIntro, { tag })));
    const table = node(doc, "table", "ck-table");
    table.appendChild(node(doc, "caption", "visually-hidden", S.ckTableCaption));
    const head = node(doc, "tr");
    head.append(node(doc, "td", "ck-corner"), headerCell(S.ckHeadAnchor), headerCell(S.ckHeadTarget));
    table.appendChild(head);
    const cells = {};
    for (const [key, label] of [["coll", S.ckRowCollection], ["rec", S.ckRowRecord], ["orig", S.ckRowOriginal], ["adj", S.ckRowAdjusted]]) {
      const row = node(doc, "tr", "ck-row-" + key);
      const rowHead = node(doc, "th", "ck-rowhead", label);
      rowHead.setAttribute("scope", "row");
      const anchorCell = node(doc, "td", "ck-cell ck-anchor");
      const targetCell = node(doc, "td", "ck-cell ck-target");
      row.append(rowHead, anchorCell, targetCell);
      table.appendChild(row);
      cells[key] = { anchor: anchorCell, target: targetCell };
    }
    const problem = node(doc, "p", "ck-hint");
    const how = node(doc, "p", "ck-how", format(S.ckPairHow, { tag }));
    pairBox.append(table, problem, how);

    const manualBox = node(doc, "div", "ck-manual");
    const field = node(doc, "label", "ck-field");
    field.appendChild(node(doc, "span", "ck-field-label", S.ckManualLabel));
    const input = node(doc, "input", "ck-input");
    input.type = "text";
    input.value = "";
    input.setAttribute("inputmode", "numeric");
    input.setAttribute("autocomplete", "off");
    input.setAttribute("spellcheck", "false");
    const hint = node(doc, "p", "ck-manual-hint", S.ckManualHint);
    hint.id = "ck-manual-hint-" + op.token;
    input.setAttribute("aria-describedby", hint.id);
    input.addEventListener("input", () => {
      op.manualText = input.value;
      input.removeAttribute("aria-invalid");
      paint(op);
    });
    input.addEventListener("change", () => commitManual(op));
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        apply(op);
      }
    });
    field.appendChild(input);
    manualBox.append(field, hint);

    const current_ = node(doc, "p", "ck-current");
    const preview = node(doc, "p", "ck-preview");
    const status = statusLine(doc, "ck-status");
    body.append(modes, pairBox, manualBox, current_, preview, status);

    const cancel = node(doc, "button", "ui-button is-ghost ck-cancel", S.ckCancel);
    cancel.type = "button";
    cancel.addEventListener("click", () => shell.close());
    const applyButton = node(doc, "button", "ui-button is-primary ck-apply", S.ckApply);
    applyButton.type = "button";
    applyButton.addEventListener("click", () => apply(op));
    shell.foot.append(cancel, applyButton);

    ui = { shell, radios, pairBox, manualBox, cells, problem, how, input, current: current_, preview, status, applyButton };
    host.appendChild(shell.dialog);
    paint(op);
    shell.open(opener || doc.activeElement);
  }

  function headerCell(text) {
    const cell = node(doc, "th", "ck-colhead", text);
    cell.setAttribute("scope", "col");
    return cell;
  }

  function setStatus(op, text, isError) {
    if (live(op)) {
      setStatusLine(ui.status, text, isError);
    }
  }

  function canApply(op) {
    if (op.mode === "pair") {
      return op.preview !== null;
    }
    const parsed = parseOffset(op.manualText);
    return parsed.ok && parsed.ms !== op.previous;
  }

  // Draws everything that depends on the operation. Idempotent; nothing here is announced (the status line is).
  function paint(op) {
    if (!live(op)) {
      return;
    }
    const tag = tagOf(op.group);
    const pair = op.mode === "pair";
    ui.pairBox.hidden = !pair;
    ui.manualBox.hidden = pair;
    ui.radios.pair.checked = pair;
    ui.radios.manual.checked = !pair;
    const { anchor, target } = op.pair;
    const shown = op.preview;
    const none = S.ckNotChosen;
    const put = (key, anchorText, targetText) => {
      ui.cells[key].anchor.textContent = anchorText;
      ui.cells[key].target.textContent = targetText;
    };
    put("coll", anchor ? tagOf(anchor.group) : "", tagOf(op.group));
    put("rec", anchor ? format(S.ckRecordLabel, { id: anchor.id }) : none, target ? format(S.ckRecordLabel, { id: target.id }) : none);
    put("orig", anchor ? timeText(anchor.ts) : "", target ? timeText(target.ts) : "");
    put("adj", anchor ? timeText(shown ? shown.anchor.ts_adjusted : anchor.tsAdjusted) : "", shown ? timeText(shown.target.ts_adjusted) : "");
    ui.problem.textContent = problemText(op.pair.problem, {
      tag, other: target === null && op.target !== null ? tagOf(op.target.group) : "",
    });
    ui.problem.hidden = ui.problem.textContent === "";
    ui.how.hidden = anchor !== null && target !== null;
    const previous = shown ? shown.previous_ms : op.previous;
    ui.current.textContent = currentText(previous);
    let text = "";
    if (pair && shown) {
      text = previewText({ tag, offset: shown.clock_offset_ms, previous, skew: shown.skew_ms });
    } else if (!pair) {
      const parsed = parseOffset(op.manualText);
      text = parsed.ok ? previewText({ tag, offset: parsed.ms, previous }) : "";
    }
    ui.preview.textContent = text;
    ui.preview.hidden = text === "";
    const busy = op.applying;
    ui.applyButton.textContent = busy ? S.ckApplying : S.ckApply;
    ui.applyButton.disabled = !busy && !canApply(op);
    ui.applyButton.setAttribute("aria-disabled", busy ? "true" : "false"); // stays focusable while it works
    ui.shell.body.setAttribute("aria-busy", busy ? "true" : "false");
  }

  function setMode(op, mode) {
    if (!live(op) || op.applying || op.mode === mode) {
      paint(op);
      return;
    }
    abortDry(op);
    op.mode = mode;
    setStatus(op, "", false);
    paint(op);
    if (mode === "pair") {
      runDry(op);
    }
  }

  // The manual field was committed (Enter or leaving it): say what applying would do, or what is wrong. Announced once.
  function commitManual(op) {
    if (!live(op) || op.mode !== "manual") {
      return;
    }
    const parsed = parseOffset(op.manualText);
    if (op.manualText.trim() === "") {
      setStatus(op, "", false);
      return;
    }
    if (!parsed.ok) {
      ui.input.setAttribute("aria-invalid", "true");
      setStatus(op, S.ckManualBad, true);
      return;
    }
    setStatus(op, previewText({ tag: tagOf(op.group), offset: parsed.ms, previous: op.previous }), false);
  }

  // ---- dry run ------------------------------------------------------------------------------------------------------

  function abortDry(op) {
    op.dryToken += 1; // an answer in flight is stale from now on
    if (op.dry !== null) {
      op.dry.abort();
      op.dry = null;
    }
  }

  async function runDry(op) {
    const { anchor, target } = op.pair;
    if (!live(op) || op.mode !== "pair" || anchor === null || target === null) {
      return;
    }
    abortDry(op);
    const token = op.dryToken;
    const controller = new AbortController();
    op.dry = controller;
    op.preview = null;
    setStatus(op, S.ckChecking, false);
    paint(op);
    let result;
    try {
      result = await api.clock.set(op.caseId, op.group, pairBody(anchor, target, true), controller.signal);
    } catch (err) {
      if (!live(op) || token !== op.dryToken || isAbort(err)) {
        return;
      }
      op.dry = null;
      setStatus(op, errorText(err), true);
      paint(op);
      return;
    }
    if (!live(op) || token !== op.dryToken) {
      return;
    }
    op.dry = null;
    if (!acceptPreview(result, { group: op.group, anchor, target })) {
      setStatus(op, S.ckErrFailed, true);
      paint(op);
      return;
    }
    op.preview = result;
    paint(op);
    setStatus(op, ui.preview.textContent, false);
  }

  // ---- apply --------------------------------------------------------------------------------------------------------

  // Sends one PUT/DELETE; at most one in flight. Resolves to {result, error}; it never rejects.
  async function mutate(scope, group, send) {
    mutating = { scope, group };
    try {
      return { result: await send(), error: null };
    } catch (err) {
      return { result: null, error: err };
    } finally {
      mutating = null;
    }
  }

  // The request failed without a definite refusal from the server (network, 5xx, an unreadable answer): it may have been
  // applied, so the view is read again. A 4xx changed nothing.
  const uncertain = (err) => !(err && Number.isInteger(err.status) && err.status >= 400 && err.status < 500);

  // The page is put right after the offsets changed: list, view, derived panels, then the record the user was on (by id, at
  // its adjusted time). The scope is checked after EVERY wait, so a case opened meanwhile (even the same one again) is never
  // reloaded or selected by this old operation. A failure is reported with the static sentence of its code.
  async function reload(scope) {
    try {
      const keep = view.keep();
      const listed = await view.refreshList();
      if (!here(scope)) {
        return;
      }
      if (listed === "failed") {
        // The offsets changed but the list (offsets, `clocks`, the Adjusted column) is the old one: reloading the grid now would
        // mix the new order with the old column and interval. Stop, say so, and offer to repeat the reload.
        onMessage(S.ckReloadFailed, true, { label: S.ckReloadRetry, run: () => reload(scope) });
        return;
      }
      await view.applyView();
      if (!here(scope)) {
        return;
      }
      view.refreshDerived();
      if (keep === null) {
        return;
      }
      try {
        await view.reveal(keep);
      } catch (err) {
        // the record may be outside the view: decided below
      }
      if (here(scope) && view.selectedId() !== keep.id) {
        view.clearDetail();
        onMessage(S.ckRecordLeftView, false); // explicit: the detail does not silently show another record
      }
    } catch (err) {
      if (here(scope)) {
        onMessage(errorText(err), true);
      }
    }
  }

  async function apply(op) {
    if (!live(op) || op.applying) {
      return;
    }
    if (!canApply(op)) {
      if (op.mode === "manual") {
        commitManual(op);
        if (parseOffset(op.manualText).ok) {
          setStatus(op, S.ckNoChange, false);
        }
      }
      return;
    }
    const { scope, group } = op; // align() and reset() refuse to run while a change is in flight: this one is alone
    const tag = tagOf(group);
    const pair = op.mode === "pair";
    const body = pair ? pairBody(op.pair.anchor, op.pair.target, false) : { offset_ms: parseOffset(op.manualText).ms };
    abortDry(op);
    op.applying = true;
    setStatus(op, S.ckApplying, false);
    paint(op);
    ui.applyButton.focus();
    const { result, error } = await mutate(scope, group, () => api.clock.set(scope.caseId, group, body));
    op.applying = false;
    if (!here(scope)) {
      return; // another case (or the same one opened again) is open: this answer is not about it
    }
    if (error !== null || !acceptApplied(result, group)) {
      const text = error !== null ? errorText(error) : S.ckErrFailed;
      if (live(op)) {
        setStatus(op, text, true);
        paint(op);
      } else {
        onMessage(text, true);
      }
      if (error === null || uncertain(error)) {
        await reload(scope);
      }
      return;
    }
    if (pair) {
      picks.consume(op.pair.anchor, op.pair.target); // only what this operation used; newer picks stay
    }
    if (live(op)) {
      ui.shell.close(); // the focus goes back to the opener before the list is drawn again
    }
    onMessage(result.clock_offset_ms === 0 ? format(S.ckAppliedNone, { tag }) : format(S.ckApplied, { tag, offset: formatOffset(result.clock_offset_ms) }), false);
    await reload(scope);
  }

  // ---- reset --------------------------------------------------------------------------------------------------------

  async function reset(group, expected) {
    const scope = scopeNow();
    const item = collectionOf(group);
    if (stale(expected) || scope.caseId === null || scope.caseId === undefined || item === null) {
      return;
    }
    const tag = tagOf(group);
    if (mutating !== null || current !== null) {
      onMessage(S.ckBusy, true);
      return;
    }
    if (!item.clock_offset_ms) {
      onMessage(format(S.ckResetNone, { tag }), false);
      return;
    }
    const { result, error } = await mutate(scope, group, () => api.clock.reset(scope.caseId, group));
    if (!here(scope)) {
      return;
    }
    if (error !== null || !acceptApplied(result, group)) {
      onMessage(error !== null ? errorText(error) : S.ckErrFailed, true);
      if (error === null || uncertain(error)) {
        await reload(scope);
      }
      return;
    }
    onMessage(format(S.ckResetDone, { tag, offset: formatOffset(result.previous_ms) }), false);
    await reload(scope);
  }

  // ---- closing ------------------------------------------------------------------------------------------------------

  // The dialog closed (button, Escape, or this module): the operation ends, the elements go, align() promises resolve.
  function finishClose(op) {
    abortDry(op);
    const shell = ui !== null ? ui.shell : null;
    if (current === op) {
      current = null;
    }
    ui = null;
    if (shell !== null) {
      shell.dialog.remove();
    }
    const done = waiters;
    waiters = [];
    for (const resolve of done) {
      resolve();
    }
  }

  // The case changed: what was picked and the dialog belong to the old one. A change in flight is left to finish; its
  // answer is dropped by the case check.
  function forgetCase() {
    picks.clear();
    if (current !== null && ui !== null) {
      ui.shell.close();
    }
  }

  return {
    setAnchor,
    setTarget,
    align,
    reset,
    forgetCase,
    busy: () => mutating !== null,
    isOpen: () => current !== null,
    picked: () => ({ anchor: picks.anchor(scopeNow()), target: picks.target(scopeNow()) }),
  };
}
