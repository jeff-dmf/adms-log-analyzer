// Minimal CSS cascade for the contrast audit of components.css (UI-10). Not a browser: it supports what the
// component stylesheet uses (type, class, attribute and a few pseudo-class selectors, :is/:not/:where,
// descendant and child combinators, specificity and source order, var() and color-mix() on hex colors).
// Rules inside @media blocks are ignored (forced-colors, reduced motion): only the base cascade is audited.

export const clean = (text) => text.replace(/\r\n?/g, "\n").replace(/\/\*[\s\S]*?\*\//g, "");

function matchBrace(text, open) {
  let depth = 0;
  for (let i = open; i < text.length; i++) {
    if (text[i] === "{") depth++;
    else if (text[i] === "}" && --depth === 0) return i;
  }
  throw new Error("unbalanced braces");
}

function splitTop(text, sep) {
  const out = [];
  let depth = 0, last = 0, quote = null;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (quote) { if (c === quote) quote = null; continue; }
    if (c === '"' || c === "'") quote = c;
    else if (c === "(" || c === "[") depth++;
    else if (c === ")" || c === "]") depth--;
    else if (c === sep && depth === 0) { out.push(text.slice(last, i)); last = i + 1; }
  }
  out.push(text.slice(last));
  return out.map((s) => s.trim()).filter(Boolean);
}

const COLOR_TAIL = /(var\([^)]*\)|color-mix\(.*\)|#[0-9a-fA-F]{3,8}|[a-zA-Z]+)\s*$/;

// Longhands the audit needs: color, background-color, border-color, outline-color.
function expand(prop, value) {
  const tail = () => { const t = (COLOR_TAIL.exec(value) ?? [])[1]; return t === "none" ? "transparent" : t; };
  if (prop === "color") return { color: value };
  if (prop === "background" || prop === "background-color") return value.includes("gradient") ? {} : { "background-color": value };
  if (prop === "border-color") return { "border-color": value };
  if (prop === "border" || prop === "border-top" || prop === "border-bottom") return prop === "border" ? { "border-color": tail() } : {};
  if (prop === "outline") return { "outline-color": tail() };
  if (prop === "outline-color") return { "outline-color": value };
  return {};
}

export function parseRules(text, out = [], media = null) {
  const css = clean(text);
  let i = 0;
  while (i < css.length) {
    const open = css.indexOf("{", i);
    if (open < 0) break;
    const close = matchBrace(css, open);
    const head = css.slice(i, open).trim();
    const body = css.slice(open + 1, close);
    if (head.startsWith("@media")) parseRules(body, out, head);
    else if (head.startsWith("@")) { /* keyframes and the like */ }
    else {
      const decls = {};
      for (const d of splitTop(body, ";")) {
        const k = d.indexOf(":");
        if (k > 0) Object.assign(decls, expand(d.slice(0, k).trim(), d.slice(k + 1).trim().replace(/\s*!important$/, "")));
      }
      for (const sel of splitTop(head, ",")) out.push({ selector: sel, decls, media, order: out.length });
    }
    i = close + 1;
  }
  return out;
}

// ---- Selectors ------------------------------------------------------------------------------------

function parseCompound(src) {
  const c = { tag: null, classes: [], attrs: [], pseudos: [], pseudoElement: null };
  let i = 0;
  const ident = () => /^[\w-]+/.exec(src.slice(i))[0];
  while (i < src.length) {
    const ch = src[i];
    if (ch === ".") { i++; const n = ident(); c.classes.push(n); i += n.length; }
    else if (ch === "[") {
      const end = src.indexOf("]", i);
      const m = /^\[\s*([\w-]+)\s*(?:=\s*"?([^"\]]*)"?)?\s*\]$/.exec(src.slice(i, end + 1));
      c.attrs.push({ name: m[1], value: m[2] ?? null });
      i = end + 1;
    } else if (ch === ":") {
      if (src[i + 1] === ":") { i += 2; c.pseudoElement = ident(); i += c.pseudoElement.length; continue; }
      i++;
      const name = ident();
      i += name.length;
      let args = null;
      if (src[i] === "(") {
        let depth = 0, j = i;
        for (; j < src.length; j++) { if (src[j] === "(") depth++; else if (src[j] === ")" && --depth === 0) break; }
        args = splitTop(src.slice(i + 1, j), ",").map(parseComplex);
        i = j + 1;
      }
      c.pseudos.push({ name, args });
    } else if (/[\w*]/.test(ch)) { const n = /^[\w*-]+/.exec(src.slice(i))[0]; c.tag = n; i += n.length; }
    else throw new Error(`unsupported selector syntax: ${src}`);
  }
  return c;
}

