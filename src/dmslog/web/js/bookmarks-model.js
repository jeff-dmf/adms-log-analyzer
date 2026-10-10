// Pure models of the bookmarks feature (no DOM): the shared marker store used by the grid, the
// detail pane and the panel, and the paged list flow of the panel. Notes and messages come from
// the user and the ERS (untrusted): they are only shaped here and shown through textContent.
import { S, format } from "./strings.js";
import { formatTs } from "./time.js";

export const BOOKMARK_COLORS = ["red", "orange", "yellow", "green", "blue", "purple", "gray"];
export const DEFAULT_COLOR = "yellow";
export const MAX_NOTE = 2000;
export const LOOKUP_BATCH = 500;
export const LIST_LIMIT = 100;

export function colorLabel(color) {
  return (S.bmColorNames && S.bmColorNames[color]) || String(color || "");
}

export function isColor(value) {
  return BOOKMARK_COLORS.includes(value);
}

function errorText(error) {
  return error && error.message ? error.message : "";
}

// A list item of the API -> what the panel shows.
export function buildBookmarkRow(item) {
  return {
    id: item.id,
    entryId: item.entry_id === undefined ? null : item.entry_id,
    ts: item.ts === undefined ? null : item.ts,
    orphan: item.orphan === true,
    color: isColor(item.color) ? item.color : DEFAULT_COLOR,
    colorText: colorLabel(item.color),
    note: item.note === null || item.note === undefined ? "" : String(item.note),
    time: item.orphan === true ? "" : formatTs(item.ts),
    text: item.msg === null || item.msg === undefined ? "" : String(item.msg),
    location: format(S.bmLocation, { member: item.member || "", line: item.line_no }),
    created: String(item.created_at || "").slice(0, 10),
  };
}

// Checks a note typed by the user; returns {note, error} (error: null | "tooLong").
export function checkNote(raw) {
  const note = String(raw === null || raw === undefined ? "" : raw).replace(/\r\n?/g, "\n").trim();
  return { note, error: [...note].length > MAX_NOTE ? "tooLong" : null };
}

// Text for the aria-label of a bookmarked grid row.
export function markerLabel(mark) {
  return mark ? format(S.bmMarkerLabel, { color: colorLabel(mark.color) }) : "";
}

// ---- the shared marker store ----------------------------------------------------------------
// Knows, for the records the grid has shown, whether each one is bookmarked. `ensure(ids)` batches
// the lookups and tells onChange(kind) what happened ("lookup" | "edit" | "reset"). A case change
// or `reset()` drops everything and ignores answers still in flight.

