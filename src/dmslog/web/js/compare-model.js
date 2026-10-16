// Pure model of "Compare two records" (no DOM): the two-step selection and the loading flow of the
// side-by-side diff. Record text comes from the ERS (untrusted): it is only shaped here.
import { S, format, pluralKey } from "./strings.js";
import { formatTs } from "./time.js";

export const CONTEXT_LINES = 3; // unchanged lines kept around a change when unchanged runs are hidden

// Two-step selection: the first call marks a record, the second (on another record) asks for the
// comparison, a repeat on the first record cancels. Returns {action: "mark"|"compare"|"cancel", ...}.
export function createCompareSelection() {
  let first = null;
  return {
    get first() {
      return first;
    },
    pick(entryId) {
      if (!Number.isSafeInteger(entryId)) {
        return { action: "none" };
      }
      if (first === null) {
        first = entryId;
        return { action: "mark", a: entryId };
      }
      if (first === entryId) {
        first = null;
        return { action: "cancel" };
      }
      const a = first;
      first = null;
      return { action: "compare", a, b: entryId };
    },
    reset() {
      first = null;
    },
  };
}

// Label of the "Compare" button for the current selection state.
export function compareButtonLabel(first, currentId) {
  return first !== null && first !== currentId ? format(S.cmpButtonSecond, { id: first }) : S.cmpButton;
}

// Rows for the diff table. ops: the server's opcodes. With `hideEqual`, long unchanged runs collapse
// into one {kind: "skip", count} row, keeping CONTEXT_LINES lines around the changes.
export function buildDiffRows(ops, hideEqual) {
  const rows = [];
  const list = Array.isArray(ops) ? ops : [];
  list.forEach((op, index) => {
    const a = Array.isArray(op.a) ? op.a : [];
    const b = Array.isArray(op.b) ? op.b : [];
    if (op.tag === "equal") {
      const first = index === 0;
      const last = index === list.length - 1;
      const head = first ? 0 : CONTEXT_LINES;
      const tail = last ? 0 : CONTEXT_LINES;
      if (hideEqual && a.length > head + tail + 1) {
        const shown = (from, to) => {
          for (let i = from; i < to; i += 1) {
            rows.push({ kind: "equal", a: a[i], b: b[i], aLine: op.a_start + i, bLine: op.b_start + i });
          }
        };
        shown(0, head);
        rows.push({ kind: "skip", count: a.length - head - tail });
        shown(a.length - tail, a.length);
        return;
      }
      for (let i = 0; i < a.length; i += 1) {
        rows.push({ kind: "equal", a: a[i], b: b[i], aLine: op.a_start + i, bLine: op.b_start + i });
      }
      return;
    }
    const height = Math.max(a.length, b.length);
    for (let i = 0; i < height; i += 1) {
      const hasA = i < a.length;
      const hasB = i < b.length;
      rows.push({
        kind: hasA && hasB ? "change" : hasA ? "remove" : "add",
        a: hasA ? a[i] : null,
        b: hasB ? b[i] : null,
        aLine: hasA ? op.a_start + i : null,
        bLine: hasB ? op.b_start + i : null,
      });
    }
  });
  return rows;
}

// Last segment of a path of the ERS ("Log/adapter.log" -> "adapter.log"); "" when there is none.
export function baseName(path) {
  const parts = String(path === undefined || path === null ? "" : path).split(/[\\/]/).filter(Boolean);
  return parts.length > 0 ? parts[parts.length - 1] : "";
}

// Header facts of one record of the answer: what a person recognizes (time, source file, message type) and not the
// internal record number, the full path or the template code. `title` is the one-line form.
export function buildSideHeader(record) {
  const tid = record.tid === null || record.tid === undefined ? "-" : "0x" + Number(record.tid).toString(16);
  const time = formatTs(record.ts) || "-"; // null, undefined and NaN have no time
  const source = baseName(record.file_name) || baseName(record.member) || "-";
  const type = record.tpl_title ? String(record.tpl_title) : record.tpl ? String(record.tpl) : "-";
  return {
    time,
    source,
    type,
    tid,
    title: [time, source, type].join(" \u00b7 "),
    items: [
      { label: S.cmpMetaTime, text: time },
      { label: S.cmpMetaSource, text: source },
      { label: S.cmpMetaTid, text: tid },
      { label: S.cmpMetaTemplate, text: type },
    ],
  };
}

export function statsText(stats) {
  return format(S[pluralKey("cmpStats", stats.equal)], {
    changed: stats.changed, removed: stats.removed, added: stats.added, equal: stats.equal,
  });
}

function errorText(error) {
  return error && error.message ? error.message : "";
}

// state.status: idle | nocase | loading | ready | error.
export function createCompareFlow({ api, getCase, onState }) {
  let generation = 0;
  let controller = null;
  const state = { caseId: null, a: null, b: null, data: null, status: "idle", error: null };

  function emit() {
    onState(state);
  }

  function close() {
    generation += 1;
    if (controller) {
      controller.abort();
      controller = null;
    }
    state.caseId = null;
    state.a = null;
    state.b = null;
    state.data = null;
    state.status = "idle";
    state.error = null;
    emit();
  }

  async function open(a, b) {
    close();
    const caseId = getCase() || null;
    state.a = a;
    state.b = b;
    if (!caseId) {
      state.status = "nocase";
      emit();
      return;
    }
    const token = generation;
    controller = new AbortController();
    const signal = controller.signal;
    state.caseId = caseId;
    state.status = "loading";
    emit();
    try {
      const data = await api.compare(caseId, a, b, signal);
      if (token !== generation) {
        return;
      }
      state.data = data;
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

  return { state, open, close };
}
