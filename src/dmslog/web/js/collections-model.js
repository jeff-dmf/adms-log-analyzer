// Pure model of the collections panel (no DOM): what a collection row says, the selection that
// becomes the `{"collection": [group]}` filter leaf, the tag map for the grid column, and the
// plain-language outcome of removing one. The DOM layer lives in collections-panel.js.
// Everything that comes from the API is untrusted text: callers write it with textContent only.
import { formatOffset, formatTs } from "./time.js";
import { S, format, countNoun } from "./strings.js";

export const TAG_COLORS = 8; // fixed palette: collection number n always uses color ((n - 1) % 8) + 1

// Color index (1..8) of a collection number.
export function colorIndex(number) {
  const n = Number.isInteger(number) && number > 0 ? number : 1;
  return ((n - 1) % TAG_COLORS) + 1;
}

// The case holds more than one collection: the Collection column and the order warning apply.
export function isMulti(collections) {
  return Array.isArray(collections) && collections.length > 1;
}

// {group id -> "C<n>"} for the grid column and the distinct labels.
export function tagMap(collections) {
  const map = new Map();
  for (const item of collections || []) {
    map.set(item.group, item.tag || format(S.colTag, { number: item.number }));
  }
  return map;
}

// {group id -> {tag, color}} for the grid column.
export function gridTags(collections) {
  const out = new Map();
  for (const item of collections || []) {
    out.set(item.group, { tag: item.tag || format(S.colTag, { number: item.number }), color: colorIndex(item.number) });
  }
  return out;
}

// {group id -> color index}.
export function colorMap(collections) {
  const map = new Map();
  for (const item of collections || []) {
    map.set(item.group, colorIndex(item.number));
  }
  return map;
}

function variantName(variant) {
  return Object.prototype.hasOwnProperty.call(S.colVariantNames, variant) ? S.colVariantNames[variant] : String(variant);
}

// "2026-10-09 14:03:11" from the yyyymmddHHMMSS of the ERS file name (shown as written, no zone).
export function formatCapture(stamp) {
  const m = /^(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})$/.exec(String(stamp || ""));
  return m ? m[1] + "-" + m[2] + "-" + m[3] + " " + m[4] + ":" + m[5] + ":" + m[6] : String(stamp || "");
}

function firstMemberName(item) {
  const first = Array.isArray(item.members) && item.members.length > 0 ? item.members[0] : null;
  return first && first.name ? String(first.name) : String(item.label || "");
}

// Identity line: "ERS 2026-10-09 14:03:11 (zone)", "Folder Log", "File x.log", "Zip x.zip".
export function title(item) {
  const server = item.server ? String(item.server) : S.colServerUnknown;
  if (item.kind === "ers") {
    return format(S.colErs, { capture: formatCapture(item.capture), server });
  }
  if (item.kind === "folder") {
    return format(S.colFolder, { name: firstMemberName(item) });
  }
  if (item.kind === "zip") {
    return format(S.colZip, { name: firstMemberName(item) });
  }
  return format(S.colFile, { name: firstMemberName(item) });
}

// GUID shortened to its first block; the full one goes to the tooltip.
export function shortGuid(guid) {
  const text = String(guid || "");
  return text.length > 8 ? text.slice(0, 8) + "..." : text;
}

// "Sensitive only: Enterprise zip not added yet" | "" (nothing missing).
export function missingLine(item) {
  if (!item.missing) {
    return "";
  }
  const have = Array.isArray(item.variants) && item.variants.length > 0
    ? item.variants.map(variantName).join(" + ")
    : "";
  const gap = item.missing === "enterprise" ? S.colMissingEnterprise : S.colMissingSensitive;
  return have === "" ? gap : format(S.colOnly, { have }) + ": " + gap;
}

export function statusLine(item) {
  if (item.status === "indexing") {
    return S.colIndexing;
  }
  return item.status === "error" ? S.colError : "";
}

export function countsLine(item) {
  return format(S.colRecords, {
    records: countNoun("nRecord", item.records),
    sources: countNoun("nSource", item.sources),
  });
}

export function windowLine(item) {
  if (item.first_ts === null || item.first_ts === undefined || item.last_ts === null || item.last_ts === undefined) {
    return "";
  }
  return format(S.colWindow, { first: formatTs(item.first_ts), last: formatTs(item.last_ts) });
}

// Clock offset of a collection (9.15): "Clock +1.234s (from pair)", "" without an offset. `clock_source` says how it was set.
export function clockLine(item) {
  const ms = item ? item.clock_offset_ms : 0;
  if (!Number.isSafeInteger(ms) || ms === 0) {
    return "";
  }
  const method = item.clock_source ? item.clock_source.method : "";
  const source = method === "pair" ? S.ckSourcePair : method === "manual" ? S.ckSourceManual : "";
  const offset = formatOffset(ms);
  return source === "" ? format(S.ckBadgeBare, { offset }) : format(S.ckBadge, { offset, source });
}

// Tooltip of that line: what the offset does, and when it was set.
export function clockTitle(item) {
  const when = item && item.clock_source && typeof item.clock_source.set_at === "string" ? item.clock_source.set_at : "";
  return when === "" ? S.ckBadgeTitle : S.ckBadgeTitle + " " + format(S.ckBadgeSet, { when });
}

