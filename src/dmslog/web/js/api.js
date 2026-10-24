// Fetch client for the local API. Errors carry the server code/message; 403 means the session is gone.
import { S } from "./strings.js";

export class ApiError extends Error {
  constructor(status, code, message) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }

  get kind() {
    return errorKind(this);
  }
}

// What kind of failure an error is, to treat it differently: "network" (server unreachable; the status bar owns it),
// "forbidden" (session gone), "timeout" (408 or a *_timeout code: the same request may succeed later), "server" (5xx),
// "query" (any other 4xx: the request itself is wrong, repeating it cannot help) or "unknown".
export function errorKind(err) {
  if (!err) {
    return "unknown";
  }
  const status = Number(err.status);
  if (err.code === "network" || status === 0) {
    return "network";
  }
  if (status === 403) {
    return "forbidden";
  }
  if (status === 408 || /timeout/i.test(String(err.code || ""))) {
    return "timeout";
  }
  if (status >= 500) {
    return "server";
  }
  if (status >= 400) {
    return "query";
  }
  return "unknown";
}

// Repeating the same request can only help when the failure was not caused by the request itself.
export function isRetryable(kind) {
  return kind !== "query" && kind !== "forbidden";
}

let forbiddenHandler = null;
let networkHandler = null;

export function onForbidden(handler) {
  forbiddenHandler = handler;
}

// Called when a request cannot reach the server at all (server stopped, network down): the status bar shows it.
export function onNetworkError(handler) {
  networkHandler = handler;
}

// A consumer that discards an answer (a newer request replaced it) says so by aborting its signal, or by binding a
// check to it: the global notice (Disconnected / session) is then not raised for a response nobody uses.
const currentChecks = new WeakMap();

export function bindCurrent(signal, isCurrent) {
  if (signal && typeof isCurrent === "function") {
    currentChecks.set(signal, isCurrent);
  }
  return signal;
}

function stillWanted(signal) {
  if (!signal) {
    return true;
  }
  if (signal.aborted) {
    return false;
  }
  const check = currentChecks.get(signal);
  return check === undefined || Boolean(check());
}

function networkFailed(signal) {
  if (networkHandler && stillWanted(signal)) {
    networkHandler();
  }
  return new ApiError(0, "network", S.errorNetwork);
}

// A body that is already JSON text (the caller guarantees it is valid).
class JsonText {
  constructor(text) {
    this.text = text;
  }
}

// Revisions of panels are 64-bit integers. A JS number holds them exactly only up to 2^53, so in an answer a "rev" of
// 16 or more digits is read as its digits (a string; panels-model.js cleanRev) and goes back to the server as the same
// digits written as a number. `"rev"` inside a string is always `\"rev\"`, which the pattern does not match.
const BIG_REV = /(?<!\\)"rev"(\s*:\s*)([0-9]{16,19})(?=\s*[,}\]])/g;

async function readJson(response, exactRev) {
  if (!exactRev || typeof response.text !== "function") {
    return response.json();
  }
  return JSON.parse((await response.text()).replace(BIG_REV, '"rev"$1"$2"'));
}

async function request(method, url, body, signal, { exactRev = false } = {}) {
  const options = { method, credentials: "same-origin", signal };
  if (body !== undefined) {
    options.headers = { "Content-Type": "application/json" };
    options.body = body instanceof JsonText ? body.text : JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch(url, options);
  } catch (err) {
    if (err && err.name === "AbortError") {
      throw err;
    }
    throw networkFailed(signal);
  }
  if (response.status === 204) {
    return null;
  }
  let data = null;
  try {
    data = await readJson(response, exactRev);
  } catch (err) {
    data = null;
  }
  if (response.status === 403) {
    if (forbiddenHandler && stillWanted(signal)) {
      forbiddenHandler();
    }
    throw new ApiError(403, "forbidden", S.errorForbidden);
  }
  if (!response.ok) {
    const code = data && data.error ? String(data.error) : "http_" + response.status;
    const message = data && data.message ? String(data.message) : S.errorGeneric + " (" + response.status + ")";
    throw new ApiError(response.status, code, message);
  }
  return data;
}

