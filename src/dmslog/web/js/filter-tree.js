// Visual editor of the filter tree: nestable AND / OR / NOT groups with condition rows,
// live validation (same limits as the backend), a live match count, undo/redo and saved filters.
// The tree is rebuilt with DOM calls and textContent only; nothing from logs is parsed as HTML.
//
//   const tree = initFilterTree(container, {api, caseId, onChange, initial});
//   tree.getFilter()  -> current valid filter JSON or null
//   tree.setFilter(json) / tree.setCase(id) / tree.destroy()
//
// Extension point for templates/events: registerLeafType() (filter-tree-model.js) for the
// data and registerLeafEditor() here for the row UI.
import {
  GROUP_OPS, History, MAX_DEPTH, MAX_NODES, MAX_TEXT, addChild, buildFilter, changeLeafType, duplicateNode,
  findNode, findParent, fromJson, leafTypeNames, makeGroup, makeLeaf, moveNode, removeNode, setGroupOp,
  ungroup, updateLeaf, wrapNode,
} from "./filter-tree-model.js";
import { initPresets } from "./presets.js";
import { initSavedViews } from "./saved-views.js";
import { S, format } from "./strings.js";
import { formatTs, parseTs } from "./time.js";

const COUNT_DELAY_MS = 400;
const COALESCE_MS = 1000;

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

function button(label, className, onClick, ariaLabel) {
  const b = el("button", className, label);
  b.type = "button";
  if (ariaLabel) {
    b.setAttribute("aria-label", ariaLabel);
  }
  b.addEventListener("click", onClick);
  return b;
}

let uid = 0;
function labelled(labelText, control, className) {
  uid += 1;
  const id = "ft-ctl-" + uid;
  control.id = id;
  const wrap = el("span", className || "ft-field");
  const label = el("label", "", labelText);
  label.htmlFor = id;
  wrap.append(label, control);
  return wrap;
}

function option(value, text, selected) {
  const o = el("option", "", text);
  o.value = String(value);
  o.selected = Boolean(selected);
  return o;
}

const OP_LABEL = () => ({ and: S.ftOpAnd, or: S.ftOpOr, not: S.ftOpNot });
const OP_SHORT = () => ({ and: S.ftOpShortAnd, or: S.ftOpShortOr, not: S.ftOpShortNot });
const TYPE_LABEL = () => ({
  source: S.ftTypeSource, sev: S.ftTypeSev, level: S.ftTypeLevel, ts: S.ftTypeTs, text: S.ftTypeText,
  raw: S.ftTypeRaw, payload: S.ftTypePayload,
});
const ERROR_TEXT = () => ({
  emptyList: S.ftErrorEmptyList, listTooLong: S.ftErrorListTooLong, badValue: S.ftErrorBadValue,
  emptyRange: S.ftErrorEmptyRange, badRange: S.ftErrorBadRange, badTime: S.ftErrorBadTime,
  emptyText: S.ftErrorEmptyText, textTooLong: S.ftErrorTextTooLong,
});

// ---- leaf editors ----------------------------------------------------------------------------
// editor: {label?: () => string, render(leaf, ctx) -> element}; ctx = {update(patch, key), sources}

const LEAF_EDITORS = {};

export function registerLeafEditor(type, editor) {
  LEAF_EDITORS[type] = editor;
}

function orderedEditor(namesKey, count) {
  return {
    render(leaf, ctx) {
      const wrap = el("span", "ft-fields");
      const op = el("select");
      op.append(
        option("le", S.ftOrWorse, leaf.data.op === "le"),
        option("ge", S.ftOrBetter, leaf.data.op === "ge"),
        option("eq", S.ftExactly, leaf.data.op === "eq"),
      );
      op.addEventListener("change", () => ctx.update({ op: op.value }, "op"));
      const value = el("select");
      value.append(option("", "", leaf.data.value === null));
      for (let i = 1; i <= count; i += 1) {
        value.append(option(i, S[namesKey][i], leaf.data.value === i));
      }
      value.addEventListener("change", () => ctx.update({ value: value.value === "" ? null : Number(value.value) }, "value"));
      wrap.append(labelled(S.ftValue, value), labelled(S.ftCompare, op));
      return wrap;
    },
  };
}

registerLeafEditor("sev", orderedEditor("severityNames", 5));
registerLeafEditor("level", orderedEditor("levelNames", 8));

