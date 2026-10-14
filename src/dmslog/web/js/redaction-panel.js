// Redaction settings: the mode (strict or standard), which kinds of values are replaced in an export,
// the user's own terms, the exceptions and the payload element names. The profile lives in the data folder of this computer
// (GET/PUT /api/redaction); the page keeps no copy outside the form. Textareas are filled through
// `.value`, labels through textContent.
import { api } from "./api.js";
import { createRedactionFlow, formToProfile, profileToForm } from "./export-model.js";
import { S } from "./strings.js";

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

// host: empty element to fill (hidden until opened). Returns { refresh() }.
export function initRedactionPanel(host) {
  host.textContent = "";
  const head = element("div", "export-head");
  head.appendChild(element("h3", "export-title", S.redactTitle));
  const closeButton = element("button", "redact-close", S.redactClose);
  closeButton.type = "button";
  head.appendChild(closeButton);
  const status = element("div", "export-status");
  status.setAttribute("role", "status");
  const invalid = element("div", "export-warning");
  invalid.hidden = true;

  const form = element("form", "redact-form");
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
  const saveButton = element("button", "redact-save", S.redactSave);
  saveButton.type = "submit";
  const resetButton = element("button", "redact-reset", S.redactReset);
  resetButton.type = "button";
  const buttons = element("div", "export-actions");
  buttons.append(saveButton, resetButton);
  form.append(modeBox, categoryBox, word.row, literal.row, exceptions.row, names.row, hint, buttons);
  host.append(head, status, invalid, form);

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
    status.classList.toggle("is-error", state.status === "error");
    if (state.status === "loading") {
      status.textContent = S.redactLoading;
    } else if (state.status === "error") {
      status.textContent = S.redactError + (state.error ? ": " + state.error : "");
    } else if (state.saved) {
      status.textContent = S.redactSaved;
    } else {
      status.textContent = "";
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
  closeButton.addEventListener("click", () => {
    host.hidden = true;
  });

  return {
    refresh: () => {
      filled = null;
      return flow.load();
    },
  };
}
