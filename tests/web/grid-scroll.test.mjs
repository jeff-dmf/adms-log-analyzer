// UI-27: scrolling the grid forces no layout. The wheel only adds up and the rows move once per animation frame;
// syncScroll writes scrollTop without reading it back; the pool is sized from the height the ResizeObserver reported.
// Geometry reads are counted with getters on a fake viewport (the fake DOM has no layout, so a read is a getter call).
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { El, makeDoc } from "./fixtures/fake-dom.mjs";

const doc = makeDoc();
doc.createTextNode = (text) => Object.assign(new El(doc, "#text"), { _text: String(text) });
globalThis.document = doc;
const observers = [];
globalThis.ResizeObserver = class {
  constructor(callback) { this.callback = callback; this.disconnected = false; observers.push(this); }
  observe() {}
  disconnect() { this.disconnected = true; }
};

const { Grid } = await import("../../src/dmslog/web/js/grid.js");
const { wheelTravel } = await import("../../src/dmslog/web/js/grid.js");
const { ROW_HEIGHT, SCROLL_SPAN } = await import("../../src/dmslog/web/js/grid-model.js");

const GEOMETRY = ["clientHeight", "clientWidth", "offsetHeight", "offsetWidth", "scrollTop", "scrollLeft", "scrollHeight", "scrollWidth"];

// A viewport whose geometry reads are counted; writes to scrollTop/scrollLeft are not reads.
function makeViewport(clientHeight = 10 * ROW_HEIGHT) {
  const el = new El(doc, "div");
  el.id = "grid-viewport";
  const reads = { total: 0 };
  const values = { clientHeight, scrollTop: 0, scrollLeft: 0, scrollWidth: 2000, clientWidth: 500, offsetWidth: 515 };
  for (const name of GEOMETRY) {
    Object.defineProperty(el, name, {
      configurable: true,
      get() { reads.total += 1; reads[name] = (reads[name] || 0) + 1; return values[name] ?? 0; },
      set(v) { values[name] = v; },
    });
  }
  el.getBoundingClientRect = () => { reads.total += 1; return { height: values.clientHeight }; };
  el.scrolled = [];
  el.scrollBy = (o) => { el.scrolled.push(o.left); };
  return { el, reads, values };
}

function makeRows(n) {
  return Array.from({ length: n }, (_, i) => ({ id: i + 1, ts: 1000 + i * 10, sev: 1, level: 3, pid: 1, tid: 1, source_id: 1, message: "m" + i }));
}

function setup({ height = 10 * ROW_HEIGHT, rows = 300, observer = true } = {}) {
  const vp = makeViewport(height);
  const frames = [];
  const cancelled = [];
  const grid = new Grid({
    viewport: vp.el, rowsLayer: new El(doc, "div"), spacer: new El(doc, "div"), header: new El(doc, "div"),
    onSelect() {}, onCount() {}, onPosition() {}, onError(err) { throw err; },
    requestFrame: (fn) => { frames.push(fn); return frames.length; },
    cancelFrame: (handle) => { cancelled.push(handle); frames[handle - 1] = null; },
  });
  grid.pump = () => {};
  grid.caseId = "A";
  grid.range = { min: 1000, max: 1000 + rows * 10 };
  grid.window.load(makeRows(rows), null, null, 0);
  grid.window.atStart = true;
  grid.window.atEnd = true;
  const watcher = observer ? observers.at(-1) : null;
  const report = (h) => watcher.callback([{ contentRect: { height: h } }]);
  if (observer) {
    report(height);
  }
  grid.render();
  vp.el.dispatch("scroll"); // the echo of the writes made while the grid came up: the next event is a fresh one
  const runFrames = () => {
    const pending = frames.splice(0).filter(Boolean);
    pending.forEach((fn) => fn());
    return pending.length;
  };
  return { grid, vp, frames, cancelled, report, runFrames, watcher };
}

const wheel = (vp, deltaY, extra = {}) => vp.el.dispatch("wheel", { deltaY, deltaX: 0, deltaMode: 0, shiftKey: false, ...extra });

test("120 wheel events cost one render, in one frame, with the deltas added up", () => {
  const { grid, vp, frames, runFrames } = setup();
  let renders = 0;
  const render = grid.render.bind(grid);
  grid.render = () => { renders += 1; render(); };
  for (let i = 0; i < 120; i += 1) {
    const event = wheel(vp, ROW_HEIGHT);
    assert.equal(event.prevented, true);
  }
  assert.equal(renders, 0, "nothing renders before the frame");
  assert.equal(grid.window.top, 0);
  assert.equal(frames.length, 1, "one frame requested for the whole burst");
  assert.equal(runFrames(), 1);
  assert.equal(grid.window.top, 120);
  assert.equal(renders, 1);
  assert.equal(grid.wheelFrame, null, "the next burst asks for a new frame");
  wheel(vp, ROW_HEIGHT);
  assert.equal(frames.length, 1);
});

