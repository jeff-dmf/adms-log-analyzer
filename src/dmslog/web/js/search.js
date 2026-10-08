// Search box helpers: term extraction and highlighting built from Text nodes + <mark> (no HTML parsing).

// Terms of a search text, mirroring the server syntax: whitespace-separated words, "phrases",
// trailing * on a bare word marks a prefix (the * is dropped for highlighting).
export function searchTerms(query) {
  const terms = [];
  const pattern = /"([^"]*)"|(\S+)/g;
  let m;
  while ((m = pattern.exec(String(query))) !== null) {
    let term = m[1] !== undefined ? m[1] : m[2];
    if (m[1] === undefined && term.endsWith("*")) {
      term = term.slice(0, -1);
    }
    term = term.trim();
    if (term !== "") {
      terms.push(term);
    }
  }
  return terms;
}

// Splits text into [{text, mark}] segments; case-insensitive literal matching (no regex, so
// terms with special characters are safe). Segments always concatenate back to the input.
export function highlightSegments(text, terms) {
  const source = String(text);
  const wanted = terms.filter((t) => t.length > 0).map((t) => t.toLowerCase());
  if (wanted.length === 0 || source === "") {
    return [{ text: source, mark: false }];
  }
  const lower = source.toLowerCase();
  if (lower.length !== source.length) {
    // Case folding changed the length (rare Unicode): skip highlighting rather than misalign.
    return [{ text: source, mark: false }];
  }
  const marked = new Array(source.length).fill(false);
  for (const term of wanted) {
    let from = 0;
    for (;;) {
      const at = lower.indexOf(term, from);
      if (at < 0) {
        break;
      }
      for (let i = at; i < at + term.length; i += 1) {
        marked[i] = true;
      }
      from = at + term.length;
    }
  }
  const segments = [];
  let start = 0;
  for (let i = 1; i <= source.length; i += 1) {
    if (i === source.length || marked[i] !== marked[start]) {
      segments.push({ text: source.slice(start, i), mark: marked[start] });
      start = i;
    }
  }
  return segments;
}

// Replaces the content of `element` with the text, highlighted via DOM nodes only.
export function renderHighlighted(element, text, terms) {
  element.textContent = "";
  for (const segment of highlightSegments(text, terms)) {
    if (segment.mark) {
      const mark = document.createElement("mark");
      mark.textContent = segment.text;
      element.appendChild(mark);
    } else {
      element.appendChild(document.createTextNode(segment.text));
    }
  }
}
