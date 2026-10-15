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

// Retry now: when the opening is still pending (the case list never loaded) it reloads the cases, otherwise the view.
// deps: health(), setOnline(), openingPending(), reloadCases(), refreshView(). Failures keep the bar in error mode.
export function createConnectionRetry({ health, setOnline, openingPending, reloadCases, refreshView, onError }) {
  return async function retry() {
    try {
      await health();
    } catch (err) {
      return;
    }
    setOnline();
    try {
      await (openingPending() ? reloadCases() : refreshView());
    } catch (err) {
      onError(err);
    }
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
    show(text, { kind = "info", timeoutMs } = {}, now = 0) {
      if (text === "" || text === null || text === undefined) {
        current = null;
        return null;
      }
      const resolvedKind = normalizeKind(kind);
      const timeout = timeoutMs === undefined ? messageTimeout(resolvedKind) : Math.max(0, Number(timeoutMs) || 0);
      serial += 1;
      current = { id: serial, text: String(text), kind: resolvedKind, expiresAt: timeout > 0 ? now + timeout : null };
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