test("fractions add up across events and frames (touchpad deltas)", () => {
  const { grid, vp, runFrames } = setup();
  for (let i = 0; i < 4; i += 1) {
    wheel(vp, ROW_HEIGHT / 3); // 1.33 rows in total
  }
  runFrames();
  assert.equal(grid.window.top, 1);
  wheel(vp, ROW_HEIGHT * 0.6);
  runFrames();
  assert.equal(grid.window.top, 1, "0.93 of a row does not move (rows are whole, the fraction waits)");
  wheel(vp, ROW_HEIGHT * 0.1);
  runFrames();
  assert.equal(grid.window.top, 2);
  wheel(vp, ROW_HEIGHT * 2 / 3);
  wheel(vp, -ROW_HEIGHT * 2 / 3);
  runFrames();
  assert.equal(grid.window.top, 2, "opposite deltas in one frame cancel");
});

test("line-mode wheel keeps the 3 rows per line and opposite deltas cancel inside a frame", () => {
  const { grid, vp, runFrames } = setup();
  wheel(vp, 2, { deltaMode: 1 });
  runFrames();
  assert.equal(grid.window.top, 6);
  wheel(vp, 5 * ROW_HEIGHT);
  wheel(vp, -5 * ROW_HEIGHT);
  runFrames();
  assert.equal(grid.window.top, 6);
});

test("deltas past either end stop at the end and the start, and the rows still move the other way at once", () => {
  const { grid, vp, runFrames } = setup({ rows: 100 });
  wheel(vp, 100000 * ROW_HEIGHT);
  runFrames();
  assert.equal(grid.window.top, grid.window.maxTop(grid.visible));
  wheel(vp, -100000 * ROW_HEIGHT);
  runFrames();
  assert.equal(grid.window.top, 0);
});

test("horizontal wheel is added up and moves body and header once per frame, with writes only", () => {
  const { grid, vp, runFrames } = setup();
  const before = vp.reads.total;
  wheel(vp, 0, { deltaX: 30 });
  wheel(vp, 0, { deltaY: 5, deltaX: 20, shiftKey: true });
  assert.equal(vp.values.scrollLeft, 0);
  runFrames();
  assert.equal(vp.values.scrollLeft, 50);
  assert.equal(grid.header.scrollLeft, 50);
  wheel(vp, ROW_HEIGHT);
  runFrames();
  assert.equal(vp.values.scrollLeft, 50, "the horizontal travel is spent: a later frame does not repeat it");
  assert.equal(grid.window.top, 1);
  assert.equal(vp.reads.total, before);
});

test("direction reversal at an edge keeps the later delta (vertical, both ends, and horizontal)", () => {
  const { grid, vp, runFrames } = setup({ rows: 100 });
  wheel(vp, -10 * ROW_HEIGHT);
  wheel(vp, ROW_HEIGHT);
  runFrames();
  assert.equal(grid.window.top, 1, "at the top: the excess of -10 is dropped, +1 follows");
  const max = grid.window.maxTop(grid.visible);
  grid.window.top = max;
  wheel(vp, 10 * ROW_HEIGHT);
  wheel(vp, -ROW_HEIGHT);
  runFrames();
  assert.equal(grid.window.top, max - 1, "at the end");
  wheel(vp, 0, { deltaX: -100 });
  wheel(vp, 0, { deltaX: 40 });
  runFrames();
  assert.equal(vp.values.scrollLeft, 40, "horizontal, at the left edge");
  wheel(vp, 0, { deltaX: 99999 });
  wheel(vp, 0, { deltaX: -10 });
  runFrames();
  assert.equal(vp.values.scrollLeft, 1500 - 10, "horizontal, at the right edge (scrollWidth - clientWidth)");
});

test("wheel units are normalised on both axes: pixel, line and page", () => {
  const ctx = { visible: 11, pageWidth: 500 };
  const y = (deltaMode, deltaY) => wheelTravel({ deltaMode, deltaY, deltaX: 0, shiftKey: false }, ctx);
  assert.deepEqual(y(0, ROW_HEIGHT), { axis: "y", amount: 1 });
  assert.deepEqual(y(1, 1), { axis: "y", amount: 3 });
  assert.deepEqual(y(2, 1), { axis: "y", amount: 10 }, "a page is the visible rows less one");
  assert.equal(wheelTravel({ deltaMode: 2, deltaY: 1, deltaX: 0 }, { visible: 1 }).amount, 1);
  const x = (deltaMode, deltaX) => wheelTravel({ deltaMode, deltaX, deltaY: 0, shiftKey: false }, ctx);
  assert.deepEqual(x(0, 30), { axis: "x", amount: 30 });
  assert.deepEqual(x(1, 1), { axis: "x", amount: 3 * ROW_HEIGHT });
  assert.deepEqual(x(2, 1), { axis: "x", amount: 500 });
  assert.deepEqual(wheelTravel({ deltaMode: 1, deltaX: 0, deltaY: 2, shiftKey: true }, ctx), { axis: "x", amount: 6 * ROW_HEIGHT });
  const { grid, vp, runFrames } = setup();
  wheel(vp, 1, { deltaMode: 2 });
  runFrames();
  assert.equal(grid.window.top, grid.visible - 1);
});

