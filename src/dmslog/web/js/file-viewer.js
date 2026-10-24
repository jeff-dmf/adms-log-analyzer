// The read-only text viewer for files that are not indexed (css/file-viewer.css, js/file-viewer-model.js is the pure
// part). The text of the file is untrusted: it is written with textContent into a <pre>, never as HTML. Values that may
// be passwords arrive masked; the original ones are shown only after a click and only until the viewer closes.
import { api } from "./api.js";
import { label } from "./case-files-model.js";
import { ViewerState, factsText, hiddenText } from "./file-viewer-model.js";

const FOCUSABLE = "button";

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

// deps: {host, fetchText?, onMessage?, restoreFocus?}. fetchText(caseId, id, {reveal}, signal) defaults to api.sourceText.
// restoreFocus(opener): called when the opener left the page while the viewer was open.
export function mountFileViewer({ host, fetchText, onMessage, restoreFocus }) {
  const read = fetchText || ((caseId, id, options, signal) => api.sourceText(caseId, id, options, signal));
  const overlay = el("div", "fv-overlay");
  overlay.hidden = true;
  const panel = el("div", "fv-panel");
  panel.setAttribute("role", "dialog");
  panel.setAttribute("aria-modal", "true");
  panel.tabIndex = -1;
  const titleId = "fv-title";
  const title = el("h2", "fv-title");
  title.id = titleId;
  panel.setAttribute("aria-labelledby", titleId);
  const closeButton = el("button", "ui-button fv-close", label("fvClose", "Close"));
  closeButton.type = "button";
  const head = el("div", "fv-head");
  head.append(title, closeButton);
  const facts = el("div", "fv-facts");
  const factText = el("span", "fv-fact");
  const hiddenNote = el("span", "fv-fact fv-hidden-count");
  facts.append(factText, hiddenNote);
  const actions = el("div", "fv-actions");
  const revealButton = el("button", "ui-button fv-reveal", label("fvReveal", "Show hidden values"));
  revealButton.type = "button";
  revealButton.setAttribute("aria-pressed", "false");
  const copyButton = el("button", "ui-button fv-copy", label("fvCopy", "Copy"));
  copyButton.type = "button";
  actions.append(revealButton, copyButton);
  const warning = el("p", "fv-warning");
  warning.setAttribute("role", "note");
  warning.hidden = true;
  const status = el("p", "fv-status");
  status.setAttribute("role", "status");
  const pre = el("pre", "fv-text");
  pre.tabIndex = 0;
  pre.setAttribute("aria-label", label("fvTextLabel", "File content"));
  panel.append(head, facts, actions, warning, status, pre);
  overlay.appendChild(panel);
  host.appendChild(overlay);

  let opener = null;
  let open = false;

  const paint = (state) => {
    const file = state.file;
    title.textContent = state.source ? String(state.source.member || state.source.name || "") : "";
    const failed = state.error !== null && state.error !== undefined;
    if (state.phase === "loading") {
      status.textContent = label("fvLoading", "Loading…");
    } else if (failed) {
      status.textContent = state.error.text;
    } else {
      status.textContent = "";
    }
    const ready = state.phase === "ready" && file !== null;
    factText.textContent = ready ? factsText(file) : "";
    hiddenNote.textContent = ready ? hiddenText(file) : "";
    pre.hidden = !ready;
    pre.textContent = ready ? file.text : "";
    const canReveal = ready && state.masked.hidden > 0;
    revealButton.hidden = !canReveal;
    revealButton.setAttribute("aria-pressed", state.showing ? "true" : "false");
    revealButton.textContent = state.showing ? label("fvHide", "Hide values") : label("fvReveal", "Show hidden values");
    warning.hidden = !state.showing;
    warning.textContent = state.showing
      ? label("fvRevealWarning", "Hidden values may be passwords. They are shown only here and never stored or exported.")
      : "";
    copyButton.disabled = !ready;
  };
  const state = new ViewerState(read, paint);

  function focusables() {
    return Array.from(panel.querySelectorAll(FOCUSABLE)).filter((node) => !node.hidden && !node.disabled);
  }

  function close() {
    if (!open) {
      return;
    }
    open = false;
    state.reset(); // the original values, if shown, go with it
    overlay.hidden = true;
    pre.textContent = "";
    const target = opener;
    opener = null;
    if (target && target.isConnected !== false && typeof target.focus === "function") {
      target.focus();
    } else if (restoreFocus) {
      restoreFocus(target);
    }
  }

  overlay.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      if (typeof event.stopPropagation === "function") {
        event.stopPropagation();
      }
      close();
      return;
    }
    if (event.key === "Tab") {
      // The panel is modal: Tab cycles inside it (the <pre> is a tab stop too).
      const stops = [...focusables(), ...(pre.hidden ? [] : [pre])];
      if (stops.length === 0) {
        event.preventDefault();
        panel.focus();
        return;
      }
      const at = stops.indexOf(document.activeElement);
      const next = event.shiftKey ? (at <= 0 ? stops.length - 1 : at - 1) : at < 0 || at === stops.length - 1 ? 0 : at + 1;
      event.preventDefault();
      stops[next].focus();
    }
  });
  overlay.addEventListener("click", (event) => {
    if (event.target === overlay) {
      close();
    }
  });
  closeButton.addEventListener("click", () => close());
  revealButton.addEventListener("click", () => {
    state.toggleReveal();
  });
  copyButton.addEventListener("click", async () => {
    const file = state.file;
    if (!file || typeof navigator === "undefined" || !navigator.clipboard) {
      return;
    }
    try {
      await navigator.clipboard.writeText(file.text); // as shown: masked unless the user revealed it
      status.textContent = label("copied", "Copied");
    } catch (err) {
      if (onMessage) {
        onMessage(label("fvFailed", "The file could not be read."), true);
      }
    }
  });

  return {
    // Opens the viewer for a skipped source of the case; `from` is the element that gets the focus back.
    open(caseId, source, from) {
      if (open) {
        state.reset();
      } else {
        opener = from || null;
      }
      open = true;
      overlay.hidden = false;
      state.open(caseId, source);
      closeButton.focus();
    },
    close,
    isOpen: () => open,
    contains: (node) => overlay.contains(node),
    destroy() {
      state.reset();
      overlay.remove();
      open = false;
    },
  };
}
