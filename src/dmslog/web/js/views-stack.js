// Chained views UI (ROADMAP 9.5, reshaped by UI-18): the left and right ends of the filter strip. Left: Back/Forward
// icon buttons and the trail (nav > ol, the active view with aria-current="page"). Right: "Narrow this view" and the
// "..." menu (Rename, Show in parent, Show in root). The chips and the "Filters" popover are filter-chips.js. The
// model is views-stack-model.js; everything written to the page is text (names may contain user text).
import { S, format, pluralKey } from "./strings.js";
import { createMenu } from "./ui-menu.js";
import { MAX_NAME, loadStack, saveStack } from "./views-stack-model.js";

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

// options: trailHost (left end of the strip), actionsHost (right end),
//   stack (createViewsStack), getCase, restoreState(state), applyView() -> Promise, getSelectedRow(),
//   reveal(row) -> Promise, showMessage(text, isError), storage (sessionStorage or null),
//   createMenu (tests only: a stand-in for ui-menu.js createMenu),
//   group (optional, popover-group.js: one popover open at a time with the strip's popovers).
export function mountViewsStack(options) {
  const { stack, getCase, restoreState, applyView, getSelectedRow, reveal, showMessage } = options;
  const storage = options.storage || null;

  const backButton = button("vs-back ui-button is-ghost", "\u2190", S.vsBackTitle);
  backButton.setAttribute("aria-label", S.vsBack);
  const forwardButton = button("vs-forward ui-button is-ghost", "\u2192", S.vsForwardTitle);
  forwardButton.setAttribute("aria-label", S.vsForward);
  const crumbs = element("nav", "vs-crumbs");
  crumbs.setAttribute("aria-label", S.vsCrumbsLabel);
  const trail = element("ol", "vs-trail");
  crumbs.appendChild(trail);
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
  const narrowButton = button("vs-narrow ui-button is-ghost", S.vsNarrow, S.vsNarrowTitle);
  const moreButton = button("vs-more ui-button is-ghost", "\u22ef", S.vsMoreTitle);
  moreButton.setAttribute("aria-label", S.vsMoreLabel);
  options.trailHost.append(backButton, forwardButton, crumbs, renameForm);
  options.actionsHost.append(narrowButton, moreButton);

  let seq = 0; // a newer view change makes a pending "show record" obsolete

  function paint() {
    trail.textContent = "";
    for (const crumb of stack.crumbs()) {
      const entry = element("li", "vs-item");
      const item = button("vs-crumb" + (crumb.active ? " is-active" : "") + (crumb.future ? " is-future" : ""), crumb.name);
      if (crumb.active) {
        item.setAttribute("aria-current", "page");
      }
      item.addEventListener("click", () => goTo(crumb.index));
      entry.appendChild(item);
      trail.appendChild(entry);
    }
    backButton.disabled = !stack.canBack();
    forwardButton.disabled = !stack.canForward();
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

  const rename = () => {
    renameForm.hidden = false;
    renameInput.value = stack.name(stack.index);
    renameInput.focus();
  };
  backButton.addEventListener("click", back);
  forwardButton.addEventListener("click", forward);
  narrowButton.addEventListener("click", narrow);
  let menu = null;
  const member = options.group ? options.group.join("views-menu", () => menu && menu.close(false)) : null;
  menu = (options.createMenu || createMenu)({
    trigger: moreButton,
    doc: document,
    label: S.vsMoreLabel,
    onOpenChange: (isOpen) => {
      if (member) {
        if (isOpen) {
          member.opened(); // closes the Filters or "+N more" popover if one is open
        } else {
          member.closed();
        }
      }
    },
    getItems: () => [
      { id: "rename", label: S.vsRename, run: rename },
      { id: "show-parent", label: S.vsShowParent, disabled: stack.isRoot, title: stack.isRoot ? S.vsAtRoot : null, run: () => showSelected(stack.index - 1) },
      { id: "show-root", label: S.vsShowRoot, disabled: stack.isRoot, title: stack.isRoot ? S.vsAtRoot : null, run: () => showSelected(0) },
    ],
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

  // Alt+Left / Alt+Right belong to the action registry (views.back / views.forward, shortcuts.js).

  paint();
  return {
    changed,
    narrow,
    back,
    forward,
    goTo,
    rename,
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
