// The common frame of every dock panel and dialog (UI-23, docs/analises-ui/CONSOLIDADO.md REC-23):
//   .ui-panel        head (title h2, summary, actions: Refresh, Close) / tools (one row) / body (scrolls) / foot (main action)
//   .ui-dialog       head (title h2, Close) / body (scrolls) / foot (main action, always visible)
// A module renders into the host it was given and asks to be closed through `onRequestClose`; it never decides by
// itself that its host is hidden (the dock owns that: a late answer must not reopen a closed tab). Without an
// `onRequestClose` option the compatible default hides the host, which the dock notices through the attribute.
// Everything is written with textContent.
import { S, format } from "./strings.js";
import { dialogKeyAction, requestCloser } from "./panel-shell-model.js";

export function node(doc, tag, className, text) {
  const item = doc.createElement(tag);
  if (className) {
    item.className = className;
  }
  if (text !== undefined) {
    item.textContent = text;
  }
  return item;
}

function ghostButton(doc, className, text) {
  const button = node(doc, "button", "ui-button is-ghost " + className, text);
  button.type = "button";
  return button;
}

// host: the element the dock (or a test) gave the module. Options:
//   title                   visible title (h2)
//   onRequestClose()        what the Close button does (default: hides the host)
//   onRefresh()             when given, a Refresh button sits in the head, left of Close (the same place in every panel)
//   refreshLabel            default S.panelRefresh
// Returns { root, head, title, summary, actions, refreshButton, closeButton, tools, body, foot, addActions, addTools,
//           addFoot, requestClose }.
export function createPanelShell(host, { title, onRequestClose = null, onRefresh = null, refreshLabel = null }) {
  const doc = host.ownerDocument || document;
  host.textContent = "";
  const root = node(doc, "div", "ui-panel panel-shell");
  const head = node(doc, "div", "ui-panel-head");
  const titleNode = node(doc, "h2", "ui-panel-title", title);
  const summary = node(doc, "span", "ui-panel-summary");
  const actions = node(doc, "div", "ui-panel-actions");
  const requestClose = requestCloser(onRequestClose, () => {
    host.hidden = true;
  });
  let refreshButton = null;
  if (typeof onRefresh === "function") {
    refreshButton = ghostButton(doc, "panel-refresh", refreshLabel || S.panelRefresh);
    refreshButton.addEventListener("click", () => onRefresh());
    actions.appendChild(refreshButton);
  }
  const closeButton = ghostButton(doc, "panel-close", S.panelClose);
  closeButton.setAttribute("aria-label", format(S.panelCloseLabel, { title }));
  closeButton.addEventListener("click", requestClose);
  actions.appendChild(closeButton);
  head.append(titleNode, summary, actions);
  const tools = node(doc, "div", "ui-panel-tools");
  tools.hidden = true;
  const body = node(doc, "div", "ui-panel-body");
  const foot = node(doc, "div", "ui-panel-foot");
  foot.hidden = true;
  root.append(head, tools, body, foot);
  host.appendChild(root);
  return {
    root,
    head,
    title: titleNode,
    summary,
    actions,
    refreshButton,
    closeButton,
    tools,
    body,
    foot,
    requestClose,
    // Extra head buttons (Export, Import...) go before Refresh and Close, which keep their place in every panel.
    addActions(...nodes) {
      const first = actions.children[0] || null;
      for (const item of nodes) {
        actions.insertBefore(item, first);
      }
    },
    addTools(...nodes) {
      tools.append(...nodes);
      tools.hidden = false;
    },
    addFoot(...nodes) {
      foot.append(...nodes);
      foot.hidden = false;
    },
  };
}

// A status line of a panel: role=status, polite, `.ui-status`; `.ui-error` styling when it reports a failure.
export function statusLine(doc, className = "") {
  const line = node(doc, "div", ("ui-status " + className).trim());
  line.setAttribute("role", "status");
  line.setAttribute("aria-live", "polite");
  return line;
}

export function setStatusLine(line, text, isError) {
  line.textContent = text;
  line.classList.toggle("is-error", Boolean(isError));
  line.classList.toggle("ui-error", Boolean(isError));
}

// A modal dialog with the main action fixed in the foot. The native <dialog> gives the focus trap, Escape and the
// backdrop; where showModal() does not exist the dialog opens with the `open` attribute and handles Escape itself.
// Options: title, onClose() (after it closed, for any reason). Returns { dialog, head, body, foot, open(opener), close(),
// isOpen() }. Focus returns to `opener` (the element that had it) when the dialog closes.
export function createDialogShell(doc, { title, onClose = null, className = "" }) {
  const dialog = node(doc, "dialog", ("ui-dialog " + className).trim());
  const titleNode = node(doc, "h2", "ui-dialog-title", title);
  titleNode.tabIndex = -1;
  const head = node(doc, "div", "ui-dialog-head");
  const closeButton = ghostButton(doc, "dialog-close", S.panelClose);
  closeButton.setAttribute("aria-label", format(S.panelCloseLabel, { title }));
  head.append(titleNode, closeButton);
  const body = node(doc, "div", "ui-dialog-body");
  const foot = node(doc, "div", "ui-dialog-foot");
  dialog.setAttribute("aria-labelledby", (titleNode.id = "dlg-" + String(++dialogUid) + "-title"));
  dialog.append(head, body, foot);
  let opener = null;
  let shown = false;

  function finish() {
    if (!shown) {
      return;
    }
    shown = false;
    dialog.open = false;
    dialog.removeAttribute("open");
    const back = opener;
    opener = null;
    if (back && typeof back.focus === "function" && (typeof back.isConnected !== "boolean" || back.isConnected)) {
      back.focus();
    }
    if (typeof onClose === "function") {
      onClose();
    }
  }

  function close() {
    if (!shown) {
      return;
    }
    if (typeof dialog.close === "function") {
      dialog.close(); // fires "close" -> finish()
    }
    finish();
  }

  function open(from) {
    if (shown) {
      return;
    }
    opener = from || doc.activeElement || null;
    shown = true;
    if (typeof dialog.showModal === "function") {
      dialog.showModal();
    } else {
      dialog.open = true;
      dialog.setAttribute("open", "");
    }
    if (typeof titleNode.focus === "function") {
      titleNode.focus();
    }
  }

  closeButton.addEventListener("click", close);
  dialog.addEventListener("close", finish);
  dialog.addEventListener("cancel", (event) => {
    event.preventDefault(); // close() runs the same path as the button
    close();
  });
  dialog.addEventListener("keydown", (event) => {
    if (typeof dialog.showModal !== "function" && dialogKeyAction(event) === "close") {
      event.preventDefault();
      close();
    }
  });
  return { dialog, head, body, foot, titleNode, open, close, isOpen: () => shown };
}

let dialogUid = 0;
