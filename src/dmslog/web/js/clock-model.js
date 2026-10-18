// Pure model of the clock alignment between collections (task 9.15, ARQUITETURA 10.13; no DOM). Three small pieces:
//   - the records the user picked as anchor and target (`createPicks`, `recordRef`),
//   - the one validated OPERATION the dialog works on (`pairProblem`, `acceptPreview`, `parseOffset`, `previewText`),
//   - the stable error texts (`errorText`).
// Everything that comes from the API or from a log is untrusted text: callers write it with textContent only.
import { S, format } from "./strings.js";
import { formatOffset, formatTs } from "./time.js";

export const MAX_OFFSET_MS = 31 * 86_400_000; // the server's limit (offset_too_large)

export { formatOffset };

const isId = (value) => Number.isSafeInteger(value) && value > 0;

// "1 d 02 h 03 min 04.500 s" for a length of time (the skew between two clocks).
export function formatSpan(ms) {
  const abs = Math.abs(Number.isFinite(ms) ? ms : 0);
  const days = Math.floor(abs / 86_400_000);
  const hours = Math.floor((abs % 86_400_000) / 3_600_000);
  const minutes = Math.floor((abs % 3_600_000) / 60_000);
  const seconds = ((abs % 60_000) / 1000).toFixed(3);
  const parts = [];
  if (days > 0) {
    parts.push(days + " d");
  }
  if (days > 0 || hours > 0) {
    parts.push(String(hours).padStart(days > 0 ? 2 : 1, "0") + " h");
  }
  if (days > 0 || hours > 0 || minutes > 0) {
    parts.push(String(minutes).padStart(days > 0 || hours > 0 ? 2 : 1, "0") + " min");
  }
  parts.push(seconds.padStart(days > 0 || hours > 0 || minutes > 0 ? 6 : 1, "0") + " s");
  return parts.join(" ");
}

// ---- the picked records -------------------------------------------------------------------------------------------

// What the dialog keeps of a record: ids and times, never the element or the row object (a row leaves the grid window).
// `entry` is a record as GET /api/entries/{id} returns it (id, ts, source_id, and ts_adjusted when the case has offsets).
// groupOfSource(sourceId) -> the collection group of that source or null. Resolves to {ok: true, ref} or {ok: false, reason}.
export function recordRef(entry, { caseId, groupOfSource }) {
  if (!entry || !isId(Number(entry.id)) || caseId === null || caseId === undefined) {
    return { ok: false, reason: "record" };
  }
  if (typeof entry.ts !== "number" || !Number.isFinite(entry.ts)) {
    return { ok: false, reason: "no_time" };
  }
  const group = groupOfSource(entry.source_id);
  if (!Number.isSafeInteger(group)) {
    return { ok: false, reason: "no_collection" };
  }
  const adjusted = typeof entry.ts_adjusted === "number" && Number.isFinite(entry.ts_adjusted) ? entry.ts_adjusted : entry.ts;
  return { ok: true, ref: Object.freeze({ caseId, id: Number(entry.id), ts: entry.ts, tsAdjusted: adjusted, group }) };
}

// The text for a failed recordRef.
export function refReasonText(reason) {
  return reason === "no_time" ? S.ckRefNoTime : reason === "no_collection" ? S.ckRefNoCollection : S.ckErrFailed;
}

// The anchor and the target the user picked, per case: a pick made in another case is never returned.
export function createPicks() {
  let anchor = null;
  let target = null;
  return {
    setAnchor(ref) {
      anchor = ref;
    },
    setTarget(ref) {
      target = ref;
    },
    anchor: (caseId) => (anchor !== null && anchor.caseId === caseId ? anchor : null),
    target: (caseId) => (target !== null && target.caseId === caseId ? target : null),
    clear() {
      anchor = null;
      target = null;
    },
  };
}

// ---- the operation ------------------------------------------------------------------------------------------------

