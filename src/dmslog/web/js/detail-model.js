// Pure stale-response guard for the detail panel (no DOM): a generation counter plus the
// case id and entry id the request was issued for.
export class RequestGuard {
  constructor() {
    this.generation = 0;
    this.controller = null;
  }

  // Invalidate every pending request and abort the in-flight one.
  invalidate() {
    this.generation += 1;
    if (this.controller) {
      this.controller.abort();
      this.controller = null;
    }
  }

  // Start a request; returns a ticket that is valid until the next invalidate()/begin().
  begin(caseId, entryId) {
    this.invalidate();
    this.controller = typeof AbortController === "undefined" ? null : new AbortController();
    return {
      generation: this.generation,
      caseId,
      entryId,
      signal: this.controller ? this.controller.signal : undefined,
    };
  }

  // True only if nothing newer started and the case and entry are still the current ones.
  isCurrent(ticket, currentCaseId, currentEntryId) {
    return (
      ticket.generation === this.generation &&
      ticket.caseId === currentCaseId &&
      (currentEntryId === undefined || ticket.entryId === currentEntryId)
    );
  }
}

// The context panel may only load for an entry that was fetched in the CURRENT case. Between a
// case switch and the next /sources answer the old entry would otherwise be queried in the new case.
export function canLoadContext(entry, entryCase, currentCaseId) {
  return Boolean(entry) && currentCaseId !== null && currentCaseId !== undefined && entryCase === currentCaseId;
}
