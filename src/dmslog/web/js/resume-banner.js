// "Resume indexing" offer (M9 9.9): a short notice in the side column, under the case row, when the open case has
// sources that an earlier partial opening left unfinished (GET .../pending). Resume now starts POST .../resume; the
// owner (sources.js) runs the job and reports back through setState(). Loaded on demand, only when there is pending
// work. The text comes from strings.js and counts only: no API text is shown here.
import { pendingTotals } from "./partial-open-model.js";
export { normalizePending } from "./partial-open-model.js"; // sources.js reads the pending list through this module
import { S, format, pluralKey } from "./strings.js";

let bannerUid = 0;

function hiddenChain(node) {
  for (let item = node; item; item = item.parentNode || item.parent) {
    if (item.hidden === true) {
      return true;
    }
  }
  return false;
}

// parent: the element that receives the banner; before: the node it goes before (null: at the end).
// onResume(): called by the button; it returns nothing, the owner calls setState("busy") itself.
export function mountResumeBanner({ parent, before = null, onResume }) {
  const doc = parent.ownerDocument || document;
  const root = doc.createElement("div");
  root.className = "resume-banner";
  root.hidden = true;
  const text = doc.createElement("span");
  text.className = "resume-banner-text";
  const button = doc.createElement("button");
  button.type = "button";
  button.className = "ui-button is-primary resume-banner-button";
  button.textContent = S.poResumeNow;
  button.title = S.poResumeTitle;
  text.id = "resume-banner-text-" + String(++bannerUid);
  button.setAttribute("aria-describedby", text.id); // the visible name stays "Resume now"; the notice describes it
  button.addEventListener("click", () => {
    if (!button.disabled && typeof onResume === "function") {
      onResume();
    }
  });
  root.append(text, button);
  parent.insertBefore(root, before);

  let state = "hidden";

  // The pending list (normalized, see partial-open-model.js) as the notice text.
  function describe(items) {
    const { sources } = pendingTotals(items);
    return sources > 0 ? format(S[pluralKey("poResume", sources)], { count: sources }) : S.poResumeInterrupted;
  }

  // The button gives up its focus when the banner hides or the button is taken away: the caller's `fallback` gets it.
  function leaveFocus(fallback) {
    if (doc.activeElement === button && typeof fallback === "function") {
      const target = fallback();
      if (target && typeof target.focus === "function" && !hiddenChain(target)) {
        target.focus();
      }
    }
  }

  return {
    node: root,
    button,
    // "ready" (offer, button on) | "busy" (resuming, button off) | "running" (a job runs elsewhere: text, no button)
    // | "hidden". `items` = pending list for the text. `fallback()` = where the focus goes if the button goes away.
    setState(next, items = [], fallback = null) {
      state = next;
      if (next === "hidden") {
        leaveFocus(fallback);
        root.hidden = true;
        text.textContent = "";
        button.disabled = false;
        return;
      }
      root.hidden = false;
      if (next === "running") {
        text.textContent = S.poResumeRunning;
        leaveFocus(fallback);
        button.hidden = true;
        return;
      }
      text.textContent = describe(items);
      button.hidden = false;
      button.disabled = next === "busy";
      button.textContent = next === "busy" ? S.poResuming : S.poResumeNow;
    },
    getState: () => state,
    isVisible: () => !root.hidden,
    dispose() {
      leaveFocus(null);
      root.remove();
    },
  };
}