test("a wheel burst, its frames and the scroll sync read no layout; a scroll event reads at most one position", () => {
  const { grid, vp, runFrames } = setup();
  const before = vp.reads.total;
  let inListener = 0;
  const scrollEvent = () => {
    const at = vp.reads.total;
    vp.el.dispatch("scroll");
    const used = vp.reads.total - at;
    assert.ok(used <= 1, "a scroll event of our own write reads at most one position, read " + used);
    inListener += used;
  };
  for (let i = 0; i < 120; i += 1) {
    wheel(vp, ROW_HEIGHT);
    if (i % 30 === 29) {
      runFrames();
      scrollEvent();
    }
  }
  runFrames();
  wheel(vp, 0, { deltaX: 40 });
  runFrames();
  scrollEvent();
  grid.scrollRows(-7);
  grid.syncScroll();
  grid.render();
  scrollEvent();
  assert.equal(vp.reads.total - before, inListener, "every read happened inside a scroll event of the grid: " + JSON.stringify(vp.reads));
  assert.ok(inListener > 0 && inListener <= 7);
  assert.equal(vp.values.scrollTop, Math.round((grid.window.topRow().ts - 1000) / (grid.range.max - 1000) * SCROLL_SPAN));
  assert.equal(grid.pendingJump, null, "an echo is not a drag");
  assert.equal(grid.header.scrollLeft, 40);
});

test("syncScroll writes the target and remembers it: the scroll event it causes is not a drag", () => {
  const { grid, vp } = setup();
  grid.window.top = 150;
  grid.syncScroll();
  const target = vp.values.scrollTop;
  assert.ok(target > 0);
  assert.equal(grid.lastScroll, target);
  vp.el.dispatch("scroll");
  assert.equal(grid.pendingJump, null, "the programmatic scroll does not start a jump");
  vp.values.scrollTop = target + 1; // sub-pixel snapping of the browser stays below the threshold
  vp.el.dispatch("scroll");
  assert.equal(grid.pendingJump, null);
});

test("a scrollbar drag is still the user's: it starts the jump to that time", () => {
  const { grid, vp } = setup();
  grid.window.top = 150;
  grid.syncScroll();
  vp.el.dispatch("scroll"); // the echo of syncScroll()
  vp.values.scrollTop = grid.lastScroll + 500;
  vp.el.dispatch("scroll");
  assert.notEqual(grid.pendingJump, null);
  grid.destroy(); // clears the timer
  assert.equal(grid.pendingJump, null);
});

test("the pool is sized from the height the observer reported, not from the DOM", () => {
  const { grid, vp, report } = setup({ height: 10 * ROW_HEIGHT });
  assert.equal(grid.visible, 11);
  vp.values.clientHeight = 99999; // the DOM would say something else: it is not asked
  const before = vp.reads.total;
  grid.render();
  assert.equal(grid.visible, 11);
  assert.equal(vp.reads.total, before);
  report(20 * ROW_HEIGHT);
  assert.equal(grid.visible, 21, "a new report resizes the pool");
  assert.equal(grid.pool.length, 21);
});

test("before the observer reported, and while hidden, the height is read from the DOM once per render, then 400", () => {
  const { grid, vp, report } = setup({ observer: false });
  assert.equal(grid.visible, 11, "no observer: the DOM height");
  vp.values.clientHeight = 5 * ROW_HEIGHT;
  grid.render();
  assert.equal(grid.visible, 6, "without an observer the fallback stays current");
  vp.values.clientHeight = 0;
  grid.render();
  assert.equal(grid.visible, Math.ceil(400 / ROW_HEIGHT) + 1, "hidden viewport: 400 px");
  // With an observer that has not fired yet:
  const second = setup({ height: 0, observer: true });
  assert.equal(second.grid.viewHeight, 0);
  assert.equal(second.grid.visible, Math.ceil(400 / ROW_HEIGHT) + 1);
  second.vp.values.clientHeight = 7 * ROW_HEIGHT;
  second.grid.render();
  assert.equal(second.grid.visible, 8, "not reported yet: asks the DOM");
  second.report(9 * ROW_HEIGHT);
  assert.equal(second.grid.viewHeight, 9 * ROW_HEIGHT);
  second.report(0); // hidden again (the dock is maximized)
  assert.equal(second.grid.viewHeight, 0);
  assert.equal(second.grid.visible, 8, "hidden: falls back, never keeps a stale height");
  void report; void grid;
});

