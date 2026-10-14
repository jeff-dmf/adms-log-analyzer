// Saved filters of a case: list, apply, save the current filter, delete (two-step).
// All text from the server (names) is placed through textContent only.
import { andFilters } from "./filters.js";
import { MAX_NAME, MAX_VIEWS } from "./filter-tree-model.js";
import { createViewsGuard, planSave } from "./saved-views-model.js";
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

// getFilter() -> the current valid filter JSON, or null when none/invalid.
// onApply(filter) is called when the user picks a saved filter. Optional: getExtraFilter() returns
// the toolbar filters (severity, level, time, search, toggles) as a filter or null; when given, a
// checkbox lets the user store them together with the tree. A saved name that exists is overwritten
// after a second press; "Rename" reuses the name field.
export function initSavedViews(container, { api, caseId, getFilter, getExtraFilter, onApply, onError }) {
  let currentCase = caseId;
  let views = [];
  let confirming = null;
  let confirmTimer = null;
  let controller = null;
  let busy = false;
  const guard = createViewsGuard();
  let renamingId = null;
  let confirmedName = null;

  container.classList.add("sv-panel");
  const title = el("h3", "sv-title", S.svTitle);
  const form = el("form", "sv-form");
  const nameId = "sv-name-" + Math.random().toString(36).slice(2, 8);
  const nameLabel = el("label", "visually-hidden", S.svName);
  nameLabel.htmlFor = nameId;
  const nameInput = el("input");
  nameInput.id = nameId;
  nameInput.type = "text";
  nameInput.autocomplete = "off";
  nameInput.placeholder = S.svNamePlaceholder;
  nameInput.maxLength = MAX_NAME * 2; // generous; the sanitized length is checked
  const saveButton = el("button", "", S.svSave);
  saveButton.type = "submit";
  const cancelButton = el("button", "", S.svCancel);
  cancelButton.type = "button";
  cancelButton.hidden = true;
  form.append(nameLabel, nameInput, saveButton, cancelButton);
  let toolbarBox = null;
  if (getExtraFilter) {
    const toolbarId = "sv-toolbar-" + Math.random().toString(36).slice(2, 8);
    toolbarBox = el("input");
    toolbarBox.type = "checkbox";
    toolbarBox.id = toolbarId;
    const toolbarLabel = el("label", "sv-toolbar-option", S.svIncludeToolbar);
    toolbarLabel.htmlFor = toolbarId;
    form.append(toolbarBox, toolbarLabel);
  }
  const message = el("div", "sv-message");
  message.setAttribute("role", "status");
  message.setAttribute("aria-live", "polite");
  const list = el("ul", "sv-list");
  list.setAttribute("aria-label", S.svTitle);
  container.append(title, form, message, list);

  function say(text, isError) {
    message.textContent = text;
    message.classList.toggle("sv-error", Boolean(isError));
  }

  function nameProblem(error) {
    const map = {
      empty: S.svErrorEmpty,
      tooLong: format(S.svErrorTooLong, { limit: MAX_NAME }),
      duplicate: S.svErrorDuplicate,
      tooMany: format(S[pluralKey("svErrorTooMany", MAX_VIEWS)], { limit: MAX_VIEWS }),
    };
    return map[error] || "";
  }

  function paintForm() {
    saveButton.textContent = renamingId !== null ? S.svRename : confirmedName !== null ? S.svOverwrite : S.svSave;
    cancelButton.hidden = renamingId === null && confirmedName === null;
  }

  function resetForm() {
    renamingId = null;
    confirmedName = null;
    paintForm();
  }

  function cancelConfirm() {
    confirming = null;
    if (confirmTimer !== null) {
      clearTimeout(confirmTimer);
      confirmTimer = null;
    }
  }

  function render(focusId) {
    list.replaceChildren();
    if (views.length === 0) {
      const empty = el("li", "sv-empty", S.svEmpty);
      list.append(empty);
      return;
    }
    for (const view of views) {
      const item = el("li", "sv-item");
      const apply = el("button", "sv-apply", view.name);
      apply.type = "button";
      apply.setAttribute("aria-label", format(S.svApply, { name: view.name }));
      apply.addEventListener("click", () => {
        cancelConfirm();
        if (view.filter) {
          onApply(view.filter);
        }
      });
      const date = el("span", "sv-date", format(S.svCreated, { date: String(view.created_at || "").slice(0, 10) }));
      const asking = confirming === view.id;
      const del = el("button", asking ? "sv-delete sv-danger" : "sv-delete", asking ? S.svConfirmDelete : S.svDelete);
      del.type = "button";
      del.setAttribute("aria-label", format(S.svDeleteNamed, { name: view.name }));
      del.addEventListener("click", () => {
        if (confirming !== view.id) {
          cancelConfirm();
          confirming = view.id;
          confirmTimer = setTimeout(() => {
            cancelConfirm();
            render();
          }, 4000);
          render(view.id);
          return;
        }
        cancelConfirm();
        remove(view);
      });
      const rename = el("button", "sv-rename", S.svRename);
      rename.type = "button";
      rename.setAttribute("aria-label", format(S.svRenameNamed, { name: view.name }));
      rename.addEventListener("click", () => {
        cancelConfirm();
        renamingId = view.id;
        confirmedName = null;
        nameInput.value = view.name;
        nameInput.focus();
        nameInput.select();
        say(S.svRenaming, false);
        paintForm();
      });
      item.append(apply, date, rename, del);
      list.append(item);
      if (focusId === view.id) {
        del.focus();
      }
    }
  }

  async function refresh() {
    guard.bump();
    resetForm();
    if (controller) {
      controller.abort();
    }
    cancelConfirm();
    if (!currentCase) {
      views = [];
      render();
      return;
    }
    controller = new AbortController();
    try {
      const data = await api.views.list(currentCase, controller.signal);
      views = data.views || [];
      say("", false);
    } catch (err) {
      if (err && err.name === "AbortError") {
        return;
      }
      views = [];
      say(S.svLoadFailed + ": " + err.message, true);
      if (onError) {
        onError(err);
      }
    }
    render();
  }

  async function remove(view) {
    try {
      const done = await guard.run(currentCase, () => currentCase, () => api.views.remove(currentCase, view.id));
      if (done.stale) {
        return;
      }
      views = views.filter((v) => v.id !== view.id);
      if (renamingId === view.id) {
        resetForm();
      }
      say("", false);
      render();
    } catch (err) {
      say(err.message, true); // a failure of the current list (stale ones never get here)
      await refresh();
    }
  }

  nameInput.addEventListener("input", () => {
    if (confirmedName !== null) {
      confirmedName = null;
      paintForm();
    }
  });
  cancelButton.addEventListener("click", () => {
    resetForm();
    nameInput.value = "";
    say("", false);
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (busy) {
      return;
    }
    const plan = planSave({ raw: nameInput.value, views, renamingId, confirmedName });
    if (plan.action === "error") {
      say(nameProblem(plan.error), true);
      return;
    }
    if (plan.action === "confirmOverwrite") {
      confirmedName = plan.name;
      say(S.svOverwriteAsk, false);
      paintForm();
      return;
    }
    let filter = null;
    if (plan.action !== "rename") {
      const tree = getFilter();
      const extra = toolbarBox && toolbarBox.checked ? getExtraFilter() : null;
      filter = andFilters(tree, extra);
      if (!currentCase || !filter) {
        say(S.svNeedFilter, true);
        return;
      }
    }
    if (!currentCase) {
      return;
    }
    busy = true;
    const ticket = guard.begin(currentCase);
    try {
      if (plan.action === "create") {
        const made = await api.views.save(currentCase, plan.name, filter);
        if (!guard.isCurrent(ticket, currentCase)) {
          return;
        }
        views.push(made);
        say(S.svSaved, false);
      } else {
        const old = views.find((v) => v.id === plan.id);
        const updated = await api.views.update(
          currentCase, plan.id, plan.name, plan.action === "rename" ? old.filter : filter, old.columns);
        if (!guard.isCurrent(ticket, currentCase)) {
          return;
        }
        views = views.map((v) => (v.id === plan.id ? updated : v));
        say(plan.action === "rename" ? S.svRenamed : S.svOverwritten, false);
      }
      views.sort((a, b) => a.name.toLowerCase().localeCompare(b.name.toLowerCase()));
      nameInput.value = "";
      resetForm();
      render();
    } catch (err) {
      if (!guard.isCurrent(ticket, currentCase)) {
        return;
      }
      say(err.message, true);
      if (plan.action !== "create") {
        await refresh();
      }
    } finally {
      busy = false;
    }
  });

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
