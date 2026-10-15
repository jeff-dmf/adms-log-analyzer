// Opening requests started by boot.js while main.js and its modules are still being fetched (UI-14).
// boot.js calls startPrefetch(); sources.js and main.js take the promises once, with takeCases(),
// takeSources() and takeCollections(). Nothing here touches the DOM. Without boot.js (tests, main.js loaded
// directly) nothing is started and every take*() answers null: the callers then ask the API as before.
//
// A failed prefetch is never handed over as an error: the taker asks the API again with its own request, so the
// connection indicator (api.js hooks, registered by main.js after the failure) sees the failure it should.
import { api } from "./api.js";

let stash = null;

function sameCase(a, b) {
  return a !== null && a !== undefined && b !== null && b !== undefined && String(a) === String(b);
}

// Starts GET /api/cases and, as soon as it answers, GET sources and GET collections of the case the opening will
// select (the first one, like SourcesPanel.loadCases(null)) in parallel.
export function startPrefetch(client = api) {
  if (stash !== null) {
    return;
  }
  const entry = { cases: null, caseId: null, sources: null, collections: null };
  stash = entry;
  entry.cases = client.cases();
  entry.cases.then((data) => {
    if (stash !== entry) {
      return;
    }
    const first = data && Array.isArray(data.cases) ? data.cases[0] : null;
    if (!first) {
      return;
    }
    entry.caseId = first.id;
    entry.sources = client.sources(first.id);
    entry.collections = client.collections(first.id);
    entry.sources.catch(() => {});
    entry.collections.catch(() => {});
  }, () => {});
}

function withRetry(promise, fallback) {
  return promise.catch(() => fallback());
}

// fallback() asks the API again; it runs only when the prefetched request failed.
export function takeCases(fallback) {
  if (stash === null || stash.cases === null) {
    return null;
  }
  const promise = stash.cases;
  stash.cases = null;
  return withRetry(promise, fallback);
}

export function takeSources(caseId, fallback) {
  if (stash === null || stash.sources === null || !sameCase(stash.caseId, caseId)) {
    return null;
  }
  const promise = stash.sources;
  stash.sources = null;
  return withRetry(promise, fallback);
}

export function takeCollections(caseId, fallback) {
  if (stash === null || stash.collections === null || !sameCase(stash.caseId, caseId)) {
    return null;
  }
  const promise = stash.collections;
  stash.collections = null;
  return withRetry(promise, fallback);
}

// Tests only.
export function resetPrefetch() {
  stash = null;
}
