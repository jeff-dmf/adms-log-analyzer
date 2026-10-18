// "Compare views" panel of the dock (ROADMAP 9.7, ARQUITETURA 10.10): two sides of the open case, compared by the
// sequence of their message types, shown aligned in two columns. The rules, the request and the one-operation-at-a-time
// flow are in compare-views-model.js; this file only draws.
// Everything that comes from the log (message text, type labels, operation and process names, saved view names) is
// written with textContent, attribute values and class names. Nothing here is built from HTML.
import {
  CURRENT, DEFAULT_MAX_SIDE, MAX_SIDE_CHOICES, MODES, createCompareFlow, createListsFlow, errorText, moveCell,
  nextDifference, nextMaxSide, operationOptions, processOptions, viewOptions,
} from "./compare-views-model.js";
import { createPanelShell, setStatusLine, statusLine } from "./panel-shell.js";
import { S, countNoun, format } from "./strings.js";

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

// True when `node` is inside `root` and nothing between them is hidden (a hidden or removed element loses the focus).
function within(root, node) {
  if (!node || !root.contains(node)) {
    return false;
  }
  for (let n = node; n && n !== root; n = n.parentNode || null) {
    if (n.hidden) {
      return false;
    }
  }
  return !root.hidden;
}

function headRow(labels) {
  const tr = element("tr");
  for (const label of labels) {
    const th = element("th", "", label);
    th.scope = "col";
    tr.appendChild(th);
  }
  const thead = element("thead");
  thead.appendChild(tr);
  return thead;
}

function number(n) {
  return n.toLocaleString("en-US");
}

const SIDE_NAMES = { a: "A", b: "B" };

