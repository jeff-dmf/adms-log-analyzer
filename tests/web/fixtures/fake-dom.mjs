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
    this.hidden = false;
    this.disabled = false;
    this._text = "";
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
  focus() { this.doc.activeElement = this; }
  dispatch(type, init = {}) {
    const event = { type, target: this, key: "", detail: 0, ctrlKey: false, altKey: false, metaKey: false, prevented: false, ...init,
      preventDefault() { this.prevented = true; }, stopPropagation() {} };
    for (let n = this; n; n = n.parent) for (const fn of n.listeners[type] ?? []) fn(event);
    return event;
  }
  click() { return this.disabled ? null : this.dispatch("click", { detail: 0 }); }
}

export function makeDoc() {
  const doc = { listeners: {}, defaultView: null };
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