// The body of PUT /api/panels. `ifRev` may be the digits of a revision a number cannot hold (see BIG_REV).
function panelUpdateBody(id, panel, ifRev) {
  if (ifRev === undefined || ifRev === null) {
    return { id, panel };
  }
  if (typeof ifRev === "string" && /^[0-9]{1,19}$/.test(ifRev)) {
    return new JsonText(JSON.stringify({ id, panel }).slice(0, -1) + ',"if_rev":' + ifRev + "}");
  }
  return { id, panel, if_rev: ifRev };
}

// POST that answers with a file (streamed by the server): `as` is "text" or "blob". Resolves to
// { data, rows, truncated } from the X-Dmslog-* headers; errors are the usual JSON ones.
async function requestFile(url, body, as, signal) {
  let response;
  try {
    response = await fetch(url, {
      method: "POST",
      credentials: "same-origin",
      signal,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch (err) {
    if (err && err.name === "AbortError") {
      throw err;
    }
    throw networkFailed(signal);
  }
  if (response.status === 403) {
    if (forbiddenHandler && stillWanted(signal)) {
      forbiddenHandler();
    }
    throw new ApiError(403, "forbidden", S.errorForbidden);
  }
  if (!response.ok) {
    let data = null;
    try {
      data = await response.json();
    } catch (err) {
      data = null;
    }
    const code = data && data.error ? String(data.error) : "http_" + response.status;
    const message = data && data.message ? String(data.message) : S.errorGeneric + " (" + response.status + ")";
    throw new ApiError(response.status, code, message);
  }
  let data;
  try {
    data = as === "blob" ? await response.blob() : await response.text();
  } catch (err) {
    if (err && err.name === "AbortError") {
      throw err;
    }
    throw networkFailed(signal); // the stream broke: never a half file
  }
  const rows = Number(response.headers.get("X-Dmslog-Rows"));
  return {
    data,
    rows: Number.isFinite(rows) ? rows : 0,
    truncated: response.headers.get("X-Dmslog-Truncated") === "1",
  };
}

function query(params) {
  const parts = [];
  for (const [key, value] of Object.entries(params)) {
    if (value !== null && value !== undefined && value !== "") {
      parts.push(encodeURIComponent(key) + "=" + encodeURIComponent(String(value)));
    }
  }
  return parts.length > 0 ? "?" + parts.join("&") : "";
}

// A path segment: "." and ".." would be resolved away by the browser, so their dots are escaped.
function segment(value) {
  const text = encodeURIComponent(String(value));
  return /^\.+$/.test(text) ? text.replace(/\./g, "%2E") : text;
}

export const api = {
  health: () => request("GET", "/api/health"),
  cases: () => request("GET", "/api/cases"),
  createCase: (name) => request("POST", "/api/cases", { name }),
  // `first` (optional): open only part first, {from, to, sources} (ARQUITETURA 9.3b); omitted when there is none.
  // `messageQueue`: true reads the message-queue databases of the collection (sensitive); false turns the case option off
  // for this and later additions; undefined leaves the case option as it is (nothing is sent).
  addCollection: (caseId, path, scope, first, messageQueue) =>
    request("POST", "/api/cases/" + encodeURIComponent(caseId) + "/collections", {
      path,
      scope,
      ...(first ? { first } : {}),
      ...(typeof messageQueue === "boolean" ? { message_queue: messageQueue } : {}),
    }),
  // Partial opening: the sources of a path before indexing, the work an earlier partial opening left, and resuming it.
  partial: {
    plan: (caseId, path, scope, signal) =>
      request("POST", "/api/cases/" + encodeURIComponent(caseId) + "/collections/plan", { path, scope }, signal),
    pending: (caseId, signal) =>
      request("GET", "/api/cases/" + encodeURIComponent(caseId) + "/pending", undefined, signal),
    resume: (caseId) => request("POST", "/api/cases/" + encodeURIComponent(caseId) + "/resume", {}),
  },
  progressUrl: (caseId) => "/api/cases/" + encodeURIComponent(caseId) + "/progress",
  sources: (caseId) => request("GET", "/api/sources" + query({ case: caseId })),
  page: (caseId, filter, cursor, dir, limit, signal, columns) =>
    request("POST", "/api/entries/page", { case: caseId, filter, cursor, dir, limit, columns: columns || [] }, signal),
  templates: (caseId, family, signal) =>
    request("GET", "/api/templates" + query({ case: caseId, family }), undefined, signal),
  histogram: (caseId, filter, bucketMs, split, signal) =>
    request("POST", "/api/histogram", { case: caseId, filter, bucket_ms: bucketMs, split }, signal),
  count: (caseId, filter, signal) => request("POST", "/api/entries/count", { case: caseId, filter }, signal),
  distinct: (caseId, filter, field, limit, span, signal, exact) =>
    request("POST", "/api/entries/distinct",
      { case: caseId, filter, field, limit, span: Boolean(span), exact: Boolean(exact) }, signal),
  entry: (caseId, id, signal) =>
    request("GET", "/api/entries/" + encodeURIComponent(id) + query({ case: caseId }), undefined, signal),
  context: (caseId, id, same, before, after, signal) =>
    request("GET", "/api/entries/" + encodeURIComponent(id) + "/context" +
      query({ case: caseId, same, before, after }), undefined, signal),
  views: {
    list: (caseId, signal) => request("GET", "/api/views" + query({ case: caseId }), undefined, signal),
    save: (caseId, name, filter, columns) =>
      request("POST", "/api/views", { case: caseId, name, filter, columns: columns || null }),
    remove: (caseId, id) =>
      request("DELETE", "/api/views/" + encodeURIComponent(id) + query({ case: caseId })),
    update: (caseId, id, name, filter, columns) =>
      request("PUT", "/api/views/" + encodeURIComponent(id), { case: caseId, name, filter, columns: columns || null }),
  },
  // Bookmarks (params of list: color, q, limit, after) and compare of two records.
  bookmarks: {
    list: (caseId, params, signal) =>
      request("GET", "/api/bookmarks" + query({ ...params, case: caseId }), undefined, signal),
    get: (caseId, id, signal) =>
      request("GET", "/api/bookmarks/" + encodeURIComponent(id) + query({ case: caseId }), undefined, signal),
    create: (caseId, entryId, color, note) =>
      request("POST", "/api/bookmarks", { case: caseId, entry_id: entryId, color, note }),
    update: (caseId, id, changes) =>
      request("PATCH", "/api/bookmarks/" + encodeURIComponent(id), { ...changes, case: caseId }),
    remove: (caseId, id) =>
      request("DELETE", "/api/bookmarks/" + encodeURIComponent(id) + query({ case: caseId })),
    lookup: (caseId, entryIds, signal) =>
      request("POST", "/api/bookmarks/lookup", { case: caseId, entry_ids: entryIds }, signal),
    exportAll: (caseId) => request("GET", "/api/bookmarks/export" + query({ case: caseId })),
    importAll: (caseId, data) => request("POST", "/api/bookmarks/import", { case: caseId, data }),
  },
  compare: (caseId, a, b, signal) =>
    request("GET", "/api/compare" + query({ case: caseId, a, b }), undefined, signal),
  templates: (caseId, family, signal) =>
    request("GET", "/api/templates" + query({ case: caseId, family }), undefined, signal),
  unknown: (caseId, family, limit, signal) =>
    request("GET", "/api/unknown" + query({ case: caseId, family, limit }), undefined, signal),
  knowledge: (caseId, template, signal) =>
    request("GET", "/api/knowledge" + query({ case: caseId, template }), undefined, signal),
  knowledgeError: (caseId, family, code, signal) =>
    request("GET", "/api/knowledge/error" + query({ case: caseId, family, code }), undefined, signal),
  configProducts: (caseId, signal) =>
    request("GET", "/api/config" + query({ case: caseId }), undefined, signal),
  config: (caseId, product, group, signal) =>
    request("GET", "/api/config" + query({ case: caseId, product, group }), undefined, signal),
  // Derived events. `params` may hold kind, name, status, stream, q, from, to, limit, after.
  events: (caseId, params, signal) =>
    request("GET", "/api/events" + query({ ...params, case: caseId }), undefined, signal),
  event: (caseId, id, signal) =>
    request("GET", "/api/events/" + encodeURIComponent(id) + query({ case: caseId }), undefined, signal),
  // Events whose expected sequence did not close (kind, status, name, ..., after) and their summary; repeated records
  // (ARQUITETURA 10.9): which of `ids` (up to 1000) repeat an earlier one, with the spec {window_ms, fields, tid}.
  missingEvents: (caseId, params, signal) =>
    request("GET", "/api/events/missing" + query({ ...params, case: caseId }), undefined, signal),
  missingSummary: (caseId, params, signal) =>
    request("GET", "/api/events/missing/summary" + query({ ...params, case: caseId }), undefined, signal),
  repeatMarks: (caseId, ids, repeat, signal) =>
    request("POST", "/api/entries/repeats", { case: caseId, ids, repeat }, signal),
  eventStats: (caseId, params, signal) =>
    request("GET", "/api/events/stats" + query({ ...params, case: caseId }), undefined, signal),
  eventHistogram: (caseId, params, signal) =>
    request("GET", "/api/events/histogram" + query({ ...params, case: caseId }), undefined, signal),
  statsMethods: (caseId, params, signal) =>
    request("GET", "/api/stats/methods" + query({ ...params, case: caseId }), undefined, signal),
  statsSeries: (caseId, params, signal) =>
    request("GET", "/api/stats/series" + query({ ...params, case: caseId }), undefined, signal),
  // Correlation: links of an entry, the best counterpart pair, records by key.
  entryLinks: (caseId, id, signal) =>
    request("GET", "/api/entries/" + encodeURIComponent(id) + "/links" + query({ case: caseId }), undefined, signal),
  entryHead: (caseId, id, contLines, signal) =>
    request("GET", "/api/entries/" + encodeURIComponent(id) + query({ case: caseId, cont_lines: contLines }),
      undefined, signal),
  sideBySide: (caseId, id, signal) =>
    request("GET", "/api/entries/" + encodeURIComponent(id) + "/side-by-side" + query({ case: caseId }),
      undefined, signal),
  // Payloads: those of one entry, one payload (raw text and optional formatted text), keyset list.
  entryPayloads: (caseId, id, signal) =>
    request("GET", "/api/entries/" + encodeURIComponent(id) + "/payloads" + query({ case: caseId }), undefined, signal),
  payload: (caseId, id, format, signal) =>
    request("GET", "/api/payloads/" + encodeURIComponent(id) + query({ case: caseId, format }), undefined, signal),
  payloadPage: (caseId, params, signal) =>
    request("GET", "/api/payloads/page" + query({ ...params, case: caseId }), undefined, signal),
  keyTypes: (caseId, signal) =>
    request("GET", "/api/keys/types" + query({ case: caseId }), undefined, signal),
  keyRecords: (caseId, type, value, params, signal) =>
    request("GET", "/api/keys/" + segment(type) + "/" + segment(value) +
      query({ ...params, case: caseId }), undefined, signal),
  // Operations and handoffs of one key (R2c): the chain of a job through its processes.
  keyChain: (caseId, type, value, params, signal) =>
    request("GET", "/api/keys/" + segment(type) + "/" + segment(value) + "/chain" +
      query({ ...params, case: caseId }), undefined, signal),
  // Redaction profile (kept in the data directory) and the .txt report built from the server.
  redaction: (signal) => request("GET", "/api/redaction", undefined, signal),
  saveRedaction: (profile, signal) => request("PUT", "/api/redaction", profile, signal),
  exportReport: (caseId, selection, options, signal) =>
    request("POST", "/api/export", { case: caseId, selection, options }, signal),
  // Copy as / export of the view (lists, original lines, CSV); see report/viewexport.py.
  exportView: (caseId, body, as, signal) =>
    requestFile("/api/export/view", { ...body, case: caseId }, as, signal),
  // File picker: native dialog opened by the server, and the built-in folder browser (ARQUITETURA 10.4).
  // `pick` resolves when the user closes the dialog; `cancelPick` closes an open one.
  fs: {
    capabilities: (signal) => request("GET", "/api/fs/capabilities", undefined, signal),
    list: (path, signal) => request("GET", "/api/fs/list" + query({ path }), undefined, signal),
    pick: (kind, multiple) => request("POST", "/api/fs/pick", { kind, multiple }),
    cancelPick: () => request("DELETE", "/api/fs/pick"),
  },
  // View presets (curated + the user's file): list for a case, create, update, delete, export, import.
  presets: {
    list: (caseId, signal) => request("GET", "/api/presets" + query({ case: caseId }), undefined, signal),
    create: (preset) => request("POST", "/api/presets", preset),
    update: (match, preset) => request("PUT", "/api/presets", { match, preset }),
    remove: (name, family) => request("DELETE", "/api/presets" + query({ name, family })),
    export: (format) => request("GET", "/api/presets/export" + query({ format })),
    import: (format, data) => request("POST", "/api/presets/import", { format, data }),
  },
  // Versioned preferences package (ARQUITETURA 10.12). `sections` must name at least one section: an empty list would
  // reach the server as "no filter" and export all of them, redaction terms included.
  preferences: {
    export: (sections, signal) => (Array.isArray(sections) && sections.length > 0
      ? request("GET", "/api/preferences/export" + query({ sections: sections.join(",") }), undefined, signal)
      : Promise.reject(new TypeError("export needs at least one section"))),
    import: (body, signal) => request("POST", "/api/preferences/import", body, signal),
  },
  // Highlight rules of the case (store/highlights.py), batch marking of rows and the ]/[ navigation.
  highlights: {
    list: (caseId, signal) => request("GET", "/api/highlights" + query({ case: caseId }), undefined, signal),
    create: (caseId, rule) => request("POST", "/api/highlights", { ...rule, case: caseId }),
    update: (caseId, id, changes) =>
      request("PATCH", "/api/highlights/" + encodeURIComponent(id), { ...changes, case: caseId }),
    remove: (caseId, id) => request("DELETE", "/api/highlights/" + encodeURIComponent(id) + query({ case: caseId })),
    order: (caseId, ids) => request("PUT", "/api/highlights/order", { case: caseId, ids }),
    key: (caseId, type, value, color) => request("POST", "/api/highlights/key", { case: caseId, type, value, color }),
    mark: (caseId, rules, ids, signal) =>
      request("POST", "/api/entries/highlight", { case: caseId, rules, ids }, signal),
    next: (caseId, filter, rules, cursor, dir, signal) =>
      request("POST", "/api/entries/highlight/next", { case: caseId, filter, rules, cursor, dir }, signal),
  },
  // Collections of a case (U21): one group per ERS capture, folder or file; list and remove one group.
  collections: (caseId, signal) =>
    request("GET", "/api/cases/" + encodeURIComponent(caseId) + "/collections", undefined, signal),
  removeCollection: (caseId, group) =>
    request("DELETE", "/api/cases/" + encodeURIComponent(caseId) + "/collections/" + encodeURIComponent(group)),
  // Message queue (issue #74, docs/notas/fila-mensagens-desenho.md 4.1). `group` null = every collection.
  queueSummary: (caseId, signal) =>
    request("GET", "/api/queue/summary?case=" + encodeURIComponent(caseId), undefined, signal),
  queueItems: (caseId, params, signal) => {
    const query = new URLSearchParams({ case: caseId });
    for (const [key, value] of Object.entries(params || {})) {
      if (value !== undefined && value !== null && value !== "") {
        query.set(key, String(value));
      }
    }
    return request("GET", "/api/queue/items?" + query.toString(), undefined, signal);
  },
  queuePreview: (caseId, id, form, signal) =>
    request(
      "GET",
      "/api/queue/items/" + segment(id) + "/preview?case=" + encodeURIComponent(caseId) + "&form=" + (form === "stored" ? "stored" : "wire"),
      undefined,
      signal,
    ),
  queueRead: (caseId, group) => request("POST", "/api/cases/" + encodeURIComponent(caseId) + "/queue/read", { group }),
  queueRemove: (caseId, group) =>
    request("DELETE", "/api/cases/" + encodeURIComponent(caseId) + "/queue" + (group === null || group === undefined ? "" : "?group=" + encodeURIComponent(group))),
  // Clock offset of a collection (9.15, ARQUITETURA 10.13): set from {anchor, target} (entry ids; dry_run: true computes only)
  // or from {offset_ms}; reset puts the offset back to 0. `group` is the collection group id.
  clock: {
    set: (caseId, group, body, signal) =>
      request("PUT", "/api/cases/" + encodeURIComponent(caseId) + "/collections/" + encodeURIComponent(group) + "/clock", body, signal),
    reset: (caseId, group, signal) =>
      request("DELETE", "/api/cases/" + encodeURIComponent(caseId) + "/collections/" + encodeURIComponent(group) + "/clock", undefined, signal),
  },
  // Local alerts (store/alerts.py): the user's rules, and the alert history of a case.
  alerts: {
    rules: (caseId, signal) => request("GET", "/api/alerts/rules" + query({ case: caseId }), undefined, signal),
    createRule: (rule) => request("POST", "/api/alerts/rules", rule),
    updateRule: (name, rule) => request("PUT", "/api/alerts/rules", { match: { name }, rule }),
    deleteRule: (name) => request("DELETE", "/api/alerts/rules" + query({ name })),
    history: (caseId, signal) => request("GET", "/api/alerts/history" + query({ case: caseId }), undefined, signal),
    clearHistory: (caseId) => request("DELETE", "/api/alerts/history" + query({ case: caseId })),
  },
  // The user's panels (store/panels.py, store/panelrun.py; ARQUITETURA 10.14). `panel` is {name, widgets}; `ifRev` is the
  // revision the caller saw (409 rev_conflict when it moved). `run` runs the STORED panel (all widgets, or `widgets`: ids).
  panels: {
    list: (signal) => request("GET", "/api/panels", undefined, signal, { exactRev: true }),
    create: (panel) => request("POST", "/api/panels", panel, undefined, { exactRev: true }),
    update: (id, panel, ifRev) => request("PUT", "/api/panels", panelUpdateBody(id, panel, ifRev), undefined, { exactRev: true }),
    remove: (id) => request("DELETE", "/api/panels" + query({ id })),
    run: (caseId, id, widgets, signal) =>
      request("POST", "/api/panels/run", widgets ? { case: caseId, id, widgets } : { case: caseId, id }, signal, { exactRev: true }),
  },
  // Declarative aggregation (store/aggregate.py): JSON result, or the same result as CSV text.
  aggregate: (caseId, body, signal) => request("POST", "/api/entries/aggregate", { ...body, case: caseId }, signal),
  aggregateCsv: (caseId, body, signal) =>
    requestFile("/api/entries/aggregate", { ...body, case: caseId, format: "csv" }, "text", signal),
  // Comparison of two views by message type (ARQUITETURA 10.10). The answer is one page of aligned rows; `offset` pages it.
  compareViews: {
    run: (caseId, body, signal) => request("POST", "/api/entries/compare", { ...body, case: caseId }, signal),
  },
  // Ad hoc regex extractor (ARQUITETURA 10.11): `check` needs no case; `promote` only returns the TOML text.
  extract: {
    check: (body, signal) => request("POST", "/api/extract/check", body, signal),
    run: (caseId, body, signal) => request("POST", "/api/entries/extract", { ...body, case: caseId }, signal),
    values: (caseId, body, signal) => request("POST", "/api/entries/extract/values", { ...body, case: caseId }, signal),
    promote: (caseId, body, signal) =>
      request("POST", "/api/entries/extract/promote", { ...body, case: caseId }, signal),
  },
};
