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

export function onForbidden(handler) {
  forbiddenHandler = handler;
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
    throw new ApiError(0, "network", S.errorNetwork);
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

function query(params) {
  const parts = [];
  for (const [key, value] of Object.entries(params)) {
    if (value !== null && value !== undefined && value !== "") {
      parts.push(encodeURIComponent(key) + "=" + encodeURIComponent(String(value)));
    }
  }
  return parts.length > 0 ? "?" + parts.join("&") : "";
}

export const api = {
  cases: () => request("GET", "/api/cases"),
  createCase: (name) => request("POST", "/api/cases", { name }),
  addCollection: (caseId, path, scope) =>
    request("POST", "/api/cases/" + encodeURIComponent(caseId) + "/collections", { path, scope }),
  progressUrl: (caseId) => "/api/cases/" + encodeURIComponent(caseId) + "/progress",
  sources: (caseId) => request("GET", "/api/sources" + query({ case: caseId })),
  page: (caseId, filter, cursor, dir, limit, signal, columns) =>
    request("POST", "/api/entries/page", { case: caseId, filter, cursor, dir, limit, columns: columns || [] }, signal),
  templates: (caseId, family, signal) =>
    request("GET", "/api/templates" + query({ case: caseId, family }), undefined, signal),
  histogram: (caseId, filter, bucketMs, split, signal) =>
    request("POST", "/api/histogram", { case: caseId, filter, bucket_ms: bucketMs, split }, signal),
  count: (caseId, filter, signal) => request("POST", "/api/entries/count", { case: caseId, filter }, signal),
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
  },
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
};
