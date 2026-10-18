// View presets: shortcuts for the adapters present in the case (curated + the user's own). A preset
// is only applied when the user clicks it; applying fills the filter tree. The user can save the
// current tree as a preset, rename, delete (two steps), export and import. Every text from the
// server (names, descriptions, messages) reaches the page through textContent only.
import { MAX_NAME } from "./filter-tree-model.js";
import {
  MAX_USER_PRESETS, exportFile, formatFromFileName, groupByFamily, importPayload, importSummary, planPresetSave,
  presetBody,
} from "./presets-model.js";
import { createViewsGuard } from "./saved-views-model.js";
import { S, format, pluralKey } from "./strings.js";

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) {
    node.className = className;
  }
  if (text !== undefined) {
    node.textContent = text;
  }
  return node;
}

function button(label, className, onClick) {
  const b = el("button", className, label);
  b.type = "button";
  b.addEventListener("click", onClick);
  return b;
}

function saveFile(name, text, type) {
  const blob = new Blob([text], { type });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// getFilter() -> the current valid filter JSON of the tree, or null. onApply(filter) fills the tree.
// Returns {setCase(id), refresh(), destroy()}.
export function initPresets(container, { api, caseId, getFilter, onApply, onError }) {
  let currentCase = caseId;
  let presets = [];
  let families = [];
  let confirming = null; // key of the preset whose delete awaits the second press
  let confirmTimer = null;
  let controller = null;
  let busy = false;
  let renaming = null;
  let confirmed = null;
  const guard = createViewsGuard();

  container.classList.add("pr-panel");
  const details = el("details", "pr-details");
  details.open = true;
  const summary = el("summary", "pr-title", S.prTitle);
  const intro = el("p", "pr-intro", S.prIntro);
  const message = el("div", "pr-message");
  message.setAttribute("role", "status");
  message.setAttribute("aria-live", "polite");
  const list = el("div", "pr-groups");

  const form = el("form", "pr-form");
  const nameId = "pr-name-" + Math.random().toString(36).slice(2, 8);
  const nameLabel = el("label", "visually-hidden", S.prName);
  nameLabel.htmlFor = nameId;
  const nameInput = el("input");
  nameInput.id = nameId;
  nameInput.type = "text";
  nameInput.autocomplete = "off";
  nameInput.placeholder = S.prNamePlaceholder;
  nameInput.maxLength = MAX_NAME * 2;
  const familyId = "pr-family-" + Math.random().toString(36).slice(2, 8);
  const familyLabel = el("label", "pr-family-label", S.prFamily);
  familyLabel.htmlFor = familyId;
  const familySelect = el("select");
  familySelect.id = familyId;
  const saveButton = el("button", "", S.prSave);
  saveButton.type = "submit";
  const cancelButton = button(S.prCancel, "", () => resetForm(true));
  cancelButton.hidden = true;
  form.append(nameLabel, nameInput, familyLabel, familySelect, saveButton, cancelButton);

  const io = el("div", "pr-io");
  const exportJson = button(S.prExportJson, "", () => exportAs("json"));
  const exportToml = button(S.prExportToml, "", () => exportAs("toml"));
  const importButton = button(S.prImport, "", () => fileInput.click());
  const fileInput = el("input");
  fileInput.type = "file";
  fileInput.accept = ".json,.toml";
  fileInput.hidden = true;
  fileInput.setAttribute("aria-label", S.prImport);
  io.append(exportJson, exportToml, importButton, fileInput);

  details.append(summary, intro, list, message, form, io);
  container.append(details);

  function say(text, isError) {
    message.textContent = text;
    message.classList.toggle("pr-error", Boolean(isError));
  }

  function paintForm() {
    saveButton.textContent = renaming ? S.prRename : confirmed ? S.prOverwrite : S.prSave;
    cancelButton.hidden = !renaming && !confirmed;
    familySelect.disabled = Boolean(renaming);
  }

  function resetForm(clear) {
    renaming = null;
    confirmed = null;
    if (clear) {
      nameInput.value = "";
      say("", false);
    }
    paintForm();
  }

  function fillFamilies() {
    const keep = familySelect.value;
    familySelect.replaceChildren();
    const all = el("option", "", S.prFamilyAll);
    all.value = "";
    familySelect.append(all);
    for (const family of families) {
      const option = el("option", "", family);
      option.value = family;
      familySelect.append(option);
    }
    familySelect.value = families.includes(keep) ? keep : "";
  }

  function cancelConfirm() {
    confirming = null;
    if (confirmTimer !== null) {
      clearTimeout(confirmTimer);
      confirmTimer = null;
    }
  }

  const keyOf = (p) => (p.family || "") + "|" + p.name.toLowerCase();

  function problem(error) {
    const map = {
      empty: S.prErrorEmpty,
      tooLong: format(S.prErrorTooLong, { limit: MAX_NAME }),
      duplicate: S.prErrorDuplicate,
      reserved: S.prErrorReserved,
      tooMany: format(S[pluralKey("prErrorTooMany", MAX_USER_PRESETS)], { limit: MAX_USER_PRESETS }),
    };
    return map[error] || "";
  }

  function render(focusKey) {
    list.replaceChildren();
    if (presets.length === 0) {
      list.append(el("p", "pr-empty", S.prEmpty));
      return;
    }
    for (const group of groupByFamily(presets)) {
      const section = el("section", "pr-group");
      section.append(el("h4", "pr-group-title", group.family ? format(S.prFamilyGroup, { family: group.family }) : S.prGlobalGroup));
      const ul = el("ul", "pr-list");
      for (const preset of group.items) {
        const item = el("li", "pr-item");
        const apply = button(preset.name, "pr-apply", () => {
          cancelConfirm();
          if (preset.filter) {
            onApply(preset.filter);
          }
        });
        apply.setAttribute("aria-label", format(S.prApply, { name: preset.name }));
        if (preset.description) {
          apply.title = preset.description;
        }
        item.append(apply);
        if (preset.description) {
          item.append(el("span", "pr-description", preset.description));
        }
        if (preset.builtin) {
          item.append(el("span", "pr-badge", S.prBuiltin));
        } else {
          const key = keyOf(preset);
          const rename = button(S.prRename, "pr-rename", () => {
            cancelConfirm();
            renaming = preset;
            confirmed = null;
            nameInput.value = preset.name;
            nameInput.focus();
            nameInput.select();
            say(S.prRenaming, false);
            paintForm();
          });
          rename.setAttribute("aria-label", format(S.prRenameNamed, { name: preset.name }));
          const asking = confirming === key;
          const del = button(asking ? S.prConfirmDelete : S.prDelete, asking ? "pr-delete pr-danger" : "pr-delete", () => {
            if (confirming !== key) {
              cancelConfirm();
              confirming = key;
              confirmTimer = setTimeout(() => {
                cancelConfirm();
                render();
              }, 4000);
              render(key);
              return;
            }
            cancelConfirm();
            remove(preset);
          });
          del.setAttribute("aria-label", format(S.prDeleteNamed, { name: preset.name }));
          item.append(rename, del);
          if (focusKey === key) {
            queueMicrotask(() => del.focus());
          }
        }
        ul.append(item);
      }
      section.append(ul);
      list.append(section);
    }
  }

  async function refresh() {
    guard.bump();
    resetForm(false);
    cancelConfirm();
    if (controller) {
      controller.abort();
    }
    if (!currentCase) {
      presets = [];
      families = [];
      fillFamilies();
      render();
      return;
    }
    controller = new AbortController();
    try {
      const data = await api.presets.list(currentCase, controller.signal);
      presets = data.presets || [];
      families = data.families || [];
      const notes = [];
      if (data.invalid) {
        notes.push(S.prInvalidFile);
      }
      if (Array.isArray(data.problems) && data.problems.length > 0) {
        notes.push(format(S[pluralKey("prProblems", data.problems.length)], { count: data.problems.length }));
      }
      say(notes.join(" "), notes.length > 0);
    } catch (err) {
      if (err && err.name === "AbortError") {
        return;
      }
      presets = [];
      say(S.prLoadFailed + ": " + err.message, true);
      if (onError) {
        onError(err);
      }
    }
    fillFamilies();
    render();
  }

  async function remove(preset) {
    try {
      const done = await guard.run(currentCase, () => currentCase, () => api.presets.remove(preset.name, preset.family));
      if (done.stale) {
        await refresh(); // a reload started meanwhile may have read the list before this delete
        return;
      }
      if (renaming && renaming.name === preset.name && (renaming.family || "") === (preset.family || "")) {
        resetForm(true);
      }
      await refresh();
    } catch (err) {
      say(err.message, true);
      await refresh();
    }
  }

  async function exportAs(fmt) {
    try {
      const answer = await api.presets.export(fmt);
      if (!answer.count) {
        say(S.prExportEmpty, false);
        return;
      }
      const file = exportFile(answer);
      saveFile(fmt === "toml" ? S.prExportNameToml : S.prExportNameJson, file.text, file.type);
      say(format(S[pluralKey("prExported", answer.count)], { count: answer.count }), false);
    } catch (err) {
      say(err.message, true);
    }
  }

  fileInput.addEventListener("change", async () => {
    const file = fileInput.files && fileInput.files[0];
    fileInput.value = "";
    if (!file) {
      return;
    }
    const fmt = formatFromFileName(file.name);
    if (!fmt) {
      say(S.prImportBadFile, true);
      return;
    }
    try {
      const report = importSummary(await api.presets.import(fmt, importPayload(fmt, await file.text())));
      const lines = [format(S.prImported, report)];
      for (const item of report.ignoredItems.slice(0, 5)) {
        lines.push(format(S.prImportIgnoredItem, item));
      }
      say(lines.join(" "), report.ignored > 0 && report.added + report.updated === 0);
      await refreshKeepingMessage();
    } catch (err) {
      say(err.message, true);
    }
  });

  async function refreshKeepingMessage() {
    const text = message.textContent;
    const wasError = message.classList.contains("pr-error");
    await refresh();
    if (!message.textContent) {
      say(text, wasError);
    }
  }

  nameInput.addEventListener("input", () => {
    if (confirmed) {
      confirmed = null;
      paintForm();
    }
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (busy || !currentCase) {
      return;
    }
    const plan = planPresetSave({
      raw: nameInput.value,
      family: familySelect.value || null,
      presets,
      renaming,
      confirmed,
    });
    if (plan.action === "error") {
      say(problem(plan.error), true);
      return;
    }
    if (plan.action === "confirmOverwrite") {
      confirmed = { name: plan.name, family: plan.family };
      say(S.prOverwriteAsk, false);
      paintForm();
      return;
    }
    let filter = plan.target ? plan.target.filter : null;
    if (plan.action !== "rename") {
      filter = getFilter();
      if (!filter) {
        say(S.prNeedFilter, true);
        return;
      }
    }
    busy = true;
    const ticket = guard.begin(currentCase);
    try {
      if (plan.action === "create") {
        await api.presets.create(presetBody({ name: plan.name, family: plan.family, filter }));
        say(S.prSaved, false);
      } else {
        const target = plan.target;
        await api.presets.update(
          { name: target.name, family: target.family || null },
          presetBody({ name: plan.name, family: target.family, description: target.description, filter }),
        );
        say(plan.action === "rename" ? S.prRenamed : S.prOverwritten, false);
      }
      if (!guard.isCurrent(ticket, currentCase)) {
        await refresh(); // a reload started meanwhile may have read the list before this save wrote
        return;
      }
      nameInput.value = "";
      await refreshKeepingMessage();
    } catch (err) {
      say(err.message, true);
      if (plan.action !== "create") {
        await refresh();
      }
    } finally {
      busy = false;
    }
  });

  paintForm();
  fillFamilies();
  refresh();

  return {
    setCase(next) {
      currentCase = next;
      refresh();
    },
    refresh,
    destroy() {
      cancelConfirm();
      if (controller) {
        controller.abort();
      }
      container.replaceChildren();
    },
  };
}
