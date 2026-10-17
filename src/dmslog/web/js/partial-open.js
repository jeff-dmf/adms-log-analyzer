// "Open only part first" (M9 9.9): the body of the section in the Add files dialog. A time window (log time) and/or
// the sources chosen from a preview (POST .../collections/plan); the choice becomes the `first` object of
// POST .../collections. Loaded on demand by case-dialogs.js the first time the option is turned on. Names, members and
// paths come from the API or from the user: textContent only.
import { api as defaultApi } from "./api.js";
import { pathKey, formatSize, splitPath } from "./case-files-model.js";
import { setFieldError } from "./field-error.js";
import {
  MAX_FIRST_SOURCES, buildFirst, canChoose, chosenSources, defaultChecked, mergePeriods, normalizePlan, parseWindow,
  periodOf, windowTouchesPeriod,
} from "./partial-open-model.js";
import { S, format, pluralKey } from "./strings.js";
import { formatTs } from "./time.js";

let uid = 0;

function el(doc, tag, className, text) {
  const node = doc.createElement(tag);
  if (className) {
    node.className = className;
  }
  if (text !== undefined) {
    node.textContent = text;
  }
  return node;
}

function button(doc, label, handler) {
  const node = el(doc, "button", "ui-button", label);
  node.type = "button";
  node.addEventListener("click", handler);
  return node;
}

function isHidden(node) {
  for (let item = node; item; item = item.parentNode || item.parent) {
    if (item.hidden === true) {
      return true;
    }
  }
  return false;
}

// Time as the window fields take it: no milliseconds when they are zero.
function timeText(ms) {
  return formatTs(ms).replace(/\.000$/, "");
}

function sourceMetaText(source) {
  const parts = [];
  if (source.family !== "") {
    parts.push(source.family);
  }
  if (source.sensitive) {
    parts.push(S.sourceSensitive);
  }
  if (source.pid !== null) {
    parts.push(format(S.sourcePid, { pid: source.pid }));
  }
  const size = source.size === null ? "" : formatSize(source.size);
  if (size !== "") {
    parts.push(size);
  }
  if (source.firstTs !== null) {
    parts.push(timeText(source.firstTs) + (source.lastTs !== null ? " - " + timeText(source.lastTs) : ""));
  }
  return parts.join(" · ");
}

