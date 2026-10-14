// "Compare two records" panel: both records side by side with a line-level diff of their text
// (computed by the server). Texts come from the ERS (untrusted): textContent only.
import { api } from "./api.js";
import { buildDiffRows, buildSideHeader, createCompareFlow, statsText } from "./compare-model.js";
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

// host: empty element to fill (hidden until opened). Options: getCase().
// Returns { open(a, b), close(), reset() }.
export function initCompare(host, { getCase }) {
  host.textContent = "";
  const head = element("div", "cmp-head");
  head.appendChild(element("h3", "cmp-title", S.cmpTitle));
  const closeButton = element("button", "cmp-close", S.cmpClose);
  closeButton.type = "button";
  head.appendChild(closeButton);
  const status = element("div", "bm-muted");
  status.setAttribute("role", "status");
  const heads = element("div", "cmp-sides");
  const tools = element("div", "cmp-tools");
  const hideLabel = element("label", "cmp-hide");
  const hideBox = element("input");
  hideBox.type = "checkbox";
  hideBox.checked = true;
  hideLabel.append(hideBox, document.createTextNode(" "), element("span", "", S.cmpHideEqual));
  const stats = element("span", "bm-muted");
  tools.append(hideLabel, stats);
  const wrap = element("div", "cmp-wrap");
  const table = element("table", "cmp-table");
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
  const note = element("div", "bm-muted");
  host.append(head, status, heads, tools, wrap, note);

  function sideBox(label, record) {
    const header = buildSideHeader(record);
    const box = element("section", "cmp-side");
    box.setAttribute("aria-label", label + ": " + header.title);
    box.appendChild(element("h4", "cmp-side-title", label + ": " + header.title));
    const dl = element("dl", "cmp-meta");
    for (const item of header.items) {
      dl.appendChild(element("dt", "", item.label));
      dl.appendChild(element("dd", "bm-mono", item.text));
    }
    box.appendChild(dl);
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
    status.classList.remove("bm-error");
    if (state.status === "idle") {
      host.hidden = true;
      return;
    }
    host.hidden = false;
    wrap.hidden = state.status !== "ready";
    tools.hidden = state.status !== "ready";
    if (state.status === "nocase") {
      status.textContent = S.cmpNoCase;
    } else if (state.status === "loading") {
      status.textContent = S.cmpLoading;
    } else if (state.status === "error") {
      status.classList.add("bm-error");
      status.textContent = S.cmpError + (state.error ? ": " + state.error : "");
    } else {
      const data = state.data;
      lastData = data;
      status.textContent = data.diff.identical ? S.cmpIdentical : "";
      heads.append(sideBox(S.cmpSideA, data.a), sideBox(S.cmpSideB, data.b));
      stats.textContent = statsText(data.diff.stats);
      renderRows(data);
      if (data.diff.truncated) {
        note.textContent = format(S[pluralKey("cmpTruncated", data.diff.max_lines)], { count: data.diff.max_lines, chars: 4000 });
      }
    }
  }

  const flow = createCompareFlow({ api, getCase, onState: render });
  closeButton.addEventListener("click", () => flow.close());
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
