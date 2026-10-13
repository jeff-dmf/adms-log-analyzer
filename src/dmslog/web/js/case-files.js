// "ERS files of the new case": the list of chosen ZIPs, Browse (native dialog or built-in
// browser), pasting a path, and the Create button. Names and paths are untrusted: textContent only.
import { api } from "./api.js";
import {
  CaseFilesState, KIND_ENTERPRISE, KIND_SENSITIVE, PickFlow, STATUS_FAILED, STATUS_OK, STATUS_REFUSED,
  STATUS_WORKING, formatSize,
} from "./case-files-model.js";
import { openFolderBrowser } from "./folder-browser.js";
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

function button(label, handler, className) {
  const node = el("button", className, label);
  node.type = "button";
  node.addEventListener("click", handler);
  return node;
}

function kindLabel(item) {
  if (item.kind === KIND_ENTERPRISE) {
    return S.cfKindEnterprise;
  }
  if (item.kind === KIND_SENSITIVE) {
    return S.cfKindSensitive;
  }
  return item.isZip ? S.cfKindOther : S.cfFolderLabel;
}

function statusLabel(item) {
  switch (item.status) {
    case STATUS_WORKING: return S.cfStatusWorking;
    case STATUS_OK: return S.cfStatusOk;
    case STATUS_REFUSED: return S.cfStatusRefused;
    case STATUS_FAILED: return S.cfStatusFailed;
    default: return "";
  }
}

export class CaseFiles {
  // host: element that receives the UI; nameInput: the case name field; onMessage(text, isError).
  constructor({ host, nameInput, onMessage, fs, storage }) {
    this.host = host;
    this.nameInput = nameInput;
    this.onMessage = onMessage;
    this.fs = fs || api.fs;
    this.storage = storage === undefined ? safeStorage() : storage;
    this.flow = new PickFlow(this.fs);
    this.state = new CaseFilesState(() => this.render());
    this.waiting = false;
    this.build();
    this.render();
  }

