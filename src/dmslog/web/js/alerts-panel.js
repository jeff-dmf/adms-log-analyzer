// Local alerts UI: the banner and the "Alerts" panel (rules, history, browser notifications). Alert events
// come from the case's progress stream (`alert`); the rule names are user text and every text reaches the
// page through textContent only. Notification permission is asked only by the explicit button.
import { api as defaultApi } from "./api.js";
import {
  DEFAULT_MIN_INTERVAL, KINDS, SEVERITIES, connectAlertStream, createAlertsFlow, createNotifier, validateDraft,
} from "./alerts-model.js";
import { S, format } from "./strings.js";

const STYLESHEET = "/css/alerts.css";
const CONFIRM_MS = 4000;

function ensureStylesheet() {
  if (!document.querySelector('link[rel="stylesheet"][href="' + STYLESHEET + '"]')) {
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = STYLESHEET;
    document.head.appendChild(link);
  }
}

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

function button(label, className, onClick) {
  const node = el("button", className, label);
  node.type = "button";
  node.addEventListener("click", onClick);
  return node;
}

let uid = 0;
function labelled(text, control, className) {
  uid += 1;
  control.id = "al-field-" + uid;
  const wrap = el("div", "al-field" + (className ? " " + className : ""));
  const label = el("label", "", text);
  label.htmlFor = control.id;
  wrap.append(label, control);
  return wrap;
}

function input(type, value) {
  const node = el("input");
  node.type = type;
  node.autocomplete = "off";
  node.value = value === undefined ? "" : value;
  return node;
}

function select(options) {
  const node = el("select");
  for (const [value, label] of options) {
    const option = el("option", "", label);
    option.value = value;
    node.appendChild(option);
  }
  return node;
}

const SEVERITY_LABEL = { info: S.alSeverityInfo, warning: S.alSeverityWarning, critical: S.alSeverityCritical };
const KIND_LABEL = { match: S.alKindMatch, threshold: S.alKindThreshold, open_operation: S.alKindOpen };
const ERROR_TEXT = {
  name: S.alErrName, nameLong: S.alErrNameLong, duplicate: S.alErrDuplicate, filter: S.alErrFilter,
  count: S.alErrCount, threshold: S.alErrThreshold, window: S.alErrWindow, timeout: S.alErrTimeout,
  param: S.alErrParam, interval: S.alErrInterval, kind: S.alErrKind,
};

function whatFor(alert) {
  if (alert.kind === "open_operation") {
    return S.alWhatOperations;
  }
  return alert.kind === "threshold" ? S.alWhatWindow : S.alWhatRecords;
}

function summarize(rule) {
  if (rule.kind === "match") {
    return format(S.alSummaryMatch, { count: rule.min_count || 1 });
  }
  if (rule.kind === "threshold") {
    return format(S.alSummaryThreshold, { threshold: rule.threshold, window: rule.window_s });
  }
  const base = rule.timeout_param
    ? format(S.alSummaryOpenParam, { param: rule.timeout_param })
    : format(S.alSummaryOpenSeconds, { seconds: rule.timeout_s });
  return base + (rule.operation ? format(S.alSummaryOperation, { operation: rule.operation }) : "");
}