registerLeafEditor("text", {
  render(leaf, ctx) {
    const input = el("input");
    input.type = "text";
    input.autocomplete = "off";
    input.maxLength = MAX_TEXT;
    input.placeholder = S.ftTextPlaceholder;
    input.value = leaf.data.fts;
    input.addEventListener("input", () => ctx.update({ fts: input.value }, "fts"));
    return labelled(S.ftTextLabel, input, "ft-fields ft-grow");
  },
});

registerLeafEditor("ts", {
  render(leaf, ctx) {
    const wrap = el("span", "ft-fields");
    const text = (key, ms) => (leaf.data[key + "Text"] !== undefined ? leaf.data[key + "Text"] : ms === null || ms === undefined ? "" : formatTs(ms));
    const make = (key, label) => {
      const input = el("input");
      input.type = "text";
      input.autocomplete = "off";
      input.placeholder = S.filterTimePlaceholder;
      input.value = text(key, leaf.data[key]);
      input.addEventListener("input", () => {
        const next = { [key + "Text"]: input.value };
        const other = key === "from" ? "to" : "from";
        const raw = input.value.trim();
        const parsed = raw === "" ? null : parseTs(raw);
        next[key] = parsed;
        const otherRaw = String(text(other, leaf.data[other])).trim();
        next.invalid = (raw !== "" && parsed === null) || (otherRaw !== "" && parseTs(otherRaw) === null);
        ctx.update(next, key);
      });
      return labelled(label, input);
    };
    wrap.append(make("from", S.ftFrom), make("to", S.ftTo));
    return wrap;
  },
});

registerLeafEditor("source", {
  render(leaf, ctx) {
    const known = ctx.sources();
    if (known.length === 0) {
      const input = el("input");
      input.type = "text";
      input.autocomplete = "off";
      input.placeholder = S.ftSourceIdsPlaceholder;
      input.value = leaf.data.ids.join(", ");
      input.addEventListener("input", () => {
        const parts = input.value.split(",").map((p) => p.trim()).filter((p) => p !== "");
        const ids = parts.map((p) => (/^\d{1,15}$/.test(p) ? Number(p) : NaN));
        ctx.update({ ids: ids.some(Number.isNaN) ? [-1] : ids }, "ids"); // [-1] fails validation
      });
      return labelled(S.ftSourceIdsLabel, input, "ft-fields ft-grow");
    }
    const select = el("select");
    select.multiple = true;
    select.size = Math.min(6, Math.max(3, known.length));
    const chosen = new Set(leaf.data.ids);
    const listed = new Set(known.map((s) => s.id));
    for (const s of known) {
      select.append(option(s.id, format(S.ftSourceOption, { name: s.name, id: s.id }), chosen.has(s.id)));
    }
    for (const id of leaf.data.ids) {
      if (!listed.has(id)) {
        select.append(option(id, format(S.ftSourceOption, { name: "?", id }), true));
      }
    }
    select.addEventListener("change", () => {
      ctx.update({ ids: Array.from(select.selectedOptions, (o) => Number(o.value)) }, "ids");
    });
    return labelled(S.ftSources, select, "ft-fields ft-grow");
  },
});

const PAYLOAD_ROLES = ["request", "response", "fault", "delta", "content", "publication", "other"];
const PAYLOAD_FORMATS = ["xml", "xml_seq", "html", "json", "text"];

registerLeafEditor("payload", {
  render(leaf, ctx) {
    const wrap = el("span", "ft-fields ft-grow");
    const multi = (labelText, values, key) => {
      const select = el("select");
      select.multiple = true;
      select.size = Math.min(values.length, 5);
      const chosen = new Set(leaf.data[key]);
      for (const value of values) {
        select.append(option(value, value, chosen.has(value)));
      }
      select.addEventListener("change", () => {
        ctx.update({ [key]: Array.from(select.selectedOptions, (o) => o.value) }, key);
      });
      return labelled(labelText, select);
    };
    const orphan = el("select");
    orphan.append(
      option("", S.ftPayloadOrphanAny, leaf.data.orphan === null),
      option("yes", S.ftPayloadOrphanYes, leaf.data.orphan === true),
      option("no", S.ftPayloadOrphanNo, leaf.data.orphan === false),
    );
    orphan.addEventListener("change", () => {
      ctx.update({ orphan: orphan.value === "" ? null : orphan.value === "yes" }, "orphan");
    });
    wrap.append(
      multi(S.ftPayloadRoles, PAYLOAD_ROLES, "roles"),
      multi(S.ftPayloadFormats, PAYLOAD_FORMATS, "fmt"),
      labelled(S.ftPayloadOrphan, orphan),
    );
    return wrap;
  },
});

