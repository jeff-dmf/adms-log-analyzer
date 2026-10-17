// "Repeated record" line of the record detail (ROADMAP 9.12): "Repeat of #12, +340 ms", how many earlier occurrences
// the window holds, and a button that goes to the record repeated. Hidden when the record repeats nothing, when marks
// are off or while the answer is not in. Text only. The answers carry the ticket of the record that asked: a record
// shown later, a case change or a window change drops the ones in flight, so a late answer never draws over a newer one.
import { earlierLabel, spanText } from "./repeat-model.js";
import { S, format } from "./strings.js";

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

const isAbort = (err) => Boolean(err && err.name === "AbortError");

// host: the section element (hidden while empty). Options:
//   repeats        the controller of repeat-model.js (lookup, window)
//   api            needs entry(caseId, id, signal): the ts of the record repeated, to reach it in the grid
//   getCase()      current case id or null
//   onReveal(row)  {id, ts}: select that record in the grid
//   onError(err)   a failure of the "go to" (the lookup itself stays silent: it is a hint)
export function initRepeatDetail(host, { repeats, api, getCase, onReveal, onError = () => {} }) {
  let ticket = 0;
  let controller = null;
  let revealTicket = 0;
  let revealController = null;
  let current = null; // { id, caseId } of the record shown

  function cancel() {
    pending = false;
    ticket += 1;
    if (controller) {
      controller.abort();
      controller = null;
    }
    revealTicket += 1;
    if (revealController) {
      revealController.abort();
      revealController = null;
    }
  }

  function clear() {
    host.hidden = true;
    host.textContent = "";
    delete host.dataset.signature;
  }

  const signatureOf = (mark) => [mark.prev, mark.dtMs, mark.earlier, repeats.window()].join("|");

  function draw(mark) {
    host.textContent = "";
    const title = element("h3", "rp-title", S.repeatTitle);
    const line = element("div", "rp-line");
    line.appendChild(element("span", "rp-label", mark.label));
    const more = [];
    const earlier = earlierLabel(mark);
    if (earlier !== "") {
      more.push(earlier);
    }
    const windowLabel = spanText(repeats.window());
    if (windowLabel !== "") {
      more.push(format(S.repeatWindow, { window: windowLabel }));
    }
    const go = element("button", "ui-button is-ghost rp-go", format(S.repeatGoTo, { prev: mark.prev }));
    go.type = "button";
    go.dataset.prev = String(mark.prev);
    line.appendChild(go);
    host.append(title, line);
    if (more.length > 0) {
      host.appendChild(element("div", "rp-more", more.join(" · ")));
    }
    host.dataset.signature = signatureOf(mark);
    host.hidden = false;
  }

  let pending = false; // a lookup is in flight for the record shown

  // Looks the mark of `entry` up and draws it. keep = the same record again (marks changed): the line stays as it is
  // until the answer differs, so a focused button is not rebuilt for nothing.
  async function load(entry, keep) {
    cancel();
    if (!keep) {
      clear();
    }
    if (!entry || !Number.isSafeInteger(entry.id)) {
      current = null;
      clear();
      return;
    }
    const caseId = getCase();
    if (caseId === null || caseId === undefined) {
      current = null;
      clear();
      return;
    }
    current = { id: entry.id, caseId };
    const mine = ticket;
    controller = new AbortController();
    pending = true;
    try {
      const mark = await repeats.lookup(entry.id, controller.signal);
      if (mine !== ticket || getCase() !== caseId) {
        return;
      }
      if (!mark) {
        clear();
      } else if (host.dataset.signature !== signatureOf(mark)) {
        draw(mark);
      }
    } catch (err) {
      if (mine === ticket && !isAbort(err)) {
        clear(); // a hint: nothing to say when it cannot be had
      }
    } finally {
      if (mine === ticket) {
        pending = false;
      }
    }
  }

  const show = (entry) => load(entry, false);

  host.addEventListener("click", async (event) => {
    const button = event.target instanceof Element ? event.target.closest(".rp-go") : null;
    if (!button || !host.contains(button) || current === null || button.getAttribute("aria-disabled") === "true") {
      return;
    }
    const prev = Number(button.dataset.prev);
    const caseId = current.caseId;
    if (!Number.isSafeInteger(prev) || getCase() !== caseId) {
      return;
    }
    revealTicket += 1;
    const mine = revealTicket;
    if (revealController) {
      revealController.abort();
    }
    revealController = new AbortController();
    button.setAttribute("aria-disabled", "true"); // aria-disabled, not disabled: the focus stays on the button
    try {
      const entry = await api.entry(caseId, prev, revealController.signal);
      if (mine === revealTicket && getCase() === caseId && entry && String(entry.id) === String(prev)) {
        await onReveal({ id: prev, ts: entry.ts });
      }
    } catch (err) {
      if (mine === revealTicket && !isAbort(err)) {
        onError(err);
      }
    } finally {
      if (mine === revealTicket) {
        button.removeAttribute("aria-disabled");
      }
    }
  });

  clear();
  return {
    show,
    // The marks changed (the grid looked up more rows, the window changed): draw the record shown again.
    refresh() {
      return current === null || pending ? Promise.resolve() : load({ id: current.id }, true);
    },
    close() {
      cancel();
      current = null;
      clear();
    },
  };
}
