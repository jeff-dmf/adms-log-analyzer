// Built-in folder browser (UI-22): a native modal <dialog> (the common dialog frame of panel-shell.js: focus trap, Escape,
// focus back to the opener) that lists folders and ZIPs through /api/fs/list and resolves to the chosen ZIPs. It is a
// <dialog> because it opens on top of the New case / Add files dialogs, and a modal <dialog> makes everything outside
// itself inert: a plain element appended to the body could not be used there. File names are untrusted data:
// textContent only; long names are cut in the middle (the full name is the tooltip) and ERS ZIPs carry a kind badge.
import { FolderBrowser, breadcrumbs, entryKind, entryLabel, restoreFocusKey, trapTarget } from "./folder-browser-model.js";
import { KIND_ENTERPRISE, formatSize } from "./case-files-model.js";
import { createDialogShell } from "./panel-shell.js";
import { S, format, pluralKey } from "./strings.js";

const SHORTCUT_LABELS = {
  home: () => S.fbHome,
  desktop: () => S.fbDesktop,
  downloads: () => S.fbDownloads,
  documents: () => S.fbDocuments,
  root: () => S.fbRoot,
  media: () => S.fbMedia,
};

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) {
    node.className = className;
  }
  if (text !== undefined) {
    node.textContent = text;
  }
  return node;
}

function button(label, handler, className) {
  const node = el("button", className, label);
  node.type = "button";
  node.addEventListener("click", handler);
  return node;
}

function formatDate(ms) {
  if (typeof ms !== "number") {
    return "";
  }
  try {
    return new Date(ms).toLocaleString();
  } catch (err) {
    return "";
  }
}

const FOCUSABLE = "button:not([disabled]), input:not([disabled]), [tabindex='0']";