registerLeafEditor("raw", {
  render(leaf) {
    const wrap = el("span", "ft-fields ft-grow");
    wrap.append(el("span", "ft-raw-label", S.ftRawLabel));
    const code = el("code", "ft-raw", JSON.stringify(leaf.data.json).slice(0, 200));
    wrap.append(code);
    return wrap;
  },
});

function editorFor(type) {
  return (
    LEAF_EDITORS[type] || {
      render: (leaf) => el("code", "ft-raw", JSON.stringify(leaf.data).slice(0, 200)),
    }
  );
}

// ---- the editor ------------------------------------------------------------------------------

export function initFilterTree(container, { api, caseId, onChange, initial, getExtraFilter, onSavedApplied }) {
  let currentCase = caseId;
  let history = new History(fromJson(initial));
  let sources = [];
  let sourcesController = null;
  let countController = null;
  let countTimer = null;
  let lastEdit = { key: "", at: 0 };
  let errorEls = new Map();
  let focusRequest = null;
  let savedViews = null;
  let presetsPanel = null;

  container.classList.add("filter-tree");
  const heading = el("h3", "ft-title", S.ftTitle);
  const bar = el("div", "ft-bar");
  const undoButton = button(S.ftUndo, "", () => replaceRoot(history.undo(), null));
  const redoButton = button(S.ftRedo, "", () => replaceRoot(history.redo(), null));
  const clearButton = button(S.ftClear, "", () => commit(fromJson(null), null));
  const applyButton = button(S.ftApply, "ft-apply", () => apply());
  bar.append(undoButton, redoButton, clearButton, applyButton);
  const status = el("div", "ft-status");
  status.setAttribute("role", "status");
  status.setAttribute("aria-live", "polite");
  const info = el("span", "ft-info");
  const countLine = el("span", "ft-count");
  status.append(info, countLine);
  const treeHost = el("div", "ft-tree");
  treeHost.setAttribute("role", "group");
  treeHost.setAttribute("aria-label", S.ftTreeLabel);
  const viewsHost = el("div");
  const presetsHost = el("div");
  container.append(heading, presetsHost, bar, status, treeHost, viewsHost);

  const root = () => history.present;
  const ctxFor = (id) => ({
    sources: () => sources,
    update: (patch, key) => edit(id, patch, key),
  });

  function commit(next, focus) {
    history.push(next);
    focusRequest = focus;
    render();
  }

  function replaceRoot(next, focus) {
    history.present = next;
    focusRequest = focus;
    lastEdit = { key: "", at: 0 };
    render();
  }

  // Typing in a field: no re-render (keeps the caret); consecutive edits of one field merge.
  function edit(id, patch, key) {
    const next = updateLeaf(root(), id, patch);
    const stamp = id + ":" + key;
    const now = Date.now();
    if (lastEdit.key === stamp && now - lastEdit.at < COALESCE_MS) {
      history.replace(next);
    } else {
      history.push(next);
    }
    lastEdit = { key: stamp, at: now };
    refresh();
  }

  function apply() {
    const built = buildFilter(root());
    if (built.errors.length > 0 || built.limit) {
      return;
    }
    if (onChange) {
      onChange(built.filter);
    }
    countLine.textContent = "";
    scheduleCount(0);
  }

  // ---- rendering ----

  function groupActions(group, isRoot) {
    const actions = el("span", "ft-actions");
    const typeSelect = el("select");
    const names = TYPE_LABEL();
    for (const t of leafTypeNames()) {
      typeSelect.append(option(t, (LEAF_EDITORS[t] && LEAF_EDITORS[t].label ? LEAF_EDITORS[t].label() : names[t]) || t));
    }
    const typeField = labelled(S.ftConditionType, typeSelect, "ft-field ft-type-pick");
    const add = button(S.ftAddCondition, "", () => {
      const leaf = makeLeaf(typeSelect.value);
      commit(addChild(root(), group.id, leaf), { id: leaf.id });
    });
    const addGroup = button(S.ftAddGroup, "", () => {
      const sub = makeGroup("and");
      commit(addChild(root(), group.id, sub), { id: sub.id });
    });
    actions.append(typeField, add, addGroup);
    if (!isRoot) {
      actions.append(...nodeButtons(group, true));
    }
    return actions;
  }

  function nodeButtons(node, isGroup) {
    const out = [
      button(S.ftDuplicate, "", () => {
        const before = findParent(root(), node.id);
        const next = duplicateNode(root(), node.id);
        commit(next, { id: before ? before.id : node.id, add: true });
      }),
      button(S.ftWrapNot, "", () => commit(wrapNode(root(), node.id, "not"), { id: node.id })),
    ];
    if (!isGroup) {
      out.push(button(S.ftWrapAnd, "", () => commit(wrapNode(root(), node.id, "and"), { id: node.id })));
    } else {
      out.push(button(S.ftUngroup, "", () => {
        const parent = findParent(root(), node.id);
        commit(ungroup(root(), node.id), { id: parent ? parent.id : null, add: true });
      }));
    }
    out.push(
      button(S.ftMoveUp, "", () => commit(moveNode(root(), node.id, -1), { id: node.id })),
      button(S.ftMoveDown, "", () => commit(moveNode(root(), node.id, 1), { id: node.id })),
      button(S.ftRemove, "ft-remove", () => {
        const parent = findParent(root(), node.id);
        commit(removeNode(root(), node.id), { id: parent ? parent.id : null, add: true });
      }),
    );
    return out;
  }

  function renderGroup(group, isRoot) {
    const box = el("div", "ft-group ft-op-" + group.op);
    box.dataset.nodeId = group.id;
    box.setAttribute("role", "group");
    box.setAttribute("aria-label", format(S.ftGroupLabel, { op: OP_SHORT()[group.op] }));
    const head = el("div", "ft-head");
    const select = el("select", "ft-op");
    for (const op of GROUP_OPS) {
      select.append(option(op, OP_LABEL()[op], op === group.op));
    }
    select.addEventListener("change", () => commit(setGroupOp(root(), group.id, select.value), { id: group.id }));
    select.dataset.first = "1";
    head.append(labelled(S.ftOperator, select, "ft-field ft-op-pick"), groupActions(group, isRoot));
    box.append(head);
    const body = el("div", "ft-children");
    if (group.children.length === 0 && isRoot) {
      body.append(el("p", "ft-empty", S.ftEmpty));
    }
    for (const child of group.children) {
      body.append(child.kind === "group" ? renderGroup(child, false) : renderLeaf(child));
    }
    box.append(body);
    return box;
  }

  function renderLeaf(leaf) {
    const row = el("div", "ft-leaf");
    row.dataset.nodeId = leaf.id;
    const typeSelect = el("select", "ft-type");
    typeSelect.dataset.first = "1";
    const names = TYPE_LABEL();
    const types = leaf.type === "raw" ? ["raw"] : leafTypeNames();
    for (const t of types) {
      typeSelect.append(option(t, (LEAF_EDITORS[t] && LEAF_EDITORS[t].label ? LEAF_EDITORS[t].label() : names[t]) || t, t === leaf.type));
    }
    typeSelect.disabled = leaf.type === "raw";
    typeSelect.addEventListener("change", () => commit(changeLeafType(root(), leaf.id, typeSelect.value), { id: leaf.id }));
    row.append(labelled(S.ftConditionType, typeSelect, "ft-field ft-type-pick"));
    row.append(editorFor(leaf.type).render(leaf, ctxFor(leaf.id)));
    const actions = el("span", "ft-actions");
    actions.append(...nodeButtons(leaf, false));
    row.append(actions);
    const error = el("span", "ft-error");
    error.setAttribute("role", "alert");
    errorEls.set(leaf.id, error);
    row.append(error);
    return row;
  }

  function render() {
    errorEls = new Map();
    treeHost.replaceChildren(renderGroup(root(), true));
    applyFocus();
    refresh();
  }

  function applyFocus() {
    const request = focusRequest;
    focusRequest = null;
    if (!request) {
      return;
    }
    let node = request.id ? findNode(root(), request.id) : null;
    if (!node) {
      node = root();
    }
    let target = null;
    const host = treeHost.querySelector('[data-node-id="' + node.id + '"]');
    if (host) {
      target = request.add && node.kind === "group" ? host.querySelector(".ft-actions button") : host.querySelector("[data-first]");
    }
    if (target) {
      target.focus();
    }
  }

  // Validation, button states and the (debounced) count. Does not rebuild the tree.
  function refresh() {
    const built = buildFilter(root());
    for (const el_ of errorEls.values()) {
      el_.textContent = "";
    }
    const texts = ERROR_TEXT();
    for (const err of built.errors) {
      const slot = errorEls.get(err.id);
      if (slot) {
        slot.textContent = texts[err.code] || S.ftErrorBadValue;
      }
    }
    const invalid = built.errors.length > 0 || built.limit !== null;
    applyButton.disabled = invalid;
    undoButton.disabled = !history.canUndo;
    redoButton.disabled = !history.canRedo;
    if (built.errors.length > 0) {
      info.textContent = S.ftFixErrors;
    } else if (built.limit === "depth") {
      info.textContent = format(S.ftLimitDepth, { limit: MAX_DEPTH });
    } else if (built.limit === "nodes") {
      info.textContent = format(S.ftLimitNodes, { limit: MAX_NODES, nodes: built.nodes });
    } else if (built.filter === null) {
      info.textContent = S.ftNoConditions;
    } else {
      info.textContent = format(S.ftNodesInfo, { nodes: built.nodes, depth: built.depth });
    }
    info.classList.toggle("ft-bad", invalid);
    scheduleCount(COUNT_DELAY_MS);
  }

  function scheduleCount(delay) {
    if (countTimer !== null) {
      clearTimeout(countTimer);
    }
    if (countController) {
      countController.abort();
      countController = null;
    }
    const built = buildFilter(root());
    if (built.errors.length > 0 || built.limit !== null || !currentCase) {
      countLine.textContent = "";
      return;
    }
    countTimer = setTimeout(async () => {
      countTimer = null;
      countController = new AbortController();
      const signal = countController.signal;
      countLine.textContent = S.ftCountLoading;
      try {
        const result = await api.count(currentCase, built.filter, signal);
        countLine.textContent = format(result.exact ? S.gridCount : S.gridCountApprox, {
          count: Number(result.count).toLocaleString(),
        });
      } catch (err) {
        if (err && err.name === "AbortError") {
          return;
        }
        countLine.textContent = S.ftCountFailed + ": " + err.message;
      }
    }, delay);
  }

  async function loadSources() {
    if (sourcesController) {
      sourcesController.abort();
    }
    sources = [];
    if (!currentCase) {
      return;
    }
    sourcesController = new AbortController();
    try {
      const data = await api.sources(currentCase);
      sources = (data.sources || []).map((s) => ({ id: s.id, name: s.name }));
      if (sources.length > 0) {
        render();
      }
    } catch (err) {
      sources = [];
    }
  }

  const onKeydown = (event) => {
    if (!(event.ctrlKey || event.metaKey) || event.altKey) {
      return;
    }
    const tag = event.target && event.target.tagName;
    if (tag === "INPUT" || tag === "TEXTAREA") {
      return; // the browser undoes text typing itself
    }
    const key = event.key.toLowerCase();
    if (key === "z" && !event.shiftKey && history.canUndo) {
      event.preventDefault();
      replaceRoot(history.undo(), null);
    } else if ((key === "y" || (key === "z" && event.shiftKey)) && history.canRedo) {
      event.preventDefault();
      replaceRoot(history.redo(), null);
    }
  };
  container.addEventListener("keydown", onKeydown);

  savedViews = initSavedViews(viewsHost, {
    api,
    caseId: currentCase,
    getFilter: () => buildFilter(root()).filter,
    getExtraFilter,
    onApply: (filter) => {
      if (onSavedApplied) {
        onSavedApplied(); // clears the toolbar filters: a saved filter replaces the whole view
      }
      commit(fromJson(filter), null);
      apply();
    },
  });

  // Preset shortcuts of the case's adapters; a preset only fills the tree when the user picks it.
  presetsPanel = initPresets(presetsHost, {
    api,
    caseId: currentCase,
    getFilter: () => buildFilter(root()).filter,
    onApply: (filter) => {
      if (onSavedApplied) {
        onSavedApplied();
      }
      commit(fromJson(filter), null);
      apply();
    },
  });

  render();
  loadSources();

  return {
    getFilter: () => buildFilter(root()).filter,
    setFilter(json) {
      history = new History(fromJson(json));
      focusRequest = null;
      render();
    },
    setCase(next) {
      currentCase = next;
      savedViews.setCase(next);
      presetsPanel.setCase(next);
      loadSources();
      refresh();
    },
    destroy() {
      if (countTimer !== null) {
        clearTimeout(countTimer);
      }
      for (const c of [countController, sourcesController]) {
        if (c) {
          c.abort();
        }
      }
      container.removeEventListener("keydown", onKeydown);
      savedViews.destroy();
      presetsPanel.destroy();
      container.replaceChildren();
    },
  };
}
