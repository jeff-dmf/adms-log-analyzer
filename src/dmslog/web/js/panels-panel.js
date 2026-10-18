// "Panels" dock panel (ROADMAP 9.11, ARQUITETURA 10.14): the user's own panels, each a list of widgets (a declarative
// aggregation plus a chart type). A panel runs through POST /api/panels/run and refreshes by itself when the progress
// stream says that something may have changed. The state machine is panels-model.js; this file only draws it.
// Everything that comes from a log or a panel name is written with textContent; the DOM is never given markup.
// The panel has ONE live region (the status line). Charts are drawn from the data and always have a table next to them.
import { EXAMPLES, MAX_GROUP_BY, MAX_LIMIT, MAX_MEASURES, applicableLookups, dimLabel, dimOptions, emptyForm, exampleForm, measureLabel, measureOptions } from "./aggregate-model.js";
import {
  CHARTS, barLayout, chartFit, createPanelsFlow, formFromQuery, formToQuery, problemLines, queryEditable, sortedPanels, summarizeQuery,
  widgetBody, widgetErrorText, widgetName,
} from "./panels-model.js";
import { createPanelShell, setStatusLine, statusLine } from "./panel-shell.js";
import { S, format, pluralKey } from "./strings.js";

const CHART_HEIGHT = 200;
const COLORS = 8; // --agg-c0 .. --agg-c7 (aggregate.css)
let uid = 0;

const defaultUplot = () => import("../vendor/uplot/uPlot.esm.js").then((module) => module.default);

// Depth-first walk that works on a collection without array methods (an HTMLCollection has none).
function find(root, test) {
  if (test(root)) {
    return root;
  }
  for (const child of root.children) {
    const found = find(child, test);
    if (found !== null) {
      return found;
    }
  }
  return null;
}

