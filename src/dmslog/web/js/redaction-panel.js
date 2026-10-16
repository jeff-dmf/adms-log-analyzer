// Redaction settings: the mode (strict or standard), which kinds of values are replaced in an export,
// the user's own terms, the exceptions and the payload element names. The profile lives in the data folder of this computer
// (GET/PUT /api/redaction); the page keeps no copy outside the form. Textareas are filled through
// `.value`, labels through textContent.
import { api } from "./api.js";
import { createRedactionFlow, formToProfile, profileToForm } from "./export-model.js";
import { createDialogShell, setStatusLine, statusLine } from "./panel-shell.js";
import { S } from "./strings.js";

let redactUid = 0;

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

function textArea(labelText, rows) {
  const area = document.createElement("textarea");
  area.rows = rows;
  area.spellcheck = false;
  area.autocomplete = "off";
  const row = element("label", "redact-field");
  row.append(element("span", "export-label", labelText), area);
  return { row, area };
}

// A modal dialog (UI-23) with Save and Reset fixed in the foot. host: the element the dialog is added to.
// Options: onClose() (after the dialog closed, for any reason). Returns { open(opener), close(), isOpen(), refresh() }.
export function initRedactionPanel(host, { onClose = null } = {}) {
  const doc = host.ownerDocument || document;
  const shell = createDialogShell(doc, { title: S.redactTitle, className: "redact-dialog", onClose });
  host.appendChild(shell.dialog);
  const status = statusLine(doc, "export-status");
  const invalid = element("div", "export-warning");
  invalid.hidden = true;

  const form = element("form", "redact-form");
  form.id = "redact-form-" + (redactUid += 1);
  const modeBox = element("fieldset", "redact-categories");
  modeBox.appendChild(element("legend", "", S.redactMode));
  const modeRadios = new Map();
  for (const [id, label] of [["strict", S.redactModeStrict], ["standard", S.redactModeStandard]]) {
    const radio = document.createElement("input");
    radio.type = "radio";
    radio.name = "redact-mode";
    radio.value = id;
    modeRadios.set(id, radio);
    const row = element("label", "export-radio");
    row.append(radio, element("span", "", label));
    modeBox.appendChild(row);
  }
  const categoryBox = element("fieldset", "redact-categories");
  categoryBox.appendChild(element("legend", "", S.redactCategories));
  const word = textArea(S.redactWordTerms, 4);
  const literal = textArea(S.redactLiteralTerms, 3);
  const exceptions = textArea(S.redactExceptions, 3);
  const names = textArea(S.redactPayloadNames, 5);
  const hint = element("div", "export-hint", S.redactHint);
  const saveButton = element("button", "ui-button is-primary redact-save", S.redactSave);
  saveButton.type = "submit";
  saveButton.setAttribute("form", form.id);
  const resetButton = element("button", "ui-button redact-reset", S.redactReset);
  resetButton.type = "button";
  form.append(modeBox, categoryBox, word.row, literal.row, exceptions.row, names.row, hint);
  shell.body.append(status, invalid, form);
  shell.foot.append(resetButton, saveButton);

  const checkboxes = new Map();
  let profileName = "default";

  function fill(profile) {
    const values = profileToForm(profile);
    profileName = values.name;
    const mode = profile && profile.mode === "standard" ? "standard" : "strict";
    for (const [id, radio] of modeRadios) {
      radio.checked = id === mode;
    }
    for (const [id, box] of checkboxes) {
      box.checked = values.categories[id] !== false;
    }
    word.area.value = values.word;
    literal.area.value = values.literal;
    exceptions.area.value = values.exceptions;
    names.area.value = values.names;
  }

  function readForm() {
    const categories = {};
    for (const [id, box] of checkboxes) {
      categories[id] = box.checked;
    }
    const profile = formToProfile({
      name: profileName,
      categories,
      word: word.area.value,
      literal: literal.area.value,
      exceptions: exceptions.area.value,
      names: names.area.value,
    });
    profile.mode = modeRadios.get("standard").checked ? "standard" : "strict";
    return profile;
  }

  let filled = null;
  function onState(state) {
    const busy = state.status === "loading" || state.status === "saving";
    saveButton.disabled = busy;
    resetButton.disabled = busy;
    invalid.hidden = !state.invalid;
    invalid.textContent = state.invalid ? S.redactInvalidTitle + ": " + state.message : "";
    if (state.status === "loading") {
      setStatusLine(status, S.redactLoading, false);
    } else if (state.status === "error") {
      setStatusLine(status, S.redactError + (state.error ? ": " + state.error : ""), true);
    } else if (state.saved) {
      setStatusLine(status, S.redactSaved, false);
    } else {
      setStatusLine(status, "", false);
    }
    if (state.status === "ready" && state.profile && state.profile !== filled) {
      if (checkboxes.size === 0) {
        for (const category of state.categories) {
          const box = document.createElement("input");
          box.type = "checkbox";
          checkboxes.set(category.id, box);
          const row = element("label", "export-radio");
          row.append(box, element("span", "", category.label));
          categoryBox.appendChild(row);
        }
      }
      filled = state.profile;
      fill(state.profile);
    }
  }

  const flow = createRedactionFlow({ api, onState });
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    flow.save(readForm());
  });
  resetButton.addEventListener("click", () => {
    if (flow.state.defaults) {
      fill(flow.state.defaults);
    }
  });

  const refresh = () => {
    filled = null;
    return flow.load();
  };
  return {
    open(opener) {
      shell.open(opener);
      return refresh();
    },
    close: shell.close,
    isOpen: shell.isOpen,
    refresh,
  };
}
