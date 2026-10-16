// "Copy as" menu: lists of values, original lines, the selected record and CSV of the view or of the
// operations. The server streams the file; this module only builds the form and hands the result to
// the clipboard or to a download. Texts of the log never reach the page as markup.
import { api } from "./api.js";
import {
  CX_DEFAULTS,
  CX_KINDS,
  CX_LIMITS,
  EVENT_STATUSES,
  actionsFor,
  createCopyExportFlow,
  isCsv,
  kindAvailability,
  sourceChoices,
} from "./copy-export-model.js";
import { createDialogShell, setStatusLine, statusLine } from "./panel-shell.js";
import { S } from "./strings.js";

const KIND_LABELS = {
  values: "cxKindValues", lines: "cxKindLines", record: "cxKindRecord", view_csv: "cxKindViewCsv",
  events_csv: "cxKindEventsCsv",
};

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

function select(options) {
  const node = document.createElement("select");
  for (const [value, label] of options) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = label;
    node.appendChild(option);
  }
  return node;
}

function row(labelText, control) {
  const wrap = element("label", "cx-field");
  wrap.append(element("span", "cx-label", labelText), control);
  return wrap;
}

function checkRow(labelText, box) {
  const wrap = element("label", "cx-check");
  wrap.append(box, element("span", "", labelText));
  return wrap;
}

function legacyCopy(text) {
  const area = document.createElement("textarea");
  area.value = text;
  area.className = "cx-offscreen";
  area.setAttribute("aria-hidden", "true");
  document.body.appendChild(area);
  area.select();
  let ok = false;
  try {
    ok = document.execCommand("copy");
  } catch (error) {
    ok = false;
  }
  area.remove();
  return ok;
}

function saveBlob(blob, name) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function isWindows() {
  const text = (navigator.userAgentData && navigator.userAgentData.platform) || navigator.platform || "";
  return /win/i.test(text);
}