// options:
//   api, getCase(), getFieldNames()   the plain API (api.panels.*, api.keyTypes, api.progressUrl)
//   onRequestClose()                  the dock closes the tab
//   flowOptions                       now, setTimer, clearTimer, connect (replaceable in tests)
//   loadUplot()                       resolves to the uPlot class (replaceable in tests)
// Returns { flow, refresh(), show(on), closed(), reset(), destroy(), focusTarget }.
export function mountPanelsPanel(host, {
  api, getCase, getFieldNames = () => [], onRequestClose = null, flowOptions = {}, loadUplot = defaultUplot,
}) {
  const doc = host.ownerDocument || document;
  const make = (tag, className, text) => {
    const node = doc.createElement(tag);
    if (className) {
      node.className = className;
    }
    if (text !== undefined) {
      node.textContent = text;
    }
    return node;
  };
  const makeButton = (className, text, onClick) => {
    const node = make("button", "ui-button " + className, text);
    node.type = "button";
    node.addEventListener("click", onClick);
    return node;
  };
  const labelled = (text, control, className = "") => {
    uid += 1;
    control.id = "pn-c" + uid;
    const wrap = make("span", ("pn-field " + className).trim());
    const label = make("label", "pn-label", text);
    label.htmlFor = control.id;
    wrap.append(label, control);
    return wrap;
  };
  const option = (value, text) => {
    const node = make("option", "", text);
    node.value = value;
    return node;
  };

  const shell = createPanelShell(host, { title: S.pnTitle, onRequestClose, onRefresh: () => flow.reloadAll() });
  let charts = []; // uPlot instances of the widgets on screen
  let renderStamp = 0; // grows with every rebuild of the results: a chart that finishes late checks it
  let keyTypes = [];
  let keyTypesCase = null;
  let keyTypesSeq = 0;
  let lastFocusSeq = 0;
  let lastNoticeSeq = 0;
  let lastNamingKind = "create";
  let resultsKey = "";
  let formWidgetId = null; // the widget whose values the persistent form shows
  let form = emptyForm();
  let formEditable = true;
  let formBusy = false; // a change is being written: the fields are disabled, so what they show is what was sent
  let destroyed = false;

  // ---- tools: the panel list and its buttons

  const select = make("select", "pn-select");
  select.addEventListener("change", () => {
    if (!flow.select(select.value)) {
      select.value = flow.state.selectedId === null ? "" : flow.state.selectedId; // refused: the list shows what is open
    }
  });
  const newButton = makeButton("pn-new", S.pnNew, () => flow.startNaming("create"));
  const renameButton = makeButton("pn-rename", S.pnRename, () => flow.startNaming("rename"));
  const editButton = makeButton("pn-edit", S.pnEdit, () => flow.edit());
  const deleteButton = makeButton("pn-delete", S.pnDelete, () => flow.askDelete());
  const runButton = makeButton("is-primary pn-run", S.pnRun, () => flow.run());
  const tools = make("div", "pn-tools");
  tools.append(labelled(S.pnPanel, select), newButton, renameButton, editButton, deleteButton, runButton);
  shell.addTools(tools);

  // The name field (create and rename) and the delete confirmation sit in the flow, not in a dialog.
  const nameForm = make("form", "pn-name-form");
  const nameInput = make("input", "pn-name-input");
  nameInput.type = "text";
  nameInput.maxLength = 200;
  const nameSubmit = make("button", "ui-button is-primary pn-name-ok");
  nameSubmit.type = "submit";
  const nameCancel = makeButton("pn-name-cancel", S.pnCancel, () => flow.cancelNaming());
  nameForm.append(labelled(S.pnName, nameInput), nameSubmit, nameCancel);
  nameForm.hidden = true;
  const submitName = () => flow.submitName(nameInput.value);
  nameForm.addEventListener("submit", (event) => {
    event.preventDefault();
    submitName();
  });
  nameSubmit.addEventListener("click", (event) => {
    event.preventDefault(); // one path: a real click would also fire "submit"
    submitName();
  });
  nameForm.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.preventDefault(); // the dock would close the tab
      flow.cancelNaming();
    }
  });

  const confirmRow = make("div", "pn-confirm");
  confirmRow.setAttribute("role", "group");
  confirmRow.setAttribute("aria-label", S.pnDeleteConfirm);
  const confirmText = make("span", "pn-confirm-text");
  const confirmOk = makeButton("is-danger pn-confirm-ok", S.pnDeleteConfirm, () => flow.confirmDelete());
  const confirmCancel = makeButton("pn-confirm-cancel", S.pnCancel, () => flow.cancelDelete());
  confirmRow.append(confirmText, confirmOk, confirmCancel);
  confirmRow.hidden = true;
  confirmRow.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      flow.cancelDelete();
    }
  });

  // ---- body

  const status = statusLine(doc, "pn-status"); // the one live region of the panel
  const problems = make("ul", "pn-problems");
  problems.hidden = true;
  const empty = make("p", "ui-empty pn-empty");
  const view = make("div", "pn-view");
  const heading = make("h3", "pn-heading");
  heading.tabIndex = -1;
  const meta = make("p", "pn-meta"); // not a live region: it changes on every automatic update
  const widgetsHost = make("div", "pn-widgets");
  view.append(heading, meta, widgetsHost);

  const editor = make("div", "pn-editor");
  const editorHeading = make("h3", "pn-heading");
  editorHeading.tabIndex = -1;
  const editorHint = make("p", "pn-meta", S.pnEditorHint);
  const editList = make("ul", "pn-edit-list");
  const addButton = makeButton("pn-add", S.pnAddWidget, () => flow.addWidget());
  editor.append(editorHeading, editorHint, editList, addButton);

  // The widget form: its controls are created once and keep what the user typed; they are filled when another
  // widget is chosen.
  const widgetForm = make("form", "pn-widget-form");
  widgetForm.setAttribute("aria-label", S.pnWidgetForm);
  const titleInput = make("input", "pn-title");
  titleInput.type = "text";
  titleInput.maxLength = 200;
  const chartSelect = make("select", "pn-chart");
  for (const chart of CHARTS) {
    chartSelect.appendChild(option(chart, S.pnCharts[chart]));
  }
  const examples = make("select", "pn-examples");
  examples.appendChild(option("", S.aggExamplesNone));
  for (const example of EXAMPLES) {
    examples.appendChild(option(example.id, example.title()));
  }
  const rowsSelect = make("select", "pn-rows");
  rowsSelect.append(option("entries", S.aggRowsEntries), option("events", S.aggRowsEvents));
  const onlyErrors = make("input", "pn-only-errors");
  onlyErrors.type = "checkbox";
  const groupSelects = [];
  const measureSelects = [];
  const queryBox = make("div", "pn-query");
  const line = () => make("div", "pn-line");
  const lineA = line();
  lineA.append(labelled(S.aggExamples, examples), labelled(S.aggRows, rowsSelect), labelled(S.aggOnlyErrors, onlyErrors));
  const lineB = line();
  for (let k = 0; k < MAX_GROUP_BY; k += 1) {
    const g = make("select", "pn-group");
    groupSelects.push(g);
    lineB.appendChild(labelled(format(S.aggGroupBy, { n: k + 1 }), g));
  }
  const lineC = line();
  for (let k = 0; k < MAX_MEASURES; k += 1) {
    const m = make("select", "pn-measure");
    measureSelects.push(m);
    lineC.appendChild(labelled(format(S.aggMeasure, { n: k + 1 }), m));
  }
  const eventInputs = {};
  const lineE = line();
  for (const [key, text] of [["eventKinds", S.aggEventKinds], ["eventNames", S.aggEventNames], ["eventStatuses", S.aggEventStatuses]]) {
    const input = make("input", "pn-event-input");
    input.type = "text";
    input.placeholder = S.aggEventHint;
    input.maxLength = 2000;
    eventInputs[key] = input;
    lineE.appendChild(labelled(text, input));
  }
  const orderSelect = make("select", "pn-order");
  const dirSelect = make("select", "pn-dir");
  dirSelect.append(option("desc", S.aggOrderDesc), option("asc", S.aggOrderAsc));
  const limitInput = make("input", "pn-limit");
  limitInput.type = "number";
  limitInput.min = "1";
  limitInput.max = String(MAX_LIMIT);
  const lookupHost = make("span", "pn-lookups");
  const lineD = line();
  lineD.append(labelled(S.aggOrderBy, orderSelect), labelled(S.aggOrderDir, dirSelect), labelled(S.aggLimit, limitInput), lookupHost);
  const queryNote = make("p", "pn-meta pn-query-note");
  const queryError = make("p", "pn-form-error pn-query-error");
  queryError.hidden = true;
  queryBox.append(lineA, lineB, lineC, lineE, lineD, queryNote, queryError);
  const fitNote = make("p", "pn-form-error pn-fit");
  fitNote.hidden = true;
  const lineTop = line();
  lineTop.append(labelled(S.pnWidgetTitle, titleInput), labelled(S.pnChart, chartSelect));
  widgetForm.append(lineTop, fitNote, queryBox);
  widgetForm.hidden = true;
  editor.appendChild(widgetForm);
  editor.hidden = true;

  shell.body.append(status, problems, empty, view, editor);
  shell.root.insertBefore(nameForm, shell.body);
  shell.root.insertBefore(confirmRow, shell.body);

  const saveButton = makeButton("is-primary pn-save", S.pnSave, () => flow.saveEdit());
  const cancelEditButton = makeButton("pn-cancel-edit", S.pnCancel, () => flow.cancelEdit());
  shell.addFoot(saveButton, cancelEditButton);
  shell.foot.hidden = true;

  // ---- helpers

  function rebuild(container, build) {
    const active = doc.activeElement;
    const key = active && active !== container && container.contains(active) && active.dataset ? active.dataset.key || "" : "";
    container.textContent = "";
    build();
    if (key !== "") {
      const next = find(container, (node) => node.dataset && node.dataset.key === key && !node.disabled);
      if (next !== null) {
        next.focus();
      }
    }
  }

  // A saved value that is not among the options (a key type the case does not have, or one that has not been loaded
  // yet) stays selectable: the form must show what is stored and never turn it into "none" on its own.
  function fillSelect(selectNode, options, groups, current, withNone, labelOf) {
    selectNode.textContent = "";
    if (withNone) {
      selectNode.appendChild(option("", S.aggNone));
    }
    const parents = {};
    for (const item of options) {
      let parent = selectNode;
      if (groups && groups[item.group]) {
        if (!parents[item.group]) {
          parents[item.group] = make("optgroup");
          parents[item.group].label = groups[item.group];
          selectNode.appendChild(parents[item.group]);
        }
        parent = parents[item.group];
      }
      parent.appendChild(option(item.value, item.label));
    }
    if (current !== "" && !options.some((item) => item.value === current)) {
      selectNode.appendChild(option(current, labelOf(current)));
    }
    selectNode.value = current;
  }

  const panelOf = () => flow.state.list.panels.find((p) => p.id === flow.state.selectedId) || null;
  const draftWidget = (id) => (flow.state.draft ? flow.state.draft.widgets.find((w) => w.id === id) || null : null);

  // ---- the widget form <-> the draft

  function paintFormControls() {
    const fields = getFieldNames();
    const dims = dimOptions(form.rows, keyTypes, fields);
    const dimGroups = { column: S.aggGroupColumns, time: S.aggGroupTime, key: S.aggGroupKeys, field: S.aggGroupFields };
    groupSelects.forEach((g, k) => fillSelect(g, dims, dimGroups, form.groupBy[k] || "", true, dimLabel));
    const measures = measureOptions(form.rows, keyTypes, fields);
    const mGroups = { count: S.aggGroupCount, distinct: S.aggGroupDistinct, number: S.aggGroupNumbers };
    measureSelects.forEach((m, k) => fillSelect(m, measures, mGroups, form.measures[k] || "", k > 0, measureLabel));
    rowsSelect.value = form.rows;
    onlyErrors.checked = Boolean(form.onlyErrors);
    lineE.hidden = form.rows !== "events";
    for (const key of Object.keys(eventInputs)) {
      eventInputs[key].value = form[key] || "";
    }
    paintOrder();
    dirSelect.value = form.orderDir === "asc" ? "asc" : "desc";
    limitInput.value = form.limit;
    paintLookups();
  }

  function paintOrder() {
    const dims = groupSelects.map((g) => g.value).filter(Boolean);
    const ids = [...dims, ...measureSelects.map((m) => m.value).filter(Boolean)];
    orderSelect.textContent = "";
    orderSelect.appendChild(option("", S.aggOrderDefault));
    for (const id of ids) {
      orderSelect.appendChild(option(id, dims.includes(id) ? dimLabel(id) : measureLabel(id)));
    }
    orderSelect.value = ids.includes(form.orderBy) ? form.orderBy : "";
  }

  let lookupBoxes = [];
  function paintLookups() {
    lookupHost.textContent = "";
    lookupBoxes = [];
    const names = applicableLookups(form);
    if (names.length === 0) {
      return;
    }
    lookupHost.appendChild(make("span", "pn-label", S.aggLookups));
    for (const name of names) {
      const box = make("input", "pn-lookup");
      box.type = "checkbox";
      box.value = name;
      box.checked = (form.lookups || []).includes(name);
      box.disabled = formBusy;
      lookupBoxes.push(box);
      box.addEventListener("change", onFormInput);
      lookupHost.appendChild(labelled(S.aggLookupNames[name] || name, box));
    }
  }

  // While the panel is written the fields cannot change (a change made after the request left would be shown but never
  // saved, and a retry would send the old value).
  function setFormBusy(on) {
    formBusy = on;
    const controls = [
      titleInput, chartSelect, examples, rowsSelect, onlyErrors, orderSelect, dirSelect, limitInput,
      ...groupSelects, ...measureSelects, ...Object.values(eventInputs), ...lookupBoxes,
    ];
    for (const control of controls) {
      control.disabled = on;
    }
  }

  function readForm() {
    form = {
      ...form,
      rows: rowsSelect.value === "events" ? "events" : "entries",
      onlyErrors: Boolean(onlyErrors.checked),
      groupBy: groupSelects.map((g) => g.value),
      measures: measureSelects.map((m) => m.value),
      orderBy: orderSelect.value,
      orderDir: dirSelect.value,
      limit: limitInput.value,
      lookups: lookupBoxes.filter((b) => b.checked).map((b) => b.value),
      eventKinds: eventInputs.eventKinds.value,
      eventNames: eventInputs.eventNames.value,
      eventStatuses: eventInputs.eventStatuses.value,
    };
  }

  function showQueryError(text) {
    queryError.textContent = text || "";
    queryError.hidden = !text;
  }

  // The form changed: a valid query goes to the draft, an invalid one blocks Save and says why next to the fields.
  function onFormInput() {
    const id = formWidgetId;
    if (id === null || !formEditable || !draftWidget(id) || flow.state.busy !== null) {
      return;
    }
    readForm();
    const built = formToQuery(form);
    if (built.error) {
      showQueryError(built.error);
      flow.setFormError(id, built.error);
      return;
    }
    showQueryError("");
    flow.setFormError(id, null);
    flow.updateWidget(id, { query: built.query });
    paintFit();
  }

  function onStructureInput() {
    if (flow.state.busy !== null) {
      return;
    }
    // A different list of rows: the choices of the selects change with it.
    const rows = rowsSelect.value;
    if (rows !== form.rows) {
      form = { ...emptyForm(), baseFilter: form.baseFilter, rows, groupBy: [rows === "events" ? "name" : "tpl", "", ""] };
      paintFormControls();
    } else {
      readForm();
      paintOrder();
      paintLookups();
    }
    onFormInput();
  }

  function paintFit() {
    const widget = draftWidget(formWidgetId);
    const reason = widget ? chartFit(widget.chart, widget.query) : null;
    fitNote.textContent = reason || "";
    fitNote.hidden = !reason;
  }

  // Fills the form with the widget being edited (only when the widget changes: typing is never overwritten).
  function loadForm(widget) {
    if (widget === null) {
      formWidgetId = null;
      return;
    }
    formWidgetId = widget.id;
    titleInput.value = widget.title;
    chartSelect.value = widget.chart;
    formEditable = queryEditable(widget.query);
    queryBox.hidden = false;
    for (const part of [lineA, lineB, lineC, lineE, lineD]) {
      part.hidden = !formEditable;
    }
    queryNote.textContent = formEditable ? "" : S.pnQueryReadOnly;
    queryNote.hidden = formEditable;
    showQueryError("");
    if (formEditable) {
      form = formFromQuery(widget.query);
      examples.value = "";
      paintFormControls();
      if (form.baseFilter !== null) {
        queryNote.textContent = S.pnKeepsFilter;
        queryNote.hidden = false;
      }
    }
    paintFit();
    loadKeyTypes();
  }

  async function loadKeyTypes() {
    const caseId = getCase();
    if (!caseId || keyTypesCase === caseId) {
      return;
    }
    keyTypesSeq += 1;
    const mine = keyTypesSeq;
    try {
      const data = await api.keyTypes(caseId);
      if (mine === keyTypesSeq && !destroyed && getCase() === caseId) {
        keyTypes = (data.types || []).map((t) => t.type);
        keyTypesCase = caseId;
        if (formWidgetId !== null && formEditable) {
          readForm();
          paintFormControls();
        }
      }
    } catch (err) {
      // Without the key types the form still offers columns, time and parsed fields.
    }
  }

  titleInput.addEventListener("input", () => {
    if (formWidgetId !== null && flow.state.busy === null) {
      flow.updateWidget(formWidgetId, { title: titleInput.value });
    }
  });
  chartSelect.addEventListener("change", () => {
    if (formWidgetId !== null && flow.state.busy === null) {
      flow.updateWidget(formWidgetId, { chart: chartSelect.value });
      paintFit();
    }
  });
  rowsSelect.addEventListener("change", onStructureInput);
  for (const control of [...groupSelects, ...measureSelects]) {
    control.addEventListener("change", onStructureInput);
  }
  for (const control of [orderSelect, dirSelect, limitInput, onlyErrors, ...Object.values(eventInputs)]) {
    control.addEventListener("change", onFormInput);
  }
  limitInput.addEventListener("input", onFormInput);
  examples.addEventListener("change", () => {
    const next = exampleForm(examples.value);
    if (next && formWidgetId !== null && formEditable && flow.state.busy === null) {
      form = { ...next, baseFilter: null };
      paintFormControls();
      onFormInput();
    }
  });
  widgetForm.addEventListener("submit", (event) => {
    event.preventDefault();
  });

  // ---- the editor

  function paintEditor(state) {
    const draft = state.draft;
    editorHeading.textContent = draft ? format(S.pnEditing, { name: draft.name }) : "";
    rebuild(editList, () => {
      if (!draft) {
        return;
      }
      draft.widgets.forEach((widget, index) => {
        const name = widgetName(widget, index);
        const item = make("li", "pn-edit-item");
        if (state.editingWidget === widget.id) {
          item.setAttribute("aria-current", "true");
          item.classList.add("is-editing");
        }
        const label = make("span", "pn-edit-name", name);
        const detail = make("span", "pn-edit-meta", S.pnCharts[widget.chart] + ": " + summarizeQuery(widget.query));
        const actions = make("span", "pn-edit-actions");
        const edit = makeButton("pn-edit-widget", S.pnEditWidget, () => flow.setEditingWidget(widget.id));
        edit.setAttribute("aria-label", format(S.pnEditWidgetLabel, { name }));
        edit.dataset.key = "edit:" + widget.id;
        const up = makeButton("pn-up", S.pnMoveUp, () => flow.moveWidget(widget.id, "up"));
        up.setAttribute("aria-label", format(S.pnMoveUpLabel, { name }));
        up.dataset.key = "up:" + widget.id;
        edit.disabled = state.busy !== null;
        up.disabled = index === 0 || state.busy !== null;
        const down = makeButton("pn-down", S.pnMoveDown, () => flow.moveWidget(widget.id, "down"));
        down.setAttribute("aria-label", format(S.pnMoveDownLabel, { name }));
        down.dataset.key = "down:" + widget.id;
        down.disabled = index === draft.widgets.length - 1 || state.busy !== null;
        const remove = makeButton("pn-remove", S.pnRemoveWidget, () => flow.removeWidget(widget.id));
        remove.setAttribute("aria-label", format(S.pnRemoveWidgetLabel, { name }));
        remove.dataset.key = "remove:" + widget.id;
        remove.disabled = state.busy !== null;
        actions.append(edit, up, down, remove);
        item.append(label, detail, actions);
        editList.appendChild(item);
      });
    });
    if (draft && draft.widgets.length === 0) {
      editList.appendChild(make("li", "ui-empty", S.pnNoWidgets));
    }
    addButton.disabled = state.busy !== null;
    setFormBusy(state.busy !== null);
    const widget = state.editingWidget !== null ? draftWidget(state.editingWidget) : null;
    widgetForm.hidden = widget === null;
    if (widget === null) {
      formWidgetId = null;
    } else if (formWidgetId !== widget.id) {
      loadForm(widget);
    }
  }

  // ---- results

  function destroyCharts() {
    for (const chart of charts) {
      try {
        chart.destroy();
      } catch (err) {
        // A chart that cannot be destroyed is already gone with its element.
      }
    }
    charts = [];
  }

  function cssColor(name, fallback) {
    if (typeof getComputedStyle !== "function") {
      return fallback;
    }
    return getComputedStyle(host).getPropertyValue(name).trim() || fallback;
  }

  function tableOf(widgetView, caption) {
    const table = make("table", "ui-table is-wrap pn-table");
    table.appendChild(make("caption", "visually-hidden", caption));
    const head = make("tr");
    widgetView.headers.forEach((text, i) => {
      const th = make("th", widgetView.numeric[i] ? "pn-num" : "", text);
      th.scope = "col";
      head.appendChild(th);
    });
    table.appendChild(make("thead")).appendChild(head);
    const body = make("tbody");
    for (const row of widgetView.rows) {
      const tr = make("tr");
      row.cells.forEach((text, i) => {
        const td = make("td", widgetView.numeric[i] ? "pn-num" : "pn-text", text);
        if (!widgetView.numeric[i]) {
          td.title = text;
        }
        tr.appendChild(td);
      });
      body.appendChild(tr);
    }
    table.appendChild(body);
    return table;
  }

  const legendOf = (series) => {
    const legend = make("ul", "pn-legend");
    series.forEach((s, k) => {
      const item = make("li", "pn-legend-item");
      item.append(make("span", "pn-swatch agg-c" + (k % COLORS)), doc.createTextNode(s.label));
      legend.appendChild(item);
    });
    return legend;
  };

  function barsOf(spec, name) {
    const wrap = make("div", "pn-bars");
    wrap.setAttribute("role", "img");
    wrap.setAttribute("aria-label", format(S.pnBarsLabel, { name, count: spec.categories.length }));
    spec.categories.forEach((category, i) => {
      const row = make("div", "pn-bar-row");
      const label = make("span", "pn-bar-label", category);
      label.title = category;
      const lines = make("div", "pn-bar-lines");
      const layout = barLayout(spec, i);
      // Every parcel sits where the shared scale puts it: negative values grow to the left of the zero line.
      const paint = (fill, part) => {
        fill.style.left = part.from.toFixed(1) + "%";
        fill.style.width = (part.to - part.from).toFixed(1) + "%";
      };
      const zeroMark = (track) => {
        if (spec.lo < 0) {
          const mark = make("span", "pn-bar-zero");
          mark.style.left = layout.zero.toFixed(1) + "%";
          track.appendChild(mark);
        }
      };
      if (spec.stacked) {
        const track = make("div", "pn-bar-track");
        zeroMark(track);
        let total = 0;
        spec.series.forEach((s, k) => {
          const part = layout.parts[k];
          total += part.value === null ? 0 : part.value;
          const fill = make("span", "pn-bar-fill agg-c" + (k % COLORS));
          paint(fill, part);
          track.appendChild(fill);
        });
        lines.append(track, make("span", "pn-bar-value", formatValue(Math.max(-Number.MAX_VALUE, Math.min(Number.MAX_VALUE, total)))));
      } else {
        spec.series.forEach((s, k) => {
          const part = layout.parts[k];
          const one = make("div", "pn-bar-line");
          const track = make("div", "pn-bar-track");
          zeroMark(track);
          const fill = make("span", "pn-bar-fill agg-c" + (k % COLORS));
          paint(fill, part);
          track.appendChild(fill);
          one.append(track, make("span", "pn-bar-value", part.value === null ? "" : formatValue(part.value)));
          lines.appendChild(one);
        });
      }
      row.append(label, lines);
      wrap.appendChild(row);
    });
    return wrap;
  }

  const formatValue = (v) => Number(v).toLocaleString("en-US", { maximumFractionDigits: 3 });

  async function drawLine(plotHost, spec, stamp) {
    let uPlot;
    try {
      uPlot = await loadUplot();
    } catch (err) {
      return; // no chart: the table is still there
    }
    if (stamp !== renderStamp || destroyed) {
      return;
    }
    const muted = cssColor("--muted", "#888");
    const border = cssColor("--border", "#ccc");
    const axis = { stroke: muted, grid: { stroke: border, width: 1 }, ticks: { stroke: border, width: 1 } };
    const series = [{}];
    spec.series.forEach((s, k) => {
      const color = cssColor("--agg-c" + (k % COLORS), "#3b82c4");
      series.push({ label: s.label, stroke: color, width: 2, spanGaps: false, points: { show: spec.xs.length < 60 } });
    });
    const options = {
      width: Math.max(200, plotHost.clientWidth || 600),
      height: CHART_HEIGHT,
      legend: { show: false },
      cursor: { points: { show: false } },
      scales: { x: { time: true }, y: { range: (_u, lo, hi) => [Math.min(0, lo), Math.max(1, hi) * 1.08] } },
      axes: [axis, { ...axis, size: 56 }],
      series,
      // The log clock is stored as UTC: draw the axis with UTC getters, never the browser zone.
      tzDate: (ts) => uPlot.tzDate(new Date(ts * 1000), "Etc/UTC"),
    };
    try {
      charts.push(new uPlot(options, [spec.xs, ...spec.series.map((s) => s.values)], plotHost));
    } catch (err) {
      // No chart: the table is still there.
    }
  }

  function chartBlock(name, body, stamp) {
    const block = make("div", "pn-chart-block");
    const spec = body.spec;
    if (spec === null) {
      block.appendChild(make("p", "pn-state", S.pnNoChart));
      return block;
    }
    if (spec.kind === "bars") {
      block.appendChild(barsOf(spec, name));
    } else {
      const plot = make("div", "pn-plot");
      plot.setAttribute("role", "img");
      plot.setAttribute("aria-label", format(S.pnLineLabel, { name, measure: spec.measure }));
      block.appendChild(plot);
      drawLine(plot, spec, stamp);
    }
    if (spec.series.length > 1 || spec.series[0].label !== spec.measure) {
      block.appendChild(legendOf(spec.series));
    }
    if (spec.truncated.categories) {
      block.appendChild(make("p", "pn-note", S.pnMoreCategories));
    }
    if (spec.truncated.series) {
      block.appendChild(make("p", "pn-note", S.pnMoreSeries));
    }
    return block;
  }

  const openTables = new Set(); // widget ids whose "Data table" is open: it stays open through an update

  function widgetCard(widget, index, state) {
    const name = widgetName(widget, index);
    const card = make("section", "pn-widget");
    uid += 1;
    const titleId = "pn-w" + uid;
    const title = make("h4", "pn-widget-title", name);
    title.id = titleId;
    card.setAttribute("aria-labelledby", titleId);
    card.appendChild(title);
    const entry = state.run.results[widget.id] || null;
    const stale = state.run.status === "running";
    if (entry === null) {
      card.appendChild(make("p", "pn-state", stale ? S.pnWidgetLoading : S.pnWidgetNotRun));
      return card;
    }
    if (entry.status === "skipped") {
      card.appendChild(make("p", "pn-state pn-budget", S.pnWidgetErrors.budget));
      return card;
    }
    if (entry.status === "error" || entry.result === null) {
      card.appendChild(make("p", "pn-state pn-error", widgetErrorText(entry.error)));
      return card;
    }
    const body = widgetBody(widget, entry.result);
    for (const note of body.view.notices) {
      card.appendChild(make("p", "pn-note", note));
    }
    if (body.empty) {
      card.appendChild(make("p", "pn-state", S.pnNoRows));
      return card;
    }
    if (widget.chart === "stat") {
      const stat = make("p", "pn-stat");
      stat.append(make("span", "pn-stat-value", body.stat ? body.stat.text : S.aggEmptyValue), make("span", "pn-stat-label", body.stat ? body.stat.label : ""));
      card.appendChild(stat);
      return card;
    }
    if (widget.chart === "table") {
      card.appendChild(make("p", "pn-meta", body.view.summary));
      card.appendChild(tableOf(body.view, name));
      return card;
    }
    card.appendChild(chartBlock(name, body, renderStamp));
    const details = make("details", "pn-details");
    details.open = openTables.has(widget.id);
    const summary = make("summary", "pn-summary", S.pnDataTable);
    summary.dataset.key = "summary:" + widget.id;
    details.appendChild(summary);
    const fill = () => {
      if (details.children.length === 1) {
        details.appendChild(tableOf(body.view, name));
      }
    };
    if (details.open) {
      fill();
    }
    details.addEventListener("toggle", () => {
      if (details.open) {
        openTables.add(widget.id);
        fill();
      } else {
        openTables.delete(widget.id);
      }
    });
    card.appendChild(details);
    return card;
  }

  function timeText(ms) {
    const d = new Date(ms);
    const two = (n) => String(n).padStart(2, "0");
    return two(d.getHours()) + ":" + two(d.getMinutes()) + ":" + two(d.getSeconds());
  }

  function paintView(state, panel) {
    heading.textContent = panel ? panel.name : "";
    const parts = [];
    if (panel) {
      parts.push(format(S[pluralKey("pnWidgets", panel.widgets.length)], { count: panel.widgets.length }));
      if (state.run.finishedAt !== null && state.run.status !== "idle") {
        parts.push(format(S.pnUpdated, { time: timeText(state.run.finishedAt) }));
      }
      if (state.run.status === "running") {
        parts.push(S.pnRunning);
      } else if (state.streamStopped) {
        parts.push(S.pnLiveOff);
      }
    }
    meta.textContent = parts.join(" - ");
    const key = [state.run.rv, state.selectedId, state.mode, state.shown, panel ? panel.rev : 0].join("|");
    widgetsHost.setAttribute("aria-busy", state.run.status === "running" ? "true" : "false");
    if (key === resultsKey) {
      return;
    }
    resultsKey = key;
    destroyCharts();
    renderStamp += 1;
    // The elements are rebuilt on every update: the one that has the focus (a "Data table" summary) is found again by
    // its widget id and gets the focus back.
    rebuild(widgetsHost, () => {
      if (!panel || state.mode !== "view" || !state.shown) {
        return;
      }
      if (panel.widgets.length === 0) {
        widgetsHost.appendChild(make("p", "ui-empty", S.pnNoWidgetsYet));
        return;
      }
      panel.widgets.forEach((widget, index) => widgetsHost.appendChild(widgetCard(widget, index, state)));
    });
  }

  // ---- the paint

  function paintSelect(state) {
    const sorted = sortedPanels(state.list.panels);
    const key = sorted.map((p) => p.id + ":" + p.name).join("\n");
    if (select.dataset.signature !== key) {
      select.dataset.signature = key;
      select.textContent = "";
      if (sorted.length === 0) {
        select.appendChild(option("", S.pnNoPanels));
      }
      for (const panel of sorted) {
        select.appendChild(option(panel.id, panel.name));
      }
    }
    select.value = state.selectedId === null ? "" : state.selectedId;
  }

  function applyFocus(state) {
    if (state.focus.seq === lastFocusSeq) {
      return;
    }
    lastFocusSeq = state.focus.seq;
    const target = state.focus.target;
    const keyed = (key) => find(editList, (node) => node.dataset && node.dataset.key === key && !node.disabled);
    let node = null;
    if (target === "heading") {
      node = heading;
    } else if (target === "select") {
      node = select;
    } else if (target === "name") {
      node = nameInput;
    } else if (target === "name-opener") {
      node = lastNamingKind === "create" ? newButton : renameButton;
    } else if (target === "confirm") {
      node = confirmCancel;
    } else if (target === "delete") {
      node = deleteButton;
    } else if (target === "editor") {
      node = editorHeading;
    } else if (target === "widget-title") {
      node = titleInput;
    } else if (target === "add") {
      node = addButton;
    } else if (target.startsWith("edit:") || target.startsWith("up:") || target.startsWith("down:")) {
      const id = target.slice(target.indexOf(":") + 1);
      node = keyed(target) || keyed("up:" + id) || keyed("down:" + id) || keyed("edit:" + id);
    }
    if (node !== null && typeof node.focus === "function") {
      node.focus();
    }
  }

  function render(state) {
    if (destroyed) {
      return;
    }
    const panel = panelOf();
    const editing = state.mode === "edit";
    const busy = state.busy !== null;
    paintSelect(state);
    select.disabled = busy || editing || state.list.panels.length === 0;
    newButton.disabled = busy || editing;
    renameButton.disabled = busy || editing || panel === null;
    editButton.disabled = busy || editing || panel === null;
    deleteButton.disabled = busy || editing || panel === null;
    runButton.disabled = editing || panel === null; // a run in progress is replaced by the new one

    nameForm.hidden = state.naming === null;
    if (state.naming !== null) {
      lastNamingKind = state.naming.kind;
      nameSubmit.textContent = state.naming.kind === "create" ? S.pnCreate : S.pnRenameOk;
      nameSubmit.disabled = busy;
      nameInput.disabled = busy;
      if (nameForm.dataset.kind !== state.naming.kind + (state.naming.id || "")) {
        nameForm.dataset.kind = state.naming.kind + (state.naming.id || "");
        nameInput.value = state.naming.kind === "rename" && panel ? panel.name : "";
      }
    } else {
      delete nameForm.dataset.kind;
    }
    confirmRow.hidden = !state.confirming || panel === null;
    if (!confirmRow.hidden) {
      confirmText.textContent = format(S.pnDeleteAsk, { name: panel.name });
      confirmOk.disabled = busy;
      confirmCancel.disabled = busy;
    }

    const lines = problemLines(state.list);
    rebuild(problems, () => {
      for (const text of lines) {
        problems.appendChild(make("li", "pn-problem", text));
      }
    });
    problems.hidden = lines.length === 0;

    if (state.list.status === "loading" || state.list.status === "idle") {
      empty.textContent = S.pnLoading;
    } else if (state.list.status === "error") {
      empty.textContent = state.list.error;
    } else {
      empty.textContent = state.list.panels.length === 0 ? S.pnNoPanelsYet : "";
    }
    empty.hidden = empty.textContent === "" || (panel !== null && state.list.status === "ready");

    view.hidden = editing || panel === null;
    editor.hidden = !editing;
    heading.hidden = view.hidden;
    shell.foot.hidden = !editing;
    saveButton.disabled = busy;
    cancelEditButton.disabled = busy;
    if (editing) {
      paintEditor(state);
    }
    paintView(state, editing ? null : panel);
    if (state.notice.seq !== lastNoticeSeq) {
      lastNoticeSeq = state.notice.seq;
      setStatusLine(status, state.notice.text, state.notice.kind === "error");
    }
    applyFocus(state);
  }

  // Escape closes the tab in the dock; a field or row that has its own use for the key handles it first (above).

  const flow = createPanelsFlow({ api, getCase, onState: render, ...flowOptions });
  render(flow.state);

  return {
    flow,
    focusTarget: select,
    refresh() {
      flow.setShown(true);
    },
    show(on) {
      flow.setShown(on);
    },
    closed() {
      flow.setShown(false); // hiding also destroys the charts (render)
    },
    reset() {
      flow.caseChanged();
      keyTypes = [];
      keyTypesCase = null;
    },
    destroy() {
      destroyed = true;
      flow.destroy();
      destroyCharts();
    },
  };
}
