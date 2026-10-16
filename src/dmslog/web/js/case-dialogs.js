// "New case" and "Add files" dialogs (UI-22, REC-22). Two native modal <dialog>s in the common frame (panel-shell.js):
// the focus is trapped, Escape closes, the focus goes back to the button that opened them. The forms are the ones that
// used to sit in the side column; they are moved into the dialog bodies the first time the dialogs are mounted, so the
// fields, the CaseFiles lists (progress, per-file results, Retry) and every handler keep working unchanged. The main
// action of each list sits in the foot. Loaded on demand (import()) by sources.js; nothing here is part of the opening.
import { createDialogShell } from "./panel-shell.js";
import { S } from "./strings.js";

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

// refs: {newCaseForm, newCaseName, openForm, pathInput, addFilesHost}. caseFiles / addFiles: the two CaseFiles lists.
// Options: host (where the <dialog>s go, default document.body), fallback() (focus target when the opener is gone),
// onClose(kind) after a dialog closed for any reason. Returns { open(kind, opener, {focusPath}), close(kind), closeAll(),
// isOpen(kind), openKind() } with kind "new" | "add".
export function mountCaseDialogs({ refs, caseFiles, addFiles, host = null, fallback = null, onClose = null }) {
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
