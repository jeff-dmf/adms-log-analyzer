// Highlight rules UI (ROADMAP 9.1): the "Highlights" panel (list, priority, color, on/off, quick form,
// "add the current filter"), the detail section ("Highlight this key", "Highlighted by"), the footer help
// and the ]/[ navigation between highlighted records of the view. Rule names, conditions and key values
// are user or log text: only textContent, attribute values and class names are written.
import {
  MAX_RULES, PALETTE, QUICK_KINDS, buildCondition, colorLabel, defaultName, describeCondition, highlightNavKey,
  nextColor,
} from "./highlight-model.js";
import { isTypingTarget } from "./filters.js";
import { S, format } from "./strings.js";

const STYLESHEET = "/css/highlight.css";
const MAX_KEY_CHIPS = 8;

function ensureStylesheet() {
  if (!document.querySelector('link[rel="stylesheet"][href="' + STYLESHEET + '"]')) {
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = STYLESHEET;
    document.head.appendChild(link);
  }
}

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

function button(className, text) {
  const node = element("button", className, text);
  node.type = "button";
  return node;
}

function colorSelect(id, value) {
  const select = element("select", "hl-color");
  if (id) {
    select.id = id;
  }
  for (const color of PALETTE) {
    const option = element("option", "", colorLabel(color));
    option.value = color;
    select.appendChild(option);
  }
  select.value = value;
  return select;
}

function swatch(color) {
  const node = element("span", "hl-swatch hl-c-" + color, colorLabel(color));
  return node;
}

// <label for=control><span>text</span>control</label>; the span lets the caption change later.
function labeled(text, control) {
  const label = element("label");
  label.htmlFor = control.id;
  label.caption = element("span", "", text);
  label.append(label.caption, control);
  return label;
}