// Opens the browser; resolves to [{path, size}] (possibly empty) or null when closed without adding.
export function openFolderBrowser({ fs, storage, host }) {
  const doc = (host && host.ownerDocument) || document;
  const parentNode = host || doc.body;
  const previousFocus = doc.activeElement;
  return new Promise((resolve) => {
    const model = new FolderBrowser({ fs, storage, onChange: () => render() });
    let active = 0; // index in the rendered rows
    let rows = [];
    let settled = false;
    const settle = (result) => {
      if (!settled) {
        settled = true;
        resolve(result);
      }
    };

    // Any close (button, Escape, Cancel, the native event) ends here: the result is whatever settled first, else null.
    const shell = createDialogShell(doc, {
      title: S.fbTitle,
      className: "fb-dialog",
      onClose() {
        document.removeEventListener("keydown", onKey, true);
        dialog.remove();
        settle(null);
      },
    });
    const dialog = shell.dialog;

    const bar = el("div", "fb-bar");
    const upButton = button(S.fbUp, () => goUp(), "ui-button fb-up");
    const pathInput = el("input", "ui-input fb-path");
    pathInput.type = "text";
    pathInput.autocomplete = "off";
    pathInput.setAttribute("aria-label", S.fbPathLabel);
    pathInput.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        const value = pathInput.value.trim();
        if (value !== "") {
          model.load(value);
        }
      }
    });
    const hiddenLabel = el("label", "fb-hidden-toggle");
    const hiddenBox = el("input");
    hiddenBox.type = "checkbox";
    hiddenBox.addEventListener("change", () => model.setShowHidden(hiddenBox.checked));
    hiddenLabel.appendChild(hiddenBox);
    hiddenLabel.appendChild(document.createTextNode(" " + S.fbShowHidden));
    bar.appendChild(upButton);
    bar.appendChild(pathInput);
    bar.appendChild(hiddenLabel);

    const crumbs = el("nav", "fb-crumbs");
    crumbs.setAttribute("aria-label", S.fbBreadcrumbs);

    const body = el("div", "fb-body");
    const places = el("ul", "fb-places");
    places.setAttribute("aria-label", S.fbShortcuts);
    const list = el("ul", "fb-list");
    list.setAttribute("role", "listbox");
    list.setAttribute("aria-multiselectable", "true");
    list.setAttribute("aria-label", S.fbList);
    list.tabIndex = 0;
    body.appendChild(places);
    body.appendChild(list);

    const hint = el("p", "fb-hint", S.fbHint);
    const status = el("div", "fb-status");
    status.setAttribute("role", "status");
    status.setAttribute("aria-live", "polite");
    const errorBox = el("div", "fb-error");
    errorBox.setAttribute("role", "alert");

    // The main action is fixed in the foot; the count sits at its left.
    const count = el("span", "fb-count");
    count.setAttribute("aria-live", "polite");
    const selectAll = button(S.fbSelectAll, () => model.selectAllVisible(), "ui-button is-ghost");
    const addButton = button(S.fbAddSelected, () => close(model.selectedEntries()), "ui-button is-primary fb-add");
    const cancelButton = button(S.cfCancel, () => close(null), "ui-button");

    for (const node of [bar, crumbs, body, hint, status, errorBox]) {
      shell.body.appendChild(node);
    }
    for (const node of [count, selectAll, cancelButton, addButton]) {
      shell.foot.appendChild(node);
    }
    parentNode.appendChild(dialog);

    function goUp() {
      if (model.parent !== null) {
        model.load(model.parent);
      }
    }

    function enter(entry) {
      if (entry.type === "dir") {
        model.load(entry.path);
      }
    }

    function close(result) {
      settle(result);
      shell.close();
    }

    // Without a native modal <dialog> nothing traps the Tab key: the same trap as before keeps it inside.
    function onKey(event) {
      if (event.key !== "Tab" || typeof dialog.showModal === "function" || !shell.isOpen()) {
        return;
      }
      const nodes = [...dialog.querySelectorAll(FOCUSABLE)].filter((n) => !n.hidden);
      const current = document.activeElement;
      const target = trapTarget(nodes.length, nodes.indexOf(current), Boolean(current) && dialog.contains(current), event.shiftKey);
      if (target !== null) {
        event.preventDefault();
        (target === "first" ? nodes[0] : nodes[nodes.length - 1]).focus();
      }
    }

    function setActive(index, scroll = true) {
      if (rows.length === 0) {
        return;
      }
      active = Math.max(0, Math.min(rows.length - 1, index));
      rows.forEach((row, i) => row.node.classList.toggle("active", i === active));
      list.setAttribute("aria-activedescendant", rows[active].node.id);
      if (scroll && typeof rows[active].node.scrollIntoView === "function") {
        rows[active].node.scrollIntoView({ block: "nearest" });
      }
    }

    list.addEventListener("keydown", (event) => {
      if (rows.length === 0) {
        if (event.key === "Backspace") {
          event.preventDefault();
          goUp();
        }
        return;
      }
      const entry = rows[active].entry;
      if (event.key === "ArrowDown") {
        event.preventDefault();
        setActive(active + 1);
      } else if (event.key === "ArrowUp") {
        event.preventDefault();
        setActive(active - 1);
      } else if (event.key === "Home") {
        event.preventDefault();
        setActive(0);
      } else if (event.key === "End") {
        event.preventDefault();
        setActive(rows.length - 1);
      } else if (event.key === "Enter") {
        event.preventDefault();
        if (entry.type === "dir") {
          enter(entry);
        } else if (model.selectedCount > 0 && model.isSelected(entry)) {
          close(model.selectedEntries());
        } else {
          model.select(entry, "toggle");
        }
      } else if (event.key === " ") {
        event.preventDefault();
        model.select(entry, event.shiftKey ? "range" : "toggle");
      } else if (event.key === "Backspace") {
        event.preventDefault();
        goUp();
      } else if ((event.key === "a" || event.key === "A") && (event.ctrlKey || event.metaKey)) {
        event.preventDefault();
        model.selectAllVisible();
      }
    });

    function renderRow(entry, index) {
      const node = el("li", "fb-row " + (entry.type === "dir" ? "fb-dir" : "fb-zip"));
      node.id = "fb-row-" + index;
      node.setAttribute("role", "option");
      const isZip = entry.type === "zip";
      if (isZip) {
        const selected = model.isSelected(entry);
        node.setAttribute("aria-selected", selected ? "true" : "false");
        node.classList.toggle("selected", selected);
        if (entry.broken) {
          node.setAttribute("aria-disabled", "true");
          node.classList.add("disabled");
        }
        const box = el("input", "fb-check");
        box.type = "checkbox";
        box.checked = selected;
        box.disabled = Boolean(entry.broken);
        box.tabIndex = -1;
        box.setAttribute("data-fb-key", "check:" + entry.path);
        box.setAttribute("aria-label", entry.name);
        box.addEventListener("click", (event) => event.stopPropagation());
        box.addEventListener("change", () => {
          setActive(index, false);
          model.select(entry, "toggle");
        });
        node.appendChild(box);
      } else {
        node.appendChild(el("span", "fb-icon", "▸"));
      }
      let name = entryLabel(entry.name);
      if (entry.link) {
        name += " ↪";
      }
      const nameNode = el("span", "fb-name", name);
      nameNode.title = entry.name; // the full name, because a long one is cut in the middle
      node.appendChild(nameNode);
      const kind = entryKind(entry);
      if (kind !== null) {
        node.appendChild(el("span", "ui-badge fb-kind" + (kind === KIND_ENTERPRISE ? "" : " is-sensitive"), kind === KIND_ENTERPRISE ? S.cfKindEnterprise : S.cfKindSensitive));
      }
      if (entry.broken) {
        node.appendChild(el("span", "fb-meta", S.fbBroken));
      } else if (isZip) {
        node.appendChild(el("span", "fb-meta fb-size", formatSize(entry.size)));
        node.appendChild(el("span", "fb-meta fb-date", formatDate(entry.mtime)));
      }
      node.addEventListener("click", (event) => {
        setActive(index, false);
        list.focus();
        if (entry.type === "dir") {
          return;
        }
        if (event.shiftKey) {
          model.select(entry, "range");
        } else if (event.ctrlKey || event.metaKey) {
          model.select(entry, "toggle");
        } else {
          model.select(entry, "only");
        }
      });
      node.addEventListener("dblclick", () => {
        if (entry.type === "dir") {
          enter(entry);
        }
      });
      return node;
    }

    function shortcutLabel(item) {
      if (item.kind === "drive") {
        return format(S.fbDrive, { path: item.path });
      }
      const make = SHORTCUT_LABELS[item.kind];
      return make ? make() : item.path;
    }

    function focusKey() {
      const current = document.activeElement;
      return current && dialog.contains(current) && typeof current.getAttribute === "function"
        ? current.getAttribute("data-fb-key")
        : null;
    }

    // The render replaces crumbs, shortcuts and checkboxes: put the focus back on the same element
    // (by key) or, if it is gone, on the list so it never falls to the page behind the modal.
    function restoreFocus(previousKey, hadFocus) {
      if (!hadFocus) {
        return;
      }
      const found = [...dialog.querySelectorAll("[data-fb-key]")];
      const key = restoreFocusKey(previousKey, found.map((n) => n.getAttribute("data-fb-key")));
      const target = key === null ? list : found.find((n) => n.getAttribute("data-fb-key") === key);
      target.focus();
    }

    function render() {
      const hadFocus = Boolean(document.activeElement) && dialog.contains(document.activeElement);
      const previousKey = focusKey();
      pathInput.value = model.path;
      upButton.disabled = model.parent === null;
      hiddenBox.checked = model.showHidden;
      crumbs.textContent = "";
      breadcrumbs(model.path).forEach((crumb, index, all) => {
        const node = button(crumb.label, () => model.load(crumb.path), "fb-crumb");
        node.setAttribute("data-fb-key", "crumb:" + crumb.path);
        if (index === all.length - 1) {
          node.setAttribute("aria-current", "location");
        }
        crumbs.appendChild(node);
      });
      places.textContent = "";
      for (const item of model.shortcuts) {
        const li = el("li");
        const place = button(shortcutLabel(item), () => model.load(item.path), "fb-place");
        place.setAttribute("data-fb-key", "place:" + item.path);
        li.appendChild(place);
        places.appendChild(li);
      }
      list.textContent = "";
      rows = [];
      const entries = model.visible;
      entries.forEach((entry, index) => {
        const node = renderRow(entry, index);
        rows.push({ node, entry });
        list.appendChild(node);
      });
      if (rows.length > 0) {
        setActive(Math.min(active, rows.length - 1), false);
      } else {
        list.removeAttribute("aria-activedescendant");
      }
      list.setAttribute("aria-busy", model.status === "loading" ? "true" : "false");
      const notes = [];
      if (model.status === "loading") {
        notes.push(S.fbLoading);
      } else if (model.status === "ready" && entries.length === 0) {
        notes.push(model.entries.length === 0 ? S.fbEmpty : S.fbNoZips);
      }
      if (model.truncated) {
        notes.push(format(S[pluralKey("fbTruncated", model.entries.length)], { n: model.entries.length.toLocaleString("en-US") }));
      }
      if (model.otherFiles > 0) {
        notes.push(format(S[pluralKey("fbOtherFiles", model.otherFiles)], { n: model.otherFiles.toLocaleString("en-US") }));
      }
      if (model.skipped > 0) {
        notes.push(format(S[pluralKey("fbSkipped", model.skipped)], { n: model.skipped.toLocaleString("en-US") }));
      }
      status.textContent = notes.join(" · ");
      errorBox.textContent = model.status === "error" && model.error
        ? S.fbErrorPrefix + ": " + model.error.message
        : "";
      count.textContent = format(S.fbSelected, { n: model.selectedCount });
      addButton.disabled = model.selectedCount === 0;
      selectAll.disabled = !model.visible.some((e) => model.selectable(e));
      restoreFocus(previousKey, hadFocus && !dialog.contains(document.activeElement));
    }

    document.addEventListener("keydown", onKey, true);
    render();
    shell.open(previousFocus);
    list.focus();
    model.open();
  });
}
