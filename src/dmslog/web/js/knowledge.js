// Knowledge section of the entry detail: hint, related parameters, error-code resolution and the
// vault note path. Texts come from the API (vault and ERS configuration): textContent only.
import { api } from "./api.js";
import { RequestGuard } from "./detail-model.js";
import { buildKnowledgeView, planKnowledge } from "./knowledge-model.js";
import { S, format } from "./strings.js";

const CACHE_LIMIT = 500;
const STYLESHEET = "/css/knowledge.css";

function ensureStylesheet() {
  if (!document.querySelector('link[rel="stylesheet"][href="' + STYLESHEET + '"]')) {
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = STYLESHEET;
    document.head.appendChild(link);
  }
}

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

function remember(cache, key, value) {
  if (cache.size >= CACHE_LIMIT) {
    cache.clear();
  }
  cache.set(key, value);
}

// host: element to fill (hidden while there is nothing to show).
// Options: getCase(), getFamily(sourceId) -> family code or "", onError(err).
export class KnowledgeSection {
  constructor(host, { getCase, getFamily, onError }) {
    this.host = host;
    this.getCase = getCase;
    this.getFamily = getFamily;
    this.onError = onError || (() => {});
    this.guard = new RequestGuard();
    this.cache = new Map(); // "case|template" and "case|family|code" -> answer (null = unknown)
    this.clear();
  }

  clear() {
    this.guard.invalidate();
    this.host.hidden = true;
    this.host.textContent = "";
  }

  // Forget cached answers (the case or its ERS configuration changed).
  reset() {
    this.cache.clear();
    this.clear();
  }

  async show(entry) {
    const caseId = this.getCase();
    if (!entry || caseId === null || caseId === undefined) {
      this.clear();
      return;
    }
    const plan = planKnowledge(entry, this.getFamily(entry.source_id));
    if (plan.template === null && plan.errors.length === 0) {
      this.clear();
      return;
    }
    const ticket = this.guard.begin(caseId, entry.id);
    try {
      const [templateData, ...errorAnswers] = await Promise.all([
        plan.template === null ? null : this.lookup("t|" + caseId + "|" + plan.template,
          () => api.knowledge(caseId, plan.template, ticket.signal)),
        ...plan.errors.map((item) =>
          this.lookup("e|" + caseId + "|" + item.family + "|" + item.code,
            () => api.knowledgeError(caseId, item.family, item.code, ticket.signal))),
      ]);
      if (!this.guard.isCurrent(ticket, this.getCase(), entry.id)) {
        return;
      }
      this.render(buildKnowledgeView(templateData, errorAnswers));
    } catch (err) {
      if (this.guard.isCurrent(ticket, this.getCase(), entry.id) && !(err && err.name === "AbortError")) {
        this.host.hidden = true;
        this.host.textContent = "";
        this.onError(err);
      }
    }
  }

  // A 404 means "nothing known": cached as null, not an error.
  async lookup(key, fetcher) {
    if (this.cache.has(key)) {
      return this.cache.get(key);
    }
    let value = null;
    try {
      value = await fetcher();
    } catch (err) {
      if (!err || err.status !== 404) {
        throw err;
      }
    }
    remember(this.cache, key, value);
    return value;
  }

  render(view) {
    const host = this.host;
    host.textContent = "";
    if (view === null) {
      host.hidden = true;
      return;
    }
    ensureStylesheet();
    host.hidden = false;
    const head = element("div", "kn-head");
    head.appendChild(element("h3", "kn-title", S.knowTitle));
    if (view.outOfVault) {
      const badge = element("span", "kn-badge", S.knowOutOfVault);
      badge.title = S.knowOutOfVaultTitle;
      head.appendChild(badge);
    }
    host.appendChild(head);
    if (view.adapterTitle) {
      host.appendChild(element("div", "kn-adapter", format(S.knowAdapter, { title: view.adapterTitle })));
    }
    if (view.hint !== null) {
      host.appendChild(element("div", "kn-label", S.knowHint));
      host.appendChild(element("p", "kn-hint", view.hint));
    }
    if (view.params.length > 0) {
      host.appendChild(this.paramsTable(view.params));
    }
    if (view.errors.length > 0) {
      host.appendChild(this.errorList(view.errors));
    }
    if (view.note !== null) {
      host.appendChild(this.noteRow(view.note));
    }
    if (view.packNotice !== null) {
      host.appendChild(element("p", "kn-pack-notice", view.packNotice));
    }
  }

  paramsTable(params) {
    const table = element("table", "kn-table");
    table.appendChild(element("caption", "", S.knowParams));
    const head = element("tr");
    for (const label of [S.knowParamName, S.knowParamDefault, S.knowParamDescription]) {
      head.appendChild(element("th", "", label));
    }
    table.appendChild(element("thead")).appendChild(head);
    const body = element("tbody");
    for (const item of params) {
      const row = element("tr");
      row.appendChild(element("td", "kn-mono", item.name));
      row.appendChild(element("td", "kn-mono", item.defaultText === null ? S.knowNoDefault : item.defaultText));
      row.appendChild(element("td", "", item.description === null ? S.knowNoDescription : item.description));
      body.appendChild(row);
    }
    table.appendChild(body);
    return table;
  }

  errorList(errors) {
    const box = element("div", "kn-errors");
    box.appendChild(element("div", "kn-label", S.knowErrors));
    const list = element("ul", "kn-error-list");
    for (const item of errors) {
      const li = element("li", "kn-error");
      const severity = item.severity === null ? S.knowSeverityUnknown : S.severityNames[item.severity];
      const source = item.source === "ers" ? S.knowSourceErs : S.knowSourceVault;
      li.appendChild(element("span", "kn-error-line", format(S.knowErrorLine, { code: item.code, severity, source })));
      if (item.outOfVault) {
        const badge = element("span", "kn-badge", S.knowOutOfVault);
        badge.title = S.knowOutOfVaultTitle;
        li.appendChild(badge);
      }
      if (item.reason !== null) {
        li.appendChild(element("div", "kn-error-text", S.knowReason + ": " + item.reason));
      }
      if (item.details !== null) {
        li.appendChild(element("div", "kn-error-text", S.knowDetails + ": " + item.details));
      }
      list.appendChild(li);
    }
    box.appendChild(list);
    return box;
  }

  noteRow(path) {
    const row = element("div", "kn-note");
    row.appendChild(element("span", "kn-label", S.knowNote));
    row.appendChild(element("code", "kn-path", path));
    const button = element("button", "kn-copy", S.knowCopyPath);
    button.type = "button";
    button.setAttribute("aria-label", S.knowCopyPathLabel);
    const status = element("span", "kn-copy-status");
    status.setAttribute("role", "status");
    button.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(path);
        status.textContent = S.copied;
      } catch (err) {
        status.textContent = "";
      }
    });
    row.append(button, status);
    return row;
  }
}
