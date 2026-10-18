// Time helpers. `ts` is epoch milliseconds that encode the log's wall-clock text read as UTC
// (no zone conversion), so every getter here is the UTC one: the grid shows exactly the log text.

function pad(value, width) {
  return String(value).padStart(width, "0");
}

export function formatTs(ms) {
  if (ms === null || ms === undefined || !Number.isFinite(ms)) {
    return "";
  }
  const d = new Date(ms);
  return pad(d.getUTCFullYear(), 4) + "-" + pad(d.getUTCMonth() + 1, 2) + "-" + pad(d.getUTCDate(), 2) +
    " " + pad(d.getUTCHours(), 2) + ":" + pad(d.getUTCMinutes(), 2) + ":" + pad(d.getUTCSeconds(), 2) +
    "." + pad(d.getUTCMilliseconds(), 3);
}

// A clock offset in milliseconds as signed seconds: "+1.234s", "-0.500s", "0.000s". Not an integer: "".
export function formatOffset(ms) {
  if (!Number.isSafeInteger(ms)) {
    return "";
  }
  const abs = Math.abs(ms);
  return (ms < 0 ? "-" : ms > 0 ? "+" : "") + Math.floor(abs / 1000) + "." + pad(abs % 1000, 3) + "s";
}

const TIME_PATTERN =
  /^\s*(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}):(\d{2})(?::(\d{2})(?:[.,](\d{1,3}))?)?)?\s*$/;

// Parses "YYYY-MM-DD[ HH:MM[:SS[.mmm]]]" as log wall-clock time; returns epoch ms or null.
export function parseTs(text) {
  const m = TIME_PATTERN.exec(String(text));
  if (!m) {
    return null;
  }
  const [year, month, day] = [Number(m[1]), Number(m[2]), Number(m[3])];
  const hour = m[4] === undefined ? 0 : Number(m[4]);
  const minute = m[5] === undefined ? 0 : Number(m[5]);
  const second = m[6] === undefined ? 0 : Number(m[6]);
  const millis = m[7] === undefined ? 0 : Number(m[7].padEnd(3, "0"));
  if (month < 1 || month > 12 || day < 1 || day > 31 || hour > 23 || minute > 59 || second > 59) {
    return null;
  }
  const ms = Date.UTC(year, month - 1, day, hour, minute, second, millis);
  const back = new Date(ms);
  if (back.getUTCMonth() !== month - 1 || back.getUTCDate() !== day) {
    return null;
  }
  return ms;
}