// Complex selector -> [{compound, combinator}] where combinator links to the previous compound (" " or ">").
export function parseComplex(src) {
  const parts = [];
  let buf = "", depth = 0, combinator = null;
  const flush = () => { if (buf) { parts.push({ compound: parseCompound(buf), combinator }); buf = ""; combinator = null; } };
  for (const ch of src.trim()) {
    if (ch === "(" || ch === "[") depth++;
    if (ch === ")" || ch === "]") depth--;
    if (depth === 0 && (ch === " " || ch === ">")) {
      flush();
      if (ch === ">") combinator = ">";
      else if (combinator === null) combinator = " ";
    } else buf += ch;
  }
  flush();
  return parts;
}

const add = (a, b) => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
const maxSpec = (list) => list.reduce((m, s) => (cmpSpec(s, m) > 0 ? s : m), [0, 0, 0]);
export const cmpSpec = (a, b) => a[0] - b[0] || a[1] - b[1] || a[2] - b[2];

export function specificity(parts) {
  let s = [0, 0, 0];
  for (const { compound: c } of parts) {
    if (c.tag && c.tag !== "*") s = add(s, [0, 0, 1]);
    s = add(s, [0, c.classes.length + c.attrs.length, 0]);
    if (c.pseudoElement) s = add(s, [0, 0, 1]);
    for (const p of c.pseudos) {
      if (p.name === "where") continue;
      if (p.name === "is" || p.name === "not") s = add(s, maxSpec(p.args.map(specificity)));
      else s = add(s, [0, 1, 0]);
    }
  }
  return s;
}

// An element: { tag, classes: [], attrs: {name: value}, states: [] } ("hover", "focus", "focus-visible", "disabled", "checked").
function matchCompound(c, el) {
  if (c.pseudoElement !== (el.pseudo ?? null)) return false; // ::before and ::after are elements of their own
  if (c.tag && c.tag !== "*" && c.tag !== el.tag) return false;
  if (!c.classes.every((k) => el.classes.includes(k))) return false;
  if (!c.attrs.every((a) => (a.value === null ? a.name in el.attrs : el.attrs[a.name] === a.value))) return false;
  return c.pseudos.every((p) => {
    if (p.name === "is" || p.name === "where") return p.args.some((sel) => matchComplex(sel, [el]));
    if (p.name === "not") return !p.args.some((sel) => matchComplex(sel, [el]));
    if (p.name === "disabled") return el.states.includes("disabled") || "disabled" in el.attrs;
    if (p.name === "focus-visible" || p.name === "focus") return el.states.includes(p.name) || (p.name === "focus" && el.states.includes("focus-visible"));
    return el.states.includes(p.name);
  });
}

// chain = [root, ..., element]; the selector's last compound must match the last element.
export function matchComplex(parts, chain, end = chain.length - 1, idx = parts.length - 1) {
  if (idx < 0) return true;
  if (end < 0 || !matchCompound(parts[idx].compound, chain[end])) return false;
  if (idx === 0) return true;
  const comb = parts[idx].combinator;
  if (comb === ">") return matchComplex(parts, chain, end - 1, idx - 1);
  for (let k = end - 1; k >= 0; k--) if (matchComplex(parts, chain, k, idx - 1)) return true;
  return false;
}

/** Rules of the base cascade; with `forced` also the @media (forced-colors: active) rules, in source order. */
export function compile(rules, { forced = false } = {}) {
  return rules.filter((r) => r.media === null || (forced && /forced-colors:\s*active/.test(r.media))).map((r) => {
    const parts = parseComplex(r.selector);
    return { ...r, parts, spec: specificity(parts) };
  });
}