// Options: host (element that receives the body), getCaseId(), getPaths() (the files that will be opened: list items
// and typed paths), getScope() ("default" | "all"), api (default: the app's), onChange() after the choice changed.
// Returns { snapshot(), validate(), reset(), invalidate(), showServerError(text), hasPreview() }.
export function mountPartialBody({ host, getCaseId, getPaths, getScope, api = defaultApi, onChange = null }) {
  const doc = host.ownerDocument || document;
  const id = String(++uid);
  host.textContent = "";

  const help = el(doc, "p", "po-help", S.poToggleHelp);

  // -- time window
  const windowBox = el(doc, "div", "po-window");
  windowBox.setAttribute("role", "group");
  const windowTitle = el(doc, "h4", "po-subtitle", S.poWindowTitle);
  windowTitle.id = "po-window-title-" + id;
  windowBox.setAttribute("aria-labelledby", windowTitle.id);
  const fields = el(doc, "div", "po-fields");
  const makeField = (key, label) => {
    const wrap = el(doc, "div", "po-field");
    const input = el(doc, "input", "po-time");
    input.id = "po-" + key + "-" + id;
    input.type = "text";
    input.autocomplete = "off";
    input.value = "";
    input.placeholder = S.poTimePlaceholder;
    const caption = el(doc, "label", "po-label", label);
    caption.setAttribute("for", input.id);
    input.addEventListener("input", () => {
      setFieldError(input, null);
      setServerError("");
      refreshSummary();
      changed();
    });
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault(); // inside the Open form: Enter must not open the typed path
      }
    });
    wrap.append(caption, input);
    return { wrap, input };
  };
  const fromField = makeField("from", S.poFrom);
  const toField = makeField("to", S.poTo);
  fields.append(fromField.wrap, toField.wrap);
  windowBox.append(windowTitle, fields);

  // -- sources
  const sourcesBox = el(doc, "div", "po-sources");
  sourcesBox.setAttribute("role", "group");
  const sourcesTitle = el(doc, "h4", "po-subtitle", S.poSourcesTitle);
  sourcesTitle.id = "po-sources-title-" + id;
  sourcesBox.setAttribute("aria-labelledby", sourcesTitle.id);
  const sourcesHelp = el(doc, "p", "po-help", S.poSourcesHelp);
  const actions = el(doc, "div", "po-actions");
  const previewButton = button(doc, S.poPreview, () => preview());
  const checkAll = button(doc, S.poCheckAll, () => setAllChecked(true));
  const uncheckAll = button(doc, S.poUncheckAll, () => setAllChecked(false));
  checkAll.hidden = true;
  uncheckAll.hidden = true;
  actions.append(previewButton, checkAll, uncheckAll);
  const status = el(doc, "p", "po-status");
  status.setAttribute("role", "status");
  status.setAttribute("aria-live", "polite");
  const periodLine = el(doc, "p", "po-period");
  const groupsBox = el(doc, "div", "po-groups");
  const sourcesError = el(doc, "p", "ui-field-error po-error");
  sourcesError.id = "po-sources-error-" + id;
  sourcesError.setAttribute("role", "alert");
  sourcesError.hidden = true;
  sourcesBox.append(sourcesHelp, actions, status, periodLine, groupsBox, sourcesError);

  // -- summary and server refusal
  const summary = el(doc, "p", "po-summary");
  summary.setAttribute("role", "status");
  summary.setAttribute("aria-live", "polite");
  const serverError = el(doc, "p", "ui-field-error po-error");
  serverError.setAttribute("role", "alert");
  serverError.hidden = true;

  host.append(help, windowBox, sourcesBox, summary, serverError);

  // -- state
  let token = 0; // one per preview run (and per reset/invalidate): an older answer is dropped
  let previewing = false;
  let plans = new Map(); // pathKey -> {path, name, scope, sources, truncated, checked, node, boxes, count, error}

  function changed() {
    if (typeof onChange === "function") {
      onChange();
    }
  }

  function setServerError(text) {
    serverError.textContent = text;
    serverError.hidden = text === "";
  }

  function setSourcesError(text) {
    sourcesError.textContent = text;
    sourcesError.hidden = text === "";
    if (text === "") {
      sourcesBox.removeAttribute("aria-describedby");
      sourcesBox.removeAttribute("aria-invalid");
    } else {
      sourcesBox.setAttribute("aria-describedby", sourcesError.id);
      sourcesBox.setAttribute("aria-invalid", "true");
    }
  }

  function currentPaths() {
    const out = [];
    const seen = new Set();
    for (const raw of getPaths() || []) {
      const path = String(raw).trim();
      if (path === "" || seen.has(pathKey(path))) {
        continue;
      }
      seen.add(pathKey(path));
      out.push(path);
    }
    return out;
  }

  // The previewed entries that belong to the files that will be opened now.
  function activePlans(paths = currentPaths()) {
    const out = [];
    for (const path of paths) {
      const entry = plans.get(pathKey(path));
      if (entry && entry.scope === getScope() && !entry.error) {
        out.push(entry);
      }
    }
    return out;
  }

  function refreshSummary() {
    const lines = [];
    const window = parseWindow(fromField.input.value, toField.input.value);
    const paths = currentPaths();
    const entries = activePlans(paths);
    let narrowed = false;
    if (window.valid && !window.empty) {
      narrowed = true;
      lines.push(format(S.poSummaryWindow, {
        from: window.from === null ? S.poOpenStart : timeText(window.from),
        to: window.to === null ? S.poOpenEnd : timeText(window.to),
      }));
      const period = mergePeriods(entries.map((entry) => periodOf(entry.sources, entry.name)));
      if (period && !windowTouchesPeriod(window, period)) {
        lines.push(S.poWindowOutside);
      }
    }
    for (const entry of entries) {
      const chosen = chosenSources(entry.sources, entry.checked);
      if (chosen.names !== null) {
        narrowed = true;
        lines.push(format(S.poSummarySources, { name: entry.name, count: chosen.count, total: chosen.total }));
      }
    }
    if (!narrowed) {
      lines.push(S.poSummaryNone);
    }
    const unpreviewed = paths.length - entries.length;
    if (entries.length > 0 && unpreviewed > 0) {
      lines.push(format(S[pluralKey("poUnpreviewed", unpreviewed)], { count: unpreviewed }));
    }
    summary.textContent = lines.join(" ");
  }

  // -- preview
  function renderGroup(entry) {
    const group = el(doc, "section", "po-group");
    const head = el(doc, "h5", "po-group-title", entry.name);
    head.title = entry.path;
    group.appendChild(head);
    if (entry.error) {
      group.appendChild(el(doc, "p", "ui-field-error po-error", format(S.poPlanFailed, { name: entry.name, message: entry.error })));
    } else if (entry.sources.length === 0) {
      group.appendChild(el(doc, "p", "po-note", format(S.poNoSources, { name: entry.name })));
    } else {
      const count = el(doc, "p", "po-note");
      entry.count = count;
      group.appendChild(count);
      const list = el(doc, "ul", "po-list");
      list.setAttribute("aria-label", entry.name);
      entry.boxes = new Map();
      for (const source of entry.sources) {
        const item = el(doc, "li", "po-item");
        const label = el(doc, "label", "po-source");
        const box = el(doc, "input", "po-check");
        box.type = "checkbox";
        box.checked = entry.checked.has(source.member);
        box.disabled = !canChoose(source);
        label.title = !source.indexable ? S.poNotIndexable : (!source.selected ? S.poOutOfScope : "");
        box.addEventListener("change", () => {
          if (box.checked) {
            entry.checked.add(source.member);
          } else {
            entry.checked.delete(source.member);
          }
          afterCheckChange(entry);
        });
        label.appendChild(box);
        label.appendChild(el(doc, "span", "po-name", source.name));
        const meta = sourceMetaText(source);
        if (meta !== "") {
          label.appendChild(el(doc, "span", "po-meta", meta));
        }
        item.appendChild(label);
        list.appendChild(item);
        entry.boxes.set(source.member, box);
      }
      group.appendChild(list);
      updateCount(entry);
      if (entry.truncated) {
        group.appendChild(el(doc, "p", "po-note", format(S.poTruncated, { max: entry.sources.length.toLocaleString("en-US"), name: entry.name })));
      }
    }
    entry.node = group;
    groupsBox.appendChild(group);
  }

  function updateCount(entry) {
    if (!entry.count) {
      return;
    }
    const chosen = chosenSources(entry.sources, entry.checked);
    entry.count.textContent = format(S.poGroupCount, { checked: chosen.count, total: chosen.total });
  }

  function afterCheckChange(entry) {
    updateCount(entry);
    setSourcesError("");
    setServerError("");
    refreshSummary();
    changed();
  }

  function setAllChecked(on) {
    for (const entry of plans.values()) {
      if (!entry.boxes) {
        continue;
      }
      entry.checked = on ? defaultChecked(entry.sources) : new Set();
      for (const source of entry.sources) {
        const box = entry.boxes.get(source.member);
        if (box) {
          box.checked = entry.checked.has(source.member);
        }
      }
      updateCount(entry);
    }
    setSourcesError("");
    setServerError("");
    refreshSummary();
    changed();
  }

  function clearPreview() {
    plans = new Map();
    groupsBox.textContent = "";
    periodLine.textContent = "";
    status.textContent = "";
    checkAll.hidden = true;
    uncheckAll.hidden = true;
    setSourcesError("");
  }

  function showPeriod() {
    const entries = [...plans.values()].filter((entry) => !entry.error);
    const period = mergePeriods(entries.map((entry) => periodOf(entry.sources, entry.name)));
    if (!period) {
      periodLine.textContent = "";
      return;
    }
    periodLine.textContent = format(period.approx ? S.poPeriodApprox : S.poPeriod, { from: timeText(period.from), to: timeText(period.to) });
  }

  async function preview() {
    if (previewing) {
      return; // a repeated click while a preview runs
    }
    const caseId = getCaseId();
    if (caseId === null || caseId === undefined) {
      status.textContent = S.needCase;
      return;
    }
    const paths = currentPaths();
    if (paths.length === 0) {
      status.textContent = S.poNeedPath;
      return;
    }
    const mine = ++token;
    const scope = getScope();
    previewing = true;
    previewButton.disabled = true;
    clearPreview();
    setServerError("");
    const total = paths.length;
    try {
      for (let index = 0; index < total; index += 1) {
        const path = paths[index];
        const name = splitPath(path).name || path;
        status.textContent = format(S.poReading, { name, index: index + 1, total });
        const entry = { path, name, scope, sources: [], truncated: false, checked: new Set(), node: null, boxes: null, count: null, error: "" };
        try {
          const data = await api.partial.plan(caseId, path, scope);
          if (mine !== token) {
            return; // reset, scope change or a newer preview: this answer is old
          }
          const normalized = normalizePlan(data);
          entry.sources = normalized.sources;
          entry.truncated = normalized.truncated;
          entry.checked = defaultChecked(normalized.sources);
        } catch (err) {
          if (mine !== token) {
            return;
          }
          entry.error = err && err.message ? String(err.message) : String(err);
        }
        if (getCaseId() !== caseId) {
          return;
        }
        plans.set(pathKey(path), entry);
        renderGroup(entry);
        showPeriod();
        refreshSummary();
      }
      status.textContent = format(S[pluralKey("poPreviewed", total)], { count: total });
      const any = [...plans.values()].some((entry) => entry.boxes);
      checkAll.hidden = !any;
      uncheckAll.hidden = !any;
      changed();
    } finally {
      if (mine === token) {
        previewing = false;
        const hadFocus = doc.activeElement === previewButton || doc.activeElement === doc.body || isHidden(doc.activeElement);
        previewButton.disabled = false;
        if (hadFocus && !isHidden(previewButton)) {
          previewButton.focus();
        }
      }
    }
  }

  // -- validation and the value to send
  function focusNode(node) {
    if (node && typeof node.focus === "function" && !node.disabled && !isHidden(node)) {
      node.focus();
    }
  }

  // {ok: true} | {ok: false, message}. Errors are shown next to the field; the first wrong one takes the focus.
  function validate() {
    const window = parseWindow(fromField.input.value, toField.input.value);
    setFieldError(fromField.input, window.errors.from ? S.poBadTime : null);
    setFieldError(toField.input, window.errors.to === "bad" ? S.poBadTime : (window.errors.to === "order" ? S.poOrder : null));
    setSourcesError("");
    setServerError("");
    if (!window.valid) {
      focusNode(window.errors.from ? fromField.input : toField.input);
      return { ok: false, message: window.errors.from ? S.poBadTime : (window.errors.to === "order" ? S.poOrder : S.poBadTime) };
    }
    let narrowed = !window.empty;
    for (const entry of activePlans()) {
      const chosen = chosenSources(entry.sources, entry.checked);
      if (chosen.none || chosen.tooMany) {
        const message = chosen.none
          ? format(S.poErrNone, { name: entry.name })
          : format(S.poErrTooMany, { name: entry.name, max: MAX_FIRST_SOURCES });
        setSourcesError(message);
        const firstBox = entry.boxes ? [...entry.boxes.values()].find((box) => !box.disabled) : null;
        focusNode(firstBox || previewButton);
        return { ok: false, message };
      }
      if (chosen.names !== null) {
        narrowed = true;
      }
    }
    if (!narrowed) {
      setSourcesError(S.poErrNothing);
      focusNode(fromField.input);
      return { ok: false, message: S.poErrNothing };
    }
    return { ok: true };
  }

  // Freezes the choice at the moment the queue starts; later edits do not change the files already queued.
  function snapshot() {
    const window = parseWindow(fromField.input.value, toField.input.value);
    const scope = getScope();
    const names = new Map();
    for (const entry of activePlans()) {
      names.set(pathKey(entry.path), chosenSources(entry.sources, entry.checked).names);
    }
    const from = window.valid ? window.from : null;
    const to = window.valid ? window.to : null;
    return {
      scope,
      window: { from, to },
      // `useScope`: the scope the file will really be opened with. A source choice previewed under another scope
      // names members that may not be in this one, so only the window is kept.
      firstFor(path, useScope = scope) {
        const key = pathKey(path);
        return buildFirst({ from, to, sources: names.has(key) && useScope === scope ? names.get(key) : null });
      },
    };
  }

  function invalidate(message = "") {
    token += 1;
    previewing = false;
    previewButton.disabled = false;
    clearPreview();
    status.textContent = message;
    refreshSummary();
    changed();
  }

  function reset() {
    token += 1;
    previewing = false;
    previewButton.disabled = false;
    clearPreview();
    for (const field of [fromField, toField]) {
      field.input.value = "";
      setFieldError(field.input, null);
    }
    setServerError("");
    refreshSummary();
  }

  refreshSummary();

  return {
    snapshot,
    validate,
    reset,
    invalidate,
    refresh: refreshSummary,
    showServerError(text) {
      setServerError(text === "" ? "" : format(S.poServerRefused, { message: text }));
    },
    hasPreview: () => plans.size > 0,
    focusFirst: () => focusNode(fromField.input),
  };
}
