// Status bar model (UI-13): pure functions and the message state, testable without a DOM or a real clock.
import { errorKind, isRetryable } from "./api.js";
import { format, pluralKey } from "./strings.js";

export const MESSAGE_KINDS = ["info", "success", "warning", "error"];
export const DEFAULT_TIMEOUT_MS = 8000;

// "1.2M", "12K", "999": the short form of an estimated count.
export function compactNumber(n) {
  const value = Math.max(0, Math.round(Number(n) || 0));
  const trim = (x) => String(Math.round(x * 10) / 10);
  if (value >= 1_000_000_000) {
    return trim(value / 1_000_000_000) + "B";
  }
  if (value >= 1_000_000) {
    return trim(value / 1_000_000) + "M";
  }
  if (value >= 10_000) {
    return (value >= 100_000 ? String(Math.round(value / 1000)) : trim(value / 1000)) + "K";
  }
  return value.toLocaleString("en-US");
}

// value: { n, exact }. Exact: "285,093 records"; estimate: "about 1.2M records" (the caller adds .is-estimate).
export function formatCount(value, S, plural = pluralKey) {
  const n = Math.max(0, Math.round(Number(value.n) || 0));
  if (value.exact) {
    return format(S[plural("statusRecords", n)], { count: n.toLocaleString("en-US") });
  }
  return format(S[plural("statusRecordsApprox", n)], { count: compactNumber(n) });
}

// Success and information go away by themselves; an error stays until it is dismissed or replaced.
export function messageTimeout(kind) {
  return kind === "error" ? 0 : DEFAULT_TIMEOUT_MS;
}

// The texts of the connection errors (network, forbidden): they live in the bar's error mode, never as messages.
export function isConnectionText(text, S) {
  return text === S.errorNetwork || text === S.errorForbidden;
}

// Is the node really shown? `hidden` on it or on any ancestor, not connected, or the browser's own answer
// (checkVisibility also sees CSS display:none, which a hidden panel in the no-case state uses).
export function isShown(node) {
  if (!node || node.isConnected === false) {
    return false;
  }
  if (typeof node.checkVisibility === "function") {
    return node.checkVisibility();
  }
  for (let n = node; n; n = n.parentNode || n.parent) {
    if (n.hidden) {
      return false;
    }
  }
  return true;
}

// The first node of the list that is shown and can take the focus, or null.
export function firstShown(nodes) {
  return nodes.find((node) => node && !node.disabled && isShown(node)) || null;
}

// Where the focus goes when what had it disappears: the grid, then the search box, then the case chooser; with no
// case open (the grid and the filters are hidden) the empty state's "New case" button, then the other empty-state
// buttons. `query(selector)` finds a node or null. The first one really shown wins.
export function fallbackTarget({ query, noCase }) {
  const selectors = noCase
    ? [".case-empty-new", ".case-empty button", "#case-select", "#search-input"]
    : ["#grid-viewport", "#search-input", "#case-select", ".case-empty-new"];
  return firstShown(selectors.map((selector) => query(selector)));
}

// Recovery from failures (UI-25), one place: every part that failed registers what repeats it (`fail(name, run)`, again
// with the same name replaces; `clear(name)` when it worked or no longer matters); `retry()` is the only way in, for
// the bar's "Retry now" and for the Retry of a message. One recovery at a time: a click while one runs gets the same
// promise. It asks /api/health first; if the server is still down nothing else is touched. Then it repeats only the
// registered tasks (each is removed first and registers itself again if it fails again). With nothing registered it
// does something only when the connection was just restored (`fallback`, the view is applied again) or when the
// opening never finished (`reloadCases`).
// deps: health(), setOnline(), openingPending(), reloadCases(), fallback(), onError(err).
export function createRecovery({ health, setOnline, openingPending, reloadCases, fallback, onError, onChange }) {
  const tasks = new Map();
  let offline = false;
  let inflight = null;
  const changed = () => {
    if (typeof onChange === "function") {
      onChange();
    }
  };

  async function run() {
    try {
      await health();
    } catch (err) {
      return; // still down: the bar stays in error mode, every task stays registered
    }
    const wasOffline = offline;
    offline = false;
    setOnline();
    try {
      if (openingPending()) {
        await reloadCases();
        return;
      }
      const batch = [...tasks.values()];
      tasks.clear();
      changed();
      if (batch.length === 0) {
        if (wasOffline) {
          await fallback();
        }
        return;
      }
      const results = await Promise.allSettled(batch.map((task) => Promise.resolve().then(task)));
      for (const result of results) {
        if (result.status === "rejected") {
          onError(result.reason);
        }
      }
    } catch (err) {
      onError(err);
    }
  }

  return {
    retry() {
      if (inflight === null) {
        inflight = run().finally(() => {
          inflight = null;
        });
      }
      return inflight;
    },
    fail(name, task) {
      if (typeof task === "function") {
        tasks.set(name, task);
        changed();
      }
    },
    clear(name) {
      if (tasks.delete(name)) {
        changed();
      }
    },
    clearAll() {
      const had = tasks.size > 0;
      tasks.clear();
      if (had) {
        changed();
      }
    },
    markOffline() {
      offline = true;
    },
    has: (name) => tasks.has(name),
    get size() {
      return tasks.size;
    },
    get busy() {
      return inflight !== null;
    },
  };
}

