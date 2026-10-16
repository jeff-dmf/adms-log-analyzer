// "Side by side" view: the normal record on the left and its sensitive counterpart on the right,
// with aligned headers (source, time, TID, template), the full text and a copy button per pane.
// Texts come from the ERS (untrusted): textContent only.
import { api } from "./api.js";
import { SIDE_BY_SIDE_LINES, buildSideView, createSideBySideFlow } from "./links-model.js";
import { createPanelShell, setStatusLine, statusLine } from "./panel-shell.js";
import { S, format, pluralKey } from "./strings.js";

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
// host). Returns { open(entryId), close(), reset() }. The panel never shows or hides its host: opening is the dock's.
export function initSideBySide(host, { getCase, onRequestClose = null }) {
  const shell = createPanelShell(host, {
    title: S.sbsTitle,
    onRequestClose: onRequestClose || (() => {
      flow.close();
      host.hidden = true;
    }),
  });
  const kind = element("span", "lnk-muted");
  shell.summary.appendChild(kind);
  const status = statusLine(document, "lnk-muted");
  const panes = element("div", "sbs-panes");
  shell.body.append(status, panes);
  const texts = [];

  function paneNode(pane, index) {
    const box = element("section", "sbs-pane");
    box.setAttribute("aria-label", pane.title);
    const header = element("div", "sbs-pane-head");
    header.appendChild(element("h4", "sbs-pane-title", pane.title));
    const copy = element("button", "sbs-copy", S.sbsCopy);
    copy.type = "button";
    copy.dataset.pane = String(index);
    const copied = element("span", "lnk-muted");
    copied.setAttribute("role", "status");
    copied.dataset.copied = String(index);
    header.append(copy, copied);
    box.appendChild(header);
    const dl = element("dl", "sbs-meta");
    for (const item of pane.headers) {
      dl.appendChild(element("dt", "", item.label));
      dl.appendChild(element("dd", "lnk-mono", item.text));
    }
    box.appendChild(dl);
    box.appendChild(element("pre", "sbs-text", pane.text));
    if (pane.truncated) {
      box.appendChild(element("div", "lnk-muted", format(S[pluralKey("sbsTruncated", SIDE_BY_SIDE_LINES)], { count: SIDE_BY_SIDE_LINES })));
    }
    return box;
  }

  function render(state) {
    panes.textContent = "";
    texts.length = 0;
    kind.textContent = "";
    setStatusLine(status, "", false);
    if (state.status === "idle") {
      return;
    }
    if (state.status === "nocase") {
      setStatusLine(status, S.sbsNoCase, false);
    } else if (state.status === "loading") {
      setStatusLine(status, S.sbsLoading, false);
    } else if (state.status === "none") {
      setStatusLine(status, S.sbsNone, false);
    } else if (state.status === "error") {
      setStatusLine(status, S.sbsError + (state.error ? ": " + state.error : ""), true);
    } else {
      const view = buildSideView(state.data);
      if (view === null) {
        setStatusLine(status, S.sbsNone, false);
        return;
      }
      kind.textContent = view.kindText;
      [view.left, view.right].forEach((pane, index) => {
        texts.push(pane.text);
        panes.appendChild(paneNode(pane, index));
      });
    }
  }

  const flow = createSideBySideFlow({ api, getCase, onState: render });
  panes.addEventListener("click", async (event) => {
    const button = event.target instanceof Element ? event.target.closest("button[data-pane]") : null;
    if (!button || !navigator.clipboard) {
      return;
    }
    const index = Number(button.dataset.pane);
    const mark = panes.querySelector('[data-copied="' + index + '"]');
    try {
      await navigator.clipboard.writeText(texts[index] || "");
      if (mark) {
        mark.textContent = S.sbsCopied;
      }
    } catch (err) {
      if (mark) {
        mark.textContent = "";
      }
    }
  });

  return {
    open: (entryId) => flow.open(entryId),
    close: () => flow.close(),
    reset: () => flow.close(),
  };
}
