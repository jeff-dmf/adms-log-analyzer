// "Extract" panel of the dock (ROADMAP 9.8, ARQUITETURA 10.11): an ad hoc regex with named groups becomes temporary
// columns over the records of the current filter. Nothing is saved in the case.
// The regex, the extracted values and the TOML text are untrusted (they come from what the user typed and from
// logs): only textContent, attribute values and class names are written.
import {
  CHECK_DEBOUNCE_MS, DEFAULT_LIMIT, DEFAULT_TOP, MAX_LIMIT, MAX_TOP, TARGETS, TYPES,
  canPromote, createCheckFlow, groupNames, createPromoteFlow, createRunFlow, errorContext, formatNumber, formatPct,
  hasScope, inputState, promoteIds, reconcileChoices, requestKey, typesPayload, usableLeaf, valueView, warningText,
} from "./extract-model.js";
import { createDialogShell, createPanelShell, setStatusLine, statusLine } from "./panel-shell.js";
import { S, format, pluralKey } from "./strings.js";

let uid = 0;

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) {
    node.className = className;
  }
  if (text !== undefined) {
    node.textContent = text;
  }
  return node;
}

function button(className, text) {
  const node = element("button", "ui-button " + className, text);
  node.type = "button";
  return node;
}

function field(labelText, control, className) {
  const wrap = element("div", "ext-field " + (className || ""));
  const label = element("label", "ext-label", labelText);
  control.id = control.id || "ext-" + String(++uid);
  label.htmlFor = control.id;
  wrap.append(label, control);
  return wrap;
}

// The one live region of a context (hidden, polite): verdicts are shown in static nodes and announced here once.
function liveRegion(className) {
  return statusLine(document, "visually-hidden " + className);
}

function announce(region, text, isError) {
  setStatusLine(region, "", false); // clearing first makes the same text read again
  setStatusLine(region, text, isError);
}

// Text of a node that is NOT a live region (verdicts and descriptions are read when the user reaches them).
function setText(node, text, isError) {
  node.textContent = text;
  node.classList.toggle("is-error", Boolean(isError));
  node.classList.toggle("ui-error", Boolean(isError));
}

function realDelay() {
  return new Promise((resolve) => setTimeout(resolve, CHECK_DEBOUNCE_MS));
}

