// "Side by side" view: the normal record on the left and its sensitive counterpart on the right,
// with aligned headers (source, time, TID, template), the full text and a copy button per pane.
// Texts come from the ERS (untrusted): textContent only.
import { api } from "./api.js";
import { SIDE_BY_SIDE_LINES, buildSideView, createSideBySideFlow } from "./links-model.js";
import { S, format } from "./strings.js";

const STYLESHEET = "/css/links.css";

function ensureStylesheet() {
  if (!document.querySelector('link[rel="stylesheet"][href="' + STYLESHEET + '"]')) {
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = STYLESHEET;
    document.head.appendChild(link);
  }
}

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
// Returns { open(entryId), close(), reset() }.
export function initSideBySide(host, { getCase }) {
  ensureStylesheet();
  host.textContent = "";
  const head = element("div", "sbs-head");
  head.appendChild(element("h3", "sbs-title", S.sbsTitle));
  const kind = element("span", "lnk-muted");
  const closeButton = element("button", "sbs-close", S.sbsClose);
  closeButton.type = "button";
  head.append(kind, closeButton);
  const status = element("div", "lnk-muted");
  status.setAttribute("role", "status");
  const panes = element("div", "sbs-panes");
  host.append(head, status, panes);
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
      box.appendChild(element("div", "lnk-muted", format(S.sbsTruncated, { count: SIDE_BY_SIDE_LINES })));
    }
    return box;
  }

  function render(state) {
    panes.textContent = "";
    texts.length = 0;
    kind.textContent = "";
    if (state.status === "idle") {
      host.hidden = true;
      return;
    }
    host.hidden = false;
    status.classList.remove("lnk-error");
    if (state.status === "nocase") {
      status.textContent = S.sbsNoCase;
    } else if (state.status === "loading") {
      status.textContent = S.sbsLoading;
    } else if (state.status === "none") {
      status.textContent = S.sbsNone;
    } else if (state.status === "error") {
      status.classList.add("lnk-error");
      status.textContent = S.sbsError + (state.error ? ": " + state.error : "");
    } else {
      const view = buildSideView(state.data);
      status.textContent = "";
      if (view === null) {
        status.textContent = S.sbsNone;
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
  closeButton.addEventListener("click", () => flow.close());
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
