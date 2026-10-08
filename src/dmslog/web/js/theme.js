// Light/dark theme: follows prefers-color-scheme until the user toggles; the choice is kept
// in localStorage (every access guarded: it can throw or be empty).
const KEY = "dmslog.theme";

export function resolveTheme(stored, prefersDark) {
  if (stored === "light" || stored === "dark") {
    return stored;
  }
  return prefersDark ? "dark" : "light";
}

function readStored() {
  try {
    return window.localStorage.getItem(KEY);
  } catch (err) {
    return null;
  }
}

function writeStored(value) {
  try {
    window.localStorage.setItem(KEY, value);
  } catch (err) {
    // Storage unavailable: the choice just does not persist.
  }
}

function prefersDark() {
  return typeof window.matchMedia === "function" && window.matchMedia("(prefers-color-scheme: dark)").matches;
}

export function currentTheme() {
  return document.documentElement.dataset.theme || resolveTheme(readStored(), prefersDark());
}

export function applyTheme() {
  const theme = resolveTheme(readStored(), prefersDark());
  document.documentElement.dataset.theme = theme;
  return theme;
}

export function toggleTheme() {
  const next = currentTheme() === "dark" ? "light" : "dark";
  writeStored(next);
  document.documentElement.dataset.theme = next;
  return next;
}