// Options:
//   api                     { extract: { check, run, promote } } (api.js)
//   getCase()               the open case id or null
//   getFilter()             the ONE composed filter of the grid
//   onDrill(leaf)           apply the leaf (a filter chip) to the user filter of the grid
//   onRequestClose()        the dock closes the tab (the default hides the host)
//   delay()                 promise that settles when typing paused (default: a timer; tests settle their own)
//   copyText(text)          promise (default: navigator.clipboard.writeText)
// Returns { refresh(), reset(), closed(), focus(), setRegex(text) }:
//   refresh   the filter of the grid changed: mark the result as out of date (it is never recomputed by itself)
//   reset     the case changed: forget result, dialog and pending requests (the typed regex stays)
//   closed    the tab was closed: close the dialog (the typed regex and the result stay for the next opening)
export function mountExtractPanel(host, options) {
  const { api, getCase, getFilter, onDrill, onRequestClose = null, delay = realDelay } = options;
  const copyText = options.copyText || ((text) => (
    navigator.clipboard && navigator.clipboard.writeText
      ? navigator.clipboard.writeText(text)
      : Promise.reject(new Error("clipboard_unavailable"))));
  const shell = createPanelShell(host, { title: S.exTitle, onRequestClose });

  // ---- form (the tools row) -------------------------------------------------------------------------------------
  const form = element("div", "ext-form");
  const regexInput = element("input", "ext-regex");
  regexInput.type = "text";
  regexInput.id = "ext-regex-" + String(++uid);
  regexInput.setAttribute("spellcheck", "false");
  regexInput.setAttribute("autocomplete", "off");
  regexInput.placeholder = S.exRegexPlaceholder;
  const regexMessage = element("div", "ui-status ext-regex-msg"); // static: the live region of the panel is `status`
  regexMessage.id = "ext-regex-msg-" + String(++uid);
  regexInput.setAttribute("aria-describedby", regexMessage.id);
  const regexContext = element("div", "ext-context");
  regexContext.hidden = true;

  const targetSelect = element("select", "ext-target");
  for (const value of TARGETS) {
    const option = element("option", "", S.exTargets[value]);
    option.value = value;
    targetSelect.appendChild(option);
  }
  targetSelect.value = "msg";
  const limitInput = element("input", "ext-limit");
  limitInput.type = "number";
  limitInput.min = "1";
  limitInput.max = String(MAX_LIMIT);
  limitInput.value = String(DEFAULT_LIMIT);
  const topInput = element("input", "ext-top");
  topInput.type = "number";
  topInput.min = "1";
  topInput.max = String(MAX_TOP);
  topInput.value = String(DEFAULT_TOP);
  const limitMessage = element("div", "ui-status ext-field-msg ext-limit-msg");
  limitMessage.id = "ext-limit-msg-" + String(++uid);
  limitInput.setAttribute("aria-describedby", limitMessage.id);
  const topMessage = element("div", "ui-status ext-field-msg ext-top-msg");
  topMessage.id = "ext-top-msg-" + String(++uid);
  topInput.setAttribute("aria-describedby", topMessage.id);
  const typesRow = element("div", "ext-types");
  typesRow.hidden = true;
  const typesLabel = element("span", "ext-types-label", S.exTypesLabel);
  const typesList = element("span", "ext-types-list");
  typesRow.append(typesLabel, typesList);

  const regexField = field(S.exRegexLabel, regexInput, "ext-field-regex");
  const optionsRow = element("div", "ext-options");
  form.append(
    regexField, regexMessage, regexContext, optionsRow, limitMessage, topMessage, typesRow,
  );
  optionsRow.append(field(S.exTargetLabel, targetSelect), field(S.exLimitLabel, limitInput), field(S.exTopLabel, topInput));
  shell.addTools(form);

  // ---- body: status and result ----------------------------------------------------------------------------------
  const status = element("div", "ui-status ext-status"); // static: announced through `live`
  const live = liveRegion("ext-live"); // the one live region of the panel
  const staleNote = element("div", "ext-stale");
  staleNote.hidden = true;
  const resultHost = element("div", "ext-result");
  shell.body.append(status, live, staleNote, resultHost);

  // ---- foot -----------------------------------------------------------------------------------------------------
  const runButton = button("is-primary ext-run", S.exRun);
  const promoteButton = button("ext-promote", S.exPromote);
  shell.addFoot(runButton, promoteButton);

  // ---- state ----------------------------------------------------------------------------------------------------
  let choices = {}; // group name -> type chosen by the user
  let typeRowNames = [];
  // The request the shown result was computed for (the promotion uses the same regex and types): the run flow keeps
  // it with the data, so a cancelled run gives back both.
  const shownBody = () => run.state.body;
  let wantRun = false; // Enter pressed while the regex was still being checked

  const check = createCheckFlow({ api, delay, onState: onCheckState });
  const run = createRunFlow({ api, getCase, onState: onRunState });
  const promote = createPromoteFlow({ api, getCase, onState: onPromoteState });

  function typedRegex() {
    return regexInput.value;
  }

  function currentInputs() {
    return {
      caseId: getCase(),
      regex: typedRegex(),
      target: targetSelect.value === "text" ? "text" : "msg",
      types: typesPayload(groupNames(regexInput.value).map((name) => ({ name })), choices),
    };
  }

  function currentKey(inputs, limit, top) {
    return requestKey({ ...inputs, filter: getFilter(), limit, top });
  }

  // ---- live validation ------------------------------------------------------------------------------------------
  function scheduleCheck() {
    const inputs = currentInputs();
    return check.schedule({ regex: inputs.regex, target: inputs.target, types: inputs.types });
  }

  function rescueFocus() {
    const active = document.activeElement;
    if (active && active !== regexInput && shell.root.contains(active) && (active.disabled || active.hidden)) {
      regexInput.focus();
    }
  }

  function syncTypeRows(columns) {
    const names = columns.map((c) => c.name);
    if (names.length === typeRowNames.length && names.every((n, i) => n === typeRowNames[i])) {
      return; // same groups: the selects stay (a focused select must not be replaced while the user picks)
    }
    const active = document.activeElement;
    const lostFocus = Boolean(active) && typesList.contains(active);
    typesList.textContent = "";
    typeRowNames = names;
    choices = reconcileChoices(choices, columns);
    for (const column of columns) {
      const select = element("select", "ext-type");
      select.dataset.group = column.name;
      select.setAttribute("aria-label", format(S.exTypeFor, { name: column.name }));
      for (const type of TYPES) {
        const option = element("option", "", S.exTypeLabels[type]);
        option.value = type;
        select.appendChild(option);
      }
      select.value = choices[column.name] || "str";
      select.addEventListener("change", () => {
        choices[column.name] = select.value;
        wantRun = false;
        scheduleCheck();
        updateControls();
      });
      const item = element("span", "ext-type-item");
      item.append(element("span", "ext-type-name", column.name), select);
      typesList.appendChild(item);
    }
    typesRow.hidden = columns.length === 0;
    if (lostFocus) {
      regexInput.focus();
    }
  }

  function setSelectValue(name, type) {
    for (const select of typesList.querySelectorAll("select")) {
      if (select.dataset.group === name) {
        select.value = type;
      }
    }
  }

  function renderCheck() {
    const state = check.state;
    regexContext.hidden = true;
    regexContext.textContent = "";
    regexInput.setAttribute("aria-invalid", state.status === "error" ? "true" : "false");
    if (state.status === "error") {
      setText(regexMessage, state.error.text || S.exBadRegexShort, true);
      const context = state.error.problem === "regex" ? errorContext(typedRegex(), state.error.offset) : null;
      if (context) {
        regexContext.hidden = false;
        regexContext.textContent = context.at === ""
          ? format(S.exErrorAtEnd, { before: context.before })
          : format(S.exErrorNear, context);
      }
      return;
    }
    if (state.status === "ok" && state.result) {
      const columns = Array.isArray(state.result.columns) ? state.result.columns : [];
      syncTypeRows(columns);
      setText(regexMessage, format(S[pluralKey("exCheckOk", columns.length)], {
        count: columns.length,
        window: formatNumber(state.result.window),
      }), false);
      return;
    }
    setText(regexMessage, state.status === "checking" ? S.exChecking : "", false);
  }

  function onCheckState(state) {
    renderCheck();
    if (state.status === "ok" || state.status === "error") {
      announce(live, regexMessage.textContent, state.status === "error");
    }
    if (state.status === "idle" && typedRegex() === "") {
      syncTypeRows([]);
    }
    if (wantRun && state.status === "ok") {
      wantRun = false;
      startRun();
    } else if (wantRun && state.status !== "checking") {
      wantRun = false;
    }
    updateControls();
  }

  // ---- run ------------------------------------------------------------------------------------------------------
  function startRun() {
    const inputs = currentInputs();
    const verdict = inputState({
      caseId: inputs.caseId, regex: inputs.regex, check: check.state, limit: limitInput.value, top: topInput.value,
    });
    if (!verdict.ok) {
      updateControls();
      return Promise.resolve();
    }
    const body = {
      filter: getFilter() || null,
      regex: inputs.regex,
      target: inputs.target,
      types: inputs.types,
      limit: verdict.limit,
      top: verdict.top,
    };
    return run.run(body, requestKey({ ...inputs, filter: body.filter, limit: body.limit, top: body.top }));
  }

  function appendStats(column, section) {
    if (column.stats.length === 0) {
      return;
    }
    const list = element("dl", "ext-stats");
    for (const stat of column.stats) {
      const item = element("div", "ext-stat");
      item.append(element("dt", "", S.exStatLabels[stat.key]), element("dd", "", stat.value));
      list.appendChild(item);
    }
    section.appendChild(list);
  }

  let onlyButtons = []; // { node, usable, label } of the shown result: enabled while the grid filter has a scope

  function updateOnly() {
    const scoped = hasScope(getFilter());
    for (const item of onlyButtons) {
      item.node.disabled = !item.usable.ok || !scoped;
      item.node.title = !item.usable.ok ? S[item.usable.reason] : scoped ? "" : S.exNeedsScope;
    }
  }

  function appendValues(column, section) {
    if (column.values.length === 0) {
      return;
    }
    const table = element("table", "ui-table is-wrap ext-table");
    table.appendChild(element("caption", "visually-hidden", format(S.exTableCaption, { name: column.name })));
    const headRow = element("tr");
    for (const [text, cls] of [[S.exColValue, ""], [S.exColCount, "ext-num"], [S.exColShare, "ext-num"], [S.exColFilter, ""]]) {
      const th = element("th", cls, text);
      th.scope = "col";
      headRow.appendChild(th);
    }
    table.appendChild(element("thead")).appendChild(headRow);
    const body = element("tbody");
    for (const item of column.values) {
      const view = valueView(item.value, S, column.type);
      const tr = element("tr");
      const valueCell = element("td", "ext-value is-" + view.kind + (view.inexact ? " is-inexact" : ""), view.text);
      if (view.inexact) {
        valueCell.title = S.exInexact;
      }
      tr.appendChild(valueCell);
      tr.appendChild(element("td", "ext-num", formatNumber(item.count)));
      tr.appendChild(element("td", "ext-num", formatPct(item.pct)));
      const cell = element("td", "ext-actions");
      const only = button("is-ghost ext-include", S.exFilterOnly);
      only.setAttribute("aria-label", format(S.exFilterOnlyLabel, { name: column.name, value: view.text }));
      const usable = usableLeaf(item.leaf);
      onlyButtons.push({ node: only, usable });
      only.addEventListener("click", () => {
        if (usable.ok && hasScope(getFilter())) {
          onDrill(item.leaf);
        }
      });
      cell.appendChild(only);
      tr.appendChild(cell);
      body.appendChild(tr);
    }
    table.appendChild(body);
    section.appendChild(table);
    if (column.othersCount > 0) {
      section.appendChild(element("div", "ext-others", format(S.exOthers, {
        count: formatNumber(column.othersCount), groups: formatNumber(column.othersGroups),
      })));
    }
  }

  // The error belongs to the request that failed: when the inputs changed since, it says so.
  function renderRunError() {
    const state = run.state;
    const inputs = currentInputs();
    const current = state.errorKey === currentKey(inputs, Number(limitInput.value), Number(topInput.value));
    const text = format(S.exError, { error: state.error.text || S.exErrorGeneric });
    setText(status, current ? text : format(S.exErrorOld, { error: text }), true);
  }

  function renderResult() {
    const state = run.state;
    resultHost.textContent = "";
    onlyButtons = [];
    if (state.status === "loading") {
      setText(status, S.exRunning, false);
      return;
    }
    if (state.status === "error") {
      renderRunError();
      return;
    }
    if (state.status !== "done") {
      setText(status, getCase() ? S.exIdle : S.exNoCase, false);
      return;
    }
    const data = state.data;
    setText(status, format(S.exSummary, {
      matched: formatNumber(data.matched), scanned: formatNumber(data.scanned),
    }), false);
    const notes = element("ul", "ext-notes");
    if (data.cut > 0) {
      notes.appendChild(element("li", "ext-note", format(S[pluralKey("exCut", data.cut)], {
        count: formatNumber(data.cut), window: formatNumber(data.window),
      })));
    }
    if (data.truncated) {
      notes.appendChild(element("li", "ext-note", format(S.exTruncated, {
        limit: formatNumber(shownBody() ? shownBody().limit : 0),
      })));
    }
    if (notes.children.length > 0) {
      resultHost.appendChild(notes);
    }
    if (data.matched === 0) {
      resultHost.appendChild(element("div", "ext-empty", S.exNoMatches));
    }
    for (const column of data.columns) {
      const section = element("section", "ext-column");
      section.setAttribute("aria-label", format(S.exColumnLabel, { name: column.name }));
      const head = element("div", "ext-column-head");
      head.append(
        element("h3", "ext-column-name ui-section-title", column.name),
        element("span", "ext-column-type", S.exTypeLabels[column.type]),
        element("span", "ext-column-meta", format(S.exColumnMeta, {
          present: formatNumber(column.present), distinct: formatNumber(column.distinct),
        })),
      );
      section.appendChild(head);
      if (column.suggested !== column.type) {
        const suggest = element("div", "ext-suggest");
        const useIt = button("is-ghost ext-use-suggested", format(S.exUseSuggested, { type: S.exTypeLabels[column.suggested] }));
        useIt.setAttribute("aria-label", format(S.exUseSuggestedLabel, {
          type: S.exTypeLabels[column.suggested], name: column.name,
        }));
        useIt.addEventListener("click", () => {
          choices[column.name] = column.suggested;
          setSelectValue(column.name, column.suggested);
          wantRun = false;
          scheduleCheck();
          updateControls();
        });
        suggest.append(element("span", "ext-suggest-text", format(S.exSuggested, { type: S.exTypeLabels[column.suggested] })), useIt);
        section.appendChild(suggest);
      }
      appendStats(column, section);
      appendValues(column, section);
      resultHost.appendChild(section);
    }
  }

  function onRunState() {
    renderResult();
    if (run.state.status !== "idle") {
      announce(live, status.textContent, run.state.status === "error");
    }
    updateControls();
    rescueFocus();
  }

  // ---- controls -------------------------------------------------------------------------------------------------
  function updateStale() {
    const state = run.state;
    let stale = false;
    if (state.status === "done") {
      const inputs = currentInputs();
      const limit = Number(limitInput.value);
      const top = Number(topInput.value);
      stale = state.key !== currentKey(inputs, limit, top);
    }
    staleNote.hidden = !stale;
    staleNote.textContent = stale ? S.exStale : "";
    if (state.status === "error") {
      renderRunError();
    }
  }

  function updateControls() {
    const inputs = currentInputs();
    const verdict = inputState({
      caseId: inputs.caseId, regex: inputs.regex, check: check.state, limit: limitInput.value, top: topInput.value,
    });
    runButton.disabled = !verdict.ok;
    runButton.title = verdict.ok ? "" : S[verdict.problem];
    limitInput.setAttribute("aria-invalid", verdict.limit === null ? "true" : "false");
    topInput.setAttribute("aria-invalid", verdict.top === null ? "true" : "false");
    setText(limitMessage, verdict.limit === null ? S.exBadLimit : "", true);
    setText(topMessage, verdict.top === null ? S.exBadTop : "", true);
    limitMessage.hidden = verdict.limit !== null;
    topMessage.hidden = verdict.top !== null;
    updateOnly();
    runButton.textContent = run.state.status === "loading" ? S.exRunning : S.exRun;
    const done = run.state.status === "done" ? run.state.data : null;
    const can = canPromote(done, shownBody() ? shownBody().target : null);
    promoteButton.disabled = !can;
    promoteButton.title = can ? "" : S[done && shownBody() && shownBody().target !== "msg" ? "exPromoteNeedsMsg" : "exPromoteNoRecords"];
    updateStale();
    rescueFocus();
  }

  // ---- promote dialog -------------------------------------------------------------------------------------------
  let dialog = null;
  const dialogParts = {};

  function ensureDialog() {
    if (dialog !== null) {
      return dialog;
    }
    dialog = createDialogShell(document, {
      title: S.exPromoteTitle,
      className: "ext-dialog",
      onClose: () => {
        promote.cancel();
      },
    });
    const intro = element("p", "ext-dialog-intro", S.exPromoteIntro);
    const familyInput = element("input", "ext-family");
    familyInput.type = "text";
    familyInput.value = "";
    familyInput.placeholder = S.exFamilyHint;
    const codeInput = element("input", "ext-code");
    codeInput.type = "text";
    codeInput.value = "";
    codeInput.placeholder = S.exCodeHint;
    for (const input of [familyInput, codeInput]) {
      input.setAttribute("autocomplete", "off");
      input.setAttribute("spellcheck", "false");
      input.addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
          event.preventDefault();
          generate();
        }
      });
      // The text shown (or being built) is for the values it was generated with: editing them drops it.
      input.addEventListener("input", () => {
        if (promote.state.status !== "idle") {
          promote.cancel();
          say(S.exPromoteEdited, false);
        }
      });
    }
    const fields = element("div", "ext-dialog-fields");
    fields.append(field(S.exFamilyLabel, familyInput), field(S.exCodeLabel, codeInput));
    const dialogStatus = element("div", "ui-status ext-dialog-status"); // static: announced through dialogLive
    const dialogLive = liveRegion("ext-dialog-live"); // the one live region of the dialog
    const checkLine = element("div", "ui-status ext-dialog-check");
    const warnings = element("ul", "ext-warnings");
    warnings.setAttribute("aria-label", S.exWarningsLabel);
    const fileLine = element("div", "ext-file");
    const toml = element("textarea", "ext-toml");
    toml.readOnly = true;
    toml.value = "";
    toml.rows = 12;
    toml.setAttribute("spellcheck", "false");
    toml.setAttribute("aria-label", S.exTomlLabel);
    dialog.body.append(intro, fields, dialogStatus, dialogLive, checkLine, warnings, fileLine, toml);
    const generateButton = button("is-primary ext-generate", S.exGenerate);
    const copyButton = button("ext-copy", S.exCopy);
    generateButton.addEventListener("click", generate);
    copyButton.addEventListener("click", copy);
    dialog.foot.append(generateButton, copyButton);
    Object.assign(dialogParts, {
      familyInput, codeInput, dialogStatus, dialogLive, checkLine, warnings, fileLine, toml, generateButton, copyButton,
    });
    document.body.appendChild(dialog.dialog);
    return dialog;
  }

  function generate() {
    const ids = promoteIds(run.state.data);
    const shown = shownBody();
    if (!shown || ids.length === 0) {
      return Promise.resolve();
    }
    const body = { ids, regex: shown.regex, target: "msg", types: shown.types };
    const family = String(dialogParts.familyInput.value || "").trim();
    const code = String(dialogParts.codeInput.value || "").trim();
    if (family !== "") {
      body.family = family;
    }
    if (code !== "") {
      body.code = code;
    }
    return promote.load(body);
  }

  // Any change of what the dialog shows (a request starts, ends, is cancelled; the dialog closes) bumps the session:
  // a copy that finishes after that belongs to a text no longer shown and touches nothing.
  let session = 0;

  // A message of the dialog: shown in its static status and announced once.
  function say(text, isError) {
    setText(dialogParts.dialogStatus, text, isError);
    announce(dialogParts.dialogLive, text, isError);
  }

  function copy() {
    const text = String(dialogParts.toml.value || "");
    if (text === "") {
      return Promise.resolve();
    }
    session += 1;
    const mine = session;
    return Promise.resolve().then(() => copyText(text)).then(
      () => {
        if (mine === session) {
          say(S.exCopied, false);
        }
      },
      () => {
        if (mine !== session) {
          return;
        }
        dialogParts.toml.focus();
        dialogParts.toml.select();
        say(S.exCopyFailed, true);
      },
    );
  }

  function onPromoteState(state) {
    if (dialog === null) {
      return;
    }
    const parts = dialogParts;
    session += 1;
    parts.warnings.textContent = "";
    parts.generateButton.disabled = state.status === "loading";
    parts.copyButton.disabled = state.status !== "done" || state.promote.toml === "";
    if (state.status === "loading") {
      say(S.exPromoteLoading, false);
      return;
    }
    if (state.status === "error") {
      parts.toml.value = "";
      parts.fileLine.textContent = "";
      setText(parts.checkLine, "", false);
      say(format(S.exError, { error: state.error.text || S.exErrorGeneric }), true);
      return;
    }
    if (state.status !== "done") {
      parts.toml.value = "";
      parts.fileLine.textContent = "";
      setText(parts.checkLine, "", false);
      setText(parts.dialogStatus, "", false);
      return;
    }
    const result = state.promote;
    parts.toml.value = result.toml;
    parts.fileLine.textContent = result.file === "" ? "" : format(S.exPromoteFile, { file: result.file });
    setText(parts.dialogStatus, "", false);
    const verdict = result.loads ? S.exCheckLoads : format(S.exCheckFails, { error: result.checkError || S.exErrorGeneric });
    setText(parts.checkLine, verdict, !result.loads);
    announce(parts.dialogLive, verdict, !result.loads);
    for (const warning of result.warnings) {
      parts.warnings.appendChild(element("li", "ext-warning", warningText(warning.code, S)));
    }
  }

  function openPromote() {
    if (promoteButton.disabled) {
      return;
    }
    const opener = promoteButton; // taken before the dialog moves the focus
    const shown = ensureDialog();
    if (shown.isOpen()) {
      return;
    }
    shown.open(opener);
    generate();
  }

  // ---- events ---------------------------------------------------------------------------------------------------
  regexInput.addEventListener("input", () => {
    wantRun = false;
    scheduleCheck();
    updateControls();
  });
  regexInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      if (runButton.disabled && check.state.status !== "checking") {
        return;
      }
      if (check.state.status === "checking") {
        wantRun = true;
      } else {
        startRun();
      }
    }
  });
  targetSelect.addEventListener("change", () => {
    wantRun = false;
    scheduleCheck();
    updateControls();
  });
  for (const input of [limitInput, topInput]) {
    input.addEventListener("input", updateControls);
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        if (!runButton.disabled) {
          startRun();
        }
      }
    });
  }
  runButton.addEventListener("click", () => {
    if (!runButton.disabled) {
      startRun();
    }
  });
  promoteButton.addEventListener("click", openPromote);

  renderResult();
  renderCheck();
  updateControls();

  return {
    refresh() {
      // Reopened (or the filter changed): a verdict dropped while the tab was closed is asked again.
      if (typedRegex() !== "" && check.state.status === "idle") {
        scheduleCheck();
      }
      updateControls();
    },
    reset() {
      wantRun = false;
      run.cancel();
      promote.cancel();
      if (dialog !== null && dialog.isOpen()) {
        dialog.close();
      }
      renderResult();
      updateControls();
    },
    // Closing the tab drops what is in flight and the wish to run; what finished stays for the next opening.
    closed() {
      wantRun = false;
      check.cancel();
      run.cancelPending();
      promote.cancel();
      if (dialog !== null && dialog.isOpen()) {
        dialog.close();
      }
    },
    focus() {
      regexInput.focus();
    },
    setRegex(text) {
      regexInput.value = String(text);
      wantRun = false;
      scheduleCheck();
      updateControls();
    },
  };
}