// "adjusted" while some collection has an offset (GET /collections `clocks`); anything else is "raw".
export function clocksMode(value) {
  return value === "adjusted" ? "adjusted" : "raw";
}

// Everything the row needs, as plain strings.
export function describe(item) {
  return {
    group: item.group,
    tag: item.tag || format(S.colTag, { number: item.number }),
    color: colorIndex(item.number),
    title: title(item),
    guid: item.guid ? shortGuid(item.guid) : "",
    guidFull: item.guid ? String(item.guid) : "",
    counts: countsLine(item),
    window: windowLine(item),
    missing: missingLine(item),
    status: statusLine(item),
    statusCode: item.status || "ready",
    members: (item.members || []).map((m) => ({
      id: m.id,
      name: String(m.name || ""),
      variant: m.variant ? variantName(m.variant) : "",
      counts: countsLine(m),
      status: statusLine(m),
    })),
  };
}

// ---- selection ---------------------------------------------------------------------------------

// Groups that still exist, in the order of the list; used when the list is reloaded.
export function pruneSelection(selected, collections) {
  const present = new Set((collections || []).map((c) => c.group));
  return [...selected].filter((group) => present.has(group));
}

// The filter leaf of the selection: null (all collections) or {collection: [group, ...]}.
export function selectionLeaf(selected) {
  const groups = [...selected];
  return groups.length === 0 ? null : { collection: groups };
}

// Status line under the list: which collections the view shows.
export function selectionLine(selected, collections) {
  const tags = tagMap(collections);
  const names = [...selected].map((g) => tags.get(g)).filter((t) => t !== undefined);
  return names.length === 0 ? S.colShowingAll : format(S.colShowing, { tags: names.join(", ") });
}

// Source ids that narrow the grid's time range and scrollbar: the chosen sources, limited to the
// chosen collections (sources carry the `collection` group id).
export function effectiveSourceIds(sources, sourceIds, collectionGroups) {
  const groups = new Set(collectionGroups || []);
  if (groups.size === 0) {
    return sourceIds.slice();
  }
  const chosen = new Set(sourceIds);
  return sources
    .filter((s) => groups.has(s.collection) && (chosen.size === 0 || chosen.has(s.id)))
    .map((s) => s.id);
}

// ---- panels that query by collection ---------------------------------------------------------

// The events and stats routes take ONE `collection` group. With exactly one collection chosen the
// queries carry it; with none (all) or several they carry nothing (the panels show every collection).
export function collectionParam(groups, params) {
  return groups && groups.length === 1 ? { ...params, collection: groups[0] } : params;
}

// A copy of `api` whose event and stats queries follow the chosen collection; getGroups() is read per call.
export function scopeApi(api, getGroups) {
  const scoped = { ...api };
  for (const name of ["events", "eventStats", "eventHistogram", "statsMethods", "statsSeries", "missingEvents", "missingSummary"]) {
    if (typeof api[name] === "function") {
      scoped[name] = (caseId, params, signal) => api[name](caseId, collectionParam(getGroups(), params || {}), signal);
    }
  }
  return scoped;
}

// ---- removal -----------------------------------------------------------------------------------

// Text of the second step, naming the collection and what goes with it.
export function removeConfirmText(item) {
  const d = describe(item);
  return format(S.colRemoveConfirm, {
    tag: d.tag,
    title: d.title,
    sources: countNoun("nSource", item.sources),
    records: countNoun("nRecord", item.records),
  });
}

// Plain-language message of a failed DELETE; `error` is an ApiError-like {status, code, message}.
export function removeErrorMessage(error, tag) {
  const code = error && error.code ? String(error.code) : "";
  if (code === "job_running") {
    return format(S.colJobRunning, { tag });
  }
  if (code === "collection_followed") {
    return format(S.colFollowedTag, { tag });
  }
  if (code === "collection_not_found") {
    return format(S.colGone, { tag });
  }
  return String(error && error.message ? error.message : error);
}

export function removedMessage(tag, summary) {
  const s = summary || {};
  return format(S.colRemoved, {
    tag,
    sources: countNoun("nSource", s.sources),
    records: countNoun("nRecord", s.records),
    bookmarks: countNoun("nBookmark", s.bookmarks),
  });
}

// ---- column preference -------------------------------------------------------------------------

export const COLUMN_KEY = "dmslog.collectionColumn";

// The user's choice for the Collection column: on unless they turned it off in the Columns menu.
export function loadColumnPref(storage) {
  try {
    return !(storage && storage.getItem(COLUMN_KEY) === "off");
  } catch (err) {
    return true;
  }
}

export function saveColumnPref(on, storage) {
  try {
    if (!storage) {
      return;
    }
    if (on) {
      storage.removeItem(COLUMN_KEY);
    } else {
      storage.setItem(COLUMN_KEY, "off");
    }
  } catch (err) {
    // Not persisted; the column still follows the menu this session.
  }
}

// The column shows only for a case with more than one collection, and only when the user did not hide it.
export function columnShown(collections, pref) {
  return isMulti(collections) && Boolean(pref);
}