export const MAX_STREAM_FAILURES = 3; // consecutive failures of the watch stream before it stops trying

// Consecutive failures of the watch stream (EventSource reconnects by itself and fails again while the server is
// down). `failed()` returns true on the failure that reaches the limit: the caller closes the stream. `opened()`
// resets the count; `reset()` forgets a stop (a new stream, Retry). Pure: no timers, no stream.
export function createStreamGuard(max = MAX_STREAM_FAILURES) {
  const limit = Number.isInteger(max) && max > 0 ? max : MAX_STREAM_FAILURES;
  let failures = 0;
  let stopped = false;
  return {
    failed() {
      if (stopped) {
        return false;
      }
      failures += 1;
      if (failures >= limit) {
        stopped = true;
        return true;
      }
      return false;
    },
    opened() {
      failures = 0;
    },
    // The browser itself gave up (CLOSED: no more events will come to reach the limit). True when this stopped it.
    stop() {
      if (stopped) {
        return false;
      }
      stopped = true;
      return true;
    },
    reset() {
      failures = 0;
      stopped = false;
    },
    get stopped() {
      return stopped;
    },
    get failures() {
      return failures;
    },
  };
}

// What is shown and repeated when something fails (UI-25), in one place with its effects injected. The notice with
// Retry is DERIVED from the set of registered recoverable failures (not from events): it is there while any task that
// has a message is registered, and gone when none is, so one recovery never removes the only way to repeat another.
// Network and session failures have no message of their own (the bar owns them).
// deps: status {message}, retryLabel, genericMessage, goOffline(onRetry), health(), setOnline(), openingPending(),
//   reloadCases(), fallback(), onError(err), and the operations that repeat what failed:
//   retryView() (apply the view that did not load), hasViewRetry(), dropViewRetry(), retryGrid(), retryCount(),
//   refreshTimeline() -> Promise, resumeStream(), cancelRequests() (case changed: grid, count, histogram, stream).
export function createFailureCenter(deps) {
  const messages = new Map(); // task name -> message text | null
  // The bar shows ONE message at a time, so what it shows is arbitrated here from the state, never from events:
  //   a 4xx error (`plain`, no Retry of its own) while tasks are registered keeps Retry (it repeats those tasks);
  //   tasks only: the latest task message with Retry; a 4xx alone: its text without Retry; nothing: nothing.
  // The handle is kept and the message is shown again when the bar no longer shows it (a newer message replaced it).
  let shown = null; // {handle, key}
  let dismissedKey = null; // the user pressed Dismiss on this very message: it stays away until the state changes
  let plain = null; // {name, text}: the last 4xx, until what it belongs to works, a new view starts or the case changes
  const dismissShown = () => {
    if (shown !== null) {
      const old = shown;
      shown = null; // before dismissing: the handle's own end is not the user's Dismiss
      old.handle.dismiss();
    }
  };
  let syncing = false;
  const syncNotice = () => {
    if (syncing) {
      return; // dismissing or showing a message makes the bar report "idle": not a second pass in the middle of this one
    }
    syncing = true;
    try {
      syncNoticeNow();
    } finally {
      syncing = false;
    }
  };
  const syncNoticeNow = () => {
    let taskText = null;
    for (const [name, message] of messages) {
      if (message !== null && recovery.has(name)) {
        taskText = message; // the most recent one
      }
    }
    const retryable = taskText !== null;
    const text = plain !== null ? plain.text : taskText;
    if (text === null) {
      dismissedKey = null;
      dismissShown();
      return;
    }
    const key = text + "\u0000" + (retryable ? "retry" : "");
    if (key === dismissedKey) {
      return;
    }
    dismissedKey = null;
    if (shown !== null && shown.key === key && shown.handle.isCurrent()) {
      return;
    }
    dismissShown();
    const options = { kind: "error" };
    const token = {};
    // Whatever ends the message (a newer message, its expiry, the user) is seen here or through onIdle(): the failure
    // that is still repeatable is put back once the bar is free, and a Dismiss by the user is respected.
    options.onEnd = (reason) => {
      if (shown !== null && shown.token === token) {
        if (reason === "dismissed") {
          dismissedKey = key;
          shown = null;
        }
      }
    };
    if (retryable) {
      options.action = { label: deps.retryLabel, run: () => recovery.retry() };
    }
    shown = { handle: deps.status.message(text, options), key, token };
  };
  const dropPlain = (name) => {
    if (plain !== null && (name === undefined || plain.name === name)) {
      plain = null;
    }
  };
  const recovery = createRecovery({
    health: deps.health,
    setOnline: deps.setOnline,
    openingPending: deps.openingPending,
    reloadCases: deps.reloadCases,
    fallback: deps.fallback,
    onError: deps.onError,
    onChange: syncNotice,
  });
  if (typeof deps.status.onIdle === "function") {
    deps.status.onIdle(syncNotice); // the bar is free again (an alert expired): show the failure that is still there
  }
  const messageOf = (err) => (err && err.message ? err.message : deps.genericMessage);

  // A failure: registered (and noticed) when repeating can help, otherwise any older task of the same part is
  // forgotten and, if `plain`, the error is shown once without Retry.
  function report(name, err, run, { plain: isPlain = false, hint = "" } = {}) {
    const kind = errorKind(err);
    messages.delete(name);
    dropPlain(name);
    if (isRetryable(kind)) {
      messages.set(name, kind === "network" || kind === "forbidden" ? null : messageOf(err));
      recovery.fail(name, run);
      syncNotice();
      return;
    }
    recovery.clear(name);
    if (isPlain && kind !== "forbidden") {
      plain = { name, text: hint ? messageOf(err) + " " + hint : messageOf(err) };
    }
    syncNotice();
  }
  function clear(name) {
    messages.delete(name);
    dropPlain(name);
    recovery.clear(name);
    syncNotice();
  }
  const countOrGrid = (scope) => (scope === "count" ? "count" : "grid");

  return {
    recovery,
    retry: () => recovery.retry(),
    goOffline() {
      recovery.markOffline();
      deps.goOffline(() => recovery.retry());
    },
    // grid.onFailure(err, {kind, scope, op}): the last operation that failed is the one Retry repeats.
    gridFailure(err, info) {
      const name = countOrGrid(info.scope);
      if (name === "grid" && info.op !== "reload") {
        deps.dropViewRetry(); // a navigation failed after the view change did: repeat THIS one, not the older one
      }
      const run = name === "count" ? deps.retryCount : () => (deps.hasViewRetry() ? deps.retryView() : deps.retryGrid());
      // A rejected page request is not repeated by itself (the scroll would hammer the server): Home clears it.
      report(name, err, run, { plain: true, hint: info.op === "page" ? deps.pageHint : "" });
    },
    gridRecovered(scope) {
      const name = countOrGrid(scope);
      if (name === "grid") {
        deps.dropViewRetry();
      }
      clear(name);
    },
    timelineFailure: (err) => report("timeline", err, () => deps.refreshTimeline()),
    timelineRecovered: () => clear("timeline"),
    // A live stream (watch, alerts, indexing progress) gave up after 3 failures: Retry opens it again.
    streamStopped(name = "stream", resume = () => deps.resumeStream()) {
      messages.delete(name);
      messages.set(name, null);
      recovery.fail(name, resume);
      this.goOffline();
    },
    // A new view change replaces the failed grid and count: their tasks go (the timeline and the stream stay).
    viewApplied() {
      // the 4xx of the grid or the count belonged to the view being replaced: clear() drops it
      clear("grid");
      clear("count");
    },
    // Another case: nothing that failed in the old one is repeated, and its requests are cancelled at once.
    caseChanged() {
      messages.clear();
      plain = null;
      recovery.clearAll();
      dismissShown();
      deps.cancelRequests();
    },
  };
}