// banner: the live element (role="status"). options: api, getCase(), getFilter() (current filter or null),
// onShowRecords(filter), onError(err), onClose() (after Escape), notifier (tests). Returns {open, close, isOpen, onCase, refresh}.
export function initAlerts(host, { banner, api = defaultApi, getCase, getFilter, onShowRecords, onError, onClose, notifier }) {
  ensureStylesheet();
  const notes = notifier || createNotifier({ storage: safeStorage() });
  let stream = null;
  let opened = false;
  let confirming = null;
  let confirmTimer = null;
  let presets = [];
  let formMessage = "";

  const describe = (alert) => ({
    title: format(S.alBanner, { severity: SEVERITY_LABEL[alert.severity], rule: alert.rule, count: alert.count, what: whatFor(alert) }),
    body: format(S.alNotifyBody, { count: alert.count, what: whatFor(alert) }),
  });
  const flow = createAlertsFlow({
    api: api.alerts, getCase, notifier: notes, describe,
    onChange: () => {
      paintBanner();
      if (opened) {
        paint();
      }
    },
  });

  // ---- banner
  function showRecords(alert) {
    const filter = flow.recordsFilterFor(alert);
    if (filter !== null) {
      onShowRecords(filter);
    }
  }
  function paintBanner() {
    banner.textContent = "";
    const current = flow.state.banner;
    banner.hidden = current === null;
    if (current === null) {
      return;
    }
    const { alert, more } = current;
    banner.className = "al-banner al-sev-" + alert.severity;
    banner.appendChild(el("span", "al-banner-text", describe(alert).title));
    if (more > 0) {
      banner.appendChild(el("span", "al-banner-more", format(S.alBannerMore, { count: more })));
    }
    banner.append(
      button(S.alShowRecords, "al-banner-show", () => showRecords(alert)),
      button(S.alDismiss, "al-banner-dismiss", () => flow.dismiss()),
    );
  }

  // ---- panel
  host.classList.add("al-panel");
  host.setAttribute("role", "region");
  host.setAttribute("aria-label", S.alTitle);
  const head = el("div", "al-head");
  head.append(el("h3", "al-title", S.alTitle), button(S.alClose, "al-close", () => close()));
  const intro = el("p", "al-muted", S.alIntro);
  const message = el("div", "al-message");
  message.setAttribute("role", "status");
  message.setAttribute("aria-live", "polite");
  const notifyBox = el("div", "al-notify");
  const rulesTitle = el("h4", "", S.alRules);
  const rulesList = el("ul", "al-list");
  const formBox = el("form", "al-form");
  const historyHead = el("div", "al-history-head");
  const historyList = el("ul", "al-list");
  host.append(head, intro, message, notifyBox, rulesTitle, rulesList, formBox, historyHead, historyList);

  // New-rule form.
  const nameField = input("text");
  nameField.maxLength = 80;
  const kindField = select(KINDS.map((k) => [k, KIND_LABEL[k]]));
  const severityField = select(SEVERITIES.map((s) => [s, SEVERITY_LABEL[s]]));
  severityField.value = "warning";
  const sourceField = select([["current", S.alFilterCurrent]]);
  const minCount = input("number", "1");
  minCount.min = "1";
  const threshold = input("number", "10");
  threshold.min = "1";
  const windowS = input("number", "60");
  windowS.min = "1";
  const timeoutMode = select([["param", S.alTimeoutParam], ["seconds", S.alTimeoutSeconds]]);
  const timeoutParam = input("text", "SendTimeout");
  const timeoutS = input("number", "60");
  timeoutS.min = "1";
  const operation = input("text");
  const minInterval = input("number", String(DEFAULT_MIN_INTERVAL));
  minInterval.min = "0";
  const groups = {
    filter: labelled(S.alFilterFrom, sourceField),
    match: labelled(S.alMinCount, minCount),
    threshold: labelled(S.alThreshold, threshold),
    window: labelled(S.alWindow, windowS),
    mode: labelled(S.alTimeoutMode, timeoutMode),
    param: labelled(S.alTimeoutParam, timeoutParam),
    seconds: labelled(S.alTimeoutSeconds, timeoutS),
    operation: labelled(S.alOperation, operation),
  };
  const fieldOf = { name: nameField, minCount, threshold, windowS, timeoutParam, timeoutS, minInterval, filter: sourceField };
  const formMessageBox = el("div", "al-form-message");
  formMessageBox.setAttribute("role", "alert");
  formBox.append(
    el("h4", "", S.alNewRule),
    labelled(S.alName, nameField), labelled(S.alKind, kindField), labelled(S.alSeverity, severityField),
    groups.filter, groups.match, groups.threshold, groups.window, groups.mode, groups.param, groups.seconds,
    groups.operation, labelled(S.alMinInterval, minInterval), formMessageBox,
    (() => {
      const submit = el("button", "al-create", S.alCreate);
      submit.type = "submit";
      return submit;
    })(),
  );

  function paintForm() {
    const kind = kindField.value;
    const open = kind === "open_operation";
    groups.filter.hidden = open;
    groups.match.hidden = kind !== "match";
    groups.threshold.hidden = kind !== "threshold";
    groups.window.hidden = kind !== "threshold";
    groups.mode.hidden = !open;
    groups.param.hidden = !open || timeoutMode.value !== "param";
    groups.seconds.hidden = !open || timeoutMode.value !== "seconds";
    groups.operation.hidden = !open;
    formMessageBox.textContent = formMessage;
  }
  kindField.addEventListener("change", paintForm);
  timeoutMode.addEventListener("change", paintForm);

  function chosenFilter() {
    if (sourceField.value === "current") {
      return getFilter();
    }
    const preset = presets[Number(sourceField.value)];
    return preset ? preset.filter : null;
  }

  formBox.addEventListener("submit", async (event) => {
    event.preventDefault();
    const draft = {
      name: nameField.value, kind: kindField.value, severity: severityField.value, filter: chosenFilter(),
      minCount: minCount.value, threshold: threshold.value, windowS: windowS.value,
      timeoutMode: timeoutMode.value, timeoutParam: timeoutParam.value, timeoutS: timeoutS.value,
      operation: operation.value, minInterval: minInterval.value,
    };
    const result = validateDraft(draft, flow.state.rules);
    if (result.error) {
      formMessage = result.error === "filter" && sourceField.value === "current" ? S.alFilterNone : ERROR_TEXT[result.error];
      paintForm();
      const field = fieldOf[result.field];
      if (field) {
        field.focus();
      }
      return;
    }
    if (await flow.create(result.body)) {
      formMessage = "";
      nameField.value = "";
      message.textContent = S.alCreated;
    }
    paintForm();
  });

  function paintNotify() {
    notifyBox.textContent = "";
    notifyBox.appendChild(el("h4", "", S.alNotifyTitle));
    const permission = notes.permission();
    if (permission === "unsupported") {
      notifyBox.appendChild(el("p", "al-muted", S.alNotifyUnsupported));
    } else if (permission === "denied") {
      notifyBox.appendChild(el("p", "al-muted", S.alNotifyDenied));
    } else if (permission === "default") {
      notifyBox.appendChild(button(S.alNotifyEnable, "al-notify-enable", async () => {
        await notes.request(); // explicit user action: the only place that asks the browser
        paintNotify();
      }));
    } else {
      const box = input("checkbox");
      box.checked = notes.wanted();
      box.addEventListener("change", () => notes.setWanted(box.checked));
      const wrap = el("label", "al-toggle");
      wrap.append(box, el("span", "", " " + S.alNotifyOn));
      notifyBox.appendChild(wrap);
    }
  }

  function ruleRow(rule) {
    const row = el("li", "al-rule");
    const toggle = input("checkbox");
    toggle.checked = Boolean(rule.enabled);
    toggle.setAttribute("aria-label", S.alEnabled + ": " + rule.name);
    toggle.addEventListener("change", async () => {
      toggle.disabled = true;
      await flow.setEnabled(rule, toggle.checked);
    });
    const text = el("div", "al-rule-text");
    const title = el("div", "al-rule-name");
    title.append(el("span", "al-sev al-sev-" + rule.severity, SEVERITY_LABEL[rule.severity] || rule.severity), el("span", "", " " + rule.name));
    text.append(title, el("div", "al-muted", KIND_LABEL[rule.kind] + ": " + summarize(rule)));
    if (rule.available === false) {
      text.appendChild(el("div", "al-unavailable", format(S.alUnavailable, { message: rule.unavailable_message || "" })));
    }
    const armed = confirming === rule.name;
    const remove = button(armed ? S.alDeleteConfirm : S.alDelete, "al-delete", async () => {
      if (confirming !== rule.name) {
        confirming = rule.name;
        clearTimeout(confirmTimer);
        confirmTimer = setTimeout(() => {
          confirming = null;
          paint();
        }, CONFIRM_MS);
        paint();
        return;
      }
      confirming = null;
      clearTimeout(confirmTimer);
      await flow.remove(rule);
    });
    row.append(toggle, text, remove);
    return row;
  }

  function historyRow(alert) {
    const row = el("li", "al-alert");
    const text = el("div", "al-rule-text");
    const title = el("div", "al-rule-name");
    title.append(
      el("span", "al-sev al-sev-" + alert.severity, SEVERITY_LABEL[alert.severity]),
      el("span", "", " " + alert.rule + " - " + alert.count + " " + whatFor(alert)),
    );
    text.append(title, el("div", "al-muted", alert.at));
    row.append(text, button(S.alShowRecords, "al-show", () => showRecords(alert)));
    return row;
  }

  function paint() {
    const { rules, history, problems, invalid, error } = flow.state;
    message.textContent = invalid ? S.alFileInvalid : error ? error : message.textContent;
    paintNotify();
    rulesList.textContent = "";
    if (rules.length === 0) {
      rulesList.appendChild(el("li", "al-muted", S.alNoRules));
    }
    for (const rule of rules) {
      rulesList.appendChild(ruleRow(rule));
    }
    if (problems.length > 0) {
      rulesList.appendChild(el("li", "al-muted", format(S.alProblems, { count: problems.length })));
    }
    historyHead.textContent = "";
    historyHead.append(el("h4", "", S.alHistory), button(S.alClearHistory, "al-clear", () => flow.clearHistory()));
    historyList.textContent = "";
    if (history.length === 0) {
      historyList.appendChild(el("li", "al-muted", S.alNoHistory));
    }
    for (const alert of history) {
      historyList.appendChild(historyRow(alert));
    }
    paintForm();
  }

  async function loadPresets() {
    const caseId = getCase();
    if (caseId === null || !api.presets) {
      return;
    }
    try {
      const data = await api.presets.list(caseId);
      presets = (data.presets || []).filter((p) => p && p.filter);
    } catch {
      presets = [];
    }
    const keep = sourceField.value;
    sourceField.textContent = "";
    sourceField.appendChild(Object.assign(el("option", "", S.alFilterCurrent), { value: "current" }));
    presets.forEach((p, index) => {
      sourceField.appendChild(Object.assign(el("option", "", p.name), { value: String(index) }));
    });
    sourceField.value = [...sourceField.options].some((o) => o.value === keep) ? keep : "current";
  }

  function connect() {
    if (stream) {
      stream.close();
      stream = null;
    }
    const caseId = getCase();
    if (caseId === null) {
      return;
    }
    stream = connectAlertStream({ url: defaultApi.progressUrl(caseId), onAlert: (data) => flow.receive(data) });
  }

  function open() {
    opened = true;
    host.hidden = false;
    message.textContent = "";
    paint();
    loadPresets();
    flow.refresh().catch((err) => onError(err));
  }
  function close() {
    opened = false;
    host.hidden = true;
  }
  host.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      close();
      if (onClose) {
        onClose();
      }
    }
  });
  host.hidden = true;
  banner.hidden = true;

  return {
    open,
    close,
    isOpen: () => opened,
    onCase() {
      flow.reset();
      connect();
      if (opened) {
        loadPresets();
        flow.refresh().catch((err) => onError(err));
      }
    },
    refresh: () => flow.refresh(),
  };
}

function safeStorage() {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}