test("the viewport becoming visible again puts the scrollbar back where the rows are", () => {
  const { grid, vp, report } = setup();
  report(0);
  grid.window.top = 80;
  vp.values.scrollTop = 0; // a write while display:none did not stick
  report(10 * ROW_HEIGHT);
  assert.ok(vp.values.scrollTop > 0);
  assert.equal(grid.lastScroll, vp.values.scrollTop);
});

test("a case change or closing the case drops the wheel travel and cancels the frame", () => {
  const { grid, vp, frames, cancelled, runFrames } = setup();
  wheel(vp, 50 * ROW_HEIGHT);
  assert.equal(frames.length, 1);
  grid.closeCase();
  assert.deepEqual(cancelled, [1]);
  assert.equal(runFrames(), 0);
  assert.equal(grid.wheelRemainder, 0);
  assert.equal(grid.window.top, 0);

  const b = setup();
  wheel(b.vp, 50 * ROW_HEIGHT);
  b.grid.adopt({ caseId: "B", filter: null, range: b.grid.range, committed: true });
  assert.deepEqual(b.cancelled, [1]);
  assert.equal(b.runFrames(), 0);
  assert.equal(b.grid.window.top, 0);
  wheel(b.vp, 3 * ROW_HEIGHT);
  b.runFrames();
  assert.equal(b.grid.window.top, 3, "wheel works again for the new view");
});

test("destroy cancels the frame, disconnects the observer and ignores later events", () => {
  const { grid, vp, frames, cancelled, runFrames, watcher, report } = setup();
  wheel(vp, 10 * ROW_HEIGHT);
  let renders = 0;
  grid.render = () => { renders += 1; };
  grid.destroy();
  assert.deepEqual(cancelled, [1]);
  assert.equal(watcher.disconnected, true);
  assert.equal(runFrames(), 0);
  wheel(vp, 10 * ROW_HEIGHT);
  assert.equal(frames.length, 0, "no frame is requested after destroy");
  report(5 * ROW_HEIGHT); // a callback already queued by the browser
  assert.equal(renders, 0);
  assert.equal(grid.window.top, 0);
});

test("the default frame scheduler uses requestAnimationFrame / cancelAnimationFrame, or a timer", async () => {
  const calls = [];
  globalThis.requestAnimationFrame = (fn) => { calls.push(["raf", fn]); return 7; };
  globalThis.cancelAnimationFrame = (h) => { calls.push(["caf", h]); };
  try {
    const vp = makeViewport();
    const grid = new Grid({ viewport: vp.el, rowsLayer: new El(doc, "div"), spacer: new El(doc, "div"), header: new El(doc, "div"), onSelect() {}, onError(err) { throw err; } });
    grid.pump = () => {};
    grid.window.load(makeRows(100), null, null, 0);
    wheel(vp, 4 * ROW_HEIGHT);
    wheel(vp, 4 * ROW_HEIGHT);
    assert.equal(calls.filter((c) => c[0] === "raf").length, 1);
    calls[0][1]();
    assert.equal(grid.window.top, 8);
    wheel(vp, ROW_HEIGHT);
    grid.destroy();
    assert.deepEqual(calls.at(-1), ["caf", 7]);
  } finally {
    delete globalThis.requestAnimationFrame;
    delete globalThis.cancelAnimationFrame;
  }
  // Without them a timer stands in.
  const vp = makeViewport();
  const grid = new Grid({ viewport: vp.el, rowsLayer: new El(doc, "div"), spacer: new El(doc, "div"), header: new El(doc, "div"), onSelect() {}, onError(err) { throw err; } });
  grid.pump = () => {};
  grid.window.load(makeRows(100), null, null, 0);
  wheel(vp, 4 * ROW_HEIGHT);
  await new Promise((r) => setTimeout(r, 40));
  assert.equal(grid.window.top, 4);
});

// -- static guards ---------------------------------------------------------------------------------------------

const source = fs.readFileSync(new URL("../../src/dmslog/web/js/grid.js", import.meta.url), "utf8");

// The text of a method (or the wheel listener) from its header to the next two-space-indented method.
function method(name) {
  const start = source.indexOf("\n  " + name + "(");
  assert.ok(start >= 0, name);
  const next = source.slice(start + 1).search(/\n  [A-Za-z]+\([^)]*\) \{/);
  return source.slice(start, next < 0 ? undefined : start + 1 + next);
}

