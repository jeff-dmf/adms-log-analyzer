// Saved filters of a case: list, apply, save the current filter, delete (two-step).
// All text from the server (names) is placed through textContent only.
import { MAX_NAME, MAX_VIEWS, checkViewName } from "./filter-tree-model.js";
import { S, format } from "./strings.js";

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
// onApply(filter) is called when the user picks a saved filter.
export function initSavedViews(container, { api, caseId, getFilter, onApply, onError }) {
  let currentCase = caseId;
  let views = [];
  let confirming = null;
  let confirmTimer = null;
  let controller = null;
  let busy = false;

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
  form.append(nameLabel, nameInput, saveButton);
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
      tooMany: format(S.svErrorTooMany, { limit: MAX_VIEWS }),
    };
    return map[error] || "";
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
      item.append(apply, date, del);
      list.append(item);
      if (focusId === view.id) {
        del.focus();
      }
    }
  }

  async function refresh() {
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
      await api.views.remove(currentCase, view.id);
      views = views.filter((v) => v.id !== view.id);
      say("", false);
      render();
    } catch (err) {
      say(err.message, true);
      await refresh();
    }
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (busy) {
      return;
    }
    const filter = getFilter();
    if (!currentCase || !filter) {
      say(S.svNeedFilter, true);
      return;
    }
    const check = checkViewName(nameInput.value, views.map((v) => v.name));
    if (check.error) {
      say(nameProblem(check.error), true);
      return;
    }
    busy = true;
    try {
      const made = await api.views.save(currentCase, check.name, filter);
      views.push(made);
      views.sort((a, b) => a.name.toLowerCase().localeCompare(b.name.toLowerCase()));
      nameInput.value = "";
      say(S.svSaved, false);
      render();
    } catch (err) {
      say(err.message, true);
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
