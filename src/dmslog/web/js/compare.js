// "Compare two records" panel: both records side by side with a line-level diff of their text
// (computed by the server). Texts come from the ERS (untrusted): textContent only.
import { api } from "./api.js";
import { buildDiffRows, buildSideHeader, createCompareFlow, statsText } from "./compare-model.js";
import { createPanelShell, setStatusLine, statusLine } from "./panel-shell.js";
import { S, format, pluralKey } from "./strings.js";

const MARKS = { change: "~", remove: "-", add: "+", equal: "", skip: "" };

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) {
    node.className = className;
  }
  if (text !== undefined) {
    node.textContent = text;
  }
  return node;
}

// host: empty element to fill. Options: getCase(), onRequestClose() (the dock closes the tab; the default hides the
// host). Returns { open(a, b), close(), reset() }. The panel never shows or hides its host: opening is the dock's.
export function initCompare(host, { getCase, onRequestClose = null }) {
  const shell = createPanelShell(host, {
    title: S.cmpTitle,
    onRequestClose: onRequestClose || (() => {
      flow.close();
      host.hidden = true;
    }),
  });
  const status = statusLine(document, "bm-muted");
  const heads = element("div", "cmp-sides");
  const hideLabel = element("label", "cmp-hide");
  const hideBox = element("input");
  hideBox.type = "checkbox";
  hideBox.checked = true;
  hideLabel.append(hideBox, document.createTextNode(" "), element("span", "", S.cmpHideEqual));
  const stats = element("span", "bm-muted");
  shell.addTools(hideLabel, stats);
  shell.tools.hidden = true; // only while a comparison is shown
  const wrap = element("div", "cmp-wrap");
  const table = element("table", "ui-table is-wrap cmp-table");
  table.appendChild(element("caption", "visually-hidden", S.cmpTableCaption));
  const headRow = element("tr");
  for (const label of ["", S.cmpSideA, "", S.cmpSideB]) {
    const th = element("th", "", label);
    th.scope = "col";
    headRow.appendChild(th);
  }
  table.appendChild(element("thead")).appendChild(headRow);
  const tbody = element("tbody");
  table.appendChild(tbody);
  wrap.appendChild(table);
  const note = element("div", "ui-status");
  // The difference comes first; the A/B identification is one compact line each, right above it.
  shell.body.append(status, heads, wrap, note);

  // One line per side: "A  time, source file, message type" (the tooltip adds the TID).
  function sideBox(label, record) {
    const header = buildSideHeader(record);
    const box = element("div", "cmp-side");
    box.title = header.items.map((item) => item.label + ": " + item.text).join("; ");
    box.setAttribute("aria-label", label + ": " + header.title);
    box.append(element("strong", "cmp-side-title", label), element("span", "cmp-side-text bm-mono", header.title));
    return box;
  }

  function renderRows(data) {
    tbody.textContent = "";
    const rows = buildDiffRows(data.diff.ops, hideBox.checked);
    for (const row of rows) {
      const tr = element("tr", "cmp-row cmp-" + row.kind);
      if (row.kind === "skip") {
        const td = element("td", "cmp-skip", format(S[pluralKey("cmpSkipped", row.count)], { count: row.count }));
        td.colSpan = 4;
        tr.appendChild(td);
      } else {
        const mark = MARKS[row.kind];
        const aLine = element("td", "cmp-line", row.aLine === null ? "" : String(row.aLine));
        const aText = element("td", "cmp-text", row.a === null ? "" : (mark && row.kind !== "add" ? mark + " " : "") + row.a);
        const bLine = element("td", "cmp-line", row.bLine === null ? "" : String(row.bLine));
        const bText = element("td", "cmp-text", row.b === null ? "" : (mark && row.kind !== "remove" ? mark + " " : "") + row.b);
        tr.append(aLine, aText, bLine, bText);
      }
      tbody.appendChild(tr);
    }
  }

  let lastData = null;

  function render(state) {
    heads.textContent = "";
    note.textContent = "";
    stats.textContent = "";
    tbody.textContent = "";
    lastData = null;
    setStatusLine(status, "", false);
    wrap.hidden = state.status !== "ready";
    shell.tools.hidden = state.status !== "ready";
    if (state.status === "idle") {
      return;
    }
    if (state.status === "nocase") {
      setStatusLine(status, S.cmpNoCase, false);
    } else if (state.status === "loading") {
      setStatusLine(status, S.cmpLoading, false);
    } else if (state.status === "error") {
      setStatusLine(status, S.cmpError + (state.error ? ": " + state.error : ""), true);
    } else {
      const data = state.data;
      lastData = data;
      setStatusLine(status, data.diff.identical ? S.cmpIdentical : "", false);
      heads.append(sideBox(S.cmpSideA, data.a), sideBox(S.cmpSideB, data.b));
      stats.textContent = statsText(data.diff.stats);
      renderRows(data);
      if (data.diff.truncated) {
        note.textContent = format(S[pluralKey("cmpTruncated", data.diff.max_lines)], { count: data.diff.max_lines, chars: 4000 });
      }
    }
  }

  const flow = createCompareFlow({ api, getCase, onState: render });
  hideBox.addEventListener("change", () => {
    if (lastData) {
      renderRows(lastData);
    }
  });

  return {
    open: (a, b) => flow.open(a, b),
    close: () => flow.close(),
    reset: () => flow.close(),
  };
}
