// Pure logic of the unknown-templates panel (no DOM): row handling and the TOML sketch.
// Signatures come from untrusted logs: everything is escaped before it reaches a regex or TOML.

export const MAX_REGEX_CHARS = 1000; // engine limit (templates.py MAX_REGEX_CHARS)
export const MAX_GROUPS = 24; // engine limit (templates.py MAX_GROUPS)
const MAX_SIGNATURE_CHARS = 240; // signature.py truncates here: a longer text has no known end

// Marker -> [regex, sample value, field type or null, group name]. Every repeat is bounded.
const MARKERS = {
  ts: [String.raw`\d{4}-\d\d-\d\d[ T]\d\d:\d\d:\d\d(?:[.,]\d{1,9})?(?:Z|[+-]\d\d:?\d\d)?`,
    "2000-01-01 00:00:00", null],
  guid: ["[0-9a-fA-F-]{36}", "00000000-0000-4000-8000-000000000000", "guid"],
  url: [String.raw`[^\s"'<>]{1,64}`, "example.invalid/x", null],
  path: [String.raw`[^\s"'<>|]{1,64}`, "C:\\example\\x", null],
  ip: [String.raw`\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(?::\d{1,5})?`, "10.0.0.1", null],
  hex: ["[0-9a-fA-Fx]{1,64}", "0x1a", "hex"],
  s: [String.raw`(?:'[^']{0,64}'|"[^"]{0,64}")`, '"sample"', null],
  n: [String.raw`\d{1,18}(?:\.\d{1,18})?`, "1", "int"],
};
// Short form used when the full one would pass the engine regex length limit.
const SHORT = String.raw`\S{1,64}`;
// Level 1 (short): <ts> keeps a bounded form compatible with its sample (one space).
// Level 2 (tiny): every marker is SHORT, so the <ts> sample switches to a space-free form.
const SHORT_TS = String.raw`\d{4}-\d\d-\d\d[ T]\d\d:\d\d:\d\d(?:\.\d{1,9})?`;
const TINY_TS_SAMPLE = "2000-01-01T00:00:00";
const MARKER_RE = /<(ts|guid|url|path|ip|hex|s|n)>/g;
const REGEX_META = /[\\.^$*+?{}[\]|()]/g;
const FAMILY_RE = /^_?[A-Za-z][A-Za-z0-9_]{0,31}$/;

export function escapeRegex(text) {
  return String(text).replace(REGEX_META, "\\$&");
}

// Control characters never reach the sketch (TOML literal strings reject them).
export function cleanSignature(signature) {
  return String(signature).replace(/[\u0000-\u001f\u007f]+/g, " ").replace(/ {2,}/g, " ").trim();
}

function splitSignature(signature) {
  const parts = []; // { text } or { marker }
  let last = 0;
  for (const m of signature.matchAll(MARKER_RE)) {
    if (m.index > last) {
      parts.push({ text: signature.slice(last, m.index) });
    }
    parts.push({ marker: m[1] });
    last = m.index + m[0].length;
  }
  if (last < signature.length) {
    parts.push({ text: signature.slice(last) });
  }
  return parts;
}

function build(parts, anchoredEnd, level) {
  const seen = {};
  const fields = {};
  let groups = 0;
  let regex = "^";
  for (const part of parts) {
    if (part.text !== undefined) {
      regex += escapeRegex(part.text);
      continue;
    }
    const [pattern, , type] = MARKERS[part.marker];
    const body = level === 0 ? pattern : (level === 1 && part.marker === "ts" ? SHORT_TS : SHORT);
    if (groups >= MAX_GROUPS) {
      regex += "(?:" + body + ")";
      continue;
    }
    seen[part.marker] = (seen[part.marker] || 0) + 1;
    const name = seen[part.marker] === 1 ? part.marker : part.marker + "_" + seen[part.marker];
    regex += "(?P<" + name + ">" + body + ")";
    groups += 1;
    if (type) {
      fields[name] = type;
    }
  }
  return { regex: regex + (anchoredEnd ? "$" : ""), fields };
}

