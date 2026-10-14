// Bookmarks UI: the section of the detail pane (star toggle, color, note) and the bookmarks panel
// (filters, jump to, edit, delete, export/import). Notes and messages are user/ERS text
// (untrusted): textContent only; the lists have ONE delegated listener each.
import { api } from "./api.js";
import {
  BOOKMARK_COLORS, MAX_NOTE, buildBookmarkRow, checkNote, colorLabel, createBookmarksFlow,
} from "./bookmarks-model.js";
import { S, format, pluralKey } from "./strings.js";

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) {
    node.className = className;
  }
  if (text !== undefined) {
    node.textContent = text;
  }
  return node;
}

let uid = 0;
function nextId(prefix) {
  uid += 1;
  return prefix + uid;
}

function colorSelect(selected, withAny) {
  const select = element("select", "bm-color-select");
  if (withAny) {
    const any = element("option", "", S.bmColorAny);
    any.value = "";
    select.appendChild(any);
  }
  for (const color of BOOKMARK_COLORS) {
    const option = element("option", "", colorLabel(color));
    option.value = color;
    option.selected = color === selected;
    select.appendChild(option);
  }
  return select;
}

function swatch(color) {
  const node = element("span", "bm-swatch bm-c-" + color);
  node.appendChild(element("span", "bm-swatch-name", colorLabel(color)));
  return node;
}

function labelled(text, control) {
  const id = nextId("bm-ctl-");
  control.id = id;
  const label = element("label", "bm-label", text);
  label.htmlFor = id;
  const wrap = element("span", "bm-field");
  wrap.append(label, control);
  return wrap;
}

// ---- detail pane section --------------------------------------------------------------------
// container: the (initially hidden) element under the detail text. Options: store (the shared
// marker store), onError(err). Returns { show(entry), refresh() }.
export function initDetailBookmark(container, { store, onError }) {
  container.textContent = "";
  const toggle = element("button", "bm-toggle");
  toggle.type = "button";
  const editor = element("div", "bm-editor");
  const colorBox = colorSelect("yellow", false);
  const noteBox = element("textarea", "bm-note-input");
  noteBox.rows = 3;
  noteBox.maxLength = MAX_NOTE;
  noteBox.placeholder = S.bmNotePlaceholder;
  const save = element("button", "bm-save", S.bmNoteSave);
  save.type = "button";
  const state = element("span", "bm-muted");
  state.setAttribute("role", "status");
  editor.append(labelled(S.bmColor, colorBox), labelled(S.bmNote, noteBox), save, state);
  container.append(element("h3", "bm-detail-title", S.bmDetailTitle), toggle, editor);

  let entry = null;
  let loadedFor = null; // bookmark id whose note is in the textarea
  let seq = 0;

  async function fill(mark) {
    if (loadedFor === mark.id) {
      return;
    }
    loadedFor = mark.id;
    const mine = (seq += 1);
    colorBox.value = mark.color;
    noteBox.value = "";
    try {
      const full = await store.load(entry.id);
      if (mine === seq && full && loadedFor === full.id) {
        noteBox.value = full.note || "";
        colorBox.value = full.color;
      }
    } catch (error) {
      if (mine === seq) {
        onError(error);
      }
    }
  }

  function refresh() {
    if (!entry) {
      container.hidden = true;
      return;
    }
    container.hidden = false;
    const mark = store.get(entry.id);
    toggle.textContent = (mark ? "★ " : "☆ ") + (mark ? S.bmToggleOff : S.bmToggleOn);
    toggle.setAttribute("aria-pressed", mark ? "true" : "false");
    editor.hidden = !mark;
    if (mark) {
      fill(mark);
    } else {
      loadedFor = null;
      state.textContent = "";
    }
  }

  async function run(action) {
    try {
      await action();
    } catch (error) {
      onError(error);
    }
    refresh();
  }

  toggle.addEventListener("click", () => run(() => store.toggle(entry.id)));
  colorBox.addEventListener("change", () => run(() => store.update(entry.id, { color: colorBox.value })));
  save.addEventListener("click", async () => {
    const checked = checkNote(noteBox.value);
    if (checked.error) {
      state.textContent = S.bmActionFailed;
      return;
    }
    await run(() => store.update(entry.id, { note: checked.note === "" ? null : checked.note }));
    state.textContent = S.bmNoteSaved;
  });

  return {
    show(next) {
      entry = next;
      loadedFor = null;
      seq += 1;
      state.textContent = "";
      if (entry) {
        store.ensure([entry.id]);
      }
      refresh();
    },
    refresh,
    toggle: () => (entry ? run(() => store.toggle(entry.id)) : Promise.resolve()),
  };
}

