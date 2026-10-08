// Minimal M0 page: shows the server version. Text is always set with textContent.
"use strict";

(async function () {
  const strings = window.DMSLOG_STRINGS;
  document.getElementById("title").textContent = strings.title;
  const versionEl = document.getElementById("version");
  try {
    const response = await fetch("/api/health", { credentials: "same-origin" });
    if (!response.ok) {
      throw new Error("HTTP " + response.status);
    }
    const health = await response.json();
    versionEl.textContent = strings.versionLabel + ": " + health.version;
  } catch (err) {
    versionEl.textContent = strings.healthError;
  }
})();
