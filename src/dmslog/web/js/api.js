// Fetch client for the local API. Errors carry the server code/message; 403 means the session is gone.
import { S } from "./strings.js";

export class ApiError extends Error {
  constructor(status, code, message) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
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

function networkFailed() {
  if (networkHandler) {
    networkHandler();
  }
  return new ApiError(0, "network", S.errorNetwork);
}

async function request(method, url, body, signal) {
  const options = { method, credentials: "same-origin", signal };
  if (body !== undefined) {
    options.headers = { "Content-Type": "application/json" };
    options.body = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch(url, options);
  } catch (err) {
    if (err && err.name === "AbortError") {
      throw err;
    }
    throw networkFailed();
  }
  if (response.status === 204) {
    return null;
  }
  let data = null;
  try {
    data = await response.json();
  } catch (err) {
    data = null;
  }
  if (response.status === 403) {
    if (forbiddenHandler) {
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
    throw networkFailed();
  }
  if (response.status === 403) {
    if (forbiddenHandler) {
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
    throw networkFailed(); // the stream broke: never a half file
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
  addCollection: (caseId, path, scope, first) =>
    request("POST", "/api/cases/" + encodeURIComponent(caseId) + "/collections",
      first ? { path, scope, first } : { path, scope }),
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
  // Local alerts (store/alerts.py): the user's rules, and the alert history of a case.
  alerts: {
    rules: (caseId, signal) => request("GET", "/api/alerts/rules" + query({ case: caseId }), undefined, signal),
    createRule: (rule) => request("POST", "/api/alerts/rules", rule),
    updateRule: (name, rule) => request("PUT", "/api/alerts/rules", { match: { name }, rule }),
    deleteRule: (name) => request("DELETE", "/api/alerts/rules" + query({ name })),
    history: (caseId, signal) => request("GET", "/api/alerts/history" + query({ case: caseId }), undefined, signal),
    clearHistory: (caseId) => request("DELETE", "/api/alerts/history" + query({ case: caseId })),
  },
  // Declarative aggregation (store/aggregate.py): JSON result, or the same result as CSV text.
  aggregate: (caseId, body, signal) => request("POST", "/api/entries/aggregate", { ...body, case: caseId }, signal),
  aggregateCsv: (caseId, body, signal) =>
    requestFile("/api/entries/aggregate", { ...body, case: caseId, format: "csv" }, "text", signal),
};
