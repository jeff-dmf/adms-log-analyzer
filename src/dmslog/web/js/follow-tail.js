// "Follow tail": a toggle in the toolbar plus a "N new records" button. While on, the server's `watch`
// events (dmslog serve --watch) reload the end of the grid when it is at the bottom; scrolled up, the
// view stays put and the button counts what arrived. Derived panels reload with a debounce. While the tab is
// hidden nothing is counted or reloaded (the stream stays open); one update runs when the tab is back.
import { api } from "./api.js";
import {
  DEBOUNCE_MS, DERIVED_DEBOUNCE_MS, DERIVED_MAX_WAIT_MS, MAX_WAIT_MS, createDebounce, createFollowFlow,
  createStreamGuard, createWatchGate, parseWatchEvent, watchInfo,
} from "./follow-tail-model.js";
import { S, format, pluralKey } from "./strings.js";

// onChanged({sources}): the derived panels (and the timeline) reload; `sources` is true only when a source appeared,
// was rewritten, or the tab came back. onStreamStopped(): the stream failed 3 times in a row and was closed (the
// server is down); resume() starts it again. `doc`: the document whose visibility pauses the updates.
export function initFollowTail({
  toggle, newButton, grid, getCase, getFilter, onChanged, onError, onStreamStopped,
  doc = typeof document === "undefined" ? null : document,
  timers, // injectable for tests ({now, setTimeout, clearTimeout}); the real clock otherwise
}) {
  let events = null;
  let destroyed = false;
  const guard = createStreamGuard();
  const gate = createWatchGate(Boolean(doc && doc.hidden));
  const refreshDebounce = createDebounce({
    timers,
    wait: DEBOUNCE_MS,
    maxWait: MAX_WAIT_MS,
    run: () => flow.refresh().catch(onError),
  });
  const derivedDebounce = createDebounce({
    timers,
    wait: DERIVED_DEBOUNCE_MS,
    maxWait: DERIVED_MAX_WAIT_MS,
    run: () => {
      const sources = gate.takeSources();
      if (flow.state.on) {
        onChanged({ sources });
      }
    },
  });

  const flow = createFollowFlow({
    api,
    getCase,
    getFilter,
    view: {
      tailInfo: () => grid.tailInfo(),
      followTail: (isCurrent) => grid.followTail(isCurrent),
      total: () => grid.total,
      showTotal: typeof grid.showTotal === "function" ? (count, exact) => grid.showTotal(count, exact) : undefined,
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
    if (destroyed) {
      return;
    }
    guard.reset(); // a new stream, a new case or no case: a stop does not outlive them
    const caseId = getCase();
    if (!flow.state.on || caseId === null || typeof EventSource !== "function") {
      return;
    }
    const stream = new EventSource(api.progressUrl(caseId));
    events = stream;
    stream.addEventListener("watch", (event) => {
      const data = events === stream ? parseWatchEvent(event.data) : null;
      if (data !== null && gate.note(watchInfo(data))) {
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
      if (events === stream && (stream.readyState === 2 ? guard.stop() : guard.failed())) {
        close();
        if (typeof onStreamStopped === "function") {
          onStreamStopped();
        }
      }
    });
  }

  function onToggle() {
    if (destroyed) {
      return;
    }
    flow.setOn(toggle.checked);
    if (flow.state.on) {
      connect();
      flow.engage().catch(onError); // to the end of the data, wherever the view was
    } else {
      close();
    }
  }
  toggle.addEventListener("change", onToggle);

  // Hidden: cancel what is scheduled or counting (a late answer is dropped by the flow's token) and remember it.
  // Visible again: ONE update, only when something was missed.
  function onVisibility() {
    if (destroyed) {
      return;
    }
    if (doc.hidden) {
      gate.hide(refreshDebounce.pending() || derivedDebounce.pending() || flow.busy());
      refreshDebounce.cancel();
      derivedDebounce.cancel();
      flow.cancel();
      return;
    }
    const missed = gate.show();
    if (missed !== null && flow.state.on && getCase() !== null) {
      flow.refresh().catch(onError);
      onChanged({ sources: missed.sources });
    }
  }
  if (doc) {
    doc.addEventListener("visibilitychange", onVisibility);
  }

  function onNewClick() {
    if (destroyed) {
      return;
    }
    flow.reset();
    grid.followTail().catch(onError);
  }
  newButton.addEventListener("click", onNewClick);

  return {
    // The case changed: drop the old stream and counter.
    onCase() {
      if (destroyed) {
        return;
      }
      flow.reset();
      connect();
    },
    // Starts the stream again after it stopped (Retry). True when it did something, false when there was nothing to resume.
    resume() {
      if (destroyed || !guard.stopped || !flow.state.on) {
        return false;
      }
      connect();
      return events !== null; // true only when a stream was really opened
    },
    stopped: () => guard.stopped,
    // The grid moved or reloaded.
    viewChanged: () => (destroyed ? undefined : flow.viewChanged()),
    // Drops the stream, the timers and every listener; the instance does nothing afterwards.
    destroy() {
      destroyed = true;
      close();
      flow.cancel();
      toggle.removeEventListener("change", onToggle);
      newButton.removeEventListener("click", onNewClick);
      if (doc) {
        doc.removeEventListener("visibilitychange", onVisibility);
      }
    },
    flow,
  };
}
