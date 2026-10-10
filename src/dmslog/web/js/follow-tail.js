// "Follow tail": a toggle in the toolbar plus a "N new records" button. While on, the server's `watch`
// events (dmslog serve --watch) reload the end of the grid when it is at the bottom; scrolled up, the
// view stays put and the button counts what arrived. Derived panels reload with a debounce.
import { api } from "./api.js";
import {
  DEBOUNCE_MS, DERIVED_DEBOUNCE_MS, DERIVED_MAX_WAIT_MS, MAX_WAIT_MS, createDebounce, createFollowFlow,
  parseWatchEvent,
} from "./follow-tail-model.js";
import { S, format } from "./strings.js";

export function initFollowTail({ toggle, newButton, grid, getCase, getFilter, onChanged, onError }) {
  let events = null;
  const refreshDebounce = createDebounce({
    wait: DEBOUNCE_MS,
    maxWait: MAX_WAIT_MS,
    run: () => flow.refresh().catch(onError),
  });
  const derivedDebounce = createDebounce({
    wait: DERIVED_DEBOUNCE_MS,
    maxWait: DERIVED_MAX_WAIT_MS,
    run: () => {
      if (flow.state.on) {
        onChanged();
      }
    },
  });

  const flow = createFollowFlow({
    api,
    getCase,
    getFilter,
    view: {
      tailInfo: () => grid.tailInfo(),
      followTail: () => grid.followTail(),
      total: () => grid.total,
    },
    onState: (state) => {
      newButton.hidden = !(state.on && state.pending > 0);
      newButton.textContent = format(S.followNew, { count: Number(state.pending).toLocaleString("en-US") });
      toggle.checked = state.on;
    },
  });

  function close() {
    if (events) {
      events.close();
      events = null;
    }
    refreshDebounce.cancel();
    derivedDebounce.cancel();
  }

  function schedule() {
    refreshDebounce.trigger();
    derivedDebounce.trigger();
  }

  function connect() {
    close();
    const caseId = getCase();
    if (!flow.state.on || caseId === null || typeof EventSource !== "function") {
      return;
    }
    events = new EventSource(api.progressUrl(caseId));
    events.addEventListener("watch", (event) => {
      if (parseWatchEvent(event.data) !== null) {
        schedule();
      }
    });
    // The browser reconnects by itself after a dropped stream.
  }

  toggle.addEventListener("change", () => {
    flow.setOn(toggle.checked);
    if (flow.state.on) {
      connect();
      flow.refresh().catch(onError);
    } else {
      close();
    }
  });

  newButton.addEventListener("click", () => {
    flow.reset();
    grid.followTail().catch(onError);
  });

  return {
    // The case changed: drop the old stream and counter.
    onCase() {
      flow.reset();
      connect();
    },
    // The grid moved or reloaded.
    viewChanged: () => flow.viewChanged(),
    flow,
  };
}