export function createBookmarkStore({ api, getCase, onChange, schedule = (fn) => setTimeout(fn, 30) }) {
  let generation = 0;
  let caseId = null;
  let marks = new Map(); // entry id -> {id, color, has_note}
  let checked = new Set(); // entry ids already looked up (marked or not)
  let queue = new Set();
  let timer = null;
  let controller = null;

  function sync() {
    const current = getCase() || null;
    if (current !== caseId) {
      reset();
      caseId = current;
    }
    return caseId;
  }

  function reset() {
    generation += 1;
    marks = new Map();
    checked = new Set();
    queue = new Set();
    timer = null;
    if (controller) {
      controller.abort();
      controller = null;
    }
    caseId = getCase() || null;
  }

  async function flush() {
    timer = null;
    const forCase = sync();
    if (!forCase || queue.size === 0) {
      queue = new Set();
      return;
    }
    const ids = Array.from(queue).slice(0, LOOKUP_BATCH);
    for (const id of ids) {
      queue.delete(id);
    }
    const token = generation;
    controller = new AbortController();
    const signal = controller.signal;
    try {
      const data = await api.bookmarks.lookup(forCase, ids, signal);
      if (token !== generation) {
        return;
      }
      const found = (data && data.bookmarks) || {};
      for (const id of ids) {
        checked.add(id);
      }
      for (const [key, mark] of Object.entries(found)) {
        marks.set(Number(key), mark);
      }
      onChange("lookup");
    } catch (error) {
      if (token === generation && !(error && error.name === "AbortError")) {
        for (const id of ids) {
          checked.add(id); // do not retry on every scroll; invalidate() tries again
        }
      }
    }
    if (token === generation && queue.size > 0 && timer === null) {
      timer = schedule(flush);
    }
  }

  const store = {
    get: (entryId) => marks.get(entryId) || null,
    ensure(ids) {
      if (!sync()) {
        return;
      }
      for (const id of ids) {
        if (Number.isSafeInteger(id) && !checked.has(id) && !marks.has(id)) {
          queue.add(id);
        }
      }
      if (queue.size > 0 && timer === null) {
        timer = schedule(flush);
      }
    },
    // Forget what is known (bookmarks changed elsewhere, e.g. an import or a delete in the panel).
    invalidate() {
      const keep = caseId;
      reset();
      caseId = keep;
      onChange("reset");
    },
    reset,
    // Bookmarks the record with the default color, or removes its bookmark. Returns the new mark or null.
    async toggle(entryId) {
      const forCase = sync();
      if (!forCase) {
        return null;
      }
      const token = generation;
      const existing = marks.get(entryId);
      if (existing) {
        await api.bookmarks.remove(forCase, existing.id);
        if (token === generation) {
          marks.delete(entryId);
          onChange("edit");
        }
        return null;
      }
      let made;
      try {
        made = await api.bookmarks.create(forCase, entryId, DEFAULT_COLOR, null);
      } catch (error) {
        if (error && error.code === "already_bookmarked") {
          store.invalidate();
          store.ensure([entryId]);
          return null;
        }
        throw error;
      }
      if (token === generation) {
        marks.set(entryId, { id: made.id, color: made.color, has_note: Boolean(made.note) });
        checked.add(entryId);
        onChange("edit");
      }
      return made;
    },
    // Change color and/or note of the record's bookmark; returns the updated bookmark.
    async update(entryId, changes) {
      const forCase = sync();
      const existing = marks.get(entryId);
      if (!forCase || !existing) {
        return null;
      }
      const token = generation;
      const changed = await api.bookmarks.update(forCase, existing.id, changes);
      if (token === generation) {
        marks.set(entryId, { id: changed.id, color: changed.color, has_note: Boolean(changed.note) });
        onChange("edit");
      }
      return changed;
    },
    // Full bookmark (with its note) of a record, or null.
    async load(entryId, signal) {
      const forCase = sync();
      const existing = marks.get(entryId);
      if (!forCase || !existing) {
        return null;
      }
      return api.bookmarks.get(forCase, existing.id, signal);
    },
  };
  return store;
}

// ---- the panel's list flow ---------------------------------------------------------------------
// state.status: idle | nocase | loading | ready | error. A new filter, a refresh, a close or a case
// change starts a new generation: answers of an older one are dropped.

