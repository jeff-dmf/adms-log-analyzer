// Status bar model (UI-13): pure functions and the message state, testable without a DOM or a real clock.
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
export function createRecovery({ health, setOnline, openingPending, reloadCases, fallback, onError }) {
  const tasks = new Map();
  let offline = false;
  let inflight = null;

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
      }
    },
    clear(name) {
      tasks.delete(name);
    },
    clearAll() {
      tasks.clear();
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
    show(text, { kind = "info", timeoutMs, action } = {}, now = 0) {
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
