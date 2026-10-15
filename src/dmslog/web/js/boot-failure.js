// Shown by boot.js when main.js (or one of its modules) could not be loaded: the page must never stay blank and
// silent. No imports: it has to work when everything else failed. Only textContent.
export const BOOT_FAILURE_TEXT = "The viewer could not start. Reload the page; if it happens again, restart dmslog.";

export function showBootFailure(doc, text = BOOT_FAILURE_TEXT) {
  const bar = doc.getElementById("toolbar");
  // main.js shows this same alert itself when its start() is what failed: never two of them.
  const shown = bar ? Array.from(bar.children).find((n) => n.className === "topbar-failed") : null;
  if (shown) {
    bar.classList.add("is-ready", "is-failed");
    return shown;
  }
  const alert = doc.createElement("span");
  alert.className = "topbar-failed";
  alert.setAttribute("role", "alert");
  alert.textContent = text;
  if (bar) {
    bar.classList.add("is-ready", "is-failed"); // reveals the bar and the panels (css/topbar.css)
    bar.appendChild(alert);
  } else {
    doc.body.appendChild(alert);
  }
  return alert;
}
