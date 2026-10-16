// Helpers for the modules that load after the opening (UI-14). No DOM, no globals: main.js hands in the pieces.

// Mounts a module that arrives late. The case is read when the module has arrived (not when the load started), so a
// case opened or switched meanwhile is the one seen; with a case already open the instance is told about it.
// Rejects when wait, load or mount reject.
export async function mountLate({ wait, load, mount, getCase, onCaseOpen }) {
  await wait;
  const module = await load();
  const instance = mount(module);
  if (getCase() !== null) {
    onCaseOpen(instance);
  }
  return instance;
}

// Memoizes load() while it runs and after it succeeds; a rejection is forgotten, so the next call tries again.
export function retryable(load) {
  let pending = null;
  return () => {
    if (pending === null) {
      pending = Promise.resolve().then(load).catch((err) => {
        pending = null;
        throw err;
      });
    }
    return pending;
  };
}

// Gate for a button whose action lives in a module that loads late: disabled (with a title that says why) while the
// module loads and after it failed, enabled once it is in. track(promise) is called for every load attempt; after a
// success it is a no-op, so a focused button is never disabled again. A failed attempt rethrows (the caller reports it).
export function createButtonGate(button, { loadingTitle, failedTitle }) {
  let ready = false;
  const set = (disabled, title) => {
    button.disabled = disabled;
    button.title = title;
    button.setAttribute("aria-disabled", disabled ? "true" : "false");
  };
  set(true, loadingTitle);
  return {
    track(promise) {
      if (ready) {
        return promise;
      }
      set(true, loadingTitle);
      return promise.then(
        (value) => {
          ready = true;
          set(false, "");
          return value;
        },
        (err) => {
          set(true, failedTitle);
          throw err;
        },
      );
    },
  };
}

// Starts every loader; resolves to the number of failures and calls onFailure(errors) once per call that had any.
export async function loadSections(loaders, onFailure) {
  const results = await Promise.allSettled(loaders.map((load) => load()));
  const errors = results.filter((r) => r.status === "rejected").map((r) => r.reason);
  if (errors.length > 0) {
    onFailure(errors);
  }
  return errors.length;
}
