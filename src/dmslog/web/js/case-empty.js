// The empty state without an open case (UI-22, REC-22): in the middle of the window, "Open an ERS to start" with the
// two primary actions (New case…, Open a path…) and the recent cases. Case names are untrusted: textContent only.
// Loaded on demand by sources.js. The layout around it (side column and detail at 0 px, filters and timeline hidden) is
// css/case-dialogs.css, driven by the `no-case` class that sources.js puts on the body.
import { S, format, pluralKey } from "./strings.js";

function node(doc, tag, className, text) {
  const item = doc.createElement(tag);
  if (className) {
    item.className = className;
  }
  if (text !== undefined) {
    item.textContent = text;
  }
  return item;
}

function count(value) {
  return typeof value === "number" && Number.isFinite(value) && value > 0 ? Math.floor(value) : 0;
}

// "3 files · 12,400 records" for a recent case; a missing or non-numeric measure is 0, never "NaN".
export function recentMeta(item) {
  const sources = count(item && item.sources);
  const records = count(item && item.records);
  return format(S[pluralKey("ceRecentSources", sources)], { n: sources.toLocaleString("en-US") }) + " · " +
    format(S[pluralKey("ceRecentRecords", records)], { n: records.toLocaleString("en-US") });
}

// host: the element the section is added to (the grid panel). Handlers: onNewCase(opener), onOpenPath(opener),
// onSelectCase(id). Returns { root, setCases(list), show(), hide(), isShown(), contains(node), focusPrimary() }.
export function mountCaseEmpty({ host, onNewCase, onOpenPath, onSelectCase }) {
  const doc = host.ownerDocument || document;
  const root = node(doc, "section", "case-empty");
  root.setAttribute("aria-labelledby", "case-empty-title");
  root.hidden = true;
  const title = node(doc, "h2", "case-empty-title", S.ceTitle);
  title.id = "case-empty-title";
  const state = node(doc, "p", "case-empty-state", S.ceNoCase);
  state.setAttribute("role", "status");
  const actions = node(doc, "div", "case-empty-actions");
  const newButton = node(doc, "button", "ui-button is-primary case-empty-new", S.ceNewCase);
  newButton.type = "button";
  newButton.addEventListener("click", () => onNewCase(newButton));
  const pathButton = node(doc, "button", "ui-button case-empty-path", S.ceOpenPath);
  pathButton.type = "button";
  pathButton.addEventListener("click", () => onOpenPath(pathButton));
  actions.append(newButton, pathButton);
  const hint = node(doc, "p", "case-empty-hint", S.ceHint);
  const recent = node(doc, "div", "case-empty-recent");
  recent.hidden = true;
  const recentTitle = node(doc, "h3", "case-empty-recent-title", S.ceRecent);
  const list = node(doc, "ul", "case-empty-list");
  recent.append(recentTitle, list);
  root.append(title, state, actions, hint, recent);
  host.appendChild(root);

  function setCases(cases) {
    list.textContent = "";
    const items = Array.isArray(cases) ? cases : [];
    for (const item of items) {
      if (!item || (typeof item.id !== "string" && typeof item.id !== "number")) {
        continue;
      }
      const row = node(doc, "li", "case-empty-item");
      const open = node(doc, "button", "ui-button is-ghost case-empty-open");
      open.type = "button";
      const name = node(doc, "span", "case-empty-name", String(item.name === undefined || item.name === null ? item.id : item.name));
      name.title = name.textContent;
      open.append(name, node(doc, "span", "case-empty-meta", recentMeta(item)));
      open.setAttribute("aria-label", format(S.ceOpenCase, { name: name.textContent }));
      open.addEventListener("click", () => onSelectCase(item.id));
      row.appendChild(open);
      list.appendChild(row);
    }
    recent.hidden = list.children.length === 0;
  }

  return {
    root,
    setCases,
    show() {
      root.hidden = false;
    },
    hide() {
      root.hidden = true;
    },
    isShown: () => !root.hidden,
    contains: (item) => Boolean(item) && root.contains(item),
    focusPrimary() {
      if (!root.hidden) {
        newButton.focus();
      }
    },
  };
}