// anchor: the Export menu trigger of the top bar (UI-12): focus returns to it when the dialog closes. This module adds
// no button of its own; the "Copy as..." action of the Export menu calls open(). The dialog (UI-23) is a modal
// <dialog> with Copy and Download fixed in the foot.
// getContext(): { caseId, filter, columns, fieldColumns, entry, recordId } of the viewer right now.
// Returns { open(), close(), isOpen(), reset() }.
export function initCopyExport(anchor, { getContext }) {
  const doc = anchor.ownerDocument || document;
  const shell = createDialogShell(doc, { title: S.cxTitle, className: "cx-dialog" });
  const panel = element("form", "cx-panel");
  panel.setAttribute("role", "group");
  panel.setAttribute("aria-label", S.cxTitle);
  shell.body.appendChild(panel);
  anchor.insertAdjacentElement("afterend", shell.dialog);

  const kindSelect = select(CX_KINDS.map((kind) => [kind, S[KIND_LABELS[kind]]]));
  const sourceSelect = select([]);
  const separatorSelect = select([["newline", S.cxSepNewline], ["comma", S.cxSepComma]]);
  const quoteSelect = select([["none", S.cxQuoteNone], ["double", S.cxQuoteDouble], ["single", S.cxQuoteSingle]]);
  const uniqueBox = document.createElement("input");
  uniqueBox.type = "checkbox";
  const statusSelect = select([["", S.cxEventsAnyStatus], ...EVENT_STATUSES.map((v) => [v, S.evStatusNames[v] || v])]);
  const nameInput = document.createElement("input");
  nameInput.type = "text";
  nameInput.placeholder = S.cxEventNameHint;
  nameInput.autocomplete = "off";
  const keyInput = document.createElement("input");
  keyInput.type = "text";
  keyInput.autocomplete = "off";
  const bomBox = document.createElement("input");
  bomBox.type = "checkbox";
  bomBox.checked = isWindows();
  const maxInput = document.createElement("input");
  maxInput.type = "number";
  maxInput.min = String(CX_LIMITS.maxRowsMin);
  maxInput.max = String(CX_LIMITS.maxRowsMax);
  maxInput.step = "1";
  maxInput.value = String(CX_DEFAULTS.maxRows);
  const copyButton = element("button", "ui-button is-primary cx-copy", S.cxCopy);
  copyButton.type = "button";
  const downloadButton = element("button", "ui-button cx-download", S.cxDownload);
  downloadButton.type = "button";
  const note = element("p", "cx-note", S.cxFormulaNote);
  const status = statusLine(doc, "cx-status");

  const groups = {
    source: row(S.cxSource, sourceSelect),
    separator: row(S.cxSeparator, separatorSelect),
    quote: row(S.cxQuote, quoteSelect),
    unique: checkRow(S.cxUnique, uniqueBox),
    eventStatus: row(S.cxEventStatus, statusSelect),
    eventName: row(S.cxEventName, nameInput),
    eventKey: row(S.cxEventKey, keyInput),
    bom: checkRow(S.cxBom, bomBox),
    max: row(S.cxMaxRows, maxInput),
  };
  panel.append(row(S.cxWhat, kindSelect), ...Object.values(groups), note);
  shell.foot.append(status, downloadButton, copyButton); // the status sits beside the actions it reports on

  const flow = createCopyExportFlow({
    api,
    getContext,
    clipboard: navigator.clipboard,
    sink: { copy: legacyCopy, save: saveBlob },
    onState: (state) => {
      setStatusLine(status, state.message, state.status === "error");
      status.classList.toggle("cx-warn", state.status === "error" || Boolean(state.truncated));
      const busy = state.status === "working";
      copyButton.disabled = busy;
      downloadButton.disabled = busy;
    },
  });

  let keyTypes = [];
  let keyCase = null;

  function form() {
    return {
      kind: kindSelect.value,
      source: sourceSelect.value,
      separator: separatorSelect.value,
      quote: quoteSelect.value,
      unique: uniqueBox.checked,
      bom: bomBox.checked,
      maxRows: maxInput.value,
      events: { status: statusSelect.value, name: nameInput.value, q: keyInput.value },
    };
  }

  function paintSources() {
    const ctx = getContext() || {};
    const previous = sourceSelect.value;
    sourceSelect.textContent = "";
    for (const choice of sourceChoices({ ...ctx, keyTypes })) {
      const option = document.createElement("option");
      option.value = choice.value;
      option.textContent = choice.label;
      sourceSelect.appendChild(option);
    }
    if ([...sourceSelect.options].some((o) => o.value === previous)) {
      sourceSelect.value = previous;
    }
  }

  function paint() {
    const ctx = getContext() || {};
    const kind = kindSelect.value;
    const available = kindAvailability(ctx);
    for (const option of kindSelect.options) {
      option.disabled = !available[option.value];
    }
    if (!available[kind]) {
      const first = [...kindSelect.options].find((o) => !o.disabled);
      if (first) {
        kindSelect.value = first.value;
      }
    }
    const current = kindSelect.value;
    const can = actionsFor(current);
    groups.source.hidden = current !== "values";
    groups.separator.hidden = current !== "values";
    groups.quote.hidden = current !== "values";
    groups.unique.hidden = current !== "values";
    for (const name of ["eventStatus", "eventName", "eventKey"]) {
      groups[name].hidden = current !== "events_csv";
    }
    groups.bom.hidden = !isCsv(current);
    groups.max.hidden = current === "record";
    note.hidden = !isCsv(current);
    copyButton.hidden = !can.copy;
    downloadButton.hidden = !can.download;
    copyButton.disabled = !available[current];
    downloadButton.disabled = !available[current];
  }

  async function loadKeyTypes() {
    const ctx = getContext() || {};
    if (!ctx.caseId || keyCase === ctx.caseId) {
      return;
    }
    keyCase = ctx.caseId;
    keyTypes = [];
    try {
      const data = await api.keyTypes(ctx.caseId);
      if (keyCase === ctx.caseId) {
        keyTypes = (data.types || []).map((t) => t.type);
      }
    } catch (error) {
      keyTypes = [];
    }
    paintSources();
  }

  function open() {
    shell.open(anchor);
    paintSources();
    paint();
    loadKeyTypes();
    kindSelect.focus();
  }

  kindSelect.addEventListener("change", paint);
  copyButton.addEventListener("click", () => flow.run(form(), "copy"));
  downloadButton.addEventListener("click", () => flow.run(form(), "download"));
  panel.addEventListener("submit", (event) => event.preventDefault());

  return {
    open,
    close: shell.close,
    isOpen: shell.isOpen,
    reset() {
      flow.reset();
      keyCase = null;
      keyTypes = [];
      if (shell.isOpen()) {
        paintSources();
        paint();
        loadKeyTypes();
      }
    },
  };
}
