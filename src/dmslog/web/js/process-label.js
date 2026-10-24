// Pure helpers for the label of a process. Streams of a pool of identical processes (the FunctionHost instances) carry
// `pool` (text) and `instance` (integer) next to `process`; the stored process name is not changed. Texts come from
// strings.js; the defaults below only keep the label readable until the keys exist there.
import { S, format } from "./strings.js";

export function hasPool(obj) {
  return Boolean(obj) && typeof obj.pool === "string" && obj.pool !== "" && Number.isSafeInteger(obj.instance);
}

// "DMS_FunctionHost #7" for a pool instance, the process name otherwise.
export function processLabel(obj) {
  if (!obj) {
    return "";
  }
  if (hasPool(obj)) {
    return format(S.poolInstance || "{pool} #{instance}", { pool: obj.pool, instance: obj.instance });
  }
  return obj.process ? String(obj.process) : "";
}

// "DMSFH / DMS_FunctionHost #7"; the family alone when it equals the label.
export function familyProcessText(obj) {
  const family = (obj && obj.family) || "";
  const label = processLabel(obj);
  return family && label && family !== label ? family + " / " + label : family || label;
}

// Order of two objects of the same pool: by the number of the instance, not by the text ("_2" before "_10").
export function compareInstance(a, b) {
  const left = hasPool(a) ? a.instance : Number.POSITIVE_INFINITY;
  const right = hasPool(b) ? b.instance : Number.POSITIVE_INFINITY;
  return left === right ? 0 : left < right ? -1 : 1;
}

// Keeps the list order, but the members of a pool are gathered where its first member is and sorted by instance.
export function groupPoolItems(items, get = (item) => item) {
  const groups = new Map();
  for (const item of items) {
    const obj = get(item);
    if (hasPool(obj)) {
      if (!groups.has(obj.pool)) {
        groups.set(obj.pool, []);
      }
      groups.get(obj.pool).push(item);
    }
  }
  for (const list of groups.values()) {
    list.sort((a, b) => compareInstance(get(a), get(b)));
  }
  const out = [];
  const done = new Set();
  for (const item of items) {
    const obj = get(item);
    if (!hasPool(obj)) {
      out.push(item);
    } else if (!done.has(obj.pool)) {
      done.add(obj.pool);
      out.push(...groups.get(obj.pool));
    }
  }
  return out;
}
