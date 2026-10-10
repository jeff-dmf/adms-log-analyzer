// Pure planning of the "Save" button of the saved filters (no DOM): a new name creates, a name that
// already exists asks for confirmation and then overwrites, and a view being renamed keeps its filter.
import { MAX_NAME, MAX_VIEWS, sanitizeName } from "./filter-tree-model.js";

// Input: {raw, views: [{id, name}], renamingId: id|null, confirmedName: string|null}.
// Output: {action: "create"|"overwrite"|"rename"|"confirmOverwrite"|"error", name, id?, error?}
// where error is "empty" | "tooLong" | "duplicate" | "tooMany".
export function planSave({ raw, views, renamingId = null, confirmedName = null }) {
  const name = sanitizeName(raw);
  if (name === "") {
    return { action: "error", name, error: "empty" };
  }
  if ([...name].length > MAX_NAME) {
    return { action: "error", name, error: "tooLong" };
  }
  const same = (a, b) => a.toLowerCase() === b.toLowerCase();
  if (renamingId !== null) {
    if (views.some((v) => v.id !== renamingId && same(v.name, name))) {
      return { action: "error", name, error: "duplicate" };
    }
    return { action: "rename", name, id: renamingId };
  }
  const existing = views.find((v) => same(v.name, name));
  if (existing) {
    if (confirmedName !== null && same(confirmedName, name)) {
      return { action: "overwrite", name, id: existing.id };
    }
    return { action: "confirmOverwrite", name, id: existing.id };
  }
  if (views.length >= MAX_VIEWS) {
    return { action: "error", name, error: "tooMany" };
  }
  return { action: "create", name };
}

// Guards the answers of saved-filter requests: a case change (or a reload) between the request and
// its answer makes the answer stale, so it must not touch the list of the case now shown.
export function createViewsGuard() {
  let generation = 0;
  return {
    // Call when the shown list is replaced (case change, reload): older answers become stale.
    bump() {
      generation += 1;
    },
    // Call right before a request; pass the returned ticket to isCurrent() after the await.
    begin(caseId) {
      return { generation, caseId };
    },
    isCurrent(ticket, currentCaseId) {
      return ticket.generation === generation && ticket.caseId === currentCaseId;
    },
    // Runs request() and returns {stale: true} when the list changed meanwhile, whether the request
    // succeeded or failed; a current failure is rethrown. Returns {stale: false, value} otherwise.
    async run(caseId, getCurrentCase, request) {
      const ticket = { generation, caseId };
      const current = () => ticket.generation === generation && ticket.caseId === getCurrentCase();
      let value;
      try {
        value = await request();
      } catch (error) {
        if (!current()) {
          return { stale: true };
        }
        throw error;
      }
      return current() ? { stale: false, value } : { stale: true };
    },
  };
}
