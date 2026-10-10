// Export panel: choose what to export, build the report on the server, preview the anonymized text,
// copy it to the clipboard or download it as .txt. The report text comes from the ERS (untrusted):
// it only reaches the page through textContent, the clipboard and a Blob.
import { api } from "./api.js";
import {
  EXPORT_DEFAULTS,
  EXPORT_LIMITS,
  SELECTION_KINDS,
  copyText,
  createExportFlow,
  defaultSelectionKind,
  selectionAvailability,
} from "./export-model.js";
import { S, format } from "./strings.js";

const STYLESHEET = "/css/export.css";
const SELECTION_LABELS = { filter: "exportSelFilter", record: "exportSelRecord", event: "exportSelEvent" };

function ensureStylesheet() {
  if (!document.querySelector('link[rel="stylesheet"][href="' + STYLESHEET + '"]')) {
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = STYLESHEET;
    document.head.appendChild(link);
  }
}

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

function labelled(labelText, control, className) {
  const row = element("label", className || "export-field");
  row.append(element("span", "export-label", labelText), control);
  return row;
}

// Legacy copy through a temporary textarea (when navigator.clipboard is not available).
function legacyCopy(text) {
  const area = document.createElement("textarea");
  area.value = text;
  area.className = "export-offscreen";
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

function download(part) {
  const blob = new Blob([part.text], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = part.name;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// host: empty element to fill (hidden until opened). Options:
//   getContext()        { caseId, filter, recordId, eventId } of the viewer right now
//   onOpenRedaction()   open the redaction settings panel
// Returns { refresh(), reset() }.
export function initExportPanel(host, { getContext, onOpenRedaction }) {
  ensureStylesheet();
  host.textContent = "";
  const head = element("div", "export-head");
  head.appendChild(element("h3", "export-title", S.exportTitle));
  const settingsButton = element("button", "export-settings", S.exportRedactionSettings);
  settingsButton.type = "button";
  head.appendChild(settingsButton);

  const form = element("form", "export-form");
  const fieldset = element("fieldset", "export-selection");
  fieldset.appendChild(element("legend", "", S.exportSelectionLegend));
  const radios = new Map();
  for (const kind of SELECTION_KINDS) {
    const radio = document.createElement("input");
    radio.type = "radio";
    radio.name = "export-selection";
    radio.value = kind;
    radios.set(kind, radio);
    const row = element("label", "export-radio");
    row.append(radio, element("span", "", S[SELECTION_LABELS[kind]]));
    fieldset.appendChild(row);
  }
  const contextInput = document.createElement("input");
  contextInput.type = "number";
  contextInput.min = "0";
  contextInput.max = String(EXPORT_LIMITS.contextMax);
  contextInput.value = String(EXPORT_DEFAULTS.context);
  const anonymizeSelect = document.createElement("select");
  for (const [value, label] of [["on", S.exportAnonOn], ["off", S.exportAnonOff]]) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = label;
    anonymizeSelect.appendChild(option);
  }
  const anonWarning = element("div", "export-warning", S.exportAnonOffWarning);
  anonWarning.hidden = true;
  const payloadBox = document.createElement("input");
  payloadBox.type = "checkbox";
  const payloadRow = element("label", "export-radio");
  payloadRow.append(payloadBox, element("span", "", S.exportPayloads));
  const sizeInput = document.createElement("input");
  sizeInput.type = "number";
  sizeInput.min = String(EXPORT_LIMITS.maxBytesMin);
  sizeInput.max = String(EXPORT_LIMITS.maxBytesMax);
  sizeInput.step = "1000";
  sizeInput.value = String(EXPORT_DEFAULTS.maxBytes);
  const buildButton = element("button", "export-build", S.exportBuild);
  buildButton.type = "submit";
  form.append(
    fieldset,
    labelled(S.exportContext, contextInput),
    labelled(S.exportAnonymize, anonymizeSelect),
    anonWarning,
    payloadRow,
    labelled(S.exportMaxBytes, sizeInput),
    buildButton,
  );

  const status = element("div", "export-status");
  status.setAttribute("role", "status");
  const result = element("div", "export-result");
  result.hidden = true;
  const partSelect = document.createElement("select");
  partSelect.className = "export-part";
  partSelect.setAttribute("aria-label", S.exportPartLabel);
  const copyButton = element("button", "export-copy", S.exportCopy);
  copyButton.type = "button";
  const downloadButton = element("button", "export-download", S.exportDownload);
  downloadButton.type = "button";
  const downloadAllButton = element("button", "export-download-all", S.exportDownloadAll);
  downloadAllButton.type = "button";
  const copyStatus = element("span", "export-copy-status");
  copyStatus.setAttribute("role", "status");
  const actions = element("div", "export-actions");
  actions.append(partSelect, copyButton, downloadButton, downloadAllButton, copyStatus);
  const summary = element("div", "export-summary");
  const preview = element("pre", "export-preview");
  preview.setAttribute("aria-label", S.exportPreviewLabel);
  preview.tabIndex = 0;
  const notes = element("div", "export-notes");
  const legend = element("ul", "export-legend");
  result.append(summary, actions, preview, notes, legend);
  host.append(head, form, status, result);

  function selectedKind() {
    for (const [kind, radio] of radios) {
      if (radio.checked) {
        return kind;
      }
    }
    return null;
  }

  function formValues() {
    return {
      context: contextInput.value,
      anonymize: anonymizeSelect.value !== "off",
      includePayloads: payloadBox.checked,
      maxBytes: sizeInput.value,
    };
  }

  function showState(state) {
    result.hidden = state.status !== "ready";
    buildButton.disabled = state.status === "loading";
    status.classList.toggle("is-error", state.status === "error");
    if (state.status === "loading") {
      status.textContent = S.exportBuilding;
    } else if (state.status === "error") {
      status.textContent = state.errorKey ? S[state.errorKey] : S.exportError + (state.error ? ": " + state.error : "");
    } else {
      status.textContent = "";
    }
    if (state.status !== "ready") {
      return;
    }
    copyStatus.textContent = "";
    const total = state.parts.length;
    partSelect.textContent = "";
    state.parts.forEach((part, index) => {
      const option = document.createElement("option");
      option.value = String(index);
      option.textContent = format(S.exportPartOption, { part: index + 1, total });
      partSelect.appendChild(option);
    });
    partSelect.value = String(state.active);
    partSelect.hidden = total < 2;
    downloadAllButton.hidden = total < 2;
    const counts = state.counts || { records: 0, events: 0, payloads: 0 };
    summary.textContent =
      (state.anonymized ? S.exportAnonymized : S.exportNotAnonymized) + ". " + format(S.exportCounts, counts) +
      (state.mapSaved ? ". " + S.exportMapSaved : "");
    summary.classList.toggle("is-warning", !state.anonymized);
    const part = state.parts[state.active];
    preview.textContent = part ? part.text : "";
    notes.textContent = "";
    if (state.warnings.length > 0) {
      notes.appendChild(element("strong", "", S.exportWarningsTitle + ": "));
      notes.appendChild(document.createTextNode(state.warnings.join(" ")));
    }
    legend.textContent = "";
    if (state.legend.length > 0) {
      legend.appendChild(element("li", "export-legend-title", S.exportLegendTitle));
    }
    for (const row of state.legend) {
      legend.appendChild(element("li", "", format(S.exportLegendLine, row)));
    }
  }

  const flow = createExportFlow({ api, getCase: () => getContext().caseId, onState: showState });

  function refresh() {
    const context = getContext();
    const available = selectionAvailability(context);
    const previous = selectedKind();
    for (const [kind, radio] of radios) {
      radio.disabled = !available[kind];
    }
    const wanted = previous && available[previous] ? previous : defaultSelectionKind(context);
    radios.get(wanted).checked = true;
  }

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const context = getContext();
    const values = formValues();
    flow.build(selectedKind(), context, values);
    if (!values.anonymize) {
      // Turning anonymization off is explicit on every export: the choice goes back to "on".
      anonymizeSelect.value = "on";
      anonWarning.hidden = true;
    }
  });
  anonymizeSelect.addEventListener("change", () => {
    anonWarning.hidden = anonymizeSelect.value !== "off";
  });
  partSelect.addEventListener("change", () => flow.selectPart(Number(partSelect.value)));
  copyButton.addEventListener("click", async () => {
    const part = flow.state.parts[flow.state.active];
    if (!part) {
      return;
    }
    const how = await copyText(part.text, { clipboard: navigator.clipboard, fallback: legacyCopy });
    copyStatus.textContent =
      how === "clipboard" ? S.exportCopied : how === "fallback" ? S.exportCopyFallback : S.exportCopyFailed;
  });
  downloadButton.addEventListener("click", () => {
    const part = flow.state.parts[flow.state.active];
    if (part) {
      download(part);
    }
  });
  downloadAllButton.addEventListener("click", () => {
    for (const part of flow.state.parts) {
      download(part);
    }
  });
  settingsButton.addEventListener("click", () => onOpenRedaction());

  return {
    refresh,
    reset: () => {
      flow.reset();
      refresh();
    },
  };
}