// options: toolbar, beforeButton, panelParent, beforePanel, footer (element after which the help line
// goes), detailAnchor (element after which the detail section goes), store (createHighlightStore),
// getCase, getFilter (the view filter), getSelectedRow, reveal(row), showMessage(text, isError).
export function mountHighlights(options) {
  const { store, getCase, getFilter, getSelectedRow, reveal, showMessage } = options;
  ensureStylesheet();

  // -- toolbar button and panel
  const toggle = button("hl-button", S.hlButton);
  toggle.setAttribute("aria-expanded", "false");
  toggle.setAttribute("aria-controls", "hl-panel");
  options.toolbar.insertBefore(toggle, options.beforeButton || null);

  const panel = element("section", "hl-panel");
  panel.id = "hl-panel";
  panel.hidden = true;
  const head = element("div", "hl-head");
  const title = element("h3", "hl-title", S.hlTitle);
  title.id = "hl-title";
  panel.setAttribute("aria-labelledby", title.id);
  const count = element("span", "hl-empty");
  head.append(title, count);
  const list = element("ul", "hl-list");
  const status = element("div", "hl-status");
  status.setAttribute("role", "status");
  status.setAttribute("aria-live", "polite");

  const form = element("form", "hl-form");
  const kindSelect = element("select", "hl-kind");
  kindSelect.id = "hl-kind";
  for (const kind of QUICK_KINDS) {
    const option = element("option", "", S.hlKinds[kind]);
    option.value = kind;
    kindSelect.appendChild(option);
  }
  const extraInput = element("input", "hl-extra");
  extraInput.id = "hl-extra";
  extraInput.type = "text";
  extraInput.autocomplete = "off";
  extraInput.maxLength = 64;
  const extraLabel = labeled(S.hlKeyType, extraInput);
  const valueInput = element("input", "hl-value");
  valueInput.id = "hl-value";
  valueInput.type = "text";
  valueInput.autocomplete = "off";
  const valueLabel = labeled(S.hlValue, valueInput);
  const sevSelect = element("select", "hl-sev");
  sevSelect.id = "hl-sev";
  for (const n of [1, 2, 3, 4, 5]) {
    const option = element("option", "", S.severityNames[n]);
    option.value = String(n);
    sevSelect.appendChild(option);
  }
  sevSelect.value = "2";
  const sevLabel = labeled(S.hlValue, sevSelect);
  const nameInput = element("input", "hl-name-input");
  nameInput.id = "hl-name";
  nameInput.type = "text";
  nameInput.autocomplete = "off";
  nameInput.maxLength = 80;
  const nameLabel = labeled(S.hlName, nameInput);
  const newColor = colorSelect("hl-new-color", PALETTE[0]);
  const colorLabelEl = labeled(S.hlColor, newColor);
  const kindLabel = labeled(S.hlKind, kindSelect);
  const addButton = element("button", "hl-add", S.hlAdd);
  addButton.type = "submit";
  const addFilterButton = button("hl-add-filter", S.hlAddFilter);
  form.append(kindLabel, extraLabel, valueLabel, sevLabel, nameLabel, colorLabelEl, addButton, addFilterButton);
  panel.append(head, list, form, status);
  options.panelParent.insertBefore(panel, options.beforePanel || null);

  function setStatus(text, isError) {
    status.textContent = text;
    status.classList.toggle("is-error", Boolean(isError));
  }

  function errorText(error) {
    return format(S.hlError, { error: error && error.message ? error.message : S.errorGeneric });
  }

  function syncForm() {
    const kind = kindSelect.value;
    const needsExtra = kind === "key" || kind === "field";
    extraLabel.hidden = !needsExtra;
    extraLabel.caption.textContent = kind === "field" ? S.hlFieldName : S.hlKeyType;
    valueLabel.hidden = kind === "sev";
    sevLabel.hidden = kind !== "sev";
  }
  kindSelect.addEventListener("change", syncForm);
  syncForm();

  function full() {
    if (store.rules().length >= MAX_RULES) {
      setStatus(format(S.hlLimit, { max: MAX_RULES }), true);
      return true;
    }
    return false;
  }

  async function run(action) {
    try {
      await action();
      setStatus("", false);
    } catch (error) {
      setStatus(errorText(error), true);
    }
  }

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    if (!getCase()) {
      setStatus(S.needCase, true);
      return;
    }
    if (full()) {
      return;
    }
    const kind = kindSelect.value;
    const value = kind === "sev" ? sevSelect.value : valueInput.value;
    const built = buildCondition(kind, value, extraInput.value);
    if (built.error) {
      setStatus(S.hlBad[built.error] || S.errorGeneric, true);
      return;
    }
    const name = nameInput.value.trim() || defaultName(kind, value, extraInput.value);
    run(async () => {
      await store.create({ name, color: newColor.value, cond: built.cond, enabled: true });
      valueInput.value = "";
      nameInput.value = "";
      newColor.value = nextColor(store.rules());
    });
  });

  addFilterButton.addEventListener("click", () => {
    const filter = getFilter();
    if (!getCase() || !filter) {
      setStatus(S.hlNoFilter, true);
      return;
    }
    if (full()) {
      return;
    }
    const name = nameInput.value.trim() || describeCondition(filter).slice(0, 80);
    run(() => store.create({ name, color: newColor.value, cond: filter, enabled: true }));
  });

  function renderList() {
    list.textContent = "";
    const rules = store.rules();
    count.textContent = format(S.hlCount, { count: rules.length, max: MAX_RULES });
    if (!getCase()) {
      list.appendChild(element("li", "hl-empty", S.needCase));
      return;
    }
    if (rules.length === 0) {
      list.appendChild(element("li", "hl-empty", S.hlEmpty));
      return;
    }
    rules.forEach((rule, index) => {
      const item = element("li", "hl-rule" + (rule.enabled ? "" : " is-off"));
      const box = element("input", "hl-enabled");
      box.type = "checkbox";
      box.checked = Boolean(rule.enabled);
      box.setAttribute("aria-label", format(S.hlEnabledLabel, { name: rule.name }));
      box.addEventListener("change", () => run(() => store.update(rule.id, { enabled: box.checked })));
      const select = colorSelect(null, rule.color);
      select.setAttribute("aria-label", format(S.hlColorLabel, { name: rule.name }));
      select.addEventListener("change", () => run(() => store.update(rule.id, { color: select.value })));
      const up = button("hl-up", S.hlUp);
      up.disabled = index === 0;
      up.setAttribute("aria-label", format(S.hlUpLabel, { name: rule.name }));
      up.addEventListener("click", () => run(() => store.move(rule.id, -1)));
      const down = button("hl-down", S.hlDown);
      down.disabled = index === rules.length - 1;
      down.setAttribute("aria-label", format(S.hlDownLabel, { name: rule.name }));
      down.addEventListener("click", () => run(() => store.move(rule.id, 1)));
      const remove = button("hl-delete", S.hlDelete);
      remove.setAttribute("aria-label", format(S.hlDeleteLabel, { name: rule.name }));
      remove.addEventListener("click", () => run(() => store.remove(rule.id)));
      item.append(box, swatch(rule.color), element("span", "hl-name", rule.name),
        element("span", "hl-desc", describeCondition(rule.cond)), select, up, down, remove);
      list.appendChild(item);
    });
  }

  toggle.addEventListener("click", () => {
    panel.hidden = !panel.hidden;
    toggle.setAttribute("aria-expanded", panel.hidden ? "false" : "true");
    if (!panel.hidden) {
      setStatus("", false);
      newColor.value = nextColor(store.rules());
      renderList();
      if (getCase()) {
        store.load().catch((error) => setStatus(errorText(error), true));
      }
    }
  });

  // -- footer help: the shortcuts of this module (n/N, t/T, b/B, c are taken; see highlightNavKey)
  const help = element("div", "shortcuts-help", S.hlShortcutsHelp);
  if (options.footer && options.footer.parentNode) {
    options.footer.parentNode.insertBefore(help, options.footer.nextSibling);
  }

  // -- detail section
  const detail = element("section", "detail-highlight");
  detail.setAttribute("aria-label", S.hlDetailTitle);
  detail.hidden = true;
  if (options.detailAnchor && options.detailAnchor.parentNode) {
    options.detailAnchor.parentNode.insertBefore(detail, options.detailAnchor.nextSibling);
  }
  let entry = null;

  function renderDetail() {
    detail.textContent = "";
    if (!entry || !getCase()) {
      detail.hidden = true;
      return;
    }
    detail.hidden = false;
    const mark = store.get(entry.id);
    if (mark) {
      const by = element("div", "hl-by");
      by.append(swatch(mark.color), " ", format(S.hlBy, { name: mark.name }));
      detail.appendChild(by);
    } else if (!store.known(entry.id)) {
      store.ensure([entry.id]);
    }
    const seen = new Set();
    for (const key of Array.isArray(entry.keys) ? entry.keys : []) {
      if (!key || typeof key.key_type !== "string" || typeof key.value !== "string" || seen.size >= MAX_KEY_CHIPS) {
        continue;
      }
      const id = key.key_type + "\u0000" + key.value;
      if (seen.has(id)) {
        continue;
      }
      seen.add(id);
      const chip = button("hl-chip", format(S.hlKeyChip, { type: key.key_type, value: key.value }));
      chip.addEventListener("click", () => {
        if (full()) {
          showMessage(format(S.hlLimit, { max: MAX_RULES }), true);
          return;
        }
        store.highlightKey(key.key_type, key.value).then(
          () => showMessage(format(S.hlKeyDone, { type: key.key_type, value: key.value }), false),
          (error) => showMessage(errorText(error), true),
        );
      });
      detail.appendChild(chip);
    }
    if (entry.tpl_id > 0) {
      const chip = button("hl-chip", S.hlTemplateChip);
      chip.addEventListener("click", () => {
        const code = options.getTemplateCode ? options.getTemplateCode(entry.tpl_id) : "";
        store.create({
          name: code || String(entry.tpl_id), color: nextColor(store.rules()), cond: { tpl: [entry.tpl_id] }, enabled: true,
        }).then(() => showMessage("", false), (error) => showMessage(errorText(error), true));
      });
      detail.appendChild(chip);
    }
  }

  // -- ]/[ navigation: next/previous record of the view that any enabled rule highlights
  let navSeq = 0;
  document.addEventListener("keydown", async (event) => {
    const dir = highlightNavKey(event);
    if (!dir || isTypingTarget(event.target) || !getCase()) {
      return;
    }
    event.preventDefault();
    const row = getSelectedRow();
    if (!row) {
      showMessage(S.navNoSelection, false);
      return;
    }
    if (store.active().length === 0) {
      showMessage(S.hlNavNoRules, false);
      return;
    }
    navSeq += 1;
    const seq = navSeq;
    const forCase = getCase();
    try {
      const found = await store.next(getFilter(), row, dir);
      if (seq !== navSeq || forCase !== getCase()) {
        return;
      }
      if (!found.row) {
        showMessage(S.hlNavNone, false);
        return;
      }
      showMessage(found.rule ? format(S.hlNavFound, { name: found.rule.name }) : "", false);
      await reveal(found.row);
    } catch (error) {
      if (seq === navSeq) {
        showMessage(errorText(error), true);
      }
    }
  });

  return {
    // The store changed ("rules" or "marks"): repaint what depends on it.
    storeChanged() {
      if (!panel.hidden) {
        renderList();
      }
      renderDetail();
    },
    showEntry(next) {
      entry = next || null;
      renderDetail();
    },
  };
}