// The pair of the dialog for the collection `group`: the anchor must be in another collection, the target in `group`.
// Returns {anchor, target, problem}: anchor/target are the usable refs (or null) and problem is null, "anchor_same" (the
// picked anchor is in `group` itself) or "target_elsewhere" (the picked target is in another collection).
export function pairOf(group, anchor, target) {
  const usableAnchor = anchor !== null && anchor.group !== group ? anchor : null;
  const usableTarget = target !== null && target.group === group ? target : null;
  let problem = null;
  if (anchor !== null && anchor.group === group) {
    problem = "anchor_same";
  } else if (target !== null && target.group !== group) {
    problem = "target_elsewhere";
  }
  return { anchor: usableAnchor, target: usableTarget, problem };
}

// The body of PUT .../clock for a pair; `dryRun` computes without writing.
export function pairBody(anchor, target, dryRun) {
  return dryRun ? { anchor: anchor.id, target: target.id, dry_run: true } : { anchor: anchor.id, target: target.id };
}

// Manual offset from the text of the field: {ok: true, ms} or {ok: false}. Whole milliseconds, within 31 days.
export function parseOffset(text) {
  const value = String(text === null || text === undefined ? "" : text).trim();
  if (!/^[+-]?\d{1,10}$/.test(value)) {
    return { ok: false };
  }
  const ms = Number(value);
  return Math.abs(ms) <= MAX_OFFSET_MS ? { ok: true, ms: ms === 0 ? 0 : ms } : { ok: false };
}

// Does a dry-run answer belong to this operation and say something usable? The operation is {group, anchor, target}.
export function acceptPreview(result, op) {
  if (!result || typeof result !== "object" || result.group !== op.group || result.applied !== false) {
    return false;
  }
  const { anchor, target } = result;
  return Number.isSafeInteger(result.clock_offset_ms) && Math.abs(result.clock_offset_ms) <= MAX_OFFSET_MS
    && Number.isSafeInteger(result.previous_ms) && Boolean(anchor) && Boolean(target)
    && anchor.entry_id === op.anchor.id && target.entry_id === op.target.id
    && Number.isFinite(anchor.ts) && Number.isFinite(anchor.ts_adjusted)
    && Number.isFinite(target.ts) && Number.isFinite(target.ts_adjusted)
    && Number.isSafeInteger(result.skew_ms);
}

// Does the answer of the real PUT / DELETE belong to the operation?
export function acceptApplied(result, group) {
  return Boolean(result) && result.group === group && result.applied === true && Number.isSafeInteger(result.clock_offset_ms)
    && Number.isSafeInteger(result.previous_ms);
}

// The sentence(s) that say what applying will do. `offset` is the resulting offset, `skew` (pairs only) the target clock's
// lead over the anchor's, `previous` the current offset.
export function previewText({ tag, offset, previous, skew = null }) {
  if (offset === previous) {
    return S.ckNoChange;
  }
  if (offset === 0) {
    return format(S.ckPreviewNone, { tag });
  }
  const lines = [format(S.ckPreviewPair, { tag, offset: formatOffset(offset) })];
  if (skew !== null) {
    lines.push(skew === 0 ? S.ckSkewNone : format(skew > 0 ? S.ckSkewAhead : S.ckSkewBehind, { span: formatSpan(skew) }));
  }
  return lines.join(" ");
}

export function currentText(previous) {
  return previous === 0 ? S.ckCurrentNone : format(S.ckCurrent, { offset: formatOffset(previous) });
}

export function problemText(problem, { tag, other }) {
  if (problem === "anchor_same") {
    return format(S.ckHintAnchorSame, { tag });
  }
  return problem === "target_elsewhere" ? format(S.ckHintTargetElsewhere, { tag, other }) : "";
}

export function timeText(ms) {
  return typeof ms === "number" && Number.isFinite(ms) ? formatTs(ms) : "";
}

// ---- errors -------------------------------------------------------------------------------------------------------

// Static text for a failed request: by the server's code, never by its message (which can name ids and values).
export function errorText(err) {
  const code = err && typeof err.code === "string" ? err.code : "";
  return Object.prototype.hasOwnProperty.call(S.ckErrors, code) ? S.ckErrors[code] : S.ckErrFailed;
}

export function isAbort(err) {
  return Boolean(err) && err.name === "AbortError";
}
