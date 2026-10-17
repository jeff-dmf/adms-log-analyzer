// "New case" and "Add files" dialogs (UI-22, REC-22). Two native modal <dialog>s in the common frame (panel-shell.js):
// the focus is trapped, Escape closes, the focus goes back to the button that opened them. The forms are the ones that
// used to sit in the side column; they are moved into the dialog bodies the first time the dialogs are mounted, so the
// fields, the CaseFiles lists (progress, per-file results, Retry) and every handler keep working unchanged. The main
// action of each list sits in the foot. Loaded on demand (import()) by sources.js; nothing here is part of the opening.
import { createDialogShell } from "./panel-shell.js";
import { S } from "./strings.js";

let slotUid = 0;

function button(doc, label, className, handler) {
  const node = doc.createElement("button");
  node.type = "button";
  node.className = className;
  node.textContent = label;
  node.addEventListener("click", handler);
  return node;
}

// True when the node or an ancestor has the `hidden` attribute (a hidden element cannot take the focus).
export function isHiddenChain(node) {
  for (let item = node; item; item = item.parentNode || item.parent) {
    if (item.hidden === true) {
      return true;
    }
  }
  return false;
}

// What gets the focus when a dialog closes: the element that opened it, when it is still on the page and shown;
// otherwise `fallback()` (for example the opener was a button of the empty state, which a new case just hid).
export function focusReturn(opener, fallback) {
  const usable = (node) => Boolean(node) && typeof node.focus === "function" && node.isConnected !== false && !isHiddenChain(node);
  return {
    get isConnected() {
      return true;
    },
    focus() {
      if (usable(opener)) {
        opener.focus();
        if (typeof document === "undefined" || document.activeElement === opener) {
          return;
        }
      }
      const other = typeof fallback === "function" ? fallback() : null;
      if (usable(other)) {
        other.focus();
      }
    },
  };
}

// Visible labels in a dialog (the side column had them visually hidden next to a placeholder).
function showLabels(root) {
  for (const label of root.querySelectorAll("label")) {
    label.classList.remove("visually-hidden");
  }
}

// "Open only part first" (M9 9.9) inside the Add files dialog: a checkbox that, when turned on, loads partial-open.js
// on demand (import(); a failed load shows Try again and is tried again, never a dead end) and shows its body. While it
// is off, or not loaded, nothing narrows the opening. Options: getPaths(), getCaseId(), getScope(), loadModule() (default:
// import("./partial-open.js")), scopeSelect (a change of scope invalidates the preview).
// The slot owns the dialog's one live region (the status and the errors of the section are spoken through it).
// Returns { node, wanted(), ready() -> Promise<boolean>, validate(paths, scope), snapshot(paths, scope), refresh(),
// reset(), showServerError(text) }: `paths`/`scope` are the files and the scope of the action being started.
function createPartialSlot(doc, { getPaths, getCaseId, getScope, loadModule, scopeSelect, onMessage }) {
  const node = doc.createElement("div");
  node.className = "po-slot";
  const row = doc.createElement("label");
  row.className = "po-toggle";
  const toggle = doc.createElement("input");
  toggle.type = "checkbox";
  toggle.className = "po-toggle-box";
  toggle.checked = false;
  const toggleText = doc.createElement("span");
  toggleText.textContent = S.poToggle;
  row.append(toggle, toggleText);
  const body = doc.createElement("div");
  body.className = "po-body";
  body.id = "po-body-" + String(++slotUid);
  body.hidden = true;
  toggle.setAttribute("aria-controls", body.id);
  toggle.setAttribute("aria-expanded", "false");
  const note = doc.createElement("p");
  note.className = "po-load-note";
  const live = doc.createElement("p");
  live.className = "po-live visually-hidden";
  live.setAttribute("role", "status");
  live.setAttribute("aria-live", "polite");
  const announce = (text) => {
    live.textContent = String(text || "");
  };
  const retry = button(doc, S.poRetry, "ui-button po-retry", () => load());
  retry.hidden = true;
  const content = doc.createElement("div");
  content.className = "po-content";
  body.append(note, retry, content);
  node.append(row, body, live);

  let controller = null;
  let loading = null; // shared promise of the load; reset by a failure so the next try imports again
  let generation = 0; // a reset (case switch) drops a load that was still running

  function load() {
    if (controller) {
      return Promise.resolve(true);
    }
    if (!loading) {
      const mine = generation;
      note.textContent = S.poLoading;
      announce(S.poLoading);
      retry.hidden = true;
      const attempt = Promise.resolve().then(() => loadModule()).then((mod) => {
        if (mine !== generation) {
          return false;
        }
        controller = mod.mountPartialBody({ host: content, getCaseId, getPaths, getScope, announce });
        controller.refresh();
        note.textContent = "";
        const wasOnRetry = doc.activeElement === retry;
        retry.hidden = true;
        if (wasOnRetry || isHiddenChain(doc.activeElement)) {
          controller.focusFirst();
        }
        return true;
      });
      loading = attempt;
      attempt.catch(() => {
        if (loading === attempt) {
          loading = null;
        }
        if (mine === generation) {
          note.textContent = S.poLoadFailed;
          announce(S.poLoadFailed);
          retry.hidden = false;
        }
      });
    }
    return loading.then((ok) => ok, () => false);
  }

  toggle.addEventListener("change", () => {
    body.hidden = !toggle.checked;
    toggle.setAttribute("aria-expanded", toggle.checked ? "true" : "false");
    if (toggle.checked) {
      load();
    }
  });
  if (scopeSelect) {
    scopeSelect.addEventListener("change", () => {
      if (controller) {
        controller.invalidate(S.poScopeChanged);
      }
    });
  }

  return {
    node,
    wanted: () => toggle.checked,
    ready: () => (toggle.checked ? load() : Promise.resolve(true)),
    validate(paths, scope) {
      if (!controller) {
        onMessage(S.poNotReady, true);
        return { ok: false, message: S.poNotReady };
      }
      return controller.validate(paths, scope);
    },
    snapshot: (paths, scope) => (controller && toggle.checked ? controller.snapshot(paths, scope) : null),
    refresh() {
      if (controller) {
        controller.refresh();
      }
    },
    showServerError(text) {
      if (controller) {
        controller.showServerError(text);
      }
    },
    // Back to the closed state: a case switch or a finished opening. A load in flight is dropped.
    reset() {
      generation += 1;
      loading = null;
      if (controller) {
        controller.reset();
      }
      const wasInside = body.contains(doc.activeElement);
      toggle.checked = false;
      toggle.setAttribute("aria-expanded", "false");
      body.hidden = true;
      if (wasInside) {
        toggle.focus();
      }
      note.textContent = "";
      announce("");
      retry.hidden = true;
    },
  };
}

