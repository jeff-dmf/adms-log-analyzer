// "Preferences: export / import…" (9.10, ARQUITETURA 10.12): one modal dialog, loaded on the first use of the action.
// Export: choose the sections (redaction is off by default and carries a warning) and download the .json file.
// Import: choose a local file, Check (a dry run, nothing is written), read the report, Apply. The logic and every
// decision live in preferences-model.js; this module draws its state and forwards the clicks. Everything is
// written with textContent; the file is read in the browser and sent only to the local server.
import { api as defaultApi } from "./api.js";
import { createDialogShell, node, setStatusLine, statusLine } from "./panel-shell.js";
import {
  MODES, SECTIONS, createExportFlow, createImportFlow, formatSize, renderMessage, renderMessages,
} from "./preferences-model.js";
import { S, format } from "./strings.js";

let uid = 0;

// Saves text as a file through a Blob and a download link. The object URL is revoked a second after the click; a
// dispose revokes the ones still waiting. env is the page (tests pass fakes).
export function createFileSaver(doc = document, env = globalThis) {
  const pending = new Map(); // timer -> object URL
  return {
    save(name, text) {
      const url = env.URL.createObjectURL(new env.Blob([text], { type: "application/json;charset=utf-8" }));
      try {
        const link = doc.createElement("a");
        link.href = url;
        link.download = name;
        doc.body.appendChild(link);
        try {
          link.click();
        } finally {
          link.remove();
        }
      } catch (err) {
        env.URL.revokeObjectURL(url);
        throw err;
      }
      const timer = env.setTimeout(() => {
        pending.delete(timer);
        env.URL.revokeObjectURL(url);
      }, 1000);
      pending.set(timer, url);
    },
    flush() {
      for (const [timer, url] of pending) {
        env.clearTimeout(timer);
        env.URL.revokeObjectURL(url);
      }
      pending.clear();
    },
  };
}

function field(doc, type, name) {
  const input = doc.createElement("input");
  input.type = type;
  if (name) {
    input.name = name;
  }
  return input;
}

function button(doc, label, className, onClick) {
  const item = node(doc, "button", "ui-button " + className, label);
  item.type = "button";
  item.addEventListener("click", onClick);
  return item;
}

function cell(doc, tag, text, className) {
  const item = node(doc, tag, className || "", text);
  if (tag === "th") {
    item.setAttribute("scope", className === "is-row" ? "row" : "col");
  }
  return item;
}