// Options:
//   api                   { compareViews: { run }, views: { list }, events }  (see compare-views-model.js)
//   getCase()             the open case id or null
//   getFilter()           the ONE composed filter of the grid (the current view)
//   onReveal(row)         "Show in grid": row = { id, ts }
//   onRequestClose()      the dock closes the tab (the default hides the host)
// Returns { refresh(), reset(), closed(), focus() }:
//   refresh   the view of the grid changed: a result read for the old one says so; it is never recomputed by itself
//   reset     the case changed: forget everything of the old case
//   closed    the tab was closed: cancel what is pending (the form and the result stay for the next opening)
export function mountCompareViewsPanel(host, options) {
  const { api, getCase, getFilter, onReveal, onRequestClose = null } = options;
  uid += 1;
  const prefix = "cv" + uid + "-";
  const shell = createPanelShell(host, {
    title: S.cvTitle,
    onRequestClose,
    onRefresh: () => lists.load(modeNow()),
    refreshLabel: S.cvRefreshLists,
  });
  const listNameOf = { filter: "views", window: null, operation: "operation", process: "process" };

  // ---- tools: mode, limit, only differences
  const tools = element("div", "cv-toolbar");
  function selectWith(id, labelText, values) {
    const label = element("label", "", labelText);
    const select = element("select");
    select.id = prefix + id;
    label.htmlFor = select.id;
    for (const [value, text] of values) {
      const option = element("option", "", text);
      option.value = value;
      select.appendChild(option);
    }
    tools.append(label, select);
    return select;
  }
  const modeSelect = selectWith("mode", S.cvMode, MODES.map((m) => [m, S.cvModes[m]]));
  const maxSelect = selectWith("max", S.cvMaxSide, MAX_SIDE_CHOICES.map((n) => [String(n), number(n)]));
  maxSelect.value = String(DEFAULT_MAX_SIDE);
  const diffLabel = element("label", "cv-check");
  const diffBox = element("input");
  diffBox.type = "checkbox";
  diffBox.checked = false;
  diffLabel.append(diffBox, element("span", "", S.cvOnlyDiff));
  tools.appendChild(diffLabel);
  shell.addTools(tools);

  // ---- sides
  const sidesBox = element("div", "cv-sides");
  const sides = {};
  function textField(group, id, labelText, placeholder) {
    const wrap = element("div", "cv-field");
    const label = element("label", "", labelText);
    const input = element("input");
    input.type = "text";
    input.id = prefix + id;
    input.placeholder = placeholder;
    input.autocomplete = "off";
    label.htmlFor = input.id;
    wrap.append(label, input);
    group.appendChild(wrap);
    return input;
  }
  function pickField(group, id, labelText) {
    const wrap = element("div", "cv-field");
    const label = element("label", "", labelText);
    const select = element("select");
    select.id = prefix + id;
    label.htmlFor = select.id;
    wrap.append(label, select);
    group.appendChild(wrap);
    return select;
  }
  for (const name of ["a", "b"]) {
    const box = element("fieldset", "cv-side cv-side-" + name);
    box.appendChild(element("legend", "", format(S.cvSide, { side: SIDE_NAMES[name] })));
    const groups = {};
    for (const mode of MODES) {
      groups[mode] = element("div", "cv-group cv-group-" + mode);
      box.appendChild(groups[mode]);
    }
    const side = {
      box,
      groups,
      filter: pickField(groups.filter, name + "-view", S.cvView),
      from: textField(groups.window, name + "-from", S.cvFrom, S.cvTimePlaceholder),
      to: textField(groups.window, name + "-to", S.cvTo, S.cvTimePlaceholder),
      operation: pickField(groups.operation, name + "-op", S.cvOperation),
      process: pickField(groups.process, name + "-proc", S.cvProcess),
      pfrom: textField(groups.process, name + "-pfrom", S.cvFromOptional, S.cvTimePlaceholder),
      pto: textField(groups.process, name + "-pto", S.cvToOptional, S.cvTimePlaceholder),
      error: element("div", "ui-error cv-side-error"),
      touched: false,
      options: { filter: [], operation: [], process: [] },
    };
    side.error.id = prefix + name + "-error";
    side.error.hidden = true;
    box.appendChild(side.error);
    side.filter.addEventListener("change", () => { side.touched = true; });
    sides[name] = side;
    sidesBox.appendChild(box);
  }

  // ---- body: the one live region, notes, results
  const status = statusLine(document, "cv-status");
  const staleNote = element("div", "ui-error cv-stale");
  staleNote.hidden = true;
  const results = element("section", "cv-results");
  results.setAttribute("aria-label", S.cvResults);
  results.hidden = true;
  const emptyHelp = element("p", "ev-muted cv-help", S.cvHelp);
  shell.body.append(sidesBox, status, staleNote, results, emptyHelp);

  // The foot: the main action and, while a comparison is running, Cancel.
  const runButton = element("button", "ui-button is-primary cv-run", S.cvCompare);
  runButton.type = "button";
  const cancelButton = element("button", "ui-button cv-cancel", S.cvCancel);
  cancelButton.type = "button";
  cancelButton.hidden = true;
  shell.addFoot(runButton, cancelButton);
  shell.foot.hidden = false;

  // ---- results
  const summaryBox = element("div", "cv-summary");
  const notesList = element("ul", "cv-notes");
  const raiseButton = element("button", "ui-button cv-raise", "");
  raiseButton.type = "button";
  raiseButton.hidden = true;
  const typesHead = element("h3", "", S.cvTypesTitle);
  const typesNote = element("div", "ev-muted cv-note");
  const typesTable = element("table", "ui-table cv-types");
  typesTable.appendChild(element("caption", "visually-hidden", S.cvTypesCaption));
  typesTable.appendChild(headRow([S.cvColType, S.cvColA, S.cvColB, S.cvColChange]));
  const typesBody = element("tbody");
  typesTable.appendChild(typesBody);
  const rowsHead = element("h3", "", S.cvRowsTitle);
  const rowsTools = element("div", "cv-rows-tools");
  const prevDiff = element("button", "ui-button is-ghost cv-prev", S.cvPrevDiff);
  const nextDiff = element("button", "ui-button is-ghost cv-next", S.cvNextDiff);
  prevDiff.type = "button";
  nextDiff.type = "button";
  const rowsCount = element("span", "ev-muted cv-rows-count");
  rowsTools.append(prevDiff, nextDiff, rowsCount);
  const rowsHint = element("div", "ev-muted cv-note", S.cvKeysHint);
  const rowsTable = element("table", "ui-table cv-rows");
  rowsTable.setAttribute("role", "grid");
  rowsTable.setAttribute("aria-label", S.cvRowsCaption);
  rowsTable.appendChild(headRow([S.cvColDiff, format(S.cvSide, { side: "A" }), format(S.cvSide, { side: "B" })]));
  const rowsBody = element("tbody");
  rowsTable.appendChild(rowsBody);
  const moreNote = element("div", "ui-error cv-more-error");
  moreNote.hidden = true;
  const moreButton = element("button", "ui-button cv-more", S.cvLoadMore);
  moreButton.type = "button";
  moreButton.hidden = true;
  results.append(summaryBox, notesList, raiseButton, typesHead, typesNote, typesTable, rowsHead, rowsTools, rowsHint, rowsTable, moreNote, moreButton);

  // ---- the aligned rows: the buttons are kept in our own arrays (rows of [A, B]); one of them has tabindex 0
  const cells = [];
  let rendered = 0;
  let renderedResult = null;
  let active = { row: 0, col: 0 };

  function buttonAt(row, col) {
    const pair = cells[row];
    return pair ? pair[col] || pair[1 - col] || null : null;
  }

  function setActive(row, col, focus) {
    const previous = buttonAt(active.row, active.col);
    const next = buttonAt(row, col);
    if (!next) {
      return;
    }
    if (previous && previous !== next) {
      previous.setAttribute("tabindex", "-1");
    }
    next.setAttribute("tabindex", "0");
    active = { row, col: next === cells[row][col] ? col : 1 - col };
    if (focus) {
      next.focus();
    }
  }

  function appendRow(row) {
    const tr = element("tr", "cv-row cv-op-" + row.op);
    const opCell = element("td", "cv-op");
    opCell.appendChild(element("span", "cv-op-label", row.opLabel));
    tr.appendChild(opCell);
    const pair = [null, null];
    ["a", "b"].forEach((name, col) => {
      const td = element("td", "cv-cell cv-cell-" + name);
      const data = row[name];
      if (data === null) {
        td.appendChild(element("span", "visually-hidden", format(S.cvNoRecord, { side: SIDE_NAMES[name] })));
        td.appendChild(element("span", "cv-none", S.cvNoneMark));
      } else {
        const open = element("button", "cv-open");
        open.type = "button";
        open.dataset.row = String(row.key);
        open.dataset.col = String(col);
        open.setAttribute("tabindex", cells.length === 0 && col === (row.a === null ? 1 : 0) ? "0" : "-1");
        open.setAttribute("aria-label", format(S.cvOpenLabel, { side: SIDE_NAMES[name], type: data.type, time: data.time }));
        open.title = S.cvShowInGrid;
        open.append(element("span", "cv-cell-type", data.type));
        if (data.time) {
          open.append(element("span", "cv-cell-time ev-muted", data.time));
        }
        if (data.text) {
          open.append(element("span", "cv-cell-text", data.text));
        }
        td.appendChild(open);
        pair[col] = open;
      }
      tr.appendChild(td);
    });
    cells.push(pair);
    rowsBody.appendChild(tr);
  }

  // ---- state -> DOM
  const flow = createCompareFlow({ api, getCase, onState: onFlowState });
  const lists = createListsFlow({ api, getCase, onChange: onListChange });
  const modeNow = () => (MODES.includes(modeSelect.value) ? modeSelect.value : "filter");

  function say(text, isError) {
    // Cleared first, so the same text announced twice is read twice.
    setStatusLine(status, "", false);
    setStatusLine(status, text, isError);
  }

  function summaryRows(result) {
    summaryBox.textContent = "";
    for (const name of ["a", "b"]) {
      const side = result[name];
      const card = element("dl", "cv-card");
      card.appendChild(element("dt", "cv-card-title", format(S.cvSide, { side: SIDE_NAMES[name] })));
      card.appendChild(element("dd", "cv-card-desc", side.description));
      card.appendChild(element("dd", "", side.countText + (side.truncated ? " (" + S.cvCut + ")" : "")));
      if (side.span) {
        card.appendChild(element("dd", "ev-muted", side.span));
      }
      summaryBox.appendChild(card);
    }
    const stats = result.stats;
    const list = element("dl", "cv-stats");
    const entries = [
      [S.cvStatSame, number(stats.same)],
      [S.cvStatOnlyA, number(stats.onlyA)],
      [S.cvStatOnlyB, number(stats.onlyB)],
      [S.cvStatChanged, format(S.cvChangedText, { a: number(stats.changedA), b: number(stats.changedB) })],
      [S.cvStatSimilarity, stats.similarity + "%"],
    ];
    for (const [term, value] of entries) {
      list.append(element("dt", "", term), element("dd", "", value));
    }
    summaryBox.appendChild(list);
  }

  function typesRows(result) {
    typesBody.textContent = "";
    for (const t of result.templates) {
      const tr = element("tr", "cv-type" + (t.delta === 0 ? "" : " is-diff"));
      const label = element("td", "is-text");
      label.appendChild(element("span", "cv-type-label", t.label));
      if (t.unknown) {
        label.appendChild(element("span", "ev-muted cv-unknown", " " + S.cvUnknownMark));
      }
      tr.append(label, element("td", "is-num", number(t.a)), element("td", "is-num", number(t.b)), element("td", "is-num", t.deltaText));
      typesBody.appendChild(tr);
    }
    typesNote.textContent = result.templatesTotal > result.templates.length
      ? format(S.cvTypesMore, { shown: number(result.templates.length), total: number(result.templatesTotal) })
      : "";
    typesTable.hidden = result.templates.length === 0;
  }

  function renderResult(state) {
    const result = state.result;
    results.hidden = result === null;
    emptyHelp.hidden = result !== null || state.status === "loading";
    if (result === null) {
      renderedResult = null;
      rendered = 0;
      cells.length = 0;
      rowsBody.textContent = "";
      return;
    }
    if (renderedResult !== result) {
      renderedResult = result;
      summaryRows(result);
      typesRows(result);
      notesList.textContent = "";
      for (const text of result.notes) {
        notesList.appendChild(element("li", "", text));
      }
      notesList.hidden = result.notes.length === 0;
      rowsBody.textContent = "";
      cells.length = 0;
      rendered = 0;
      active = { row: 0, col: 0 };
    }
    for (let i = rendered; i < state.rows.length; i += 1) {
      appendRow(state.rows[i]);
    }
    rendered = state.rows.length;
    rowsTable.hidden = state.rows.length === 0;
    rowsTools.hidden = state.rows.length === 0;
    rowsHint.hidden = state.rows.length === 0;
    rowsCount.textContent = format(S.cvRowsCount, { shown: number(state.rows.length), total: number(result.totalRows) });
    const room = state.status === "too_large" ? nextMaxSide(state.body.max_side) : null;
    raiseButton.hidden = room === null;
    if (room !== null) {
      raiseButton.dataset.max = String(room);
      raiseButton.textContent = format(S.cvRaise, { max: number(room) });
    }
    const more = state.nextOffset !== null && !state.changed;
    moreButton.hidden = !more && !state.moreError;
    moreButton.setAttribute("aria-disabled", state.loadingMore ? "true" : "false");
    moreButton.textContent = state.loadingMore ? S.cvLoadingMore : state.moreError ? S.cvRetry : S.cvLoadMore;
    moreNote.hidden = !state.moreError && !state.changed;
    moreNote.textContent = state.changed ? S.cvChanged : state.moreError ? S.cvMoreError + ": " + errorText(state.moreError) : "";
  }

  function sideErrors(errors) {
    for (const name of ["a", "b"]) {
      const side = sides[name];
      const mine = errors.filter((e) => e.side === name);
      side.error.hidden = mine.length === 0;
      side.error.textContent = mine.map((e) => S.cvInvalid[e.key] || S.cvInvalid.badMode).join(" ");
      if (mine.length > 0) {
        side.box.setAttribute("aria-describedby", side.error.id);
      } else {
        side.box.removeAttribute("aria-describedby");
      }
    }
  }

  function statusText(state) {
    if (state.errors.length > 0 && state.status !== "loading") {
      return { text: S.cvFixSides, error: true };
    }
    if (state.status === "loading") {
      return { text: S.cvLoading, error: false };
    }
    if (state.status === "error") {
      return { text: S.cvError + ": " + errorText(state.error), error: true };
    }
    if (state.result !== null) {
      const result = state.result;
      let text = format(S.cvDone, { a: result.a.countText, b: result.b.countText, pct: result.stats.similarity });
      if (state.status === "too_large") {
        text += ". " + S.cvTooLargeShort;
      }
      if (state.loadingMore) {
        text = S.cvLoadingMore;
      } else if (state.moreError) {
        return { text: S.cvMoreError + ": " + errorText(state.moreError), error: true };
      } else if (state.changed) {
        return { text: S.cvChanged, error: true };
      }
      return { text, error: false };
    }
    if (state.cancelled) {
      return { text: S.cvCancelled, error: false };
    }
    if (state.stale) {
      return { text: S.cvViewChangedRun, error: false };
    }
    return { text: getCase() ? S.cvIdle : S.cvNoCase, error: false };
  }

  let lastStatus = "";
  function onFlowState(state) {
    // The focus sits on something this render removes (a button of the results): the main action takes it.
    const hadFocus = results.contains(document.activeElement);
    const moreHadFocus = document.activeElement === moreButton;
    const before = rendered;
    renderResult(state);
    sideErrors(state.errors);
    const loading = state.status === "loading";
    cancelButton.hidden = !(loading || state.loadingMore);
    staleNote.hidden = !state.stale;
    staleNote.textContent = state.stale ? S.cvViewChanged : "";
    runButton.setAttribute("aria-disabled", "false");
    if (hadFocus && !within(results, document.activeElement)) {
      runButton.focus();
    }
    if (moreHadFocus && moreButton.hidden && before < rendered && cells[before]) {
      setActive(before, 0, true); // the end of the list: the focus goes on to the first row that was just added
    }
    const line = statusText(state);
    const key = (line.error ? "e:" : "i:") + line.text;
    if (key !== lastStatus) {
      lastStatus = key;
      say(line.text, line.error);
    }
  }

  // ---- the pickers
  function fill(select, choices, placeholder, wanted) {
    const keep = wanted !== undefined ? wanted : select.value;
    select.textContent = "";
    const entries = placeholder === null ? choices : [{ value: "", label: placeholder }, ...choices];
    for (const choice of entries) {
      const option = element("option", "", choice.label);
      option.value = choice.value;
      select.appendChild(option);
    }
    select.value = entries.some((c) => c.value === keep) ? keep : entries[0].value;
  }

  function listPlaceholder(name, kind) {
    const list = lists.lists[name];
    if (list.status === "loading") {
      return S.cvListLoading;
    }
    if (list.status === "error") {
      return S.cvListError;
    }
    if (list.status === "ready" && list.items.length === 0) {
      return kind === "operation" ? S.cvNoOperations : S.cvNoProcesses;
    }
    return kind === "operation" ? S.cvChooseOperation : S.cvChooseProcess;
  }

  function paintLists(name) {
    for (const key of ["a", "b"]) {
      const side = sides[key];
      if (name === null || name === "views") {
        const choices = viewOptions(lists.lists.views.items);
        side.options.filter = choices;
        // B starts on the first saved view (A is the current view) until the user chooses.
        const wanted = key === "b" && !side.touched && choices.length > 1 ? choices[1].value : undefined;
        fill(side.filter, choices, null, wanted);
      }
      if (name === null || name === "operation") {
        side.options.operation = operationOptions(lists.lists.operation.items);
        fill(side.operation, side.options.operation, listPlaceholder("operation", "operation"));
      }
      if (name === null || name === "process") {
        side.options.process = processOptions(lists.lists.process.items);
        fill(side.process, side.options.process, listPlaceholder("process", "process"));
      }
    }
  }

  function onListChange(name) {
    paintLists(name);
  }

  function showMode() {
    const mode = modeNow();
    for (const side of Object.values(sides)) {
      for (const m of MODES) {
        side.groups[m].hidden = m !== mode;
      }
    }
    if (listNameOf[mode]) {
      lists.ensure(listNameOf[mode]);
    }
  }

  function labelOf(side, value) {
    const found = side.options.filter.find((c) => c.value === value);
    return found ? found.label : "";
  }

  function readSide(name) {
    const side = sides[name];
    const mode = modeNow();
    if (mode === "filter") {
      return { source: side.filter.value };
    }
    if (mode === "window") {
      return { from: side.from.value, to: side.to.value };
    }
    if (mode === "operation") {
      return { event: side.operation.value };
    }
    return { stream: side.process.value, from: side.pfrom.value, to: side.pto.value };
  }

  function run() {
    const mode = modeNow();
    const names = mode === "filter"
      ? {
        a: sides.a.filter.value === CURRENT ? "" : labelOf(sides.a, sides.a.filter.value),
        b: sides.b.filter.value === CURRENT ? "" : labelOf(sides.b, sides.b.filter.value),
      }
      : null;
    return flow.run({
      mode,
      a: readSide("a"),
      b: readSide("b"),
      common: getFilter(),
      views: lists.lists.views.items,
      maxSide: Number(maxSelect.value),
      onlyDiff: diffBox.checked,
    }, names);
  }

  // ---- events
  runButton.addEventListener("click", () => run());
  cancelButton.addEventListener("click", () => {
    flow.cancel();
    runButton.focus(); // the Cancel button hides itself
  });
  modeSelect.addEventListener("change", showMode);
  diffBox.addEventListener("change", () => {
    // The same comparison, other rows; a form that was never run has nothing to redo.
    if (flow.state.body !== null) {
      flow.rerun({ onlyDiff: diffBox.checked });
    }
  });
  maxSelect.addEventListener("change", () => {
    if (flow.state.body !== null && Number(maxSelect.value) !== flow.state.body.max_side) {
      flow.rerun({ maxSide: Number(maxSelect.value) });
    }
  });
  raiseButton.addEventListener("click", () => {
    const max = Number(raiseButton.dataset.max);
    if (MAX_SIDE_CHOICES.includes(max)) {
      maxSelect.value = String(max);
      flow.rerun({ maxSide: max });
    }
  });
  moreButton.addEventListener("click", () => {
    if (moreButton.getAttribute("aria-disabled") !== "true") {
      flow.more();
    }
  });
  function jump(dir) {
    const rows = flow.state.rows;
    const at = nextDifference(rows, active.row, dir);
    if (at >= 0) {
      setActive(at, active.col, true);
    } else {
      say(dir > 0 ? S.cvNoNextDiff : S.cvNoPrevDiff, false);
    }
  }
  nextDiff.addEventListener("click", () => jump(1));
  prevDiff.addEventListener("click", () => jump(-1));
  rowsBody.addEventListener("click", (event) => {
    const button = event.target instanceof Element ? event.target.closest(".cv-open") : null;
    if (!button || !rowsBody.contains(button)) {
      return;
    }
    const state = flow.state;
    const row = state.rows[Number(button.dataset.row)];
    const data = row ? row[Number(button.dataset.col) === 0 ? "a" : "b"] : null;
    // The ids belong to the case the result was read for.
    if (data && state.caseId !== null && state.caseId === getCase()) {
      setActive(Number(button.dataset.row), Number(button.dataset.col), false);
      onReveal({ id: data.id, ts: data.ts });
    }
  });
  rowsBody.addEventListener("keydown", (event) => {
    const button = event.target instanceof Element ? event.target.closest(".cv-open") : null;
    if (!button || event.ctrlKey || event.altKey || event.metaKey) {
      return;
    }
    const next = moveCell({ row: Number(button.dataset.row), col: Number(button.dataset.col) }, event.key, cells.length);
    if (next === null) {
      return;
    }
    event.preventDefault();
    setActive(next.row, next.col, true);
  });
  rowsBody.addEventListener("focusin", (event) => {
    const button = event.target instanceof Element ? event.target.closest(".cv-open") : null;
    if (button) {
      setActive(Number(button.dataset.row), Number(button.dataset.col), false);
    }
  });

  showMode();
  onFlowState(flow.state);

  return {
    refresh: () => flow.viewChanged(),
    // Another case: the results and the lists are forgotten; the form (modes, times) stays.
    reset() {
      lists.clear();
      flow.reset();
      for (const side of Object.values(sides)) {
        side.touched = false;
      }
      showMode();
    },
    closed() {
      flow.cancel({ quiet: true });
      lists.cancelPending();
    },
    focus: () => modeSelect.focus(),
  };
}
