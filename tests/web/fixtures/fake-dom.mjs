// A very small fake DOM for the UI-12 flow tests (dock, top bar): elements with attributes, children, hidden,
// dataset, focus and event dispatch with bubbling. Not a general implementation.
export class El {
  constructor(doc, tag) {
    this.doc = doc;
    this.ownerDocument = doc;
    this.tagName = String(tag).toUpperCase();
    this.attrs = {};
    this.dataset = {};
    this.children = [];
    this.parent = null;
    this.listeners = {};
    this.style = {};
    this.className = "";
    this.id = "";
    this.title = "";
    this._hidden = false;
    this.disabled = false;
    this._text = "";
  }
  get hidden() { return this._hidden; }
  set hidden(v) {
    const changed = Boolean(v) !== this._hidden;
    this._hidden = Boolean(v);
    if (changed) for (const o of this.doc.observers) if (o.target === this) o.callback([{ type: "attributes", attributeName: "hidden" }]);
  }
  set textContent(v) { this.children = []; this._text = String(v); }
  get textContent() { return this._text + this.children.map((c) => c.textContent).join(""); }
  get isConnected() { return this.parent === null ? this === this.doc.body : this.parent.isConnected; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  appendChild(c) { c.remove(); c.parent = this; this.children.push(c); return c; }
  append(...cs) { for (const c of cs) this.appendChild(c); }
  insertBefore(c, ref) {
    c.remove();
    c.parent = this;
    const i = ref ? this.children.indexOf(ref) : -1;
    if (i < 0) this.children.push(c); else this.children.splice(i, 0, c);
    return c;
  }
  remove() {
    if (this.parent) this.parent.children = this.parent.children.filter((c) => c !== this);
    this.parent = null;
    if (this.doc.activeElement === this) this.doc.activeElement = this.doc.body;
  }
  contains(t) { return t === this || this.children.some((c) => c.contains(t)); }
  all() { return [this, ...this.children.flatMap((c) => c.all())]; }
  querySelector(sel) {
    const tags = sel.split(",").map((t) => t.trim().toUpperCase());
    return this.all().find((n) => n !== this && tags.includes(n.tagName)) || null;
  }
  addEventListener(type, fn) { (this.listeners[type] ??= []).push(fn); }
  focus() {
    this.doc.activeElement = this;
    this.dispatch("focusin");
    for (const fn of this.doc.listeners.focusin ?? []) fn({ type: "focusin", target: this });
  }
  dispatch(type, init = {}) {
    const event = { type, target: this, key: "", detail: 0, ctrlKey: false, altKey: false, metaKey: false, prevented: false, ...init,
      preventDefault() { this.prevented = true; }, stopPropagation() {} };
    for (let n = this; n; n = n.parent) for (const fn of n.listeners[type] ?? []) fn(event);
    return event;
  }
  click() { return this.disabled ? null : this.dispatch("click", { detail: 0 }); }

  // ---- the rest of what the panel modules touch (used by installGlobalDom)
  get classList() {
    const owner = this;
    const names = () => owner.className.split(/\s+/).filter(Boolean);
    const write = (list) => { owner.className = list.join(" "); };
    return {
      add: (...c) => write([...new Set([...names(), ...c])]),
      remove: (...c) => write(names().filter((n) => !c.includes(n))),
      contains: (c) => names().includes(c),
      toggle: (c, force) => {
        const on = force === undefined ? !names().includes(c) : Boolean(force);
        write(on ? [...new Set([...names(), c])] : names().filter((n) => n !== c));
        return on;
      },
    };
  }
  removeAttribute(k) { delete this.attrs[k]; }
  removeEventListener(type, fn) { this.listeners[type] = (this.listeners[type] ?? []).filter((f) => f !== fn); }
  dispatchEvent(event) { return this.dispatch(event.type, event); }
  replaceChildren(...cs) { this.children = []; this._text = ""; for (const c of cs) this.appendChild(typeof c === "string" ? Object.assign(new El(this.doc, "#text"), { _text: c }) : c); }
  insertAdjacentElement(where, el) {
    if (where === "afterend" && this.parent) {
      const at = this.parent.children.indexOf(this);
      this.parent.insertBefore(el, this.parent.children[at + 1] ?? null);
    }
    return el;
  }
  closest(sel) { for (let n = this; n; n = n.parent) if (n.matches?.(sel)) return n; return null; }
  matches(sel) {
    const parts = sel.split(",").map((t) => t.trim());
    return parts.some((p) => (p.startsWith(".") ? this.className.split(/\s+/).includes(p.slice(1)) : p.startsWith("#") ? this.id === p.slice(1) : this.tagName === p.toUpperCase()));
  }
  querySelectorAll(sel) { return this.all().filter((n) => n !== this && n.matches(sel)); }
  getBoundingClientRect() { return { top: 0, left: 0, right: 0, bottom: 0, width: 0, height: 0 }; }
  get offsetWidth() { return 0; }
  get clientWidth() { return 0; }
  get clientHeight() { return 0; }
  get options() { return this.children.filter((c) => c.tagName === "OPTION"); }
  get selectedOptions() { return this.options.filter((o) => o.value === this.value); }
  scrollIntoView() {}
  select() {}
  get parentNode() { return this.parent; }
  get nextSibling() { const i = this.parent ? this.parent.children.indexOf(this) : -1; return i >= 0 ? this.parent.children[i + 1] ?? null : null; }
  get previousElementSibling() { const i = this.parent ? this.parent.children.indexOf(this) : -1; return i > 0 ? this.parent.children[i - 1] : null; }
}

// Makes a page-like environment of globals for the real panel modules: document, window, observers, events.
export function installGlobalDom() {
  const doc = makeDoc();
  doc.createTextNode = (text) => { const n = new El(doc, "#text"); n._text = String(text); return n; };
  doc.documentElement = new El(doc, "html");
  doc.head = new El(doc, "head");
  doc.querySelector = (sel) => doc.body.all().find((n) => n.matches(sel)) ?? null;
  doc.querySelectorAll = (sel) => doc.body.all().filter((n) => n.matches(sel));
  doc.getElementById = (id) => doc.body.all().find((n) => n.id === id) ?? null;
  const win = { MutationObserver: doc.defaultView.MutationObserver, innerWidth: 1400, innerHeight: 900, localStorage: null, matchMedia: () => ({ matches: false, addEventListener() {}, removeEventListener() {} }), addEventListener() {}, removeEventListener() {}, dispatchEvent() {} };
  doc.defaultView = win;
  globalThis.document = doc;
  globalThis.window = win;
  globalThis.devicePixelRatio = 1;
  globalThis.matchMedia = win.matchMedia;
  win.devicePixelRatio = 1;
  globalThis.MutationObserver = class { observe() {} disconnect() {} };
  globalThis.ResizeObserver = class { observe() {} disconnect() {} };
  Object.defineProperty(globalThis, "navigator", { value: { clipboard: null, platform: "Linux" }, configurable: true });
  return doc;
}

export function makeDoc() {
  const doc = { listeners: {}, observers: [], defaultView: null };
  doc.defaultView = {
    MutationObserver: class {
      constructor(callback) { this.callback = callback; }
      observe(target) { doc.observers.push({ target, callback: this.callback }); }
    },
  };
  doc.createElement = (tag) => new El(doc, tag);
  doc.body = new El(doc, "body");
  doc.activeElement = doc.body;
  doc.addEventListener = (t, fn) => (doc.listeners[t] ??= []).push(fn);
  doc.removeEventListener = (t, fn) => (doc.listeners[t] = (doc.listeners[t] ?? []).filter((f) => f !== fn));
  return doc;
}

export const tick = async () => { for (let i = 0; i < 8; i += 1) await Promise.resolve(); };
export const menuEls = (doc) => doc.body.children.filter((c) => c.className === "ui-menu");
export const menuItems = (menu) => menu.children.filter((c) => c.attrs.role !== "separator");

// Waits for a condition that depends on a dynamic import (the loader of an action), up to 3 s.
export async function until(check, what = "condition") {
  const end = Date.now() + 3000;
  while (!check()) {
    if (Date.now() > end) throw new Error("timed out waiting for " + what);
    await new Promise((resolve) => setTimeout(resolve, 2));
  }
}
