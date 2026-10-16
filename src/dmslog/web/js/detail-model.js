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

// ---- remembered choices of the detail (UI-20), no DOM ---------------------------------------------------------
// Which collapsible section is open and the payload's "Wrap lines". The storage is injected and every access is guarded, because storage can be missing or throw
// (private windows, blocked site data) and the detail must work without it.

export const PREFS_KEY = "dmslog.detail.prefs";

// Sections 5 to 9 of the detail, in reading order. The payload is the heavy one: closed until the reader opens it.
export const SECTION_KEYS = ["knowledge", "fields", "keys", "links", "payload", "bookmark"];
export const SECTION_DEFAULTS = { knowledge: true, fields: true, keys: true, links: true, payload: false, bookmark: true };

// What a stored value may be: an object with boolean section flags (known keys only) and a boolean wrap flag.
export function parsePrefs(raw) {
  const out = { sections: {}, wrap: false };
  let data = null;
  try {
    data = typeof raw === "string" ? JSON.parse(raw) : null;
  } catch (err) {
    data = null;
  }
  if (!data || typeof data !== "object" || Array.isArray(data)) {
    return out;
  }
  if (data.sections && typeof data.sections === "object" && !Array.isArray(data.sections)) {
    for (const key of SECTION_KEYS) {
      if (typeof data.sections[key] === "boolean") {
        out.sections[key] = data.sections[key];
      }
    }
  }
  out.wrap = data.wrap === true;
  return out;
}

// storage: { getItem, setItem } or null.
export function createDetailPrefs(storage) {
  let state = { sections: {}, wrap: false };
  try {
    state = parsePrefs(storage ? storage.getItem(PREFS_KEY) : null);
  } catch (err) {
    state = { sections: {}, wrap: false };
  }
  const save = () => {
    try {
      if (storage) {
        storage.setItem(PREFS_KEY, JSON.stringify(state));
      }
    } catch (err) {
      // not remembered; the choice still holds for this session
    }
  };
  return {
    sectionOpen(key) {
      if (typeof state.sections[key] === "boolean") {
        return state.sections[key];
      }
      return SECTION_DEFAULTS[key] !== false;
    },
    setSectionOpen(key, open) {
      if (SECTION_KEYS.includes(key)) {
        state.sections[key] = Boolean(open);
        save();
      }
    },
    wrap() {
      return state.wrap;
    },
    setWrap(value) {
      state.wrap = Boolean(value);
      save();
    },
  };
}

// The severity class of a record (1 critical .. 5 noise); anything else gets none (no colour, the label still shows).
export function severityClass(sev) {
  return Number.isInteger(sev) && sev >= 1 && sev <= 5 ? "dt-sev-" + sev : "";
}

const intOrNull = (value) => (value === null || value === undefined || value === "" || !Number.isFinite(Number(value)) ? null : Number(value));

// What the header says, as plain data. info: { name, sensitive, tag } of the source (all optional).
// pid/tid are shown only when the log has them; a TID is shown in hex as the logs write it.
export function headerParts(entry, info, { formatTime, levelNames }) {
  const pid = intOrNull(entry.pid);
  const tid = intOrNull(entry.tid);
  const level = Number.isInteger(entry.level) ? levelNames[entry.level] || "" : "";
  return {
    time: formatTime(entry.ts),
    level,
    sevClass: severityClass(entry.sev),
    source: info && info.name ? String(info.name) : "",
    sensitive: Boolean(info && info.sensitive === true),
    pid: pid === null ? null : String(pid),
    tid: tid === null ? null : "0x" + tid.toString(16),
    tag: info && info.tag ? String(info.tag) : "",
  };
}
