// Theme: System, Light or Dark. "System" follows prefers-color-scheme (also while the page is open); Light and Dark
// are kept in localStorage. Every access to storage is guarded: it can throw or be empty.
const KEY = "dmslog.theme";

export const THEME_CHOICES = ["system", "light", "dark"];

/** The stored value as a choice: anything but "light" and "dark" means System. */
export function resolvePreference(stored) {
  return stored === "light" || stored === "dark" ? stored : "system";
}

/** The theme to paint for a stored value and the OS setting. */
export function resolveTheme(stored, prefersDark) {
  if (stored === "light" || stored === "dark") {
    return stored;
  }
  return prefersDark ? "dark" : "light";
}

// The choice of this session when the storage refused the write: it still applies until the page closes.
let sessionChoice = null;

/** Forgets the in-memory fallback (tests). */
export function forgetSessionChoice() {
  sessionChoice = null;
}

function readStored() {
  if (sessionChoice !== null) {
    return sessionChoice.value;
  }
  try {
    return window.localStorage.getItem(KEY);
  } catch (err) {
    return null;
  }
}

function writeStored(value) {
  try {
    if (value === "system") {
      window.localStorage.removeItem(KEY);
    } else {
      window.localStorage.setItem(KEY, value);
    }
    sessionChoice = null;
  } catch (err) {
    sessionChoice = { value }; // not persisted, but the choice holds for this session
  }
}

function prefersDark() {
  return typeof window.matchMedia === "function" && window.matchMedia("(prefers-color-scheme: dark)").matches;
}

/** "system" | "light" | "dark": what the user picked. */
export function themePreference() {
  return resolvePreference(readStored());
}

export function currentTheme() {
  return document.documentElement.dataset.theme || resolveTheme(readStored(), prefersDark());
}

let watching = false;

export function applyTheme() {
  const theme = resolveTheme(readStored(), prefersDark());
  document.documentElement.dataset.theme = theme;
  if (!watching && typeof window.matchMedia === "function") {
    watching = true;
    const query = window.matchMedia("(prefers-color-scheme: dark)");
    const follow = () => {
      if (themePreference() === "system") {
        document.documentElement.dataset.theme = resolveTheme(null, query.matches);
      }
    };
    if (typeof query.addEventListener === "function") {
      query.addEventListener("change", follow);
    }
  }
  return theme;
}

/** Saves the choice and paints it. Returns the painted theme ("light" or "dark"). */
export function setThemePreference(choice) {
  const next = THEME_CHOICES.includes(choice) ? choice : "system";
  writeStored(next);
  return applyTheme();
}
