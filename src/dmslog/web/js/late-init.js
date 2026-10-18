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

// Opens a drawer (UI-17) before `action` reveals or focuses something inside it. With the drawers module already in,
// everything runs synchronously (the focus the action sets is not overtaken by a later step); otherwise the module is
// loaded first (load() is retryable, so a failed import is tried again here). A load or open failure goes to onError and
// the action still runs: the panel is just not opened, the action is never lost.
export function afterDrawerOpen({ current, load, which, action, onError }) {
  const drawers = current();
  if (drawers) {
    drawers.open(which);
    return action();
  }
  return Promise.resolve()
    .then(load)
    .then((loaded) => loaded.open(which))
    .catch(onError)
    .then(action);
}

// The first use of a control whose module is not in (or failed to load): loads it (again) and opens the drawer.
export function openWhenLoaded({ current, load, which, onError }) {
  if (current() !== null) {
    return Promise.resolve(); // the module's own handler does the work
  }
  return Promise.resolve().then(load).then((loaded) => loaded.open(which)).catch(onError);
}

/**
 * One way to make the detail show a record, for the grid (a selection), the record menu and the palette. Pieces from main.js:
 *   getEntry()  the record the detail shows (or null)     show(id)  loads it (resolves when the request ended)
 *   hold(id)    true when `id` is the one shown, after aborting any request for another record that is still loading
 * ensure(row) -> Promise<boolean>: true when the detail shows `row`. A record already shown is only held (so a late answer for
 * another record cannot replace it); a request already running for the same record is shared; `refresh` loads it again.
 */
export function createRecordLoader({ getEntry, show, hold }) {
  let running = null; // { id, promise }
  const shown = (row) => {
    const entry = getEntry();
    return Boolean(entry) && String(entry.id) === String(row.id);
  };
  return {
    ensure(row, { refresh = false } = {}) {
      if (!refresh && shown(row) && hold(row.id)) {
        running = null;
        return Promise.resolve(true);
      }
      if (running !== null && String(running.id) === String(row.id)) {
        return running.promise;
      }
      const mine = { id: row.id, promise: null };
      mine.promise = Promise.resolve(show(row.id)).then(() => shown(row), () => false).finally(() => {
        if (running === mine) {
          running = null;
        }
      });
      running = mine;
      return mine.promise;
    },
  };
}

/**
 * Watches what makes a pending request for a menu obsolete before its module is in: a scroll, a wheel, a click or the focus
 * moving to another control (`keep`, the grid, may have it), the window losing the focus or resizing. Calls cancel() on each.
 * Returns stop(), which removes every listener (safe to call twice).
 */
export function watchGesture({ doc, win, keep = null, cancel }) {
  const hit = () => cancel();
  const focusHit = (event) => {
    if (event.target !== keep) {
      cancel();
    }
  };
  doc.addEventListener("scroll", hit, true);
  doc.addEventListener("wheel", hit, true);
  doc.addEventListener("pointerdown", hit, true);
  doc.addEventListener("focusin", focusHit);
  win.addEventListener("blur", hit);
  win.addEventListener("resize", hit);
  let on = true;
  return () => {
    if (on) {
      on = false;
      doc.removeEventListener("scroll", hit, true);
      doc.removeEventListener("wheel", hit, true);
      doc.removeEventListener("pointerdown", hit, true);
      doc.removeEventListener("focusin", focusHit);
      win.removeEventListener("blur", hit);
      win.removeEventListener("resize", hit);
    }
  };
}
