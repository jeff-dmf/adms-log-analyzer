// The fake DOM of fake-dom.mjs with the one thing it gets wrong for code that walks children: in a browser,
// `node.children` is an HTMLCollection (indexable, iterable, `length`, no map/filter/find/forEach/indexOf). Here EVERY
// element hands out such a collection, so a panel that calls an array method on it fails in the test as it would in a page.
import { El, installGlobalDom } from "./fake-dom.mjs";

class Collection {
  constructor(items) {
    items.forEach((item, i) => {
      this[i] = item;
    });
    Object.defineProperty(this, "length", { value: items.length });
    Object.defineProperty(this, "items", { value: items.slice() });
  }

  [Symbol.iterator]() {
    return this.items[Symbol.iterator]();
  }

  item(i) {
    return this.items[i] ?? null;
  }
}

const optionsOf = (node) => node.kids.flatMap((c) => (c.tagName === "OPTION" ? [c] : c.tagName === "OPTGROUP" ? c.kids.filter((o) => o.tagName === "OPTION") : []));

export class StrictEl extends El {
  constructor(doc, tag) {
    super(doc, tag);
    if (this.tagName === "SELECT") {
      // A select only holds the value of one of its options: setting any other value selects nothing (value ""), and a
      // select that was never set shows its first option, as in a browser. Rebuilding the options forgets the choice.
      let chosen;
      Object.defineProperty(this, "value", {
        configurable: true,
        get() {
          const all = optionsOf(this);
          if (chosen === undefined) {
            return all.length > 0 ? all[0].value : "";
          }
          return all.some((o) => o.value === chosen) ? chosen : "";
        },
        set(v) {
          chosen = String(v);
        },
      });
      Object.defineProperty(this, "forgetChoice", { value() { chosen = undefined; } });
    }
    Object.defineProperty(this, "kids", { value: [], writable: true, configurable: true, enumerable: false });
    Object.defineProperty(this, "children", {
      configurable: true,
      get() { return new Collection(this.kids); },
      set(list) { this.kids = Array.from(list); },
    });
  }

  get textContent() { return this._text + this.kids.map((c) => c.textContent).join(""); }
  set textContent(v) { this.kids = []; this._text = String(v); if (this.forgetChoice) this.forgetChoice(); }
  appendChild(c) { c.remove(); c.parent = this; this.kids.push(c); return c; }
  insertBefore(c, ref) {
    c.remove();
    c.parent = this;
    const i = ref ? this.kids.indexOf(ref) : -1;
    if (i < 0) this.kids.push(c); else this.kids.splice(i, 0, c);
    return c;
  }
  remove() {
    if (this.parent) this.parent.kids = this.parent.kids.filter((c) => c !== this);
    this.parent = null;
    if (this.doc.activeElement === this) this.doc.activeElement = this.doc.body;
  }
  contains(t) { return t === this || this.kids.some((c) => c.contains(t)); }
  all() { return [this, ...this.kids.flatMap((c) => c.all())]; }
  replaceChildren(...cs) { this.kids = []; this._text = ""; for (const c of cs) this.appendChild(c); }
  get nextSibling() { const i = this.parent ? this.parent.kids.indexOf(this) : -1; return i >= 0 ? this.parent.kids[i + 1] ?? null : null; }
  get previousElementSibling() { const i = this.parent ? this.parent.kids.indexOf(this) : -1; return i > 0 ? this.parent.kids[i - 1] : null; }
  get options() { return optionsOf(this); }
}

// installGlobalDom() whose elements are all StrictEl, and whose innerHTML (and friends) throw.
export function installStrictDom() {
  const doc = installGlobalDom();
  doc.createElement = (tag) => new StrictEl(doc, tag);
  doc.createTextNode = (text) => {
    const node = new StrictEl(doc, "#text");
    node._text = String(text);
    return node;
  };
  doc.body = new StrictEl(doc, "body");
  doc.activeElement = doc.body;
  for (const name of ["innerHTML", "outerHTML"]) {
    Object.defineProperty(El.prototype, name, {
      configurable: true,
      set() { throw new Error(name + " must not be used"); },
      get() { throw new Error(name + " must not be read"); },
    });
  }
  Object.defineProperty(El.prototype, "insertAdjacentHTML", { configurable: true, value() { throw new Error("insertAdjacentHTML must not be used"); } });
  globalThis.Element = El;
  return doc;
}