  build() {
    const host = this.host;
    host.textContent = "";
    host.className = "case-files";
    const title = el("h3", "cf-title", S.cfTitle);
    title.id = "cf-title";
    this.help = el("p", "cf-help", S.cfHelp);
    this.help.id = "cf-help";

    const pathRow = el("div", "cf-path-row");
    this.pathInput = el("input", "cf-path");
    this.pathInput.type = "text";
    this.pathInput.autocomplete = "off";
    this.pathInput.setAttribute("aria-label", S.cfPathLabel);
    this.pathInput.placeholder = S.cfPathPlaceholder;
    this.pathInput.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault(); // the field is inside the case form: Enter adds, never submits
        this.addTyped();
      }
    });
    this.addButton = button(S.cfAdd, () => this.addTyped());
    this.browseButton = button(S.cfBrowse, () => this.browse(), "cf-browse");
    pathRow.appendChild(this.pathInput);
    pathRow.appendChild(this.addButton);
    pathRow.appendChild(this.browseButton);

    this.waitBox = el("div", "cf-wait");
    this.waitBox.setAttribute("role", "status");
    this.waitBox.setAttribute("aria-live", "polite");
    this.waitBox.hidden = true;
    this.waitText = el("span", "cf-wait-text", S.cfWaitingDialog);
    this.cancelButton = button(S.cfCancel, () => this.flow.cancel());
    this.waitBox.appendChild(this.waitText);
    this.waitBox.appendChild(this.cancelButton);

    this.notices = el("ul", "cf-notices");
    this.notices.setAttribute("aria-live", "polite");

    const head = el("div", "cf-list-head");
    this.count = el("span", "cf-count");
    this.count.setAttribute("aria-live", "polite");
    this.clearButton = button(S.cfClear, () => this.state.clear());
    head.appendChild(this.count);
    head.appendChild(this.clearButton);

    this.list = el("ul", "cf-list");
    this.list.setAttribute("aria-label", S.cfListLabel);
    this.list.setAttribute("aria-describedby", "cf-help");

    this.createButton = el("button", "primary cf-create");
    this.createButton.type = "submit";

    for (const node of [title, this.help, pathRow, this.waitBox, this.notices, head, this.list, this.createButton]) {
      host.appendChild(node);
    }
  }

  // -- list ---------------------------------------------------------------------------------

  addTyped() {
    const value = this.pathInput.value.trim();
    if (value === "") {
      return;
    }
    this.state.add([value]);
    this.pathInput.value = "";
    this.pathInput.focus();
  }

  async browse() {
    if (this.waiting || this.state.busy) {
      return;
    }
    this.waiting = true;
    this.browseButton.disabled = true;
    this.render();
    try {
      const outcome = await this.flow.browse();
      await this.handleOutcome(outcome);
    } finally {
      this.waiting = false;
      this.browseButton.disabled = false;
      this.render();
    }
  }

  async handleOutcome(outcome) {
    const state = this.state;
    switch (outcome.outcome) {
      case "paths":
        state.add(outcome.paths);
        state.addErrors(outcome.errors);
        break;
      case "timed_out":
        state.addNotice("info", format(S.cfDialogTimeout, { minutes: outcome.minutes }));
        break;
      case "busy":
        state.addNotice("info", S.cfDialogBusy);
        break;
      case "error":
        state.addNotice("error", outcome.message);
        break;
      case "fallback": {
        if (outcome.failed || this.flow.native === false) {
          state.addNotice("info", S.cfNoNative);
        }
        this.waiting = false;
        this.render();
        const picked = await openFolderBrowser({ fs: this.fs, storage: this.storage });
        if (picked && picked.length > 0) {
          state.add(picked);
        }
        this.browseButton.focus();
        break;
      }
      default:
        break; // cancelled: nothing to show
    }
  }

  // -- create -------------------------------------------------------------------------------

  // deps: {createCase, selectCase, openPath}; see CaseFilesState.create.
  async submit(deps) {
    const name = this.nameInput.value.trim();
    if (name === "") {
      this.onMessage(S.cfNeedName, true);
      return null;
    }
    if (this.state.items.length === 0) {
      this.onMessage(S.cfNeedFiles, true);
      return null;
    }
    const result = await this.state.create(name, deps);
    if (result.state === "ok") {
      this.nameInput.value = "";
      this.onMessage(format(S.cfCreatedAll, { total: result.total }), false);
    } else if (result.state === "partial") {
      this.onMessage(format(S.cfPartial, { ok: result.ok, total: result.total }), true);
    } else if (result.reason === "case") {
      this.onMessage(this.state.notices.length > 0 ? this.state.notices[this.state.notices.length - 1].message : "", true);
    }
    return result;
  }

  // -- rendering ----------------------------------------------------------------------------

  render() {
    const state = this.state;
    this.waitBox.hidden = !this.waiting;
    this.addButton.disabled = state.busy;
    this.pathInput.disabled = state.busy;
    this.browseButton.disabled = state.busy || this.waiting;
    this.clearButton.disabled = state.busy || state.items.length === 0;
    this.clearButton.hidden = state.items.length === 0;
    this.createButton.disabled = !state.canCreate;
    this.createButton.textContent = state.items.length > 0
      ? format(S.cfCreateWith, { n: state.items.length })
      : S.cfCreate;
    this.count.textContent = state.items.length > 0 ? format(S.cfCount, { n: state.items.length }) : S.cfEmpty;

    this.notices.textContent = "";
    for (const notice of state.notices) {
      const li = el("li", "cf-notice cf-notice-" + notice.type);
      if (notice.type === "duplicate") {
        li.textContent = format(S.cfDuplicate, { name: notice.name });
      } else if (notice.type === "limit") {
        li.textContent = format(S.cfLimit, { name: notice.name, message: notice.message });
      } else if (notice.name !== "") {
        li.textContent = format(S.cfItemError, { name: notice.name, message: notice.message });
      } else {
        li.textContent = notice.message;
      }
      this.notices.appendChild(li);
    }

    this.list.textContent = "";
    for (const item of state.items) {
      this.list.appendChild(this.renderItem(item));
    }
  }

  renderItem(item) {
    const li = el("li", "cf-item cf-" + item.status);
    const text = el("div", "cf-item-text");
    text.appendChild(el("span", "cf-name", item.name));
    const bits = [kindLabel(item)];
    const size = formatSize(item.size);
    if (size !== "") {
      bits.push(size);
    }
    text.appendChild(el("span", "cf-meta", bits.join(" · ")));
    if (item.dir !== "") {
      const dir = el("span", "cf-dir", item.dir);
      dir.title = item.dir;
      text.appendChild(dir);
    }
    const label = statusLabel(item);
    if (label !== "") {
      const result = el("span", "cf-result cf-result-" + item.status, label + (item.message ? ": " + item.message : ""));
      result.setAttribute("role", item.status === STATUS_WORKING ? "status" : "note");
      text.appendChild(result);
    }
    li.appendChild(text);
    const remove = button(S.cfRemove, () => this.state.remove(item.id), "cf-remove");
    remove.setAttribute("aria-label", format(S.cfRemoveLabel, { name: item.name }));
    remove.disabled = this.state.busy;
    li.appendChild(remove);
    return li;
  }
}

function safeStorage() {
  try {
    return window.localStorage;
  } catch (err) {
    return null;
  }
}
