// UI-12: the theme is System, Light or Dark; the choice persists in localStorage (guarded) and System follows the OS.
import test from "node:test";
import assert from "node:assert/strict";

// theme.js subscribes to the OS setting once per page, so the tests share one media query object.
const listeners = [];
const query = { matches: false, addEventListener: (type, fn) => listeners.push(fn) };

function install({ stored = null, dark = false, throwing = false } = {}) {
  const store = new Map(stored === null ? [] : [["dmslog.theme", stored]]);
  query.matches = dark;
  globalThis.window = {
    localStorage: {
      getItem: (k) => { if (throwing) throw new Error("blocked"); return store.has(k) ? store.get(k) : null; },
      setItem: (k, v) => { if (throwing) throw new Error("blocked"); store.set(k, v); },
      removeItem: (k) => { if (throwing) throw new Error("blocked"); store.delete(k); },
    },
    matchMedia: () => query,
  };
  globalThis.document = { documentElement: { dataset: {} } };
  return { store, query, listeners };
}

const theme = await import("../../src/dmslog/web/js/theme.js");

test("resolvePreference: only light and dark are explicit choices", () => {
  assert.equal(theme.resolvePreference("light"), "light");
  assert.equal(theme.resolvePreference("dark"), "dark");
  assert.equal(theme.resolvePreference("system"), "system");
  assert.equal(theme.resolvePreference(null), "system");
  assert.equal(theme.resolvePreference("neon"), "system");
  assert.deepEqual(theme.THEME_CHOICES, ["system", "light", "dark"]);
});

test("System follows the OS, Light and Dark win over it, and the choice persists", () => {
  const env = install({ dark: true });
  assert.equal(theme.themePreference(), "system");
  assert.equal(theme.applyTheme(), "dark");
  assert.equal(theme.setThemePreference("light"), "light");
  assert.equal(env.store.get("dmslog.theme"), "light");
  assert.equal(document.documentElement.dataset.theme, "light");
  assert.equal(theme.themePreference(), "light");
  assert.equal(theme.setThemePreference("dark"), "dark");
  assert.equal(env.store.get("dmslog.theme"), "dark");
  assert.equal(theme.setThemePreference("system"), "dark"); // the OS is dark
  assert.equal(env.store.has("dmslog.theme"), false); // System is stored as "no choice"
  assert.equal(theme.themePreference(), "system");
});

test("a stored choice is applied again on the next opening", () => {
  install({ stored: "light", dark: true });
  assert.equal(theme.themePreference(), "light");
  assert.equal(theme.applyTheme(), "light");
});

test("blocked storage never throws: the theme still changes for the session", () => {
  install({ throwing: true, dark: false });
  assert.equal(theme.themePreference(), "system");
  assert.equal(theme.setThemePreference("dark"), "light"); // not persisted: applied from storage and the OS
  assert.equal(theme.currentTheme(), "light");
});

test("while System is chosen, a change of the OS setting repaints the page", () => {
  const env = install({ dark: false });
  theme.applyTheme();
  assert.equal(document.documentElement.dataset.theme, "light");
  env.query.matches = true;
  for (const fn of env.listeners) fn();
  assert.equal(document.documentElement.dataset.theme, "dark");
  theme.setThemePreference("light");
  env.query.matches = false;
  env.query.matches = true;
  for (const fn of env.listeners) fn();
  assert.equal(document.documentElement.dataset.theme, "light"); // an explicit choice is not overridden
});
