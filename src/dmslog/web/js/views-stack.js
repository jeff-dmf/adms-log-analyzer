// Chained views UI (ROADMAP 9.5): breadcrumbs above the grid, back/forward, "Narrow this view",
// rename, and "Show in parent" / "Show in root" for the selected record. The model is
// views-stack-model.js; everything written to the page is text (names may contain user text).
import { ignoresShortcut } from "./filters.js";
import { S, format, pluralKey } from "./strings.js";
import { MAX_NAME, loadStack, saveStack, viewsKey } from "./views-stack-model.js";

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

function button(className, text, title) {
  const node = element("button", className, text);
  node.type = "button";
  if (title) {
    node.title = title;
  }
  return node;
}

// options: parent, beforePanel (node the bar goes before), footer (shortcut help goes after it),
//   stack (createViewsStack), getCase, restoreState(state), applyView() -> Promise, getSelectedRow(),
//   reveal(row) -> Promise, showMessage(text, isError), storage (sessionStorage or null).
export function mountViewsStack(options) {
  const { stack, getCase, restoreState, applyView, getSelectedRow, reveal, showMessage } = options;
  const storage = options.storage || null;

  const bar = element("div", "vs-bar");
  bar.id = "views-stack-bar";
  bar.setAttribute("role", "group");
  bar.setAttribute("aria-label", S.vsBarLabel);
  const backButton = button("vs-back", S.vsBack, S.vsBackTitle);
  const forwardButton = button("vs-forward", S.vsForward, S.vsForwardTitle);
  const crumbs = element("nav", "vs-crumbs");
  crumbs.setAttribute("aria-label", S.vsCrumbsLabel);
  const narrowButton = button("vs-narrow", S.vsNarrow, S.vsNarrowTitle);
  const renameButton = button("vs-rename", S.vsRename);
  const parentButton = button("vs-show-parent", S.vsShowParent);
  const rootButton = button("vs-show-root", S.vsShowRoot);
  const renameForm = element("form", "vs-rename-form");
  renameForm.hidden = true;
  const renameInput = element("input", "vs-rename-input");
  renameInput.type = "text";
  renameInput.id = "vs-rename-input";
  renameInput.maxLength = MAX_NAME;
  renameInput.autocomplete = "off";
  const renameLabel = element("label", "visually-hidden", S.vsRenameLabel);
  renameLabel.htmlFor = renameInput.id;
  const renameOk = button("vs-rename-ok", S.vsRenameOk);
  renameOk.type = "submit";
  const renameCancel = button("vs-rename-cancel", S.vsRenameCancel);
  renameForm.append(renameLabel, renameInput, renameOk, renameCancel);
  bar.append(backButton, forwardButton, crumbs, narrowButton, renameButton, parentButton, rootButton, renameForm);
  options.parent.insertBefore(bar, options.beforePanel || null);

  const help = element("div", "shortcuts-help", S.vsShortcutsHelp);
  if (options.footer && options.footer.parentNode) {
    options.footer.parentNode.insertBefore(help, options.footer.nextSibling);
  }

  let seq = 0; // a newer view change makes a pending "show record" obsolete

  function paint() {
    crumbs.textContent = "";
    const list = stack.crumbs();
    list.forEach((crumb, i) => {
      if (i > 0) {
        const sep = element("span", "vs-sep", "›");
        sep.setAttribute("aria-hidden", "true");
        crumbs.appendChild(sep);
      }
      const item = button("vs-crumb" + (crumb.active ? " is-active" : "") + (crumb.future ? " is-future" : ""), crumb.name);
      if (crumb.active) {
        item.setAttribute("aria-current", "true");
      }
      item.addEventListener("click", () => goTo(crumb.index));
      crumbs.appendChild(item);
    });
    backButton.disabled = !stack.canBack();
    forwardButton.disabled = !stack.canForward();
    parentButton.disabled = stack.isRoot;
    rootButton.disabled = stack.isRoot;
  }

  function persist() {
    const caseId = getCase();
    if (storage && caseId !== null) {
      saveStack(storage, caseId, stack);
    }
  }

  // Called after any change of the live state (and after every change of view).
  function changed() {
    paint();
    persist();
  }

  function guardCase() {
    if (getCase() === null) {
      showMessage(S.vsNeedCase, true);
      return false;
    }
    return true;
  }

  // Active view -> view i; the toolbar takes that view's state and the grid reloads.
  function goTo(i) {
    renameForm.hidden = true;
    const state = stack.goTo(i);
    if (state === null) {
      return null;
    }
    seq += 1;
    restoreState(state);
    const loaded = applyView();
    changed();
    return loaded;
  }

  function narrow() {
    if (!guardCase()) {
      return;
    }
    const result = stack.narrow();
    if (!result.ok) {
      showMessage(result.reason === "full" ? format(S[pluralKey("vsFull", stack.maxDepth)], { n: stack.maxDepth }) : S.vsNeedFilter, result.reason === "full");
      return;
    }
    showMessage("", false);
    renameForm.hidden = true;
    seq += 1;
    restoreState(result.state);
    applyView();
    changed();
  }

  const back = () => (stack.canBack() ? goTo(stack.index - 1) : null);
  const forward = () => (stack.canForward() ? goTo(stack.index + 1) : null);

  // "Show in parent" / "Show in root": leave the child view keeping the selected record selected.
  async function showSelected(target) {
    if (!guardCase()) {
      return;
    }
    if (stack.isRoot) {
      showMessage(S.vsAtRoot, false);
      return;
    }
    const row = getSelectedRow();
    if (!row) {
      showMessage(S.navNoSelection, false);
      return;
    }
    showMessage("", false);
    const loaded = goTo(target);
    const mine = seq;
    try {
      await loaded;
      if (mine !== seq) {
        return;
      }
      await reveal({ id: row.id, ts: row.ts });
    } catch (err) {
      // The record may be hidden by a toggle of the parent view: the grid keeps its first page.
    }
  }

  backButton.addEventListener("click", back);
  forwardButton.addEventListener("click", forward);
  narrowButton.addEventListener("click", narrow);
  parentButton.addEventListener("click", () => showSelected(stack.index - 1));
  rootButton.addEventListener("click", () => showSelected(0));
  renameButton.addEventListener("click", () => {
    renameForm.hidden = !renameForm.hidden;
    if (!renameForm.hidden) {
      renameInput.value = stack.name(stack.index);
      renameInput.focus();
    }
  });
  renameForm.addEventListener("submit", (event) => {
    event.preventDefault();
    stack.rename(stack.index, renameInput.value);
    renameForm.hidden = true;
    changed();
  });
  renameCancel.addEventListener("click", () => {
    renameForm.hidden = true;
  });
  renameInput.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      renameForm.hidden = true;
      event.stopPropagation();
    }
  });

  document.addEventListener("keydown", (event) => {
    const action = viewsKey(event);
    if (!action || ignoresShortcut(event) || getCase() === null) {
      return;
    }
    event.preventDefault(); // Alt+Left/Right would otherwise leave the app (browser history)
    if (action === "back") {
      back();
    } else {
      forward();
    }
  });

  paint();
  return {
    changed,
    narrow,
    back,
    forward,
    goTo,
    showInParent: () => showSelected(stack.index - 1),
    showInRoot: () => showSelected(0),
    // New case: the stack starts from the one saved for it in this tab, if any. Returns the live
    // state to apply (or null) and repaints.
    onCase(caseId) {
      stack.reset();
      renameForm.hidden = true;
      seq += 1;
      const state = storage && caseId !== null ? loadStack(storage, caseId, stack) : null;
      paint();
      return state;
    },
  };
}
