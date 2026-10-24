// Pure model of the sources tree (no DOM): the folder structure of the ERS (the member path of every source), the
// rows that are visible for a set of open folders and a filter, the check state of a folder, and the keys. The DOM
// layer is source-tree.js. A folder checks every source under it; the selection is the same set the flat list uses.
import { isNotIndexed } from "./case-files-model.js";
import { buildNotIndexed } from "./file-viewer-model.js";
import { compareInstance, hasPool, processLabel } from "./process-label.js";
import { fold, matchRange } from "./source-filter-model.js";
import { S, format, pluralKey } from "./strings.js";

export const STORAGE_PREFIX = "dmslog.sourceTree."; // + case id, in sessionStorage: the open folders of that case
export const VIEW_KEY = "dmslog.sourcesView"; // "list" | "tree", in localStorage

// "A/B\\C.log" -> ["A", "B", "C.log"]; empty parts and "." are dropped. A source with no member path uses its name.
export function pathParts(source) {
  const text = source && source.member ? String(source.member) : source && source.name ? String(source.name) : "";
  const parts = text.split(/[\\/]+/).filter((part) => part !== "" && part !== ".");
  return parts.length > 0 ? parts : [String(source ? source.id : "?")];
}

const compare = (a, b) => {
  const left = fold(a.name);
  const right = fold(b.name);
  return left < right ? -1 : left > right ? 1 : a.name < b.name ? -1 : a.name > b.name ? 1 : 0;
};

// "DMS_FunctionHost (28 instances)": the keys poolFolderOne/poolFolderOther are created in strings.js; the defaults keep
// the text readable until then.
function poolFolderName(pool, count) {
  const key = pluralKey("poolFolder", count);
  const fallback = count === 1 ? "{pool} ({count} instance)" : "{pool} ({count} instances)";
  return format(S[key] || fallback, { pool, count: count.toLocaleString("en-US") });
}

function folderNode(key, name) {
  return { key, name, folder: true, children: [], ids: [], files: 0, records: 0, unreadable: 0, sensitive: 0 };
}

// {roots: [node]}. Node: {key, name, folder, children, ids (every source id under it), files, records, unreadable,
// sensitive, source (files only)}. Several collections (ers_group): one top folder per collection, as the grid's C<n>.
// A folder that holds only one folder is merged with it ("SchneiderElectricData/ADMS") so a deep path costs one row.
export function buildTree(sources) {
  // Sources the indexer skipped (not selected, not indexable, over the limit) are never checkable: they go to one
  // "Not indexed" group at the end (file-viewer-model.js), closed by default.
  const notIndexed = buildNotIndexed(sources);
  sources = sources.filter((s) => !isNotIndexed(s));
  const groups = new Set(sources.map((s) => s.collection).filter((g) => g !== null && g !== undefined));
  const byCollection = groups.size > 1;
  const top = new Map(); // collection -> folder node
  const root = folderNode("", "");
  const lookup = new Map([["", root]]);
  const folderOf = (parent, key, name) => {
    let node = lookup.get(key);
    if (!node) {
      node = folderNode(key, name);
      lookup.set(key, node);
      parent.children.push(node);
    }
    return node;
  };
  for (const source of sources) {
    let parent = root;
    let prefix = "";
    if (byCollection) {
      const group = source.collection === null || source.collection === undefined ? "-" : String(source.collection);
      prefix = "C" + group + "|";
      if (!top.has(group)) {
        top.set(group, folderOf(root, prefix, "C" + group));
      }
      parent = top.get(group);
    }
    const parts = pathParts(source);
    for (let i = 0; i < parts.length - 1; i += 1) {
      prefix += parts[i] + "/";
      parent = folderOf(parent, prefix, parts[i]);
    }
    let poolFolder = null;
    if (hasPool(source)) {
      // The instances of a pool (the FunctionHost) sit in one virtual folder inside the folder they are in. The key
      // holds a NUL, which a member name never has, so it cannot collide with a real folder.
      poolFolder = folderOf(parent, prefix + "\u0000pool:" + source.pool + "/", source.pool);
      poolFolder.pool = source.pool;
      poolFolder.instances = (poolFolder.instances || 0) + 1;
      poolFolder.name = poolFolderName(source.pool, poolFolder.instances);
      parent = poolFolder;
    }
    const leaf = {
      key: prefix + parts[parts.length - 1] + "#" + source.id,
      name: parts[parts.length - 1],
      label: poolFolder ? processLabel(source) + ", " + parts[parts.length - 1] : null,
      folder: false,
      children: [],
      ids: [source.id],
      files: 1,
      records: Number.isFinite(source.records) && source.records > 0 ? source.records : 0,
      unreadable: source.status === "error" ? 1 : 0,
      sensitive: source.sensitive ? 1 : 0,
      source,
    };
    parent.children.push(leaf);
  }
  const finish = (node) => {
    if (node.pool) {
      node.children.sort((a, b) => compareInstance(a.source, b.source) || compare(a, b));
    } else {
      node.children.sort((a, b) => (a.folder === b.folder ? compare(a, b) : a.folder ? -1 : 1));
    }
    for (const child of node.children) {
      if (child.folder) {
        finish(child);
      }
      node.ids.push(...child.ids);
      node.files += child.files;
      node.records += child.records;
      node.unreadable += child.unreadable;
      node.sensitive += child.sensitive;
    }
  };
  finish(root);
  const merge = (node) => {
    // A pool folder is never merged into its parent (nor another folder into it): it is a group, not a path.
    while (node.folder && !node.pool && !node.notIndexed && node.children.length === 1 && node.children[0].folder && !node.children[0].pool) {
      const only = node.children[0];
      node.name = node.name === "" ? only.name : node.name + "/" + only.name;
      node.key = only.key;
      node.children = only.children;
    }
    node.children.forEach(merge);
  };
  root.children.forEach(merge);
  if (notIndexed !== null) {
    root.children.push(notIndexed);
  }
  return { roots: root.children, ids: root.ids };
}