/** Winning declaration of `prop` for the last element of `chain` (no inheritance), or undefined. */
export function winning(compiled, chain, prop) {
  let best = null;
  for (const r of compiled) {
    if (!(prop in r.decls) || !matchComplex(r.parts, chain)) continue;
    if (!best || cmpSpec(r.spec, best.spec) > 0 || (cmpSpec(r.spec, best.spec) === 0 && r.order > best.order)) best = r;
  }
  return best?.decls[prop];
}

// ---- Values ---------------------------------------------------------------------------------------

const hexToRgb = (h) => [0, 1, 2].map((i) => parseInt(h.slice(1 + 2 * i, 3 + 2 * i), 16));
const rgbToHex = (v) => "#" + v.map((x) => Math.round(x).toString(16).padStart(2, "0")).join("");

/** Resolves a color value to "#rrggbb"; "transparent" -> null; throws on anything else it cannot compute. */
export function resolveColor(value, theme, current = null, depth = 0) {
  const v = value.trim();
  if (/^[A-Z][A-Za-z]+$/.test(v) && v in theme) return theme[v].toLowerCase(); // system color of a forced palette
  if (depth > 8) throw new Error(`color recursion: ${value}`);
  if (/^#[0-9a-f]{6}$/i.test(v)) return v.toLowerCase();
  if (v === "transparent") return null;
  if (v === "currentColor") return current;
  let m = /^var\(\s*(--[\w-]+)\s*\)$/.exec(v);
  if (m) {
    if (!(m[1] in theme)) throw new Error(`undefined token ${m[1]}`);
    return resolveColor(theme[m[1]], theme, current, depth + 1);
  }
  m = /^color-mix\(\s*in srgb\s*,\s*(.+)\)$/.exec(v);
  if (m) {
    const [a, b] = splitTop(m[1], ",").map((s) => {
      const pm = /^(.*?)\s+(\d+(?:\.\d+)?)%$/.exec(s);
      return pm ? { color: resolveColor(pm[1], theme, current, depth + 1), pct: Number(pm[2]) / 100 } : { color: resolveColor(s, theme, current, depth + 1), pct: null };
    });
    let pa = a.pct, pb = b.pct;
    if (pa === null && pb === null) pa = pb = 0.5;
    else if (pa === null) pa = 1 - pb;
    else if (pb === null) pb = 1 - pa;
    if (a.color === null || b.color === null) return null; // mixing with transparent: not a solid color
    const ra = hexToRgb(a.color), rb = hexToRgb(b.color);
    return rgbToHex(ra.map((x, i) => (x * pa + rb[i] * pb) / (pa + pb)));
  }
  throw new Error(`unsupported color value: ${value}`);
}

const lum = (hex) => {
  const [r, g, b] = hexToRgb(hex).map((x) => { const c = x / 255; return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4; });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
};
export function contrast(a, b) {
  const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

/**
 * Effective painted values of the last element of `chain`: text color (inherited down the chain), the
 * background (nearest non-transparent ancestor, else `surface`), border and outline colors (null = none/transparent).
 */
export function computed(compiled, chain, theme, surface, forced = false) {
  // Forced colors: the UA replaces author colors that are not system colors; approximate the replacement.
  const FALLBACK = { color: "CanvasText", "background-color": "Canvas", "border-color": "ButtonText", "outline-color": "ButtonText" };
  const value = (prop, v) => {
    if (forced && v !== "transparent" && !(/^[A-Z][A-Za-z]+$/.test(v.trim()) && v.trim() in theme)) return FALLBACK[prop];
    return v;
  };
  let color = null;
  for (let k = 0; k < chain.length; k++) {
    const own = winning(compiled, chain.slice(0, k + 1), "color");
    if (own !== undefined) color = resolveColor(value("color", own), theme, color);
  }
  let background = null;
  for (let k = chain.length - 1; k >= 0 && background === null; k--) {
    const own = winning(compiled, chain.slice(0, k + 1), "background-color");
    if (own !== undefined) background = resolveColor(value("background-color", own), theme, color);
  }
  const pick = (prop) => {
    const v = winning(compiled, chain, prop);
    return v === undefined ? null : resolveColor(value(prop, v), theme, color);
  };
  return { color, background: background ?? surface, ownBackground: background !== null, border: pick("border-color"), outline: pick("outline-color") };
}