// ---- the panel ------------------------------------------------------------------------------
// host: empty element (hidden until opened). Options: getCase(), store, onReveal({id, ts}),
// onError(err). Returns { open(), close(), refresh(), reset(), isOpen() }.
export function initBookmarksPanel(host, { getCase, store, onReveal, onError }) {
  host.textContent = "";
  const head = element("div", "bm-head");
  head.appendChild(element("h3", "bm-title", S.bmTitle));
  const exportButton = element("button", "bm-export", S.bmExport);
  exportButton.type = "button";
  const importButton = element("button", "bm-import", S.bmImport);
  importButton.type = "button";
  const fileInput = element("input", "visually-hidden");
  fileInput.type = "file";
  fileInput.accept = "application/json,.json";
  fileInput.setAttribute("aria-label", S.bmImportLabel);
  fileInput.tabIndex = -1;
  const closeButton = element("button", "bm-close", S.bmClose);
  closeButton.type = "button";
  head.append(exportButton, importButton, fileInput, closeButton);

  const filters = element("form", "bm-filters");
  const filterColor = colorSelect("", true);
  const filterText = element("input", "bm-filter-text");
  filterText.type = "search";
  filterText.autocomplete = "off";
  filterText.placeholder = S.bmFilterPlaceholder;
  filters.append(labelled(S.bmColor, filterColor), labelled(S.bmFilterText, filterText));

  const status = element("div", "bm-muted");
  status.setAttribute("role", "status");
  const list = element("ul", "bm-list");
  list.setAttribute("aria-label", S.bmTableCaption);
  const moreButton = element("button", "bm-more", S.bmLoadMore);
  moreButton.type = "button";
  host.append(head, filters, status, list, moreButton);

  let editing = null; // bookmark id being edited
  let confirming = null;
  let confirmTimer = null;
  let message = "";
  let messageError = false;
  const rows = new Map(); // bookmark id -> row shaped by buildBookmarkRow

  function setMessage(text, isError) {
    message = text;
    messageError = Boolean(isError);
  }

  function cancelConfirm() {
    confirming = null;
    if (confirmTimer !== null) {
      clearTimeout(confirmTimer);
      confirmTimer = null;
    }
  }

  function itemNode(item) {
    const row = buildBookmarkRow(item);
    rows.set(row.id, row);
    const li = element("li", "bm-item" + (row.orphan ? " bm-orphan" : ""));
    li.dataset.id = String(row.id);
    const top = element("div", "bm-item-top");
    top.appendChild(swatch(row.color));
    if (row.orphan) {
      top.appendChild(element("span", "bm-muted", S.bmOrphan));
    } else {
      top.appendChild(element("span", "bm-mono", row.time));
    }
    top.appendChild(element("span", "bm-muted bm-mono", row.location));
    li.appendChild(top);
    if (row.text) {
      li.appendChild(element("div", "bm-text bm-mono", row.text));
    }
    if (editing === row.id) {
      const form = element("div", "bm-edit");
      const color = colorSelect(row.color, false);
      color.dataset.role = "color";
      const note = element("textarea", "bm-note-input");
      note.rows = 3;
      note.maxLength = MAX_NOTE;
      note.value = row.note;
      note.dataset.role = "note";
      form.append(labelled(S.bmColor, color), labelled(S.bmNote, note));
      for (const [act, label] of [["save", S.bmNoteSave], ["cancel", S.svCancel]]) {
        const b = element("button", "bm-act", label);
        b.type = "button";
        b.dataset.act = act;
        form.appendChild(b);
      }
      li.appendChild(form);
    } else if (row.note) {
      li.appendChild(element("div", "bm-note", row.note));
    }
    const actions = element("div", "bm-actions");
    const asking = confirming === row.id;
    const defs = [];
    if (!row.orphan) {
      defs.push(["jump", S.bmJump, format(S.bmJumpNamed, { id: row.entryId })]);
    }
    defs.push(["edit", S.bmEdit, format(S.bmEditNamed, { id: row.id })]);
    defs.push(["delete", asking ? S.bmConfirmDelete : S.bmDelete, format(S.bmDeleteNamed, { id: row.id })]);
    for (const [act, label, aria] of defs) {
      const b = element("button", "bm-act" + (act === "delete" && asking ? " bm-danger" : ""), label);
      b.type = "button";
      b.dataset.act = act;
      b.setAttribute("aria-label", aria);
      actions.appendChild(b);
    }
    actions.appendChild(element("span", "bm-muted", format(S.bmCreated, { date: row.created })));
    li.appendChild(actions);
    return li;
  }

  function onState(state) {
    if (state.status === "idle") {
      host.hidden = true;
      list.textContent = "";
      return;
    }
    host.hidden = false;
    list.textContent = "";
    rows.clear();
    for (const item of state.items) {
      list.appendChild(itemNode(item));
    }
    moreButton.hidden = state.next === null || state.status !== "ready";
    moreButton.disabled = state.loadingMore;
    status.classList.toggle("bm-error", state.status === "error" || messageError);
    if (state.status === "nocase") {
      status.textContent = S.bmNoCase;
    } else if (state.status === "loading") {
      status.textContent = S.bmLoading;
    } else if (state.status === "error") {
      status.textContent = S.bmError + (state.error ? ": " + state.error : "");
    } else if (message) {
      status.textContent = message;
    } else if (state.items.length === 0) {
      status.textContent = state.color || state.text ? S.bmNoMatch : S.bmEmpty;
    } else {
      status.textContent = format(S[pluralKey(state.next === null ? "bmCount" : "bmCountMore", state.items.length)], { count: state.items.length });
    }
  }

  const flow = createBookmarksFlow({ api, getCase, onState });

  // Runs a user action; a failure is shown and the list reloaded, unless the panel was closed or
  // another case opened meanwhile (then the failure belongs to a list that is no longer shown).
  async function guarded(action) {
    const forCase = getCase();
    try {
      await action();
    } catch (error) {
      if (host.hidden || getCase() !== forCase) {
        return;
      }
      setMessage(error && error.message ? error.message : S.bmActionFailed, true);
      onError(error);
      await flow.refresh();
    }
  }

  filters.addEventListener("submit", (event) => event.preventDefault());
  const applyFilters = () => {
    editing = null;
    cancelConfirm();
    setMessage("", false);
    flow.setFilters({ color: filterColor.value, text: filterText.value });
  };
  filterColor.addEventListener("change", applyFilters);
  let filterTimer = null;
  filterText.addEventListener("input", () => {
    clearTimeout(filterTimer);
    filterTimer = setTimeout(applyFilters, 250);
  });

  list.addEventListener("click", (event) => {
    const button = event.target instanceof Element ? event.target.closest("button[data-act]") : null;
    const li = button ? button.closest("li[data-id]") : null;
    if (!button || !li || !list.contains(li)) {
      return;
    }
    const id = Number(li.dataset.id);
    const row = rows.get(id);
    if (!row) {
      return;
    }
    const act = button.dataset.act;
    if (act !== "delete") {
      cancelConfirm();
    }
    if (act === "jump" && row.entryId !== null) {
      onReveal({ id: row.entryId, ts: row.ts });
    } else if (act === "edit") {
      editing = id;
      onState(flow.state);
    } else if (act === "cancel") {
      editing = null;
      onState(flow.state);
    } else if (act === "save") {
      const checked = checkNote(li.querySelector('[data-role="note"]').value);
      if (checked.error) {
        setMessage(format(S.bmNoteTooLong, { limit: MAX_NOTE }), true);
        onState(flow.state);
        return;
      }
      const color = li.querySelector('[data-role="color"]').value;
      guarded(async () => {
        const changed = await flow.updateItem(id, { color, note: checked.note === "" ? null : checked.note });
        if (changed === null) {
          return; // the list changed (another case, a reload) while the request ran: drop the answer
        }
        editing = null;
        setMessage("", false);
        store.invalidate();
      });
    } else if (act === "delete") {
      if (confirming !== id) {
        cancelConfirm();
        confirming = id;
        confirmTimer = setTimeout(() => {
          cancelConfirm();
          onState(flow.state);
        }, 4000);
        onState(flow.state);
        return;
      }
      cancelConfirm();
      guarded(async () => {
        if (await flow.deleteItem(id)) {
          store.invalidate();
        }
      });
    }
  });

  moreButton.addEventListener("click", () => flow.loadMore());
  closeButton.addEventListener("click", () => close());

  exportButton.addEventListener("click", () => {
    const forCase = getCase();
    if (!forCase) {
      return;
    }
    guarded(async () => {
      const data = await api.bookmarks.exportAll(forCase);
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = S.bmExportName;
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    });
  });
  importButton.addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", () => {
    const file = fileInput.files && fileInput.files[0];
    fileInput.value = "";
    const forCase = getCase();
    if (!file || !forCase) {
      return;
    }
    guarded(async () => {
      const data = JSON.parse(await file.text());
      const counts = await api.bookmarks.importAll(forCase, data);
      if (forCase !== getCase()) {
        return; // another case is open now: its list is not affected by this import
      }
      setMessage(format(S.bmImported, counts), false);
      store.invalidate();
      await flow.refresh();
    });
  });

  function close() {
    cancelConfirm();
    editing = null;
    setMessage("", false);
    flow.close();
  }

  return {
    open() {
      setMessage("", false);
      return flow.open();
    },
    close,
    refresh: () => flow.refresh(),
    reset: close,
    isOpen: () => !host.hidden,
  };
}