// refs: {newCaseForm, newCaseName, openForm, pathInput, addFilesHost}. caseFiles / addFiles: the two CaseFiles lists.
// Options: host (where the <dialog>s go, default document.body), fallback() (focus target when the opener is gone),
// onClose(kind) after a dialog closed for any reason. Returns { open(kind, opener, {focusPath}), close(kind), closeAll(),
// isOpen(kind), openKind() } with kind "new" | "add".
export function mountCaseDialogs({
  refs, caseFiles, addFiles, host = null, fallback = null, onClose = null, getCaseId = () => null,
  getScope = () => "default", loadPartial = () => import("./partial-open.js"), onMessage = () => {},
}) {
  const parent = host || document.body;
  const doc = parent.ownerDocument || document;
  const closed = (kind) => () => {
    if (typeof onClose === "function") {
      onClose(kind);
    }
  };

  // -- New case: name field and the ERS files list; Create case in the foot (still the form's submit button)
  const newShell = createDialogShell(doc, { title: S.cdNewTitle, className: "case-dialog case-dialog-new", onClose: closed("new") });
  const newForm = refs.newCaseForm;
  showLabels(newForm);
  newForm.hidden = false;
  newShell.body.appendChild(newForm);
  newShell.foot.appendChild(button(doc, S.cfCancel, "ui-button case-dialog-cancel", () => newShell.close()));
  caseFiles.placeAction(newShell.foot, newForm.id || null);
  parent.appendChild(newShell.dialog);

  // -- Add files: the list first (Add to list, then "Add N files to case" in the foot), then "Folder or single file"
  const addShell = createDialogShell(doc, { title: S.cdAddTitle, className: "case-dialog case-dialog-add", onClose: closed("add") });
  const addForm = refs.openForm;
  showLabels(addForm);
  addForm.hidden = false;
  const heading = doc.createElement("h3");
  heading.className = "cd-section-title";
  heading.textContent = S.cdFolderSection;
  addForm.insertBefore(heading, addForm.children[0] || null);
  addForm.insertBefore(refs.addFilesHost, heading); // the list goes above the "Folder or single file" section
  refs.addFilesHost.hidden = false;
  addShell.body.appendChild(addForm);
  // Open only part first: after the folder section (its Open button and the list's main action both honour it)
  const partial = createPartialSlot(doc, {
    getPaths: () => [...addFiles.state.items.map((item) => item.path), addFiles.pathInput.value, refs.pathInput.value],
    getCaseId,
    getScope,
    loadModule: loadPartial,
    scopeSelect: refs.scopeSelect || null,
    onMessage,
  });
  addForm.appendChild(partial.node);
  // The files of the dialog change (list edited, a path typed): the section marks previews of files that left.
  for (const input of [addFiles.pathInput, refs.pathInput]) {
    input.addEventListener("input", () => partial.refresh());
  }
  const listChanged = addFiles.state.onChange;
  addFiles.state.onChange = (state) => {
    listChanged(state);
    partial.refresh();
  };
  addShell.foot.appendChild(button(doc, S.cfCancel, "ui-button case-dialog-cancel", () => addShell.close()));
  addFiles.placeAction(addShell.foot);
  parent.appendChild(addShell.dialog);

  const shells = { new: newShell, add: addShell };

  function open(kind, opener, { focusPath = false } = {}) {
    const shell = shells[kind];
    if (!shell || shell.isOpen()) {
      return false;
    }
    for (const other of Object.keys(shells)) {
      if (other !== kind) {
        shells[other].close();
      }
    }
    shell.open(focusReturn(opener, fallback));
    // The field that is useful first. A control that cannot take the focus (disabled while a queue runs) leaves it on
    // the dialog title, never on the page behind.
    if (kind === "new") {
      if (focusPath) {
        caseFiles.focusPath();
      } else if (refs.newCaseName && !refs.newCaseName.disabled) {
        refs.newCaseName.focus();
      }
    } else if (focusPath) {
      refs.pathInput.focus();
    } else {
      addFiles.focusPath();
    }
    return true;
  }

  return {
    open,
    partial,
    close(kind) {
      if (shells[kind]) {
        shells[kind].close();
      }
    },
    closeAll() {
      newShell.close();
      addShell.close();
    },
    isOpen: (kind) => Boolean(shells[kind]) && shells[kind].isOpen(),
    openKind: () => (newShell.isOpen() ? "new" : addShell.isOpen() ? "add" : null),
  };
}
