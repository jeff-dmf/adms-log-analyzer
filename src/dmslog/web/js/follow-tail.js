// "Follow tail": a toggle in the toolbar plus a "N new records" button. While on, the server's `watch`
// events (dmslog serve --watch) reload the end of the grid when it is at the bottom; scrolled up, the
// view stays put and the button counts what arrived. Derived panels reload with a debounce.
import { api } from "./api.js";
import {
  DEBOUNCE_MS, DERIVED_DEBOUNCE_MS, DERIVED_MAX_WAIT_MS, MAX_WAIT_MS, createDebounce, createFollowFlow,
  createStreamGuard, parseWatchEvent,
} from "./follow-tail-model.js";
import { S, format, pluralKey } from "./strings.js";

// onStreamStopped(): the stream failed 3 times in a row and was closed (the server is down); resume() starts it again.
export function initFollowTail({ toggle, newButton, grid, getCase, getFilter, onChanged, onError, onStreamStopped }) {
  let events = null;
  const guard = createStreamGuard();
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
      newButton.textContent = format(S[pluralKey("followNew", state.pending)], { count: Number(state.pending).toLocaleString("en-US") });
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
    guard.reset(); // a new stream, a new case or no case: a stop does not outlive them
    const caseId = getCase();
    if (!flow.state.on || caseId === null || typeof EventSource !== "function") {
      return;
    }
    const stream = new EventSource(api.progressUrl(caseId));
    events = stream;
    stream.addEventListener("watch", (event) => {
      if (events === stream && parseWatchEvent(event.data) !== null) {
        schedule();
      }
    });
    stream.addEventListener("open", () => {
      if (events === stream) {
        guard.opened();
      }
    });
    // The browser reconnects by itself after a dropped stream; after 3 failures in a row we stop it.
    stream.addEventListener("error", () => {
      if (events === stream && guard.failed()) {
        close();
        if (typeof onStreamStopped === "function") {
          onStreamStopped();
        }
      }
    });
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
    // Starts the stream again after it stopped (Retry). True when it did something, false when there was nothing to resume.
    resume() {
      if (!guard.stopped || !flow.state.on) {
        return false;
      }
      connect();
      return events !== null; // true only when a stream was really opened
    },
    stopped: () => guard.stopped,
    // The grid moved or reloaded.
    viewChanged: () => flow.viewChanged(),
    flow,
  };
}
