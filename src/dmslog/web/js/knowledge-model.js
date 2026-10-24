// Pure helpers of the entry-detail Knowledge section (no DOM). Everything returned is plain data;
// the DOM code places it with textContent only.

export const MAX_CODES = 8;
const CODE_PATTERN = /^[A-Za-z0-9_][A-Za-z0-9_.:-]{0,127}$/;
const FAMILY_PATTERN = /^[A-Za-z0-9_]{1,32}$/;
const SINGLE_CODE_FIELDS = ["code", "error_code"];

// Error codes carried by the parsed fields of a record: the list field `codes` and the single
// fields `code` / `error_code`. Only strings the API would accept are kept (order kept, no repeats).
export function errorCodesOf(fields) {
  if (!fields || typeof fields !== "object") {
    return [];
  }
  const raw = [];
  if (Array.isArray(fields.codes)) {
    raw.push(...fields.codes);
  }
  for (const name of SINGLE_CODE_FIELDS) {
    raw.push(fields[name]);
  }
  const seen = new Set();
  const out = [];
  for (const value of raw) {
    if (typeof value !== "string") {
      continue;
    }
    const code = value.trim();
    if (CODE_PATTERN.test(code) && !seen.has(code)) {
      seen.add(code);
      out.push(code);
      if (out.length >= MAX_CODES) {
        break;
      }
    }
  }
  return out;
}

// What to ask the API for a record: its template code and the (family, code) pairs of its errors.
// `family` is the family of the record's source (empty = unknown, then no error is asked).
export function planKnowledge(entry, family) {
  if (!entry) {
    return { template: null, errors: [] };
  }
  const template = entry.tpl_id > 0 && typeof entry.tpl === "string" && entry.tpl ? entry.tpl : null;
  const known = typeof family === "string" && FAMILY_PATTERN.test(family);
  const errors = known ? errorCodesOf(entry.fields).map((code) => ({ family, code })) : [];
  return { template, errors };
}

function textOrNull(value) {
  return typeof value === "string" && value.trim() !== "" ? value : null;
}

function stringList(value) {
  return Array.isArray(value)
    ? value.filter((item) => typeof item === "string" && item.trim() !== "")
    : [];
}

// The RT service block of the template answer (null when the answer has no usable `service`).
// Everything comes from the local pack: text only, lists filtered to strings.
export function buildServiceView(service) {
  if (!service || typeof service !== "object" || Array.isArray(service)) {
    return null;
  }
  const processName = textOrNull(service.process);
  if (processName === null) {
    return null;
  }
  const references = [];
  for (const item of Array.isArray(service.references) ? service.references : []) {
    if (item && textOrNull(item.doc) !== null) {
      references.push({
        doc: item.doc,
        note: textOrNull(item.note),
        section: textOrNull(item.section),
        mentions: Number.isInteger(item.mentions) ? item.mentions : null,
      });
    }
  }
  const related = [];
  for (const item of Array.isArray(service.related) ? service.related : []) {
    if (item && textOrNull(item.process) !== null) {
      related.push({ process: item.process, via: textOrNull(item.via) });
    }
  }
  return {
    process: processName,
    family: textOrNull(service.family),
    group: textOrNull(service.group),
    title: textOrNull(service.title),
    summary: textOrNull(service.summary),
    note: textOrNull(service.note),
    section: textOrNull(service.section),
    installDir: textOrNull(service.install_dir),
    monitored: stringList(service.monitored),
    counters: stringList(service.counters),
    usageEvents: Number.isInteger(service.usage_events) ? service.usage_events : null,
    references,
    related,
    keys: stringList(service.keys),
    inVault: service.in_vault === true,
  };
}

// Server text explaining that the knowledge pack is missing or invalid (null when it is installed).
// The pack is optional and installed locally; the text comes from the server (messages.py).
export function packNoticeOf(templateData, errorAnswers) {
  for (const answer of [templateData, ...(errorAnswers || [])]) {
    const state = answer && answer.knowledge_pack;
    if (state && state.installed === false) {
      return textOrNull(state.message);
    }
  }
  return null;
}

// Joins the template answer (GET /api/knowledge) and the error answers (GET /api/knowledge/error;
// null for a code the API does not know) into what the section shows. null = nothing to show:
// the section needs a hint, at least one resolved error code or a knowledge-pack notice.
export function buildKnowledgeView(templateData, errorAnswers) {
  const hintData = templateData && templateData.hint ? templateData.hint : null;
  const hintText = hintData ? textOrNull(hintData.text) : null;
  const errors = [];
  for (const answer of errorAnswers || []) {
    if (!answer || typeof answer.code !== "string") {
      continue;
    }
    errors.push({
      code: answer.code,
      severity: Number.isInteger(answer.severity) ? answer.severity : null,
      reason: textOrNull(answer.reason),
      details: textOrNull(answer.details),
      source: answer.source === "ers" ? "ers" : "vault",
      outOfVault: answer.out_of_vault === true,
    });
  }
  const packNotice = packNoticeOf(templateData, errorAnswers);
  const service = buildServiceView(templateData ? templateData.service : null);
  if (hintText === null && errors.length === 0 && packNotice === null && service === null) {
    return null;
  }
  const params = [];
  if (hintText !== null && templateData && Array.isArray(templateData.params)) {
    for (const item of templateData.params) {
      if (item && typeof item.name === "string") {
        params.push({
          name: item.name,
          defaultText: textOrNull(item.default),
          description: textOrNull(item.description),
        });
      }
    }
  }
  const adapter = templateData && templateData.adapter ? templateData.adapter : null;
  return {
    hint: hintText,
    params,
    adapterTitle: adapter ? textOrNull(adapter.title) : null,
    note: templateData ? textOrNull(templateData.vault_note) : null,
    errors,
    outOfVault: errors.some((item) => item.outOfVault),
    packNotice,
    service,
  };
}