// Regex for a normalized signature: literals escaped, markers as named groups.
// Returns { regex, fields } where fields maps group name -> engine field type.
export function signatureToRegex(signature) {
  const clean = cleanSignature(signature);
  const parts = splitSignature(clean);
  const anchoredEnd = clean.length < MAX_SIGNATURE_CHARS;
  for (let level = 0; level < 2; level += 1) {
    const built = build(parts, anchoredEnd, level);
    if (built.regex.length <= MAX_REGEX_CHARS) {
      return { ...built, level };
    }
  }
  return { ...build(parts, anchoredEnd, 2), level: 2 };
}

// A synthetic message that the regex of the same signature matches (markers -> sample values).
// Invariant: the regex of signatureToRegex(signature) matches this example.
export function signatureExample(signature) {
  const tiny = signatureToRegex(signature).level === 2;
  return splitSignature(cleanSignature(signature))
    .map((part) => {
      if (part.text !== undefined) {
        return part.text;
      }
      return tiny && part.marker === "ts" ? TINY_TS_SAMPLE : MARKERS[part.marker][1];
    })
    .join("");
}

function tomlString(text) {
  if (!text.includes("'") ) {
    return "'" + text + "'";
  }
  let out = '"';
  for (const ch of text) {
    if (ch === "\\" || ch === '"') {
      out += "\\" + ch;
    } else {
      out += ch;
    }
  }
  return out + '"';
}

export function safeFamily(family) {
  return FAMILY_RE.test(String(family || "")) ? String(family) : "FAMILY";
}

// Starter template for src/dmslog/adapters/<family>.toml (schema: adapters/_schema.md).
// The example is a synthetic message built from the signature, so the template passes the
// catalog example check as generated; the original signature stays in a comment.
export function buildTomlSketch(row, family) {
  const fam = safeFamily(family || (row && row.family));
  const signature = cleanSignature(row.signature);
  const { regex, fields } = signatureToRegex(signature);
  const codePrefix = fam.replace(/^_+/, "").toUpperCase().replace(/[^A-Z0-9]/g, "") || "NEW";
  const fieldEntries = Object.entries(fields).map(([k, v]) => k + ' = "' + v + '"');
  const lines = [
    "# Starter template from an unknown signature (" + (row.n || 0) + " records). Review before use.",
    "# Signature: " + signature,
    "# Paste the [[template]] block into src/dmslog/adapters/" + fam.toLowerCase() + ".toml (drop the header",
    "# lines if the file already has them), pick a unique code and class, and rename the groups.",
    "schema = 1",
    'family = "' + fam + '"',
    "",
    "[[template]]",
    'code = "' + codePrefix.slice(0, 20) + '-NEW-001"  # placeholder: pick the real ID',
    'class = "other"',
    'title = "TODO: short name"',
    "regex = " + tomlString(regex),
  ];
  if (fieldEntries.length > 0) {
    lines.push("fields = { " + fieldEntries.join(", ") + " }");
  }
  lines.push("examples = [" + tomlString(signatureExample(signature)) + "]");
  return lines.join("\n") + "\n";
}

// ---- table rows ----

export function shareOf(n, total) {
  return total > 0 ? n / total : 0;
}

export function formatShare(fraction) {
  const pct = fraction * 100;
  if (pct > 0 && pct < 0.1) {
    return "<0.1%";
  }
  return pct.toFixed(pct >= 10 ? 0 : 1) + "%";
}

export function buildRows(unknown, totalRecords) {
  const total = totalRecords > 0 ? totalRecords : unknown.reduce((sum, u) => sum + u.n, 0);
  return unknown.map((u) => ({
    family: u.family,
    signature: String(u.signature),
    n: u.n,
    share: shareOf(u.n, total),
    firstEntryId: u.first_entry_id,
  }));
}

export function filterRows(rows, text) {
  const needle = String(text || "").trim().toLowerCase();
  return needle ? rows.filter((r) => r.signature.toLowerCase().includes(needle)) : rows;
}

export const SORTS = ["count", "signature"];

export function sortRows(rows, key, desc) {
  const sign = desc ? -1 : 1;
  const out = rows.slice();
  out.sort((a, b) => {
    const diff = key === "signature" ? (a.signature < b.signature ? -1 : a.signature > b.signature ? 1 : 0) : a.n - b.n;
    return diff !== 0 ? sign * diff : (a.signature < b.signature ? -1 : 1);
  });
  return out;
}