const GEOMETRY_READ = /clientHeight|clientWidth|clientTop|offsetWidth|offsetHeight|offsetTop|offsetLeft|getBoundingClientRect|scrollTop\b(?!\s*=[^=])|scrollLeft\s*\+?=|scrollHeight|scrollWidth|getComputedStyle|innerHeight|\.scrollLeft\b(?!\s*\+=)/;

test("the hot scroll and render paths read no geometry (static): only readHeight and the scrollbar input do", () => {
  for (const name of ["scrollRows", "moveRows", "syncScroll", "ensurePool", "render", "applyWheel", "cancelWheel", "createRow"]) {
    const body = method(name).replace(/\/\/.*$/gm, "");
    const cleaned = body.replace(/this\.viewport\.scrollTop = top;/, "").replace(/this\.viewport\.scrollLeft \+= x;/, "");
    assert.ok(!GEOMETRY_READ.test(cleaned), name + ": " + (GEOMETRY_READ.exec(cleaned) || [])[0]);
  }
  const bind = source.slice(source.indexOf("  bind() {"), source.indexOf("  readHeight()"));
  const wheelListener = bind.slice(bind.indexOf('addEventListener("wheel"'), bind.indexOf('addEventListener("scroll"')).replace(/\/\/.*$/gm, "");
  assert.ok(wheelListener.length > 200 && !GEOMETRY_READ.test(wheelListener) && !/scrollRows|moveRows|render\(|scrollLeft/.test(wheelListener));
  // The few reads that remain are named: the fallback in readHeight() and the scroll event of the scrollbar.
  const reads = source.split("\n").filter((l) => /clientHeight|\.scrollTop\b(?!\s*=)/.test(l) && !/^\s*\/\//.test(l));
  assert.equal(reads.length, 2, reads.join("\n"));
  assert.ok(/readHeight\(\) \{/.test(method("readHeight")) && /clientHeight/.test(method("readHeight")));
  const bar = method("onScrollBar") + method("followSideways");
  assert.ok(/scrollTop/.test(bar) && /scrollLeft/.test(bar), "the reads of the native position are in the scroll listener only");
  assert.ok(!/setTimeout|selfScroll|settle/.test(bar) && !/selfScroll|settleScroll|settleTimer/.test(source), "no timer, no ignore-the-next-event state");
});

test("syncScroll never reads scrollTop back (static)", () => {
  const body = method("syncScroll");
  assert.match(body, /this\.viewport\.scrollTop = top;\s*\n\s*this\.lastScroll = top;/);
  assert.ok(!/= this\.viewport\.scrollTop/.test(body));
});

test("the css contains the viewport and the rows, and gives the pooled rows no content-visibility", () => {
  const css = fs.readFileSync(new URL("../../src/dmslog/web/css/app.css", import.meta.url), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
  const rule = (selector) => {
    const m = new RegExp("\\n" + selector.replace(/[.#]/g, "\\$&") + " \\{([^}]*)\\}").exec(css);
    assert.ok(m, selector);
    return m[1];
  };
  assert.match(rule(".grid-viewport"), /contain:\s*strict;/);
  assert.match(rule(".grid-row"), /contain:\s*layout paint style;/);
  assert.ok(!/content-visibility/.test(rule(".grid-row")) && !/content-visibility/.test(rule(".grid-viewport")));
  // Nothing of the row is drawn outside its box: every selection/focus outline is inward.
  for (const m of css.matchAll(/\.grid-(?:viewport:focus-visible )?[^{}]*grid-row\.selected[^{}]*\{[^}]*outline-offset:\s*(-?\d+)px/g)) {
    assert.ok(Number(m[1]) < 0, m[0]);
  }
});

test("the resizer has no scroll listener and measures layout only from apply() and the observer (static)", () => {
  const src = fs.readFileSync(new URL("../../src/dmslog/web/js/column-resize.js", import.meta.url), "utf8");
  assert.ok(!/addEventListener\("scroll"/.test(src));
  const grid = fs.readFileSync(new URL("../../src/dmslog/web/js/grid.js", import.meta.url), "utf8");
  const calls = grid.split("\n").filter((l) => /resizer\.measureLayout\(\)/.test(l));
  assert.equal(calls.length, 1, "only the ResizeObserver callback");
});

// -- navigation and destroy ------------------------------------------------------------------------------------

const { api } = await import("../../src/dmslog/web/js/api.js");

function withApi(setupOptions) {
  const ctx = setup(setupOptions);
  const pages = [];
  api.page = (caseId, filter, cursor, dir, limit, signal) => new Promise((resolve, reject) => {
    pages.push({ cursor, dir, signal, resolve, reject });
  });
  api.count = async () => ({ count: 300, exact: true });
  return { ...ctx, pages };
}

test("Home, End, a time jump and navigation keys drop the pending wheel travel", async () => {
  for (const [start, expectTop] of [[(g) => g.goHome(), () => 0], [(g) => g.goEnd(), (g) => g.window.maxTop(g.visible)], [(g) => g.jumpToTime(1500), () => 0]]) {
    const { grid, vp, pages, runFrames } = withApi();
    wheel(vp, 10 * ROW_HEIGHT);
    const done = start(grid);
    assert.equal(grid.wheelRemainder, 0);
    assert.equal(runFrames(), 0, "the pending frame was cancelled");
    pages[0].resolve({ rows: makeRows(300), prev: null, next: null });
    await done;
    assert.equal(grid.window.top, expectTop(grid), "the frame of an older wheel does not move the new rows");
  }
  for (const key of ["ArrowDown", "PageDown", "Home", "End"]) {
    const { grid, vp, pages, runFrames } = withApi();
    wheel(vp, 10 * ROW_HEIGHT);
    vp.el.dispatch("keydown", { key });
    assert.equal(grid.wheelRemainder, 0, key);
    assert.equal(runFrames(), 0, key);
    pages.forEach((p) => p.resolve({ rows: makeRows(300), prev: null, next: null }));
  }
  const { grid, vp, runFrames } = withApi();
  wheel(vp, 10 * ROW_HEIGHT);
  grid.scrollRows(2); // a programmatic move
  assert.equal(runFrames(), 0);
  assert.equal(grid.window.top, 2);
});

test("the scrollbar jump (80 ms debounce) also drops the wheel travel", async () => {
  const { grid, vp, pages } = withApi();
  vp.values.scrollTop = 5000;
  vp.el.dispatch("scroll");
  wheel(vp, 10 * ROW_HEIGHT);
  await new Promise((r) => setTimeout(r, 120));
  assert.equal(pages.length, 1);
  assert.equal(grid.wheelRemainder, 0);
  grid.destroy();
});

test("destroy is idempotent and leaves no live work: listeners, requests, count, keys, fonts", async () => {
  const removed = [];
  document.fonts = { addEventListener() {}, removeEventListener: (type, fn) => removed.push([type, fn]) };
  const { grid, vp, pages } = withApi();
  let jumped = grid.goEnd();
  assert.equal(pages.length, 1);
  const signal = pages[0].signal;
  let renders = 0;
  const ensurePool = grid.ensurePool.bind(grid); // render() reaches it only when it does work
  grid.ensurePool = () => { renders += 1; ensurePool(); };
  const rowsBefore = grid.window.rows;
  grid.destroy();
  grid.destroy();
  assert.equal(signal.aborted, true, "the request in flight is aborted");
  assert.equal(grid.listeners.signal.aborted, true, "every listener is tied to a signal that was aborted");
  assert.equal(removed.length, 1);
  assert.equal(removed[0][0], "loadingdone");
  pages[0].resolve({ rows: makeRows(5), prev: null, next: null }); // a late answer
  assert.equal(await jumped, "superseded");
  assert.equal(grid.window.rows, rowsBefore);
  vp.el.dispatch("keydown", { key: "ArrowDown" });
  vp.el.dispatch("scroll");
  vp.el.dispatch("wheel", { deltaY: 100, deltaX: 0, deltaMode: 0 });
  grid.rowsLayer.dispatch("click", { target: { closest: () => null } });
  assert.equal(renders, 0, "no render after destroy");
  assert.equal(await grid.goHome(), "superseded");
  assert.equal(pages.length, 1, "no request after destroy");
  grid.render();
  grid.scrollRows(3);
  grid.pump();
  assert.equal(renders, 0);
  assert.equal(grid.resizer.destroyed, true);
  delete document.fonts;
});

test("destroy aborts the count in flight and ignores keys even for a host that ignores the listener signal", async () => {
  const { grid, vp } = withApi();
  let countSignal = null;
  api.count = (caseId, filter, signal) => new Promise(() => { countSignal = signal; });
  grid.refreshCount(true, null);
  assert.ok(countSignal && countSignal.aborted === false);
  grid.window.selected = 5;
  grid.destroy();
  assert.equal(countSignal.aborted, true);
  vp.el.dispatch("keydown", { key: "ArrowDown" });
  assert.equal(grid.window.selected, 5, "the key did nothing");
  api.count = async () => ({ count: 300, exact: true });
  assert.equal(await grid.refreshCount(true, null), undefined);
});

test("the resizer measures the horizontal range in apply() and stops after destroy()", () => {
  const { grid, vp } = setup();
  vp.values.scrollWidth = 3000;
  grid.resizer.apply();
  assert.equal(grid.resizer.maxScrollX, 2500);
  assert.equal(grid.resizer.pageWidth, 500);
  grid.resizer.destroy();
  vp.values.scrollWidth = 9000;
  const widths = grid.rowsLayer.style.minWidth;
  grid.resizer.setWidth("msg", 700, false); // would re-apply every width
  grid.resizer.measureLayout();
  assert.equal(grid.resizer.maxScrollX, 2500);
  assert.equal(grid.rowsLayer.style.minWidth, widths);
});


// -- native scroll, coalesced echoes, jumps, resizer destroy ---------------------------------------------------

test("a native sideways drag moves the header and the kept position; the next wheel continues from there", () => {
  const { grid, vp, runFrames } = setup();
  vp.values.scrollLeft = 600; // the user dragged the horizontal bar
  vp.el.dispatch("scroll");
  assert.equal(grid.header.scrollLeft, 600);
  assert.equal(grid.resizer.scrollX, 600);
  wheel(vp, 0, { deltaX: 30 });
  runFrames();
  assert.equal(vp.values.scrollLeft, 630);
  assert.equal(grid.header.scrollLeft, 630);
});

test("wheel travel gathered while a jump is on its way is dropped when the page arrives", async () => {
  const { grid, vp, pages, runFrames } = withApi();
  grid.window.top = 100;
  const done = grid.goEnd();
  wheel(vp, -10 * ROW_HEIGHT); // between the request and the answer
  pages[0].resolve({ rows: makeRows(300), prev: null, next: null });
  await done;
  runFrames();
  assert.equal(grid.window.top, grid.window.maxTop(grid.visible), "the old travel did not move the new rows");
  assert.equal(grid.wheelRemainder, 0);
});

test("the resizer takes no input after destroy: handles, keys, double click, fit, reset, setWidth", () => {
  const { grid } = setup();
  const resizer = grid.resizer;
  const handle = grid.header.children[0].children.find((c) => c.className === "col-resizer");
  assert.ok(handle, "the header has separators");
  let prepared = 0;
  resizer.prepareFit = () => { prepared += 1; return null; };
  resizer.setWidth("msg", 640, false);
  const widthBefore = resizer.model.get("msg");
  assert.equal(widthBefore, 640);
  resizer.destroy();
  resizer.destroy();
  assert.equal(resizer.listeners.signal.aborted, true);
  let captured = 0;
  handle.setPointerCapture = () => { captured += 1; };
  const events = [
    handle.dispatch("dblclick"), handle.dispatch("keydown", { key: "ArrowRight" }), handle.dispatch("keydown", { key: "Home" }),
    handle.dispatch("pointerdown", { button: 0, clientX: 10, pointerId: 1 }),
  ];
  assert.ok(events.every((e) => e.prevented === false), "no handler acts");
  assert.equal(captured, 0);
  resizer.fit("msg");
  resizer.setWidth("msg", 900, true);
  resizer.reset();
  resizer.adoptScrollLeft(300);
  assert.equal(prepared, 0);
  assert.equal(resizer.model.get("msg"), widthBefore);
  assert.equal(resizer.scrollX, 0);
});


test("a drag coalesced with our own write is adopted at once: the next wheel goes on from where the user left it", () => {
  const { grid, vp, runFrames } = setup();
  wheel(vp, 0, { deltaX: 100 });
  runFrames();
  assert.equal(vp.values.scrollLeft, 100);
  vp.values.scrollLeft = 600; // the user dragged on, in the same event as the write above
  vp.el.dispatch("scroll");
  assert.equal(grid.header.scrollLeft, 600);
  assert.equal(grid.resizer.scrollX, 600);
  wheel(vp, 0, { deltaX: 30 });
  runFrames();
  assert.equal(vp.values.scrollLeft, 630);
  assert.equal(grid.header.scrollLeft, 630);
});

test("sideways wheel travel gathered before a native drag is dropped, not added to the old position", () => {
  const { grid, vp, runFrames } = setup();
  wheel(vp, 0, { deltaX: 100 });
  vp.values.scrollLeft = 600;
  vp.el.dispatch("scroll");
  runFrames();
  assert.equal(vp.values.scrollLeft, 600);
  assert.equal(grid.header.scrollLeft, 600);
});

test("a vertical drag coalesced with our write is followed at once, wherever it ended", () => {
  const { grid, vp } = setup();
  grid.window.top = 150;
  grid.syncScroll();
  const written = grid.lastScroll;
  vp.values.scrollTop = written + 500;
  vp.el.dispatch("scroll");
  assert.notEqual(grid.pendingJump, null);
  assert.equal(grid.lastScroll, written + 500);
  grid.destroy();
});

test("Home right after a coalesced drag runs alone: the drag's own jump is dropped, not fired later", async () => {
  const { grid, vp, pages } = withApi();
  grid.window.top = 150;
  grid.syncScroll();
  vp.values.scrollTop = grid.lastScroll + 500;
  vp.el.dispatch("scroll");
  assert.notEqual(grid.pendingJump, null);
  const home = grid.goHome();
  assert.equal(grid.pendingJump, null);
  await new Promise((r) => setTimeout(r, 160));
  assert.equal(pages.length, 1, "one request: Home, not Home aborted by the drag's jump");
  assert.equal(pages[0].signal.aborted, false);
  pages[0].resolve({ rows: makeRows(300), prev: null, next: null });
  assert.equal(await home, "ok");
});

test("every listener of a separator is registered with the abort signal, and the dblclick is gone after destroy", () => {
  const { grid } = setup();
  const resizer = grid.resizer;
  const registered = [];
  const target = new EventTarget();
  const add = target.addEventListener.bind(target);
  target.addEventListener = (type, fn, options) => { registered.push([type, options && options.signal]); add(type, fn, options); };
  target.classList = { add() {}, remove() {} };
  target.setPointerCapture = () => {};
  let fits = 0;
  resizer.prepareFit = () => { fits += 1; return null; };
  resizer.bindHandle(target, "msg");
  assert.deepEqual(registered.map((r) => r[0]).sort(), ["click", "dblclick", "keydown", "pointerdown"]);
  assert.ok(registered.every((r) => r[1] === resizer.listeners.signal), "each one has the signal");
  target.dispatchEvent(new Event("dblclick"));
  assert.equal(fits, 1, "it works before destroy");
  resizer.destroy();
  target.dispatchEvent(new Event("dblclick"));
  assert.equal(fits, 1, "removed by the signal, not only ignored");
});

test("sideways travel continues from the last write, and an echo of it keeps pending travel", () => {
  const { grid, vp, runFrames } = setup();
  wheel(vp, 0, { deltaX: 100 });
  runFrames();
  wheel(vp, 0, { deltaX: 30 });
  runFrames();
  assert.equal(vp.values.scrollLeft, 130);
  wheel(vp, 0, { deltaX: 20 }); // pending
  vp.el.dispatch("scroll"); // the echo of the write at 130 (within the tolerance): not the user
  vp.values.scrollLeft = 131;
  vp.el.dispatch("scroll");
  runFrames();
  assert.equal(vp.values.scrollLeft, 150);
  assert.equal(grid.header.scrollLeft, 150);
});

test("which axis an event reads follows our own write: top after a vertical write, left after a sideways one", () => {
  const { grid, vp, runFrames } = setup();
  let at = { ...vp.reads };
  const delta = (name) => (vp.reads[name] || 0) - (at[name] || 0);
  grid.window.top = 150;
  grid.syncScroll();
  at = { ...vp.reads };
  vp.el.dispatch("scroll");
  assert.deepEqual([delta("scrollTop"), delta("scrollLeft")], [1, 0]);
  wheel(vp, 0, { deltaX: 40 });
  runFrames();
  at = { ...vp.reads };
  vp.el.dispatch("scroll");
  assert.deepEqual([delta("scrollTop"), delta("scrollLeft")], [0, 1]);
  at = { ...vp.reads };
  vp.el.dispatch("scroll"); // not ours: the vertical bar is where we left it, so the sideways one is checked too
  assert.deepEqual([delta("scrollTop"), delta("scrollLeft")], [1, 1]);
});

test("a stale own-write flag never hides a vertical drag: the position is compared at every event", () => {
  const { grid, vp } = setup();
  grid.window.top = 150;
  grid.syncScroll(); // no event is delivered for this write
  vp.values.scrollTop = grid.lastScroll + 800;
  vp.el.dispatch("scroll");
  assert.notEqual(grid.pendingJump, null);
  grid.destroy();
});

test("Home answers before the drag's debounce, End starts: the old drag timer cannot abort End", async () => {
  const { grid, vp, pages } = withApi();
  grid.window.top = 150;
  grid.syncScroll();
  const home = grid.goHome();
  assert.equal(pages.length, 1);
  vp.el.dispatch("scroll"); // flush the own-write flag
  vp.values.scrollTop = grid.lastScroll + 700; // the user drags while Home loads
  vp.el.dispatch("scroll");
  assert.notEqual(grid.pendingJump, null, "the debounce is running");
  pages[0].resolve({ rows: makeRows(300), prev: null, next: null }); // Home answers before 80 ms
  assert.equal(await home, "ok");
  assert.notEqual(grid.pendingJump, null, "the timer's reference survives the answer");
  const end = grid.goEnd(); // End starts: it owns the navigation now
  assert.equal(grid.pendingJump, null, "and End cancelled it");
  await new Promise((r) => setTimeout(r, 160));
  assert.equal(pages.length, 2, "Home and End only: the drag's jump never ran");
  assert.equal(pages[1].signal.aborted, false, "End is not aborted");
  pages[1].resolve({ rows: makeRows(300), prev: null, next: null });
  assert.equal(await end, "ok");
});