// host: the element the dialog is added to (document.body). Options: api, onApplied({unknown}) after an Apply that
// wrote files (or may have), saveFile(name, text), readFile(file), now(), onClose().
// Returns { open(opener), close(), isOpen(), dispose() }.
export function mountPreferencesDialog(host, {
  api = defaultApi, onApplied = () => {}, saveFile = null, readFile, now, onClose = null,
} = {}) {
  const doc = host.ownerDocument || document;
  const id = "pf-" + String((uid += 1));
  const saver = saveFile === null ? createFileSaver(doc) : null;
  const save = saveFile || ((name, text) => saver.save(name, text));

  let exporting = null;
  let importing = null;
  const shell = createDialogShell(doc, {
    title: S.pfTitle,
    className: "pf-dialog",
    onClose() {
      exporting.reset(); // nothing pending, nothing kept: the file may hold the user's redaction terms
      importing.reset();
      fileInput.value = "";
      if (typeof onClose === "function") {
        onClose();
      }
    },
  });
  host.appendChild(shell.dialog);

  // ---- the one live region (the foot): messages of both flows, the last one that spoke
  const status = statusLine(doc, "pf-status");
  shell.foot.appendChild(status);

  // ---- Export
  const exportBox = node(doc, "section", "pf-section");
  exportBox.setAttribute("aria-labelledby", id + "-export");
  const exportHeading = node(doc, "h3", "pf-heading", S.pfExportHeading);
  exportHeading.id = id + "-export";
  const sectionsBox = node(doc, "fieldset", "pf-group");
  sectionsBox.appendChild(node(doc, "legend", "", S.pfExportSections));
  const boxes = new Map();
  for (const section of SECTIONS) {
    const input = field(doc, "checkbox", "pf-section-" + section.id);
    const row = node(doc, "label", "pf-choice");
    row.append(input, node(doc, "span", "", S[section.titleKey]));
    const hint = node(doc, "p", "pf-hint", S[section.hintKey]);
    hint.id = `${id}-${section.id}-hint`;
    const describedBy = [hint.id];
    sectionsBox.append(row, hint);
    if (section.id === "redaction") {
      const warning = node(doc, "p", "pf-warning", S.pfRedactionWarning);
      warning.id = id + "-redaction-warning";
      describedBy.push(warning.id);
      sectionsBox.appendChild(warning);
    }
    input.setAttribute("aria-describedby", describedBy.join(" "));
    input.addEventListener("change", () => exporting.setSection(section.id, input.checked));
    boxes.set(section.id, input);
  }
  const exportButton = button(doc, S.pfExportButton, "is-primary pf-export", () => {
    exporting.run();
  });
  const exportActions = node(doc, "div", "pf-actions");
  exportActions.appendChild(exportButton);
  exportBox.append(exportHeading, sectionsBox, exportActions);

  // ---- Import
  const importBox = node(doc, "section", "pf-section");
  importBox.setAttribute("aria-labelledby", id + "-import");
  const importHeading = node(doc, "h3", "pf-heading", S.pfImportHeading);
  importHeading.id = id + "-import";
  const fileInput = field(doc, "file");
  fileInput.id = id + "-file";
  fileInput.accept = ".json,application/json";
  const fileLabel = node(doc, "label", "pf-subheading", S.pfFileLabel);
  fileLabel.setAttribute("for", fileInput.id);
  const fileInfo = node(doc, "p", "pf-file-info", S.pfFileNone);
  fileInfo.id = id + "-file-info";
  fileInput.setAttribute("aria-describedby", fileInfo.id);

  const modeBox = node(doc, "fieldset", "pf-group");
  modeBox.appendChild(node(doc, "legend", "", S.pfModeLegend));
  const radios = new Map();
  for (const [mode, labelKey, hintKey] of [["merge", "pfModeMerge", "pfModeMergeHint"], ["replace", "pfModeReplace", "pfModeReplaceHint"]]) {
    const radio = field(doc, "radio", id + "-mode");
    radio.value = mode;
    const hint = node(doc, "p", "pf-hint", S[hintKey]);
    hint.id = `${id}-${mode}-hint`;
    radio.setAttribute("aria-describedby", hint.id);
    const row = node(doc, "label", "pf-choice");
    row.append(radio, node(doc, "span", "", S[labelKey]));
    radio.addEventListener("change", () => {
      if (radio.checked) {
        importing.setMode(mode);
      }
    });
    modeBox.append(row, hint);
    radios.set(mode, radio);
  }
  const skipInput = field(doc, "checkbox", id + "-skip");
  const skipHint = node(doc, "p", "pf-hint", S.pfPolicySkipHint);
  skipHint.id = id + "-skip-hint";
  skipInput.setAttribute("aria-describedby", skipHint.id);
  skipInput.addEventListener("change", () => importing.setSkipInvalid(skipInput.checked));
  const skipRow = node(doc, "label", "pf-choice");
  skipRow.append(skipInput, node(doc, "span", "", S.pfPolicySkip));
  modeBox.append(skipRow, skipHint);

  const checkButton = button(doc, S.pfCheckButton, "pf-check", () => {
    importing.check();
  });
  const applyButton = button(doc, S.pfApplyButton, "is-primary pf-apply", () => {
    importing.apply();
  });
  const actions = node(doc, "div", "pf-actions");
  actions.append(checkButton, applyButton);

  const confirm = node(doc, "div", "pf-confirm");
  confirm.setAttribute("role", "group");
  const confirmText = node(doc, "p", "", S.pfConfirmReplace);
  confirmText.id = id + "-confirm";
  confirm.setAttribute("aria-labelledby", confirmText.id);
  const confirmYes = button(doc, S.pfConfirmYes, "pf-confirm-yes", () => {
    importing.apply({ confirmed: true });
  });
  const confirmNo = button(doc, S.pfConfirmNo, "pf-confirm-no", () => {
    importing.cancelConfirm();
    applyButton.focus();
  });
  const confirmActions = node(doc, "div", "pf-actions");
  confirmActions.append(confirmNo, confirmYes);
  confirm.append(confirmText, confirmActions);
  confirm.hidden = true;

  const reportBox = node(doc, "div", "pf-report");
  reportBox.tabIndex = -1;
  reportBox.setAttribute("aria-label", S.pfReportLabel);
  reportBox.hidden = true;

  importBox.append(importHeading, fileLabel, fileInput, fileInfo, modeBox, actions, confirm, reportBox);
  shell.body.append(node(doc, "p", "pf-intro", S.pfIntro), exportBox, importBox);

  fileInput.addEventListener("change", () => {
    const picked = fileInput.files && fileInput.files.length > 0 ? fileInput.files[0] : null;
    fileInput.value = ""; // the same file can be chosen again (it may have changed); the File object stays valid
    if (picked !== null) {
      importing.chooseFile(picked);
    }
  });

  // ---- drawing
  let spoke = { export: -1, import: -1 };
  const say = (who, state) => {
    if (state.seq !== spoke[who]) {
      spoke[who] = state.seq;
      setStatusLine(status, renderMessages(state.messages), state.isError);
    }
  };

  function renderReport(report) {
    reportBox.textContent = "";
    reportBox.hidden = report === null;
    if (report === null) {
      return;
    }
    reportBox.appendChild(node(doc, "h3", "pf-heading", report.dryRun ? S.pfReportCheck : S.pfReportDone));
    reportBox.appendChild(node(doc, "p", "pf-headline", renderMessage(report.headline)));
    if (report.migratedFrom !== null) {
      reportBox.appendChild(node(doc, "p", "pf-note", format(S.pfMigrated, { version: report.migratedFrom })));
    }
    if (report.errors.length > 0) {
      const list = node(doc, "ul", "pf-list");
      for (const error of report.errors) {
        list.appendChild(node(doc, "li", "", renderMessage(error)));
      }
      reportBox.appendChild(list);
    }
    if (report.sections.length === 0) {
      return;
    }
    const table = node(doc, "table", "ui-table is-wrap pf-table");
    table.appendChild(node(doc, "caption", "", S.pfSummaryCaption));
    const head = node(doc, "tr");
    for (const [key, numeric] of [["pfColSection", false], ["pfColStatus", false], ["pfColAdded", true], ["pfColUpdated", true],
      ["pfColUnchanged", true], ["pfColRejected", true], ["pfColRemoved", true]]) {
      head.appendChild(cell(doc, "th", S[key], numeric ? "is-num" : ""));
    }
    const thead = node(doc, "thead");
    thead.appendChild(head);
    const tbody = node(doc, "tbody");
    for (const section of report.sections) {
      const row = node(doc, "tr");
      row.append(
        cell(doc, "th", S[SECTIONS.find((s) => s.id === section.id).titleKey], "is-row"),
        cell(doc, "td", S[section.statusKey], section.status === "rejected" || section.unknown ? "is-bad" : ""),
      );
      for (const key of ["added", "updated", "unchanged", "rejected", "removed"]) {
        row.appendChild(cell(doc, "td", String(section.counts[key]), "is-num"));
      }
      tbody.appendChild(row);
    }
    table.append(thead, tbody);
    reportBox.appendChild(table);
    for (const section of report.sections) {
      renderSectionDetail(section);
    }
  }

  function renderSectionDetail(section) {
    const title = S[SECTIONS.find((s) => s.id === section.id).titleKey];
    const hasDetail = section.items.length > 0 || section.removed.length > 0 || section.warnings.length > 0
      || section.error !== null || section.noteKey !== null;
    if (!hasDetail) {
      return;
    }
    const box = node(doc, "div", "pf-detail");
    box.appendChild(node(doc, "h4", "pf-subheading", title));
    if (section.error !== null) {
      box.appendChild(node(doc, "p", "pf-note is-bad", renderMessage(section.error)));
    }
    for (const warning of section.warnings) {
      box.appendChild(node(doc, "p", "pf-note", renderMessage(warning)));
    }
    if (section.noteKey !== null) {
      box.appendChild(node(doc, "p", "pf-note", S[section.noteKey]));
    }
    if (section.items.length > 0) {
      const table = node(doc, "table", "ui-table is-wrap pf-table");
      table.appendChild(node(doc, "caption", "", format(S.pfItemsCaption, { section: title })));
      const head = node(doc, "tr");
      for (const key of ["pfColNumber", "pfColName", "pfColResult", "pfColReason"]) {
        head.appendChild(cell(doc, "th", S[key], key === "pfColNumber" ? "is-num" : ""));
      }
      const thead = node(doc, "thead");
      thead.appendChild(head);
      const tbody = node(doc, "tbody");
      for (const item of section.items) {
        const row = node(doc, "tr");
        row.append(
          cell(doc, "td", String(item.number), "is-num"),
          cell(doc, "td", item.name),
          cell(doc, "td", S.pfItemOutcome[item.outcome], item.outcome === "rejected" ? "is-bad" : ""),
          cell(doc, "td", item.reason === null ? "" : renderMessage(item.reason)),
        );
        tbody.appendChild(row);
      }
      table.append(thead, tbody);
      box.appendChild(table);
      if (section.hiddenItems > 0) {
        box.appendChild(node(doc, "p", "pf-note", format(S.pfItemsMore, { count: section.hiddenItems })));
      }
    }
    if (section.removed.length > 0) {
      box.appendChild(node(doc, "p", "pf-note", format(S.pfRemovedTitle, { section: title })));
      const list = node(doc, "ul", "pf-list");
      for (const name of section.removed) {
        list.appendChild(node(doc, "li", "", name));
      }
      box.appendChild(list);
      if (section.hiddenRemoved > 0) {
        box.appendChild(node(doc, "p", "pf-note", format(S.pfItemsMore, { count: section.hiddenRemoved })));
      }
    }
    reportBox.appendChild(box);
  }

  let before = "empty";
  function drawImport(state) {
    say("import", state);
    fileInfo.textContent = state.file === null ? S.pfFileNone : format(S.pfFileInfo, { name: state.file.name, size: formatSize(state.file.size) });
    for (const mode of MODES) {
      radios.get(mode).checked = state.mode === mode;
    }
    skipInput.checked = state.skipInvalid;
    checkButton.disabled = state.phase === "empty" || state.phase === "reading";
    applyButton.disabled = !state.canApply;
    confirm.hidden = !state.confirming;
    renderReport(state.report);
    // A focused control that disappears or turns off must not take the focus with it.
    if (state.confirming && before !== "confirming") {
      confirmNo.focus();
    }
    if (state.phase === "applying" && before !== "applying") {
      reportBox.hidden = false;
      reportBox.focus();
    } else if (before === "applying" && state.phase === "ready") {
      checkButton.focus();
    } else if (before === "applying" && state.phase === "applied") {
      reportBox.focus();
    }
    before = state.confirming ? "confirming" : state.phase;
  }

  function drawExport(state) {
    say("export", state);
    for (const [section, box] of boxes) {
      box.checked = state.selection[section] === true;
    }
  }

  exporting = createExportFlow({ api, save, now, onState: drawExport });
  importing = createImportFlow({ api, readFile, onState: drawImport, onApplied });
  drawExport(exporting.state);
  drawImport(importing.state);

  return {
    open: (opener) => shell.open(opener),
    close: () => shell.close(),
    isOpen: () => shell.isOpen(),
    // The page is going away (or the dialog is not wanted any more): pending work stops, the element leaves.
    dispose() {
      exporting.dispose();
      importing.dispose();
      shell.close();
      shell.dialog.remove();
      if (saver !== null) {
        saver.flush();
      }
    },
  };
}