// The folders open when nothing was chosen yet: the top ones, so the first level is in view.
export function defaultOpen(tree) {
  return new Set(tree.roots.filter((node) => node.folder && !node.notIndexed).map((node) => node.key));
}

// Keys of the folders that hold a source whose path matches the filter (they open by themselves while it is typed).
// Match: case-insensitive substring of the whole path, so "adms" finds a folder as well as a file.
export function matchedIds(tree, query, sources) {
  const needle = String(query).trim();
  if (needle === "") {
    return null;
  }
  const ids = new Set();
  for (const source of sources) {
    if (matchRange(pathParts(source).join("/"), needle) !== null) {
      ids.add(source.id);
    }
  }
  return ids;
}

// A folder is open when it is in the set or when a folder inside it is (that one was reached by opening this one). So a
// folder merged into a chain ("A/B") that later splits ("A" and "A/B") keeps both open.
export function impliedOpen(open) {
  const all = new Set(open);
  for (const key of open) {
    const bar = key.indexOf("|"); // "C1|A/B/": the collection's own folder is an ancestor too
    if (bar >= 0 && bar < key.length - 1) {
      all.add(key.slice(0, bar + 1));
    }
    for (let at = key.indexOf("/"); at >= 0 && at < key.length - 1; at = key.indexOf("/", at + 1)) {
      all.add(key.slice(0, at + 1));
    }
  }
  return all;
}

// The set after a folder is closed: it and everything inside it go (a folder inside a closed one is not open).
export function closed(open, key) {
  return new Set([...open].filter((k) => !k.startsWith(key)));
}

// Rows on screen: [{node, level, open, setSize, posInSet, matched}]. With a filter only the paths to a match are listed,
// all open. `open`: Set of folder keys.
export function visibleRows(tree, openKeys, matched) {
  const open = impliedOpen(openKeys);
  const rows = [];
  const keep = (node) => matched === null || (node.notIndexed ? node.allIds : node.ids).some((id) => matched.has(id));
  const walk = (nodes, level) => {
    const shown = nodes.filter(keep);
    shown.forEach((node, index) => {
      const isOpen = node.folder && (matched !== null || open.has(node.key));
      rows.push({ node, level, open: isOpen, setSize: shown.length, posInSet: index + 1 });
      if (isOpen) {
        walk(node.children, level + 1);
      }
    });
  };
  walk(tree.roots, 1);
  return rows;
}

// The ids a click on a node acts on: everything under it, or only what the filter shows.
export function idsOf(node, matched) {
  if (node.notIndexed) {
    return []; // a source that was not indexed has no records to select
  }
  return matched === null ? node.ids : node.ids.filter((id) => matched.has(id));
}

// "all" | "some" | "none" for the ids of a node against the selection.
export function checkState(ids, selected) {
  let hit = 0;
  for (const id of ids) {
    if (selected.has(id)) {
      hit += 1;
    }
  }
  return hit === 0 ? "none" : hit === ids.length ? "all" : "some";
}

// The selection after a click on a node: all checked -> unchecks them; none or some -> checks them all.
export function toggled(selected, ids) {
  const next = new Set(selected);
  if (checkState(ids, selected) === "all") {
    ids.forEach((id) => next.delete(id));
  } else {
    ids.forEach((id) => next.add(id));
  }
  return next;
}

// Arrow keys of the tree (WAI-ARIA tree pattern). Returns {focus: index} | {open: key} | {close: key} | null.
export function treeKeyTarget(rows, index, key) {
  const row = rows[index];
  if (!row) {
    return null;
  }
  if (key === "ArrowDown") {
    return index + 1 < rows.length ? { focus: index + 1 } : null;
  }
  if (key === "ArrowUp") {
    return index > 0 ? { focus: index - 1 } : null;
  }
  if (key === "Home") {
    return { focus: 0 };
  }
  if (key === "End") {
    return { focus: rows.length - 1 };
  }
  if (key === "ArrowRight") {
    if (!row.node.folder) {
      return null;
    }
    return row.open ? (rows[index + 1] && rows[index + 1].level > row.level ? { focus: index + 1 } : null) : { open: row.node.key };
  }
  if (key === "ArrowLeft") {
    if (row.node.folder && row.open) {
      return { close: row.node.key };
    }
    for (let i = index - 1; i >= 0; i -= 1) {
      if (rows[i].level < row.level) {
        return { focus: i };
      }
    }
    return null;
  }
  return null;
}

export function parseOpen(text) {
  try {
    const value = JSON.parse(text);
    return Array.isArray(value) ? new Set(value.filter((k) => typeof k === "string")) : null;
  } catch (err) {
    return null;
  }
}

export function serializeOpen(open) {
  return JSON.stringify([...open].slice(0, 2000));
}
