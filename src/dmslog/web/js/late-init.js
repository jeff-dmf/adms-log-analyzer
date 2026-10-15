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

// Starts every loader; resolves to the number of failures and calls onFailure(errors) once per call that had any.
export async function loadSections(loaders, onFailure) {
  const results = await Promise.allSettled(loaders.map((load) => load()));
  const errors = results.filter((r) => r.status === "rejected").map((r) => r.reason);
  if (errors.length > 0) {
    onFailure(errors);
  }
  return errors.length;
}