export function createBookmarksFlow({ api, getCase, onState, limit = LIST_LIMIT }) {
  let generation = 0;
  let controller = null;
  const state = {
    caseId: null,
    color: "",
    text: "",
    items: [],
    next: null,
    status: "idle",
    loadingMore: false,
    error: null,
  };

  function emit() {
    onState(state);
  }

  function abort() {
    generation += 1;
    if (controller) {
      controller.abort();
      controller = null;
    }
  }

  // The list a request is made for: null when there is none (no case, or not the current case).
  function ticketNow() {
    const caseId = state.caseId;
    return caseId && caseId === (getCase() || null) ? { generation, caseId } : null;
  }

  function ticketCurrent(ticket) {
    return ticket.generation === generation && ticket.caseId === state.caseId && ticket.caseId === (getCase() || null);
  }

  function params(extra) {
    const out = { limit, ...extra };
    if (state.color) {
      out.color = state.color;
    }
    if (state.text) {
      out.q = state.text;
    }
    return out;
  }

  async function load() {
    abort();
    const caseId = getCase() || null;
    state.items = [];
    state.next = null;
    state.loadingMore = false;
    state.error = null;
    state.caseId = caseId;
    if (!caseId) {
      state.status = "nocase";
      emit();
      return;
    }
    const token = generation;
    controller = new AbortController();
    const signal = controller.signal;
    state.status = "loading";
    emit();
    try {
      const data = await api.bookmarks.list(caseId, params({}), signal);
      if (token !== generation) {
        return;
      }
      state.items = Array.isArray(data.bookmarks) ? data.bookmarks : [];
      state.next = data.next === undefined ? null : data.next;
      state.status = "ready";
      emit();
    } catch (error) {
      if (token === generation && !signal.aborted) {
        state.status = "error";
        state.error = errorText(error);
        emit();
      }
    }
  }

  async function loadMore() {
    if (state.status !== "ready" || state.loadingMore || state.next === null || !state.caseId) {
      return;
    }
    const token = generation;
    const signal = controller.signal;
    state.loadingMore = true;
    emit();
    try {
      const data = await api.bookmarks.list(state.caseId, params({ after: state.next }), signal);
      if (token !== generation) {
        return;
      }
      const known = new Set(state.items.map((item) => item.id));
      for (const item of Array.isArray(data.bookmarks) ? data.bookmarks : []) {
        if (!known.has(item.id)) {
          state.items.push(item);
        }
      }
      state.next = data.next === undefined ? null : data.next;
      state.loadingMore = false;
      emit();
    } catch (error) {
      if (token === generation && !signal.aborted) {
        state.loadingMore = false;
        state.status = "error";
        state.error = errorText(error);
        emit();
      }
    }
  }

  return {
    state,
    open: () => load(),
    refresh: () => load(),
    setFilters({ color, text }) {
      state.color = isColor(color) ? color : "";
      state.text = String(text || "").trim().slice(0, 128);
      return load();
    },
    loadMore,
    close() {
      abort();
      state.items = [];
      state.next = null;
      state.status = "idle";
      state.loadingMore = false;
      state.error = null;
      state.caseId = null;
      emit();
    },
    // Local edits after a successful call, so the list does not need a reload.
    replaceItem(item) {
      const index = state.items.findIndex((it) => it.id === item.id);
      if (index >= 0) {
        state.items[index] = item;
        emit();
      }
    },
    removeItem(id) {
      state.items = state.items.filter((it) => it.id !== id);
      emit();
    },
    // Calls the api for one item and applies the answer ONLY if the list is still the one the
    // request was made for (same case, no reload, filter change or close in between). A failure of
    // a stale request is dropped too. Returns the updated item, or null when the answer was stale.
    async updateItem(id, changes) {
      const ticket = ticketNow();
      if (!ticket) {
        return null;
      }
      let changed;
      try {
        changed = await api.bookmarks.update(ticket.caseId, id, changes);
      } catch (error) {
        if (!ticketCurrent(ticket)) {
          return null;
        }
        throw error;
      }
      if (!ticketCurrent(ticket)) {
        return null;
      }
      const index = state.items.findIndex((it) => it.id === id);
      if (index >= 0) {
        state.items[index] = changed;
        emit();
      }
      return changed;
    },
    // Same for a delete; returns true when applied to the list.
    async deleteItem(id) {
      const ticket = ticketNow();
      if (!ticket) {
        return false;
      }
      try {
        await api.bookmarks.remove(ticket.caseId, id);
      } catch (error) {
        if (!ticketCurrent(ticket)) {
          return false;
        }
        throw error;
      }
      if (!ticketCurrent(ticket)) {
        return false;
      }
      state.items = state.items.filter((it) => it.id !== id);
      emit();
      return true;
    },
  };
}