export function normalizeKind(kind) {
  return MESSAGE_KINDS.includes(kind) ? kind : "info";
}

// One message at a time: the last one replaces the previous. `now` is injected (milliseconds).
export function createMessageState() {
  let current = null;
  let serial = 0;
  return {
    get current() {
      return current;
    },
    // text "" clears. Returns the new message (or null) so the caller can paint it.
    // options.action: { label, run } adds a button to the message (Retry); it needs a function and a label.
    show(text, { kind = "info", timeoutMs, action, onEnd } = {}, now = 0) {
      if (text === "" || text === null || text === undefined) {
        current = null;
        return null;
      }
      const resolvedKind = normalizeKind(kind);
      const timeout = timeoutMs === undefined ? messageTimeout(resolvedKind) : Math.max(0, Number(timeoutMs) || 0);
      serial += 1;
      const valid = action && typeof action.run === "function" && typeof action.label === "string" && action.label !== "";
      current = {
        id: serial, text: String(text), kind: resolvedKind, expiresAt: timeout > 0 ? now + timeout : null,
        action: valid ? { label: action.label, run: action.run } : null,
        onEnd: typeof onEnd === "function" ? onEnd : null,
      };
      return current;
    },
    // True when the current message expired at `now` and was cleared.
    expire(now) {
      if (current !== null && current.expiresAt !== null && now >= current.expiresAt) {
        current = null;
        return true;
      }
      return false;
    },
    // Clears only the message with this id (a stale dismiss handle must not clear a newer message).
    dismiss(id) {
      if (current !== null && current.id === id) {
        current = null;
        return true;
      }
      return false;
    },
  };
}
