// Status bar (UI-13): the fixed 22 px footer. Segments: count, position, indexing progress, two slots (clock
// warning, alerts), the message and the help button. A global error (connection) swaps the contents of the bar
// itself; nothing is ever laid over the filter strip, and showing or hiding a message never moves another element
// (the bar has a fixed height and the message has its own fixed segment). Contract: docs/notas/ui-contratos.md, 3.
// Every text is set with textContent; every visible text comes from strings.js.
import { createMessageState, formatCount, isConnectionText, normalizeKind } from "./status-bar-model.js";
import { format } from "./strings.js";
import { formatTs } from "./time.js";

// root: the #status-bar footer. Options:
//   S                  the string catalog
//   helpText()         the text shown by the "?" button until the shortcuts dialog exists (UI-28)
//   now, setTimer, clearTimer   injected clock and timers (tests)
export function createStatusBar(root, { S, helpText = () => "", now = () => Date.now(), setTimer, clearTimer } = {}) {
  const doc = root.ownerDocument;
  const arm = setTimer || ((fn, ms) => setTimeout(fn, ms));
  const disarm = clearTimer || ((id) => clearTimeout(id));
  const make = (tag, className, text) => {
    const node = doc.createElement(tag);
    if (className) {
      node.className = className;
    }
    if (text !== undefined) {
      node.textContent = text;
    }
    return node;
  };

  root.textContent = "";
  root.classList.add("ui-statusbar");
  root.setAttribute("aria-label", S.statusLabel);

  const countEl = make("span", "sb-seg sb-count");
  const positionEl = make("span", "sb-seg sb-position");
  const progressBox = make("span", "sb-seg sb-progress");
  const progressEl = make("progress", "sb-progress-bar");
  progressEl.setAttribute("aria-label", S.progressLabel);
  const progressText = make("span", "sb-progress-text");
  progressBox.append(progressEl, progressText);
  const slots = { clock: make("span", "sb-slot sb-slot-clock"), alerts: make("span", "sb-slot sb-slot-alerts") };
  const slotContent = {};
  for (const [id, wrapper] of Object.entries(slots)) {
    slotContent[id] = make("div", "sb-slot-content");
    slotContent[id].hidden = true; // the owning module shows it when it has something to say
    wrapper.appendChild(slotContent[id]);
  }
  // The only live region of messages (polite); the hidden announcer speaks the count only on request.
  const messageEl = make("span", "sb-seg sb-message");
  messageEl.setAttribute("role", "status");
  messageEl.setAttribute("aria-live", "polite");
  const messageText = make("span", "sb-message-text");
  const dismissButton = make("button", "ui-button is-ghost sb-button sb-dismiss", S.sbDismiss);
  dismissButton.type = "button";
  messageEl.append(messageText, dismissButton);
  const announcer = make("span", "visually-hidden sb-announcer");
  announcer.setAttribute("aria-live", "polite");
  announcer.setAttribute("aria-atomic", "true");
  const helpButton = make("button", "ui-button is-ghost sb-button sb-help", S.sbHelp);
  helpButton.type = "button";
  helpButton.setAttribute("aria-label", S.actShortcuts);
  helpButton.setAttribute("aria-expanded", "false");
  helpButton.setAttribute("aria-controls", "sb-help-pop");
  const helpPop = make("div", "sb-help-pop");
  helpPop.id = "sb-help-pop";
  helpPop.hidden = true;
  // Global error: replaces the segments while it lasts.
  const errorEl = make("span", "sb-error");
  errorEl.setAttribute("role", "alert");
  errorEl.hidden = true;
  const errorText = make("span", "sb-error-text");
  const retryButton = make("button", "ui-button sb-button sb-retry", S.sbRetry);
  retryButton.type = "button";
  retryButton.hidden = true;
  errorEl.append(errorText, retryButton);

  root.append(countEl, positionEl, progressBox, slots.clock, slots.alerts, messageEl, announcer, helpButton, helpPop, errorEl);
  countEl.hidden = true;
  positionEl.hidden = true;
  progressBox.hidden = true;
  messageEl.hidden = true;
  dismissButton.hidden = true;

  // ---- count, position, progress
  let lastCount = null;
  function paintCount(value, { announce = false } = {}) {
    if (value === "loading") {
      countEl.classList.add("is-loading");
      if (lastCount === null) {
        countEl.textContent = S.loading;
        countEl.hidden = false;
      }
      return;
    }
    countEl.classList.remove("is-loading");
    if (value === null || value === undefined) {
      lastCount = null;
      countEl.textContent = "";
      countEl.hidden = true;
      countEl.classList.remove("is-estimate");
      return;
    }
    lastCount = value;
    const text = formatCount(value, S);
    countEl.textContent = text;
    countEl.hidden = false;
    countEl.classList.toggle("is-estimate", !value.exact);
    if (announce) {
      announcer.textContent = text;
    }
  }

  let lastPosition;
  function paintPosition(ts) {
    if (ts === lastPosition) {
      return; // scrolling calls this for every frame: touch the DOM only when the value changed
    }
    lastPosition = ts;
    const empty = ts === null || ts === undefined;
    positionEl.textContent = empty ? "" : format(S.gridPosition, { time: formatTs(ts) });
    positionEl.hidden = empty;
  }

  function paintProgress(value) {
    if (value === null || value === undefined) {
      progressBox.hidden = true;
      progressText.textContent = "";
      progressEl.removeAttribute("value");
      return;
    }
    progressBox.hidden = false;
    if (value.value === null || value.value === undefined || !(value.max > 0)) {
      progressEl.removeAttribute("value");
      progressEl.removeAttribute("max");
    } else {
      progressEl.max = value.max;
      progressEl.value = value.value;
    }
    progressText.textContent = value.text || "";
  }

  // ---- message
  const messages = createMessageState();
  let timer = null;
  function clearTimerNow() {
    if (timer !== null) {
      disarm(timer);
      timer = null;
    }
  }
  function paintMessage() {
    const current = messages.current;
    clearTimerNow();
    if (current === null) {
      messageText.textContent = "";
      messageEl.hidden = true;
      dismissButton.hidden = true;
      messageEl.className = "sb-seg sb-message";
      return;
    }
    messageText.textContent = current.text;
    messageText.title = current.text; // long text is cut with an ellipsis; the full text stays in the tooltip
    messageEl.className = "sb-seg sb-message is-" + current.kind;
    messageEl.hidden = false;
    dismissButton.hidden = current.kind !== "error";
    if (current.expiresAt !== null) {
      timer = arm(() => {
        timer = null;
        if (messages.expire(now())) {
          paintMessage();
        }
      }, Math.max(0, current.expiresAt - now()));
    }
  }
  dismissButton.addEventListener("click", () => {
    messages.show("");
    paintMessage();
  });

  // ---- connection
  let retryHandler = null;
  function connection(state, { onRetry } = {}) {
    if (state === "online") {
      root.classList.remove("is-error");
      errorEl.hidden = true;
      errorText.textContent = "";
      retryHandler = null;
      return;
    }
    root.classList.add("is-error");
    errorText.textContent = state === "forbidden" ? S.errorForbidden : S.errorNetwork;
    retryHandler = state === "offline" && typeof onRetry === "function" ? onRetry : null;
    retryButton.hidden = retryHandler === null;
    errorEl.hidden = false;
  }
  retryButton.addEventListener("click", () => {
    if (retryHandler !== null) {
      retryHandler();
    }
  });

  // ---- help (the list of shortcuts, until the dialog of UI-28)
  function setHelp(open) {
    const on = open === undefined ? helpPop.hidden : Boolean(open);
    if (on) {
      helpPop.textContent = helpText();
    }
    helpPop.hidden = !on;
    helpButton.setAttribute("aria-expanded", String(on));
  }
  helpButton.addEventListener("click", () => setHelp());
  doc.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !helpPop.hidden) {
      setHelp(false);
      helpButton.focus();
    }
  });
  doc.addEventListener("click", (event) => {
    if (!helpPop.hidden && !helpPop.contains(event.target) && !helpButton.contains(event.target)) {
      setHelp(false);
    }
  });

  return {
    count: paintCount,
    position: paintPosition,
    progress: paintProgress,
    message(text, { kind = "info", timeoutMs } = {}) {
      if (isConnectionText(text, S)) {
        // The connection error owns the bar (connection()); a stale copy must not come back after recovery.
        return { dismiss() {} };
      }
      const shown = messages.show(text, { kind: normalizeKind(kind), timeoutMs }, now());
      paintMessage();
      const id = shown === null ? null : shown.id;
      return {
        dismiss() {
          if (id !== null && messages.dismiss(id)) {
            paintMessage();
          }
        },
      };
    },
    connection,
    slot: (id) => slotContent[id],
    help: setHelp,
  };
}
