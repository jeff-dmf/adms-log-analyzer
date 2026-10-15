// Clock-jump warning above the timeline: shown when the case has a clock_jump event (or a gap
// flagged as a possible clock change). Texts come from strings.js and the formatted time only.
import { bannerText, createClockJumpFlow } from "./events-model.js";
import { S } from "./strings.js";

// container: the clock segment of the status bar (status.slot("clock"), hidden until there is a warning). Options:
//   api               needs events(caseId, params, signal)
//   getCase()         current case id or null
//   onShowInGrid(t)   t = { from, to, entryId }
// Returns { refresh() }; call it when the case changed or indexing finished.
export function initClockBanner(container, { api, getCase, onShowInGrid }) {
  container.textContent = "";
  const text = document.createElement("span");
  const show = document.createElement("button");
  show.type = "button";
  show.textContent = S.clkShow;
  container.append(text, " ", show);

  function onState(state) {
    const summary = state.summary;
    container.hidden = summary === null;
    text.textContent = summary === null ? "" : bannerText(summary);
    text.title = text.textContent; // the status bar cuts a long text with an ellipsis
  }

  const flow = createClockJumpFlow({ api, getCase, onState });
  show.addEventListener("click", () => {
    const summary = flow.state.summary;
    if (summary && summary.target && flow.state.caseId !== null && flow.state.caseId === getCase()) {
      onShowInGrid(summary.target);
    }
  });
  return { refresh: () => flow.refresh() };
}
